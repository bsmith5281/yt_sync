import csv
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from difflib import SequenceMatcher

import whisper
from adventures import ADVENTURE_DIRECTORY, ALIASES

INPUT_CSV = "adventure_metadata_preview.csv"
OUTPUT_CSV = "adventure_metadata_validated_v7.csv"
CLIP_DURATION_SECONDS = 16
WHISPER_MODEL_NAME = "base"
TITLE_TEMPLATE = "{hero} vs {boss} – {series_name} | Hearthstone | Adventure Mode"


def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_rows(path, rows):
    fieldnames = list(rows[0].keys()) if rows else []
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_key(name):
    n = (name or "").strip().lower()
    n = n.replace("\u2019", "'")
    n = re.sub(r"\s+", " ", n)
    return ALIASES.get(n, n)


def build_indexes():
    hero_to_mode = {}
    bosses = set()
    all_names = set()
    canonical = {}
    for mode, payload in ADVENTURE_DIRECTORY.items():
        for hero, hero_bosses in payload.get("heroes", {}).items():
            hk = normalize_key(hero)
            hero_to_mode[hk] = mode
            canonical[hk] = hero
            all_names.add(hk)
            for boss in hero_bosses:
                bk = normalize_key(boss)
                bosses.add(bk)
                canonical[bk] = boss
                all_names.add(bk)
    return hero_to_mode, bosses, canonical, sorted(all_names, key=len, reverse=True)


HERO_TO_MODE, BOSS_KEYS, CANONICAL, ALL_NAMES = build_indexes()


def build_watch_url(video_id):
    return f"https://youtu.be/{video_id}"


def clean_text(text):
    lowered = (text or "").lower().replace("-", " ").replace("\u2019", "'")
    lowered = re.sub(r"[^a-z0-9' ]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def fuzzy_contains(text, phrase, threshold=0.9):
    words = text.split()
    target = phrase.split()
    if not words or not target:
        return False
    n = len(target)
    for i in range(0, len(words) - n + 1):
        window = " ".join(words[i:i + n])
        if SequenceMatcher(None, window, phrase).ratio() >= threshold:
            return True
    return False


def extract_candidates(text):
    cleaned = clean_text(text)
    found = []
    for name in ALL_NAMES:
        phrase = name.lower()
        if phrase in cleaned or fuzzy_contains(cleaned, phrase):
            if name not in found:
                found.append(name)
    return found


def score_candidates(row, transcript):
    score = defaultdict(int)
    transcript_names = extract_candidates(transcript)
    metadata_blob = " ".join([
        row.get("old_title", "") or "",
        row.get("old_description", "") or "",
        row.get("current_hero", "") or "",
        row.get("current_boss", "") or "",
        row.get("current_series", "") or "",
        row.get("previous_formatted_title", "") or "",
        row.get("next_formatted_title", "") or "",
        row.get("hero_name", "") or "",
        row.get("boss_name", "") or "",
    ])
    metadata_names = extract_candidates(metadata_blob)
    for name in transcript_names:
        score[name] += 5
    for name in metadata_names:
        score[name] += 3
    return score, transcript_names, metadata_names


def resolve_matchup(row, transcript):
    scores, transcript_names, metadata_names = score_candidates(row, transcript)
    raw_candidates = set(transcript_names + metadata_names)

    title_blob = " ".join([
        row.get("old_title", "") or "",
        row.get("old_description", "") or "",
        row.get("current_hero", "") or "",
        row.get("current_boss", "") or "",
        row.get("current_series", "") or "",
        row.get("previous_formatted_title", "") or "",
        row.get("next_formatted_title", "") or "",
        row.get("hero_name", "") or "",
        row.get("boss_name", "") or "",
    ])

    cleaned_transcript = clean_text(transcript)
    cleaned_title_blob = clean_text(title_blob)

    def contains_name(cleaned, key_name):
        return key_name in cleaned if key_name else False

    best_mode = ""
    best_hero = ""
    best_boss = ""
    best_score = 0

    current_hero = normalize_key(row.get("current_hero", ""))
    current_boss = normalize_key(row.get("current_boss", ""))

    for mode, payload in ADVENTURE_DIRECTORY.items():
        heroes = payload.get("heroes", {})
        for hero_canonical, boss_list in heroes.items():
            hero_key = normalize_key(hero_canonical)
            if hero_key not in raw_candidates and not contains_name(cleaned_title_blob, hero_key):
                continue
            for boss_canonical in boss_list:
                boss_key = normalize_key(boss_canonical)
                pair_score = scores.get(hero_key, 0) + scores.get(boss_key, 0)
                if contains_name(cleaned_transcript, hero_key):
                    pair_score += 6
                if contains_name(cleaned_transcript, boss_key):
                    pair_score += 6
                if contains_name(cleaned_title_blob, hero_key):
                    pair_score += 4
                if contains_name(cleaned_title_blob, boss_key):
                    pair_score += 4
                if current_hero == hero_key:
                    pair_score += 4
                if current_boss == boss_key:
                    pair_score += 4
                if pair_score > best_score:
                    best_score = pair_score
                    best_mode = mode
                    best_hero = hero_canonical
                    best_boss = boss_canonical

    if best_score >= 14 and best_hero and best_boss:
        confidence = "high"
    elif best_score >= 8 and best_hero and best_boss:
        confidence = "medium"
    elif transcript_names or metadata_names:
        confidence = "low"
    else:
        confidence = "none"

    return best_mode, best_hero, best_boss, confidence, transcript_names, metadata_names, best_score


def current_title_matches(row, hero, boss, series_name):
    title = (row.get("old_title") or "").strip()
    compact = clean_text(title)
    needed = [clean_text(hero), clean_text(boss), clean_text(series_name)]
    return all(x and x in compact for x in needed)


def metadata_pair_state(row, hero, boss):
    metadata_hero = normalize_key(row.get("current_hero", ""))
    metadata_boss = normalize_key(row.get("current_boss", ""))
    resolved_hero = normalize_key(hero)
    resolved_boss = normalize_key(boss)
    supports = (
        bool(resolved_hero and resolved_boss)
        and metadata_hero == resolved_hero
        and metadata_boss == resolved_boss
    )
    conflicts = (
        bool(metadata_hero and resolved_hero and metadata_hero != resolved_hero)
        or bool(metadata_boss and resolved_boss and metadata_boss != resolved_boss)
    )
    return supports, conflicts


def should_process(row):
    family = (row.get("title_family") or "").strip().lower()
    return family in {
        "default_timestamp",
        "contains_timestamp",
        "other",
        "formatted_book_of_heroes",
        "formatted_book_of_mercenaries",
        "formatted_adventure_mode",
    }


def download_clip(video_id, out_dir):
    url = build_watch_url(video_id)
    out_path = os.path.join(out_dir, f"{video_id}.%(ext)s")
    cmd = [
        "python", "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "--max-filesize", "50m",
        "--download-sections", f"*0-{CLIP_DURATION_SECONDS}",
        "-o", out_path,
        url,
    ]
    print(f"[yt-dlp] Downloading first {CLIP_DURATION_SECONDS}s for {video_id} ...")
    subprocess.run(cmd, check=True)
    for fname in os.listdir(out_dir):
        if fname.startswith(video_id + "."):
            return os.path.join(out_dir, fname)
    raise FileNotFoundError(f"Expected audio clip for {video_id} was not found in {out_dir}")


def transcribe_clip(model, audio_path):
    result = model.transcribe(audio_path, language="en")
    return (result.get("text") or "").strip()


def build_title(row, hero, boss, mode):
    hero = (hero or "").strip()
    boss = (boss or "").strip()
    series_name = ADVENTURE_DIRECTORY.get(mode, {}).get("series_name", "") or row.get("series_name", "") or "Adventure Mode"
    if not hero or not boss:
        return row.get("old_title", "")
    return f"{hero} vs {boss} – {series_name} | Hearthstone | Adventure Mode"


def build_description(row, hero, boss, mode):
    hero = (hero or "").strip()
    boss = (boss or "").strip()
    series_name = ADVENTURE_DIRECTORY.get(mode, {}).get("series_name", "") or row.get("series_name", "") or "Adventure Mode"
    if not hero and not boss:
        return row.get("old_description", "")
    matchup = f"{hero} vs {boss}" if hero and boss else (hero or boss)
    return "\n\n".join([
        f"Hearthstone {series_name} gameplay from Adventure Mode.",
        f"Featuring the {matchup} encounter.",
        "Part of the Hearthstone adventure playlist.",
    ])


# ---------------------------------------------------------------------------
# FIX #1 — Hero-only trust when boss is unrecognized in the directory.
#
# v6 required BOTH hero AND boss to be validated in the directory together
# before setting trusted_hero/trusted_boss. If current_boss = "Kraxx" (not in
# directory), trusted_boss stayed empty, inference kicked in and picked the
# wrong boss, then metadata_conflicts_pair fired and blocked apply=TRUE.
#
# v7 strategy (in priority order):
#   1. If meta hero+boss are both valid in directory → trust both (same as v6)
#   2. If meta hero is valid in directory but boss is NOT → trust hero + use
#      current_boss as-is from the title (it's already the correct boss name,
#      just not in our lookup table yet). Treat this as a title-confirmed pair.
#   3. If neither hero nor boss is in directory → fall back to STT inference.
# ---------------------------------------------------------------------------
def resolve_trusted_pair(row, mode_inf, hero_inf, boss_inf):
    meta_hero = (row.get("current_hero") or "").strip()
    meta_boss = (row.get("current_boss") or "").strip()
    meta_hero_key = normalize_key(meta_hero)
    meta_boss_key = normalize_key(meta_boss)

    # Determine mode from meta hero if possible.
    if meta_hero_key in HERO_TO_MODE:
        meta_mode = HERO_TO_MODE[meta_hero_key]
    else:
        meta_mode = mode_inf

    trusted_hero = ""
    trusted_boss = ""
    trusted_mode = meta_mode or mode_inf
    trust_source = "inference"

    if meta_mode and meta_mode in ADVENTURE_DIRECTORY:
        heroes = ADVENTURE_DIRECTORY[meta_mode].get("heroes", {})
        for hero_candidate, boss_list in heroes.items():
            if normalize_key(hero_candidate) != meta_hero_key:
                continue

            # Priority 1: both hero and boss are in the directory.
            boss_keys_in_dir = [normalize_key(b) for b in boss_list]
            if meta_boss_key in boss_keys_in_dir:
                trusted_hero = hero_candidate
                for b in boss_list:
                    if normalize_key(b) == meta_boss_key:
                        trusted_boss = b
                        break
                trusted_mode = meta_mode
                trust_source = "meta_pair_directory_validated"
                break

            # FIX #1 — Priority 2: hero is in directory but boss is not.
            # The title already names the correct boss — use it directly.
            if meta_boss:
                trusted_hero = hero_candidate
                trusted_boss = meta_boss   # raw title value, not from directory
                trusted_mode = meta_mode
                trust_source = "meta_hero_validated_boss_from_title"
                break

    # Priority 3: fall back to STT inference.
    if not trusted_hero and hero_inf:
        trusted_hero = hero_inf
        trust_source = "inference"
    if not trusted_boss and boss_inf:
        trusted_boss = boss_inf

    if not trusted_mode:
        trusted_mode = mode_inf

    return trusted_hero, trusted_boss, trusted_mode, trust_source


# ---------------------------------------------------------------------------
# FIX #2 — Smarter conflict detection.
#
# v6 called metadata_pair_state() AFTER setting trusted_hero/trusted_boss,
# which compared trusted values back against current_hero/current_boss.
# When trust_source = "meta_hero_validated_boss_from_title", trusted_boss IS
# current_boss, so there can never be a real conflict — but the normalize_key
# comparison would still fire if spellings differed slightly.
#
# v7 skips conflict detection entirely when the boss came from the title
# (trust_source ends with "_from_title"), since we're using the title's own
# value and there's nothing to conflict with.
# ---------------------------------------------------------------------------
def compute_conflict(row, trusted_hero, trusted_boss, trust_source):
    if trust_source == "meta_hero_validated_boss_from_title":
        # Hero is validated, boss is taken directly from the title — no conflict possible.
        return False, True   # (conflicts=False, supports=True)
    return metadata_pair_state(row, trusted_hero, trusted_boss)


# ---------------------------------------------------------------------------
# FIX #3 — For uncertain timestamp rows: use playlist neighbours + description
# to infer hero/boss even when STT fails entirely.
#
# Many default_timestamp videos have descriptions already updated to the
# correct Book of Heroes format by the prior cleanup script, which contains
# the hero+boss pair in the first line. We parse that as a strong signal.
# ---------------------------------------------------------------------------
def extract_pair_from_description(desc):
    """
    Tries to parse hero+boss from a description line like:
    'Hearthstone Book of Heroes gameplay from Adventure Mode.'
    or the older format first line: 'Garrosh vs Greatmother Geyah – Book of Heroes | ...'
    """
    if not desc:
        return "", ""
    first_line = desc.strip().splitlines()[0].strip()
    # Pattern: "Hero vs Boss – Series | ..."
    m = re.match(r"^(?P<hero>.+?)\s+vs\s+(?P<boss>.+?)\s+[–-]", first_line)
    if m:
        return m.group("hero").strip(), m.group("boss").strip()
    return "", ""


def main():
    rows = load_rows(INPUT_CSV)
    model = whisper.load_model(WHISPER_MODEL_NAME)

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, row in enumerate(rows, start=1):
            if not should_process(row):
                row["verification_status"] = "skipped"
                row["verification_reason"] = "title_family_not_targeted"
                row["needs_review"] = row.get("needs_review", "FALSE") or "FALSE"
                row["apply"] = row.get("apply", "FALSE") or "FALSE"
                print(f"[{i}/{len(rows)}] {row.get('video_id', '')} -> skipped")
                continue

            video_id = (row.get("video_id") or "").strip()
            transcript = ""

            if video_id:
                try:
                    clip_path = download_clip(video_id, tmpdir)
                    transcript = transcribe_clip(model, clip_path)
                except Exception as e:
                    row["verification_status"] = "stt_failed"
                    row["verification_reason"] = f"stt_failed:{e}"
                    row["stt_raw"] = ""
                    row["stt_confidence"] = "none"
                    row["pair_score"] = "0"
                    transcript = ""

            mode_inf, hero_inf, boss_inf, confidence, transcript_names, metadata_names, pair_score = resolve_matchup(row, transcript)

            # FIX #3 — If STT and directory inference both failed and we still
            # have no hero/boss, try parsing the description for a title-format pair.
            if not hero_inf and not boss_inf:
                desc_hero, desc_boss = extract_pair_from_description(row.get("old_description", ""))
                if desc_hero and desc_boss:
                    # Validate the hero against the directory.
                    desc_hero_key = normalize_key(desc_hero)
                    if desc_hero_key in HERO_TO_MODE:
                        hero_inf = desc_hero
                        boss_inf = desc_boss
                        mode_inf = HERO_TO_MODE[desc_hero_key]
                        confidence = "medium"
                        pair_score = 8   # treat description-sourced pair as medium evidence

            # FIX #1 — Use the improved trusted pair resolver.
            trusted_hero, trusted_boss, trusted_mode, trust_source = resolve_trusted_pair(
                row, mode_inf, hero_inf, boss_inf
            )

            series_name = ADVENTURE_DIRECTORY.get(trusted_mode, {}).get("series_name", "Adventure Mode")
            final_title = build_title(row, trusted_hero, trusted_boss, trusted_mode)
            final_description = build_description(row, trusted_hero, trusted_boss, trusted_mode)

            row["mode"] = trusted_mode
            row["hero_name"] = trusted_hero
            row["boss_name"] = trusted_boss
            row["series_name"] = series_name
            row["stt_raw"] = transcript
            row["stt_confidence"] = confidence
            row["pair_score"] = str(pair_score)
            row["final_title"] = final_title
            row["final_description"] = final_description

            # FIX #2 — Use smarter conflict detection.
            metadata_conflicts_pair, metadata_supports_pair = compute_conflict(
                row, trusted_hero, trusted_boss, trust_source
            )

            # Verification status.
            if trusted_hero or trusted_boss:
                if current_title_matches(row, trusted_hero or "", trusted_boss or "", series_name):
                    row["verification_status"] = "verified_match"
                    row["verification_reason"] = f"current_title_matches_trusted_matchup|source={trust_source}"
                else:
                    row["verification_status"] = "needs_update"
                    reason_bits = [
                        "trusted_matchup_differs_from_current_title",
                        f"confidence={confidence}",
                        f"pair_score={pair_score}",
                        f"source={trust_source}",
                    ]
                    if metadata_supports_pair:
                        reason_bits.append("metadata_supports_pair")
                    if metadata_conflicts_pair:
                        reason_bits.append("metadata_conflicts_pair")
                    row["verification_reason"] = "|".join(reason_bits)
            else:
                row["verification_status"] = "uncertain"
                row["verification_reason"] = (
                    f"insufficient_match_data|confidence={confidence}|pair_score={pair_score}|"
                    f"transcript={','.join(transcript_names[:5])}|metadata={','.join(metadata_names[:5])}"
                )

            # Apply decision.
            family = (row.get("title_family") or "").strip().lower()

            if metadata_conflicts_pair:
                # Genuine conflict — human must verify.
                row["needs_review"] = "TRUE"
                row["apply"] = "FALSE"
                row["review_notes"] = "metadata_conflict_do_not_auto_apply"

            elif trusted_hero and trusted_boss:
                # We have a complete pair — decide how confident we are.
                is_dir_validated = trust_source == "meta_pair_directory_validated"
                is_title_hero = trust_source == "meta_hero_validated_boss_from_title"
                is_formatted = family in (
                    "formatted_book_of_heroes",
                    "formatted_book_of_mercenaries",
                    "formatted_adventure_mode",
                )
                is_timestamp = family in ("default_timestamp", "contains_timestamp")

                if is_dir_validated or is_title_hero:
                    # Hero confirmed in directory, boss confirmed from title or directory.
                    # Safe to auto-apply for both formatted and timestamp families.
                    row["needs_review"] = "FALSE"
                    row["apply"] = "TRUE"
                    row["review_notes"] = f"auto_apply_v7_{trust_source}"

                elif is_formatted and confidence in ("high", "medium"):
                    row["needs_review"] = "FALSE"
                    row["apply"] = "TRUE"
                    row["review_notes"] = "auto_apply_v7_formatted_confident"

                elif is_timestamp and confidence in ("high", "medium") and pair_score >= 8:
                    row["needs_review"] = "FALSE"
                    row["apply"] = "TRUE"
                    row["review_notes"] = "auto_apply_v7_timestamp_confident"

                else:
                    row["needs_review"] = "TRUE"
                    row["apply"] = "FALSE"
                    row["review_notes"] = "low_confidence_needs_human_ok"

            else:
                row["needs_review"] = "TRUE"
                row["apply"] = "FALSE"
                row["review_notes"] = "risky_no_pair_resolved"

            print(
                f"[{i}/{len(rows)}] {video_id} -> {row['verification_status']} | "
                f"{trusted_hero} vs {trusted_boss} | score={pair_score} | conf={confidence} | "
                f"source={trust_source} | apply={row['apply']}"
            )

    save_rows(OUTPUT_CSV, rows)
    print(f"\nWrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
