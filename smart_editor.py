#!/usr/bin/env python3
"""
smart_editor.py — The Editor  (V6.0 Complete Overhaul)
Reads chunk_X.mp4 from /queue, applies a Blur+Fit top-panel effect,
and composites it with a rotating bottom satisfying video.

NEW in V6.0:
  • Blur + Fit Top Panel: Replaces AI/YOLO tracking entirely.
    The cartoon chunk is rendered with a blurred full-bleed background
    and a correctly-fitted overlay — no crop logic, no model weights.

  • Rotating Satisfying Base: Instead of a single satisfying_base.mp4,
    the script reads all .mp4 files from the satisfying_base/ directory,
    sorts them alphabetically, and uses modulo math against a persistent
    satisfying_index counter stored in state.json to loop infinitely
    through every video in the folder.

  • Two-Pass FFmpeg Render:
      Pass 1 — filter_complex stacks top + bottom panels into temp_stacked.mp4
               (NO subtitle filter here — avoids libass drop issues).
      Pass 2 — A clean second FFmpeg call burns temp_subs.srt onto the
               stacked 1080×1920 output with force_style and MarginV=200.

  • Safe Cleanup: temp_audio.wav, temp_subs.srt, and temp_stacked.mp4 are
    all removed in the finally block.

75/25 Split (1080 × 1920 YouTube Short):
  • Top  (cartoon)     : 1080 × 1440  (75 % of 1920)
  • Bottom (satisfying): 1080 × 480   (25 % of 1920)

Hardware optimisation: -threads 4 and -preset ultrafast on all FFmpeg
commands.  Designed for i3-3220 / 8 GB RAM — no GPU required.
"""

import os
import sys
import json
import glob
import subprocess


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
STATE_FILE        = "state.json"
QUEUE_DIR         = "queue"
SATISFYING_DIR    = "satisfying_base"
OUTPUT_FILE       = "ready_to_upload.mp4"
TEMP_AUDIO        = "temp_audio.wav"
TEMP_SUBS         = "temp_subs.srt"
TEMP_STACKED      = "temp_stacked.mp4"

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


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


# ─────────────────────────────────────────────
# Rotating satisfying base selector
# ─────────────────────────────────────────────
def select_satisfying_base(state: dict) -> tuple[str, dict]:
    """
    Scan SATISFYING_DIR for .mp4 files (sorted alphabetically).
    Use state['satisfying_index'] % len(bases) to pick the current one.
    Increment the index and return (selected_path, updated_state).
    """
    pattern = os.path.join(SATISFYING_DIR, "*.mp4")
    bases   = sorted(glob.glob(pattern))

    if not bases:
        raise FileNotFoundError(
            f"No .mp4 files found in '{SATISFYING_DIR}/'.\n"
            "Add at least one satisfying video (e.g. 1.mp4, 2.mp4) and retry."
        )

    idx           = state.get("satisfying_index", 0)
    selected_base = bases[idx % len(bases)]

    # Advance index for the next run
    state["satisfying_index"] = idx + 1

    print(f"\n🎞️   Satisfying Base Selector:")
    print(f"    Directory   : {SATISFYING_DIR}/  ({len(bases)} video(s) found)")
    print(f"    Index       : {idx}  →  using [{idx % len(bases)}] '{os.path.basename(selected_base)}'")
    print(f"    Next index  : {state['satisfying_index']}")

    return selected_base, state


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
    stacked_out:   str,
) -> None:
    """
    Pass 1 — Blur + Fit top panel + satisfying bottom panel → vstack.

    Filter graph:
      [0:v] → blurred full-bleed background (top 1080×1440)
      [0:v] → fitted foreground overlay, centred on blur_bg
      [blur_bg][fg] → overlay → [top_half]
      [1:v] → scaled + centre-cropped to 1080×480 → [bottom_half]
      [top_half][bottom_half] → vstack → [stacked]

    Output is written to stacked_out (temp_stacked.mp4).
    NO subtitle filter is applied here.
    """
    filter_complex = (
        # ── Top-half: blurred background ──────────────────────────────────────
        "[0:v]scale=1080:1440:force_original_aspect_ratio=increase,"
        "crop=1080:1440,boxblur=20:5[blur_bg];"
        # ── Top-half: fitted foreground ───────────────────────────────────────
        "[0:v]scale=1080:1440:force_original_aspect_ratio=decrease[fg];"
        # ── Composite ─────────────────────────────────────────────────────────
        "[blur_bg][fg]overlay=(W-w)/2:(H-h)/2[top_half];"
        # ── Bottom-half: satisfying base ─────────────────────────────────────
        f"[1:v]scale=1080:-1,crop=1080:480:(in_w-1080)/2:(in_h-480)/2[bottom_half];"
        # ── Vertical stack ────────────────────────────────────────────────────
        "[top_half][bottom_half]vstack=inputs=2[stacked]"
    )

    # Audio: cartoon chunk only
    cmd = [
        "ffmpeg", "-y",
        # Input 0: cartoon chunk
        "-i", cartoon_chunk,
        # Input 1: selected satisfying base video
        "-i", base_video,
        # Filter graph
        "-filter_complex", filter_complex,
        "-map", "[stacked]",
        "-map", "0:a",
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

    print(f"\n⚙️   Pass 1 — Blur+Fit stack → {stacked_out}")
    print("    Base video : " + base_video)
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

    MarginV=200 places the subtitle baseline 200 px from the bottom of the
    screen — sitting cleanly inside the 480-px satisfying panel.
    """
    subtitle_vf = (
        "subtitles=temp_subs.srt:force_style='"
        "Fontname=Arial,"
        "Fontsize=18,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=3,"
        "Shadow=0,"
        "Alignment=2,"
        "MarginV=200'"
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

    print(f"\n🎬  smart_editor V6.0 — Blur+Fit & Rotating Satisfying Base")
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

    # 3. Select rotating satisfying base & persist incremented index
    selected_base, state = select_satisfying_base(state)
    save_state(state)

    srt_path = TEMP_SUBS
    try:
        # 4. Whisper subtitle generation
        srt_ok = generate_srt(chunk_file, srt_path)

        # 5. Pass 1 — Blur+Fit stack (no subtitles)
        run_pass1_stack(chunk_file, selected_base, TEMP_STACKED)

        # 6. Pass 2 — Burn subtitles (or copy if no SRT)
        if srt_ok and os.path.exists(srt_path):
            run_pass2_subtitles(TEMP_STACKED, srt_path, OUTPUT_FILE)
        else:
            print(f"\n⚙️   No subtitles — copying stacked output to {OUTPUT_FILE}")
            copy_cmd = [
                "ffmpeg", "-y",
                "-i", TEMP_STACKED,
                "-c", "copy",
                OUTPUT_FILE,
            ]
            subprocess.run(copy_cmd, check=True)

        print(f"\n✅  Output written: {OUTPUT_FILE}")
        print(f"    Top  (cartoon)     : {OUT_W} × {TOP_H}  [Blur+Fit]")
        print(f"    Bottom (satisfying): {OUT_W} × {BOT_H}  [{os.path.basename(selected_base)}]")
        print(f"    Subtitles burned   : {'yes' if srt_ok else 'no'}")

    finally:
        # 7. Cleanup all temp files
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
        print(f"\n❌  smart_editor V6.0 failed: {exc}", file=sys.stderr)
        sys.exit(1)
