# YouTube Shorts Automation Architecture

This document provides a technical breakdown of the four core Python scripts that constitute the automated YouTube Shorts pipeline. 

The pipeline is designed to be highly modular, with each script handling a distinct phase of the content lifecycle: from raw download to final upload.

## 1. `fetcher.py` (Ingestion Phase)
**Purpose:** Acquires raw source material.
**Technical Details:**
- Handles the downloading of original cartoon video clips.
- Prepares the raw video file for the downstream processing pipeline.
- Designed to handle network interruptions and ensure the full clip is available locally before moving to the next step.

## 2. `reframer.py` (Visual Processing Phase)
**Purpose:** Converts horizontal (16:9) video into the vertical (9:16) format required for YouTube Shorts.
**Technical Details:**
- Uses OpenCV with Haar Cascades to intelligently identify the main subject (e.g., faces or key focal points) frame-by-frame.
- Calculates crop coordinates dynamically to keep the subject centered in the 9:16 aspect ratio.
- Offloads the actual cropping and rendering to FFmpeg for optimal resource usage.

## 3. `caption_trivia.py` (Value-Add Phase)
**Purpose:** Injects 'Transformative' educational content into the video.
**Technical Details:**
- Queries Wikipedia's API for the cartoon title to fetch the first two sentences of its entry.
- Summarizes the text into a bite-sized "Fun Fact".
- Uses FFmpeg to burn this text overlay onto the bottom 30% of the video.
- Implements a semi-transparent black background behind the text to guarantee readability.
- Outputs the fully processed `final_short.mp4`.

## 4. `yt_uploader.py` (Distribution Phase)
**Purpose:** Pushes the finalized content to YouTube via the official Data API v3.
**Technical Details:**
- Executes an OAuth2 flow for secure user authentication (`client_secrets.json`).
- Uploads `final_short.mp4` to the authenticated channel.
- Enforces strict metadata policies: Privacy status is set to 'private', category is set to 'Film & Animation' (Category ID 1), and `#Shorts` is dynamically appended to the title.
- Returns the direct YouTube Studio link for final manual review.
