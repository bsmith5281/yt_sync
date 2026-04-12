import argparse
import csv
import os
import re
import sys
import time
from collections import Counter

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
PLAYLIST_ID = os.environ.get("PLAYLIST_ID", "PL2wUlQkvGyYfSMMzX1Ak2WR6wNbfny45P")
CREDENTIALS_FILE = os.environ.get("CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.environ.get("TOKEN_FILE", "token.json")
PREVIEW_CSV = os.environ.get("PREVIEW_CSV", "adventure_metadata_preview_safe.csv")
VALIDATED_CSV = os.environ.get("VALIDATED_CSV", "adventure_metadata_validated_safe.csv")
SLEEP_SECONDS = float(os.environ.get("SLEEP_SECONDS", "0.2"))
DRY_RUN_DEFAULT = os.environ.get("DRY_RUN", "TRUE").strip().lower() in {"1", "true", "yes", "y"}
TITLE_SUFFIX = "Book of Heroes Hearthstone Adventure Mode"
DESCRIPTION_TEMPLATE = (
    "Hearthstone Book of Heroes gameplay from Adventure Mode. "
    "Featuring the {hero} vs {boss} encounter. Part of the Hearthstone adventure playlist."
)
NON_ALNUM_RE = re.compile(r"[^a-z0-9' ]+")
SPACES_RE = re.compile(r"\s+")
TIMESTAMP_TITLE_RE = re.compile(r"^Hearthstone Heroes of Warcraft\s+\d{4}\s\d{2}\s\d{2}T\d{2}\s\d{2}\s\d{2}$", re.I)
VS_RE = re.compile(r"\bvs\b", re.I)

HERO_ALIASES = {
    "garrosh": "Garrosh",
    "garrosh of wrath": "Garrosh of Wrath",
    "uther": "Uther",
    "jaina": "Jaina",
    "rexxar": "Rexxar",
    "valeera": "Valeera",
    "anduin": "Anduin",
    "thrall": "Thrall",
    "guldan": "Guldan",
    "gul'dan": "Guldan",
    "nemesis guldan": "Nemesis Guldan",
    "nemesis gul'dan": "Nemesis Guldan",
    "malfurion": "Malfurion",
    "illidan": "Illidan",
    "magni": "Magni",
    "ragnaros": "Ragnaros",
    "kelthuzad": "KelThuzad",
    "kel'thuzad": "KelThuzad",
    "rastakhan": "King Rastakhan",
    "king rastakhan": "King Rastakhan",
    "chef scabbs": "Chef Scabbs",
    "scabbs": "Chef Scabbs",
    "medivh": "Medivh",
    "opera diva tamsin": "Opera Diva Tamsin",
    "opera diva tamsim": "Opera Diva Tamsin",
    "tamsin": "Opera Diva Tamsin",
    "tamsim": "Opera Diva Tamsin",
    "mecha jaraxxus": "Mecha Jaraxxus",
    "jaraxxus": "Mecha Jaraxxus",
    "nzoth": "NZoth",
    "n'zoth": "NZoth",
    "arthas": "Arthas",
}

BOSS_ALIASES = {
    "seriona": "Seriona",
    "whompwhisker": "Whompwhisker",
    "blackseed": "Blackseed",
    "blackseed the vile": "Blackseed",
    "candlebeard": "Candlebeard",
    "giant rat": "Giant Rat",
    "fungalmancer flurgl": "Fungalmancer Flurgl",
    "flurgl": "Fungalmancer Flurgl",
    "frostfur": "Frostfur",
    "chronomancer inara": "Chronomancer Inara",
    "inara": "Chronomancer Inara",
    "george and karl": "George and Karl",
    "gutmook": "Gutmook",
    "pathmaker hamm": "Pathmaker Hamm",
    "hamm": "Pathmaker Hamm",
    "graves the cleric": "Graves the Cleric",
    "graves": "Graves the Cleric",
    "elder brandlemar": "Elder Brandlemar",
    "brandlemar": "Elder Brandlemar",
    "russell the bard": "Russell the Bard",
    "russell": "Russell the Bard",
    "wee whelp": "Wee Whelp",
    "overseer mogark": "Overseer Mogark",
    "mogark": "Overseer Mogark",
    "bristlesnarl": "Bristlesnarl",
    "greatmother geyah": "Greatmother Geyah",
    "kurtrus ashfallen": "Kurtrus Ashfallen",
    "cenarius": "Cenarius",
    "a.f.kay": "A.F.Kay",
    "af kay": "A.F.Kay",
    "spiritseeker azun": "Spiritseeker Azun",
    "waxmancer sturmi": "Waxmancer Sturmi",
    "xol the unscathed": "Xol the Unscathed",
    "king togwaggle": "King Togwaggle",
    "kraxx": "Kraxx",
    "thaddock the thief": "Thaddock the Thief",
}

SUFFIX_PATTERNS = [
    re.compile(r"\s*[\-|–|—|:]?\s*Book of Heroes\s*[\-|–|—|:]?\s*Hearthstone\s*[\-|–|—|:]?\s*Adventure Mode\s*$", re.I),
    re.compile(r"\s*[\-|–|—|:]?\s*Book of Heroes\s*[|]\s*Hearthstone\s*[|]\s*Adventure Mode\s*$", re.I),
    re.compile(r"\s*[\-|–|—|:]?\s*Adventure Mode\s*[|\-–—:]?\s*Hearthstone\s*[|\-–—:]?\s*Book of Heroes\s*$", re.I),
    re.compile(r"\s*[\-|–|—|:]?\s*Book of Heroes\s*$", re.I),
]


def norm(text):
    text = (text or "").strip().lower().replace("-", " ")
    text = NON_ALNUM_RE.sub(" ", text)
    return SPACES_RE.sub(" ", text).strip()


def get_youtube_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def get_all_playlist_video_ids(youtube, playlist_id):
    out = []
    page_token = None
    while True:
        resp = youtube.playlistItems().list(part="snippet,contentDetails", playlistId=playlist_id, maxResults=50, pageToken=page_token).execute()
        for item in resp.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                out.append(vid)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def get_video_details(youtube, ids):
    rows = []
    for chunk in chunked(ids, 50):
        resp = youtube.videos().list(part="snippet,status", id=",".join(chunk), maxResults=50).execute()
        rows.extend(resp.get("items", []))
    return rows


def canonicalize_hero(text):
    return HERO_ALIASES.get(norm(text))


def canonicalize_boss(text):
    return BOSS_ALIASES.get(norm(text))


def strip_known_wrappers(title):
    t = (title or "").strip()
    t = re.sub(r"^Hearthstone Heroes of Warcraft\s*[\-|–|—|:]?\s*", "", t, flags=re.I).strip()
    t = re.sub(r"^Hearthstone\s*[\-|–|—|:]?\s*", "", t, flags=re.I).strip()
    for pat in SUFFIX_PATTERNS:
        new_t = pat.sub("", t).strip()
        if new_t != t:
            t = new_t
    return t.strip(" -–—|:")


def parse_authoritative_pair_from_title(title):
    raw = (title or "").strip()
    if not raw or TIMESTAMP_TITLE_RE.match(raw):
        return None, None, False
    candidate = strip_known_wrappers(raw)
    if not VS_RE.search(candidate):
        return None, None, False
    parts = re.split(r"\bvs\b", candidate, flags=re.I, maxsplit=1)
    if len(parts) != 2:
        return None, None, False
    left = parts[0].strip(" -–—|:")
    right = parts[1].strip(" -–—|:")
    hero = canonicalize_hero(left)
    boss = canonicalize_boss(right)
    if hero and boss:
        return hero, boss, True
    return None, None, False


def build_final_title(hero, boss):
    return f"{hero} vs {boss} {TITLE_SUFFIX}"


def build_final_description(hero, boss):
    return DESCRIPTION_TEMPLATE.format(hero=hero, boss=boss)


def save_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def collect_playlist_rows(youtube):
    ids = get_all_playlist_video_ids(youtube, PLAYLIST_ID)
    videos = get_video_details(youtube, ids)
    rows = []
    for pos, item in enumerate(videos):
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        title = (snippet.get("title") or "").strip()
        hero, boss, trusted = parse_authoritative_pair_from_title(title)
        family = "defaulttimestamp" if TIMESTAMP_TITLE_RE.match(title) else ("trustedpair" if trusted else "other")
        rows.append({
            "playlist_position": pos,
            "video_id": item.get("id", ""),
            "privacy_status": status.get("privacyStatus", ""),
            "old_title": title,
            "old_description": (snippet.get("description") or "").strip(),
            "current_hero": hero or "",
            "current_boss": boss or "",
            "title_family": family,
            "authoritative_pair": "TRUE" if trusted else "FALSE",
            "verification_status": "",
            "verification_reason": "",
            "final_title": "",
            "final_description": "",
            "needs_review": "TRUE",
            "apply": "FALSE",
            "review_notes": "",
        })
    return rows


def run_preview(output_csv):
    youtube = get_youtube_service()
    rows = collect_playlist_rows(youtube)
    counts = Counter(r["title_family"] for r in rows)
    save_csv(output_csv, rows)
    print(f"Fetched playlist rows: {len(rows)}")
    for k in ["trustedpair", "defaulttimestamp", "other"]:
        print(f"  {k}: {counts.get(k, 0)}")
    print(f"\nWrote {output_csv}")


def run_validate(input_csv, output_csv):
    rows = load_csv(input_csv)
    for row in rows:
        old_title = row["old_title"]
        old_desc = row["old_description"]
        hero, boss, trusted = parse_authoritative_pair_from_title(old_title)
        if trusted:
            final_title = build_final_title(hero, boss)
            final_desc = build_final_description(hero, boss)
            changed = final_title != old_title or final_desc != old_desc
            row["current_hero"] = hero
            row["current_boss"] = boss
            row["verification_status"] = "verifiedmatch"
            row["verification_reason"] = "authoritative_existing_title_pair_locked"
            row["final_title"] = final_title
            row["final_description"] = final_desc
            if changed:
                row["needs_review"] = "FALSE"
                row["apply"] = "TRUE"
                row["review_notes"] = "safe_autoapply_existing_pair_normalization"
            else:
                row["needs_review"] = "FALSE"
                row["apply"] = "FALSE"
                row["review_notes"] = "already_canonical_no_change"
        else:
            row["verification_status"] = "quarantined"
            if TIMESTAMP_TITLE_RE.match(old_title):
                row["verification_reason"] = "default_or_timestamp_title_requires_manual_or_separate_stt_workflow"
            else:
                row["verification_reason"] = "no_trusted_existing_pair_detected_do_not_autoapply"
            row["final_title"] = old_title
            row["final_description"] = old_desc
            row["needs_review"] = "TRUE"
            row["apply"] = "FALSE"
            row["review_notes"] = "quarantined_non_authoritative_row"
    save_csv(output_csv, rows)
    print(f"Validated {len(rows)} rows")
    print(f"Wrote {output_csv}")


def run_apply(input_csv, dry_run):
    youtube = get_youtube_service()
    rows = load_csv(input_csv)
    targets = [r for r in rows if str(r.get("apply", "")).upper() == "TRUE"]
    print(f"Rows marked apply=TRUE: {len(targets)}")
    print(f"DRY_RUN={dry_run}")
    updated = 0
    skipped = 0
    for row in targets:
        vid = row["video_id"].strip()
        final_title = row["final_title"].strip()
        final_desc = row["final_description"].strip()
        old_title = row["old_title"].strip()
        hero, boss, trusted = parse_authoritative_pair_from_title(old_title)
        if not trusted:
            print(f"SKIP untrusted row: {vid}")
            skipped += 1
            continue
        if final_title != build_final_title(hero, boss):
            print(f"SKIP title mismatch guard: {vid}")
            skipped += 1
            continue
        current = youtube.videos().list(part="snippet,status", id=vid).execute()
        items = current.get("items", [])
        if not items:
            print(f"SKIP missing video: {vid}")
            skipped += 1
            continue
        item = items[0]
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        live_title = (snippet.get("title") or "").strip()
        live_hero, live_boss, live_trusted = parse_authoritative_pair_from_title(live_title)
        if not live_trusted or live_hero != hero or live_boss != boss:
            print(f"SKIP live metadata drift/conflict: {vid}")
            skipped += 1
            continue
        print(f"\nVIDEO {vid}")
        print(f"OLD TITLE: {live_title}")
        print(f"NEW TITLE: {final_title}")
        if dry_run:
            continue
        body = {
            "id": vid,
            "snippet": {
                "categoryId": snippet.get("categoryId"),
                "title": final_title,
                "description": final_desc,
                "tags": snippet.get("tags", []),
                "defaultLanguage": snippet.get("defaultLanguage"),
            },
            "status": {"privacyStatus": status.get("privacyStatus", "private")},
        }
        youtube.videos().update(part="snippet,status", body=body).execute()
        updated += 1
        time.sleep(SLEEP_SECONDS)
    print(f"\nDone. Updated={updated}, Skipped={skipped}, DryRun={dry_run}")


def main():
    parser = argparse.ArgumentParser(description="Apply-safe YouTube adventure metadata automation")
    sub = parser.add_subparsers(dest="command", required=True)
    p1 = sub.add_parser("preview")
    p1.add_argument("--output", default=PREVIEW_CSV)
    p2 = sub.add_parser("validate")
    p2.add_argument("--input", default=PREVIEW_CSV)
    p2.add_argument("--output", default=VALIDATED_CSV)
    p3 = sub.add_parser("apply")
    p3.add_argument("--input", default=VALIDATED_CSV)
    p3.add_argument("--dry-run", action="store_true", default=DRY_RUN_DEFAULT)
    p3.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.command == "preview":
        run_preview(args.output)
    elif args.command == "validate":
        run_validate(args.input, args.output)
    elif args.command == "apply":
        run_apply(args.input, dry_run=(False if args.live else args.dry_run))

if __name__ == "__main__":
    main()
