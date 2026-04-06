"""
PID Controller for gimbal tracking.

Converts target position error (how far the target is from frame center)
into gimbal movement commands (tilt/pan speed from -1 to 1).
"""
import time


class PIDController:
    """Dual-axis PID controller for pan and tilt."""

    def __init__(self, kp=2.0, ki=0.0, kd=0.5, dead_zone=0.05, smoothing=0.3):
        """
        kp: Proportional gain - how aggressively to chase the target
        ki: Integral gain - correct steady-state error (usually 0 for gimbal)
        kd: Derivative gain - dampen oscillation
        dead_zone: Ignore errors smaller than this (fraction of frame, 0.05 = 5%)
        smoothing: Exponential moving average factor (0=no smoothing, 1=full smoothing)
        """
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.dead_zone = dead_zone
        self.smoothing = smoothing

        # Per-axis state
        self._prev_error = {"pan": 0.0, "tilt": 0.0}
        self._integral = {"pan": 0.0, "tilt": 0.0}
        self._prev_output = {"pan": 0.0, "tilt": 0.0}
        self._prev_time = None

    def reset(self):
        """Reset controller state."""
        self._prev_error = {"pan": 0.0, "tilt": 0.0}
        self._integral = {"pan": 0.0, "tilt": 0.0}
        self._prev_output = {"pan": 0.0, "tilt": 0.0}
        self._prev_time = None

    def update(self, person_x, person_y, frame_w, frame_h,
               target_x=None, target_y=None):
        """Compute gimbal commands from target position in frame.

        person_x, person_y: center of detected person bounding box in pixels
        frame_w, frame_h: frame dimensions in pixels
        target_x, target_y: desired position in frame (default: frame center)

        Returns (pan, tilt) each in range -1.0 to 1.0
        """
        now = time.monotonic()
        dt = (now - self._prev_time) if self._prev_time else 0.02
        dt = max(dt, 0.001)
        self._prev_time = now

        if target_x is None:
            target_x = frame_w / 2
        if target_y is None:
            target_y = frame_h / 2

        error_x = (person_x - target_x) / (frame_w / 2)
        error_y = (person_y - target_y) / (frame_h / 2)

        pan = self._compute_axis("pan", error_x, dt)
        tilt = self._compute_axis("tilt", -error_y, dt)

        return pan, tilt

    def _compute_axis(self, axis, error, dt):
        # Dead zone
        if abs(error) < self.dead_zone:
            error = 0.0

        # PID terms
        p = self.kp * error
        self._integral[axis] += error * dt
        self._integral[axis] = max(-1.0, min(1.0, self._integral[axis]))  # anti-windup
        i = self.ki * self._integral[axis]
        d = self.kd * (error - self._prev_error[axis]) / dt
        self._prev_error[axis] = error

        output = p + i + d
        output = max(-1.0, min(1.0, output))

        # Exponential smoothing
        smoothed = (self.smoothing * self._prev_output[axis] +
                    (1 - self.smoothing) * output)
        self._prev_output[axis] = smoothed

        return smoothed
