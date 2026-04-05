"""
Face embedding database for target re-identification.

Stores face embeddings of the tracked target and matches against
new detections using cosine similarity. Auto-updates with new
embeddings from different angles/lighting over time.
"""
import time
import numpy as np


class FaceDB:
    """In-memory face embedding database for a single target."""

    def __init__(self, max_embeddings=50, match_threshold=0.4, update_interval=2.0,
                 min_face_size=40):
        """
        max_embeddings: maximum stored embeddings per target (FIFO)
        match_threshold: cosine similarity threshold for positive match
        update_interval: seconds between auto-capturing new embeddings
        min_face_size: minimum face bbox width in pixels to accept
        """
        self.max_embeddings = max_embeddings
        self.match_threshold = match_threshold
        self.update_interval = update_interval
        self.min_face_size = min_face_size

        self._embeddings = []  # list of np.ndarray (512-dim)
        self._last_update = 0
        self._face_model = None

    def _load_model(self):
        """Lazy-load face recognition model."""
        if self._face_model is not None:
            return
        try:
            from insightface.app import FaceAnalysis
            self._face_model = FaceAnalysis(
                name="buffalo_sc",  # small model, good for Pi
                providers=["CPUExecutionProvider"],
            )
            self._face_model.prepare(ctx_id=-1, det_size=(160, 160))
        except ImportError:
            print("[FaceDB] insightface not installed, face recognition disabled")

    def get_embedding(self, frame, bbox):
        """Extract face embedding from a person crop.

        frame: full frame (BGR)
        bbox: (x1, y1, x2, y2) of the person

        Returns embedding (np.ndarray) or None if no face found.
        """
        self._load_model()
        if self._face_model is None:
            return None

        x1, y1, x2, y2 = bbox
        # Expand crop slightly for better face detection
        h, w = frame.shape[:2]
        pad_x = int((x2 - x1) * 0.1)
        pad_y = int((y2 - y1) * 0.1)
        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_y)
        cx2 = min(w, x2 + pad_x)
        cy2 = min(h, y2 + pad_y)
        crop = frame[cy1:cy2, cx1:cx2]

        if crop.shape[0] < self.min_face_size or crop.shape[1] < self.min_face_size:
            return None

        faces = self._face_model.get(crop)
        if not faces:
            return None

        # Return embedding of the largest face in the crop
        largest = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        return largest.embedding

    def register(self, embedding):
        """Register initial target embedding."""
        self._embeddings = [embedding]
        self._last_update = time.monotonic()

    def clear(self):
        """Clear all stored embeddings."""
        self._embeddings = []

    @property
    def has_target(self):
        return len(self._embeddings) > 0

    def match(self, embedding):
        """Check if embedding matches the stored target.

        Returns (is_match: bool, similarity: float).
        """
        if not self._embeddings or embedding is None:
            return False, 0.0

        # Cosine similarity against all stored embeddings, take best
        best_sim = 0.0
        emb_norm = embedding / (np.linalg.norm(embedding) + 1e-8)
        for stored in self._embeddings:
            stored_norm = stored / (np.linalg.norm(stored) + 1e-8)
            sim = float(np.dot(emb_norm, stored_norm))
            best_sim = max(best_sim, sim)

        return best_sim >= self.match_threshold, best_sim

    def auto_update(self, embedding):
        """Add a new embedding if enough time has passed since last update.

        Call this every frame while tracking. It will only store a new
        embedding every `update_interval` seconds.
        """
        if embedding is None:
            return

        now = time.monotonic()
        if now - self._last_update < self.update_interval:
            return

        # Only add if it's sufficiently different from existing ones
        # (avoid storing near-duplicates)
        _, sim = self.match(embedding)
        if sim < 0.95:  # different enough to be worth storing
            self._embeddings.append(embedding)
            if len(self._embeddings) > self.max_embeddings:
                self._embeddings.pop(0)  # FIFO

        self._last_update = now
