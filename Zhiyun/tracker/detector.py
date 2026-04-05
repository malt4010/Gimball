"""
Person detection using YOLOv8n.

Detects all people in a frame and returns bounding boxes.
Optimized for Raspberry Pi with small input size.
"""
import numpy as np


class PersonDetector:
    """YOLOv8n person detector."""

    def __init__(self, model_size="n", confidence=0.5, input_size=640):
        """
        model_size: 'n' (nano, fastest), 's' (small), 'm' (medium)
        confidence: minimum detection confidence
        input_size: YOLO input resolution (320=fastest, 640=default)
        """
        from ultralytics import YOLO
        self._model = YOLO(f"yolov8{model_size}.pt")
        self._confidence = confidence
        self._input_size = input_size

    def detect(self, frame):
        """Detect persons in frame.

        Returns list of dicts: [{"bbox": (x1,y1,x2,y2), "confidence": float}, ...]
        Coordinates are in pixel space of the input frame.
        """
        results = self._model(
            frame,
            conf=self._confidence,
            classes=[0],  # class 0 = person in COCO
            imgsz=self._input_size,
            verbose=False,
        )

        detections = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0])
                detections.append({
                    "bbox": (int(x1), int(y1), int(x2), int(y2)),
                    "confidence": conf,
                })

        return detections
