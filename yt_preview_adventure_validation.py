import csv
import os
import re
from collections import Counter
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
PLAYLIST_ID = "PL2wUlQkvGyYfSMMzX1Ak2WR6wNbfny45P"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
OUTPUT_CSV = "adventure_metadata_preview.csv"

DEFAULT_TITLE_RE = re.compile(r"^Hearthstone\s+Heroes of Warcraft\s+(?P<stamp>\d{4}\s\d{2}\s\d{2}T\d{2}\s\d{2}\s\d{2})\s*$", re.IGNORECASE)
TIMESTAMP_IN_TITLE_RE = re.compile(r"\d{4}\s\d{2}\s\d{2}T\d{2}\s\d{2}\s\d{2}")
MATCHUP_RE = re.compile(r"^(?P<hero>.+?)\s+vs\s+(?P<boss>.+?)\s+–\s+(?P<series>Book of Heroes|Book of Mercenaries|Adventure Mode)\s+\|\s+Hearthstone\s+\|\s+Adventure Mode$", re.IGNORECASE)


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


def fetch_playlist_items(youtube, playlist_id):
    items = []
    next_page_token = None
    position = 0
    while True:
        resp = youtube.playlistItems().list(
            part="snippet,contentDetails,status",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page_token,
        ).execute()
        for item in resp.get("items", []):
            snippet = item.get("snippet", {})
            resource_id = snippet.get("resourceId", {})
            video_id = resource_id.get("videoId") or item.get("contentDetails", {}).get("videoId")
            if not video_id:
                continue
            items.append(
                {
                    "playlist_position": position,
                    "video_id": video_id,
                    "published_at": snippet.get("publishedAt", ""),
                    "title": snippet.get("title", "").strip(),
                    "description": snippet.get("description", "").strip(),
                    "privacy_status": item.get("status", {}).get("privacyStatus", ""),
                }
            )
            position += 1
        next_page_token = resp.get("nextPageToken")
        if not next_page_token:
            break
    return items


def normalize_default_title(title):
    m = DEFAULT_TITLE_RE.match(" ".join(title.split()))
    if not m:
        return None
    raw = m.group("stamp")
    try:
        dt = datetime.strptime(raw, "%Y %m %dT%H %M %S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return raw


def title_family(title):
    compact = " ".join(title.split())
    m = MATCHUP_RE.match(compact)
    if DEFAULT_TITLE_RE.match(compact):
        return "default_timestamp"
    if m:
        series = m.group("series").lower()
        return f"formatted_{series.replace(' ', '_')}"
    if TIMESTAMP_IN_TITLE_RE.search(compact):
        return "contains_timestamp"
    return "other"


def parse_matchup(title):
    m = MATCHUP_RE.match(" ".join(title.split()))
    if not m:
        return "", "", ""
    return m.group("hero").strip(), m.group("boss").strip(), m.group("series").strip()


def main():
    youtube = get_youtube_service()
    items = fetch_playlist_items(youtube, PLAYLIST_ID)
    rows = []
    counts = Counter()
    for item in items:
        family = title_family(item["title"])
        counts[family] += 1
        hero, boss, series = parse_matchup(item["title"])
        rows.append(
            {
                "playlist_position": item["playlist_position"],
                "video_id": item["video_id"],
                "published_at": item["published_at"],
                "privacy_status": item["privacy_status"],
                "title_family": family,
                "is_target_default": "TRUE" if family == "default_timestamp" else "FALSE",
                "normalized_timestamp": normalize_default_title(item["title"]) if family == "default_timestamp" else "",
                "old_title": item["title"],
                "old_description": item["description"],
                "current_hero": hero,
                "current_boss": boss,
                "current_series": series,
                "mode": "",
                "hero_name": "",
                "boss_name": "",
                "series_name": "",
                "verification_status": "pending",
                "verification_reason": "",
                "stt_confidence": "",
                "stt_raw": "",
                "final_title": "",
                "final_description": "",
                "needs_review": "FALSE",
                "apply": "FALSE",
                "review_notes": "",
            }
        )
    fieldnames = list(rows[0].keys()) if rows else []
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Fetched playlist rows: {len(rows)}")
    for k, v in sorted(counts.items()):
        print(f"{k}: {v}")
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
