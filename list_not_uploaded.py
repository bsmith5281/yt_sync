import csv
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token_readonly.json"
OUTPUT_FILE = "not_uploaded.csv"

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
    channel_resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_playlist_id = channel_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos = []
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in resp.get("items", []):
            snippet = item["snippet"]
            videos.append({
                "id": snippet["resourceId"]["videoId"],
                "title": snippet["title"],
            })

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
    if len(sys.argv) < 2:
        print('Usage: python list_not_uploaded.py "C:\\path\\to\\local\\folder"')
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

    uploaded_titles = {normalize_name(v["title"]) for v in uploads}

    not_uploaded = []
    for fname in os.listdir(local_folder):
        full_path = os.path.join(local_folder, fname)
        if not os.path.isfile(full_path):
            continue
        if normalize_name(fname) not in uploaded_titles:
            not_uploaded.append({"local_file": fname})

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["local_file"])
        writer.writeheader()
        writer.writerows(not_uploaded)

    print(f"Done. Wrote {len(not_uploaded)} rows to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
