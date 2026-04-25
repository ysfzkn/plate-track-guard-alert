"""Module 2: Intrusion / unauthorized person detection.

Multi-camera orchestration, person detection (YOLOv8 + ByteTrack),
zone-based classification (point-in-polygon), night-mode windowing,
loitering logic, video clip recording, and event commit pipeline.

Wired into the FastAPI app via main.py when ENABLE_INTRUSION_MODULE=true.
"""
