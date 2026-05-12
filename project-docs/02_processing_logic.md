# Processing Logic & Hardware Optimization

This document outlines the architectural decisions made to ensure the video processing pipeline runs efficiently on resource-constrained hardware, specifically an Intel Core i3-3220 CPU and 8GB of RAM.

## The Challenge
The system operates on an older, 3rd-generation Intel Core i3 processor (2 cores, 4 threads) without a dedicated, high-performance GPU for compute acceleration. Advanced computer vision models (like YOLO, MTCNN, or standard DNN-based face detectors) require significant computational power and would severely bottleneck the pipeline or cause memory out-of-bounds errors on this hardware.

## 1. Why Haar Cascades?
To perform smart cropping (converting 16:9 to 9:16), the system needs to track the subject within the video. We selected **Haar Cascades** (via OpenCV) for the following reasons:
- **Low Computational Overhead:** Haar Cascades rely on simple edge/line feature detection (Haar-like features) rather than complex matrix multiplications typical of deep neural networks.
- **CPU Efficiency:** They are exceptionally fast on standard CPUs, making them ideal for the i3-3220.
- **Sufficient Accuracy:** For cartoons and animated content, where features are often exaggerated and distinct, Haar Cascades provide 'good enough' accuracy to keep the primary subject in frame without over-engineering the tracking logic.

## 2. Why FFmpeg Subprocesses?
Video encoding and decoding are memory-intensive operations. Rather than using native Python libraries that might load entire frames into memory:
- **C-Optimized Execution:** FFmpeg is written in C and highly optimized for stream processing. Triggering it via Python subprocesses allows the heavy lifting to happen outside the Python Global Interpreter Lock (GIL).
- **Stream Processing:** FFmpeg processes video frame-by-frame or in chunks, drastically reducing the RAM footprint. This ensures the 8GB RAM limit is never exceeded, preventing system swaps or crashes.
- **Fast Text Rendering:** Burning the Wikipedia trivia captions (`caption_trivia.py`) using FFmpeg's `drawtext` filter is highly performant and eliminates the need to manipulate individual image frames in Python.
