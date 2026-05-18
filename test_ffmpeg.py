import subprocess
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
    with open("temp_hook_voice.mp3", "w") as f: f.write("test")
    with open("temp_sfx.mp3", "w") as f: f.write("test")
    res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
except subprocess.CalledProcessError as e:
    print("EXIT STATUS:", e.returncode)
    print("STDERR:", e.stderr.decode())
