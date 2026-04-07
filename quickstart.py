from __future__ import print_function

import os.path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Full read/write scope so later you can update descriptions
SCOPES = ["https://www.googleapis.com/auth/youtube"]

def main():
    creds = None
    # 1) token.json is what stores the *credentials*, not your client secret
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # 2) If no valid creds, run OAuth flow using your client secret JSON
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "yt-tools-client-secret.json",  # <-- your downloaded OAuth client file
                SCOPES
            )
            creds = flow.run_local_server(port=0)

        # 3) Save the credentials to token.json for next time
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    youtube = build("youtube", "v3", credentials=creds)

    # Example: fetch your own channel’s snippet to confirm it works
    response = youtube.channels().list(
        part="snippet,contentDetails",
        mine=True
    ).execute()

    print(response)

if __name__ == "__main__":
    main()