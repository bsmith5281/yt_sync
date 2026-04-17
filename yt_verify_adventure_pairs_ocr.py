import csv
import os
import re
import sys
import tempfile
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from difflib import SequenceMatcher

from hearthstone_adventure_directory import ADVENTURE_DIRECTORY

# I/O defaults: tuned for your v14 run
INPUT_CSV = os.environ.get("INPUTCSV", "output/adventure_pair_capture_v14.csv")
OUTPUT_CSV = os.environ.get("OUTPUTCSV", "output/adventure_pair_verified_v14.csv")

# Early frames where the versus splash / boss text is likely visible
FRAME_TIMES = [3, 6, 9, 12, 16, 22, 30]

NONALNUM_RE = re.compile(r"[^a-z0-9]+")
SPACE_RE = re.compile(r"\s+")


def norm(text: str) -> str:
    text = (text or "").lower().strip().replace("-", " ")
    text = NONALNUM_RE.sub(" ", text)
    return SPACE_RE.sub(" ", text).strip()


def build_catalog():
    heroes = set()
    bosses = set()
    hero_to_bosses = defaultdict(set)
    for _, series in ADVENTURE_DIRECTORY.items():
        for hero, boss_list in series["heroes"].items():
            heroes.add(hero)
            for boss in boss_list:
                bosses.add(boss)
                hero_to_bosses[hero].add(boss)
    return heroes, bosses, hero_to_bosses


HEROES, BOSSES, HERO_TO_BOSSES = build_catalog()
HERO_KEYS = {norm(x): x for x in HEROES}
BOSS_KEYS = {norm(x): x for x in BOSSES}


def load_rows(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input CSV not found: {path}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_rows(path: str, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def ensure_tesseract() -> bool:
    try:
        subprocess.run(
            ["tesseract", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def fuzzy_hits(text: str, canon_map):
    cleaned = norm(text)
    hits = Counter()
    evidence = []
    if not cleaned:
        return hits, evidence

    tokens = cleaned.split()
    windows = []
    for size in range(1, min(6, len(tokens)) + 1):
        for i in range(0, len(tokens) - size + 1):
            windows.append(" ".join(tokens[i : i + size]))

    for phrase in windows:
        if phrase in canon_map:
            hits[canon_map[phrase]] += 5
            evidence.append(f"exact:{phrase}")
            continue
        for key, canon in canon_map.items():
            ratio = SequenceMatcher(None, phrase, key).ratio()
            if phrase in key or key in phrase:
                ratio = max(ratio, 0.985)
            if ratio >= 0.95:
                hits[canon] += 3
                evidence.append(f"fuzzy:{phrase}->{key}:{ratio:.3f}")
    return hits, evidence


def extract_frames(video_id: str, outdir: str):
    url = f"https://youtu.be/{video_id}"
    video_path = os.path.join(outdir, f"{video_id}.mp4")
    frame_dir = os.path.join(outdir, f"{video_id}_frames")
    os.makedirs(frame_dir, exist_ok=True)

    # Download just the first 35 seconds for OCR
    subprocess.run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "-f",
            "mp4/bestvideo+bestaudio/best",
            "--force-overwrites",
            "--download-sections",
            "*0-35",
            "-o",
            video_path,
            url,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    for sec in FRAME_TIMES:
        out_png = os.path.join(frame_dir, f"frame_{sec:02d}.png")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(sec), "-i", video_path, "-frames:v", "1", out_png],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return sorted(
        str(Path(frame_dir) / x)
        for x in os.listdir(frame_dir)
        if x.lower().endswith(".png")
    )


def ocr_image(path: str) -> str:
    txt_base = path + "_ocr"
    subprocess.run(
        ["tesseract", path, txt_base, "--psm", "6"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    txt_path = txt_base + ".txt"
    if not os.path.exists(txt_path):
        return ""
    return Path(txt_path).read_text(encoding="utf-8", errors="ignore").strip()


def choose_verified_pair(row, hero_hits: Counter, boss_hits: Counter):
    current_hero = (row.get("currenthero") or "").strip()
    current_boss = (row.get("currentboss") or "").strip()
    captured_hero = (row.get("capturedhero") or "").strip()
    captured_boss = (row.get("capturedboss") or "").strip()

    # 1) If metadata already has a legal pair, trust it
    if current_hero and current_boss and current_boss in HERO_TO_BOSSES.get(
        current_hero, set()
    ):
        return current_hero, current_boss, "trusted_current", 100

    # 2) If capture pair is legal, boost with OCR hits
    if captured_hero and captured_boss and captured_boss in HERO_TO_BOSSES.get(
        captured_hero, set()
    ):
        score = 70 + hero_hits.get(captured_hero, 0) * 3 + boss_hits.get(
            captured_boss, 0
        ) * 3
        return captured_hero, captured_boss, "capture_plus_ocr", min(99, score)

    # 3) Otherwise, resolve purely from OCR hits + directory legality
    best_pair = None
    best_score = -1
    for hero, hv in hero_hits.items():
        for boss, bv in boss_hits.items():
            if boss not in HERO_TO_BOSSES.get(hero, set()):
                continue
            score = hv * 10 + bv * 10
            if score > best_score:
                best_score = score
                best_pair = (hero, boss)

    if best_pair and best_score >= 30:
        return best_pair[0], best_pair[1], "ocr_pair_resolved", min(
            95, 60 + best_score
        )

    return "", "", "unresolved", 0


def main():
    print(f"[ocr] Input CSV: {INPUT_CSV}")
    if not ensure_tesseract():
        raise RuntimeError("tesseract is required for OCR verifier script")

    rows = load_rows(INPUT_CSV)
    print(f"[ocr] Rows loaded: {len(rows)}")

    processed = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, row in enumerate(rows, start=1):
            # v14 column name first, then fallbacks
            video_id = (
                row.get("video_id")
                or row.get("videoid")
                or row.get("id")
                or ""
            ).strip()
            if not video_id:
                row.setdefault("verifystatus", "missing_video_id")
                continue

            row.setdefault("verifiedhero", "")
            row.setdefault("verifiedboss", "")
            row.setdefault("verifyconfidence", "")
            row.setdefault("verifystatus", "")
            row.setdefault("verifysource", "")
            row.setdefault("verifyocrtext", "")
            row.setdefault("verifyevidence", "")

            print(f"[ocr] ({idx}/{len(rows)}) video {video_id} ...")

            ocr_texts = []
            hero_hits = Counter()
            boss_hits = Counter()
            evidence = []

            try:
                frames = extract_frames(video_id, tmpdir)
                for frame in frames:
                    text = ocr_image(frame)
                    if text:
                        ocr_texts.append(text)
                    hv, he = fuzzy_hits(text, HERO_KEYS)
                    bv, be = fuzzy_hits(text, BOSS_KEYS)
                    hero_hits.update(hv)
                    boss_hits.update(bv)
                    evidence.extend([f"hero:{x}" for x in he])
                    evidence.extend([f"boss:{x}" for x in be])
            except Exception as e:
                evidence.append(f"ocr_error:{e}")

            hero, boss, source, confidence = choose_verified_pair(
                row, hero_hits, boss_hits
            )
            row["verifiedhero"] = hero
            row["verifiedboss"] = boss
            row["verifyconfidence"] = str(confidence)
            row["verifysource"] = source
            row["verifyocrtext"] = " || ".join(ocr_texts[:20])
            row["verifyevidence"] = " | ".join(evidence[:100])
            row["verifystatus"] = (
                "verified" if hero and boss and confidence >= 70 else "needs_review"
            )

            processed += 1

    print(f"[ocr] Rows processed: {processed}")
    save_rows(OUTPUT_CSV, rows)
    print(f"[ocr] Wrote OCR-verified CSV to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()