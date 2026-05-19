#!/usr/bin/env python3
"""
yt_uploader_v2.py — The Uploader & Browser Launcher  [V7.8]
Reads state.json, uploads ready_to_upload.mp4 as a private draft,
opens YouTube Studio for review, then cleans up and increments state.

Security: OAuth2 token file is chmod 600; all credentials stay local.

V7.8 changes:
  • Description attribution: auto-appends "Voice by elevenlabs.io"
    to every upload's description. The video title is NEVER modified.
"""

import os
import stat
import json
import webbrowser
import sys
import socket
import re
from datetime import datetime, timedelta, timezone

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


def calculate_next_upload_slot(state: dict) -> str:
    """
    Locks to exactly ONE upload per day at 8:30 PM IST (20:30 IST).

    Logic:
      • If last_scheduled_time exists AND is in the future → schedule exactly
        24 hours after that existing time (preserves the 20:30 anchor).
      • Otherwise (past or missing) → find the next upcoming 8:30 PM IST
        relative to now.

    Returns an ISO 8601 string and writes it back into state.
    """
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist_tz)

    last_scheduled_str = state.get("last_scheduled_time")

    if last_scheduled_str:
        try:
            last_scheduled = datetime.fromisoformat(last_scheduled_str)
            if last_scheduled > now:
                # Preserve the 20:30 anchor: add exactly 24 hours
                target = last_scheduled + timedelta(days=1)
                target = target.replace(hour=20, minute=30, second=0, microsecond=0)
                target_iso = target.isoformat()
                state["last_scheduled_time"] = target_iso
                return target_iso
        except ValueError:
            pass  # fall through to fresh calculation

    # No valid future slot — find the next upcoming 8:30 PM IST
    target = now.replace(hour=20, minute=30, second=0, microsecond=0)
    if target <= now:
        # 8:30 PM today has already passed; use 8:30 PM tomorrow
        target = target + timedelta(days=1)

    target_iso = target.isoformat()
    state["last_scheduled_time"] = target_iso
    return target_iso


# ─────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────
def upload_video(
    youtube,
    file_path: str,
    title: str,
    description: str,
    tags: list = None,
    schedule_timestamp: str = None,
) -> str:
    """
    Upload file_path as a private draft.
    Returns the video_id on success.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Upload target not found: {file_path}")

    if tags is None:
        tags = ["shorts", "cartoon", "viral"]

    print(f"\n⬆️   Uploading '{file_path}' to YouTube…")
    print(f"    Title: {title!r}")
    print(f"    Tags : {tags}")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": CATEGORY_FILM_ANIMATION,
            "tags": tags,
        },
        "status": {
            "privacyStatus": "private",      # always uploaded as private draft
            "selfDeclaredMadeForKids": False,
        },
    }

    if schedule_timestamp:
        body["status"]["publishAt"] = schedule_timestamp

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
    # Keep the processed chunk in /queue as requested unless queue is complete
    if os.path.exists(chunk_file):
        print(f"💾   Keeping chunk for review: {chunk_file}")
        # os.remove(chunk_file)

    # Delete the compiled split-screen output
    if os.path.exists(UPLOAD_FILE):
        print(f"🗑️   Deleting compiled output: {UPLOAD_FILE}")
        os.remove(UPLOAD_FILE)

    # Increment current_chunk or mark as complete
    current = state.get("current_chunk", 1)
    total = state.get("total_chunks", 1)

    if current >= total:
        print("\n🎉 QUEUE COMPLETE! All scheduled chunks have been successfully pushed to YouTube.")
        
        user_choice = input("❓ All chunks are uploaded! Do you want to perform a Smart Reset and delete local media assets? (y/n): ").strip().lower()
        if user_choice in ['y', 'yes']:
            # 1. Trigger Asset Cleanup
            import glob
            
            # Delete residual chunk video files in queue/
            for f in glob.glob(os.path.join(QUEUE_DIR, "*.mp4")):
                try:
                    os.remove(f)
                except OSError:
                    pass
                    
            # Delete cached hook audio files in queue/
            for f in glob.glob(os.path.join(QUEUE_DIR, "*_hook.aac")):
                try:
                    os.remove(f)
                except OSError:
                    pass
                    
            # Delete lingering temp_* files and ready_to_upload.mp4 in root
            for f in glob.glob("temp_*"):
                try:
                    os.remove(f)
                except OSError:
                    pass
                    
            if os.path.exists(UPLOAD_FILE):
                try:
                    os.remove(UPLOAD_FILE)
                except OSError:
                    pass

            # 2. Smart State Wipe (Preserve last_scheduled_time)
            keys_to_wipe = ['original_title', 'total_chunks', 'current_chunk', 'chunk_metadata']
            for key in keys_to_wipe:
                if key in state:
                    del state[key]
                    
            print("🧹 Queue complete: Video assets cleared and clip metadata reset. Master schedule tracking preserved.")
        else:
            print("💾 Assets and metadata preserved. You can safely review your drafts in YouTube Studio or rerun chunks if needed.")

        next_chunk = "COMPLETED"
        total_str = str(total)
    else:
        state["current_chunk"] = current + 1
        next_chunk = state["current_chunk"]
        total_str  = str(total)

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

    if current < total:
        print(f"📝  state.json updated → current_chunk={next_chunk}/{total_str}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    # 1. Load state
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError(
            f"{STATE_FILE} not found. Run brainrot_fetcher.py first."
        )
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\n❌  Could not read {STATE_FILE}: {exc}", file=sys.stderr)
        sys.exit(1)

    original_title = state.get("original_title", "Unknown Cartoon")
    current_chunk  = state.get("current_chunk", 1)
    total_chunks   = state.get("total_chunks", 5)

    print(f"\n📤  yt_uploader_v2 — Chunk {current_chunk}/{total_chunks}")
    print(f"    Source title: {original_title!r}")

    # 2. Compose title and description — clean organic template (no PART/brainrot tokens)
    chunk_str = str(current_chunk)
    metadata = state.get("chunk_metadata", {}).get(chunk_str, {})
    
    ai_title = metadata.get("title", "").strip()
    ai_desc  = metadata.get("description", "").strip()
    tags     = metadata.get("tags", ["shorts", "cartoon", "viral"])
    if not isinstance(tags, list) or not tags:
        tags = ["shorts", "cartoon", "viral"]

    # Title must remain pristine — NO #shorts in the title
    if ai_title:
        video_title = re.sub(r'#\w+', '', ai_title).strip()
    else:
        video_title = f"{original_title} 💀🗣️"

    if ai_desc:
        video_description = ai_desc
    else:
        video_description = (
            f"The most insane moments from {original_title}!\n\n"
            "Like & subscribe for more daily clips! 🎬"
        )

    # ── Attribution enforcement (V7.8) ─────────────────────────────────────
    # ABSOLUTE CONSTRAINT: the title string must remain clean and untouched.
    # Attribution and #shorts live exclusively inside the description field.
    ELEVENLABS_ATTRIBUTION = "\n\nVoice by elevenlabs.io"
    video_description = video_description + ELEVENLABS_ATTRIBUTION + "\n\n#shorts"

    # 3. Calculate schedule timestamp
    schedule_timestamp = calculate_next_upload_slot(state)
    print(f"📅 Scheduled upload slot allocated: {schedule_timestamp.replace('T', ' ')}")

    # 4. Authenticate (reuse existing OAuth flow)
    try:
        youtube = get_authenticated_service()

        # 5. Upload
        video_id = upload_video(youtube, UPLOAD_FILE, video_title, video_description, tags, schedule_timestamp)
    except (ConnectionError, socket.gaierror, socket.timeout, Exception) as e:
        err_str = str(e).lower()
        if isinstance(e, (ConnectionError, socket.gaierror, socket.timeout)) or "network" in err_str or "connection" in err_str or "timeout" in err_str or "resolve" in err_str:
            print("\n🌐 Network Error: Internet connection lost. Upload paused.")
            sys.exit(1)
        raise

    # 5. Open YouTube Studio in browser for review
    studio_url = f"https://studio.youtube.com/video/{video_id}/edit"
    print(f"\n🌐  Opening YouTube Studio: {studio_url}")
    webbrowser.open(studio_url)

    if os.environ.get("AUTO_RUN") != "1":
        qc_choice = input("❓ Was the upload perfect? (y/n): ").strip().lower()
        if qc_choice not in ['y', 'yes']:
            print("Rollback initiated. State preserved for re-run.")
            sys.exit(0)

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
