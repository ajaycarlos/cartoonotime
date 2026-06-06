#!/usr/bin/env python3
"""
hook_generator.py — ElevenLabs Voice-Only Hook Compositor  [V7.8]

Generates a hook clip that is prepended to the start of every processed chunk.
The hook consists of:

  • A phonetically-modulated ElevenLabs voice-over (Adam, eleven_turbo_v2_5)
    with the custom hook_text extracted from state.json → chunk_metadata.
  • A silent teaser clip extracted from near the END of the video — so
    the viewer gets a flash of the climax before seeing it in context.

SFX and amix removed (V7.8 cost-optimisation): temp_hook_audio.aac is now
the raw re-encoded TTS voice track with no mixing stage.

Pipeline
--------
    apply_hook(input_video, output_video, chunk_index)
        └── load_state() → extract hook_text for chunk_index
        └── generate_elevenlabs_voice(hook_text)  → temp_hook_voice.mp3
        └── _encode_voice_only()                  → temp_hook_audio.aac
        └── ffmpeg extract teaser                 → temp_teaser_visual.mp4
        └── ffmpeg merge A+V                      → temp_hook_final.mp4
        └── ffmpeg concat demuxer                 → output_video

Security:
  • ELEVENLABS_API_KEY is NEVER hard-coded; read from env / .env file only.
  • No user audio data is persisted beyond temporary files that are deleted
    immediately after mixing.

All temp files are deleted on exit (success or failure).
"""

import hashlib
import json
import os
import subprocess
import sys

from dotenv import load_dotenv
load_dotenv()

# ── ElevenLabs client (V7.8 — native SDK) ─────────────────────────────────────
try:
    from elevenlabs.client import ElevenLabs
    from elevenlabs import VoiceSettings
except ImportError:
    print(
        "❌  elevenlabs SDK is not installed.\n"
        "    Run:  pip install elevenlabs",
        file=sys.stderr,
    )
    sys.exit(1)


# ═════════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════════
STATE_FILE = "state.json"

# ElevenLabs voice configuration — newer turbo v2.5 model
ELEVENLABS_MODEL    = "eleven_turbo_v2_5"

# Duration of the teaser visual clip (seconds)
HOOK_DURATION = 2.5

# How far from the END of the video to start the teaser clip (seconds)
TEASER_START_OFFSET = 5.0   # start of teaser = duration - this
TEASER_END_OFFSET   = 2.5   # end of teaser   = duration - this (unused directly)


# ═════════════════════════════════════════════════════════════════════════════
# State Reader
# ═════════════════════════════════════════════════════════════════════════════
def load_state() -> dict:
    """Load state.json and return the parsed dict."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"   ⚠️   Could not read {STATE_FILE}: {exc}", file=sys.stderr)
        return {}


def get_chunk_hook_data(chunk_index: int) -> str:
    """
    Extract hook_text for the given chunk_index from state.json.

    Falls back to a safe default if the field is absent.
    """
    state = load_state()
    chunk_meta = state.get("chunk_metadata", {}).get(str(chunk_index), {})

    hook_text = chunk_meta.get("hook_text", "").strip()

    # Safe fallback (non-fatal)
    if not hook_text:
        hook_text = "Wwwait, you actually need to SEE this ending!"
        print(f"   ⚠️   No hook_text in state.json for chunk {chunk_index} — using fallback.",
              file=sys.stderr)

    return hook_text


# ═════════════════════════════════════════════════════════════════════════════
# ElevenLabs Voice Synthesis
# ═════════════════════════════════════════════════════════════════════════════
def _get_elevenlabs_client() -> "ElevenLabs":
    """
    Authenticate securely using ELEVENLABS_API_KEY from environment.
    Never hard-codes credentials.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError(
            "ELEVENLABS_API_KEY is not set.\n"
            "Export it:  export ELEVENLABS_API_KEY='your_key'\n"
            "Or add it to your .env file."
        )
    return ElevenLabs(api_key=api_key)


def generate_elevenlabs_voice(hook_text: str, output_path: str) -> None:
    """
    Synthesize phonetically-modulated voice using ElevenLabs Adam
    (eleven_turbo_v2_5 — cost-optimised V7.8 model).

    Args:
        hook_text:    The phonetic vocal narration line from state.json.
        output_path:  Where to write the .mp3 file (e.g. temp_hook_voice.mp3).
    """
    print(f"   🎤  ElevenLabs [Adam / eleven_turbo_v2_5]: \"{hook_text}\"")

    try:
        client = _get_elevenlabs_client()

        # Fetch all voices currently assigned or available to the active API key
        available_voices = client.voices.get_all()

        # Default fallback to a standard hash if the lookup entirely fails
        chosen_voice_id = "pNInz6obpgDQGcFmaJgB"

        # Iterate through the account's active library to match by name string
        for voice in available_voices.voices:
            if voice.name and voice.name.lower() == "adam":
                chosen_voice_id = voice.voice_id
                print(f"   🎯 Found active voice ID for Adam: {chosen_voice_id}")
                break

        # client.text_to_speech.convert() returns an iterator of audio bytes chunks
        audio_iterator = client.text_to_speech.convert(
            voice_id   = chosen_voice_id,
            text       = hook_text,
            model_id   = ELEVENLABS_MODEL,
            voice_settings=VoiceSettings(
                stability=0.35,
                similarity_boost=0.85,
                style=0.45,
            ),
        )

        with open(output_path, "wb") as out_fh:
            for chunk in audio_iterator:
                if chunk:
                    out_fh.write(chunk)

    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"ElevenLabs voice synthesis failed (possible API timeout or network error): {exc}"
        ) from exc

    print(f"   ✅  Voice audio → {output_path}")


# ═════════════════════════════════════════════════════════════════════════════
# NOTE: generate_sfx() and mix_voice_and_sfx() removed in V7.8
# cost-optimisation pass.  The raw TTS voice is re-encoded directly to
# temp_hook_audio.aac via _encode_voice_only() — no SFX, no amix.
# ═════════════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════════════
# FFmpeg Helpers — Teaser Visual & Concat
# ═════════════════════════════════════════════════════════════════════════════
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


def _extract_teaser_visual(input_video: str, teaser_path: str, clip_duration: float) -> None:
    """
    Cut a clip_duration-second silent clip from near the END of input_video.

    Start = duration - TEASER_START_OFFSET
    clip_duration is passed in dynamically from the actual audio length.
    (clamped so start >= 0)
    """
    duration  = _get_duration(input_video)
    start_sec = max(0.0, duration - TEASER_START_OFFSET)

    print(f"   ✂️   Teaser visual: {start_sec:.2f}s → {start_sec + clip_duration:.2f}s  (of {duration:.2f}s, audio-matched)")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec),
        "-i",  input_video,
        "-t",  str(clip_duration),
        "-an",                      # strip original audio — SFX/voice will replace it
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-threads", "4",
        teaser_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Teaser visual → {teaser_path}")


def _merge_audio_visual(teaser_video: str, mixed_audio: str, hook_out: str) -> None:
    """
    Merge teaser_video (silent) + the pre-mixed studio master audio.

    Audio is normalized to 48 kHz stereo AAC for concat compatibility.
    The teaser visual was already cut to match the audio duration exactly
    (dynamic duration from ffprobe), so -shortest is not needed here.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", teaser_video,
        "-i", mixed_audio,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-threads", "4",
        hook_out,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Hook A+V merged → {hook_out}")


def _concat_hook_and_video(hook_clip: str, main_video: str, output_video: str) -> None:
    """
    Concatenate hook_clip (2.5 s) followed by main_video.

    Both audio streams are resampled to 48 kHz / stereo / fltp before the
    concat node to ensure continuous audio throughout the full container.
    """
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


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════
def apply_hook(input_video: str, output_video: str, chunk_index: int = 1) -> None:
    """
    Prepend an ElevenLabs-voiced hook to input_video and write the result to
    output_video.  SFX and audio mixing removed in V7.8 cost-optimisation pass.

    Steps
    -----
    1. Extract hook_text for chunk_index from state.json.
    2. Synthesize ElevenLabs Adam voice (temp_hook_voice.mp3).
    3. Re-encode voice to normalized AAC baseline (temp_hook_audio.aac).
    4. Extract silent teaser visual from end of source (temp_teaser_visual.mp4).
    5. Merge teaser visual + voice audio (temp_hook_final.mp4).
    6. Concatenate hook + FULL UNCUT raw chunk → output_video.
    7. Delete all temporary intermediate files.

    Args:
        input_video:  Path to the raw source chunk (e.g. queue/chunk_1.mp4).
        output_video: Destination path for the hooked video.
        chunk_index:  The 1-based chunk number — used to look up state.json.
    """
    # Temporary file names
    temp_voice  = "temp_hook_voice.mp3"
    temp_teaser = "temp_teaser_visual.mp4"
    temp_hook   = "temp_hook_final.mp4"

    # All temps that must be cleaned up (cached_audio EXCLUDED)
    all_temps = (temp_voice, temp_teaser, temp_hook)

    print(f"\n🪝  Hook Generator [V7.8] — prepending voice hook to: {input_video}")

    # Step 1 — Resolve phonetic hook text from state.json
    hook_text = get_chunk_hook_data(chunk_index)
    print(f"    Hook text : \"{hook_text}\"")

    # Hash the hook text to create a content-aware cache filename
    text_hash = hashlib.md5(hook_text.encode("utf-8")).hexdigest()
    # Cache path based on hash of hook text
    cached_audio = os.path.join(os.path.dirname(input_video), f"hook_{text_hash}.aac")

    try:
        # Step 2 & 3 — Cache check (Skip API if found)
        if os.path.exists(cached_audio):
            print("💾 Cached hook audio found. Skipping API call to save credits.")
        else:
            # Step 2 — ElevenLabs voice synthesis (Adam, eleven_turbo_v2_5)
            generate_elevenlabs_voice(hook_text, temp_voice)

            # Step 3 — Re-encode voice to normalized AAC (no SFX mixing)
            print("   ℹ️   Encoding voice-only audio (SFX removed in V7.8).")
            _encode_voice_only(temp_voice, cached_audio)

        # Step 4 — Extract silent teaser visual from end of chunk,
        #           sized to match the actual audio duration exactly.
        audio_duration = _get_duration(cached_audio)
        print(f"   🕐  Dynamic audio duration detected: {audio_duration:.3f}s")
        _extract_teaser_visual(input_video, temp_teaser, clip_duration=audio_duration)

        # Step 5 — Merge teaser visual + voice audio
        _merge_audio_visual(temp_teaser, cached_audio, temp_hook)

        # Step 6 — Concatenate hook + FULL UNCUT raw chunk (no -ss on main input)
        _concat_hook_and_video(temp_hook, input_video, output_video)

        print(f"\n✅  Hook applied → {output_video}")

    finally:
        # Step 7 — Clean up all temporary tracks
        for tmp in all_temps:
            if os.path.exists(tmp):
                os.remove(tmp)
                print(f"   🗑️   Removed: {tmp}")


def _encode_voice_only(voice_mp3: str, output_aac: str) -> None:
    """
    Re-encode voice track alone to a normalized AAC baseline when SFX
    generation is unavailable, so the merge step still receives a valid file.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", voice_mp3,
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        output_aac,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Voice-only audio encoded → {output_aac}")


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: python hook_generator.py <input_video> <output_video> [chunk_index]")
        sys.exit(1)
    _chunk_idx = int(sys.argv[3]) if len(sys.argv) == 4 else 1
    apply_hook(sys.argv[1], sys.argv[2], chunk_index=_chunk_idx)
