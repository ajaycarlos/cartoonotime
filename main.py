#!/usr/bin/env python3
"""
main.py — The Brainrot Pipeline Orchestrator  ([V7.8] ElevenLabs + History Engine)
Interactive Mode: Single Chunk Processing
"""

import os
import sys
import json
import subprocess
from pipeline_core import *

def main():
    print("=" * 60)
    print("   🧠  BRAINROT PIPELINE  —  [V7.8] ElevenLabs + History Engine")
    print("   🤖  Interactive Mode: Single Chunk Processing")
    print("=" * 60)

    # ── Step 0: Security gate & Pre-checks ─────────────────────
    verify_auth_token()
    run_safety_pre_checks()

    # ── Step 1: Fetch if queue is empty ────────────────────────
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

        history = load_history()

        print("    🔍  Fetching video title for duplicate check...")
        try:
            title_cmd = ["yt-dlp", "--skip-download", "--print", "title", url]
            title_result = subprocess.run(title_cmd, capture_output=True, text=True, check=True)
            fetched_title = title_result.stdout.strip()
        except Exception:
            fetched_title = "Unknown Title"

        should_continue = check_duplicate_and_confirm(url, fetched_title, history, auto_run=False)
        if not should_continue:
            sys.exit(0)

        print("    Running ai_director.py…")
        run_script("AI Director", SCRIPTS["fetcher"], args=[url])

        try:
            with open(STATE_FILE, encoding="utf-8") as sf:
                fresh_state = json.load(sf)
            resolved_title = fresh_state.get("original_title", fetched_title)
        except (OSError, json.JSONDecodeError):
            resolved_title = fetched_title

        append_to_history(url, resolved_title, history)
    else:
        state   = read_state()
        current = state.get("current_chunk", 1)
        total   = state.get("total_chunks", "?")
        title   = state.get("original_title", "?")
        print(f"\n📬  Queue ready — chunk {current}/{total}  |  {title!r}")

    # ── Step 2: Edit (compose 75/25 split-screen, Blur+Fit) ────
    if os.path.exists("ready_to_upload.mp4"):
        print("\n⏩ Found existing 'ready_to_upload.mp4'. Skipping audio and video generation. Resuming upload phase...")
    else:
        prompt_and_save_layout_choice(auto_run=False)
        run_script("Smart Editor [V7.8] (Widescreen Hybrid Zoom + Dynamic Layout)", SCRIPTS["editor"])

    # ── Step 3: Upload + open browser + advance state ──────────
    run_script("Uploader", SCRIPTS["uploader"])

    # ── Done ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("   ✅  ONE chunk processed  [V7.8]. YouTube Studio should be open.")
    print("       Review the draft, then run main.py again for next chunk.")
    print("=" * 60 + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
