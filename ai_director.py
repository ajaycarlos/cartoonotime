#!/usr/bin/env python3
"""
ai_director.py — V7 AI Director
════════════════════════════════════════════════════════════════════════════════
Replaces interactive_fetcher.py with a fully automated, AI-driven pipeline.

Workflow:
  1. [Transcript]  Download auto-generated subtitles (VTT) via yt-dlp —
                   NO video download.  Parse them into "[HH:MM:SS → HH:MM:SS] text"
                   lines so the LLM gets clean, timestamped text.

  2. [AI Analysis] Send the transcript to Gemini (gemini-1.5-flash) with a
                   strict editorial prompt.  Receive a JSON array of clip
                   definitions: {"title": …, "start_time": …, "end_time": …}.

  3. [Download]    For each clip, call yt-dlp --download-sections to pull
                   *only* that timestamp window — no full-video download.
                   Clips are saved as queue/chunk_<N>.mp4.

  4. [State]       Write/update state.json so V6.0 smart_editor.py can take
                   over immediately without any manual configuration.

Security:
  • GEMINI_API_KEY is NEVER hard-coded; it is read exclusively from the
    environment variable  GEMINI_API_KEY.
  • No user data is persisted beyond the queue/ folder and state.json.
  • The yt-dlp call is executed via a list (no shell=True) to prevent
    command-injection attacks.

Usage:
    export GEMINI_API_KEY="your_key_here"
    python ai_director.py <YouTube_URL>

    Optional flags:
      --max-clips N     Maximum number of clips to download (default: 5)
      --output-dir DIR  Queue directory (default: queue/)
      --dry-run         Analyse & print clips but do NOT download
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ── google-genai (current SDK) ───────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    print(
        "❌  google-genai is not installed.\n"
        "    Run:  pip install google-genai",
        file=sys.stderr,
    )
    sys.exit(1)


# ═════════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════════
STATE_FILE    = "state.json"
QUEUE_DIR     = "queue"
GEMINI_MODEL  = "gemini-2.5-flash"

# yt-dlp format: best ≤1080p video + best audio, merged into mp4
YTDLP_FORMAT  = "bestvideo[height<=1080]+bestaudio/best"

# Minimum and maximum acceptable clip durations (seconds) — must mirror the
# LLM prompt so malformed responses can be validated locally.
CLIP_MIN_SEC  = 28   # slight buffer below the LLM's "30 s" rule
CLIP_MAX_SEC  = 65   # slight buffer above the LLM's "60 s" rule


# ═════════════════════════════════════════════════════════════════════════════
# Task 1 — Transcript Extraction
# ═════════════════════════════════════════════════════════════════════════════

def _vtt_time_to_seconds(vtt_ts: str) -> float:
    """Convert a VTT timestamp (HH:MM:SS.mmm  or  MM:SS.mmm) to float seconds."""
    parts = vtt_ts.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        return float(parts[0])


def _seconds_to_hms(seconds: float) -> str:
    """Format float seconds as  HH:MM:SS  for the LLM prompt."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def _parse_vtt(vtt_text: str) -> list[dict]:
    """
    Parse a WebVTT string into a list of  {start, end, text}  dicts.

    Handles:
      • Standard  HH:MM:SS.mmm --> HH:MM:SS.mmm  cue headers.
      • Cue identifiers (lines that are pure integers or arbitrary IDs).
      • <c.colorXXXXXX>…</c>  inline tags — stripped to plain text.
      • Duplicate consecutive cues — merged into the previous entry.
    """
    # Strip inline VTT tags  <...>
    _tag_re    = re.compile(r"<[^>]+>")
    # Timestamp line pattern
    _ts_re     = re.compile(
        r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{2}:\d{2}[.,]\d{3})"
        r"\s+-->\s+"
        r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{2}:\d{2}[.,]\d{3})"
    )

    cues: list[dict] = []
    lines = vtt_text.splitlines()
    i = 0
    while i < len(lines):
        m = _ts_re.match(lines[i])
        if m:
            start_raw = m.group(1).replace(",", ".")
            end_raw   = m.group(2).replace(",", ".")
            start_sec = _vtt_time_to_seconds(start_raw)
            end_sec   = _vtt_time_to_seconds(end_raw)

            # Collect cue body lines (until blank line or next timestamp)
            i += 1
            body_lines: list[str] = []
            while i < len(lines) and lines[i].strip() and not _ts_re.match(lines[i]):
                body_lines.append(_tag_re.sub("", lines[i]).strip())
                i += 1

            text = " ".join(body_lines).strip()
            if not text:
                continue

            # Merge if the text is identical to the previous cue (auto-CC duplication)
            if cues and cues[-1]["text"] == text:
                cues[-1]["end"] = end_sec   # extend the previous cue's end time
            else:
                cues.append({"start": start_sec, "end": end_sec, "text": text})
        else:
            i += 1

    return cues


def fetch_transcript(url: str) -> tuple[str, str]:
    """
    Download auto-generated English subtitles for *url* using yt-dlp.

    Returns:
        (formatted_transcript, video_title)
        formatted_transcript — one line per cue:  "[HH:MM:SS → HH:MM:SS] text"
        video_title          — the video's title string

    Raises:
        RuntimeError  if subtitles cannot be found or parsed.
    """
    print(f"\n📥  Fetching transcript for: {url}")

    with tempfile.TemporaryDirectory(prefix="ai_director_subs_") as tmpdir:
        # ── Step 1: Retrieve video title ──────────────────────────────────────
        title_cmd = [
            "yt-dlp",
            "--skip-download",
            "--print", "title",
            url,
        ]
        try:
            title_result = subprocess.run(
                title_cmd,
                capture_output=True,
                text=True,
                check=True,
            )
            video_title = title_result.stdout.strip() or "Unknown Title"
        except subprocess.CalledProcessError:
            video_title = "Unknown Title"

        print(f"    🎬  Video title: {video_title!r}")

        # ── Step 2: Download subtitles only (NO video) ────────────────────────
        sub_cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-auto-sub",
            "--sub-lang",    "en",
            "--sub-format",  "vtt",
            "--convert-subs","vtt",
            "-o",            os.path.join(tmpdir, "%(id)s.%(ext)s"),
            url,
        ]

        print("    ⬇️   Downloading subtitles (no video)…")
        result = subprocess.run(
            sub_cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # yt-dlp writes diagnostic text to stderr
            raise RuntimeError(
                f"yt-dlp subtitle download failed (exit {result.returncode}):\n"
                f"{result.stderr[-2000:]}"
            )

        # ── Step 3: Locate the downloaded .vtt file ───────────────────────────
        vtt_files = list(Path(tmpdir).glob("*.vtt"))
        if not vtt_files:
            raise RuntimeError(
                "No VTT subtitle file was produced by yt-dlp.\n"
                "The video may not have auto-generated captions, or the URL is "
                "invalid/private."
            )

        vtt_path = vtt_files[0]
        print(f"    📄  Parsing: {vtt_path.name}")

        with open(vtt_path, encoding="utf-8", errors="replace") as fh:
            vtt_text = fh.read()

    # ── Step 4: Parse & format ────────────────────────────────────────────────
    cues = _parse_vtt(vtt_text)
    if not cues:
        raise RuntimeError(
            "VTT file was downloaded but contained no parseable cue text.\n"
            "Check that the video has readable captions."
        )

    lines = [
        f"[{_seconds_to_hms(c['start'])} → {_seconds_to_hms(c['end'])}] {c['text']}"
        for c in cues
    ]
    formatted = "\n".join(lines)

    print(f"    ✅  Transcript ready — {len(cues)} cues, "
          f"~{len(formatted)//1000} KB of text.")
    return formatted, video_title


# ═════════════════════════════════════════════════════════════════════════════
# Task 2 — AI Analysis
# ═════════════════════════════════════════════════════════════════════════════

# ── System instruction sent to Gemini ────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are an expert YouTube Shorts editor. Read the following video transcript. \
Find all highly engaging, self-contained segments that would make viral Shorts.

Strict Rules:
1. Each clip must be between 30 and 60 seconds long.
2. The start time must be a strong hook.
3. CRITICAL: The clip MUST NOT end randomly. It must end at the natural conclusion \
of a thought, a punchline, or a full sentence. Check the transcript text to ensure \
the final sentence is complete.
4. Use the EXACT numeric second values from the transcript timestamps (convert \
HH:MM:SS format to total seconds, e.g. 00:01:15.50 → 75.5).
5. Prefer moments with high energy, surprise, humour, or emotion.
6. Clips must be self-contained — a viewer with no context of the full video \
must be able to understand and enjoy it.

Return the output STRICTLY as a JSON array of objects, like this:
[{"title": "Crazy Car Jump", "start_time": 15.5, "end_time": 58.2}]

Do not output markdown, just the raw JSON.\
"""

def analyse_transcript(transcript: str, max_clips: int = 5) -> list[dict]:
    """
    Send *transcript* to Gemini and return a list of clip dicts.

    Each dict has keys:  title (str),  start_time (float),  end_time (float).

    Raises:
        EnvironmentError  if GEMINI_API_KEY is not set.
        RuntimeError      if the model returns unparseable output.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY environment variable is not set.\n"
            "Export it before running:  export GEMINI_API_KEY='your_key'"
        )

    client = genai.Client(api_key=api_key)

    # ── Build contents list (system prompt + user message) ───────────────────
    user_message = (
        f"Here is the video transcript. Identify up to {max_clips} viral Short clips.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    print(f"\n🤖  Sending transcript to Gemini ({GEMINI_MODEL})…")
    print(f"    Transcript length: {len(transcript):,} chars")

    # ── Call Gemini ───────────────────────────────────────────────────────────
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_message,
        config=genai_types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            temperature=0.2,   # low temperature for consistent JSON output
        ),
    )

    raw_text = response.text.strip()
    print(f"    📨  Gemini response received ({len(raw_text)} chars).")

    # ── Parse JSON ────────────────────────────────────────────────────────────
    # Strip accidental markdown fences the model might add despite the prompt
    json_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
    json_text = re.sub(r"\s*```$",          "", json_text, flags=re.MULTILINE)
    json_text = json_text.strip()

    try:
        clips = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Gemini returned non-JSON output. Parse error: {exc}\n"
            f"Raw response (first 1000 chars):\n{raw_text[:1000]}"
        ) from exc

    if not isinstance(clips, list):
        raise RuntimeError(
            f"Expected a JSON array but got {type(clips).__name__}.\n"
            f"Raw response: {raw_text[:500]}"
        )

    # ── Validate & sanitise each clip ─────────────────────────────────────────
    valid_clips: list[dict] = []
    for i, clip in enumerate(clips):
        try:
            title      = str(clip.get("title", f"Clip {i+1}")).strip()
            start_time = float(clip["start_time"])
            end_time   = float(clip["end_time"])
        except (KeyError, TypeError, ValueError) as exc:
            print(f"    ⚠️   Clip {i+1} skipped (bad schema): {exc}", file=sys.stderr)
            continue

        duration = end_time - start_time
        if end_time <= start_time:
            print(f"    ⚠️   Clip {i+1} '{title}' skipped: end ≤ start.", file=sys.stderr)
            continue
        if duration < CLIP_MIN_SEC:
            print(f"    ⚠️   Clip {i+1} '{title}' skipped: "
                  f"duration {duration:.1f}s < {CLIP_MIN_SEC}s minimum.", file=sys.stderr)
            continue
        if duration > CLIP_MAX_SEC:
            print(f"    ⚠️   Clip {i+1} '{title}' skipped: "
                  f"duration {duration:.1f}s > {CLIP_MAX_SEC}s maximum.", file=sys.stderr)
            continue

        valid_clips.append({"title": title, "start_time": start_time, "end_time": end_time})

    if not valid_clips:
        raise RuntimeError(
            "Gemini returned clips but none passed validation.\n"
            f"Raw response: {raw_text[:800]}"
        )

    # Cap at max_clips
    valid_clips = valid_clips[:max_clips]

    print(f"\n🎯  {len(valid_clips)} valid clip(s) identified by Gemini:")
    for idx, clip in enumerate(valid_clips, 1):
        dur = clip["end_time"] - clip["start_time"]
        print(f"    [{idx}] {clip['title']!r:45s}  "
              f"{_seconds_to_hms(clip['start_time'])} → {_seconds_to_hms(clip['end_time'])}  "
              f"({dur:.1f}s)")

    return valid_clips


# ═════════════════════════════════════════════════════════════════════════════
# Task 3 — Precision Downloading
# ═════════════════════════════════════════════════════════════════════════════

def download_clip(url: str, clip: dict, output_path: str) -> bool:
    """
    Download a single clip from *url* using yt-dlp --download-sections.

    Args:
        url         — The YouTube video URL.
        clip        — {"title": …, "start_time": float, "end_time": float}
        output_path — Full path for the output .mp4 file.

    Returns:
        True on success, False on failure (non-fatal; caller decides to skip).
    """
    start = clip["start_time"]
    end   = clip["end_time"]

    # yt-dlp format: "*START-END"  (the asterisk means "from file start" is
    # not used; the range is absolute in the video timeline).
    section = f"*{start}-{end}"

    cmd = [
        "yt-dlp",
        "--download-sections", section,
        "-f", YTDLP_FORMAT,
        "--merge-output-format", "mp4",
        # Force-keyframe at section boundaries for precise cuts
        "--force-keyframes-at-cuts",
        # Prevent re-encoding the full video before slicing
        "--no-playlist",
        # Rate-limit to be polite (adjust if needed)
        "--limit-rate", "5M",
        "-o", output_path,
        url,
    ]

    print(f"\n⬇️   Downloading: {clip['title']!r}")
    print(f"    Section  : {section}")
    print(f"    Output   : {output_path}")

    try:
        subprocess.run(cmd, check=True)
        if not os.path.exists(output_path):
            # yt-dlp sometimes appends an extension even when -o is given
            # Try to find a file with the same stem
            stem = Path(output_path).stem
            parent = Path(output_path).parent
            candidates = list(parent.glob(f"{stem}.*"))
            if candidates:
                candidate = candidates[0]
                candidate.rename(output_path)
                print(f"    🔧  Renamed {candidate.name} → {Path(output_path).name}")
            else:
                print(f"    ⚠️   Output file not found after download: {output_path}",
                      file=sys.stderr)
                return False
        print(f"    ✅  Saved: {output_path}")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"    ❌  yt-dlp failed for '{clip['title']}': {exc}", file=sys.stderr)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Task 4 — Integration / State Management
# ═════════════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """Load state.json if it exists, otherwise return a fresh default state."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError:
            print(f"    ⚠️   {STATE_FILE} is corrupt — starting fresh.", file=sys.stderr)
    return {"satisfying_index": 0}


def save_state(state: dict) -> None:
    """Atomically write state to STATE_FILE."""
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=4)
    os.replace(tmp, STATE_FILE)


def update_state(
    video_title:   str,
    total_chunks:  int,
    current_chunk: int,
    existing_state: dict,
) -> dict:
    """
    Merge AI Director run results into existing state so smart_editor.py can
    take over without any manual editing.

    Preserves: satisfying_index   (managed exclusively by smart_editor.py)
    Resets:    current_chunk → 1  (smart_editor always starts from 1)
    """
    state = dict(existing_state)          # shallow copy — keep satisfying_index
    state["original_title"]  = video_title
    state["total_chunks"]    = total_chunks
    state["current_chunk"]   = current_chunk
    state["ai_director_run"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return state


# ═════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ═════════════════════════════════════════════════════════════════════════════

def run(url: str, max_clips: int = 5, output_dir: str = QUEUE_DIR,
        dry_run: bool = False) -> None:
    """
    Full pipeline:  fetch transcript → AI analysis → download → update state.
    """
    # ── Ensure queue directory exists ────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)

    # ── Step 1: Transcript ────────────────────────────────────────────────────
    transcript, video_title = fetch_transcript(url)

    # ── Step 2: AI Analysis ───────────────────────────────────────────────────
    clips = analyse_transcript(transcript, max_clips=max_clips)

    if dry_run:
        print("\n🔍  DRY-RUN mode — skipping download and state update.")
        print("    Identified clips:")
        for i, clip in enumerate(clips, 1):
            dur = clip["end_time"] - clip["start_time"]
            print(f"      [{i}] {clip['title']}  "
                  f"({clip['start_time']:.2f}s → {clip['end_time']:.2f}s, {dur:.1f}s)")
        return

    # ── Step 3: Download each clip ────────────────────────────────────────────
    existing_state = load_state()
    downloaded_chunks: list[int] = []

    print(f"\n🚀  Downloading {len(clips)} clip(s) into '{output_dir}/'…")

    for i, clip in enumerate(clips, start=1):
        output_path = os.path.join(output_dir, f"chunk_{i}.mp4")

        # Remove stale file from a previous run to avoid partial-file issues
        if os.path.exists(output_path):
            os.remove(output_path)
            print(f"    🗑️   Removed stale: {output_path}")

        success = download_clip(url, clip, output_path)
        if success:
            downloaded_chunks.append(i)
        else:
            print(f"    ⚠️   Clip {i} failed — it will be absent from the queue.",
                  file=sys.stderr)

    if not downloaded_chunks:
        print("\n❌  No clips were successfully downloaded.", file=sys.stderr)
        sys.exit(1)

    # ── Step 4: Update state.json ─────────────────────────────────────────────
    total = len(downloaded_chunks)
    state = update_state(
        video_title   = video_title,
        total_chunks  = total,
        current_chunk = 1,           # smart_editor.py always starts at chunk 1
        existing_state= existing_state,
    )
    save_state(state)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("✅  AI Director V7 — Run Complete")
    print("═" * 60)
    print(f"   Source video  : {video_title!r}")
    print(f"   Clips found   : {len(clips)}")
    print(f"   Clips saved   : {total}  (in '{output_dir}/')")
    print(f"   State file    : {STATE_FILE}")
    print(f"   → Run  python smart_editor.py  to process chunk 1/{total}")
    print("═" * 60)


# ═════════════════════════════════════════════════════════════════════════════
# CLI Entry-Point
# ═════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_director.py",
        description=(
            "V7 AI Director — automatically extract viral 60-second Shorts "
            "from any YouTube video using Gemini AI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python ai_director.py https://youtu.be/xxxxx\n"
            "  python ai_director.py https://youtu.be/xxxxx --max-clips 3\n"
            "  python ai_director.py https://youtu.be/xxxxx --dry-run\n"
        ),
    )
    parser.add_argument(
        "url",
        help="YouTube video URL to process.",
    )
    parser.add_argument(
        "--max-clips",
        type=int,
        default=5,
        metavar="N",
        help="Maximum number of clips to download (default: 5).",
    )
    parser.add_argument(
        "--output-dir",
        default=QUEUE_DIR,
        metavar="DIR",
        help=f"Output queue directory (default: {QUEUE_DIR}/).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyse and print clips but do NOT download them.",
    )
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args   = parser.parse_args()

    try:
        run(
            url        = args.url,
            max_clips  = args.max_clips,
            output_dir = args.output_dir,
            dry_run    = args.dry_run,
        )
    except EnvironmentError as exc:
        print(f"\n🔐  Configuration Error:\n    {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"\n❌  Pipeline Error:\n    {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚡  Interrupted by user.", file=sys.stderr)
        sys.exit(130)
