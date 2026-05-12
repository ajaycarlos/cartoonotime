#!/usr/bin/env python3
"""
brainrot_fetcher.py — The Downloader & Volume Slicer
Searches Internet Archive for colored cartoons (1970–2005),
downloads one, finds the 5 loudest 60-second segments, slices
them into /queue/, writes state.json, then deletes the raw files.

RAM-optimised for i3-3220 / 8 GB RAM — no full MP4 ever loaded
into pydub; a low-bitrate mono WAV is used for analysis only.
"""

import os
import json
import random
import subprocess
import glob
import sys

import requests
from tqdm import tqdm
from internetarchive import search_items, get_item
from pydub import AudioSegment

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
QUEUE_DIR        = "queue"
STATE_FILE       = "state.json"
TEMP_AUDIO_FILE  = "temp_audio.wav"
CHUNK_DURATION_S = 60
CHUNK_DURATION_MS = CHUNK_DURATION_S * 1000
TOP_N_CHUNKS     = 5

# Archive.org search query: coloured cartoons 1970-2005
SEARCH_QUERY = (
    'collection:animationandcartoons AND '
    'date:[1970-01-01 TO 2005-12-31]'
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def ensure_dirs():
    os.makedirs(QUEUE_DIR, exist_ok=True)


def get_random_cartoon() -> str:
    """Return a random Archive.org identifier for a qualifying cartoon."""
    print("\n🔍  Searching Internet Archive for colored cartoons (1970–2005)…")
    results = []
    for i, item in enumerate(search_items(SEARCH_QUERY)):
        results.append(item)
        if i >= 99:          # sample 100, pick 1 at random
            break

    if not results:
        raise RuntimeError("No items returned from Internet Archive search.")

    chosen = random.choice(results)
    identifier = chosen["identifier"]
    print(f"   ✅  Selected: {identifier}")
    return identifier


def download_video(identifier: str) -> tuple[str, str]:
    """
    Download the largest MP4 from the given Archive.org item.
    Returns (local_filename, human_title).
    Uses a tqdm progress bar while streaming.
    """
    print(f"\n📦  Fetching metadata for: {identifier}")
    item = get_item(identifier)

    # Prefer h.264 MP4; fall back to any MP4
    mp4_files = [
        f for f in item.files
        if f["name"].lower().endswith(".mp4")
    ]
    if not mp4_files:
        raise RuntimeError(f"No .mp4 files found for item: {identifier}")

    # Pick the largest file (most content)
    mp4_files.sort(key=lambda x: int(x.get("size", 0)), reverse=True)
    selected = mp4_files[0]["name"]
    file_size = int(mp4_files[0].get("size", 0))

    url = f"https://archive.org/download/{identifier}/{selected}"
    local_path = selected  # save to cwd

    print(f"   ⬇️   Downloading: {selected}  ({file_size / 1_048_576:.1f} MB)")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(local_path, "wb") as f, tqdm(
            total=file_size,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=selected[:40],
            dynamic_ncols=True,
        ) as bar:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))

    title = item.metadata.get("title", identifier)
    print(f"   ✅  Download complete. Title: {title!r}")
    return local_path, title


def extract_audio(video_file: str, audio_file: str = TEMP_AUDIO_FILE) -> str:
    """
    RAM-safe extraction: use FFmpeg subprocess to write a low-bitrate
    mono WAV (22 kHz).  Never loads the MP4 into pydub.
    """
    print(f"\n🎙️   Extracting audio from {video_file} → {audio_file} …")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_file,
            "-vn",                        # drop video stream
            "-acodec", "pcm_s16le",       # 16-bit PCM
            "-ar", "22050",               # 22 kHz — enough for loudness analysis
            "-ac", "1",                   # mono  — halves RAM usage
            audio_file,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    print(f"   ✅  Audio extracted to {audio_file}")
    return audio_file


def find_loudest_chunks(audio_file: str, n: int = TOP_N_CHUNKS) -> list[dict]:
    """
    Load the WAV (already tiny/mono/22 kHz) into pydub.
    Slide a 60-second window and rank by dBFS.
    Returns the top-N loudest windows, sorted chronologically.
    """
    print(f"\n📊  Analysing audio waveform for top-{n} loudest 60-second segments…")
    audio = AudioSegment.from_wav(audio_file)
    total_ms = len(audio)

    segments = []
    for start_ms in range(0, total_ms - CHUNK_DURATION_MS, CHUNK_DURATION_MS):
        segment = audio[start_ms : start_ms + CHUNK_DURATION_MS]
        dbfs = segment.dBFS
        if dbfs != float("-inf"):       # skip silent blocks
            segments.append({"start_time": start_ms / 1000.0, "dbfs": dbfs})

    if not segments:
        raise RuntimeError("No valid (non-silent) segments found in audio.")

    # Top-N by loudness
    segments.sort(key=lambda x: x["dbfs"], reverse=True)
    top = segments[:n]

    # Re-sort chronologically so chunk numbers are in order
    top.sort(key=lambda x: x["start_time"])

    for i, seg in enumerate(top, 1):
        print(f"   Segment {i}: start={seg['start_time']:.1f}s  dBFS={seg['dbfs']:.2f}")

    return top


def slice_chunks(video_file: str, segments: list[dict]) -> list[str]:
    """
    Use 'ffmpeg -c copy' to slice each segment from the original MP4.
    Saves to /queue/chunk_1.mp4 … chunk_N.mp4.
    Returns list of output paths.
    """
    print(f"\n✂️   Slicing {len(segments)} chunk(s) into {QUEUE_DIR}/…")
    paths = []
    for i, seg in enumerate(segments, 1):
        out_path = os.path.join(QUEUE_DIR, f"chunk_{i}.mp4")
        print(f"   [{i}/{len(segments)}] chunk_{i}.mp4  @ {seg['start_time']:.1f}s")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_file,
                "-ss", str(seg["start_time"]),
                "-t", str(CHUNK_DURATION_S),
                "-c", "copy",              # lossless copy — fastest possible
                out_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        paths.append(out_path)

    print(f"   ✅  All {len(segments)} chunks saved.")
    return paths


def write_state(title: str, total_chunks: int):
    """Write state.json with current_chunk=1 and total_chunks."""
    state = {
        "original_title": title,
        "current_chunk": 1,
        "total_chunks": total_chunks,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)
    print(f"\n📝  Written {STATE_FILE}: {state}")


def cleanup(files: list[str]):
    for path in files:
        if os.path.exists(path):
            print(f"🗑️   Removing {path}")
            os.remove(path)


# ─────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────
def main():
    ensure_dirs()
    video_file = None
    try:
        identifier = get_random_cartoon()
        video_file, title = download_video(identifier)

        audio_file = extract_audio(video_file)

        segments = find_loudest_chunks(audio_file, n=TOP_N_CHUNKS)

        slice_chunks(video_file, segments)
        write_state(title, total_chunks=len(segments))

        # Clean up the large MP4 and temporary WAV — keep only the queue chunks
        cleanup([video_file, audio_file])

        print("\n🎉  brainrot_fetcher complete! Queue is ready.\n")

    except Exception as exc:
        print(f"\n❌  Fatal error in brainrot_fetcher: {exc}", file=sys.stderr)
        # Best-effort cleanup even on failure
        cleanup([f for f in [video_file, TEMP_AUDIO_FILE] if f])
        sys.exit(1)


if __name__ == "__main__":
    main()
