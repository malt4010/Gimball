"""
Web server with live video dashboard and gimbal controls.

Provides a browser-based UI for:
- Viewing live camera feed with detection overlays
- Clicking to select tracking target
- Center/stop controls
- Connection status
"""
import asyncio
import json
import cv2
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles


class WebServer:
    """FastAPI-based web dashboard."""

    def __init__(self, tracker, gimbal_controller, host="0.0.0.0", port=8080):
        self.tracker = tracker
        self.gimbal = gimbal_controller
        self.host = host
        self.port = port
        self.app = FastAPI()
        self._latest_frame = None
        self._control_callbacks = {}

        self._setup_routes()

    def set_frame(self, frame):
        """Update the latest frame (called from main loop)."""
        self._latest_frame = frame

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
                        x = data.get("x", 0)
                        y = data.get("y", 0)
                        frame = self._latest_frame
                        if frame is not None:
                            success = self.tracker.select_target(x, y, frame)
                            await ws.send_json({"event": "target_selected", "success": success})

                    elif action == "stop_tracking":
                        self.tracker.stop_tracking()
                        if self.gimbal:
                            await self.gimbal.stop()
                        await ws.send_json({"event": "tracking_stopped"})

                    elif action == "center":
                        if self.gimbal:
                            await self.gimbal.stop()
                        await ws.send_json({"event": "centered"})

                    # Send periodic status updates
                    status = {
                        "event": "status",
                        "state": self.tracker.state,
                        "gimbal_connected": self.gimbal.connected if self.gimbal else False,
                        "target_bbox": self.tracker.target_bbox,
                    }
                    await ws.send_json(status)

            except WebSocketDisconnect:
                pass

    async def _generate_mjpeg(self):
        """Generate MJPEG stream from latest frames."""
        while True:
            if self._latest_frame is not None:
                _, buffer = cv2.imencode(".jpg", self._latest_frame,
                                         [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" +
                       buffer.tobytes() + b"\r\n")
            await asyncio.sleep(0.033)  # ~30 fps

    async def start(self):
        """Start the web server."""
        import uvicorn
        config = uvicorn.Config(self.app, host=self.host, port=self.port,
                                log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()
