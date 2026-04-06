"""
Web dashboard for AI gimbal tracker.
"""
import asyncio
import json
import cv2
import numpy as np
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse


class WebServer:
    def __init__(self, tracker, gimbal_controller, video_capture,
                 host="0.0.0.0", port=8080):
        self.tracker = tracker
        self.gimbal = gimbal_controller
        self.video = video_capture
        self.host = host
        self.port = port
        self.app = FastAPI()
        self._latest_annotated = None

        # Framing offset (0 = center, -0.5..0.5)
        self.offset_x = 0.0
        self.offset_y = -0.15  # default: slight headroom

        # Axis locks
        self.lock_pan = False
        self.lock_tilt = False

        # Current gimbal output (for dashboard arrows)
        self.gimbal_pan = 0.0
        self.gimbal_tilt = 0.0

        # PID tuning (set from dashboard)
        self.pid_controller = None

        self._setup_routes()

    def set_annotated_frame(self, frame):
        self._latest_annotated = frame

    def _setup_routes(self):
        app = self.app

        @app.get("/")
        async def index():
            html_path = Path(__file__).parent / "static" / "index.html"
            return HTMLResponse(html_path.read_text())

        @app.get("/camera")
        async def camera():
            """Dedicated camera page for phone - sends video feed only."""
            html_path = Path(__file__).parent / "static" / "camera.html"
            return HTMLResponse(html_path.read_text())

        @app.get("/video_feed")
        async def video_feed():
            """Processed feed with detection overlays (for dashboard)."""
            return StreamingResponse(
                self._generate_mjpeg(annotated=True),
                media_type="multipart/x-mixed-replace; boundary=frame"
            )

        @app.get("/clean_feed")
        async def clean_feed():
            """Clean feed without overlays (for OBS / livestream)."""
            return StreamingResponse(
                self._generate_mjpeg(annotated=False),
                media_type="multipart/x-mixed-replace; boundary=frame"
            )

        @app.websocket("/ws/camera")
        async def camera_ws(ws: WebSocket):
            """Dedicated WebSocket for camera frames (binary JPEG)."""
            await ws.accept()
            print("[WS] Camera connected")
            try:
                while True:
                    data = await ws.receive_bytes()
                    arr = np.frombuffer(data, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        self.video.push_frame(frame)
                        if self.video._frame_count <= 3:
                            h, w = frame.shape[:2]
                            print(f"[WS] Camera frame: {w}x{h} ({len(data)}B)")
            except WebSocketDisconnect:
                print("[WS] Camera disconnected")
            except Exception as e:
                print(f"[WS] Camera error: {e}")

        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            """WebSocket for dashboard control messages (JSON only)."""
            await ws.accept()
            try:
                while True:
                    data = json.loads(await ws.receive_text())
                    action = data.get("action")

                    if action == "select_target":
                        x, y = data.get("x", 0), data.get("y", 0)
                        frame = self.video.frame
                        if frame is not None:
                            success = self.tracker.select_target(x, y, frame)
                            await ws.send_json({"event": "target_selected",
                                                "success": success})

                    elif action == "stop_tracking":
                        self.tracker.stop_tracking()
                        if self.gimbal:
                            await self.gimbal.stop()
                        await ws.send_json({"event": "tracking_stopped"})

                    elif action == "center":
                        if self.gimbal:
                            await self.gimbal.stop()
                        await ws.send_json({"event": "centered"})

                    elif action == "connect_gimbal":
                        if self.gimbal:
                            if self.gimbal.connected:
                                await self.gimbal.disconnect()
                                await ws.send_json({"event": "gimbal_status",
                                                    "message": "Disconnected"})
                            else:
                                success = await self.gimbal.connect()
                                msg = "Connected!" if success else "Not found"
                                await ws.send_json({"event": "gimbal_status",
                                                    "message": msg})

                    elif action == "change_source":
                        source = data.get("source", "")
                        if source == "websocket":
                            self.video.stop()
                            self.video.source = None
                            await ws.send_json({"event": "source_changed",
                                                "source": "Phone Camera"})
                        elif source:
                            self.video.change_source(source)
                            await ws.send_json({"event": "source_changed",
                                                "source": source})

                    elif action == "set_framing":
                        self.offset_x = float(data.get("offset_x", 0))
                        self.offset_y = float(data.get("offset_y", -0.15))

                    elif action == "set_pid":
                        if self.pid_controller:
                            self.pid_controller.kp = float(data.get("kp", 1.5))
                            self.pid_controller.kd = float(data.get("kd", 0.8))
                            self.pid_controller.smoothing = float(data.get("smoothing", 0.3))
                        self.max_speed = float(data.get("max_speed", 0.4))

                    elif action == "set_locks":
                        self.lock_pan = bool(data.get("lock_pan", False))
                        self.lock_tilt = bool(data.get("lock_tilt", False))

                    elif action == "get_status":
                        await ws.send_json({
                            "event": "status",
                            "state": self.tracker.state,
                            "gimbal_connected": self.gimbal.connected if self.gimbal else False,
                            "fps": round(self.video.fps, 1),
                            "detections": len(self.tracker.detections),
                            "source": str(self.video.source or "none"),
                            "gimbal_pan": round(self.gimbal_pan, 3),
                            "gimbal_tilt": round(self.gimbal_tilt, 3),
                        })

            except WebSocketDisconnect:
                pass
            except Exception:
                pass

    def set_clean_frame(self, frame):
        """Set the latest clean frame (no overlays) for OBS."""
        self._latest_clean = frame

    async def _generate_mjpeg(self, annotated=True):
        while True:
            frame = self._latest_annotated if annotated else getattr(self, '_latest_clean', None)
            if frame is not None:
                quality = 75 if annotated else 90  # higher quality for OBS
                _, buf = cv2.imencode(".jpg", frame,
                                      [cv2.IMWRITE_JPEG_QUALITY, quality])
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" +
                       buf.tobytes() + b"\r\n")
            await asyncio.sleep(0.033)

    async def start(self):
        import uvicorn
        base = Path(__file__).parent.parent
        cert, key = base / "cert.pem", base / "key.pem"
        kwargs = {}
        if cert.exists() and key.exists():
            kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
            print("[Web] HTTPS enabled")
        config = uvicorn.Config(self.app, host=self.host, port=self.port,
                                log_level="warning", **kwargs)
        await uvicorn.Server(config).serve()
