"""
Web server with WebRTC camera input, live video dashboard, and gimbal controls.

The phone opens the web UI in its browser which:
1. Sends camera feed to Pi via WebRTC
2. Shows processed video (with detection overlays) back via MJPEG
3. Allows clicking on persons to track them
4. Provides gimbal controls (center, stop)

A separate device (laptop/tablet) can also open the UI for monitoring only.
"""
import asyncio
import json
import base64
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

        self._setup_routes()

    def set_annotated_frame(self, frame):
        """Set the latest AI-processed frame for streaming back."""
        self._latest_annotated = frame

    def _setup_routes(self):
        app = self.app

        @app.get("/")
        async def index():
            html_path = Path(__file__).parent / "static" / "index.html"
            return HTMLResponse(html_path.read_text())

        @app.get("/video_feed")
        async def video_feed():
            return StreamingResponse(
                self._generate_mjpeg(),
                media_type="multipart/x-mixed-replace; boundary=frame"
            )

        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await ws.accept()
            try:
                while True:
                    msg = await ws.receive()

                    # Binary = JPEG frame from phone camera
                    if "bytes" in msg and msg["bytes"]:
                        jpeg_bytes = msg["bytes"]
                        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if frame is not None:
                            self.video.push_frame(frame)
                            if self.video._frame_count <= 3:
                                h, w = frame.shape[:2]
                                print(f"[WebSocket] Receiving frames: {w}x{h}")
                        continue

                    # Text = JSON control message
                    if "text" in msg and msg["text"]:
                        data = json.loads(msg["text"])
                        action = data.get("action")

                        # Base64 JPEG frame (iOS Safari compatibility)
                        if action == "frame":
                            data_url = data.get("data", "")
                            # Strip "data:image/jpeg;base64," prefix
                            if "," in data_url:
                                b64 = data_url.split(",", 1)[1]
                                jpeg_bytes = base64.b64decode(b64)
                                arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                                if frame is not None:
                                    self.video.push_frame(frame)
                                    if self.video._frame_count <= 3:
                                        h, w = frame.shape[:2]
                                        print(f"[WebSocket] Receiving frames: {w}x{h}")
                            continue

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

                        elif action == "get_status":
                            await ws.send_json({
                                "event": "status",
                                "state": self.tracker.state,
                                "gimbal_connected": self.gimbal.connected if self.gimbal else False,
                                "target_bbox": self.tracker.target_bbox,
                                "fps": round(self.video.fps, 1),
                            })

            except WebSocketDisconnect:
                pass
            except Exception as e:
                print(f"[WebSocket] Error: {e}")

    async def _generate_mjpeg(self):
        while True:
            frame = self._latest_annotated
            if frame is not None:
                _, buf = cv2.imencode(".jpg", frame,
                                      [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" +
                       buf.tobytes() + b"\r\n")
            await asyncio.sleep(0.033)

    async def start(self):
        import uvicorn
        import ssl
        from pathlib import Path

        # Use HTTPS if cert files exist (required for camera access on phones)
        base = Path(__file__).parent.parent
        cert = base / "cert.pem"
        key = base / "key.pem"

        kwargs = {}
        if cert.exists() and key.exists():
            kwargs["ssl_certfile"] = str(cert)
            kwargs["ssl_keyfile"] = str(key)
            print(f"[Web] HTTPS enabled (self-signed cert)")
        else:
            print(f"[Web] HTTP only (no cert.pem/key.pem found)")

        config = uvicorn.Config(self.app, host=self.host, port=self.port,
                                log_level="warning", **kwargs)
        server = uvicorn.Server(config)
        await server.serve()
