import os
import random
import subprocess
from internetarchive import search_items, get_item

TEMP_DIR = "temp_raw"

def fetch_and_trim():
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)
        
    print("Searching for public domain cartoons (1930 and older)...")
    # Enforce strict public domain dates and look for MPEG4
    query = 'collection:animationandcartoons AND date:[1800-01-01 TO 1930-12-31] AND format:"MPEG4"'
    
    # Add a custom 60-second timeout to prevent ReadTimeoutError
    request_kwargs = {'timeout': 60}
    
    try:
        search_iterator = search_items(query, request_kwargs=request_kwargs)
        results = []
        for i, item in enumerate(search_iterator):
            results.append(item)
            if i >= 50: # Limit to 50 items for speed
                break
                
        if not results:
            print("No cartoons found matching criteria. Please run the script again.")
            return
            
        random_item_meta = random.choice(results)
        identifier = random_item_meta['identifier']
        print(f"Selected item: {identifier}")
        
        # Pass the timeout to the get_item request as well
        item = get_item(identifier, request_kwargs=request_kwargs)
        
    except Exception as e:
        print(f"Critical Error during fetching or connecting to Archive.org: {e}")
        return
    
    # Locate largest mp4
    mp4_files = [f for f in item.files if f['name'].lower().endswith('.mp4')]
    
    if not mp4_files:
        print("No MP4 files found in the item.")
        return
        
    largest_mp4 = max(mp4_files, key=lambda x: int(x.get('size', 0)))
    file_name = largest_mp4['name']
    
    print(f"Downloading {file_name} (Size: {int(largest_mp4.get('size', 0)) / (1024*1024):.2f} MB)...")
    
    # Download the file with error handling for Archive.org server issues
    try:
        item.download(files=file_name, destdir=TEMP_DIR, no_directory=True)
    except Exception as e:
        print(f"\nArchive.org server error during download: {e}")
        print("This is a temporary issue with their servers. Please run the script again to pick a different cartoon.")
        return
    
    downloaded_path = os.path.join(TEMP_DIR, file_name)
    
    # Verify file was downloaded
    if not os.path.exists(downloaded_path):
        print(f"Error: Could not find the downloaded file at {downloaded_path}")
        return

    print("Download complete. Retrieving video duration...")
    
    # Get video duration using ffprobe
    duration_cmd = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of",
        "default=noprint_wrappers=1:nokey=1", downloaded_path
    ]
    
    try:
        duration_output = subprocess.check_output(duration_cmd, stderr=subprocess.STDOUT).decode('utf-8').strip()
        duration = float(duration_output)
    except Exception as e:
        print(f"Error getting duration: {e}. Defaulting to 60 seconds.")
        duration = 60.0
        
    if duration <= 60:
        print("Video is shorter than or equal to 60 seconds. Using the whole video.")
        start_time = 0
    else:
        # Trim a random 60-second segment
        # Avoid the very beginning and very end (often credits/intros) if possible
        buffer = min(duration * 0.1, 60.0) # 10% buffer or 60s
        safe_start = buffer
        safe_end = duration - 60 - buffer
        
        if safe_end > safe_start:
            start_time = random.uniform(safe_start, safe_end)
        else:
            start_time = random.uniform(0, max(0, duration - 60))
            
    output_clip = os.path.join(TEMP_DIR, "raw_clip.mp4")
    
    print(f"Trimming 60 seconds starting from {start_time:.2f}...")
    # Use -c copy for lightning-fast trimming without re-encoding
    trim_cmd = [
        "ffmpeg", "-y", "-ss", str(start_time), "-i", downloaded_path,
        "-t", "60", "-c", "copy", output_clip
    ]
    
    try:
        subprocess.run(trim_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Trim complete.")
    except subprocess.CalledProcessError as e:
        print(f"Error during FFmpeg trimming: {e}")
        return
    
    # Delete original large file
    print("Cleaning up original large file to save space...")
    os.remove(downloaded_path)
    print(f"Done! Final clip saved to {output_clip}")

if __name__ == "__main__":
    fetch_and_trim()
