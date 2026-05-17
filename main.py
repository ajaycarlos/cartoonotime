#!/usr/bin/env python3
"""
main.py — The Brainrot Pipeline Orchestrator  ([V7.5] AI Director Update)
Processes exactly ONE chunk per execution:

  1. Security check (API_AUTH_TOKEN & GEMINI_API_KEY env vars)
  2. If /queue is empty → prompt for YouTube URL & run ai_director.py
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

from dotenv import load_dotenv
load_dotenv()


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
STATE_FILE  = "state.json"
QUEUE_DIR   = "queue"

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

    print("🔒  Security checks passed (Auth Token & Gemini Key).")


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
    print("   🧠  BRAINROT PIPELINE  —  [V7.5] AI Director Update")
    print("   🤖  Gemini AI Slicing + Blur/Fit Active")
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
            
        print("    Running ai_director.py…")
        run_script("AI Director", SCRIPTS["fetcher"], args=[url])
    else:
        state   = read_state()
        current = state.get("current_chunk", 1)
        total   = state.get("total_chunks", "?")
        title   = state.get("original_title", "?")
        print(f"\n📬  Queue ready — chunk {current}/{total}  |  {title!r}")

    # ── Step 3: Edit (compose 75/25 split-screen, Blur+Fit + subtitles) ────
    prompt_and_save_layout_choice()
    run_script("Smart Editor [V7.5] (Widescreen Hybrid Zoom + Dynamic Layout)", SCRIPTS["editor"])

    # ── Step 4: Upload + open browser + advance state ─────────
    run_script("Uploader", SCRIPTS["uploader"])

    # ── Done ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("   ✅  ONE chunk processed  [V7.5]. YouTube Studio should be open.")
    print("       Review the draft, then run main.py again for next chunk.")
    print("=" * 60 + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
