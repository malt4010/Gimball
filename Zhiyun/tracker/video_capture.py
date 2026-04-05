"""
Video capture from WebRTC (phone browser), RTSP, or local camera.

Supports two modes:
1. WebRTC: Phone sends camera via browser, frames arrive via callback
2. OpenCV: Local camera or RTSP/NDI stream via cv2.VideoCapture

Thread-safe - always provides the latest frame.
"""
import threading
import time
import cv2
import numpy as np


class VideoCapture:
    """Thread-safe video capture that always provides the latest frame."""

    def __init__(self, source=None, width=640, height=480):
        """
        source: camera index (0), RTSP URL, or None for WebRTC-only mode
        width, height: resize target for AI processing
        """
        self.source = source
        self.width = width
        self.height = height
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._fps = 0.0
        self._frame_count = 0
        self._fps_start = time.monotonic()
        self._fps_count = 0

    @property
    def frame(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def fps(self):
        return self._fps

    def push_frame(self, frame):
        """Push a frame from an external source (WebRTC).

        frame: numpy array (BGR, any size - will be resized)
        """
        h, w = frame.shape[:2]
        if w != self.width or h != self.height:
            frame = cv2.resize(frame, (self.width, self.height))

        with self._lock:
            self._frame = frame

        self._fps_count += 1
        elapsed = time.monotonic() - self._fps_start
        if elapsed >= 1.0:
            self._fps = self._fps_count / elapsed
            self._fps_count = 0
            self._fps_start = time.monotonic()

    def start(self):
        """Start OpenCV capture in background thread (not needed for WebRTC)."""
        if self.source is None:
            return  # WebRTC mode - frames come via push_frame
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _capture_loop(self):
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            print(f"[VideoCapture] Failed to open: {self.source}")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            self.push_frame(frame)

        cap.release()
