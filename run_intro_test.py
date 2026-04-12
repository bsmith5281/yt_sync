import csv
import os
import subprocess
from pathlib import Path
import re

# === EDIT THESE TWO PATHS ===
WHISPER_EXE = r"C:\path\to\whisper-cli.exe"
WHISPER_MODEL = r"C:\path\to\ggml-medium.bin"
# ============================

CSV_IN = "whisper_cli_test_flow.csv"
CSV_OUT = "whisper_cli_test_flow_filled.csv"
INTROS_DIR = Path("intros")

PROMPT = (
    "This is Hearthstone Adventure Mode intro dialogue. "
    "Hero names: Garrosh, Uther, Jaina, Rexxar, Valeera, Anduin, Thrall, Gul'dan, "
    "Malfurion, Illidan. Boss names: Greatmother Geyah, Elder Brandlemar, "
    "Graves the Cleric, Kel'Thuzad, Ragnaros, Frostfur, Chronomancer Inara, "
    'George and Karl, Nemesis Gul\'dan, Candlebeard, Medivh, etc. '
    "Transcribe names exactly."
)

aliases = {
    "gul dan": "gul'dan",
    "gul'dan": "gul'dan",
    "great mother geyah": "greatmother geyah",
    "greatmother gayah": "greatmother geyah",
    "elder brandlemar": "elder brandlemar",
}

def norm(s: str) -> str:
    s = (s or "").lower().replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return aliases.get(s, s)

def contains_name(text: str, name: str) -> bool:
    if not text or not name:
        return False
    return norm(name) in norm(text)

def download_intro(video_id: str, wav_path: Path):
    if wav_path.exists():
        print(f"[skip] already have {wav_path}")
        return
    url = f"https://youtu.be/{video_id}"
    print(f"[yt-dlp] {video_id}")
    p1 = subprocess.Popen(
        ["yt-dlp", "-f", "bestaudio/best", "-o", "-", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0",
                "-t", "18",
                "-vn", "-ac", "1", "-ar", "16000",
                "-af", "highpass=f=120,lowpass=f=3800,loudnorm",
                str(wav_path),
            ],
            stdin=p1.stdout,
            check=True,
        )
    finally:
        if p1.stdout:
            p1.stdout.close()
        p1.wait()

def transcribe_intro(wav_path: Path, txt_path: Path):
    if txt_path.exists():
        print(f"[skip] already have {txt_path}")
        return txt_path.read_text(encoding="utf-8", errors="ignore")

    print(f"[whisper] {wav_path.name}")
    subprocess.run(
        [
            WHISPER_EXE,
            "-m", WHISPER_MODEL,
            "-f", str(wav_path),
            "-l", "en",
            "--beam-size", "5",
            "--prompt", PROMPT,
            "-otxt",
            "-of", str(txt_path.with_suffix("")),
        ],
        check=True,
    )
    return txt_path.read_text(encoding="utf-8", errors="ignore")

def main():
    if not Path(CSV_IN).exists():
        print(f"Missing {CSV_IN} in current folder.")
        return

    if not Path(WHISPER_EXE).exists():
        print(f"WHISPER_EXE not found: {WHISPER_EXE}")
        return

    if not Path(WHISPER_MODEL).exists():
        print(f"WHISPER_MODEL not found: {WHISPER_MODEL}")
        return

    INTROS_DIR.mkdir(exist_ok=True)

    rows = []
    with open(CSV_IN, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vid = (row.get("video_id") or "").strip()
            if not vid:
                rows.append(row)
                continue

            wav_path = INTROS_DIR / f"{vid}.wav"
            txt_path = INTROS_DIR / f"{vid}_out.txt"

            try:
                download_intro(vid, wav_path)
            except Exception as e:
                print(f"[error] yt-dlp/ffmpeg for {vid}: {e}")
                row["Transcript snippet"] = ""
                row["Matched hero"] = ""
                row["Matched boss"] = ""
                row["Correct?"] = "ERROR"
                rows.append(row)
                continue

            try:
                transcript = transcribe_intro(wav_path, txt_path)
            except Exception as e:
                print(f"[error] whisper for {vid}: {e}")
                row["Transcript snippet"] = ""
                row["Matched hero"] = ""
                row["Matched boss"] = ""
                row["Correct?"] = "ERROR"
                rows.append(row)
                continue

            snippet = (transcript or "").strip().replace("\n", " ")
            snippet = snippet[:140]

            hero = row.get("Actual Hero", "")
            boss = row.get("Actual Boss", "")

            hero_hit = contains_name(transcript, hero)
            boss_hit = contains_name(transcript, boss)

            row["Transcript snippet"] = snippet
            row["Matched hero"] = hero if hero_hit else ""
            row["Matched boss"] = boss if boss_hit else ""
            row["Correct?"] = "YES" if (hero_hit and boss_hit) else "NO"

            rows.append(row)

    if not rows:
        print("No rows read from CSV.")
        return

    fieldnames = list(rows[0].keys())
    with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    yes_count = sum(1 for r in rows if r.get("Correct?") == "YES")
    no_count = sum(1 for r in rows if r.get("Correct?") == "NO")
    print(f"Wrote {CSV_OUT}. YES={yes_count}, NO={no_count}, total={len(rows)}")

if __name__ == "__main__":
    main()