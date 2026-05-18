import subprocess
import os

# Create valid silent mp3 files
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-q:a", "9", "-acodec", "libmp3lame", "temp_hook_voice.mp3"], check=True, stderr=subprocess.DEVNULL)
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-q:a", "9", "-acodec", "libmp3lame", "temp_sfx.mp3"], check=True, stderr=subprocess.DEVNULL)

cmd = [
    "ffmpeg", "-y",
    "-i", "temp_hook_voice.mp3",
    "-i", "temp_sfx.mp3",
    "-filter_complex", "[0:a]volume=1.0[voice];[1:a]volume=0.35[sfx];[voice][sfx]amix=inputs=2:duration=longest:dropout_transition=0[aout]",
    "-map", "[aout]",
    "-c:a", "aac",
    "-b:a", "128k",
    "-ar", "48000",
    "-ac", "2",
    "temp_hook_audio.mp3",
]
try:
    res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print("SUCCESS")
except subprocess.CalledProcessError as e:
    print("EXIT STATUS:", e.returncode)
    print("STDERR:", e.stderr.decode())
