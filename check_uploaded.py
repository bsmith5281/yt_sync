from __future__ import print_function
import os
import os.path
import csv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token_readonly.json"

def get_creds():
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
    return creds

def get_all_uploads(youtube):
    channel_resp = youtube.channels().list(
        part="contentDetails",
        mine=True
    ).execute()

    uploads_playlist_id = (
        channel_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    )

    videos = []
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=page_token
        ).execute()

        for item in resp.get("items", []):
            snippet = item["snippet"]
            video_id = snippet["resourceId"]["videoId"]
            title = snippet["title"]
            videos.append({"id": video_id, "title": title})

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return videos

def normalize_name(name):
    base = os.path.splitext(name)[0]
    base = base.lower()
    for ch in [" ", "-", "_", ":", ".", ",", "!", "?", "(", ")", "[", "]"]:
        base = base.replace(ch, "")
    return base

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python check_uploaded.py \"C:\\path\\to\\local\\folder\"")
        sys.exit(1)

    local_folder = sys.argv[1]
    if not os.path.isdir(local_folder):
        print("Folder does not exist:", local_folder)
        sys.exit(1)

    creds = get_creds()
    youtube = build("youtube", "v3", credentials=creds)

    print("Fetching uploads from YouTube...")
    uploads = get_all_uploads(youtube)
    print("Found", len(uploads), "uploaded videos.")

    title_index = {}
    for v in uploads:
        norm = normalize_name(v["title"])
        title_index.setdefault(norm, []).append(v)

    print("Scanning local folder:", local_folder)
    results = []
    for fname in os.listdir(local_folder):
        full_path = os.path.join(local_folder, fname)
        if not os.path.isfile(full_path):
            continue

        norm_local = normalize_name(fname)
        matched = title_index.get(norm_local, [])
        if matched:
            uploaded = "Y"
            vid = matched[0]
            vid_id = vid["id"]
            vid_title = vid["title"]
        else:
            uploaded = "N"
            vid_id = ""
            vid_title = ""

        results.append({
            "local_file": fname,
            "uploaded": uploaded,
            "matched_video_id": vid_id,
            "matched_title": vid_title,
        })

    out_file = "compare_output.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["local_file", "uploaded", "matched_video_id", "matched_title"],
        )
        writer.writeheader()
        writer.writerows(results)

    print("Done. Wrote results to", out_file)

if __name__ == "__main__":
    main()