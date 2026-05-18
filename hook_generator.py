#!/usr/bin/env python3
"""
hook_generator.py — ElevenLabs Native Voice & Cinematic SFX Hook Compositor  [V7.8]

Generates a 2.5-second "hook" clip that is prepended to the start of
every processed chunk.  The hook consists of:

  • A phonetically-modulated ElevenLabs voice-over (Adam, eleven_v3) with the
    custom hook_text extracted from state.json → chunk_metadata.
  • A generative cinematic sound effect (ElevenLabs SFX API) from the custom
    sfx_prompt in state.json, mixed at the ABSOLUTE BEGINNING of the audio track
    at full impact volume, then fading under Adam's opening vocal stutter.
  • A silent teaser clip extracted from near the END of the video — so
    the viewer gets a flash of the climax before seeing it in context.

Pipeline
--------
    apply_hook(input_video, output_video, chunk_index)
        └── load_state() → extract hook_text + sfx_prompt for chunk_index
        └── generate_elevenlabs_voice(hook_text)  → temp_hook_voice.mp3
        └── generate_sfx(sfx_prompt)              → temp_sfx.mp3
        └── mix_voice_and_sfx()                   → temp_hook_audio.mp3
        └── ffmpeg extract teaser                 → temp_teaser_visual.mp4
        └── ffmpeg merge A+V                      → temp_hook_final.mp4
        └── ffmpeg concat demuxer                 → output_video

Security:
  • ELEVENLABS_API_KEY is NEVER hard-coded; read from env / .env file only.
  • No user audio data is persisted beyond temporary files that are deleted
    immediately after mixing.

All temp files are deleted on exit (success or failure).
"""

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

# ElevenLabs voice configuration
ELEVENLABS_MODEL    = "eleven_v3"

# Duration of the teaser visual clip (seconds)
HOOK_DURATION = 2.5

# How far from the END of the video to start the teaser clip (seconds)
TEASER_START_OFFSET = 5.0   # start of teaser = duration - this
TEASER_END_OFFSET   = 2.5   # end of teaser   = duration - this (unused directly)

# SFX mix parameters — SFX is front-loaded at full impact, voice is primary
SFX_VOLUME   = 0.35    # SFX relative volume so it never clips / drowns the vocal
VOICE_VOLUME = 1.0     # Voice stays at full unity gain


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


def get_chunk_hook_data(chunk_index: int) -> tuple[str, str]:
    """
    Extract (hook_text, sfx_prompt) for the given chunk_index from state.json.

    Falls back to safe defaults if the fields are absent.
    """
    state = load_state()
    chunk_meta = state.get("chunk_metadata", {}).get(str(chunk_index), {})

    hook_text  = chunk_meta.get("hook_text", "").strip()
    sfx_prompt = chunk_meta.get("sfx_prompt", "").strip()

    # Safe fallbacks (non-fatal)
    if not hook_text:
        hook_text = "W-W-Wait... you actually need to SEE this ending!"
        print(f"   ⚠️   No hook_text in state.json for chunk {chunk_index} — using fallback.",
              file=sys.stderr)
    if not sfx_prompt:
        sfx_prompt = "Cinematic deep sub-bass drop with a sudden sharp whoosh transition"
        print(f"   ⚠️   No sfx_prompt in state.json for chunk {chunk_index} — using fallback.",
              file=sys.stderr)

    return hook_text, sfx_prompt


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
    Synthesize phonetically-modulated voice using ElevenLabs Adam (eleven_v3).

    Args:
        hook_text:    The phonetic vocal narration line from state.json.
        output_path:  Where to write the .mp3 file (e.g. temp_hook_voice.mp3).
    """
    print(f"   🎤  ElevenLabs [Adam / eleven_v3]: \"{hook_text}\"")

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
# ElevenLabs SFX Synthesis
# ═════════════════════════════════════════════════════════════════════════════
def generate_sfx(sfx_prompt: str, output_path: str) -> bool:
    """
    Synthesize a generative cinematic sound effect via ElevenLabs SFX API.

    Args:
        sfx_prompt:  Audio design command from state.json.
        output_path: Where to write the .mp3 file (e.g. temp_sfx.mp3).

    Returns:
        True on success, False if the SFX API call fails (non-fatal).
    """
    print(f"   🔊  ElevenLabs SFX: \"{sfx_prompt}\"")

    try:
        client = _get_elevenlabs_client()

        sfx_iterator = client.text_to_sound_effects.convert(text=sfx_prompt)

        with open(output_path, "wb") as out_fh:
            for chunk in sfx_iterator:
                if chunk:
                    out_fh.write(chunk)

        print(f"   ✅  SFX audio → {output_path}")
        return True

    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠️   SFX generation failed: {exc} — hook will use voice-only.",
              file=sys.stderr)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# FFmpeg Audio Compositor — Voice + SFX Intro Mix
# ═════════════════════════════════════════════════════════════════════════════
def mix_voice_and_sfx(voice_path: str, sfx_path: str, mixed_output: str) -> None:
    """
    Mix the ElevenLabs vocal track with the cinematic SFX intro using FFmpeg.

    CRITICAL TIMING DESIGN:
      • The SFX is placed at 0.0 seconds — the absolute beginning of the audio
        track — so it fires the moment Adam's opening stutter begins, delivering
        a sharp attention-snapping cinematic impact.
      • SFX volume is scaled to SFX_VOLUME (0.35) so it creates a powerful hit
        but never drowns or clips the vocal narration.
      • Voice plays at unity gain (1.0).
      • amix=inputs=2:duration=longest keeps the full voice length even if SFX
        ends before the voice track does.

    Args:
        voice_path:   Path to the ElevenLabs voice .mp3.
        sfx_path:     Path to the generated SFX .mp3.
        mixed_output: Path for the final mixed .mp3.
    """
    # FFmpeg filter graph:
    #   [0:a] = voice at unity gain
    #   [1:a] = sfx scaled down to SFX_VOLUME, starting at t=0.0
    #   amix combines both streams; duration=longest keeps full voice
    filter_complex = (
        f"[0:a]volume={VOICE_VOLUME}[voice];"
        f"[1:a]volume={SFX_VOLUME}[sfx];"
        "[voice][sfx]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", voice_path,
        "-i", sfx_path,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        mixed_output,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"   ✅  Voice + SFX mixed → {mixed_output}")


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
    Prepend a cinematic ElevenLabs-voiced + SFX-mixed 2.5-second hook to
    input_video and write the result to output_video.

    Steps
    -----
    1. Extract hook_text + sfx_prompt for chunk_index from state.json.
    2. Synthesize ElevenLabs Adam voice (temp_hook_voice.mp3).
    3. Synthesize generative SFX (temp_sfx.mp3).
    4. Mix voice + SFX with front-loaded SFX impact (temp_hook_audio.mp3).
    5. Extract silent teaser visual from end of source (temp_teaser_visual.mp4).
    6. Merge teaser visual + mixed audio (temp_hook_final.mp4).
    7. Concatenate hook + original video → output_video.
    8. Delete all single unmixed and temporary intermediate tracks.

    Args:
        input_video:  Path to the raw source chunk (e.g. queue/chunk_1.mp4).
        output_video: Destination path for the hooked video.
        chunk_index:  The 1-based chunk number — used to look up state.json.
    """
    # Temporary file names
    temp_voice   = "temp_hook_voice.mp3"
    temp_sfx     = "temp_sfx.mp3"
    temp_audio   = "temp_hook_audio.aac"   # final mixed master
    temp_teaser  = "temp_teaser_visual.mp4"
    temp_hook    = "temp_hook_final.mp4"

    # All temps that must be cleaned up (including unmixed singles)
    all_temps = (temp_voice, temp_sfx, temp_audio, temp_teaser, temp_hook)

    print(f"\n🪝  Hook Generator [V7.8] — prepending cinematic hook to: {input_video}")

    # Step 1 — Resolve phonetic hook data from state.json
    hook_text, sfx_prompt = get_chunk_hook_data(chunk_index)
    print(f"    Hook text : \"{hook_text}\"")
    print(f"    SFX prompt: \"{sfx_prompt}\"")

    try:
        # Step 2 — ElevenLabs voice synthesis (Adam, eleven_v3)
        generate_elevenlabs_voice(hook_text, temp_voice)

        # Step 3 — Generative SFX synthesis
        sfx_available = generate_sfx(sfx_prompt, temp_sfx)

        # Step 4 — FFmpeg audio mix: SFX at t=0.0 under vocal stutter
        if sfx_available and os.path.exists(temp_sfx):
            mix_voice_and_sfx(temp_voice, temp_sfx, temp_audio)
        else:
            # SFX generation failed — use voice-only, still properly encoded
            print("   ℹ️   Using voice-only audio (SFX skipped).")
            _encode_voice_only(temp_voice, temp_audio)

        # Step 5 — Extract silent teaser visual from end of chunk,
        #           sized to match the actual audio duration exactly.
        audio_duration = _get_duration(temp_audio)
        print(f"   🕐  Dynamic audio duration detected: {audio_duration:.3f}s")
        _extract_teaser_visual(input_video, temp_teaser, clip_duration=audio_duration)

        # Step 6 — Merge teaser visual + studio master audio
        _merge_audio_visual(temp_teaser, temp_audio, temp_hook)

        # Step 7 — Concatenate hook + original main video
        _concat_hook_and_video(temp_hook, input_video, output_video)

        print(f"\n✅  Hook applied → {output_video}")

    finally:
        # Step 8 — Clean up ALL temporary tracks (unmixed singles + intermediates)
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
