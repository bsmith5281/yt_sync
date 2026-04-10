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
OUTPUT_CSV = "adventure_metadata_validated_v5.csv"
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
    n = n.replace("’", "'")
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
    lowered = (text or "").lower().replace("-", " ").replace("’", "'")
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
        # Fallback: keep original title when we don't trust a full matchup
        return row.get("old_title", "")

    # Standardized template
    return f"{hero} vs {boss} – {series_name} | Hearthstone | Adventure Mode"


def build_description(row, hero, boss, mode):
    hero = (hero or "").strip()
    boss = (boss or "").strip()
    series_name = ADVENTURE_DIRECTORY.get(mode, {}).get("series_name", "") or row.get("series_name", "") or "Adventure Mode"

    if not hero or not boss:
        # Fallback to original description if we do not trust names
        return row.get("old_description", "")

    matchup = f"{hero} vs {boss}"
    line1 = f"Hearthstone {series_name} gameplay from Adventure Mode."
    line2 = f"Featuring the {matchup} encounter."
    line3 = "Part of the Hearthstone adventure playlist."
    return "\n\n".join([line1, line2, line3])


def classify_match(row):
    status = row.get("verification_status", "")
    reason = row.get("verification_reason", "")
    conf = row.get("stt_confidence", "")
    try:
        score = int(row.get("pair_score", "0"))
    except ValueError:
        score = 0

    # Hard stop: anything already marked uncertain/needs_update with conflict is risky
    if status in ("needs_update", "uncertain") or "metadata_conflicts_pair" in reason:
        return "needs_review_risky"

    # Tier 1: slam-dunk matches – safe for auto-apply
    if (
        status == "verified_match"
        and "current_title_matches_inferred_matchup" in reason
        and conf == "high"
        and score >= 18
    ):
        return "auto_apply"

    # Tier 2: good, but still needs a human sanity‑check
    if status == "verified_match" and conf in ("high", "medium") and score >= 11:
        return "needs_review_good"

    return "needs_review_risky"


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
                print(f"[{i}/{len(rows)}] {row.get('video_id','')} -> skipped")
                continue

            video_id = (row.get("video_id") or "").strip()
            transcript = ""

            if video_id:
                try:
                    clip_path = download_clip(video_id, tmpdir)
                    transcript = transcribe_clip(model, clip_path)
                except Exception as e:
                    row["verification_status"] = "uncertain"
                    row["verification_reason"] = f"stt_failed:{e}"
                    row["needs_review"] = "TRUE"
                    row["apply"] = "FALSE"
                    row["stt_raw"] = transcript
                    print(f"[{i}/{len(rows)}] {video_id} -> STT FAILED ({e})")
                    continue

            mode, hero, boss, confidence, transcript_names, metadata_names, pair_score = resolve_matchup(row, transcript)
            series_name = ADVENTURE_DIRECTORY.get(mode, {}).get("series_name", "Adventure Mode")

            # For default-timestamp style titles, demand a minimum score
            family = (row.get("title_family") or "").strip().lower()
            if family in ("default_timestamp", "contains_timestamp") and pair_score < 11:
                # too weak – do not try to overwrite anything
                hero = ""
                boss = ""

            final_title = build_title(row, hero, boss, mode)
            final_description = build_description(row, hero, boss, mode)

            row["mode"] = mode
            row["hero_name"] = hero
            row["boss_name"] = boss
            row["series_name"] = series_name
            row["stt_raw"] = transcript
            row["stt_confidence"] = confidence
            row["pair_score"] = str(pair_score)
            row["final_title"] = final_title
            row["final_description"] = final_description

            metadata_supports_pair, metadata_conflicts_pair = metadata_pair_state(row, hero, boss)

            if hero and boss and current_title_matches(row, hero, boss, series_name):
                row["verification_status"] = "verified_match"
                row["verification_reason"] = "current_title_matches_inferred_matchup"
            elif hero and boss:
                row["verification_status"] = "needs_update"
                reason_bits = ["inferred_matchup_differs_from_current_title", f"confidence={confidence}", f"pair_score={pair_score}"]
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

            # Final decision on needs_review/apply using the new classifier
            tier = classify_match(row)

            if tier == "auto_apply":
                row["needs_review"] = "FALSE"
                row["apply"] = "TRUE"
                row["review_notes"] = "auto_apply_slam_dunk"
            elif tier == "needs_review_good":
                row["needs_review"] = "TRUE"
                row["apply"] = "FALSE"
                row["review_notes"] = "good_candidate_needs_human_ok"
            else:
                row["needs_review"] = "TRUE"
                row["apply"] = "FALSE"
                if "metadata_conflicts_pair" in row.get("verification_reason", ""):
                    row["review_notes"] = "metadata_conflict_do_not_auto_apply"
                else:
                    row["review_notes"] = "risky_inference_do_not_auto_apply"

            print(
                f"[{i}/{len(rows)}] {video_id} -> {row['verification_status']} | "
                f"{hero} vs {boss} | score={pair_score} | conf={confidence} | tier={tier} | apply={row['apply']}"
            )

    save_rows(OUTPUT_CSV, rows)
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()