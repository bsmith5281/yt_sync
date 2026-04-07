import csv
import os
import re
import subprocess
import tempfile
from difflib import SequenceMatcher

import whisper

INPUT_CSV = "default_title_cleanup_preview.csv"
OUTPUT_CSV = "default_title_cleanup_with_stt.csv"

CLIP_DURATION_SECONDS = 12
WHISPER_MODEL_NAME = "base"

TITLE_TEMPLATE = "{hero} vs {boss} – Book of Heroes | Hearthstone | Adventure Mode"
STANDARD_DESCRIPTION = (
    "Hearthstone Book of Heroes gameplay from Adventure Mode.\n\n"
    "Part of the Hearthstone - Adventure mode playlist."
)

KNOWN_NAMES = [
    "garrosh", "garrosh of wrath",
    "uther", "jaina", "rexxar", "valeera", "anduin", "thrall",
    "gul'dan", "guldan", "malfurion", "illidan", "magni",
    "ragnaros", "kel'thuzad", "kelthuzad", "rastakhan",
    "seriona", "whompwhisker", "blackseed", "blackseed the vile",
    "candlebeard", "giant rat", "fungalmancer flurgl", "flurgl",
    "frostfur", "chronomancer inara", "inara",
    "george and karl", "george", "karl",
    "chef scabbs", "scabbs", "gutmook",
    "medivh",
    "nemesis gul'dan", "nemesis guldan",
    "opera diva tamsin", "tamsin",
    "mecha jaraxxus", "jaraxxus",
    "n'zoth", "nzoth",
    "pathmaker hamm", "hamm",
    "graves the cleric", "graves",
    "elder brandlemar", "brandlemar",
    "russell the bard", "russell",
    "wee whelp", "overseer mogark", "mogark",
    "bristlesnarl",
]

NORMALIZED_ALIASES = {
    "guldan": "gul'dan",
    "kelthuzad": "kel'thuzad",
    "nzoth": "n'zoth",
    "nemesis guldan": "nemesis gul'dan",
    "george": "george and karl",
    "karl": "george and karl",
}

HERO_PRIORITY = [
    "garrosh of wrath", "uther", "jaina", "rexxar", "valeera",
    "anduin", "thrall", "gul'dan", "malfurion", "illidan",
]

TIMESTAMPY_TITLE_RE = re.compile(
    r"^Hearthstone Heroes of Warcraft \d{4} \d{2} \d{2}T\d{2} \d{2} \d{2}$",
    re.IGNORECASE,
)

def load_rows(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find {path}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def save_rows(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def ensure_extra_columns(rows):
    for row in rows:
        row.setdefault("hero_name", "")
        row.setdefault("boss_name", "")
        row.setdefault("final_title", "")
        row.setdefault("final_description", "")
        row.setdefault("stt_raw", "")
        row.setdefault("stt_confidence", "")
        row.setdefault("apply", row.get("apply", "FALSE") or "FALSE")
    return rows

def build_watch_url(video_id):
    return f"https://youtu.be/{video_id}"

def normalize_name(name):
    n = (name or "").strip().lower()
    return NORMALIZED_ALIASES.get(n, n)

def prettify_name(name):
    if not name:
        return ""
    parts = []
    for token in name.split():
        if token in {"of", "and", "the", "vs"}:
            parts.append(token)
        elif "'" in token:
            left, right = token.split("'", 1)
            parts.append(left.capitalize() + "'" + right.capitalize())
        else:
            parts.append(token.capitalize())
    pretty = " ".join(parts)
    pretty = pretty.replace("N'zoth", "N'Zoth")
    return pretty

def looks_like_target_default(row):
    if str(row.get("is_target_default", "")).strip().upper() == "TRUE":
        return True
    title = (row.get("old_title") or row.get("title") or "").strip()
    return bool(TIMESTAMPY_TITLE_RE.match(title))

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

    print(f"[yt-dlp] Downloading first {CLIP_DURATION_SECONDS}s for {video_id} ...")
    subprocess.run(cmd, check=True)

    for fname in os.listdir(out_dir):
        if fname.startswith(video_id + "."):
            return os.path.join(out_dir, fname)

    raise FileNotFoundError(f"Expected audio clip for {video_id} was not found in {out_dir}")

def transcribe_clip(model, audio_path):
    print(f"[whisper] Transcribing {os.path.basename(audio_path)} ...")
    result = model.transcribe(audio_path, language="en")
    return (result.get("text") or "").strip()

def clean_transcript(text):
    lowered = (text or "").lower()
    lowered = lowered.replace("-", " ")
    lowered = re.sub(r"[^a-z0-9' ]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered

def fuzzy_contains(text, phrase, threshold=0.88):
    words = text.split()
    target = phrase.split()
    if not words or not target:
        return False
    n = len(target)
    for i in range(0, len(words) - n + 1):
        window = " ".join(words[i:i+n])
        if SequenceMatcher(None, window, phrase).ratio() >= threshold:
            return True
    return False

def find_names_in_text(text):
    cleaned = clean_transcript(text)
    found = []

    for raw_name in KNOWN_NAMES:
        canonical = normalize_name(raw_name)
        phrase = raw_name.lower()
        if phrase in cleaned or fuzzy_contains(cleaned, phrase):
            if canonical not in found:
                found.append(canonical)

    hero = ""
    boss = ""
    confidence = "none"

    for candidate in found:
        if candidate in HERO_PRIORITY:
            hero = candidate
            break

    if not hero:
        for candidate in found:
            hero = candidate
            break

    if hero:
        for candidate in found:
            if candidate != hero:
                boss = candidate
                break

    if hero and boss:
        confidence = "high"
    elif hero:
        confidence = "low"

    return prettify_name(hero), prettify_name(boss), confidence

def fallback_from_existing_metadata(row):
    text_parts = [
        row.get("old_title", ""),
        row.get("old_description", ""),
        row.get("previous_formatted_title", ""),
        row.get("next_formatted_title", ""),
        row.get("suggested_title", ""),
    ]
    combined = " ".join(x for x in text_parts if x)
    return find_names_in_text(combined)

def build_final_title(hero, boss, row):
    if hero and boss:
        return TITLE_TEMPLATE.format(hero=hero, boss=boss)
    if hero:
        return f"{hero} – Book of Heroes | Hearthstone | Adventure Mode"
    existing = (row.get("suggested_title") or row.get("old_title") or row.get("title") or "").strip()
    return existing

def main():
    print("START STT SCRIPT")

    rows = load_rows(INPUT_CSV)
    rows = ensure_extra_columns(rows)

    target_rows = [r for r in rows if looks_like_target_default(r)]
    print(f"Total rows: {len(rows)}")
    print(f"Rows with default_timestamp titles (targets): {len(target_rows)}")

    if not target_rows:
        print("No target rows to process; exiting.")
        save_rows(OUTPUT_CSV, rows[0].keys(), rows)
        print(f"Wrote {OUTPUT_CSV}")
        return

    print(f"Loading Whisper model '{WHISPER_MODEL_NAME}' ...")
    model = whisper.load_model(WHISPER_MODEL_NAME)

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, row in enumerate(target_rows, start=1):
            video_id = (row.get("video_id") or row.get("id") or "").strip()
            print(f"\n[{i}/{len(target_rows)}] Processing {video_id or '(missing video_id)'}")

            hero = ""
            boss = ""
            confidence = "none"
            transcript = ""

            if video_id:
                try:
                    clip_path = download_clip(video_id, tmpdir)
                    transcript = transcribe_clip(model, clip_path)
                    hero, boss, confidence = find_names_in_text(transcript)
                except Exception as e:
                    print(f"  STT failed: {e}")

            if not hero:
                meta_hero, meta_boss, meta_conf = fallback_from_existing_metadata(row)
                hero = hero or meta_hero
                boss = boss or meta_boss
                if confidence == "none":
                    confidence = meta_conf

            row["hero_name"] = hero
            row["boss_name"] = boss
            row["stt_raw"] = transcript
            row["stt_confidence"] = confidence
            row["final_title"] = build_final_title(hero, boss, row)
            row["final_description"] = STANDARD_DESCRIPTION
            row["apply"] = "TRUE" if row["final_title"] and row["final_title"] != (row.get("old_title") or row.get("title") or "").strip() else "FALSE"

            print(f"  hero={hero!r} boss={boss!r} confidence={confidence!r}")
            print(f"  final_title={row['final_title']!r}")

    save_rows(OUTPUT_CSV, rows[0].keys(), rows)
    print(f"\nWrote {OUTPUT_CSV}")

if __name__ == "__main__":
    main()