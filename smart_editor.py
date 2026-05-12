#!/usr/bin/env python3
"""
smart_editor.py — The 75/25 Split-Screen Editor
Reads chunk_X.mp4 from /queue and satisfying_base.mp4 from the root.

OpenCV pass (lightweight, 1 fps):
  • Scans chunk_X at 1 frame/sec using Haar Cascade face detection.
  • Falls back to frame center if no faces found.
  • Computes the average X-coordinate of detected faces → "center of action."
  • This X-offset is used to crop the top panel — no dynamic zoom.

75/25 Split (1080 × 1920 YouTube Short):
  • Top  (cartoon)   : 1080 × 1440  (75 % of 1920)
  • Bottom (satisfying): 1080 × 480   (25 % of 1920)

FFmpeg:
  • Uses a single filter_complex with vstack.
  • Output: ready_to_upload.mp4 — libx264, preset ultrafast, CRF 28.

Optimised for i3-3220 / 8 GB RAM.
"""

import os
import sys
import json
import random
import subprocess

# OpenCV is only used for the lightweight 1-fps analysis pass
try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False
    print(
        "⚠️   cv2 (opencv-python) not installed. "
        "Falling back to centre-crop for the top panel.",
        file=sys.stderr,
    )


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
STATE_FILE   = "state.json"
QUEUE_DIR    = "queue"
BASE_VIDEO   = "satisfying_base.mp4"
OUTPUT_FILE  = "ready_to_upload.mp4"
CASCADE_FILE = "haarcascade_frontalface_default.xml"

# Final output: 9:16 vertical Short
OUT_W   = 1080
OUT_H   = 1920
TOP_H   = int(OUT_H * 0.75)   # 1440 px  (75 %)
BOT_H   = OUT_H - TOP_H       # 480  px  (25 %)


# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError(
            f"{STATE_FILE} not found. Run interactive_fetcher.py first."
        )
    with open(STATE_FILE) as f:
        return json.load(f)


# ─────────────────────────────────────────────
# ffprobe helpers
# ─────────────────────────────────────────────
def get_video_duration(path: str) -> float:
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


def get_video_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) of the first video stream."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    w, h = result.stdout.strip().split(",")
    return int(w), int(h)


# ─────────────────────────────────────────────
# OpenCV: lightweight 1-fps face scan
# ─────────────────────────────────────────────
def find_action_center_x(chunk_path: str, source_width: int) -> int:
    """
    Sample the cartoon chunk at 1 frame per second using OpenCV.
    Run Haar Cascade face detection on each sampled frame.
    Return the average X-coordinate (center of bounding box) of all
    detected faces across all sampled frames.

    Falls back to source_width // 2 if:
      • OpenCV is not installed, OR
      • the cascade file is missing, OR
      • no faces are detected in any frame.

    No dynamic zoom is performed — this value is used only for the
    horizontal crop offset of the top panel.
    """
    fallback = source_width // 2
    print(f"\n🔍  OpenCV face scan on: {chunk_path}")

    if not OPENCV_AVAILABLE:
        print("   ℹ️   OpenCV unavailable — using centre-crop.")
        return fallback

    if not os.path.exists(CASCADE_FILE):
        print(
            f"   ⚠️   {CASCADE_FILE} not found — using centre-crop.\n"
            "       (Place the cascade XML in the project root for panning.)"
        )
        return fallback

    cap = cv2.VideoCapture(chunk_path)
    if not cap.isOpened():
        print("   ⚠️   Could not open chunk with OpenCV — using centre-crop.")
        return fallback

    fps     = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step    = max(1, int(fps))           # read 1 frame per second
    cascade = cv2.CascadeClassifier(CASCADE_FILE)

    x_coords: list[int] = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % step == 0:
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30),
            )
            for (x, y, w, h) in faces:
                x_coords.append(x + w // 2)   # center of face bounding box
        frame_idx += 1

    cap.release()

    if x_coords:
        avg_x = int(sum(x_coords) / len(x_coords))
        print(
            f"   ✅  Detected {len(x_coords)} face sample(s) across "
            f"{frame_idx // step} frame(s)."
        )
        print(f"   🎯  Average action center X : {avg_x}px")
        return avg_x
    else:
        print("   ℹ️   No faces detected — using centre-crop.")
        return fallback


# ─────────────────────────────────────────────
# Crop offset calculation
# ─────────────────────────────────────────────
def compute_crop_x(action_center_x: int, source_w: int) -> int:
    """
    Given the source frame width and the horizontal center of action,
    compute the X offset for an OUT_W-wide crop.

    The crop window is OUT_W pixels wide and is centered on action_center_x,
    clamped so it never exceeds the source frame boundaries.

    If the source is narrower than OUT_W, the crop X is forced to 0 and
    the scaling step in filter_complex will handle upscaling.
    """
    if source_w <= OUT_W:
        return 0
    # Center the crop window on action_center_x
    x = action_center_x - OUT_W // 2
    # Clamp to valid range
    x = max(0, min(x, source_w - OUT_W))
    return x


# ─────────────────────────────────────────────
# FFmpeg command builder
# ─────────────────────────────────────────────
def build_ffmpeg_cmd(
    cartoon_chunk: str,
    base_video:    str,
    base_start:    float,
    crop_x:        int,
    output:        str,
) -> list[str]:
    """
    Build the FFmpeg filter_complex command for the 75/25 split-screen.

    Layout (1080 × 1920 Short):
      ┌─────────────────────────┐
      │  Cartoon  — 1080 × 1440 │  ← 75 % (TOP_H)
      ├─────────────────────────┤
      │  Satisfying — 1080 × 480│  ← 25 % (BOT_H)
      └─────────────────────────┘
    """
    filter_complex = (
        # ── Top panel: scale height to exactly TOP_H (1440), let width expand, then crop
        # crop_x pins the horizontal window to the "center of action"
        f"[0:v]scale=-1:{TOP_H},crop={OUT_W}:{TOP_H}:{crop_x}:0[vtop];"

        # ── Bottom panel: satisfying video (sought via -ss before decode)
        # Scale height to exactly BOT_H (480), then center-crop
        f"[1:v]scale=-1:{BOT_H},crop={OUT_W}:{BOT_H}:(in_w-{OUT_W})/2:(in_h-{BOT_H})/2[vbottom];"

        # ── Vertical stack
        "[vtop][vbottom]vstack=inputs=2[vout];"

        # ── Audio: cartoon only, normalised for legibility
        "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[aout]"
    )

    return [
        "ffmpeg", "-y",
        # Input 0: cartoon chunk (from queue)
        "-i", cartoon_chunk,
        # Input 1: satisfying base — seek BEFORE decode for speed
        "-ss", f"{base_start:.3f}",
        "-t",  str(60),
        "-i", base_video,
        # Filter graph
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        # Video codec
        "-c:v", "libx264",
        "-preset", "ultrafast",   # maximum speed for constrained hardware
        "-crf", "28",             # quality / size balance
        # Audio codec
        "-c:a", "aac",
        "-b:a", "128k",
        # Stop at the shorter stream
        "-shortest",
        output,
    ]


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def process_video():
    # 1. Read state
    state          = load_state()
    current_chunk  = state.get("current_chunk", 1)
    total_chunks   = state.get("total_chunks", 5)
    original_title = state.get("original_title", "Unknown")

    print(f"\n🎬  smart_editor — Processing chunk {current_chunk}/{total_chunks}")
    print(f"    Title: {original_title!r}")
    print(f"    Split: {TOP_H}px top (75%) / {BOT_H}px bottom (25%)")

    # 2. Locate cartoon chunk
    chunk_file = os.path.join(QUEUE_DIR, f"chunk_{current_chunk}.mp4")
    if not os.path.exists(chunk_file):
        raise FileNotFoundError(
            f"Chunk not found: {chunk_file}\n"
            "Run interactive_fetcher.py or verify state.json."
        )

    # 3. Verify base video
    if not os.path.exists(BASE_VIDEO):
        raise FileNotFoundError(
            f"Base video not found: {BASE_VIDEO}\n"
            "Place satisfying_base.mp4 in the project root."
        )

    # 4. Lightweight OpenCV pass → center of action X
    source_w, source_h = get_video_dimensions(chunk_file)
    print(f"    Source dimensions : {source_w} × {source_h}")

    action_x = find_action_center_x(chunk_file, source_w)
    crop_x   = compute_crop_x(action_x, source_w)
    print(f"    Crop X offset     : {crop_x}px")

    # 5. Random start for the bottom panel
    base_dur   = get_video_duration(BASE_VIDEO)
    base_start = 0.0
    if base_dur > 60.0:
        base_start = random.uniform(0.0, base_dur - 60.0)
    print(f"    Base video length : {base_dur:.1f}s")
    print(f"    Random base start : {base_start:.2f}s")

    # 6. Build and run FFmpeg
    cmd = build_ffmpeg_cmd(chunk_file, BASE_VIDEO, base_start, crop_x, OUTPUT_FILE)
    print(f"\n⚙️   Running FFmpeg…")
    print("    " + " ".join(cmd))
    print()
    subprocess.run(cmd, check=True)

    print(f"\n✅  Output written: {OUTPUT_FILE}")
    print(f"    Top  (cartoon)    : {OUT_W} × {TOP_H}")
    print(f"    Bottom (satisfying): {OUT_W} × {BOT_H}")


# ─────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        process_video()
    except Exception as exc:
        print(f"\n❌  smart_editor failed: {exc}", file=sys.stderr)
        sys.exit(1)
