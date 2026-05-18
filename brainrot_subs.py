#!/usr/bin/env python3
"""
brainrot_subs.py — Local Brainrot Subtitle Generator

Parses Whisper-generated SRT files and produces a styled .ass file with
Hormozi/Brainrot aesthetics:
  • Arial Black  Fontsize=90  MarginV=290
  • Bright-yellow text, thick black outline
  • Max 2 words per flash chunk for rapid eye engagement

SRT Parser (V2 — State Machine):
  Replaces the original fragile regex with a robust line-by-line state
  machine.  The old regex used DOTALL + optional block-number groups and
  would silently drop every subtitle block after block 1 whenever Whisper
  output had unusual spacing, Windows CRLF endings, or multi-line text —
  which was the root cause of missing subtitles past the 2.5-second hook.

Subtitle Timestamp Fix:
  All timestamps in the Whisper SRT are *already* relative to the start of
  the transcribed file (TEMP_HOOKED), so no additional offset subtraction
  is needed here.  The state machine preserves them verbatim.
"""

import re


# ─────────────────────────────────────────────
# Time conversion utilities
# ─────────────────────────────────────────────

def parse_srt_time(time_str: str) -> float:
    """
    Parse an SRT/VTT timestamp (HH:MM:SS,mmm or HH:MM:SS.mmm) → float seconds.

    Accepts both comma and dot as the sub-second separator so this works
    with both SRT (comma) and VTT (dot) inputs without pre-normalisation.
    """
    time_str = time_str.strip().replace('.', ',')
    h, m, s_ms = time_str.split(':')
    s, ms = s_ms.split(',')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def to_ass_time(seconds: float) -> str:
    """Convert float seconds → ASS timestamp H:MM:SS.cs (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# ─────────────────────────────────────────────
# State-machine SRT parser
# ─────────────────────────────────────────────

def _parse_srt_blocks(content: str) -> list[tuple[float, float, str]]:
    """
    Parse raw SRT text into a list of (start_sec, end_sec, text) tuples.

    Uses a simple 4-state machine (SEEK_INDEX → SEEK_TIMESTAMP → COLLECT_TEXT
    → FLUSH) that is robust against:
      • Extra blank lines between blocks
      • Windows CRLF line-endings
      • Leading/trailing whitespace on any line
      • Multi-line text inside a single subtitle block
      • Missing or repeated block-index numbers
      • Whisper's occasionally non-sequential index numbering

    This replaces the old DOTALL+MULTILINE regex that silently dropped all
    blocks after block 1 whenever the SRT had non-standard formatting.
    """
    # Normalise line endings to LF and split
    lines = content.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    TIMESTAMP_RE = re.compile(
        r'^(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})'
    )

    blocks: list[tuple[float, float, str]] = []

    state       = "SEEK_INDEX"   # states: SEEK_INDEX, SEEK_TIMESTAMP, COLLECT_TEXT
    start_sec   = 0.0
    end_sec     = 0.0
    text_lines: list[str] = []

    def flush():
        nonlocal text_lines
        if text_lines:
            combined = " ".join(t for t in text_lines if t)
            if combined.strip():
                blocks.append((start_sec, end_sec, combined.strip()))
        text_lines = []

    for raw_line in lines:
        line = raw_line.strip()

        if state == "SEEK_INDEX":
            # Expect a block-index number; skip blank lines
            if line.isdigit():
                state = "SEEK_TIMESTAMP"
            # Also handle files where the index is missing: try timestamp directly
            elif TIMESTAMP_RE.match(line):
                m = TIMESTAMP_RE.match(line)
                start_sec = parse_srt_time(m.group(1))
                end_sec   = parse_srt_time(m.group(2))
                state     = "COLLECT_TEXT"

        elif state == "SEEK_TIMESTAMP":
            m = TIMESTAMP_RE.match(line)
            if m:
                start_sec = parse_srt_time(m.group(1))
                end_sec   = parse_srt_time(m.group(2))
                state     = "COLLECT_TEXT"
            elif line == "":
                # Unexpected blank before timestamp — reset
                state = "SEEK_INDEX"

        elif state == "COLLECT_TEXT":
            if line == "":
                # Blank line = block separator → flush and seek next block
                flush()
                state = "SEEK_INDEX"
            elif line.isdigit() and not text_lines:
                # Edge case: no text, next block index appeared immediately
                flush()
                state = "SEEK_TIMESTAMP"
            else:
                text_lines.append(line)

    # Flush the last block (file may not end with a trailing blank line)
    flush()

    return blocks


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def generate_brainrot_ass(input_srt_path: str, output_ass_path: str) -> None:
    """
    Parse input_srt_path (SRT or VTT produced by Whisper) and write a
    styled .ass file to output_ass_path.

    Brainrot style spec (from V7.2 tuning):
      Fontname  = Arial Black
      Fontsize  = 75          
      MarginV   = 290         (clears mobile UI overlays)
      Alignment = 2           (bottom-centre)
      Primary   = &H0000FFFF  (bright yellow)
      Outline   = 4 px black
    """
    with open(input_srt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = _parse_srt_blocks(content)

    if not blocks:
        print("   ⚠️   brainrot_subs: no subtitle blocks parsed — check SRT content.")

    # ── ASS header ────────────────────────────────────────────────────────────
    ass_header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Brainrot,Arial Black,55,&H0000FFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,2,10,10,290,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    MAX_WORDS_PER_CHUNK = 2

    for start_sec, end_sec, text in blocks:
        words = text.split()
        if not words:
            continue

        # Flash-chunk: split into groups of ≤2 words for rapid cycling
        chunks = [
            " ".join(words[i:i + MAX_WORDS_PER_CHUNK])
            for i in range(0, len(words), MAX_WORDS_PER_CHUNK)
        ]

        duration       = max(end_sec - start_sec, 0.001)  # guard against 0-length
        chunk_duration = duration / len(chunks)

        for i, chunk_text in enumerate(chunks):
            chunk_start = start_sec + i * chunk_duration
            chunk_end   = start_sec + (i + 1) * chunk_duration

            ass_start = to_ass_time(chunk_start)
            ass_end   = to_ass_time(chunk_end)

            events.append(
                f"Dialogue: 0,{ass_start},{ass_end},Brainrot,,0,0,0,,{{\\fscx130\\fscy130\\t(0,100,\\fscx100\\fscy100)}}{chunk_text}"
            )

    with open(output_ass_path, 'w', encoding='utf-8') as f:
        f.write(ass_header)
        f.write("\n".join(events) + "\n")

    print(
        f"   ✅  brainrot_subs: wrote {len(events)} flash-chunk event(s) → {output_ass_path}"
    )


# ─────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        generate_brainrot_ass(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python brainrot_subs.py input.srt output.ass")
