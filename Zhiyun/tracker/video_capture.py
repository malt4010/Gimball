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
        self._frame_full = None  # full resolution for clean feed
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._fps = 0.0
        self._frame_count = 0
        self._fps_start = time.monotonic()
        self._fps_count = 0

    @property
    def frame(self):
        """AI-sized frame (resized to width x height)."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def frame_full(self):
        """Full resolution frame (for OBS clean feed)."""
        with self._lock:
            return self._frame_full.copy() if self._frame_full is not None else None

    @property
    def fps(self):
        return self._fps

    def push_frame(self, frame):
        """Push a frame from an external source.

        frame: numpy array (BGR, any size)
        Stores both full-res and AI-resized versions.
        """
        with self._lock:
            self._frame_full = frame

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

    def change_source(self, new_source):
        """Switch to a different video source without restarting the system."""
        print(f"[VideoCapture] Switching to: {new_source}")
        self.stop()
        if isinstance(new_source, str) and new_source.isdigit():
            new_source = int(new_source)
        self.source = new_source
        self._frame = None
        self._fps = 0.0
        self._fps_count = 0
        self._frame_count = 0
        self._fps_start = time.monotonic()
        self._running = False
        self._thread = None
        self.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _capture_loop(self):
        source_str = str(self.source)

        # HTTP MJPEG streams (DroidCam, IP cameras)
        if source_str.startswith("http"):
            self._capture_mjpeg(source_str)
        else:
            self._capture_opencv(self.source)

    def _capture_opencv(self, source):
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            print(f"[VideoCapture] Failed to open: {source}")
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

    def _capture_mjpeg(self, url):
        """Read MJPEG stream from HTTP (DroidCam, IP cameras)."""
        import requests

        print(f"[VideoCapture] Connecting MJPEG: {url}")
        retries = 0

        while self._running:
            try:
                resp = requests.get(url, stream=True, timeout=5)
                if resp.status_code != 200:
                    print(f"[VideoCapture] HTTP {resp.status_code} from {url}")
                    time.sleep(2)
                    continue

                print(f"[VideoCapture] MJPEG connected: {url}")
                retries = 0
                buf = b""

                for chunk in resp.iter_content(8192):
                    if not self._running:
                        break
                    buf += chunk

                    # Prevent buffer from growing forever
                    if len(buf) > 500000:
                        start = buf.rfind(b"\xff\xd8")
                        buf = buf[start:] if start >= 0 else b""

                    # Find JPEG frames (FFD8 = start, FFD9 = end)
                    while True:
                        start = buf.find(b"\xff\xd8")
                        if start < 0:
                            break
                        end = buf.find(b"\xff\xd9", start + 2)
                        if end < 0:
                            break

                        jpg = buf[start:end + 2]
                        buf = buf[end + 2:]

                        arr = np.frombuffer(jpg, dtype=np.uint8)
                        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if frame is not None:
                            self.push_frame(frame)

                resp.close()
            except requests.exceptions.ConnectionError:
                retries += 1
                wait = min(retries * 2, 10)
                print(f"[VideoCapture] Can't reach {url}, retry in {wait}s...")
                time.sleep(wait)
            except Exception as e:
                print(f"[VideoCapture] Error: {e}, reconnecting...")
                time.sleep(2)
