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

INPUTCSV = os.environ.get("INPUTCSV", "adventure_metadata_validated_v7.csv")
OUTPUTCSV = os.environ.get("OUTPUTCSV", "adventure_metadata_generated_v10.csv")
CLIP_DURATION_SECONDS = int(os.environ.get("CLIP_DURATION_SECONDS", "18"))
WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL_NAME", "base")
TITLE_SUFFIX = "Book of Heroes Hearthstone Adventure Mode"
DESCRIPTION_TEMPLATE = (
    "Hearthstone Book of Heroes gameplay from Adventure Mode. "
    "Featuring the {hero} vs {boss} encounter. Part of the Hearthstone adventure playlist."
)
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
    "Garrosh": {"Greatmother Geyah", "Elder Brandlemar", "Seriona", "Kraxx", "Giant Rat", "Frostfur", "Russell the Bard", "Wee Whelp", "Bristlesnarl", "Pathmaker Hamm", "Thaddock the Thief", "Waxmancer Sturmi", "Blackseed", "Graves the Cleric"},
    "King Rastakhan": {"Giant Rat", "Candlebeard", "Seriona", "Blackseed", "Whompwhisker", "Chronomancer Inara", "King Togwaggle", "Wee Whelp", "Frostfur", "Graves the Cleric"},
    "KelThuzad": {"George and Karl", "Wee Whelp", "Overseer Mogark", "Frostfur", "A.F.Kay"},
    "Medivh": {"Fungalmancer Flurgl", "Graves the Cleric"},
    "Chef Scabbs": {"Gutmook"},
    "Nemesis Guldan": {"Spiritseeker Azun", "Opera Diva Tamsin", "Blackseed"},
    "Opera Diva Tamsin": {"Overseer Mogark", "George and Karl"},
    "Mecha Jaraxxus": {"Xol the Unscathed"},
    "NZoth": {"Waxmancer Sturmi", "Frostfur"},
    "Illidan": {"Kurtrus Ashfallen"},
    "Malfurion": {"Cenarius"},
}

TIMESTAMP_TITLE_RE = re.compile(r"Hearthstone Heroes of Warcraft\s+\d{4}\s\d{2}\s\d{2}T\d{2}\s\d{2}\s\d{2}", re.I)
FORMATTED_TITLE_RE = re.compile(r"^(?P<pair>.+?)\s+Book of Heroes Hearthstone Adventure Mode$", re.I)
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
        "heroname", "bossname", "finaltitle", "finaldescription", "sttraw", "sttconfidence",
        "verificationstatus", "verificationreason", "reviewnotes", "pairscore", "apply",
        "evidence_title", "evidence_description", "evidence_transcript", "evidence_metadata",
        "preservedcurrentpair",
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


def is_book_of_heroes_row(row):
    blob = " ".join([
        row.get("seriesname", ""), row.get("mode", ""), row.get("title", ""), row.get("oldtitle", ""), row.get("description", ""), row.get("olddescription", "")
    ]).lower()
    return "book of heroes" in blob or "adventure mode" in blob


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
    m = FORMATTED_TITLE_RE.match(text)
    if m:
        text = m.group("pair").strip()
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
                scores[ALL_NAME_LOOKUP[cand]] = max(scores[ALL_NAME_LOOKUP[cand]], round(ratio, 3))
    return scores


def build_watch_url(video_id):
    return f"https://youtu.be/{video_id}"


def download_clip(video_id, outdir):
    outpath = os.path.join(outdir, f"{video_id}.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "--max-filesize", "50m",
        "--download-sections", f"*0-{CLIP_DURATION_SECONDS}", "-o", outpath, build_watch_url(video_id),
    ]
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
    title = row.get("oldtitle") or row.get("title") or ""
    desc = row.get("olddescription") or row.get("description") or ""
    metadata_blob = " ".join([
        row.get("currenthero", ""), row.get("currentboss", ""), row.get("heroname", ""), row.get("bossname", ""),
        row.get("suggestedtitle", ""), row.get("previousformattedtitle", ""), row.get("nextformattedtitle", ""),
        row.get("seriesname", ""), row.get("mode", "")
    ])
    for source_name, text in {"title": title, "description": desc, "metadata": metadata_blob, "transcript": transcript_text or ""}.items():
        scores = extract_names_with_scores(text)
        for canonical in scores:
            evidence[source_name].add(canonical)
    return evidence


def infer_current_pair(row):
    title = row.get("oldtitle") or row.get("title") or ""
    hero, boss = parse_pair_text(title)
    if hero and boss:
        return hero, boss
    hero = canonicalize_name(row.get("currenthero"), is_hero=True) or canonicalize_name(row.get("heroname"), is_hero=True)
    boss = canonicalize_name(row.get("currentboss"), is_hero=False) or canonicalize_name(row.get("bossname"), is_hero=False)
    return hero, boss


def choose_pair(row, evidence):
    current_hero, current_boss = infer_current_pair(row)
    hero_votes = Counter()
    boss_votes = Counter()
    for _, names in evidence.items():
        for name in names:
            if name in HERO_VALUES:
                hero_votes[name] += 1
            if name in BOSS_VALUES:
                boss_votes[name] += 1

    chosen_hero = current_hero
    if hero_votes:
        top_hero, hero_count = hero_votes.most_common(1)[0]
        if hero_count >= 2 or not current_hero:
            chosen_hero = top_hero

    allowed_bosses = HERO_TO_BOSSES.get(chosen_hero, set()) if chosen_hero else set()
    filtered_boss_votes = Counter({k: v for k, v in boss_votes.items() if not allowed_bosses or k in allowed_bosses})

    chosen_boss = current_boss
    if filtered_boss_votes:
        top_boss, boss_count = filtered_boss_votes.most_common(1)[0]
        if current_boss == top_boss:
            chosen_boss = top_boss
        elif boss_count >= 2:
            chosen_boss = top_boss

    title_pair_supported = bool(current_hero and current_boss and current_hero in evidence["title"] and current_boss in evidence["title"])
    replacing_existing_boss = bool(current_boss and chosen_boss and current_boss != chosen_boss)
    metadata_conflict = bool(replacing_existing_boss and current_boss in evidence["metadata"])

    pair_score = 0
    if chosen_hero:
        pair_score += hero_votes.get(chosen_hero, 0) * 4
    if chosen_boss:
        pair_score += filtered_boss_votes.get(chosen_boss, 0) * 4
    if chosen_hero and chosen_boss and chosen_boss in HERO_TO_BOSSES.get(chosen_hero, {chosen_boss}):
        pair_score += 2
    if title_pair_supported:
        pair_score += 4
    if metadata_conflict:
        pair_score -= 5

    if current_hero and current_boss and replacing_existing_boss and (metadata_conflict or filtered_boss_votes.get(chosen_boss, 0) < 2):
        return {
            "hero": current_hero, "boss": current_boss, "status": "preservedmatch",
            "reason": "preservedcurrentpairinsufficientevidencetooverwriteboss", "apply": "FALSE",
            "pairscore": str(max(pair_score, 0)), "preserved": "TRUE",
        }

    if chosen_hero and chosen_boss:
        current_same = current_hero == chosen_hero and current_boss == chosen_boss
        if current_same:
            status = "verifiedmatch"
            reason = "currenttitlematchesevidencebackedpair"
            apply = "TRUE"
        elif (
            pair_score >= 16
            and filtered_boss_votes.get(chosen_boss, 0) >= 3
            and not metadata_conflict
            and chosen_hero not in HIGH_RISK_HEROES
        ):
            status = "needsupdate"
            reason = "evidencebackedpairdiffersfromcurrenttitle"
            apply = "TRUE"
        else:
            status = "uncertain"
            reason = "pairnotstrongenoughforautomaticreplacement"
            apply = "FALSE"

        if chosen_hero in HIGH_RISK_HEROES and current_same:
            apply = "TRUE"

        return {
            "hero": chosen_hero, "boss": chosen_boss, "status": status,
            "reason": reason, "apply": apply, "pairscore": str(max(pair_score, 0)), "preserved": "FALSE",
        }

    if current_hero and current_boss:
        return {
            "hero": current_hero, "boss": current_boss, "status": "uncertain",
            "reason": "keptcurrentpairinsufficientnewsignal", "apply": "FALSE",
            "pairscore": str(max(pair_score, 0)), "preserved": "TRUE",
        }

    return {
        "hero": current_hero, "boss": current_boss, "status": "uncertain",
        "reason": "insufficientmatchdata", "apply": "FALSE",
        "pairscore": str(max(pair_score, 0)), "preserved": "FALSE",
    }


def build_final_title(hero, boss, row):
    if hero and boss:
        return f"{hero} vs {boss} {TITLE_SUFFIX}"
    return (row.get("suggestedtitle") or row.get("oldtitle") or row.get("title") or "").strip()


def build_final_description(hero, boss, row):
    if hero and boss:
        return DESCRIPTION_TEMPLATE.format(hero=hero, boss=boss)
    return (row.get("olddescription") or row.get("description") or "").strip()


def classify_stt_confidence(transcript_text, evidence):
    if not transcript_text:
        return "none"
    found = len(evidence.get("transcript", set()))
    if found >= 2:
        return "high"
    if found == 1:
        return "medium"
    return "low"


def main():
    rows = ensure_columns(load_rows(INPUTCSV))
    targets = [r for r in rows if is_book_of_heroes_row(r) or looks_like_default(r) or FORMATTED_TITLE_RE.match((r.get("oldtitle") or r.get("title") or "").strip())]

    model = None
    if whisper is not None:
        try:
            model = whisper.load_model(WHISPER_MODEL_NAME)
        except Exception:
            model = None

    with tempfile.TemporaryDirectory() as tmpdir:
        for row in targets:
            video_id = (row.get("videoid") or row.get("id") or "").strip()
            transcript_text = ""
            if model is not None and video_id:
                try:
                    clip = download_clip(video_id, tmpdir)
                    transcript_text = transcribe_clip(model, clip)
                except Exception:
                    transcript_text = ""

            evidence = collect_evidence(row, transcript_text)
            decision = choose_pair(row, evidence)
            hero = decision["hero"]
            boss = decision["boss"]

            row["heroname"] = hero or ""
            row["bossname"] = boss or ""
            row["sttraw"] = transcript_text
            row["sttconfidence"] = classify_stt_confidence(transcript_text, evidence)
            row["verificationstatus"] = decision["status"]
            row["verificationreason"] = decision["reason"]
            row["pairscore"] = decision["pairscore"]
            row["apply"] = decision["apply"]
            row["preservedcurrentpair"] = decision["preserved"]
            row["finaltitle"] = build_final_title(hero, boss, row)
            row["finaldescription"] = build_final_description(hero, boss, row)
            row["evidence_title"] = ", ".join(sorted(evidence["title"]))
            row["evidence_description"] = ", ".join(sorted(evidence["description"]))
            row["evidence_transcript"] = ", ".join(sorted(evidence["transcript"]))
            row["evidence_metadata"] = ", ".join(sorted(evidence["metadata"]))

            notes = []
            if decision["status"] == "preservedmatch":
                notes.append("conflicting boss inference blocked")
            if decision["status"] == "uncertain":
                notes.append("manual review recommended")
            if decision["status"] == "needsupdate":
                notes.append("multi-source evidence supports replacement")
            if decision["status"] == "verifiedmatch":
                notes.append("safe auto-validated")
            row["reviewnotes"] = "; ".join(notes)

    fieldnames = list(rows[0].keys()) if rows else []
    save_rows(OUTPUTCSV, fieldnames, rows)
    print(OUTPUTCSV)


if __name__ == "__main__":
    main()
