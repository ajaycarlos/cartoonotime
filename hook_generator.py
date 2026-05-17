#!/usr/bin/env python3
"""
hook_generator.py — Visual Teaser + Edge TTS Hook Prepender

Generates a 2.5-second "hook" clip that is prepended to the start of
every processed chunk.  The hook consists of:

  • A TTS voice-over (en-US-ChristopherNeural, high-energy male) with a
    randomly selected brainrot line.
  • A silent teaser clip extracted from near the END of the video — so
    the viewer gets a flash of the climax before seeing it in context.
  • The voice-over replaces / truncates the teaser's original audio so
    the final hook duration matches the TTS exactly.

Pipeline
--------
    apply_hook(input_video, output_video)
        └── generate_tts_audio(text)  → temp_hook_audio.mp3
        └── ffmpeg extract teaser     → temp_teaser_visual.mp4
        └── ffmpeg merge A+V          → temp_hook_final.mp4
        └── ffmpeg concat demuxer     → output_video

All temp files are deleted on exit (success or failure).
"""

import asyncio
import os
import random
import subprocess
import sys
import tempfile

import edge_tts


# ── Brainrot hook lines ────────────────────────────────────────────────────────
HOOK_LINES = [
    "Wait for the end!",
    "You won't believe what happens next!",
    "Bro is actually cooked.",
    "Watch until the very end!",
    "This part is INSANE — stay tuned!",
    "You need to see how this ends!",
    "The ending will blow your mind!",
    "Don't skip — the best part is at the end!",
]

# Voice to use — en-US-ChristopherNeural is a high-energy, clear male voice
TTS_VOICE = "en-US-ChristopherNeural"

# Duration of the teaser visual clip (seconds)
HOOK_DURATION = 2.5

# How far from the END of the video to start the teaser clip (seconds)
# e.g. if video is 60 s long, teaser starts at 60 - 5.0 = 55.0 s
TEASER_END_OFFSET   = 2.5   # end of teaser = duration - this
TEASER_START_OFFSET = 5.0   # start of teaser = duration - this


# ── TTS generation ─────────────────────────────────────────────────────────────
async def _tts_to_file(text: str, output_path: str) -> None:
    """Async inner function: call edge_tts and save to output_path."""
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(output_path)


def generate_tts_audio(text: str, output_audio_path: str) -> None:
    """
    Generate TTS audio using en-US-ChristopherNeural via edge_tts.

    Args:
        text:              The line to speak.
        output_audio_path: Where to write the .mp3 file.
    """
    print(f"   🎤  TTS [{TTS_VOICE}]: \"{text}\"")
    asyncio.run(_tts_to_file(text, output_audio_path))
    print(f"   ✅  TTS audio → {output_audio_path}")


# ── ffprobe helper (re-exported from smart_editor pattern) ─────────────────────
def _get_duration(path: str) -> float:
    """Return the duration of a media file in seconds via ffprobe."""
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


# ── FFmpeg helpers ─────────────────────────────────────────────────────────────
def _extract_teaser_visual(input_video: str, teaser_path: str) -> None:
    """
    Cut a HOOK_DURATION-second silent clip from near the END of input_video.

    Start = duration - TEASER_START_OFFSET
    End   = duration - TEASER_END_OFFSET
    (clamped so start >= 0)
    """
    duration   = _get_duration(input_video)
    start_sec  = max(0.0, duration - TEASER_START_OFFSET)
    clip_len   = HOOK_DURATION  # keep exactly this many seconds

    print(f"   ✂️   Teaser visual: {start_sec:.2f}s → {start_sec + clip_len:.2f}s  (of {duration:.2f}s)")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i",  input_video,
        "-t",  str(clip_len),
        "-an",                      # strip original audio
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-threads", "4",
        teaser_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Teaser visual → {teaser_path}")


def _merge_audio_visual(teaser_video: str, tts_audio: str, hook_out: str) -> None:
    """
    Merge teaser_video (silent) + tts_audio.
    The shortest stream wins (-shortest) so the video is truncated to
    exactly match the TTS audio duration.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", teaser_video,
        "-i", tts_audio,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",                # truncate video to audio length
        "-threads", "4",
        hook_out,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Hook A+V merged → {hook_out}")


def _concat_hook_and_video(hook_clip: str, main_video: str, output_video: str) -> None:
    """
    Concatenate hook_clip (2.5 s) followed by main_video using the
    FFmpeg concat demuxer.  A temporary concat list file is used and
    deleted immediately after.

    Both clips must share the same resolution, frame-rate, and codec;
    since hook_clip is re-encoded in _merge_audio_visual this is safe
    as long as the caller passes the original chunk (same encoding
    settings from smart_editor Pass 1 / raw chunk).

    We use the filter_complex concat filter instead of the demuxer to
    handle potential codec/timebase differences gracefully.
    """
    # Use filter_complex concat so we handle any codec/timebase mismatch
    filter_complex = (
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", hook_clip,
        "-i", main_video,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-threads", "4",
        output_video,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Concat complete → {output_video}")


# ── Public API ─────────────────────────────────────────────────────────────────
def apply_hook(input_video: str, output_video: str) -> None:
    """
    Prepend a 2.5-second AI-voiced visual teaser to input_video and
    write the result to output_video.

    Steps
    -----
    1. Pick a random brainrot hook line.
    2. Generate TTS audio (temp_hook_audio.mp3).
    3. Extract a silent teaser clip from near the end (temp_teaser_visual.mp4).
    4. Merge teaser + TTS audio (temp_hook_final.mp4).
    5. Concatenate hook + original video → output_video.
    6. Delete all temp files.

    Args:
        input_video:  Path to the raw source chunk (e.g. queue/chunk_1.mp4).
        output_video: Destination path for the hooked video.
    """
    hook_line    = random.choice(HOOK_LINES)
    temp_audio   = "temp_hook_audio.mp3"
    temp_teaser  = "temp_teaser_visual.mp4"
    temp_hook    = "temp_hook_final.mp4"

    print(f"\n🪝  Hook Generator — prepending teaser to: {input_video}")
    print(f"    Hook line : \"{hook_line}\"")

    try:
        # Step 1 — TTS
        generate_tts_audio(hook_line, temp_audio)

        # Step 2 — Extract silent teaser visual
        _extract_teaser_visual(input_video, temp_teaser)

        # Step 3 — Merge teaser visual + TTS audio
        _merge_audio_visual(temp_teaser, temp_audio, temp_hook)

        # Step 4 — Prepend hook to the original video
        _concat_hook_and_video(temp_hook, input_video, output_video)

        print(f"\n✅  Hook applied → {output_video}")

    finally:
        # Always clean up temp files
        for tmp in (temp_audio, temp_teaser, temp_hook):
            if os.path.exists(tmp):
                os.remove(tmp)
                print(f"   🗑️   Removed: {tmp}")


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python hook_generator.py <input_video> <output_video>")
        sys.exit(1)
    apply_hook(sys.argv[1], sys.argv[2])
