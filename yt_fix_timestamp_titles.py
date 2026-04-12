"""
yt_fix_timestamp_titles.py

For every video in the playlist whose title is still a raw timestamp,
this script:
  1. Fetches the LIVE description from YouTube (not a stale CSV)
  2. Parses the Hero vs Boss pair out of it
  3. Updates the title to: "Hero vs Boss – Book of Heroes | Hearthstone | Adventure Mode"
  4. Also standardizes the description to the clean 3-line format

If the live description doesn't contain a parseable Hero vs Boss pair,
the video is logged and skipped — nothing is written to YouTube.

No Whisper. No STT. No validator. No stale CSVs.

USAGE:
  python yt_fix_timestamp_titles.py   # DRY_RUN=True by default
  # Review the printed OLD -> NEW pairs, then set DRY_RUN = False and run again.
"""

import os
import re
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCOPES           = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"
PLAYLIST_ID      = "PL2wUlQkvGyYfSMMzX1Ak2WR6wNbfny45P"

DRY_RUN          = True     # flip to False when OLD->NEW pairs look correct
SLEEP_SECONDS    = 0.3

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Matches the raw auto-generated timestamp title formats:
#   "Hearthstone Heroes of Warcraft 2026 03 16T19 24 22"
#   "Hearthstone [Heroes of Warcraft] 2026 03 20T00 03 40"
#   "Hearthstone  Heroes of Warcraft   2026 03 16T19 24 22 *"
TIMESTAMP_TITLE_RE = re.compile(
    r"Hearthstone\s*(?:\[Heroes of Warcraft\]|Heroes of Warcraft)\s*"
    r"\d{4}[\s_]\d{2}[\s_]\d{2}T\d{2}[\s_]\d{2}[\s_]\d{2}",
    re.IGNORECASE,
)

# Matches "X vs Y – Series | ..." anywhere in the description
DESC_PAIR_RE = re.compile(
    r"^(?P<hero>.+?)\s+vs\s+(?P<boss>.+?)\s+[–\-]\s+(?P<series>[^|]+?)\s+\|",
    re.IGNORECASE | re.MULTILINE,
)

# Matches "featuring the X vs Y boss fight/encounter" style
FEATURING_RE = re.compile(
    r"featuring the\s+(?P<hero>.+?)\s+vs\s+(?P<boss>.+?)\s+(?:boss fight|encounter)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

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
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_playlist_video_ids(youtube, playlist_id):
    ids = []
    token = None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=token,
        ).execute()
        for item in resp.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                ids.append(vid)
        token = resp.get("nextPageToken")
        if not token:
            break
    return ids


def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def get_video_details(youtube, video_ids):
    videos = {}
    for chunk in chunked(video_ids, 50):
        resp = youtube.videos().list(
            part="snippet,status",
            id=",".join(chunk),
        ).execute()
        for item in resp.get("items", []):
            videos[item["id"]] = item
    return videos


def looks_like_timestamp(text):
    """Returns True if text contains a year-like number — rejects bad parses."""
    return bool(re.search(r"\b20\d{2}\b", text))


def parse_pair_from_description(desc):
    """
    Extracts hero and boss from the live YouTube description.

    Priority order:
      1. "Hero vs Boss – Series | Hearthstone | Adventure Mode" on any line
      2. "featuring the Hero vs Boss boss fight/encounter" anywhere in text

    Returns (hero, boss, series) or ("", "", "").
    """
    if not desc:
        return "", "", ""

    # Priority 1 — structured title-format line
    for m in DESC_PAIR_RE.finditer(desc):
        hero   = m.group("hero").strip()
        boss   = m.group("boss").strip()
        series = m.group("series").strip()
        if looks_like_timestamp(hero) or looks_like_timestamp(boss):
            continue
        if len(hero) < 2 or len(boss) < 2:
            continue
        return hero, boss, series

    # Priority 2 — featuring-the line
    m = FEATURING_RE.search(desc)
    if m:
        hero = m.group("hero").strip()
        boss = m.group("boss").strip()
        if not looks_like_timestamp(hero) and not looks_like_timestamp(boss):
            if len(hero) >= 2 and len(boss) >= 2:
                return hero, boss, "Book of Heroes"

    return "", "", ""


def build_title(hero, boss, series):
    return f"{hero} vs {boss} \u2013 {series} | Hearthstone | Adventure Mode"


def build_description(hero, boss, series):
    return "\n\n".join([
        f"Hearthstone {series} gameplay from Adventure Mode.",
        f"Featuring the {hero} vs {boss} encounter.",
        "Part of the Hearthstone adventure playlist.",
    ])

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    youtube = get_youtube_service()

    print("Fetching playlist video IDs...")
    video_ids = get_playlist_video_ids(youtube, PLAYLIST_ID)
    print(f"  {len(video_ids)} videos total\n")

    print("Fetching live video details from YouTube API...")
    videos = get_video_details(youtube, video_ids)
    print(f"  {len(videos)} videos retrieved\n")

    # Find videos that still have timestamp titles
    timestamp_videos = [
        (vid_id, item)
        for vid_id, item in videos.items()
        if TIMESTAMP_TITLE_RE.search(item.get("snippet", {}).get("title", ""))
    ]
    # Preserve playlist order
    id_order = {vid: i for i, vid in enumerate(video_ids)}
    timestamp_videos.sort(key=lambda x: id_order.get(x[0], 9999))

    print(f"Videos with timestamp titles : {len(timestamp_videos)}")
    print(f"DRY_RUN                      : {DRY_RUN}\n")

    if not timestamp_videos:
        print("Nothing to do.")
        return

    updated  = 0
    skipped  = 0
    no_pair  = []

    for video_id, item in timestamp_videos:
        snippet    = item.get("snippet", {})
        status_obj = item.get("status", {})
        live_title = snippet.get("title", "").strip()
        live_desc  = snippet.get("description", "").strip()

        hero, boss, series = parse_pair_from_description(live_desc)

        if not hero or not boss:
            no_pair.append((video_id, live_title))
            skipped += 1
            continue

        new_title = build_title(hero, boss, series)
        new_desc  = build_description(hero, boss, series)

        if live_title == new_title:
            print(f"SKIP [already_correct] {video_id}")
            skipped += 1
            continue

        print(f"\nVIDEO     : https://youtu.be/{video_id}")
        print(f"OLD TITLE : {live_title}")
        print(f"NEW TITLE : {new_title}")
        print(f"HERO/BOSS : {hero} vs {boss}")

        if DRY_RUN:
            print("  [DRY RUN — not pushed]")
            continue

        body = {
            "id": video_id,
            "snippet": {
                "title":       new_title,
                "description": new_desc,
                "categoryId":  snippet.get("categoryId"),
                "tags":        snippet.get("tags", []),
            },
            "status": {
                "privacyStatus": status_obj.get("privacyStatus", "private"),
            },
        }
        if snippet.get("defaultLanguage"):
            body["snippet"]["defaultLanguage"] = snippet["defaultLanguage"]

        youtube.videos().update(part="snippet,status", body=body).execute()
        updated += 1
        time.sleep(SLEEP_SECONDS)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Done.  Updated={updated}  Skipped={skipped}  DryRun={DRY_RUN}")

    if no_pair:
        print(f"\n{len(no_pair)} videos could not be resolved (no Hero vs Boss in description):")
        for vid_id, title in no_pair:
            print(f"  https://youtu.be/{vid_id}")
            print(f"    {title[:80]}")
        print(
            "\nThese need their YouTube descriptions updated first to contain\n"
            "'Hero vs Boss \u2013 Book of Heroes | Hearthstone | Adventure Mode'\n"
            "on the first line, then re-run this script."
        )

    if DRY_RUN:
        print("\nSet DRY_RUN = False to push changes to YouTube.")


if __name__ == "__main__":
    main()
