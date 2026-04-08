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
OUTPUT_CSV = "adventure_metadata_validated.csv"
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


def prettify_name(name):
    key = normalize_key(name)
    if key in CANONICAL:
        return CANONICAL[key]
    if not name:
        return ""
    return " ".join(
        w.capitalize() if w.lower() not in {"of", "and", "the", "vs"} else w.lower()
        for w in str(name).split()
    )


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

    # Collect raw candidate tokens from all evidence
    raw_candidates = set(transcript_names + metadata_names)
    title_blob = " ".join([
        row.get("old_title", "") or "",
        row.get("old_description", "") or "",
        row.get("current_hero", "") or "",
        row.get("current_boss", "") or "",
        row.get("current_series", "") or "",
    ])

    cleaned_transcript = clean_text(transcript)
    cleaned_title_blob = clean_text(title_blob)

    def contains_name(cleaned, key_name):
        return key_name in cleaned if key_name else False

    best_mode = ""
    best_hero = ""
    best_boss = ""
    best_score = 0

    for mode, payload in ADVENTURE_DIRECTORY.items():
        heroes = payload.get("heroes", {})
        for hero_canonical, boss_list in heroes.items():
            hero_key = normalize_key(hero_canonical)

            # Require some hint that this hero is relevant
            if hero_key not in raw_candidates:
                if not contains_name(cleaned_title_blob, clean_text(hero_canonical)):
                    continue

            for boss_canonical in boss_list:
                boss_key = normalize_key(boss_canonical)
                score = 0

                # Transcript evidence
                if contains_name(cleaned_transcript, hero_key):
                    score += 6
                if contains_name(cleaned_transcript, boss_key):
                    score += 6

                # Title / description / neighbor evidence
                if contains_name(cleaned_title_blob, hero_key):
                    score += 4
                if contains_name(cleaned_title_blob, boss_key):
                    score += 4

                # Current metadata hints
                current_hero = normalize_key(row.get("current_hero", ""))
                current_boss = normalize_key(row.get("current_boss", ""))
                if current_hero == hero_key:
                    score += 4
                if current_boss == boss_key:
                    score += 4

                if score > best_score:
                    best_score = score
                    best_mode = mode
                    best_hero = hero_canonical
                    best_boss = boss_canonical

    mode = best_mode
    hero = best_hero
    boss = best_boss

    if best_score >= 14 and hero and boss:
        confidence = "high"
    elif best_score >= 8 and hero and boss:
        confidence = "medium"
    elif transcript_names or metadata_names:
        confidence = "low"
    else:
        confidence = "none"

    return mode, hero, boss, confidence, transcript_names, metadata_names


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
        bool(resolved_hero and resolved_boss) and
        metadata_hero == resolved_hero and
        metadata_boss == resolved_boss
    )

    conflicts = (
        bool(metadata_hero and resolved_hero and metadata_hero != resolved_hero) or
        bool(metadata_boss and resolved_boss and metadata_boss != resolved_boss)
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
        "py", "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "--max-filesize", "50m",
        "--download-sections", f"*0-{CLIP_DURATION_SECONDS}",
        "-o", out_path,
        url,
    ]
    subprocess.run(cmd, check=True)
    for fname in os.listdir(out_dir):
        if fname.startswith(video_id + "."):
            return os.path.join(out_dir, fname)
    raise FileNotFoundError(f"Expected audio clip for {video_id} was not found")


def transcribe_clip(model, audio_path):
    result = model.transcribe(audio_path, language="en")
    return (result.get("text") or "").strip()


def main():
    rows = load_rows(INPUT_CSV)
    model = whisper.load_model(WHISPER_MODEL_NAME)

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, row in enumerate(rows, start=1):
            if not should_process(row):
                row["verification_status"] = "skipped"
                row["verification_reason"] = "title_family_not_targeted"
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
                    row["stt_raw"] = transcript
                    print(f"[{i}/{len(rows)}] {video_id} -> STT FAILED ({e})")
                    continue

            mode, hero, boss, confidence, transcript_names, metadata_names = resolve_matchup(row, transcript)

            # Series name directly from directory
            series_name = ADVENTURE_DIRECTORY.get(mode, {}).get("series_name", "Adventure Mode")

            if hero and boss:
                final_title = TITLE_TEMPLATE.format(hero=hero, boss=boss, series_name=series_name)
            else:
                final_title = ""

            def build_description(hero, boss, mode):
                series = ADVENTURE_DIRECTORY.get(mode, {}).get("series_name", "Adventure Mode")
                matchup = f"{hero} vs {boss}" if hero and boss else hero or boss or "Adventure gameplay"
                return (
                    f"Hearthstone {series} gameplay from Adventure Mode.\n\n"
                    f"Featuring the {matchup} encounter.\n\n"
                    f"Part of the Hearthstone adventure playlist."
                )

            final_description = build_description(hero, boss, mode) if (hero or boss) else ""

            row["mode"] = mode
            row["hero_name"] = hero
            row["boss_name"] = boss
            row["series_name"] = series_name
            row["stt_raw"] = transcript
            row["stt_confidence"] = confidence
            row["final_title"] = final_title
            row["final_description"] = final_description

                        title_family = (row.get("title_family") or "").strip().lower()
            is_timestamp_family = title_family in {"default_timestamp", "contains_timestamp"}
            metadata_supports_pair, metadata_conflicts_pair = metadata_pair_state(row, hero, boss)

            if hero and boss and current_title_matches(row, hero, boss, series_name):
                row["verification_status"] = "verified_match"
                row["verification_reason"] = "current_title_matches_inferred_matchup"
                row["needs_review"] = "FALSE"
                row["apply"] = "FALSE"

            elif hero and boss and final_title:
                row["verification_status"] = "needs_update"

                reason_bits = ["inferred_matchup_differs_from_current_title"]
                if is_timestamp_family:
                    reason_bits.append("timestamp_family")
                if metadata_supports_pair:
                    reason_bits.append("metadata_supports_pair")
                if metadata_conflicts_pair:
                    reason_bits.append("metadata_conflicts_pair")
                if confidence:
                    reason_bits.append(f"confidence={confidence}")

                row["verification_reason"] = "|".join(reason_bits)

                if metadata_conflicts_pair:
                    row["needs_review"] = "TRUE"
                    row["apply"] = "FALSE"
                elif is_timestamp_family or metadata_supports_pair or confidence in {"high", "medium"}:
                    row["needs_review"] = "FALSE"
                    row["apply"] = "TRUE"
                else:
                    row["needs_review"] = "TRUE"
                    row["apply"] = "FALSE"

            else:
                row["verification_status"] = "uncertain"
                row["verification_reason"] = (
                    f"insufficient_match_data "
                    f"transcript={','.join(transcript_names[:5])} "
                    f"metadata={','.join(metadata_names[:5])}"
                )
                row["needs_review"] = "TRUE"
                row["apply"] = "FALSE"
            print(f"[{i}/{len(rows)}] {video_id} -> {row['verification_status']} | {hero} vs {boss} | {series_name}")

    save_rows(OUTPUT_CSV, rows)
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()