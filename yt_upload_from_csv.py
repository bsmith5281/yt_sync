import csv
import os
import time
import glob

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token_upload.json"
BASE_FOLDER = r"C:\Users\Owner\Videos\Insights Capture"
DRY_RUN = False
SLEEP_SECONDS = 1.0

def find_latest_not_uploaded_csv():
    pattern = "not_uploaded*.csv"
    candidates = glob.glob(pattern)
    if not candidates:
        raise FileNotFoundError("No not_uploaded*.csv files found.")
    return max(candidates, key=os.path.getmtime)

INPUT_CSV = find_latest_not_uploaded_csv()
print(f"Using CSV: {INPUT_CSV}")

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

def main():
    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"Could not find {INPUT_CSV}")

    youtube = get_youtube_service()

    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print(f"Rows to upload: {len(rows)}")
    uploaded = 0
    skipped = 0

    for row in rows:
        fname = row.get("local_file", "").strip()

        if not fname or fname.lower() == "cd":
            print("SKIP non-file row:", fname)
            skipped += 1
            continue

        full_path = os.path.join(BASE_FOLDER, fname)
        if not os.path.exists(full_path):
            print(f"SKIP missing file: {full_path}")
            skipped += 1
            continue

        title = os.path.splitext(os.path.basename(fname))[0]
        description = "Raw gameplay archive."

        print("\n----------------------------")
        print(f"FILE: {full_path}")
        print(f"TITLE: {title}")

        if DRY_RUN:
            print("DRY RUN - not uploading.")
            uploaded += 1
            continue

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "20"
            },
            "status": {
                "privacyStatus": "unlisted"
            }
        }

        media = MediaFileUpload(full_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = request.execute()
        video_id = response.get("id")
        print(f"UPLOADED videoId={video_id}")
        uploaded += 1
        time.sleep(SLEEP_SECONDS)

    print(f"\nDone. Uploaded={uploaded}, Skipped={skipped}, DryRun={DRY_RUN}")

if __name__ == "__main__":
    main()
