#!/usr/bin/env python3
"""
main.py — The Brainrot Pipeline Orchestrator  ([V7.8] ElevenLabs + History Engine)
Processes exactly ONE chunk per execution:

  1. Security check (API_AUTH_TOKEN & GEMINI_API_KEY & ELEVENLABS_API_KEY env vars)
  2. If /queue is empty → prompt for YouTube URL, run duplicate-history check,
     then run ai_director.py, and append the processed video to history.
  3. Run smart_editor.py to compose the 75/25 split-screen video
  4. Run yt_uploader_v2.py to upload, open Studio, and advance state

The script exits after opening the browser so the user can review
the upload before the next cycle starts.
"""

import os
import sys
import json
import glob
import subprocess
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
STATE_FILE    = "state.json"
QUEUE_DIR     = "queue"
HISTORY_FILE  = "processed_history.json"   # V7.8 — duplicate checkpoint DB

SCRIPTS = {
    "fetcher":  "ai_director.py",
    "editor":   "smart_editor.py",
    "uploader": "yt_uploader_v2.py",
}


# ─────────────────────────────────────────────
# Security gate
# ─────────────────────────────────────────────
def verify_auth_token():
    """
    Enforce strict user verification before running the pipeline.
    Prevents unauthorised access / scraping per global security rules.
    Set:  export API_AUTH_TOKEN=admin_authorized
    """
    token = os.environ.get("API_AUTH_TOKEN")
    if token != "admin_authorized":
        print(
            "\n🔒  Security Error: API_AUTH_TOKEN is invalid or not set.\n"
            "    Run:  export API_AUTH_TOKEN=admin_authorized\n"
            "    Unauthorized execution is prohibited.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.environ.get("GEMINI_API_KEY"):
        print(
            "\n🤖  Security Error: GEMINI_API_KEY is not set.\n"
            "    This pipeline requires Gemini AI for the AI Director.\n"
            "    Run:  export GEMINI_API_KEY='your_key' or add it to .env",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.environ.get("ELEVENLABS_API_KEY"):
        print(
            "\n🔊  Security Error: ELEVENLABS_API_KEY is not set.\n"
            "    This pipeline requires ElevenLabs for voice synthesis (V7.8).\n"
            "    Run:  export ELEVENLABS_API_KEY='your_key' or add it to .env",
            file=sys.stderr,
        )
        sys.exit(1)

    print("🔒  Security checks passed (Auth Token, Gemini Key & ElevenLabs Key).")


# ─────────────────────────────────────────────
# Duplicate History Checkpoint Engine  (V7.8)
# ─────────────────────────────────────────────
def load_history() -> dict:
    """
    Load processed_history.json.  Always returns a safe dict:
      {"records": [{"url": ..., "title": ..., "completed_at": ...}, ...]}
    Creates the file if it does not yet exist.
    """
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict) and "records" in data:
                    return data
        except (json.JSONDecodeError, OSError):
            print(f"    ⚠️   {HISTORY_FILE} is corrupt — starting fresh history.",
                  file=sys.stderr)
    return {"records": []}


def save_history(history: dict) -> None:
    """
    Atomically write history to HISTORY_FILE with strict 600 permissions
    to prevent unauthorised scraping of processed URL logs.
    """
    import stat
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=4)
    os.replace(tmp, HISTORY_FILE)
    try:
        import stat as _stat
        os.chmod(HISTORY_FILE, _stat.S_IRUSR | _stat.S_IWUSR)
    except OSError:
        pass


def check_duplicate_and_confirm(url: str, title: str, history: dict) -> bool:
    """
    Check if url OR title already exist in the history records.

    Returns:
        True  — safe to proceed (no duplicate, or user forced override).
        False — user chose to abort (pipeline should exit 0).
    """
    records = history.get("records", [])
    matched = [
        r for r in records
        if r.get("url") == url or r.get("title") == title
    ]
    if not matched:
        return True  # no duplicate — green light

    # ── Duplicate detected — print warning banner ─────────────────────────
    os.system("clear")
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║  ⚠️  DUPLICATE DETECTED" + " " * 48 + "║")
    print("║" + " " * 68 + "║")
    print("║  You have already processed and clipped this specific video    ║")
    print("║  source in a previous production session.                      ║")
    print("║" + " " * 68 + "║")
    if matched[0].get("completed_at"):
        ts = matched[0]["completed_at"]
        print(f"║  Previously processed : {ts:<44}║")
    if matched[0].get("title"):
        clipped_title = matched[0]["title"][:44]
        print(f"║  Title match          : {clipped_title:<44}║")
    print("║" + " " * 68 + "║")
    print("╚" + "═" * 68 + "╝")

    while True:
        answer = input(
            "\n❓  Do you want to ignore this safeguard and force process the video anyway? (y/n): "
        ).strip().lower()
        if answer in ("y", "yes"):
            print("   ⚡  Override confirmed — proceeding with forced reprocessing.")
            return True
        elif answer in ("n", "no"):
            print("   ✅  Safeguard respected. Exiting pipeline gracefully.")
            return False
        else:
            print("   ⚠️   Please enter 'y' or 'n'.")


def append_to_history(url: str, title: str, history: dict) -> None:
    """
    Add a completed processing record to history and persist to disk.
    Called once, AFTER ai_director.py finishes downloading all clips.
    """
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    timestamp = datetime.now(ist_tz).isoformat()
    history["records"].append({
        "url":          url,
        "title":        title,
        "completed_at": timestamp,
    })
    save_history(history)
    print(f"   📝  History updated: '{title}' logged in {HISTORY_FILE}.")


# ─────────────────────────────────────────────
# Queue helpers
# ─────────────────────────────────────────────
def queue_is_empty() -> bool:
    chunks = glob.glob(os.path.join(QUEUE_DIR, "chunk_*.mp4"))
    return len(chunks) == 0


def state_exists() -> bool:
    return os.path.exists(STATE_FILE)


def read_state() -> dict:
    with open(STATE_FILE) as f:
        return json.load(f)


def prompt_and_save_layout_choice():
    """Check state.json for layout preference, prompt if missing, and save."""
    state = read_state()
    if "use_satisfying_base" not in state:
        while True:
            answer = input("\n❓  Apply satisfying gameplay base split layer? (y/n): ").strip().lower()
            if answer in ("y", "yes"):
                print("    ✅  Layout: 75/25 split with satisfying base.")
                state["use_satisfying_base"] = True
                break
            elif answer in ("n", "no"):
                print("    ✅  Layout: Full 1080×1920 canvas (no satisfying base).")
                state["use_satisfying_base"] = False
                break
            else:
                print("    ⚠️   Please enter 'y' or 'n'.")
        
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)


# ─────────────────────────────────────────────
# Script runner
# ─────────────────────────────────────────────
def run_script(name: str, path: str, args: list[str] = None):
    """
    Run a child Python script and propagate any non-zero exit code.

    stdin/stdout/stderr are intentionally inherited from the parent
    process (default when no redirection args are passed) so that:
      • Scripts can receive terminal prompts
      • tqdm progress bars and all print() output render correctly.
    """
    print(f"\n{'─' * 60}")
    print(f"▶  Running {name}: {path}")
    print(f"{'─' * 60}")
    
    cmd = [sys.executable, path]
    if args:
        cmd.extend(args)

    result = subprocess.run(
        cmd,
        stdin=None,   # explicitly inherit the parent’s stdin
    )
    if result.returncode != 0:
        print(
            f"\n❌  {name} exited with code {result.returncode}. "
            "Pipeline halted.",
            file=sys.stderr,
        )
        sys.exit(result.returncode)


# ─────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("   🧠  BRAINROT PIPELINE  —  [V7.8] ElevenLabs + History Engine")
    print("   🤖  Gemini AI Slicing + ElevenLabs Voice + SFX Compositor")
    print("=" * 60)

    # ── Step 0: Security gate ──────────────────────────────────
    verify_auth_token()

    # ── Step 1: Safety pre-checks ──────────────────────────────
    if not os.path.exists("client_secrets.json"):
        print(
            "❌  client_secrets.json not found.\n"
            "    Download it from Google Cloud Console → Credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not glob.glob("satisfying_base/*.mp4"):
        print(
            "❌  No satisfying videos found in satisfying_base/ directory.\n"
            "    Place your satisfying videos (.mp4) there before running.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(QUEUE_DIR, exist_ok=True)

    # ── Step 2: Fetch if queue is empty ───────────────────────
    needs_fetch = queue_is_empty() or not state_exists()

    if not needs_fetch:
        state_data = read_state()
        if str(state_data.get("current_chunk")) == "COMPLETED":
            needs_fetch = True

    if needs_fetch:
        print("\n💭  Queue is empty, or previous queue is completed (or no state.json found).")

        url = input("    🔗  Enter the YouTube video URL to process: ").strip()
        if not url:
            print("    ❌  No URL provided. Exiting.", file=sys.stderr)
            sys.exit(1)

        # ── V7.8: Duplicate History Checkpoint ──────────────────────────────
        history = load_history()

        # Attempt a fast pre-check using URL only (title not known yet).
        # A full title-match check runs after ai_director resolves the title.
        url_records = [r for r in history.get("records", []) if r.get("url") == url]
        if url_records:
            should_continue = check_duplicate_and_confirm(url, url_records[0].get("title", ""), history)
            if not should_continue:
                sys.exit(0)

        print("    Running ai_director.py…")
        run_script("AI Director", SCRIPTS["fetcher"], args=[url])

        # ── V7.8: Append to history after successful fetch ──────────────────
        # Read the title that ai_director just saved to state.json
        try:
            with open(STATE_FILE, encoding="utf-8") as sf:
                fresh_state = json.load(sf)
            resolved_title = fresh_state.get("original_title", "Unknown Title")
        except (OSError, json.JSONDecodeError):
            resolved_title = "Unknown Title"

        # Run a second, more precise title-level duplicate check before appending
        if not url_records:  # URL was not a duplicate; check title now
            title_records = [r for r in history.get("records", []) if r.get("title") == resolved_title]
            if title_records:
                print(
                    f"\n⚠️   Title duplicate detected ('{resolved_title}' was previously processed). "
                    "History not re-appended."
                )
            else:
                append_to_history(url, resolved_title, history)
        else:
            # URL was already a duplicate but user forced through — still log the run
            append_to_history(url, resolved_title, history)
    else:
        state   = read_state()
        current = state.get("current_chunk", 1)
        total   = state.get("total_chunks", "?")
        title   = state.get("original_title", "?")
        print(f"\n📬  Queue ready — chunk {current}/{total}  |  {title!r}")

    # ── Step 3: Edit (compose 75/25 split-screen, Blur+Fit + subtitles) ────
    prompt_and_save_layout_choice()
    run_script("Smart Editor [V7.8] (Widescreen Hybrid Zoom + Dynamic Layout)", SCRIPTS["editor"])

    # ── Step 4: Upload + open browser + advance state ─────────
    run_script("Uploader", SCRIPTS["uploader"])

    # ── Done ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("   ✅  ONE chunk processed  [V7.8]. YouTube Studio should be open.")
    print("       Review the draft, then run main.py again for next chunk.")
    print("=" * 60 + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
