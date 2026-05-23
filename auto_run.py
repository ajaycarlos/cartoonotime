#!/usr/bin/env python3
import json
import subprocess
import sys
import os

STATE_FILE = "state.json"

def main():
    # Prompt user for shutdown toggle at the very beginning of the script
    shutdown_choice = input("Do you want to shut down the PC after the entire queue is processed? (y/n): ").strip().lower()
    
    os.environ["AUTO_RUN"] = "1"
    print("🚀 Starting Unattended Batch Runner...")
    
    if not os.path.exists(STATE_FILE):
        print("❌ state.json not found! Run main.py manually first to fetch a video.")
        sys.exit(1)

    with open(STATE_FILE) as f:
        state = json.load(f)
        
    current = state.get("current_chunk", 1)
    total = state.get("total_chunks", 1)

    if str(current) == "COMPLETED" or current > total:
        print("✅ Queue is already completed.")
        sys.exit(0)

    chunks_left = total - current + 1
    print(f"📦 Found {chunks_left} chunk(s) left in the queue.\n")

    for i in range(chunks_left):
        print(f"{'='*50}")
        print(f"▶️ Executing chunk {current + i}/{total}...")
        print(f"{'='*50}")
        
        if os.path.exists("ready_to_upload.mp4"):
            print("⏩ Found existing 'ready_to_upload.mp4'. Skipping audio and video generation. Resuming upload phase...")
        
        # Run main.py. In AUTO_RUN mode, prompts are automatically bypassed.
        result = subprocess.run(
            [sys.executable, "main.py"],
            text=True
        )
        
        if result.returncode != 0:
            print(f"\n❌ main.py crashed on chunk {current + i}. Halting unattended run!")
            if shutdown_choice == 'y':
                print("🛑 Executing system shutdown due to crash as requested...")
                os.system("shutdown /s /t 60")
            else:
                print("🛑 Unattended run halted due to crash. Exit cleanly.")
            sys.exit(result.returncode)

    print("\n🎉 All chunks processed and scheduled successfully!")
    if shutdown_choice == 'y':
        print("🛑 Executing system shutdown...")
        os.system("shutdown /s /t 60")
    else:
        print("🛑 Queue processing complete. System will not shut down. Exiting cleanly.")
        sys.exit(0)

if __name__ == "__main__":
    main()
