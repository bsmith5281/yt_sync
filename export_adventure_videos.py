"""
export_adventure_videos.py

Dump a simple CSV of your adventure videos directly from YouTube:
video_id, old_title, old_description

This is the input for yt_fix_timestamp_titles.py.
"""

import csv
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES           = ["https://www.googleapis.com/auth/youtube.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token_readonly.json"  # separate from your force-ssl token if you want
OUTPUT_CSV       = "adventure_timestamp_titles.csv"

# Either a playlist ID, or leave None and use channel uploads
PLAYLIST_ID      = "PL2wUlQkvGyYfSMMzX1Ak2WR6wNbfny45P"   # e.g. Adventure Mode playlist
CHANNEL_ID       = "UCC-kKLy-__XXEjtDiHhDLyg"   # or set this and let it use uploads playlist


def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def get_uploads_playlist_id(youtube, channel_id):
    resp = youtube.channels().list(
        part="contentDetails",
        id=channel_id,
        maxResults=1,
    ).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("Channel not found or no contentDetails.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def iter_playlist_items(youtube, playlist_id):
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        for item in resp.get("items", []):
            yield item["contentDetails"]["videoId"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def fetch_video_snippets(youtube, video_ids):
    """
    Yield (video_id, title, description) for a list of IDs.
    Handles batching into <=50 per API call.
    """
    video_ids = list(video_ids)
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = youtube.videos().list(
            part="snippet",
            id=",".join(batch),
            maxResults=50,
        ).execute()
        for item in resp.get("items", []):
            vid = item["id"]
            snip = item.get("snippet", {})
            title = (snip.get("title") or "").replace("\r\n", "\n")
            desc = (snip.get("description") or "").replace("\r\n", "\n")
            yield vid, title, desc


def main():
    youtube = get_service()

    if PLAYLIST_ID:
        playlist_id = PLAYLIST_ID
    else:
        if not CHANNEL_ID:
            raise RuntimeError(
                "Set PLAYLIST_ID or CHANNEL_ID at the top of this script."
            )
        playlist_id = get_uploads_playlist_id(youtube, CHANNEL_ID)

    print(f"Using playlist: {playlist_id}")

    video_ids = list(iter_playlist_items(youtube, playlist_id))
    print(f"Found {len(video_ids)} videos in playlist")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["video_id", "old_title", "old_description"])
        for vid, title, desc in fetch_video_snippets(youtube, video_ids):
            writer.writerow([vid, title, desc])

    print(f"Wrote {len(video_ids)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()