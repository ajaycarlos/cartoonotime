#!/usr/bin/env python3
"""
auto_run.py — The Brainrot Pipeline Orchestrator  ([V7.8] ElevenLabs + History Engine)
Unattended Mode: Batch Processing
"""

import os
import sys
import json
import subprocess
from pipeline_core import *

def main():
    print("=" * 60)
    print("   🧠  BRAINROT PIPELINE  —  [V7.8] ElevenLabs + History Engine")
    print("   🤖  Unattended Mode: Batch Processing")
    print("=" * 60)

    # Ask for shutdown toggle initially (Interactive phase before automation begins)
    shutdown_choice = input("Do you want to shut down the PC after the entire queue is processed? (y/n): ").strip().lower()
    
    os.environ["AUTO_RUN"] = "1"
    print("\n🚀 Starting Unattended Batch Runner...")
    
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
        # In Unattended Mode, if we need to fetch, we MUST ask for the URL upfront before we leave the user.
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

        # Auto_run duplicate check bypass/automate
        should_continue = check_duplicate_and_confirm(url, fetched_title, history, auto_run=True)
        if not should_continue:
            sys.exit(0)

        print("    Running ai_director.py…")
        try:
            run_script("AI Director", SCRIPTS["fetcher"], args=[url])
        except SystemExit as e:
            if e.code != 0:
                print(f"\n❌ Pipeline crashed during fetch. Halting unattended run!")
                if shutdown_choice in ('y', 'yes'):
                    print("🛑 Executing system shutdown due to crash as requested...")
                    os.system("shutdown /s /t 60")
                sys.exit(e.code)
            sys.exit(0)

        try:
            with open(STATE_FILE, encoding="utf-8") as sf:
                fresh_state = json.load(sf)
            resolved_title = fresh_state.get("original_title", fetched_title)
        except (OSError, json.JSONDecodeError):
            resolved_title = fetched_title

        append_to_history(url, resolved_title, history)

    # ── Step 2: Process remaining chunks in loop ───────────────
    state = read_state()
    current = state.get("current_chunk", 1)
    total = state.get("total_chunks", 1)

    if str(current) == "COMPLETED" or current > total:
        print("✅ Queue is already completed.")
        sys.exit(0)

    chunks_left = total - current + 1
    print(f"📦 Found {chunks_left} chunk(s) left in the queue.\n")

    for i in range(chunks_left):
        state = read_state() # Re-read to ensure we have latest current_chunk
        current = state.get("current_chunk", 1)

        print(f"\n{'='*50}")
        print(f"▶️ Executing chunk {current}/{total}...")
        print(f"{'='*50}")

        try:
            if os.path.exists("ready_to_upload.mp4"):
                print("\n⏩ Found existing 'ready_to_upload.mp4'. Skipping audio and video generation. Resuming upload phase...")
            else:
                prompt_and_save_layout_choice(auto_run=True)
                run_script("Smart Editor [V7.8] (Widescreen Hybrid Zoom + Dynamic Layout)", SCRIPTS["editor"])

            run_script("Uploader", SCRIPTS["uploader"])
        except SystemExit as e:
            if e.code != 0:
                print(f"\n❌ Pipeline crashed on chunk {current}. Halting unattended run!")
                if shutdown_choice in ('y', 'yes'):
                    print("🛑 Executing system shutdown due to crash as requested...")
                    os.system("shutdown /s /t 60")
                sys.exit(e.code)
            else:
                # normal exit (e.g. from duplicate check abort)
                sys.exit(0)

    print("\n🎉 All chunks processed and scheduled successfully!")
    if shutdown_choice in ('y', 'yes'):
        print("🛑 Executing system shutdown...")
        os.system("shutdown /s /t 60")
    else:
        print("🛑 Queue processing complete. System will not shut down. Exiting cleanly.")
        sys.exit(0)

if __name__ == "__main__":
    main()
