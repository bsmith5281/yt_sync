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
INPUT_CSV = "adventure_metadata_validated.csv"
DRY_RUN = False
SLEEP_SECONDS = 0.2
ALLOWED_STATUSES = {"needs_update", "manual_update"}
ALLOWED_SERIES = {"Book of Heroes", "Book of Mercenaries", "Adventure Mode"}


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


def safe_get(row, *keys):
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def is_suspicious_title(title):
    if not title:
        return True, "blank_title"
    if "Hearthstone" not in title:
        return True, "missing_hearthstone"
    if " | Adventure Mode" not in title:
        return True, "missing_adventure_suffix"
    if " vs " not in title:
        return True, "missing_matchup_separator"
    if not any(series in title for series in ALLOWED_SERIES):
        return True, "missing_known_series"
    left_side = title.split("|", 1)[0].strip()
    if " vs  " in left_side or left_side.startswith("vs ") or left_side.endswith(" vs"):
        return True, "incomplete_matchup"
    return False, ""


def main():
    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"Could not find {INPUT_CSV}. Run the validator script first.")

    youtube = get_youtube_service()

    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    target_rows = []
    for r in rows:
        if not parse_bool(r.get("apply", "FALSE")):
            continue
        status = (r.get("verification_status") or "").strip()
        if status not in ALLOWED_STATUSES:
            continue
        target_rows.append(r)

    print(f"Rows marked apply=TRUE with allowed status: {len(target_rows)}")
    print(f"DRY_RUN={DRY_RUN}")

    updated = 0
    skipped = 0

    for row in target_rows:
        video_id = safe_get(row, "video_id")
        new_title = safe_get(row, "final_title", "new_title")
        new_desc = safe_get(row, "final_description", "new_description")
        old_title_csv = safe_get(row, "old_title")

        suspicious, reason = is_suspicious_title(new_title)
        if suspicious:
            print(f"SKIP suspicious title: {video_id} [{reason}] -> {new_title}")
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
        current_title_live = (snippet.get("title") or "").strip()
        current_desc_live = (snippet.get("description") or "").strip()

        if current_title_live == new_title and current_desc_live == new_desc:
            print(f"SKIP already matches live metadata: {video_id}")
            skipped += 1
            continue

        body = {
            "id": video_id,
            "snippet": {
                "categoryId": snippet.get("categoryId"),
                "title": new_title,
                "description": new_desc,
                "tags": snippet.get("tags", []),
            },
            "status": {
                "privacyStatus": status.get("privacyStatus", "private")
            },
        }

        if snippet.get("defaultLanguage"):
            body["snippet"]["defaultLanguage"] = snippet.get("defaultLanguage")

        print(f"\nVIDEO {video_id}")
        print(f"CSV OLD TITLE : {old_title_csv}")
        print(f"LIVE OLD TITLE: {current_title_live}")
        print(f"NEW TITLE     : {new_title}")

        if DRY_RUN:
            continue

        youtube.videos().update(
            part="snippet,status",
            body=body,
        ).execute()
        updated += 1
        time.sleep(SLEEP_SECONDS)

    print(f"\nDone. Updated={updated}, Skipped={skipped}, DryRun={DRY_RUN}")
    print("Leave DRY_RUN = True until the printed OLD/NEW pairs look correct.")


if __name__ == "__main__":
    main()
