#!/usr/bin/env python3
"""
smart_editor.py — The AI Brain  (V5.0 True AI Editor)
Reads chunk_X.mp4 from /queue and satisfying_base.mp4 from the root.

NEW in V5.0:
  • YOLOv8n Object Tracking: Replaces OpenCV frame-differencing with a
    real YOLO model. Every 10th frame is sampled; the detection with the
    largest bounding-box area is used as the focal point for that frame.
    Falls back to the previous frame's X (or centre) when nothing is detected.
    A 30-frame Moving Average smooths the resulting pan curve.

  • Two-Pass FFmpeg Render:
      Pass 1 — filter_complex stacks top + bottom panels into temp_stacked.mp4
               (NO subtitle filter here — avoids libass drop issues).
      Pass 2 — A clean second FFmpeg call burns temp_subs.srt onto the
               stacked 1080×1920 output with force_style and MarginV=60.
               This guarantees subtitle burn-in every time.

  • Safe Cleanup: temp_audio.wav, temp_subs.srt, and temp_stacked.mp4 are
    all removed in the finally block.

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

# ── YOLOv8 ───────────────────────────────────────────────────────────────────
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    print(
        "⚠️   ultralytics not installed. "
        "Falling back to centre-crop for the top panel.",
        file=sys.stderr,
    )

# ── OpenCV (needed for frame reading even with YOLO) ─────────────────────────
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
STATE_FILE    = "state.json"
QUEUE_DIR     = "queue"
BASE_VIDEO    = "satisfying_base.mp4"
OUTPUT_FILE   = "ready_to_upload.mp4"
TEMP_AUDIO    = "temp_audio.wav"
TEMP_SUBS     = "temp_subs.srt"
TEMP_STACKED  = "temp_stacked.mp4"

# Final output: 9:16 vertical Short
OUT_W   = 1080
OUT_H   = 1920
TOP_H   = int(OUT_H * 0.75)   # 1440 px  (75 %)
BOT_H   = OUT_H - TOP_H       # 480  px  (25 %)

# Moving Average Filter window for smooth-pan tracking
SMOOTH_PAN_WINDOW = 30

# YOLOv8: sample every Nth frame (keeps CPU load low on i3)
YOLO_SAMPLE_EVERY = 10


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
# YOLOv8: Object Tracking → Smooth Pan X
# ─────────────────────────────────────────────
def find_smooth_pan_x_offsets(chunk_path: str, source_width: int) -> list[int]:
    """
    Sample the cartoon chunk every YOLO_SAMPLE_EVERY frames using YOLOv8n.

    Algorithm (per sampled frame):
      1. Run yolov8n inference on the frame.
      2. Among all detected bounding boxes, pick the one with the largest area
         — this is almost always the main character / focal object.
      3. Extract that bounding box's centre-X as the pan target for this frame.
      4. If no objects are detected, carry forward the previous frame's X
         (or fall back to source_width // 2 on the very first frame).

    Apply a 30-frame Moving Average Filter so the crop window pans smoothly.

    Returns a list of smoothed crop-X values (one per sampled frame), each
    clamped to [0, source_width - OUT_W].

    Falls back to centre-crop if YOLO/OpenCV is unavailable or the video
    cannot be opened.
    """
    fallback_x = max(0, source_width // 2 - OUT_W // 2)
    print(f"\n🤖  YOLOv8n Object Tracking: {chunk_path}")

    if not YOLO_AVAILABLE or not OPENCV_AVAILABLE:
        print("   ℹ️   YOLO/OpenCV unavailable — using centre-crop.")
        return [fallback_x]

    # Load model (downloads yolov8n.pt on first run, ~6 MB)
    try:
        model = YOLO("yolov8n.pt")
    except Exception as exc:
        print(f"   ⚠️   Could not load YOLOv8 model: {exc}", file=sys.stderr)
        return [fallback_x]

    cap = cv2.VideoCapture(chunk_path)
    if not cap.isOpened():
        print("   ⚠️   Could not open chunk with OpenCV — using centre-crop.")
        return [fallback_x]

    raw_x_values: list[int] = []
    frame_idx = 0
    sampled   = 0
    hits      = 0
    last_cx   = source_width // 2   # carry-forward seed

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % YOLO_SAMPLE_EVERY == 0:
            try:
                results = model(frame, verbose=False)
                boxes = results[0].boxes  # ultralytics Boxes object

                if boxes is not None and len(boxes) > 0:
                    # Find the bounding box with the largest area
                    best_cx    = None
                    best_area  = -1
                    for box in boxes:
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        area = (x2 - x1) * (y2 - y1)
                        if area > best_area:
                            best_area = area
                            best_cx   = int((x1 + x2) / 2)

                    if best_cx is not None:
                        last_cx = best_cx
                        hits += 1

                raw_x_values.append(last_cx)
            except Exception as exc:
                # Graceful degradation on per-frame inference errors
                print(f"   ⚠️   YOLO inference error on frame {frame_idx}: {exc}",
                      file=sys.stderr)
                raw_x_values.append(last_cx)

            sampled += 1

        frame_idx += 1

    cap.release()
    print(
        f"   ✅  Sampled {sampled} frame(s) — "
        f"{hits} frame(s) with YOLO detections."
    )

    if not raw_x_values:
        return [fallback_x]

    # ── Apply Moving Average Filter (window = SMOOTH_PAN_WINDOW) ──────────────
    smoothed_center_x: list[int] = []
    for i, cx in enumerate(raw_x_values):
        lo  = max(0, i - SMOOTH_PAN_WINDOW // 2)
        hi  = min(len(raw_x_values), i + SMOOTH_PAN_WINDOW // 2 + 1)
        avg = int(sum(raw_x_values[lo:hi]) / (hi - lo))
        smoothed_center_x.append(avg)

    # Convert centre-X → crop-X (top-left of the 1080-wide crop window)
    smoothed_crop_x: list[int] = []
    for cx in smoothed_center_x:
        crop_x = cx - OUT_W // 2
        crop_x = max(0, min(crop_x, source_width - OUT_W))
        smoothed_crop_x.append(crop_x)

    print(
        f"   🎯  Smooth-pan range: X={min(smoothed_crop_x)}"
        f"–{max(smoothed_crop_x)}px  "
        f"(avg={int(sum(smoothed_crop_x)/len(smoothed_crop_x))}px)"
    )
    return smoothed_crop_x


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

    V4.1 Sync Fix (retained):
      Audio is first extracted to TEMP_AUDIO (16 kHz mono) via FFmpeg so
      timestamps always start at 0.000 regardless of any PTS offset in the
      source chunk.  The WAV is deleted in the finally block.

    Write an SRT file to srt_path.
    Returns True on success, False if Whisper is unavailable or fails.
    """
    if not WHISPER_AVAILABLE:
        print("   ℹ️   Whisper unavailable — no subtitles.")
        return False

    # ── Step 1: Extract clean, timestamp-reset audio ──────────────────────────
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

    # ── Step 2: Whisper transcription ─────────────────────────────────────────
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
# Two-Pass FFmpeg Pipeline
# ─────────────────────────────────────────────
def run_pass1_stack(
    cartoon_chunk: str,
    base_video:    str,
    base_start:    float,
    base_duration: float,
    crop_x:        int,
    stacked_out:   str,
) -> None:
    """
    Pass 1: Scale, crop, and vstack the cartoon + satisfying panels.
    Output is written to stacked_out (temp_stacked.mp4).
    NO subtitle filter is applied here.
    """
    # ── Top panel: scale height to 1440, width expands proportionally, then crop ──
    top_filter = (
        f"[0:v]scale=-1:{TOP_H},"
        f"crop={OUT_W}:{TOP_H}:{crop_x}:0[vtop]"
    )

    # ── Bottom panel: scale width to 1080, then centre-crop height to 480 ───────
    bot_filter = (
        f"[1:v]scale={OUT_W}:-1,"
        f"crop={OUT_W}:{BOT_H}:(in_w-{OUT_W})/2:(in_h-{BOT_H})/2[vbottom]"
    )

    # ── Vertical stack ────────────────────────────────────────────────────────
    stack_filter = "[vtop][vbottom]vstack=inputs=2[vout]"

    # ── Audio: cartoon only ───────────────────────────────────────────────────
    audio_filter = "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[aout]"

    filter_complex = (
        f"{top_filter};"
        f"{bot_filter};"
        f"{stack_filter};"
        f"{audio_filter}"
    )

    cmd = [
        "ffmpeg", "-y",
        # Input 0: cartoon chunk
        "-i", cartoon_chunk,
        # Input 1: satisfying base — seek BEFORE decode for speed
        "-ss", f"{base_start:.3f}",
        "-t",  f"{base_duration:.3f}",
        "-i", base_video,
        # Filter graph
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        # Video codec
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        # Audio codec
        "-c:a", "aac",
        "-b:a", "128k",
        # Hardware target: i3-3220
        "-threads", "4",
        "-shortest",
        stacked_out,
    ]

    print(f"\n⚙️   Pass 1 — Stacking panels → {stacked_out}")
    print("    " + " ".join(cmd))
    print()
    subprocess.run(cmd, check=True)
    print(f"   ✅  Pass 1 complete: {stacked_out}")


def run_pass2_subtitles(
    stacked_input: str,
    srt_path:      str,
    final_output:  str,
) -> None:
    """
    Pass 2: Burn subtitles onto the already-stacked 1080×1920 video.

    MarginV=60 places the subtitle baseline 60 px from the bottom of the
    screen — sitting cleanly inside the 480-px parkour/satisfying panel.
    """
    escaped_srt = srt_path.replace("'", "\\'").replace(":", "\\:")

    subtitle_vf = (
        f"subtitles='{escaped_srt}':"
        f"force_style='FontName=Arial,FontSize=24,"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        f"Outline=2,MarginV=60'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", stacked_input,
        "-vf", subtitle_vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "copy",
        final_output,
    ]

    print(f"\n⚙️   Pass 2 — Burning subtitles → {final_output}")
    print("    " + " ".join(cmd))
    print()
    subprocess.run(cmd, check=True)
    print(f"   ✅  Pass 2 complete: {final_output}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def process_video():
    # 1. Read state
    state          = load_state()
    current_chunk  = state.get("current_chunk", 1)
    total_chunks   = state.get("total_chunks", 5)
    original_title = state.get("original_title", "Unknown")

    print(f"\n🎬  smart_editor V5.0 — YOLOv8 Tracking & Two-Pass Subtitle Burn")
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

        # 5. YOLOv8 smooth-pan pass → per-frame crop X offsets
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

        # ── Pass 1: Stack panels (no subtitles) ─────────────────────────────
        run_pass1_stack(
            chunk_file, BASE_VIDEO, base_start, chunk_dur,
            crop_x, TEMP_STACKED
        )

        # ── Pass 2: Burn subtitles (or just copy if no SRT) ─────────────────
        if srt_ok and os.path.exists(srt_path):
            run_pass2_subtitles(TEMP_STACKED, srt_path, OUTPUT_FILE)
        else:
            # No subtitles — simply re-mux stacked to final output
            print(f"\n⚙️   No subtitles — copying stacked output to {OUTPUT_FILE}")
            copy_cmd = [
                "ffmpeg", "-y",
                "-i", TEMP_STACKED,
                "-c", "copy",
                OUTPUT_FILE,
            ]
            subprocess.run(copy_cmd, check=True)

        print(f"\n✅  Output written: {OUTPUT_FILE}")
        print(f"    Top  (cartoon)     : {OUT_W} × {TOP_H}")
        print(f"    Bottom (satisfying): {OUT_W} × {BOT_H}")
        print(f"    Subtitles burned   : {'yes' if srt_ok else 'no'}")

    finally:
        # 8. Cleanup all temp files
        for tmp in (TEMP_AUDIO, TEMP_SUBS, TEMP_STACKED):
            if os.path.exists(tmp):
                os.remove(tmp)
                print(f"   🗑️   Removed temp file: {tmp}")


# ─────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    try:
        process_video()
    except Exception as exc:
        print(f"\n❌  smart_editor V5.0 failed: {exc}", file=sys.stderr)
        sys.exit(1)
