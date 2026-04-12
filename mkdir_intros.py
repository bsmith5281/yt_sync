mkdir intros

python - << "PY"
import csv, subprocess, os

INPUT = "whisper_cli_test_flow.csv"
OUTDIR = "intros"
os.makedirs(OUTDIR, exist_ok=True)

with open(INPUT, newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        vid = (row.get("video_id") or "").strip()
        if not vid:
            continue
        wav_path = os.path.join(OUTDIR, f"{vid}.wav")
        if os.path.exists(wav_path):
            print("Already have", wav_path)
            continue
        url = f"https://youtu.be/{vid}"
        print("Downloading intro for", vid)
        p1 = subprocess.Popen(
            ["yt-dlp", "-f", "bestaudio/best", "-o", "-", url],
            stdout=subprocess.PIPE
        )
        p2 = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", "pipe:0",
                "-t", "18",
                "-vn", "-ac", "1", "-ar", "16000",
                "-af", "highpass=f=120,lowpass=f=3800,loudnorm",
                wav_path,
            ],
            stdin=p1.stdout,
        )
        if p1.stdout:
            p1.stdout.close()
        p1.wait()
PY