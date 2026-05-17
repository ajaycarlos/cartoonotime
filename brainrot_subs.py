import re

def parse_srt_time(time_str):
    """Parses SRT time format HH:MM:SS,mmm into seconds float."""
    h, m, s_ms = time_str.strip().split(':')
    s, ms = s_ms.split(',')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

def to_ass_time(seconds):
    """Converts seconds float into ASS time format H:MM:SS.cs."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    # ASS requires 2 decimal places for fractions of a second
    return f"{h}:{m:02d}:{s:05.2f}"

def generate_brainrot_ass(input_vtt_path, output_ass_path):
    """
    Parses VTT/SRT, chunks text into max 2 words, recalculates timestamps locally,
    and writes to an .ass file with the 'Brainrot' style.
    """
    with open(input_vtt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Match SRT/VTT blocks. This regex works for typical SRT formats.
    # It looks for optional block number, timestamp line, and text.
    pattern = re.compile(r'(?:^\d+$\n)?^(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3}).*?\n(.*?)(?=\n\n|\n\d+\n|\Z)', re.MULTILINE | re.DOTALL)
    
    blocks = pattern.findall(content)

    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Brainrot,Arial Black,24,&H0000FFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,2,10,10,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    events = []
    
    for start_str, end_str, text_block in blocks:
        text_block = text_block.replace('\n', ' ').strip()
        if not text_block:
            continue
            
        # Normalize comma to dot if parsing VTT instead of SRT
        start_str = start_str.replace('.', ',')
        end_str = end_str.replace('.', ',')
        
        start_sec = parse_srt_time(start_str)
        end_sec = parse_srt_time(end_str)
        
        words = text_block.split()
        if not words:
            continue
            
        chunks = []
        for i in range(0, len(words), 2):
            chunks.append(" ".join(words[i:i+2]))
            
        num_chunks = len(chunks)
        duration = end_sec - start_sec
        chunk_duration = duration / num_chunks
        
        for i, chunk in enumerate(chunks):
            chunk_start = start_sec + i * chunk_duration
            chunk_end = start_sec + (i + 1) * chunk_duration
            
            ass_start = to_ass_time(chunk_start)
            ass_end = to_ass_time(chunk_end)
            
            # Format: Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
            events.append(f"Dialogue: 0,{ass_start},{ass_end},Brainrot,,0,0,0,,{chunk}")

    with open(output_ass_path, 'w', encoding='utf-8') as f:
        f.write(ass_header)
        f.write("\n".join(events) + "\n")

if __name__ == "__main__":
    # Simple test logic if run standalone
    import sys
    if len(sys.argv) == 3:
        generate_brainrot_ass(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python brainrot_subs.py input.srt output.ass")
