#!/usr/bin/env python3
"""
yt_uploader_v2.py — The Uploader & Browser Launcher
Reads state.json, uploads ready_to_upload.mp4 as a private draft,
opens YouTube Studio for review, then cleans up and increments state.

Security: OAuth2 token file is chmod 600; all credentials stay local.
"""

import os
import stat
import json
import webbrowser
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE          = "token.json"
STATE_FILE          = "state.json"
UPLOAD_FILE         = "ready_to_upload.mp4"
QUEUE_DIR           = "queue"

SCOPES           = ["https://www.googleapis.com/auth/youtube.upload"]
API_SERVICE_NAME = "youtube"
API_VERSION      = "v3"

CATEGORY_FILM_ANIMATION = "1"   # YouTube category: Film & Animation


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────
def get_authenticated_service():
    """
    Reuses the existing OAuth2 flow with automatic token refresh.
    Enforces strict file permissions on token.json to prevent
    unauthorized access (global security rule).
    """
    credentials = None

    if os.path.exists(TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                raise FileNotFoundError(
                    f"Missing {CLIENT_SECRETS_FILE}. "
                    "Download it from Google Cloud Console → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES
            )
            credentials = flow.run_local_server(port=0)

        # Persist refreshed/new credentials
        with open(TOKEN_FILE, "w") as tf:
            tf.write(credentials.to_json())

        # Security: 600 permissions — owner read/write only
        os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)

    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)


# ─────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────
def upload_video(youtube, file_path: str, title: str, description: str) -> str:
    """
    Upload file_path as a private draft.
    Returns the video_id on success.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Upload target not found: {file_path}")

    print(f"\n⬆️   Uploading '{file_path}' to YouTube…")
    print(f"    Title: {title!r}")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": CATEGORY_FILM_ANIMATION,
        },
        "status": {
            "privacyStatus": "private",      # always uploaded as private draft
            "selfDeclaredMadeForKids": False,
        },
    }

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(file_path, chunksize=-1, resumable=True),
    )

    response = insert_request.execute()
    video_id = response["id"]

    print(f"\n✅  Upload successful!")
    print(f"    Video ID : {video_id}")
    print(f"    URL      : https://youtu.be/{video_id}")
    return video_id


# ─────────────────────────────────────────────
# Cleanup & State
# ─────────────────────────────────────────────
def cleanup_and_advance(state: dict, chunk_file: str):
    """Delete chunk and compiled video, then increment current_chunk in state.json."""
    # Delete the processed chunk from /queue
    if os.path.exists(chunk_file):
        print(f"🗑️   Deleting chunk: {chunk_file}")
        os.remove(chunk_file)

    # Delete the compiled split-screen output
    if os.path.exists(UPLOAD_FILE):
        print(f"🗑️   Deleting: {UPLOAD_FILE}")
        os.remove(UPLOAD_FILE)

    # Increment current_chunk
    state["current_chunk"] = state.get("current_chunk", 1) + 1
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

    next_chunk = state["current_chunk"]
    total      = state.get("total_chunks", "?")
    print(f"📝  state.json updated → current_chunk={next_chunk}/{total}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    # 1. Load state
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError(
            f"{STATE_FILE} not found. Run brainrot_fetcher.py first."
        )
    with open(STATE_FILE) as f:
        state = json.load(f)

    original_title = state.get("original_title", "Unknown Cartoon")
    current_chunk  = state.get("current_chunk", 1)
    total_chunks   = state.get("total_chunks", 5)

    print(f"\n📤  yt_uploader_v2 — Chunk {current_chunk}/{total_chunks}")
    print(f"    Source title: {original_title!r}")

    # 2. Compose title and description per spec
    video_title = (
        f"PART {current_chunk} - {original_title} 💀🗣️ #shorts #brainrot"
    )
    video_description = (
        f"Part {current_chunk} of {total_chunks}: {original_title}\n\n"
        "Classic public-domain animation — no copyright issues.\n"
        "Like & subscribe for daily brainrot! 💀"
    )

    # 3. Authenticate (reuse existing OAuth flow)
    youtube = get_authenticated_service()

    # 4. Upload
    video_id = upload_video(youtube, UPLOAD_FILE, video_title, video_description)

    # 5. Open YouTube Studio in browser for review
    studio_url = f"https://studio.youtube.com/video/{video_id}/edit"
    print(f"\n🌐  Opening YouTube Studio: {studio_url}")
    webbrowser.open(studio_url)

    # 6. Cleanup + advance counter
    chunk_file = os.path.join(QUEUE_DIR, f"chunk_{current_chunk}.mp4")
    cleanup_and_advance(state, chunk_file)

    print("\n✅  yt_uploader_v2 complete.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\n❌  Upload failed: {exc}", file=sys.stderr)
        sys.exit(1)
