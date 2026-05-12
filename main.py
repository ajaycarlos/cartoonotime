#!/usr/bin/env python3
"""
main.py — The Brainrot Pipeline Orchestrator  (v3)
Processes exactly ONE chunk per execution:

  1. Security check (API_AUTH_TOKEN env var)
  2. If /queue is empty → run interactive_fetcher.py
     (PAUSES the pipeline to ask the user for their intro start time)
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


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
STATE_FILE  = "state.json"
QUEUE_DIR   = "queue"

SCRIPTS = {
    "fetcher":  "interactive_fetcher.py",
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
    print("🔒  Security check passed.")


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


# ─────────────────────────────────────────────
# Script runner
# ─────────────────────────────────────────────
def run_script(name: str, path: str):
    """
    Run a child Python script and propagate any non-zero exit code.

    Note: interactive_fetcher.py is interactive (reads stdin), so we
    run it WITHOUT stdout/stderr redirection so the user sees all output
    and the tqdm progress bar renders correctly.
    """
    print(f"\n{'─' * 60}")
    print(f"▶  Running {name}: {path}")
    print(f"{'─' * 60}")
    result = subprocess.run([sys.executable, path])
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
    print("   🧠  BRAINROT PIPELINE  —  v3")
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

    if not os.path.exists("satisfying_base.mp4"):
        print(
            "❌  satisfying_base.mp4 not found in project root.\n"
            "    Place your satisfying video there before running.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(QUEUE_DIR, exist_ok=True)

    # ── Step 2: Fetch if queue is empty ───────────────────────
    needs_fetch = queue_is_empty() or not state_exists()

    if needs_fetch:
        print("\n📭  Queue is empty (or no state.json found).")
        print(
            "    Running interactive_fetcher.py…\n"
            "    ⚠️   The pipeline will PAUSE to ask for your intro start time."
        )
        # interactive_fetcher reads stdin → run as inherited process
        run_script("Interactive Fetcher", SCRIPTS["fetcher"])
    else:
        state   = read_state()
        current = state.get("current_chunk", 1)
        total   = state.get("total_chunks", "?")
        title   = state.get("original_title", "?")
        print(f"\n📬  Queue ready — chunk {current}/{total}  |  {title!r}")

    # ── Step 3: Edit (compose 75/25 split-screen) ─────────────
    run_script("Smart Editor (75/25)", SCRIPTS["editor"])

    # ── Step 4: Upload + open browser + advance state ─────────
    run_script("Uploader", SCRIPTS["uploader"])

    # ── Done ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("   ✅  ONE chunk processed. YouTube Studio should be open.")
    print("       Review the draft, then run main.py again for next chunk.")
    print("=" * 60 + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
