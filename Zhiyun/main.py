"""
AI Person Tracking Gimbal System - Main Entry Point

Connects all components:
  Video Capture → Person Tracker → PID Controller → Gimbal BLE
  Web Server for remote monitoring and control

Usage:
  python main.py                    # webcam input (for testing)
  python main.py --source rtsp://.. # RTSP/NDI stream
  python main.py --no-gimbal        # test without gimbal hardware
"""
import asyncio
import argparse
import signal
import sys

from tracker.video_capture import VideoCapture
from tracker.tracker import PersonTracker, TargetState
from controller.gimbal_ble import ZhiyunGimbal
from controller.pid import PIDController
from web.server import WebServer


async def main(args):
    # --- Initialize components ---
    print("[INIT] Starting AI Tracking Gimbal System...")

    # Video capture
    if args.source == "webrtc":
        video = VideoCapture(source=None, width=args.width, height=args.height)
        print(f"[INIT] Video: WebRTC (waiting for phone camera via browser)")
    else:
        source = int(args.source) if args.source.isdigit() else args.source
        video = VideoCapture(source=source, width=args.width, height=args.height)
        video.start()
        print(f"[INIT] Video capture: {args.source} ({args.width}x{args.height})")

        for _ in range(50):
            if video.frame is not None:
                break
            await asyncio.sleep(0.1)

        if video.frame is None:
            print("[ERROR] No video frames received!")
            video.stop()
            return

    print(f"[INIT] Video ready")

    # Person tracker
    tracker = PersonTracker(
        confidence=args.confidence,
        model_size=args.model,
        input_size=args.yolo_size,
    )
    print(f"[INIT] Tracker: YOLOv8{args.model} @ {args.yolo_size}px")

    # Gimbal
    gimbal = ZhiyunGimbal()
    if not args.no_gimbal:
        print("[INIT] Connecting to gimbal...")
        if await gimbal.connect():
            print("[INIT] Gimbal connected!")
        else:
            print("[WARN] Gimbal not found - running without gimbal")
    else:
        print("[INIT] Gimbal disabled (--no-gimbal)")

    # PID controller
    pid = PIDController(
        kp=args.pid_p,
        ki=args.pid_i,
        kd=args.pid_d,
        dead_zone=args.dead_zone,
        smoothing=args.smoothing,
    )

    # Web server
    web = WebServer(tracker, gimbal, video, port=args.port)
    print(f"[INIT] Web UI: http://0.0.0.0:{args.port}")

    # Start web server in background
    web_task = asyncio.create_task(web.start())

    # --- Main loop ---
    print("[RUN] System running. Open web UI to select target.")

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

            # Run detection + tracking
            annotated = tracker.process_frame(frame)

            # Update web UI frame
            web.set_frame(annotated)

            # Gimbal control based on tracker state
            if tracker.state == TargetState.TRACKING and tracker.target_bbox:
                x1, y1, x2, y2 = tracker.target_bbox
                target_cx = (x1 + x2) / 2
                target_cy = (y1 + y2) / 2
                h, w = frame.shape[:2]

                pan, tilt = pid.update(target_cx, target_cy, w, h)

                if gimbal.connected:
                    await gimbal.move(tilt=tilt, pan=pan)

            elif tracker.state in (TargetState.LOST, TargetState.IDLE):
                # Stop gimbal when not tracking
                if gimbal.connected:
                    await gimbal.stop()
                pid.reset()

            await asyncio.sleep(0.02)  # ~50 Hz main loop

    finally:
        print("\n[SHUTDOWN] Stopping...")
        video.stop()
        if gimbal.connected:
            await gimbal.stop()
            await gimbal.disconnect()
        web_task.cancel()
        print("[SHUTDOWN] Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Person Tracking Gimbal")
    parser.add_argument("--source", default="webrtc", help="Video source: 'webrtc' (phone browser), camera index, or RTSP URL")
    parser.add_argument("--width", type=int, default=640, help="Frame width")
    parser.add_argument("--height", type=int, default=480, help="Frame height")
    parser.add_argument("--model", default="n", choices=["n", "s", "m"], help="YOLO model size")
    parser.add_argument("--yolo-size", type=int, default=640, help="YOLO input size")
    parser.add_argument("--confidence", type=float, default=0.5, help="Detection confidence")
    parser.add_argument("--no-gimbal", action="store_true", help="Run without gimbal")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port")
    parser.add_argument("--pid-p", type=float, default=2.0, help="PID proportional gain")
    parser.add_argument("--pid-i", type=float, default=0.0, help="PID integral gain")
    parser.add_argument("--pid-d", type=float, default=0.5, help="PID derivative gain")
    parser.add_argument("--dead-zone", type=float, default=0.05, help="PID dead zone")
    parser.add_argument("--smoothing", type=float, default=0.3, help="Output smoothing")

    asyncio.run(main(parser.parse_args()))
