"""
Safe validator for Hearthstone adventure titles.

Core rule:
- If the current title already contains a recognizable Hero vs Boss pair, preserve it.
- STT/fuzzy evidence may fill missing pairs for timestamp/default titles.
- Formatting-only normalization on a preserved pair is safe to auto-apply.
- Conflicting replacements are blocked.
"""

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

from hearthstone_adventure_directory_v2 import canonicalize_name, HERO_TO_BOSSES, HERO_ALIASES, BOSS_ALIASES

INPUTCSV = os.environ.get("INPUTCSV", "adventure_metadata_validated_v9.csv")
OUTPUTCSV = os.environ.get("OUTPUTCSV", "adventure_metadata_generated_v12.csv")
CLIP_DURATION_SECONDS = int(os.environ.get("CLIP_DURATION_SECONDS", "18"))
WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL_NAME", "base")
TITLE_SUFFIX = "â€“ Book of Heroes | Hearthstone | Adventure Mode"
DESCRIPTION_TEMPLATE = (
    "Hearthstone Book of Heroes gameplay from Adventure Mode.\n\n"
    "Featuring the {hero} vs {boss} encounter.\n\n"
    "Part of the Hearthstone adventure playlist."
)

TIMESTAMP_TITLE_RE = re.compile(r"Hearthstone\s*(?:\[Heroes of Warcraft\]|Heroes of Warcraft)\s+\d{4}[\s_]\d{2}[\s_]\d{2}T\d{2}[\s_]\d{2}[\s_]\d{2}", re.I)
PAIR_ANYWHERE_RE = re.compile(r"(?P<hero>[^\n|]+?)\s+vs\s+(?P<boss>[^\n|]+?)\s*(?:[â€“\-|]|$)", re.I)
NON_ALNUM_RE = re.compile(r"[^a-z0-9' .]+")
SPACES_RE = re.compile(r"\s+")
ALL_NAME_KEYS = list(dict.fromkeys(list(HERO_ALIASES.keys()) + list(BOSS_ALIASES.keys())))


def norm(s):
    s = (s or "").strip().lower().replace("-", " ")
    s = NON_ALNUM_RE.sub(" ", s)
    return SPACES_RE.sub(" ", s).strip()


def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_rows(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def ensure_columns(rows):
    extras = [
        "heroname", "bossname", "finaltitle", "finaldescription", "sttraw", "sttconfidence",
        "verificationstatus", "verificationreason", "reviewnotes", "pairscore", "apply",
        "evidence_title", "evidence_description", "evidence_transcript", "evidence_metadata",
        "preservedcurrentpair", "currentpairparsed",
    ]
    for row in rows:
        for c in extras:
            row.setdefault(c, "")
    return rows


def looks_like_default(row):
    title = (row.get("oldtitle") or row.get("old_title") or row.get("title") or "").strip()
    return bool(TIMESTAMP_TITLE_RE.search(title)) or str(row.get("istargetdefault") or row.get("is_target_default") or "").strip().upper() == "TRUE"


def parse_pair_from_title(text):
    text = (text or "").strip()
    if not text:
        return None, None
    m = PAIR_ANYWHERE_RE.search(text)
    if not m:
        return None, None
    hero = canonicalize_name(m.group("hero"), role="hero")
    boss = canonicalize_name(m.group("boss"), role="boss")
    if hero and boss:
        return hero, boss
    return None, None


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
    for phrase in windows:
        for cand in (allowed_names or ALL_NAME_KEYS):
            ratio = SequenceMatcher(None, phrase, cand).ratio()
            if cand in phrase or phrase in cand:
                ratio = max(ratio, 0.97)
            if ratio >= 0.90:
                canonical = canonicalize_name(cand)
                if canonical:
                    scores[canonical] = max(scores[canonical], round(ratio, 3))
    return scores


def build_watch_url(video_id):
    return f"https://youtu.be/{video_id}"


def download_clip(video_id, outdir):
    outpath = os.path.join(outdir, f"{video_id}.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "--max-filesize", "50m", "--download-sections", f"*0-{CLIP_DURATION_SECONDS}", "-o", outpath, build_watch_url(video_id)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for fname in os.listdir(outdir):
        if fname.startswith(video_id + "."):
            return os.path.join(outdir, fname)
    raise FileNotFoundError(f"Audio clip not found for {video_id}")


def transcribe_clip(model, audio_path):
    result = model.transcribe(audio_path, language="en")
    return (result.get("text") or "").strip()


def collect_evidence(row, transcript_text):
    evidence = defaultdict(set)
    title = row.get("oldtitle") or row.get("old_title") or row.get("title") or ""
    desc = row.get("olddescription") or row.get("old_description") or row.get("description") or ""
    metadata_blob = " ".join([
        row.get("currenthero", ""), row.get("currentboss", ""), row.get("heroname", ""), row.get("bossname", ""),
        row.get("suggestedtitle", ""), row.get("suggested_title", ""), row.get("previousformattedtitle", ""), row.get("previous_formatted_title", ""),
        row.get("nextformattedtitle", ""), row.get("next_formatted_title", ""), row.get("seriesname", ""), row.get("series_name", ""), row.get("mode", "")
    ])
    for source_name, text in {"title": title, "description": desc, "metadata": metadata_blob, "transcript": transcript_text or ""}.items():
        scores = extract_names_with_scores(text)
        for canonical in scores:
            evidence[source_name].add(canonical)
    return evidence


def infer_current_pair(row):
    title = row.get("oldtitle") or row.get("old_title") or row.get("title") or ""
    hero, boss = parse_pair_from_title(title)
    if hero and boss:
        return hero, boss, True
    hero = canonicalize_name(row.get("currenthero") or row.get("hero_name") or row.get("heroname"), role="hero")
    boss = canonicalize_name(row.get("currentboss") or row.get("boss_name") or row.get("bossname"), role="boss")
    return hero, boss, False


def choose_pair(row, evidence):
    current_hero, current_boss, current_title_has_pair = infer_current_pair(row)

    if current_title_has_pair and current_hero and current_boss:
        return {
            "hero": current_hero,
            "boss": current_boss,
            "status": "verifiedmatch",
            "reason": "recognizedpairpreservedfromcurrenttitle",
            "apply": "TRUE",
            "pairscore": "100",
            "preserved": "TRUE",
            "parsed": "TRUE",
        }

    hero_votes = Counter()
    boss_votes = Counter()
    for _, names in evidence.items():
        for name in names:
            if name in HERO_TO_BOSSES:
                hero_votes[name] += 1
            hero_bosses = set().union(*HERO_TO_BOSSES.values())
            if name in hero_bosses:
                boss_votes[name] += 1

    chosen_hero = hero_votes.most_common(1)[0][0] if hero_votes else current_hero
    allowed_bosses = HERO_TO_BOSSES.get(chosen_hero, set()) if chosen_hero else set()
    filtered = Counter({k: v for k, v in boss_votes.items() if not allowed_bosses or k in allowed_bosses})
    chosen_boss = filtered.most_common(1)[0][0] if filtered else current_boss
    hero_support = hero_votes.get(chosen_hero, 0) if chosen_hero else 0
    boss_support = filtered.get(chosen_boss, 0) if chosen_boss else 0
    score = hero_support * 5 + boss_support * 5

    if looks_like_default(row) and chosen_hero and chosen_boss and hero_support >= 2 and boss_support >= 2:
        return {
            "hero": chosen_hero,
            "boss": chosen_boss,
            "status": "verifiedmatch",
            "reason": "filledmissingpairfrommultisourceevidence",
            "apply": "TRUE",
            "pairscore": str(score),
            "preserved": "FALSE",
            "parsed": "FALSE",
        }

    return {
        "hero": current_hero or chosen_hero,
        "boss": current_boss or chosen_boss,
        "status": "uncertain",
        "reason": "insufficientevidencefornewpair",
        "apply": "FALSE",
        "pairscore": str(score),
        "preserved": "FALSE",
        "parsed": "FALSE",
    }


def build_final_title(hero, boss, row):
    if hero and boss:
        return f"{hero} vs {boss} {TITLE_SUFFIX}"
    return (row.get("suggestedtitle") or row.get("suggested_title") or row.get("oldtitle") or row.get("old_title") or row.get("title") or "").strip()


def build_final_description(hero, boss, row):
    if hero and boss:
        return DESCRIPTION_TEMPLATE.format(hero=hero, boss=boss)
    return (row.get("olddescription") or row.get("old_description") or row.get("description") or "").strip()
