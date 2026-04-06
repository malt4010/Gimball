"""
AI Person Tracking Gimbal System

Architecture:
  Phone (DroidCam/NDI) → WiFi video stream
  Pi/Mac captures stream via OpenCV → YOLOv8 detection → PID → Gimbal BLE
  Web dashboard for monitoring + target selection (any browser)
  OBS captures same DroidCam/NDI stream independently (clean, 0 latency)

Usage:
  python main.py --source http://PHONE_IP:4747/video   # DroidCam
  python main.py --source 0                             # local webcam
  python main.py --source rtsp://...                    # RTSP stream
  python main.py --no-gimbal                            # test without gimbal
"""
import asyncio
import argparse
import signal

from tracker.video_capture import VideoCapture
from tracker.tracker import PersonTracker, TargetState
from controller.gimbal_ble import ZhiyunGimbal
from controller.pid import PIDController
from web.server import WebServer


async def main(args):
    print("[INIT] Starting AI Tracking Gimbal System...")

    # Video capture from DroidCam / webcam / RTSP
    source = args.source
    if source.isdigit():
        source = int(source)
    video = VideoCapture(source=source, width=args.width, height=args.height)
    video.start()
    print(f"[INIT] Video source: {args.source}")

    # Wait for first frame
    for _ in range(100):
        if video.frame is not None:
            break
        await asyncio.sleep(0.1)

    if video.frame is None:
        print("[ERROR] No video! Check that DroidCam is running and URL is correct.")
        print(f"  Tried: {args.source}")
        print(f"  DroidCam URL format: http://PHONE_IP:4747/video")
        video.stop()
        return

    h, w = video.frame.shape[:2]
    print(f"[INIT] Video OK: {w}x{h} @ {video.fps:.0f} FPS")

    # Person tracker
    tracker = PersonTracker(
        confidence=args.confidence,
        model_size=args.model,
        input_size=args.yolo_size,
    )
    print(f"[INIT] YOLOv8{args.model} @ {args.yolo_size}px")

    # Gimbal
    gimbal = ZhiyunGimbal()
    if not args.no_gimbal:
        print("[INIT] Connecting to gimbal...")
        if await gimbal.connect():
            print("[INIT] Gimbal connected!")
        else:
            print("[WARN] Gimbal not found")
    else:
        print("[INIT] Gimbal disabled")

    # PID controller
    pid = PIDController(
        kp=args.pid_p, ki=args.pid_i, kd=args.pid_d,
        dead_zone=args.dead_zone, smoothing=args.smoothing,
    )

    # Web dashboard
    web = WebServer(tracker, gimbal, video, port=args.port)
    print(f"[INIT] Dashboard: https://0.0.0.0:{args.port}")

    web_task = asyncio.create_task(web.start())

    print("[RUN] Ready. Open dashboard to select target.")

    running = True
    def stop(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        while running:
            frame = video.frame
            if frame is None:
                await asyncio.sleep(0.01)
                continue

            # AI detection + tracking
            annotated = tracker.process_frame(frame)
            web.set_annotated_frame(annotated)

            # Gimbal control with framing offset and axis locks
            if tracker.state == TargetState.TRACKING and tracker.target_bbox:
                x1, y1, x2, y2 = tracker.target_bbox
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                h, w = frame.shape[:2]

                # Apply framing offset: shift the "target center" in the frame
                # offset_x > 0 means person should be right of center
                # offset_y < 0 means person should be above center (headroom)
                target_cx = w * (0.5 + web.offset_x)
                target_cy = h * (0.5 + web.offset_y)

                pan, tilt = pid.update(cx, cy, w, h,
                                       target_x=target_cx,
                                       target_y=target_cy)

                # Apply axis locks
                if web.lock_pan:
                    pan = 0.0
                if web.lock_tilt:
                    tilt = 0.0

                # Store for dashboard arrows
                web.gimbal_pan = pan
                web.gimbal_tilt = tilt

                if gimbal.connected:
                    await gimbal.move(tilt=tilt, pan=pan)

            elif tracker.state in (TargetState.LOST, TargetState.IDLE):
                web.gimbal_pan = 0.0
                web.gimbal_tilt = 0.0
                if gimbal.connected:
                    await gimbal.stop()
                pid.reset()

            await asyncio.sleep(0.02)

    finally:
        print("\n[SHUTDOWN]")
        video.stop()
        if gimbal.connected:
            await gimbal.stop()
            await gimbal.disconnect()
        web_task.cancel()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="AI Person Tracking Gimbal")
    p.add_argument("--source", default="0",
                   help="DroidCam URL (http://IP:4747/video), camera index, or RTSP URL")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--model", default="n", choices=["n", "s", "m"])
    p.add_argument("--yolo-size", type=int, default=320)
    p.add_argument("--confidence", type=float, default=0.5)
    p.add_argument("--no-gimbal", action="store_true")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--pid-p", type=float, default=2.0)
    p.add_argument("--pid-i", type=float, default=0.0)
    p.add_argument("--pid-d", type=float, default=0.5)
    p.add_argument("--dead-zone", type=float, default=0.05)
    p.add_argument("--smoothing", type=float, default=0.3)

    asyncio.run(main(p.parse_args()))
