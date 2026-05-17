#!/usr/bin/env python3
"""
hook_generator.py — Visual Teaser + Edge TTS Hook Prepender

Generates a 2.5-second "hook" clip that is prepended to the start of
every processed chunk.  The hook consists of:

  • A TTS voice-over (en-GB-EthanNeural, Microsoft premium British narrator) with a
    randomly selected hook phrase (loaded from hooks.txt or built-in fallback).
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


# ── Local hooks.txt loader ─────────────────────────────────────────────────────
# Reads hook phrases from hooks.txt in the project root directory.
# Falls back to the built-in list if the file is missing or empty.

HOOKS_FILE = "hooks.txt"

_FALLBACK_HOOK_LINES = [
    "Wait for the end!",
    "You won't believe what happens next!",
    "Bro is actually cooked.",
    "Watch until the very end!",
    "This part is INSANE — stay tuned!",
    "You need to see how this ends!",
    "The ending will blow your mind!",
    "Don't skip — the best part is at the end!",
]


def _load_hook_lines() -> list[str]:
    """Load hook phrases from HOOKS_FILE; fall back to built-in list."""
    hooks_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), HOOKS_FILE)
    try:
        with open(hooks_path, "r", encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        if lines:
            print(f"   📄  Loaded {len(lines)} hook(s) from {HOOKS_FILE}")
            return lines
        else:
            print(f"   ⚠️   {HOOKS_FILE} is empty — using built-in fallback hooks.")
    except FileNotFoundError:
        print(f"   ⚠️   {HOOKS_FILE} not found — using built-in fallback hooks.")
    return _FALLBACK_HOOK_LINES


# Voice to use — en-GB-EthanNeural is Microsoft's premium British male narrator
TTS_VOICE = "en-GB-LibbyNeural"

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
    Generate TTS audio using en-GB-EthanNeural (Microsoft premium British narrator)
    via edge_tts.

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

    Audio Fix: Re-encode the TTS audio to AAC at a normalized 48 kHz stereo
    baseline so it is packet-compatible with the main chunk's audio when the
    two clips are later joined by the concat filter.  We still use -shortest
    here only to align the silent video to the TTS duration — this is safe
    because the teaser visual has no useful audio anyway.
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
        "-ar", "48000",             # normalize to 48 kHz
        "-ac", "2",                 # normalize to stereo
        "-shortest",                # truncate silent visual to TTS length
        "-threads", "4",
        hook_out,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Hook A+V merged → {hook_out}")


def _concat_hook_and_video(hook_clip: str, main_video: str, output_video: str) -> None:
    """
    Concatenate hook_clip (2.5 s) followed by main_video.

    Audio Fix: Both audio streams are resampled to a common 48 kHz / stereo /
    fltp descriptor *before* reaching the concat node.  Without this step the
    FFmpeg concat filter silently terminates the mixed audio output whenever
    the two input streams differ in sample-rate, channel-count, or sample-fmt
    — which was causing the audio to drop to absolute silence at ~15 seconds.

    The `aresample=48000` + `aformat` pads guarantee that both audio segments
    are packet-compatible and that the output audio stream is continuous for
    the full container duration with no early EOF.
    """
    # Resample both audio inputs to identical specs before concat.
    # [0:a] = hook clip (already 48kHz/stereo from _merge_audio_visual)
    # [1:a] = main raw chunk (unknown rate — normalize it here)
    filter_complex = (
        "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a0];"
        "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a1];"
        "[0:v][a0][1:v][a1]concat=n=2:v=1:a=1[vout][aout]"
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
        "-ar", "48000",
        "-ac", "2",
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
    hook_lines   = _load_hook_lines()
    hook_line    = random.choice(hook_lines)
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
