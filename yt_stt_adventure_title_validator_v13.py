import csv
import os
import re
import sys
import tempfile
import subprocess
from collections import Counter, defaultdict
from difflib import SequenceMatcher

try:
    import whisper
except Exception:
    whisper = None

# ---------------------------------------------------------------------------
# Config: pure hero/boss detection for Adventure playlist default-timestamp videos
# ---------------------------------------------------------------------------

INPUTCSV = os.environ.get("INPUTCSV", "adventure_metadata_validated_v9.csv")
OUTPUTCSV = os.environ.get("OUTPUTCSV", "adventure_hero_boss_from_stt_v12.csv")

# Focus on just the intro call.
CLIP_DURATION_SECONDS = int(os.environ.get("CLIP_DURATION_SECONDS", "10"))

# Use strong model by default for best name recognition.
WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL_NAME", "large-v2")

# Only operate on this playlist and timestamp titles.
ADVENTURE_PLAYLIST_ID = "PL2wUlQkvGyYfSMMzX1Ak2WR6wNbfny45P"

# ---------------------------------------------------------------------------
# Name dictionaries (unchanged from v11 core)
# ---------------------------------------------------------------------------

HIGH_RISK_HEROES = {"Garrosh", "King Rastakhan", "Nemesis Guldan"}

HERO_ALIASES = {
    "garrosh": "Garrosh", "garrosh of wrath": "Garrosh", "uther": "Uther", "jaina": "Jaina",
    "rexxar": "Rexxar", "valeera": "Valeera", "anduin": "Anduin", "thrall": "Thrall",
    "guldan": "Guldan", "gul'dan": "Guldan", "nemesis guldan": "Nemesis Guldan",
    "nemesis gul'dan": "Nemesis Guldan", "malfurion": "Malfurion", "illidan": "Illidan",
    "magni": "Magni", "ragnaros": "Ragnaros", "kelthuzad": "KelThuzad", "kel'thuzad": "KelThuzad",
    "rastakhan": "King Rastakhan", "king rastakhan": "King Rastakhan", "chef scabbs": "Chef Scabbs",
    "scabbs": "Chef Scabbs", "medivh": "Medivh", "opera diva tamsin": "Opera Diva Tamsin",
    "opera diva tamsim": "Opera Diva Tamsin", "tamsin": "Opera Diva Tamsin", "tamsim": "Opera Diva Tamsin",
    "mecha jaraxxus": "Mecha Jaraxxus", "jaraxxus": "Mecha Jaraxxus", "nzoth": "NZoth",
    "n'zoth": "NZoth", "arthas": "Arthas",
}

BOSS_ALIASES = {
    "seriona": "Seriona", "whompwhisker": "Whompwhisker", "blackseed": "Blackseed",
    "blackseed the vile": "Blackseed", "candlebeard": "Candlebeard", "giant rat": "Giant Rat",
    "fungalmancer flurgl": "Fungalmancer Flurgl", "flurgl": "Fungalmancer Flurgl", "frostfur": "Frostfur",
    "chronomancer inara": "Chronomancer Inara", "inara": "Chronomancer Inara", "george and karl": "George and Karl",
    "george": "George and Karl", "karl": "George and Karl", "gutmook": "Gutmook",
    "pathmaker hamm": "Pathmaker Hamm", "hamm": "Pathmaker Hamm", "graves the cleric": "Graves the Cleric",
    "graves": "Graves the Cleric", "elder brandlemar": "Elder Brandlemar", "brandlemar": "Elder Brandlemar",
    "russell the bard": "Russell the Bard", "russell": "Russell the Bard", "wee whelp": "Wee Whelp",
    "overseer mogark": "Overseer Mogark", "mogark": "Overseer Mogark", "bristlesnarl": "Bristlesnarl",
    "greatmother geyah": "Greatmother Geyah", "kurtrus ashfallen": "Kurtrus Ashfallen", "cenarius": "Cenarius",
    "a.f.kay": "A.F.Kay", "af kay": "A.F.Kay", "spiritseeker azun": "Spiritseeker Azun",
    "waxmancer sturmi": "Waxmancer Sturmi", "xol the unscathed": "Xol the Unscathed",
    "king togwaggle": "King Togwaggle", "kraxx": "Kraxx", "thaddock the thief": "Thaddock the Thief",
}

HERO_TO_BOSSES = {
    "Garrosh": {
        "Greatmother Geyah", "Elder Brandlemar", "Seriona", "Kraxx", "Giant Rat", "Frostfur",
        "Russell the Bard", "Wee Whelp", "Bristlesnarl", "Pathmaker Hamm", "Thaddock the Thief",
        "Waxmancer Sturmi", "Blackseed", "Graves the Cleric",
    },
    "King Rastakhan": {
        "Giant Rat", "Candlebeard", "Seriona", "Blackseed", "Whompwhisker", "Chronomancer Inara",
        "King Togwaggle", "Wee Whelp", "Frostfur", "Graves the Cleric",
    },
    "KelThuzad": {
        "George and Karl", "Wee Whelp", "Overseer Mogark", "Frostfur", "A.F.Kay",
    },
    "Medivh": {"Fungalmancer Flurgl", "Graves the Cleric"},
    "Chef Scabbs": {"Gutmook"},
    "Nemesis Guldan": {"Spiritseeker Azun", "Opera Diva Tamsin", "Blackseed"},
    "Opera Diva Tamsin": {"Overseer Mogark", "George and Karl"},
    "Mecha Jaraxxus": {"Xol the Unscathed"},
    "NZoth": {"Waxmancer Sturmi", "Frostfur"},
    "Illidan": {"Kurtrus Ashfallen"},
    "Malfurion": {"Cenarius"},
}

TIMESTAMP_TITLE_RE = re.compile(
    r"Hearthstone Heroes of Warcraft\s+\d{4}\s\d{2}\s\d{2}T\d{2}\s\d{2}\s\d{2}",
    re.I,
)
PAIR_RE = re.compile(r"(?P<hero>.+?)\s+vs\s+(?P<boss>.+)", re.I)
NON_ALNUM_RE = re.compile(r"[^a-z0-9' ]+")
SPACES_RE = re.compile(r"\s+")

ALL_NAME_LOOKUP = {}
for k, v in HERO_ALIASES.items():
    ALL_NAME_LOOKUP[k] = v
for k, v in BOSS_ALIASES.items():
    ALL_NAME_LOOKUP[k] = v

HERO_VALUES = set(HERO_ALIASES.values())
BOSS_VALUES = set(BOSS_ALIASES.values())

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def norm(s):
    s = (s or "").strip().lower().replace("-", " ")
    s = NON_ALNUM_RE.sub(" ", s)
    return SPACES_RE.sub(" ", s).strip()

def load_rows(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find input CSV: {path}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def save_rows(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def ensure_columns(rows):
    extras = [
        "heroname", "bossname",
        "sttraw", "sttconfidence",
        "pairscore", "reviewnotes",
        "evidence_transcript",
    ]
    for row in rows:
        for c in extras:
            row.setdefault(c, "")
    return rows

def looks_like_default(row):
    if str(row.get("istargetdefault", "")).strip().upper() == "TRUE":
        return True
    title = (row.get("oldtitle") or row.get("title") or "").strip()
    return bool(TIMESTAMP_TITLE_RE.search(title))

def canonicalize_name(text, is_hero=None):
    n = norm(text)
    if not n:
        return None
    if is_hero is True:
        return HERO_ALIASES.get(n)
    if is_hero is False:
        return BOSS_ALIASES.get(n)
    return ALL_NAME_LOOKUP.get(n)

def parse_pair_text(text):
    text = (text or "").strip()
    m = PAIR_RE.search(text)
    if not m:
        return None, None
    hero = canonicalize_name(m.group("hero"), is_hero=True)
    boss = canonicalize_name(m.group("boss"), is_hero=False)
    return hero, boss

def extract_names_with_scores(text, allowed_names=None):
    cleaned = norm(text)
    if not cleaned:
        return Counter()
    tokens = cleaned.split()
    windows = []
    for size in range(1, min(4, len(tokens)) + 1):
        for i in range(0, len(tokens) - size + 1):
            windows.append(" ".join(tokens[i:i + size]))
    scores = Counter()
    candidates = list(dict.fromkeys(allowed_names or ALL_NAME_LOOKUP.keys()))
    for phrase in windows:
        for cand in candidates:
            ratio = SequenceMatcher(None, phrase, cand).ratio()
            if cand in phrase or phrase in cand:
                ratio = max(ratio, 0.97)
            if ratio >= 0.90:
                scores[ALL_NAME_LOOKUP[cand]] = max(
                    scores[ALL_NAME_LOOKUP[cand]], round(ratio, 3)
                )
    return scores

def classify_stt_confidence(transcript_text, evidence):
    if not transcript_text:
        return "none"
    found = len(evidence.get("transcript", set()))
    if found >= 2:
        return "high"
    if found == 1:
        return "medium"
    return "low"

def build_watch_url(video_id):
    return f"https://youtu.be/{video_id}"

def download_clip(video_id, outdir):
    outpath = os.path.join(outdir, f"{video_id}.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "--max-filesize", "50m",
        "--download-sections", f"*0-{CLIP_DURATION_SECONDS}",
        "-o", outpath,
        build_watch_url(video_id),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for fname in os.listdir(outdir):
        if fname.startswith(video_id + "."):
            return os.path.join(outdir, fname)
    raise FileNotFoundError(f"Audio clip not found for {video_id}")

def transcribe_clip(model, audio_path):
    result = model.transcribe(audio_path, language="en")
    return (result.get("text") or "").strip()

def collect_transcript_evidence(transcript_text):
    evidence = defaultdict(set)
    scores = extract_names_with_scores(transcript_text)
    for canonical in scores:
        evidence["transcript"].add(canonical)
    return evidence

def choose_pair_from_transcript(transcript_evidence):
    hero_votes = Counter()
    boss_votes = Counter()
    for _, names in transcript_evidence.items():
        for name in names:
            if name in HERO_VALUES:
                hero_votes[name] += 1
            if name in BOSS_VALUES:
                boss_votes[name] += 1

    chosen_hero = None
    if hero_votes:
        chosen_hero, _ = hero_votes.most_common(1)[0]

    allowed_bosses = HERO_TO_BOSSES.get(chosen_hero, set()) if chosen_hero else set()
    filtered_boss_votes = Counter({
        k: v for k, v in boss_votes.items()
        if not allowed_bosses or k in allowed_bosses
    })

    chosen_boss = None
    if filtered_boss_votes:
        chosen_boss, _ = filtered_boss_votes.most_common(1)[0]

    hero_support = hero_votes.get(chosen_hero, 0) if chosen_hero else 0
    boss_support = filtered_boss_votes.get(chosen_boss, 0) if chosen_boss else 0
    pair_score = hero_support * 4 + boss_support * 4
    if chosen_hero and chosen_boss and chosen_boss in HERO_TO_BOSSES.get(chosen_hero, {chosen_boss}):
        pair_score += 2

    return chosen_hero, chosen_boss, pair_score

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    rows = ensure_columns(load_rows(INPUTCSV))

    # Only rows in the Adventure playlist and with default timestamp titles.
    targets = []
    for r in rows:
        playlist_id = (r.get("playlistid") or r.get("playlist_id") or "").strip()
        if playlist_id != ADVENTURE_PLAYLIST_ID:
            continue
        if not looks_like_default(r):
            continue
        targets.append(r)

    print(f"Total rows: {len(rows)}")
    print(f"Target Adventure default-timestamp rows: {len(targets)}")

    model = None
    if whisper is not None:
        try:
            print(f"Loading Whisper model: {WHISPER_MODEL_NAME}")
            model = whisper.load_model(WHISPER_MODEL_NAME)
        except Exception as e:
            print(f"Failed to load Whisper model: {e}", file=sys.stderr)
            model = None
    else:
        print("whisper module not available; no STT will be performed.", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, row in enumerate(targets, start=1):
            video_id = (row.get("videoid") or row.get("id") or "").strip()
            print(f"[{idx}/{len(targets)}] {video_id} ...")
            transcript_text = ""
            transcript_evidence = defaultdict(set)
            hero = None
            boss = None
            pair_score = 0

            if model is not None and video_id:
                try:
                    clip = download_clip(video_id, tmpdir)
                    transcript_text = transcribe_clip(model, clip)
                    transcript_evidence = collect_transcript_evidence(transcript_text)
                    hero, boss, pair_score = choose_pair_from_transcript(transcript_evidence)
                except Exception as e:
                    print(f"  STT failed for {video_id}: {e}", file=sys.stderr)
                    transcript_text = ""
                    transcript_evidence = defaultdict(set)
                    hero, boss, pair_score = None, None, 0

            # Update row with just STT + guessed hero/boss.
            row["heroname"] = hero or ""
            row["bossname"] = boss or ""
            row["sttraw"] = transcript_text
            row["sttconfidence"] = classify_stt_confidence(transcript_text, transcript_evidence)
            row["pairscore"] = str(pair_score)
            row["reviewnotes"] = "transcript_only_v12"
            row["evidence_transcript"] = ", ".join(sorted(transcript_evidence.get("transcript", set())))

    fieldnames = list(rows[0].keys()) if rows else []
    save_rows(OUTPUTCSV, fieldnames, rows)
    print(OUTPUTCSV)

if __name__ == "__main__":
    main()