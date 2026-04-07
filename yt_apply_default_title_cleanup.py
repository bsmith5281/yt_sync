import csv
import os
import time
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
INPUT_CSV = "default_title_cleanup_with_stt.csv"
DRY_RUN = False
SLEEP_SECONDS = 0.2

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

def parse_bool(value):
    return str(value).strip().lower() in {"true", "1", "yes", "y"}

def main():
    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"Could not find {INPUT_CSV}. Run the preview script first.")

    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    target_rows = [r for r in rows if parse_bool(r.get("apply", "FALSE"))]
    if not target_rows:
        print("No rows marked apply=TRUE. Review the preview CSV first.")
        return

    youtube = get_youtube_service()
    print(f"Rows marked apply=TRUE: {len(target_rows)}")
    print(f"DRY_RUN={DRY_RUN}")

    updated = 0
    skipped = 0

    for row in target_rows:
        video_id = row["video_id"].strip()
        old_title_from_csv = row.get("old_title", "").strip()
        new_title = row.get("suggested_title", "").strip()
        new_desc = row.get("suggested_description", "").strip()
        confidence = row.get("confidence", "").strip().lower()

        if not new_title or not new_desc:
            print(f"SKIP missing new metadata: {video_id}")
            skipped += 1
            continue
        if confidence not in {"high", "manual", "low"}:
            print(f"SKIP invalid confidence value: {video_id}")
            skipped += 1
            continue

        current = youtube.videos().list(part="snippet,status", id=video_id).execute()
        items = current.get("items", [])
        if not items:
            print(f"SKIP missing video: {video_id}")
            skipped += 1
            continue

        item = items[0]
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        live_title = snippet.get("title", "").strip()

        body = {
            "id": video_id,
            "snippet": {
                "categoryId": snippet.get("categoryId"),
                "title": new_title,
                "description": new_desc,
                "tags": snippet.get("tags", []),
                "defaultLanguage": snippet.get("defaultLanguage"),
            },
            "status": {
                "privacyStatus": status.get("privacyStatus", "private")
            },
        }

        print(f"\nVIDEO {video_id}")
        print(f"CSV OLD TITLE:  {old_title_from_csv}")
        print(f"LIVE OLD TITLE: {live_title}")
        print(f"NEW TITLE:      {new_title}")
        print(f"CONFIDENCE:     {confidence}")

        if DRY_RUN:
            continue

        youtube.videos().update(part="snippet,status", body=body).execute()
        updated += 1
        time.sleep(SLEEP_SECONDS)

    print(f"\nDone. Updated={updated}, Skipped={skipped}, DryRun={DRY_RUN}")
    print("Set DRY_RUN = False only after reviewing default_title_cleanup_preview.csv carefully.")

if __name__ == "__main__":
    main()