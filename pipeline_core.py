import os
import sys
import json
import glob
import subprocess
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
STATE_FILE    = "state.json"
QUEUE_DIR     = "queue"
HISTORY_FILE  = "processed_history.json"

SCRIPTS = {
    "fetcher":  "ai_director.py",
    "editor":   "smart_editor.py",
    "uploader": "yt_uploader_v2.py",
}

# ─────────────────────────────────────────────
# Security gate
# ─────────────────────────────────────────────
def verify_auth_token():
    token = os.environ.get("API_AUTH_TOKEN")
    if token != "admin_authorized":
        print(
            "\n🔒  Security Error: API_AUTH_TOKEN is invalid or not set.\n"
            "    Run:  export API_AUTH_TOKEN=admin_authorized\n"
            "    Unauthorized execution is prohibited.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.environ.get("GEMINI_API_KEY"):
        print(
            "\n🤖  Security Error: GEMINI_API_KEY is not set.\n"
            "    This pipeline requires Gemini AI for the AI Director.\n"
            "    Run:  export GEMINI_API_KEY='your_key' or add it to .env",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.environ.get("ELEVENLABS_API_KEY"):
        print(
            "\n🔊  Security Error: ELEVENLABS_API_KEY is not set.\n"
            "    This pipeline requires ElevenLabs for voice synthesis (V7.8).\n"
            "    Run:  export ELEVENLABS_API_KEY='your_key' or add it to .env",
            file=sys.stderr,
        )
        sys.exit(1)

    print("🔒  Security checks passed (Auth Token, Gemini Key & ElevenLabs Key).")


# ─────────────────────────────────────────────
# Safety Pre-checks
# ─────────────────────────────────────────────
def run_safety_pre_checks():
    if not os.path.exists("client_secrets.json"):
        print(
            "❌  client_secrets.json not found.\n"
            "    Download it from Google Cloud Console → Credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not glob.glob("satisfying_base/*.mp4"):
        print(
            "❌  No satisfying videos found in satisfying_base/ directory.\n"
            "    Place your satisfying videos (.mp4) there before running.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(QUEUE_DIR, exist_ok=True)


# ─────────────────────────────────────────────
# Duplicate History Checkpoint Engine  (V7.8)
# ─────────────────────────────────────────────
def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict) and "records" in data:
                    return data
        except (json.JSONDecodeError, OSError):
            print(f"    ⚠️   {HISTORY_FILE} is corrupt — starting fresh history.", file=sys.stderr)
    return {"records": []}


def save_history(history: dict) -> None:
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=4)
    os.replace(tmp, HISTORY_FILE)
    try:
        import stat as _stat
        os.chmod(HISTORY_FILE, _stat.S_IRUSR | _stat.S_IWUSR)
    except OSError:
        pass


def normalize_title(s: str) -> str:
    if not s:
        return ""
    import re
    return re.sub(r'[^a-z0-9]', '', s.lower())


def check_duplicate_and_confirm(url: str, title: str, history: dict, auto_run: bool = False) -> bool:
    records = history.get("records", [])
    norm_title = normalize_title(title)
    
    matched = []
    for r in records:
        if r.get("url") == url:
            matched.append(r)
            continue
        r_title = r.get("title")
        if r_title and norm_title and normalize_title(r_title) == norm_title:
            matched.append(r)
            continue

    if not matched:
        return True  # no duplicate — green light

    os.system("clear")
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║  ⚠️  DUPLICATE DETECTED" + " " * 48 + "║")
    print("║" + " " * 68 + "║")
    print("║  You have already processed and clipped this specific video    ║")
    print("║  source in a previous production session.                      ║")
    print("║" + " " * 68 + "║")
    if matched[0].get("completed_at"):
        ts = matched[0]["completed_at"]
        print(f"║  Previously processed : {ts:<44}║")
    if matched[0].get("title"):
        clipped_title = matched[0]["title"][:44]
        print(f"║  Title match          : {clipped_title:<44}║")
    print("║" + " " * 68 + "║")
    print("╚" + "═" * 68 + "╝")

    if auto_run:
        print("   🤖  AUTO_RUN: Duplicate detected. Automating safeguard abort (exiting pipeline gracefully).")
        return False

    while True:
        answer = input(
            "\n❓  Do you want to ignore this safeguard and force process the video anyway? (y/n): "
        ).strip().lower()
        if answer in ("y", "yes"):
            print("   ⚡  Override confirmed — proceeding with forced reprocessing.")
            return True
        elif answer in ("n", "no"):
            print("   ✅  Safeguard respected. Exiting pipeline gracefully.")
            return False
        else:
            print("   ⚠️   Please enter 'y' or 'n'.")


def append_to_history(url: str, title: str, history: dict) -> None:
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    timestamp = datetime.now(ist_tz).isoformat()
    history["records"].append({
        "url":          url,
        "title":        title,
        "completed_at": timestamp,
    })
    save_history(history)
    print(f"   📝  History updated: '{title}' logged in {HISTORY_FILE}.")


# ─────────────────────────────────────────────
# Queue helpers
# ─────────────────────────────────────────────
def queue_is_empty() -> bool:
    chunks = glob.glob(os.path.join(QUEUE_DIR, "chunk_*.mp4"))
    return len(chunks) == 0


def state_exists() -> bool:
    return os.path.exists(STATE_FILE)


def read_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"\n❌  Could not read {STATE_FILE}: {exc}", file=sys.stderr)
        sys.exit(1)


def prompt_and_save_layout_choice(auto_run: bool = False):
    state = read_state()
    if "use_satisfying_base" not in state:
        if auto_run or os.environ.get("AUTO_RUN") == "1":
            print("    🤖  AUTO_RUN: Automatically applying satisfying gameplay base split layer.")
            state["use_satisfying_base"] = True
        else:
            while True:
                answer = input("\n❓  Apply satisfying gameplay base split layer? (y/n): ").strip().lower()
                if answer in ("y", "yes"):
                    print("    ✅  Layout: 75/25 split with satisfying base.")
                    state["use_satisfying_base"] = True
                    break
                elif answer in ("n", "no"):
                    print("    ✅  Layout: Full 1080×1920 canvas (no satisfying base).")
                    state["use_satisfying_base"] = False
                    break
                else:
                    print("    ⚠️   Please enter 'y' or 'n'.")
        
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)


# ─────────────────────────────────────────────
# Script runner
# ─────────────────────────────────────────────
def run_script(name: str, path: str, args: list[str] = None):
    print(f"\n{'─' * 60}")
    print(f"▶  Running {name}: {path}")
    print(f"{'─' * 60}")
    
    cmd = [sys.executable, path]
    if args:
        cmd.extend(args)

    result = subprocess.run(
        cmd,
        stdin=None,
    )
    if result.returncode != 0:
        print(
            f"\n❌  {name} exited with code {result.returncode}. Pipeline halted.",
            file=sys.stderr,
        )
        sys.exit(result.returncode)
