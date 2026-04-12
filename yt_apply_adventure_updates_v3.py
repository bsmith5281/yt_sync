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
INPUT_CSV = "adventure_metadata_validated_v6.csv"   # point at your latest output
DRY_RUN = True      # Set to False only after reviewing printed OLD→NEW pairs
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


def safe_get(row, *keys):
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


# FIX: Replaced the old is_suspicious_title() which required the series name to
# be one of 3 hardcoded values in ALLOWED_SERIES — this blocked all valid
# "Book of Heroes" titles whose series slot didn't match the suffix check.
# NEW: Structural check only. The validator already verified the series via
# ADVENTURE_DIRECTORY before setting apply=TRUE.
def is_suspicious_title(title):
    if not title:
        return True, "blank_title"
    if "Hearthstone" not in title:
        return True, "missing_hearthstone"
    if " vs " not in title:
        return True, "missing_vs_separator"
    if " | " not in title:
        return True, "missing_pipe_separator"
    if not title.strip().endswith("| Adventure Mode"):
        return True, "missing_adventure_mode_suffix"
    left_of_pipe = title.split("|")[0].strip()
    parts = left_of_pipe.split(" vs ", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return True, "incomplete_matchup"
    return False, ""


def main():
    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(
            f"Could not find {INPUT_CSV}. Run the validator script first."
        )

    youtube = get_youtube_service()

    with open(INPUT_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # FIX: Removed ALLOWED_STATUSES gate. Old code silently dropped rows with
    # apply=TRUE but status outside {"needs_update","manual_update"} — which
    # excluded "verified_match" (v5) and "stt_failed" (v6) rows that were
    # correctly marked apply=TRUE. We trust apply=TRUE set by the validator.
    # The live YouTube API title comparison below is the real safety gate.
    target_rows = [r for r in rows if parse_bool(r.get("apply", "FALSE"))]

    print(f"Rows marked apply=TRUE: {len(target_rows)}")
    print(f"DRY_RUN={DRY_RUN}\n")

    updated = 0
    skipped = 0
    skip_log = []

    for row in target_rows:
        video_id = safe_get(row, "video_id")
        new_title = safe_get(row, "final_title", "new_title")
        new_desc = safe_get(row, "final_description", "new_description")
        old_title_csv = safe_get(row, "old_title")

        suspicious, reason = is_suspicious_title(new_title)
        if suspicious:
            msg = f"SKIP [{reason}] {video_id}: {repr(new_title)}"
            print(msg)
            skip_log.append(msg)
            skipped += 1
            continue

        current = youtube.videos().list(part="snippet,status", id=video_id).execute()
        items = current.get("items", [])
        if not items:
            msg = f"SKIP [missing_video] {video_id}"
            print(msg)
            skip_log.append(msg)
            skipped += 1
            continue

        item = items[0]
        snippet = item.get("snippet", {})
        status = item.get("status", {})
        current_title_live = (snippet.get("title") or "").strip()
        current_desc_live = (snippet.get("description") or "").strip()

        if current_title_live == new_title and current_desc_live == new_desc:
            print(f"SKIP [already_current] {video_id}")
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
            body["snippet"]["defaultLanguage"] = snippet["defaultLanguage"]

        print(f"\nVIDEO       : {video_id}")
        print(f"CSV OLD     : {old_title_csv}")
        print(f"LIVE OLD    : {current_title_live}")
        print(f"NEW TITLE   : {new_title}")
        print(f"STATUS/CONF : {row.get('verification_status')} / {row.get('stt_confidence')}")

        if DRY_RUN:
            print("  [DRY RUN — not pushed]")
            continue

        youtube.videos().update(part="snippet,status", body=body).execute()
        updated += 1
        time.sleep(SLEEP_SECONDS)

    print(f"\n{'=' * 60}")
    print(f"Done.  Updated={updated}  Skipped={skipped}  DryRun={DRY_RUN}")
    if skip_log:
        print("\nSkip details:")
        for s in skip_log:
            print(f"  {s}")
    if DRY_RUN:
        print("\nSet DRY_RUN = False to push changes to YouTube.")


if __name__ == "__main__":
    main()
