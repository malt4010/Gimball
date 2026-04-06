"""
PID Controller for gimbal tracking.

Converts target position error (how far the target is from frame center)
into gimbal movement commands (tilt/pan speed from -1 to 1).
"""
import time


class PIDController:
    """Dual-axis PID controller for pan and tilt."""

    def __init__(self, kp=1.5, ki=0.0, kd=0.8, dead_zone=0.05, smoothing=0.3):
        """
        kp: Proportional gain
        ki: Integral gain (usually 0)
        kd: Derivative gain - dampens oscillation
        dead_zone: Ignore errors smaller than this (fraction of frame)
        smoothing: Output smoothing (0=instant, higher=smoother)
        """
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.dead_zone = dead_zone
        self.smoothing = smoothing

        self._prev_error = {"pan": 0.0, "tilt": 0.0}
        self._integral = {"pan": 0.0, "tilt": 0.0}
        self._prev_output = {"pan": 0.0, "tilt": 0.0}

    def reset(self):
        self._prev_error = {"pan": 0.0, "tilt": 0.0}
        self._integral = {"pan": 0.0, "tilt": 0.0}
        self._prev_output = {"pan": 0.0, "tilt": 0.0}

    def update(self, person_x, person_y, frame_w, frame_h,
               target_x=None, target_y=None):
        if target_x is None:
            target_x = frame_w / 2
        if target_y is None:
            target_y = frame_h / 2

        # Normalized error: -1 to 1
        error_x = (person_x - target_x) / (frame_w / 2)
        error_y = (person_y - target_y) / (frame_h / 2)

        pan = self._compute_axis("pan", error_x)
        tilt = self._compute_axis("tilt", -error_y)

        return pan, tilt

    def _compute_axis(self, axis, error):
        # Dead zone - snap to zero when close enough
        if abs(error) < self.dead_zone:
            # Gradually reduce output to zero (don't snap instantly)
            self._prev_error[axis] = 0.0
            self._integral[axis] = 0.0
            output = self._prev_output[axis] * 0.5  # decay
            self._prev_output[axis] = output
            return output

        # Proportional: respond to current error
        p = self.kp * error

        # Integral: accumulate error over time (with anti-windup)
        self._integral[axis] += error
        self._integral[axis] = max(-5.0, min(5.0, self._integral[axis]))
        i = self.ki * self._integral[axis]

        # Derivative: respond to CHANGE in error (dampen oscillation)
        # Using raw error difference, not divided by dt, for stability
        d = self.kd * (error - self._prev_error[axis])
        self._prev_error[axis] = error

        # Combine
        raw_output = p + i + d

        # Clamp
        raw_output = max(-1.0, min(1.0, raw_output))

        # Smooth output to prevent jerky movement
        alpha = 1.0 - self.smoothing  # higher smoothing = lower alpha = slower response
        output = alpha * raw_output + self.smoothing * self._prev_output[axis]
        self._prev_output[axis] = output

        return output
