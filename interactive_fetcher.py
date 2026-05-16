#!/usr/bin/env python3
"""
interactive_fetcher.py — The Interactive Downloader
Accepts a manually pasted Archive.org URL or item identifier,
downloads the highest-resolution MP4 available, opens it in
the system video player, then asks the user where the real
content begins so the intro can be skipped.

Slices the video sequentially into 60-second chunks from the
user-defined start time using libx264 ultrafast re-encode to prevent
NAL unit / keyframe freeze errors.  Writes state.json and deletes the
original large file when done.

Optimised for i3 / 8 GB RAM — no pydub, no audio analysis.
"""

import os
import sys
import json
import subprocess
import math
import glob

import re
import urllib.parse

from internetarchive import get_item


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
QUEUE_DIR        = "queue"
STATE_FILE       = "state.json"
CHUNK_DURATION_S = 60    # V6.0: 1 min — fast 60-second slices

# Regex to extract an identifier from a full Archive.org URL or a bare identifier.
# Handles:
#   https://archive.org/details/TheBarnDance
#   https://archive.org/details/TheBarnDance/
#   https://archive.org/details/TheBarnDance/Nested/File.mp4
#   TheBarnDance   (bare identifier)
_ARCHIVE_URL_RE = re.compile(
    r"(?:https?://(?:[a-zA-Z0-9\-]+\.)?archive\.org/(?:details|download|0/items|items)/)"
    r"([A-Za-z0-9_\-\.]+)"
    r"(?:/([^?#]+))?",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────
# Security gate helper
# ─────────────────────────────────────────────
def verify_auth_token():
    """
    Prevent unauthorised / scraping runs.
    Set:  export API_AUTH_TOKEN=admin_authorized
    """
    token = os.environ.get("API_AUTH_TOKEN")
    if token != "admin_authorized":
        print(
            "\n🔒  Security Error: API_AUTH_TOKEN is invalid or not set.\n"
            "    Run:  export API_AUTH_TOKEN=admin_authorized\n"
            "    Unauthorized execution is prohibited.",
            file=sys.stderr,
        )
        sys.exit(1)


# ─────────────────────────────────────────────
# Archive.org helpers
# ─────────────────────────────────────────────
def prompt_identifier() -> tuple:
    """
    Ask the user to paste an Archive.org URL or bare item identifier.
    Accepts:
      - Full URL  : https://archive.org/details/TheBarnDance
      - File URL  : https://archive.org/details/TheBarnDance/Specific+File.mp4
      - Bare ID   : TheBarnDance
    Returns (identifier, specific_file).
    """
    raw = input(
        "\n🔗 Paste the Archive.org URL or Item Identifier for the cartoon: "
    ).strip()

    # Try to extract from a full URL first
    match = _ARCHIVE_URL_RE.search(raw)
    if match:
        identifier = match.group(1)
        print(f"   🔍  Extracted identifier from URL: {identifier}")
        
        specific_file = match.group(2)
        if specific_file:
            specific_file = specific_file.strip("/")
            if specific_file:
                specific_file = urllib.parse.unquote_plus(specific_file)
            else:
                specific_file = None
                
        if specific_file:
            print(f"   🔍  Targeting specific file: {specific_file}")
            
        return identifier, specific_file

    # Validate bare identifier: alphanumeric, hyphens, underscores, dots
    if re.fullmatch(r"[A-Za-z0-9_\-\.]+", raw):
        print(f"   🔍  Using identifier: {raw}")
        return raw, None

    raise ValueError(
        f"Could not parse a valid Archive.org identifier from: {raw!r}\n"
        "Expected a URL like https://archive.org/details/TheBarnDance "
        "or a bare identifier like TheBarnDance."
    )


def find_best_mp4(identifier: str, specific_file: str = None) -> tuple:
    """
    Fetch item metadata and return
    (download_url, filename, file_size_bytes, human_title).
    Prefers the specific file if requested, else highest-resolution (largest) MP4.
    """
    print(f"\n📦  Fetching metadata for: {identifier}")
    item = get_item(identifier)

    # Verify the item actually exists on the Archive
    if not item.exists:
        raise RuntimeError(
            f"Item '{identifier}' was not found on Internet Archive.\n"
            "Double-check the URL or identifier and try again."
        )

    best = None
    if specific_file:
        for f in item.files:
            if f["name"] == specific_file:
                best = f
                break
        if not best:
            print(f"   ⚠️   Specific file '{specific_file}' not found. Falling back to largest MP4.")

    if not best:
        mp4_files = [
            f for f in item.files
            if f["name"].lower().endswith(".mp4")
        ]
        if not mp4_files:
            raise RuntimeError(f"No .mp4 files found for item: {identifier}")

        # Sort descending by file size → highest resolution first
        mp4_files.sort(key=lambda x: int(x.get("size", 0)), reverse=True)
        best = mp4_files[0]

    filename  = best["name"]
    file_size = int(best.get("size", 0))
    url       = f"https://archive.org/download/{identifier}/{filename}"
    title     = item.metadata.get("title", identifier)

    print(f"   🎯  Selected MP4 : {filename}  ({file_size / 1_048_576:.1f} MB)")
    return url, filename, file_size, title


# ─────────────────────────────────────────────
# Downloader
# ─────────────────────────────────────────────
def download_video(url: str, filename: str, file_size: int) -> str:
    """
    Download with aria2c for maximum speed.
    Returns the local file path.
    """
    # Use basename to avoid FileNotFoundError when filename contains nested paths
    local_path = os.path.basename(filename)  # save to cwd (project root)

    print(f"\n⬇️   Downloading: {local_path}")
    
    try:
        subprocess.run(
            [
                "aria2c",
                "-x", "16",
                "-s", "16",
                "-j", "16",
                "--summary-interval=1",
                "--allow-overwrite=true",
                "-c",
                "--auto-file-renaming=false",
                "-d", ".",
                "-o", local_path,
                url
            ],
            check=True
        )
    except FileNotFoundError:
        print("\n❌  Error: aria2c is not installed.", file=sys.stderr)
        print("    Please run: sudo apt install aria2", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\n❌  aria2c download failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"   ✅  Download complete → {local_path}")
    return local_path


# ─────────────────────────────────────────────
# Video preview & intro-skip prompt
# ─────────────────────────────────────────────
def open_video_player(path: str):
    """
    Open the video file in the default Linux video player via xdg-open.
    Falls back to vlc if xdg-open is not available.
    The process is launched in the background so the script continues.
    """
    print(f"\n🎬  Opening video in your default player: {path}")
    try:
        subprocess.Popen(
            ["xdg-open", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        try:
            subprocess.Popen(
                ["vlc", "--fullscreen", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("   ℹ️   (opened with vlc — xdg-open not found)")
        except FileNotFoundError:
            print(
                "   ⚠️   Could not open video automatically.\n"
                "       Please open it manually:\n"
                f"       {os.path.abspath(path)}",
            )


def prompt_start_time() -> float:
    """
    Block until the user enters the exact second where real content begins.
    Returns the validated float.
    """
    print()
    while True:
        raw = input(
            "Video opened. Enter the exact second where the actual content "
            "begins (e.g., 14.5) to skip the intro: "
        ).strip()
        try:
            value = float(raw)
            if value < 0:
                print("   ⚠️   Value must be 0 or greater. Try again.")
                continue
            return value
        except ValueError:
            print("   ⚠️   Please enter a number (e.g. 0, 14.5, 120). Try again.")


# ─────────────────────────────────────────────
# Duration & slicing
# ─────────────────────────────────────────────
def get_video_duration(path: str) -> float:
    """Return total duration in seconds via ffprobe."""
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


def calculate_chunks(total_duration: float, start_time: float) -> int:
    """
    How many full 60-second chunks fit between start_time and the end?
    """
    usable = total_duration - start_time
    return max(0, math.floor(usable / CHUNK_DURATION_S))


def slice_chunks_sequential(
    video_path: str,
    start_time: float,
    num_chunks: int,
) -> list[str]:
    """
    Slice num_chunks × 60-second segments sequentially from start_time.

    CRITICAL (V4.0): Uses libx264 re-encode instead of -c copy to ensure
    every segment starts on a clean keyframe, eliminating the 13-second
    freeze caused by NAL unit / keyframe alignment errors.

    Codec settings: libx264 ultrafast, CRF 23, AAC 128k, 4 threads.
    Saves to /queue/chunk_1.mp4 … chunk_N.mp4.
    """
    os.makedirs(QUEUE_DIR, exist_ok=True)
    paths = []
    print(f"\n✂️   Slicing {num_chunks} chunk(s) sequentially from {start_time:.1f}s…")
    print("   ℹ️   Re-encoding with libx264 ultrafast (fixes NAL/keyframe freeze).")

    for i in range(1, num_chunks + 1):
        chunk_start = start_time + (i - 1) * CHUNK_DURATION_S
        out_path    = os.path.join(QUEUE_DIR, f"chunk_{i}.mp4")

        print(f"   [{i}/{num_chunks}] chunk_{i}.mp4  @ {chunk_start:.1f}s")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{chunk_start:.3f}",   # fast seek BEFORE -i
                "-i",  video_path,
                "-t",  str(CHUNK_DURATION_S),
                # ── V4.0 CRITICAL FIX: re-encode for clean keyframes ──
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-threads", "4",
                out_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        paths.append(out_path)

    print(f"   ✅  All {num_chunks} chunks saved to {QUEUE_DIR}/")
    return paths


# ─────────────────────────────────────────────
# State management
# ─────────────────────────────────────────────
def write_state(title: str, total_chunks: int):
    """Write / overwrite state.json with current_chunk=1."""
    state = {
        "original_title": title,
        "current_chunk":  1,
        "total_chunks":   total_chunks,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)
    print(f"\n📝  Written {STATE_FILE}:\n    {state}")


def cleanup(paths: list[str]):
    """Delete files; silently skip missing ones."""
    for path in paths:
        if path and os.path.exists(path):
            print(f"🗑️   Removing {path}")
            os.remove(path)


# ─────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("   📥  INTERACTIVE FETCHER")
    print("=" * 60)

    verify_auth_token()

    video_path = None
    try:
        # 1. Prompt user for an Archive.org URL or bare identifier
        identifier, specific_file = prompt_identifier()
        url, filename, file_size, title = find_best_mp4(identifier, specific_file)

        # 2. Download with rich progress bar
        video_path = download_video(url, filename, file_size)

        # 3. Open in system video player
        open_video_player(video_path)

        # 4. Ask user where the real content starts
        start_time = prompt_start_time()

        # 5. Determine total duration & how many chunks fit
        print(f"\n🔬  Running ffprobe on {video_path}…")
        total_dur  = get_video_duration(video_path)
        num_chunks = calculate_chunks(total_dur, start_time)

        print(f"   Total duration  : {total_dur:.1f}s")
        print(f"   Intro skip      : {start_time:.1f}s")
        print(f"   Usable content  : {total_dur - start_time:.1f}s")
        print(f"   60-s chunks     : {num_chunks}")

        if num_chunks == 0:
            print(
                "\n⚠️   No full 60-second chunks fit after the intro.\n"
                "     Choose a smaller start time or try another video.",
                file=sys.stderr,
            )
            cleanup([video_path])
            sys.exit(1)

        # 6. Slice sequentially with ffmpeg -c copy
        slice_chunks_sequential(video_path, start_time, num_chunks)

        # 7. Write state.json
        write_state(title, num_chunks)

        # 8. Delete the original large MP4
        cleanup([video_path])

        print(f"\n🎉  interactive_fetcher complete!  {num_chunks} chunk(s) queued.\n")

    except KeyboardInterrupt:
        print("\n\n⚠️   Interrupted by user.", file=sys.stderr)
        cleanup([video_path])
        sys.exit(130)
    except Exception as exc:
        print(f"\n❌  Fatal error in interactive_fetcher: {exc}", file=sys.stderr)
        cleanup([video_path] if video_path else [])
        sys.exit(1)


if __name__ == "__main__":
    main()
