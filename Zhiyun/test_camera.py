"""Minimal test: phone camera → WebSocket → server."""
import asyncio
import json
import base64
import ssl
from pathlib import Path
import numpy as np
import cv2
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

app = FastAPI()

HTML = """<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Camera Test</title>
</head>
<body style="background:#111;color:#eee;font-family:sans-serif;padding:20px;">
<h2>Camera Test</h2>
<p id="status">Loading...</p>
<video id="vid" autoplay playsinline muted style="width:100%;max-height:40vh;background:#333;"></video>
<br><br>
<button onclick="startCam()" style="padding:12px 24px;font-size:16px;">Start Camera</button>
<p id="log" style="font-family:monospace;font-size:12px;color:#888;white-space:pre-wrap;"></p>

<canvas id="c" style="display:none;"></canvas>

<script>
const vid = document.getElementById('vid');
const canvas = document.getElementById('c');
const status = document.getElementById('status');
const logEl = document.getElementById('log');
let ws = null;
let sending = false;

function log(msg) {
    logEl.textContent += msg + '\\n';
    console.log(msg);
}

// Step 1: Connect WebSocket
const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
log('Connecting WebSocket: ' + proto + '//' + location.host + '/ws');

ws = new WebSocket(proto + '//' + location.host + '/ws');

ws.onopen = () => {
    log('WebSocket OPEN');
    status.textContent = 'WebSocket connected. Press Start Camera.';
    status.style.color = '#4f4';
};
ws.onerror = (e) => {
    log('WebSocket ERROR: ' + JSON.stringify(e));
    status.textContent = 'WebSocket error!';
    status.style.color = '#f44';
};
ws.onclose = (e) => {
    log('WebSocket CLOSED code=' + e.code + ' reason=' + e.reason);
    status.textContent = 'WebSocket closed';
    status.style.color = '#f84';
};
ws.onmessage = (e) => {
    log('Server says: ' + e.data);
};

// Step 2: Start camera
async function startCam() {
    log('Requesting camera...');
    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: 'environment', width: {ideal:640}, height: {ideal:480} },
            audio: false
        });
        vid.srcObject = stream;
        await vid.play();
        log('Camera playing: ' + vid.videoWidth + 'x' + vid.videoHeight);

        // Wait for actual frames
        await new Promise(r => {
            const check = () => vid.videoWidth > 0 ? r() : requestAnimationFrame(check);
            check();
        });

        canvas.width = vid.videoWidth;
        canvas.height = vid.videoHeight;
        log('Canvas: ' + canvas.width + 'x' + canvas.height);
        status.textContent = 'Camera active. Sending frames...';

        // Step 3: Send frames
        const ctx = canvas.getContext('2d');
        let count = 0;

        function send() {
            if (!ws || ws.readyState !== 1) {
                log('WS not ready, state=' + (ws ? ws.readyState : 'null'));
                setTimeout(send, 500);
                return;
            }
            ctx.drawImage(vid, 0, 0);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.5);
            ws.send(JSON.stringify({action:'frame', data:dataUrl}));
            count++;
            if (count <= 5 || count % 20 === 0) {
                log('Sent frame #' + count);
            }
            status.textContent = 'Frames sent: ' + count;
            setTimeout(send, 150);
        }
        send();

    } catch(err) {
        log('Camera error: ' + err.name + ': ' + err.message);
        status.textContent = 'Camera failed: ' + err.message;
        status.style.color = '#f44';
    }
}
</script>
</body></html>
"""

frame_count = 0

@app.get("/")
async def index():
    return HTMLResponse(HTML)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    global frame_count
    await ws.accept()
    print("[WS] Client connected")
    await ws.send_text("Hello from server!")

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("action") == "frame":
                data_url = data.get("data", "")
                if "," in data_url:
                    b64 = data_url.split(",", 1)[1]
                    jpg = base64.b64decode(b64)
                    arr = np.frombuffer(jpg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        frame_count += 1
                        if frame_count <= 5 or frame_count % 20 == 0:
                            h, w = frame.shape[:2]
                            print(f"[SERVER] Frame #{frame_count}: {w}x{h} ({len(jpg)} bytes)")
                        await ws.send_text(f"Got frame #{frame_count}")
    except Exception as e:
        print(f"[WS] Disconnected: {e}")

if __name__ == "__main__":
    import uvicorn
    cert = Path(__file__).parent / "cert.pem"
    key = Path(__file__).parent / "key.pem"
    kwargs = {}
    if cert.exists():
        kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        print("HTTPS enabled")
    uvicorn.run(app, host="0.0.0.0", port=8080, **kwargs)
