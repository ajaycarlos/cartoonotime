#!/usr/bin/env python3
"""
smart_editor.py — The Editor  (V7.1 + Hook Generator + Widescreen Hybrid Zoom)
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

from brainrot_subs import generate_brainrot_ass
from hook_generator import apply_hook


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
TEMP_HOOKED       = "temp_hooked.mp4"
TEMP_AUDIO        = "temp_audio.wav"
TEMP_SUBS         = "temp_subs.srt"
TEMP_ASS          = "temp_brainrot.ass"
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
        "-af", "aresample=async=1",
        "-muxdelay", "0",
        "-muxpreload", "0",
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
    cartoon_chunk:       str,
    stacked_out:         str,
    use_satisfying_base: bool,
    base_video:          str = "",
) -> None:
    """
    Pass 1 — Widescreen Hybrid Zoom + Blur+Fit composite → stacked_out.

    Path A (use_satisfying_base=True):
      • Container : 1080×1440 (top) + 1080×480 (satisfying bottom) = 1080×1920
      • Background: blurred, fills 1080×1440
      • Foreground: 5% left/right crop → scale to 1080 wide → centre overlay
      • Bottom     : satisfying video scaled/cropped to 1080×480, vstack'd below

    Path B (use_satisfying_base=False):
      • Container : 1080×1920 full canvas
      • Background: blurred, fills 1080×1920
      • Foreground: same 5% left/right crop → scale to 1080 → centre overlay
      • No satisfying video imported or overlaid.

    NO subtitle filter is applied here.
    """
    if use_satisfying_base:
        # ── Path A: 75/25 split (1080×1440 top + 1080×480 satisfying base) ──
        filter_complex = (
            # Blurred background fills the 1080×1440 top container
            "[0:v]scale=1080:1440:force_original_aspect_ratio=increase,"
            "crop=1080:1440,boxblur=20:5[blur_bg];"
            # Hybrid zoom: crop 5% off each side, then scale to 1080 wide
            "[0:v]crop=iw*0.9:ih:iw*0.05:0,scale=1080:-1[fg];"
            # Composite fg centred on the 1080×1440 blurred background
            "[blur_bg][fg]overlay=(W-w)/2:(H-h)/2[top_half];"
            # Satisfying base: scale then centre-crop to 1080×480
            "[1:v]scale=1080:-1,crop=1080:480:(in_w-1080)/2:(in_h-480)/2[bottom_half];"
            # Vertical stack → full 1080×1920 output
            "[top_half][bottom_half]vstack=inputs=2[stacked]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", cartoon_chunk,
            "-stream_loop", "-1",    # loop satisfying base so it never runs short
            "-i", base_video,
            "-filter_complex", filter_complex,
            "-map", "[stacked]",
            "-map", "0:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-threads", "4",
            # NOTE: -shortest intentionally REMOVED here.
            # Output duration is driven by stream 0 (cartoon chunk).
            # -stream_loop -1 on the satisfying base guarantees it never
            # exhausts before the cartoon audio does, so the audio track
            # stays continuous for the full container duration.
            stacked_out,
        ]
        print(f"\n⚙️   Pass 1 — Path A (Split + Satisfying Base) → {stacked_out}")
        print("    Base video : " + base_video)
    else:
        # ── Path B: Full 1080×1920 canvas — no satisfying base ────────────────
        filter_complex = (
            # Blurred background fills the full 1080×1920 canvas
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=20:5[blur_bg];"
            # Hybrid zoom: crop 5% off each side, then scale to 1080 wide
            "[0:v]crop=iw*0.9:ih:iw*0.05:0,scale=1080:-1[fg];"
            # Composite fg centred on the full 1080×1920 blurred background
            "[blur_bg][fg]overlay=(W-w)/2:(H-h)/2[stacked]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", cartoon_chunk,
            "-filter_complex", filter_complex,
            "-map", "[stacked]",
            "-map", "0:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-threads", "4",
            stacked_out,
        ]
        print(f"\n⚙️   Pass 1 — Path B (Full Canvas, No Satisfying Base) → {stacked_out}")

    print()
    subprocess.run(cmd, check=True)
    print(f"   ✅  Pass 1 complete: {stacked_out}")


def run_pass2_subtitles(
    stacked_input: str,
    ass_path:      str,
    final_output:  str,
) -> None:
    """
    Pass 2: Burn subtitles onto the already-stacked 1080×1920 video.
    """
    subtitle_vf = (
        f"ass={ass_path}"
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

    print(f"\n🎬  smart_editor V7.1 — Widescreen Hybrid Zoom + Dynamic Layout")
    print(f"    Processing chunk {current_chunk}/{total_chunks}")
    print(f"    Title: {original_title!r}")

    # 2. Read layout choice from state (prompted in main.py)
    use_satisfying_base = state.get("use_satisfying_base", True)

    if use_satisfying_base:
        print(f"    Split: {TOP_H}px top (75%) / {BOT_H}px bottom (25%)")
    else:
        print(f"    Canvas: {OUT_W}×{OUT_H} full vertical (no satisfying base)")

    # 3. Locate cartoon chunk
    chunk_file = os.path.join(QUEUE_DIR, f"chunk_{current_chunk}.mp4")
    if not os.path.exists(chunk_file):
        raise FileNotFoundError(
            f"Chunk not found: {chunk_file}\n"
            "Run interactive_fetcher.py or verify state.json."
        )

    # 4. Select rotating satisfying base only when needed
    if use_satisfying_base:
        selected_base, state = select_satisfying_base(state)
        save_state(state)
    else:
        selected_base = ""
        save_state(state)   # still persist any other state changes

    srt_path = TEMP_SUBS
    ass_path = TEMP_ASS
    try:
        # ── Step 5: Apply Hook ──────────────────────────────────────────────────
        # Prepend a 2.5-second AI-voiced visual teaser to the raw chunk.
        # All downstream steps (Whisper, Pass 1, Pass 2) operate on this
        # hooked video so the TTS voice-over gets transcribed & styled too.
        apply_hook(chunk_file, TEMP_HOOKED, chunk_index=current_chunk)
        working_file = TEMP_HOOKED   # everything below uses this

        # ── Step 6: Whisper subtitle generation ────────────────────────────────
        srt_ok = generate_srt(working_file, srt_path)

        if srt_ok and os.path.exists(srt_path):
            generate_brainrot_ass(srt_path, ass_path)

        # ── Step 7: Pass 1 — Hybrid Zoom + Blur+Fit (no subtitles) ───────────
        run_pass1_stack(
            cartoon_chunk=working_file,
            stacked_out=TEMP_STACKED,
            use_satisfying_base=use_satisfying_base,
            base_video=selected_base,
        )

        # ── Step 8: Pass 2 — Burn subtitles (or copy if no SRT) ───────────────
        if srt_ok and os.path.exists(ass_path):
            run_pass2_subtitles(TEMP_STACKED, ass_path, OUTPUT_FILE)
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
        if use_satisfying_base:
            print(f"    Top  (cartoon)     : {OUT_W} × {TOP_H}  [Hybrid Zoom + Blur+Fit]")
            print(f"    Bottom (satisfying): {OUT_W} × {BOT_H}  [{os.path.basename(selected_base)}]")
        else:
            print(f"    Canvas             : {OUT_W} × {OUT_H}  [Hybrid Zoom + Blur+Fit, full]")
        print(f"    Subtitles burned   : {'yes' if srt_ok else 'no'}")

    finally:
        # 9. Cleanup all temp files (hook generator cleans its own temps)
        for tmp in (TEMP_HOOKED, TEMP_AUDIO, TEMP_SUBS, TEMP_ASS, TEMP_STACKED):
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
        print(f"\n❌  smart_editor V7.1 failed: {exc}", file=sys.stderr)
        sys.exit(1)
