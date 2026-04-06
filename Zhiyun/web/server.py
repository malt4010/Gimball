"""
Web dashboard for AI gimbal tracker.
"""
import asyncio
import json
import cv2
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

        self._setup_routes()

    def set_annotated_frame(self, frame):
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
                    msg = await ws.receive_text()
                    data = json.loads(msg)
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

                    elif action == "change_source":
                        source = data.get("source", "")
                        if source:
                            self.video.change_source(source)
                            await ws.send_json({"event": "source_changed",
                                                "source": source})

                    elif action == "set_framing":
                        self.offset_x = float(data.get("offset_x", 0))
                        self.offset_y = float(data.get("offset_y", -0.15))

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

    async def _generate_mjpeg(self):
        while True:
            frame = self._latest_annotated
            if frame is not None:
                _, buf = cv2.imencode(".jpg", frame,
                                      [cv2.IMWRITE_JPEG_QUALITY, 75])
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
