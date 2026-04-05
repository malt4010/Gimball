"""
Main tracking module.

Combines person detection, object tracking (ByteTrack), and face
re-identification into a single pipeline with a target state machine.

States: IDLE → LOCKING → TRACKING → LOST → IDLE
"""
import time
import numpy as np
import cv2
from .detector import PersonDetector
from .face_db import FaceDB


class TargetState:
    IDLE = "idle"
    LOCKING = "locking"
    TRACKING = "tracking"
    LOST = "lost"


class PersonTracker:
    """Full tracking pipeline: detect → track → identify → follow."""

    def __init__(self, confidence=0.5, model_size="n", input_size=640,
                 lost_timeout=3.0):
        """
        confidence: YOLO detection confidence threshold
        model_size: YOLO model size ('n', 's', 'm')
        input_size: YOLO input resolution
        lost_timeout: seconds before giving up on lost target
        """
        self._detector = PersonDetector(model_size, confidence, input_size)
        self._face_db = FaceDB()
        self._state = TargetState.IDLE
        self._target_track_id = None
        self._target_bbox = None
        self._lost_since = None
        self._lost_timeout = lost_timeout
        self._frame_count = 0
        self._detections = []

        # Simple tracker state (track_id → bbox)
        self._tracks = {}
        self._next_track_id = 1

    @property
    def state(self):
        return self._state

    @property
    def target_bbox(self):
        """Current target bounding box or None."""
        return self._target_bbox

    @property
    def detections(self):
        """All current detections with track IDs."""
        return self._detections

    def select_target(self, click_x, click_y, frame):
        """User clicked at (click_x, click_y) to select a target.

        Finds the detection containing the click point and locks onto it.
        """
        for det in self._detections:
            x1, y1, x2, y2 = det["bbox"]
            if x1 <= click_x <= x2 and y1 <= click_y <= y2:
                # Try to get face embedding for this person
                embedding = self._face_db.get_embedding(frame, det["bbox"])
                if embedding is not None:
                    self._face_db.register(embedding)

                self._target_track_id = det.get("track_id")
                self._target_bbox = det["bbox"]
                self._state = TargetState.TRACKING
                return True

        return False

    def stop_tracking(self):
        """Stop tracking and return to idle."""
        self._state = TargetState.IDLE
        self._target_track_id = None
        self._target_bbox = None
        self._face_db.clear()

    def process_frame(self, frame):
        """Process a single frame through the full pipeline.

        Returns the annotated frame with bounding boxes drawn.
        """
        self._frame_count += 1
        h, w = frame.shape[:2]

        # Step 1: Detect all persons
        raw_dets = self._detector.detect(frame)

        # Step 2: Simple IoU-based tracking (assign track IDs)
        self._detections = self._assign_tracks(raw_dets)

        # Step 3: Update target state
        if self._state == TargetState.TRACKING:
            self._update_tracking(frame)
        elif self._state == TargetState.LOST:
            self._update_lost(frame)

        # Step 4: Draw overlays
        annotated = self._draw_overlays(frame.copy())

        return annotated

    def _assign_tracks(self, detections):
        """Simple IoU tracker - assign persistent IDs to detections."""
        new_tracks = {}
        assigned = []

        for det in detections:
            best_id = None
            best_iou = 0.3  # minimum IoU to match

            for tid, prev_bbox in self._tracks.items():
                iou = self._compute_iou(det["bbox"], prev_bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_id = tid

            if best_id is not None:
                det["track_id"] = best_id
            else:
                det["track_id"] = self._next_track_id
                self._next_track_id += 1

            new_tracks[det["track_id"]] = det["bbox"]
            assigned.append(det)

        self._tracks = new_tracks
        return assigned

    def _update_tracking(self, frame):
        """Update while actively tracking a target."""
        # Find our target by track_id
        target_det = None
        for det in self._detections:
            if det.get("track_id") == self._target_track_id:
                target_det = det
                break

        if target_det:
            self._target_bbox = target_det["bbox"]
            self._lost_since = None

            # Auto-update face DB every few frames
            if self._frame_count % 10 == 0:
                emb = self._face_db.get_embedding(frame, self._target_bbox)
                self._face_db.auto_update(emb)
        else:
            # Track ID lost - try face re-identification
            if self._face_db.has_target:
                best_det = None
                best_sim = 0.0
                for det in self._detections:
                    emb = self._face_db.get_embedding(frame, det["bbox"])
                    is_match, sim = self._face_db.match(emb)
                    if is_match and sim > best_sim:
                        best_sim = sim
                        best_det = det

                if best_det:
                    self._target_track_id = best_det["track_id"]
                    self._target_bbox = best_det["bbox"]
                    return

            # No match found - enter LOST state
            self._state = TargetState.LOST
            self._lost_since = time.monotonic()

    def _update_lost(self, frame):
        """Update while target is lost."""
        # Try face re-identification on all detections
        if self._face_db.has_target:
            for det in self._detections:
                emb = self._face_db.get_embedding(frame, det["bbox"])
                is_match, sim = self._face_db.match(emb)
                if is_match:
                    self._target_track_id = det["track_id"]
                    self._target_bbox = det["bbox"]
                    self._state = TargetState.TRACKING
                    self._lost_since = None
                    return

        # Timeout
        if self._lost_since and time.monotonic() - self._lost_since > self._lost_timeout:
            self._target_bbox = None

    def _draw_overlays(self, frame):
        """Draw bounding boxes and status on frame."""
        for det in self._detections:
            x1, y1, x2, y2 = det["bbox"]
            is_target = det.get("track_id") == self._target_track_id

            if is_target and self._state == TargetState.TRACKING:
                color = (0, 255, 0)  # green for active target
                thickness = 3
            elif is_target and self._state == TargetState.LOST:
                color = (0, 165, 255)  # orange for lost
                thickness = 2
            else:
                color = (128, 128, 128)  # gray for others
                thickness = 1

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
            tid = det.get("track_id", "?")
            cv2.putText(frame, f"#{tid}", (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # Status text
        status_color = {
            TargetState.IDLE: (200, 200, 200),
            TargetState.TRACKING: (0, 255, 0),
            TargetState.LOST: (0, 165, 255),
            TargetState.LOCKING: (255, 255, 0),
        }.get(self._state, (255, 255, 255))

        cv2.putText(frame, f"State: {self._state.upper()}",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

        return frame

    @staticmethod
    def _compute_iou(box1, box2):
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - inter
        return inter / union if union > 0 else 0
