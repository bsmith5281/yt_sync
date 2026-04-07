import csv
import os
import re
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
print("DEBUG: preview script started")

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
PLAYLIST_ID = "PL2wUlQkvGyYfSMMzX1Ak2WR6wNbfny45P"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
OUTPUT_CSV = "adventure_metadata_preview.csv"

OLD_TITLE_RE = re.compile(r"^Hearthstone \[Heroes of Warcraft\] Book of Heroes - (?P<pair>.+?)\s*$")
OLD_DESC_RE = re.compile(
    r"^Hearthstone Book of Heroes - (?P<pair>.+?)\s*\n(?P<timestamp>\[[^\n]+\])\s*$",
    re.DOTALL,
)
TIMESTAMP_ONLY_RE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s*$")
NEW_TITLE_HINT_RE = re.compile(r"Book of Heroes\s*\|\s*Hearthstone\s*\|\s*Adventure Mode", re.IGNORECASE)


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


def get_all_playlist_video_ids(youtube, playlist_id):
    video_ids = []
    page_token = None
    while True:
        response = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        for item in response.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                video_ids.append(vid)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def get_video_details(youtube, video_ids):
    rows = []
    for chunk in chunked(video_ids, 50):
        response = youtube.videos().list(
            part="snippet,status",
            id=",".join(chunk),
            maxResults=50,
        ).execute()
        rows.extend(response.get("items", []))
    return rows


def build_new_title(old_title):
    m = OLD_TITLE_RE.match(old_title.strip())
    if m:
        pair = m.group("pair").strip()
        return f"{pair} – Book of Heroes | Hearthstone | Adventure Mode", "converted_old_pattern", pair
    if NEW_TITLE_HINT_RE.search(old_title):
        return old_title.strip(), "already_new_format", None
    return old_title.strip(), "unmatched_title_review", None


def extract_pair_from_title(title):
    m = OLD_TITLE_RE.match(title.strip())
    if m:
        return m.group("pair").strip()
    marker = "– Book of Heroes | Hearthstone | Adventure Mode"
    if marker in title:
        return title.split(marker, 1)[0].strip()
    return title.strip()


def build_new_description(old_title, old_desc, resolved_pair=None):
    old_desc = (old_desc or "").strip()
    pair = resolved_pair or extract_pair_from_title(old_title)

    m = OLD_DESC_RE.match(old_desc)
    if m:
        pair = m.group("pair").strip()
        timestamp = m.group("timestamp").strip()
        new_desc = (
            f"{pair} – Book of Heroes | Hearthstone | Adventure Mode [Heroes of Warcraft]\n"
            f"Solo PvE gameplay from Hearthstone's Book of Heroes adventure, featuring the {pair} boss fight\n"
            f"{timestamp}"
        )
        return new_desc, "converted_old_desc"

    if "Solo PvE gameplay from Hearthstone's Book of Heroes adventure" in old_desc and "Book of Heroes | Hearthstone | Adventure Mode" in old_desc:
        return old_desc, "already_new_format"

    ts = ""
    lines = [ln.rstrip() for ln in old_desc.splitlines() if ln.strip()]
    if lines:
        last = lines[-1]
        if TIMESTAMP_ONLY_RE.match(last):
            ts = last

    new_desc = (
        f"{pair} – Book of Heroes | Hearthstone | Adventure Mode [Heroes of Warcraft]\n"
        f"Solo PvE gameplay from Hearthstone's Book of Heroes adventure, featuring the {pair} boss fight"
    )
    if ts:
        new_desc += f"\n{ts}"
    return new_desc, "generated_from_title_review"


def main():
    youtube = get_youtube_service()
    video_ids = get_all_playlist_video_ids(youtube, PLAYLIST_ID)
    videos = get_video_details(youtube, video_ids)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "apply",
                "video_id",
                "privacy_status",
                "old_title",
                "new_title",
                "title_status",
                "old_description",
                "new_description",
                "description_status",
            ],
        )
        writer.writeheader()

        for item in videos:
            snippet = item.get("snippet", {})
            status = item.get("status", {})
            old_title = (snippet.get("title") or "").strip()
            old_desc = (snippet.get("description") or "").strip()
            new_title, title_status, pair = build_new_title(old_title)
            new_desc, desc_status = build_new_description(old_title, old_desc, resolved_pair=pair)
            writer.writerow(
                {
                    "apply": "TRUE" if (new_title != old_title or new_desc != old_desc) else "FALSE",
                    "video_id": item.get("id", ""),
                    "privacy_status": status.get("privacyStatus", ""),
                    "old_title": old_title,
                    "new_title": new_title,
                    "title_status": title_status,
                    "old_description": old_desc,
                    "new_description": new_desc,
                    "description_status": desc_status,
                }
            )

    print(f"Wrote preview CSV to {OUTPUT_CSV} with {len(videos)} rows.")
    print("Review the CSV, change apply to TRUE/FALSE as needed, then use the apply script.")


if __name__ == "__main__":
    main()    