from __future__ import print_function
import os
import os.path
import shutil

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
TOKEN_FILE = "token_readonly"
CLIENT_SECRET_FILE = "credentials.json"

ARCHIVE_ROOT = r"F:\Youtube_Archive"

def get_creds():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
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
            s = item["snippet"]
            videos.append({"id": s["resourceId"]["videoId"], "title": s["title"]})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return videos

def normalize_name(name):
    base = os.path.splitext(name)[0].lower()
    for ch in [" ", "-", "_", ":", ".", ",", "!", "?", "(", ")", "[", "]"]:
        base = base.replace(ch, "")
    return base

def main():
    import sys
    if len(sys.argv) < 2:
        print('Usage: python move_uploaded_to_archive.py "C:\\path\\to\\local\\folder"')
        sys.exit(1)

    local_folder = sys.argv[1]
    if not os.path.isdir(local_folder):
        print("Folder does not exist:", local_folder)
        sys.exit(1)

    if not os.path.isdir(ARCHIVE_ROOT):
        print("Archive folder does not exist, creating:", ARCHIVE_ROOT)
        os.makedirs(ARCHIVE_ROOT, exist_ok=True)

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
    moved = 0
    skipped = 0

    for fname in os.listdir(local_folder):
        src = os.path.join(local_folder, fname)
        if not os.path.isfile(src):
            continue

        norm_local = normalize_name(fname)
        matched = title_index.get(norm_local, [])
        if not matched:
            skipped += 1
            continue

        # Optional: preserve subfolder by game
        # here we use the prefix before the first ' - ' as a game folder
        base = os.path.splitext(fname)[0]
        game_prefix = base.split(" - ")[0].strip()
        dest_dir = os.path.join(ARCHIVE_ROOT, game_prefix)
        os.makedirs(dest_dir, exist_ok=True)

        dest = os.path.join(dest_dir, fname)
        print(f"Moving: {src} -> {dest}")
        shutil.move(src, dest)
        moved += 1

    print("Done. Moved", moved, "uploaded files to archive.")
    print("Skipped", skipped, "files (not found on YouTube).")

if __name__ == "__main__":
    main()