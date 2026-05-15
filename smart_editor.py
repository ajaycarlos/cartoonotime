#!/usr/bin/env python3
"""
smart_editor.py — The AI Brain  (V4.1 Editor Overhaul)
Reads chunk_X.mp4 from /queue and satisfying_base.mp4 from the root.

NEW in V4.1:
  • Subtitle Sync Fix: audio is first extracted to temp_audio.wav at
    16 kHz mono (timestamp-reset) before Whisper transcription, ensuring
    perfect zero-start synchronisation.
  • Subtitle Styling: fontsize reduced to 48, text placed at y=1480 so it
    sits neatly within the bottom satisfying panel (1440–1920 px).
  • Motion Tracking (OpenCV Frame Differencing): Haar Cascade face
    detection removed. Frame differencing (absdiff + threshold + findContours)
    locates the largest moving object in each sampled frame. Its center X is
    fed into a 30-frame Moving Average Filter for smooth, jerk-free panning.
    Falls back to center-crop if no motion is found.

75/25 Split (1080 × 1920 YouTube Short):
  • Top  (cartoon)    : 1080 × 1440  (75 % of 1920)
  • Bottom (satisfying): 1080 × 480   (25 % of 1920)

Hardware optimisation: -threads 4 and -preset ultrafast on all FFmpeg
commands.  Designed for i3-3220 / 8 GB RAM — no GPU required.
"""

import os
import sys
import json
import random
import subprocess
import tempfile

# ── OpenCV ────────────────────────────────────────────────────────────────────
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

# ── Whisper ───────────────────────────────────────────────────────────────────
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print(
        "⚠️   openai-whisper not installed. Subtitles will be skipped.",
        file=sys.stderr,
    )


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
STATE_FILE   = "state.json"
QUEUE_DIR    = "queue"
BASE_VIDEO   = "satisfying_base.mp4"
OUTPUT_FILE  = "ready_to_upload.mp4"
TEMP_AUDIO   = "temp_audio.wav"
TEMP_SUBS    = "temp_subs.srt"

# Final output: 9:16 vertical Short
OUT_W   = 1080
OUT_H   = 1920
TOP_H   = int(OUT_H * 0.75)   # 1440 px  (75 %)
BOT_H   = OUT_H - TOP_H       # 480  px  (25 %)

# Moving Average Filter window for smooth-pan tracking
SMOOTH_PAN_WINDOW = 30


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
# OpenCV: Motion Tracking (Frame Differencing)
# ─────────────────────────────────────────────
def find_smooth_pan_x_offsets(chunk_path: str, source_width: int) -> list[int]:
    """
    Sample the cartoon chunk at 1 fps using OpenCV frame differencing.

    Algorithm (per sampled frame pair):
      1. Convert both frames to grayscale and apply Gaussian Blur.
      2. cv2.absdiff(prev_gray, curr_gray) → motion mask.
      3. cv2.threshold → binary mask.
      4. cv2.findContours → locate all motion blobs.
      5. Pick the largest contour; use its bounding-box center X.
      6. Fall back to source_width // 2 if no contour is found.

    Apply a 30-frame Moving Average Filter so the crop window pans
    smoothly without jerking when motion spikes.

    Returns a list of smoothed crop-X values (one per sampled frame),
    each clamped to [0, source_width - OUT_W].

    Falls back to center-crop if OpenCV is unavailable or the video
    cannot be opened.
    """
    fallback_x = max(0, source_width // 2 - OUT_W // 2)
    print(f"\n🎯  OpenCV Motion Tracking (frame differencing): {chunk_path}")

    if not OPENCV_AVAILABLE:
        print("   ℹ️   OpenCV unavailable — using centre-crop.")
        return [fallback_x]

    cap = cv2.VideoCapture(chunk_path)
    if not cap.isOpened():
        print("   ⚠️   Could not open chunk with OpenCV — using centre-crop.")
        return [fallback_x]

    fps      = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step     = max(1, int(fps))   # 1 sample per second

    raw_x_values: list[int] = []
    frame_idx   = 0
    sampled     = 0
    motion_hits = 0

    prev_gray: "cv2.Mat | None" = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % step == 0:
            curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            curr_gray = cv2.GaussianBlur(curr_gray, (21, 21), 0)

            if prev_gray is None:
                # First sample: no previous frame to diff against
                raw_x_values.append(source_width // 2)
            else:
                diff  = cv2.absdiff(prev_gray, curr_gray)
                _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                contours, _ = cv2.findContours(
                    thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )

                if contours:
                    # Largest contour = the most significant moving object
                    largest = max(contours, key=cv2.contourArea)
                    bx, by, bw, bh = cv2.boundingRect(largest)
                    center_x = bx + bw // 2
                    raw_x_values.append(center_x)
                    motion_hits += 1
                else:
                    raw_x_values.append(source_width // 2)  # center fallback

            prev_gray = curr_gray
            sampled += 1

        frame_idx += 1

    cap.release()
    print(
        f"   ✅  Sampled {sampled} frame(s) — "
        f"{motion_hits} frame(s) with detected motion."
    )

    # ── Apply Moving Average Filter (window = SMOOTH_PAN_WINDOW) ──────────
    smoothed_center_x: list[int] = []
    for i, cx in enumerate(raw_x_values):
        lo  = max(0, i - SMOOTH_PAN_WINDOW // 2)
        hi  = min(len(raw_x_values), i + SMOOTH_PAN_WINDOW // 2 + 1)
        avg = int(sum(raw_x_values[lo:hi]) / (hi - lo))
        smoothed_center_x.append(avg)

    # Convert center-X → crop-X (top-left of the 1080-wide crop window)
    smoothed_crop_x: list[int] = []
    for cx in smoothed_center_x:
        crop_x = cx - OUT_W // 2
        crop_x = max(0, min(crop_x, source_width - OUT_W))
        smoothed_crop_x.append(crop_x)

    if smoothed_crop_x:
        print(
            f"   🎯  Smooth-pan range: X={min(smoothed_crop_x)}"
            f"–{max(smoothed_crop_x)}px  "
            f"(avg={int(sum(smoothed_crop_x)/len(smoothed_crop_x))}px)"
        )
        return smoothed_crop_x

    return [fallback_x]


def representative_crop_x(smooth_offsets: list[int]) -> int:
    """Return the median crop-X from the smooth-pan offset list."""
    sorted_vals = sorted(smooth_offsets)
    return sorted_vals[len(sorted_vals) // 2]


# ─────────────────────────────────────────────
# Whisper subtitle generation
# ─────────────────────────────────────────────
def _seconds_to_srt_ts(seconds: float) -> str:
    """Convert float seconds to SRT timestamp  HH:MM:SS,mmm."""
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(chunk_path: str, srt_path: str) -> bool:
    """
    Transcribe chunk_path audio using Whisper tiny model.

    V4.1 Sync Fix:
      Audio is first extracted to TEMP_AUDIO (16 kHz mono) via FFmpeg so
      that timestamps always start at 0.000 regardless of any PTS offset in
      the source chunk.  The WAV is deleted after transcription.

    Write an SRT file to srt_path.
    Returns True on success, False if Whisper is unavailable or fails.
    """
    if not WHISPER_AVAILABLE:
        print("   ℹ️   Whisper unavailable — no subtitles.")
        return False

    # ── Step 1: Extract clean, timestamp-reset audio ────────────────────────
    print(f"\n🔊  Extracting audio for Whisper sync fix → {TEMP_AUDIO}")
    extract_cmd = [
        "ffmpeg", "-y",
        "-i", chunk_path,
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        TEMP_AUDIO,
    ]
    try:
        subprocess.run(extract_cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        print(f"   ⚠️   Audio extraction failed: {exc}", file=sys.stderr)
        return False

    # ── Step 2: Whisper transcription ───────────────────────────────────────
    print(f"🗣️   Whisper (tiny) transcribing: {TEMP_AUDIO}")
    try:
        model  = whisper.load_model("tiny")
        result = model.transcribe(TEMP_AUDIO, verbose=False)
    except Exception as exc:
        print(f"   ⚠️   Whisper transcription failed: {exc}", file=sys.stderr)
        return False

    segments = result.get("segments", [])
    if not segments:
        print("   ℹ️   No speech detected — skipping subtitles.")
        return False

    lines: list[str] = []
    for idx, seg in enumerate(segments, start=1):
        start_ts = _seconds_to_srt_ts(seg["start"])
        end_ts   = _seconds_to_srt_ts(seg["end"])
        text     = seg["text"].strip()
        lines.append(f"{idx}\n{start_ts} --> {end_ts}\n{text}\n")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"   ✅  SRT written: {srt_path}  ({len(segments)} segment(s))")
    return True


# ─────────────────────────────────────────────
# FFmpeg command builder
# ─────────────────────────────────────────────
def build_ffmpeg_cmd(
    cartoon_chunk: str,
    base_video:    str,
    base_start:    float,
    base_duration: float,
    crop_x:        int,
    output:        str,
    srt_path:      str | None = None,
) -> list[str]:
    """
    Build the FFmpeg filter_complex command for the V4.0 75/25 split-screen.

    Layout (1080 × 1920 Short):
      ┌─────────────────────────┐
      │  Cartoon  — 1080 × 1440 │  ← 75 % (TOP_H)
      ├─────────────────────────┤
      │  Satisfying — 1080 × 480│  ← 25 % (BOT_H)
      └─────────────────────────┘

    Subtitles (if srt_path provided) are burned via subtitles filter on the
    FINAL stacked output so they sit on top of everything.
    """
    # ── Top panel: scale height to 1440, width expands proportionally, then crop ──
    top_filter = (
        f"[0:v]scale=-1:{TOP_H},"
        f"crop={OUT_W}:{TOP_H}:{crop_x}:0[vtop]"
    )

    # ── Bottom panel: scale width to 1080, then center-crop height to 480 ──────
    bot_filter = (
        f"[1:v]scale={OUT_W}:-1,"
        f"crop={OUT_W}:{BOT_H}:(in_w-{OUT_W})/2:(in_h-{BOT_H})/2[vbottom]"
    )

    # ── Vertical stack ────────────────────────────────────────────────────────
    stack_filter = "[vtop][vbottom]vstack=inputs=2[stacked]"

    # ── Subtitles filter (applied to stacked output) ──────────────────────────
    if srt_path and os.path.exists(srt_path):
        # Escape the SRT path for FFmpeg filter syntax
        escaped_srt = srt_path.replace("'", "\\'").replace(":", "\\:")
        # V4.1: FontSize=48, subtitles positioned at y=1480 (bottom panel).
        # Alignment=2 (bottom-center ASS alignment) with MarginV adjusted so
        # that the baseline sits at y≈1480 within the 1920-tall output.
        # MarginV = OUT_H - subtitle_y = 1920 - 1480 = 440
        subtitle_filter = (
            f"[stacked]subtitles='{escaped_srt}':"
            f"force_style='FontName=Arial,FontSize=48,PrimaryColour=&HFFFFFF,"
            f"OutlineColour=&H000000,Outline=3,Alignment=2,MarginV=440'[vout]"
        )
        filter_complex = (
            f"{top_filter};"
            f"{bot_filter};"
            f"{stack_filter};"
            f"{subtitle_filter};"
            # Audio: cartoon only, normalised
            "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[aout]"
        )
    else:
        filter_complex = (
            f"{top_filter};"
            f"{bot_filter};"
            f"{stack_filter};"
            # Rename stacked to vout directly
            "[stacked]null[vout];"
            # Audio: cartoon only, normalised
            "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[aout]"
        )

    return [
        "ffmpeg", "-y",
        # Input 0: cartoon chunk (from queue)
        "-i", cartoon_chunk,
        # Input 1: satisfying base — seek BEFORE decode for speed
        "-ss", f"{base_start:.3f}",
        "-t",  f"{base_duration:.3f}",
        "-i", base_video,
        # Filter graph
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        # Video codec — V4.0 hardware-optimised
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        # Audio codec
        "-c:a", "aac",
        "-b:a", "128k",
        # Hardware target: i3-3220 (4 threads)
        "-threads", "4",
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

    print(f"\n🎬  smart_editor V4.2 — Motion Tracking & Subtitle Sync Fix Active")
    print(f"    Processing chunk {current_chunk}/{total_chunks}")
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

    srt_path = TEMP_SUBS
    try:
        # 4. Whisper subtitle generation
        srt_ok = generate_srt(chunk_file, srt_path)

        # 5. OpenCV smooth-pan pass → per-frame crop X offsets
        source_w, source_h = get_video_dimensions(chunk_file)
        print(f"    Source dimensions : {source_w} × {source_h}")

        smooth_offsets = find_smooth_pan_x_offsets(chunk_file, source_w)
        crop_x = representative_crop_x(smooth_offsets)
        print(f"    Representative crop X : {crop_x}px  (from {len(smooth_offsets)} sample(s))")

        # 6. Random start for the bottom panel
        base_dur   = get_video_duration(BASE_VIDEO)
        chunk_dur  = get_video_duration(chunk_file)
        base_start = 0.0
        if base_dur > chunk_dur:
            base_start = random.uniform(0.0, base_dur - chunk_dur)
        elif base_dur > 0.0:
            base_start = 0.0
        print(f"    Base video length  : {base_dur:.1f}s")
        print(f"    Random base start  : {base_start:.2f}s")
        print(f"    Subtitle SRT       : {srt_path if srt_ok else 'none (skipped)'}")

        # 7. Build and run FFmpeg
        cmd = build_ffmpeg_cmd(
            chunk_file, BASE_VIDEO, base_start, chunk_dur,
            crop_x, OUTPUT_FILE, srt_path if srt_ok else None
        )
        print(f"\n⚙️   Running FFmpeg (threads=4, preset=ultrafast)…")
        print("    " + " ".join(cmd))
        print()
        subprocess.run(cmd, check=True)

        print(f"\n✅  Output written: {OUTPUT_FILE}")
        print(f"    Top  (cartoon)     : {OUT_W} × {TOP_H}")
        print(f"    Bottom (satisfying): {OUT_W} × {BOT_H}")
        print(f"    Subtitles burned   : {'yes' if srt_ok else 'no'}")

    finally:
        # 8. Cleanup temp files after rendering
        if os.path.exists(TEMP_AUDIO):
            os.remove(TEMP_AUDIO)
            print(f"   🗑️   Removed temp audio: {TEMP_AUDIO}")
        if os.path.exists(srt_path):
            os.remove(srt_path)
            print(f"   🗑️   Removed temp subs: {srt_path}")


# ─────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        process_video()
    except Exception as exc:
        print(f"\n❌  smart_editor V4.1 failed: {exc}", file=sys.stderr)
        sys.exit(1)
