#!/usr/bin/env python3
import json
import subprocess
import sys
import os

STATE_FILE = "state.json"

def main():
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
        
        # Run main.py and automatically pipe "y\n" to standard input.
        # This handles the Layout prompt (if missing) AND the final Smart Reset prompt.
        result = subprocess.run(
            [sys.executable, "main.py"],
            input="y\n",
            text=True
        )
        
        if result.returncode != 0:
            print(f"\n❌ main.py crashed on chunk {current + i}. Halting unattended run!")
            print("⚠️ System will NOT shut down so you can review the error logs.")
            sys.exit(result.returncode)

    print("\n🎉 All chunks processed and scheduled successfully!")
    print("🛑 Executing system shutdown...")
    os.system("sudo shutdown now")

if __name__ == "__main__":
    main()
