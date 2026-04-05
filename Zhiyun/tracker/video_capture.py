"""
Video capture from NDI, RTSP, or local camera.

Runs in a separate thread to keep frames fresh and minimize latency.
"""
import threading
import time
import cv2


class VideoCapture:
    """Thread-safe video capture that always provides the latest frame."""

    def __init__(self, source=0, width=640, height=480):
        """
        source: camera index (0), RTSP URL, or NDI URL
                For NDI via FFmpeg: "udp://@:5000" or similar
        width, height: resize target for processing (lower = faster AI)
        """
        self.source = source
        self.width = width
        self.height = height
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._fps = 0
        self._frame_count = 0

    @property
    def frame(self):
        """Get the latest frame (may be None if not started)."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def fps(self):
        return self._fps

    def start(self):
        """Start capturing in background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop capturing."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _capture_loop(self):
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            print(f"[VideoCapture] Failed to open: {self.source}")
            return

        # Try to set camera resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # minimize buffer latency

        fps_start = time.monotonic()
        fps_count = 0

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            # Resize if needed
            h, w = frame.shape[:2]
            if w != self.width or h != self.height:
                frame = cv2.resize(frame, (self.width, self.height))

            with self._lock:
                self._frame = frame

            self._frame_count += 1
            fps_count += 1
            elapsed = time.monotonic() - fps_start
            if elapsed >= 1.0:
                self._fps = fps_count / elapsed
                fps_count = 0
                fps_start = time.monotonic()

        cap.release()
