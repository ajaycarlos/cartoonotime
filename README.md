# 🚀 PROJECT TITLE & OVERVIEW

Welcome to the **Brainrot Pipeline** — a fully automated, intelligent YouTube Shorts content pipeline! 

This project leverages Gemini AI to effortlessly slice long-form YouTube content into engaging 60-second chunks, apply dynamic voiceover hooks, overlay satisfying background videos, and generate humanized, stylized subtitles. It handles everything from content curation to YouTube auto-scheduling and uploading.

Featuring the **V7.5 core engine baseline**, this pipeline is designed to save you hours of manual editing while maximizing audience retention and engagement.

# 📋 PREREQUISITES (SYSTEM DEPENDENCIES)

Before setting up the project, you must have the following system-level dependencies installed on your host machine:

- **Python 3.10+**: Required for the core scripts and AI libraries.
- **FFmpeg**: The powerhouse behind all video and audio processing, editing, and subtitle rendering.

### Installing FFmpeg
Choose the command for your operating system:

**macOS** (Using Homebrew):
```bash
brew install ffmpeg
```

**Linux (Ubuntu/Debian)** (Using apt):
```bash
sudo apt update && sudo apt install ffmpeg
```

**Windows**:
- **Option 1 (Winget)**: Open PowerShell or Command Prompt and run:
  ```powershell
  winget install Gyan.FFmpeg
  ```
- **Option 2 (Manual Setup)**: 
  1. Download the latest FFmpeg build zip from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases).
  2. Extract the contents to a folder on your drive (e.g., `C:\ffmpeg`).
  3. Add the `C:\ffmpeg\bin` folder to your System Environment Path variables.

# ⚙️ INSTALLATION & SETUP (STEP-BY-STEP)

### Step 1: Clone the Repository
Get the code onto your local machine:
```bash
git clone <repo_url> 
cd <repo_folder>
```

### Step 2: Create and Activate a Python Virtual Environment
Keep your dependencies isolated and clean.

**Windows (CMD/PowerShell)**:
```cmd
python -m venv venv
venv\Scripts\activate
```

**Linux / macOS**:
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Python Packages
With your virtual environment active, install the required libraries used across the pipeline:
```bash
pip install python-dotenv google-genai yt-dlp openai-whisper google-auth-oauthlib google-api-python-client google-auth-httplib2 edge-tts elevenlabs
```
*(If a `requirements.txt` is available in your repository, simply run `pip install -r requirements.txt`)*

# 🔒 CONFIGURATION (API KEYS & OAUTH)

To protect the pipeline and enable AI features, you must configure strict security tokens and API keys.

### 1. Environment Variables
You need to set three primary environment variables:
- `API_AUTH_TOKEN`: Acts as a security gate to prevent unauthorized execution. **Must be set to `admin_authorized`.**
- `GEMINI_API_KEY`: Your Google Gemini API key for the AI Director to analyze transcripts and slice the video.
- `ELEVENLABS_API_KEY`: Your ElevenLabs API key for high-quality voice synthesis for video hooks.

**Windows (CMD)**:
```cmd
set API_AUTH_TOKEN=admin_authorized
set GEMINI_API_KEY=your_key_here
set ELEVENLABS_API_KEY=your_elevenlabs_key_here
```

**Windows (PowerShell)**:
```powershell
$env:API_AUTH_TOKEN="admin_authorized"
$env:GEMINI_API_KEY="your_key_here"
$env:ELEVENLABS_API_KEY="your_elevenlabs_key_here"
```

**Linux / macOS**:
```bash
export API_AUTH_TOKEN="admin_authorized"
export GEMINI_API_KEY="your_key_here"
export ELEVENLABS_API_KEY="your_elevenlabs_key_here"
```
> **Tip**: You can also create a `.env` file in the root directory and add the following keys to load them automatically:
> ```env
> API_AUTH_TOKEN="admin_authorized"
> GEMINI_API_KEY="your_key_here"
> ELEVENLABS_API_KEY="your_elevenlabs_key_here"
> ```
> **Security Note**: Never commit your `.env` or `client_secrets.json` file to public repositories. Ensure they are listed in your `.gitignore` to prevent accidental credential leakage and keep your integrations secure.

### 2. YouTube API Credentials (OAuth)
To automate private draft uploads to YouTube, you need a Google Cloud Console project with the YouTube Data API v3 enabled.
- Download your OAuth 2.0 Client credentials JSON file.
- Rename it to `client_secrets.json`.
- Place `client_secrets.json` directly into the **root directory** of this project.

# 📁 DIRECTORY STRUCTURE CAPTURE

Your project directory needs specific folders to function correctly. Ensure your structure looks like this:

```text
toonshorts/
├── main.py
├── ai_director.py
├── smart_editor.py
├── yt_uploader_v2.py
├── client_secrets.json      <-- Drop your YouTube OAuth JSON here
├── .env                     <-- (Optional) Store your API keys here
├── queue/                   <-- [AUTO-CREATED] The AI Director will cache downloaded chunks here
└── satisfying_base/         <-- [REQUIRED] Create this and drop your satisfying MP4 videos here
```

> **IMPORTANT**: You must manually create the `satisfying_base/` folder and place at least one `.mp4` satisfying video inside if you intend to use the dual-screen stack layout! The `queue/` folder is handled by the pipeline automatically.

# 🎬 HOW TO RUN IT

Once your environment is configured, `client_secrets.json` is in place, and your `satisfying_base/` has videos, you can launch the pipeline!

Start the orchestrator by running:
```bash
python3 main.py
```
*(Or `python main.py` on Windows)*

### Interactive Pipeline Flow:
1. **Target URL**: The AI Director will ask for the YouTube video URL you want to process.
2. **Layout Prompt**: You will be asked: `❓ Apply satisfying gameplay base split layer? (y/n)`. 
   - Choosing `y` will use a 75/25 split-screen with videos from your `satisfying_base/` folder.
   - Choosing `n` will render the video using the full 1080x1920 canvas.
3. **Automated Processing**: The pipeline will fetch the transcript, let Gemini analyze and slice the best clips, apply voiceover hooks, render the video with "Brainrot" subtitles, and upload the first chunk to your YouTube channel as a private draft.
4. **Browser Review**: YouTube Studio will open in your browser automatically so you can review the uploaded draft.
5. **Smart Reset Confirmation**: Once all scheduled chunks are completed, the script will prompt you to perform a "Smart Reset". 
   - Selecting `y` will automatically clean up all residual chunk files and temporary video assets while preserving your scheduling calendar. 
   - Selecting `n` preserves all local media files for manual review.

Run `python3 main.py` again to process the next chunk in the queue!
