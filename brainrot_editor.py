#!/usr/bin/env python3
"""
brainrot_editor.py — The Split-Screen Engine
Reads the current chunk from state.json, composites it with a random
60-second window of satisfying_base.mp4 in a 9:16 vstack layout, and
outputs ready_to_upload.mp4.

Optimised for i3-3220 / 8 GB RAM: all video work delegated to FFmpeg
with -preset ultrafast so the CPU stays comfortable.
"""

import os
import sys
import json
import random
import subprocess


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
STATE_FILE   = "state.json"
QUEUE_DIR    = "queue"
BASE_VIDEO   = "satisfying_base.mp4"
OUTPUT_FILE  = "ready_to_upload.mp4"

# Final output dimensions (9:16 vertical)
OUT_W = 1080
OUT_H = 1920
HALF_H = OUT_H // 2     # 960 — each panel gets exactly half


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError(
            f"{STATE_FILE} not found. Run brainrot_fetcher.py first."
        )
    with open(STATE_FILE) as f:
        return json.load(f)


def get_video_duration(path: str) -> float:
    """Return duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def random_start(base_duration: float, segment_duration: float = 60.0) -> float:
    """Pick a uniformly random start so the bottom panel is always unique."""
    if base_duration > segment_duration:
        return random.uniform(0.0, base_duration - segment_duration)
    return 0.0


def build_ffmpeg_cmd(
    cartoon_chunk: str,
    base_video: str,
    base_start: float,
    output: str,
) -> list[str]:
    """
    Assemble the FFmpeg command for the split-screen composite.

    Layout (9:16 = 1080×1920):
      ┌─────────────────┐
      │  Cartoon (top)  │  1080 × 960
      ├─────────────────┤
      │  Satisfying     │  1080 × 960
      │  (bottom, muted)│
      └─────────────────┘

    Audio: cartoon track only — satisfying video audio is discarded.
    """
    filter_complex = (
        # ── Top panel: cartoon scaled + cropped to 1080×960
        f"[0:v]scale={OUT_W}:{HALF_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{HALF_H}[vtop];"

        # ── Bottom panel: satisfying video (already seeked with -ss)
        f"[1:v]scale={OUT_W}:{HALF_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{HALF_H}[vbottom];"

        # ── Stack panels vertically
        "[vtop][vbottom]vstack=inputs=2[vout];"

        # ── Audio: cartoon only (apply light normalisation, keep it legible)
        "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[aout]"
    )

    return [
        "ffmpeg", "-y",
        # Input 0: cartoon chunk
        "-i", cartoon_chunk,
        # Input 1: satisfying base — seek BEFORE decode (fast)
        "-ss", f"{base_start:.3f}",
        "-t", "60",
        "-i", base_video,
        # Filter graph
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        # Video codec
        "-c:v", "libx264",
        "-preset", "ultrafast",    # max speed for constrained hardware
        "-crf", "28",              # reasonable quality/size trade-off
        # Audio codec
        "-c:a", "aac",
        "-b:a", "128k",
        # Stop when the shortest stream ends
        "-shortest",
        output,
    ]


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def process_video():
    # 1. Read state
    state = load_state()
    current_chunk = state.get("current_chunk", 1)
    total_chunks  = state.get("total_chunks", 5)
    original_title = state.get("original_title", "Unknown")

    print(f"\n🎬  brainrot_editor — Processing chunk {current_chunk}/{total_chunks}")
    print(f"    Title: {original_title!r}")

    # 2. Locate cartoon chunk
    chunk_file = os.path.join(QUEUE_DIR, f"chunk_{current_chunk}.mp4")
    if not os.path.exists(chunk_file):
        raise FileNotFoundError(
            f"Chunk not found: {chunk_file}\n"
            "Run brainrot_fetcher.py or verify state.json."
        )

    # 3. Verify base video
    if not os.path.exists(BASE_VIDEO):
        raise FileNotFoundError(
            f"Base video not found: {BASE_VIDEO}\n"
            "Place satisfying_base.mp4 in the project root."
        )

    # 4. Random start for the bottom panel
    base_dur   = get_video_duration(BASE_VIDEO)
    base_start = random_start(base_dur)
    print(f"    Base video duration : {base_dur:.1f}s")
    print(f"    Random start offset : {base_start:.2f}s")

    # 5. Build and run FFmpeg
    cmd = build_ffmpeg_cmd(chunk_file, BASE_VIDEO, base_start, OUTPUT_FILE)
    print(f"\n⚙️   Running FFmpeg…")
    print("    " + " ".join(cmd))

    subprocess.run(cmd, check=True)

    print(f"\n✅  Output written: {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        process_video()
    except Exception as exc:
        print(f"\n❌  brainrot_editor failed: {exc}", file=sys.stderr)
        sys.exit(1)
