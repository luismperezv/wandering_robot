"""
Distance tuning routine for the wandering robot.

Place the robot facing a flat wall, then run:
    python -m firmware.tools.distance_tuner --trials 20

The script will:
1) Move forward/backward with varied speeds/durations.
2) Measure the front ultrasonic distance before/after each move.
3) Fit a simple linear model (cm moved toward wall vs speed*duration*direction).
4) Save raw samples and model coefficients to the logs directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import time
from datetime import datetime
from typing import Iterable, Tuple

from gpiozero import CamJamKitRobot

try:
    from firmware import config
    from firmware.hardware.ultrasonic import MultiUltrasonic
except Exception:  # pragma: no cover - fallback for direct module execution
    import config  # type: ignore
    from hardware.ultrasonic import MultiUltrasonic  # type: ignore


def median_distance_cm(sensor: MultiUltrasonic, samples: int, pause_s: float) -> float:
    readings = []
    for _ in range(samples):
        d = sensor.get_distance("front")
        if d != float("inf"):
            readings.append(d)
        time.sleep(pause_s)
    if not readings:
        return float("inf")
    return statistics.median(readings)


def run_trial(
    robot: CamJamKitRobot,
    sensors: MultiUltrasonic,
    direction: str,
    speed: float,
    duration_s: float,
    settle_s: float,
    measure_samples: int,
    measure_pause_s: float,
    min_clearance_cm: float,
) -> Tuple[float, float, float, float]:
    """Execute a single motion, return (start_cm, end_cm, actual_delta_cm, cmd_delta_u)."""
    start_cm = median_distance_cm(sensors, measure_samples, measure_pause_s)

    # Direction sign: +1 forward (toward wall), -1 backward (away)
    cmd_sign = 1.0 if direction == "forward" else -1.0
    cmd_delta_u = cmd_sign * speed * duration_s

    # Abort forward moves when clearance is unknown/too small
    if direction == "forward":
        if start_cm != start_cm or start_cm in (float("inf"), float("-inf")) or start_cm < min_clearance_cm:
            # Skip motion; return NaN delta and zero command magnitude so it doesn't pollute the fit
            return start_cm, start_cm, float("nan"), 0.0

    if direction == "forward":
        robot.forward(speed)
    else:
        robot.backward(speed)
    time.sleep(duration_s)
    robot.stop()

    time.sleep(settle_s)
    end_cm = median_distance_cm(sensors, measure_samples, measure_pause_s)

    if start_cm == float("inf") or end_cm == float("inf"):
        actual_delta_cm = float("nan")
    else:
        actual_delta_cm = start_cm - end_cm

    return start_cm, end_cm, actual_delta_cm, cmd_delta_u


def fit_linear(xs: Iterable[float], ys: Iterable[float]) -> Tuple[float, float, float]:
    """Return (slope, intercept, r2) for ys ~= slope*xs + intercept."""
    xs = list(xs)
    ys = list(ys)
    n = len(xs)
    if n == 0:
        return 0.0, 0.0, 0.0

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, ys))

    denom = n * sum_xx - (sum_x ** 2)
    slope = (n * sum_xy - sum_x * sum_y) / denom if denom else 0.0
    intercept = (sum_y - slope * sum_x) / n

    # r^2
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot else 0.0
    return slope, intercept, r2


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Distance tuning routine")
    p.add_argument("--trials", type=int, default=20, help="Number of motion trials")
    p.add_argument("--min-speed", type=float, default=0.3, help="Min speed (0-1)")
    p.add_argument("--max-speed", type=float, default=0.9, help="Max speed (0-1)")
    p.add_argument("--min-duration", type=float, default=0.3, help="Min move duration (s)")
    p.add_argument("--max-duration", type=float, default=1.2, help="Max move duration (s)")
    p.add_argument("--settle-s", type=float, default=0.35, help="Pause after motion before measuring")
    p.add_argument("--measure-samples", type=int, default=5, help="Samples per distance measurement")
    p.add_argument("--measure-pause-s", type=float, default=0.05, help="Pause between measurement samples")
    p.add_argument("--min-clearance-cm", type=float, default=10.0, help="Minimum clearance to allow forward moves")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    return p


def main():
    args = build_arg_parser().parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    FIXED_SPEED = 0.7

    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join("logs", f"distance_tuning_{timestamp}.csv")
    model_path = os.path.join("logs", f"distance_tuning_model_{timestamp}.json")

    # Only use the front sensor pins
    sensor_config = {"front": (config.FRONT_TRIG, config.FRONT_ECHO)}
    sensors = MultiUltrasonic(
        config=sensor_config,
        max_distance_m=config.MAX_DISTANCE_M,
        samples=config.SAMPLES_PER_READ,
    )
    robot = CamJamKitRobot()

    print(f"Logging raw samples to {csv_path}")
    print("Place the robot facing a flat wall. Press Ctrl+C to stop early.")

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["trial", "direction", "speed", "duration_s", "start_cm", "end_cm", "actual_delta_cm", "cmd_delta_u"]
        )

        xs = []
        ys = []

        last_dirs = []
        pending_back = False

        try:
            for trial in range(1, args.trials + 1):
                # Decide direction with rules:
                # - first move must be forward
                # - never allow 3 same directions in a row
                # - if pending_back is set, schedule a back move (unless we insert a spacer to avoid 3-in-a-row)
                start_cm = median_distance_cm(sensors, args.measure_samples, args.measure_pause_s)

                def can_forward():
                    return (start_cm != float("inf")) and (start_cm == start_cm) and (start_cm > args.min_clearance_cm)

                direction = "forward" if trial == 1 else None

                if direction is None and pending_back:
                    # Avoid 3 consecutive backs: insert a spacer forward if needed and safe
                    if len(last_dirs) >= 2 and last_dirs[-1] == last_dirs[-2] == "backward" and can_forward():
                        direction = "forward"
                        # keep pending_back for the following iteration
                    else:
                        direction = "backward"
                        pending_back = False

                if direction is None:
                    # General case: pick direction respecting no 3-in-a-row
                    last_two_same = len(last_dirs) >= 2 and last_dirs[-1] == last_dirs[-2]
                    forbidden = last_dirs[-1] if last_two_same else None
                    candidates = ["forward", "backward"]
                    if forbidden:
                        candidates = [c for c in candidates if c != forbidden]
                    # If forward not safe, fallback to back
                    if "forward" in candidates and not can_forward():
                        candidates = [c for c in candidates if c != "forward"] or ["backward"]
                    direction = random.choice(candidates)

                speed = FIXED_SPEED
                duration_s = random.uniform(args.min_duration, args.max_duration)

                start_cm, end_cm, actual_delta_cm, cmd_delta_u = run_trial(
                    robot,
                    sensors,
                    direction,
                    speed,
                    duration_s,
                    args.settle_s,
                    args.measure_samples,
                    args.measure_pause_s,
                    args.min_clearance_cm,
                )

                last_dirs.append(direction)
                # If end distance is valid and too close, enqueue a mandatory back move next
                if end_cm == end_cm and end_cm not in (float("inf"), float("-inf")) and end_cm < 20.0:
                    pending_back = True

                writer.writerow(
                    [trial, direction, f"{speed:.3f}", f"{duration_s:.3f}", f"{start_cm:.2f}", f"{end_cm:.2f}", f"{actual_delta_cm:.2f}", f"{cmd_delta_u:.4f}"]
                )
                f.flush()

                if actual_delta_cm == actual_delta_cm:  # not NaN
                    xs.append(cmd_delta_u)
                    ys.append(actual_delta_cm)

                print(
                    f"[{trial:02d}] {direction:8s} speed={speed:.2f} dur={duration_s:.2f}s "
                    f"start={start_cm:.1f}cm end={end_cm:.1f}cm delta={actual_delta_cm:.2f}cm"
                )
        except KeyboardInterrupt:
            print("\nInterrupted by user, fitting model with collected samples.")
        finally:
            robot.stop()
            sensors.cleanup()

    slope, intercept, r2 = fit_linear(xs, ys)
    model = {
        "slope_cm_per_speed_sec": slope,
        "intercept_cm": intercept,
        "r2": r2,
        "samples": len(xs),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "note": "Predict cm moved toward wall from (speed * duration * direction_sign)",
    }
    with open(model_path, "w") as f:
        json.dump(model, f, indent=2)

    print(f"Raw data: {csv_path}")
    print(f"Model   : {model_path}")


def run_tuning_session(controller, bucket_name: str, speed: float, trials: int):
    """
    Execute a tuning session with a specific speed and number of trials.
    Saves data to logs/<bucket_name>/<speed>/<timestamp>.csv
    """
    # Validate inputs
    if not bucket_name or ".." in bucket_name or "/" in bucket_name:
        raise ValueError("Invalid bucket name")
    
    # Setup paths
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    speed_dir = os.path.join("logs", bucket_name, f"{speed:.2f}")
    os.makedirs(speed_dir, exist_ok=True)
    csv_path = os.path.join(speed_dir, f"tuning_{timestamp}.csv")
    
    # Get sensor from controller
    sensor = getattr(controller, "sensor", None)
    if not sensor:
        raise ValueError("Controller has no sensor")

    print(f"Starting tuning session: bucket={bucket_name}, speed={speed}, trials={trials}")
    print(f"Logging to {csv_path}")

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["trial", "direction", "speed", "duration_s", "start_cm", "end_cm", "actual_delta_cm", "cmd_delta_u"]
        )

        last_dirs = []
        pending_back = False

        try:
            for trial in range(1, trials + 1):
                # Measure start distance
                start_cm = median_distance_cm(sensor, samples=5, pause_s=0.05)
                
                # Direction logic (reused from original)
                def can_forward():
                    return (start_cm != float("inf")) and (start_cm == start_cm) and (start_cm > 10.0) # min_clearance

                direction = "forward" if trial == 1 else None

                if direction is None and pending_back:
                    if len(last_dirs) >= 2 and last_dirs[-1] == last_dirs[-2] == "backward" and can_forward():
                        direction = "forward"
                    else:
                        direction = "backward"
                        pending_back = False

                if direction is None:
                    last_two_same = len(last_dirs) >= 2 and last_dirs[-1] == last_dirs[-2]
                    forbidden = last_dirs[-1] if last_two_same else None
                    candidates = ["forward", "backward"]
                    if forbidden:
                        candidates = [c for c in candidates if c != forbidden]
                    if "forward" in candidates and not can_forward():
                        candidates = [c for c in candidates if c != "forward"] or ["backward"]
                    direction = random.choice(candidates)

                # Duration logic
                duration_s = random.uniform(0.3, 1.2) # min/max duration

                # Execute move using controller
                # We need to manually calculate cmd_delta_u for logging
                cmd_sign = 1.0 if direction == "forward" else -1.0
                cmd_delta_u = cmd_sign * speed * duration_s

                # Skip forward if unsafe (redundant check but safe)
                if direction == "forward" and not can_forward():
                     # Log as skipped/invalid
                     writer.writerow([trial, direction, f"{speed:.3f}", f"{duration_s:.3f}", f"{start_cm:.2f}", "nan", "nan", "0.0"])
                     f.flush()
                     continue

                controller.execute_command_sequence([
                    {"name": direction, "speed": speed, "duration_s": duration_s}
                ])
                
                time.sleep(0.35) # settle_s

                end_cm = median_distance_cm(sensor, samples=5, pause_s=0.05)

                if start_cm == float("inf") or end_cm == float("inf"):
                    actual_delta_cm = float("nan")
                else:
                    actual_delta_cm = start_cm - end_cm

                last_dirs.append(direction)
                if end_cm == end_cm and end_cm not in (float("inf"), float("-inf")) and end_cm < 20.0:
                    pending_back = True

                writer.writerow(
                    [trial, direction, f"{speed:.3f}", f"{duration_s:.3f}", f"{start_cm:.2f}", f"{end_cm:.2f}", f"{actual_delta_cm:.2f}", f"{cmd_delta_u:.4f}"]
                )
                f.flush()

                print(
                    f"[{trial:02d}/{trials}] {direction:8s} speed={speed:.2f} dur={duration_s:.2f}s "
                    f"start={start_cm:.1f}cm end={end_cm:.1f}cm delta={actual_delta_cm:.2f}cm"
                )
        except Exception as e:
            print(f"Tuning session error: {e}")
            raise

    return csv_path


def test_model_accuracy(controller, model: dict, target_cm: float) -> dict:
    """
    Test the distance model by measuring, moving, and measuring again.
    Uses the provided controller to execute moves and read sensors.
    """
    # 1. Measure starting distance
    # We can reuse median_distance_cm if we have access to the sensor object
    # controller.sensor should be the MultiUltrasonic instance or similar
    sensor = getattr(controller, "sensor", None)
    if not sensor:
        raise ValueError("Controller has no sensor")

    start_cm = median_distance_cm(sensor, samples=5, pause_s=0.05)
    if start_cm == float("inf") or start_cm is None:
        raise ValueError("Could not measure valid starting distance")

    # 2. Calculate move
    slope = float(model.get("slope_cm_per_speed_sec", 0))
    intercept = float(model.get("intercept_cm", 0))

    if abs(slope) < 1e-4:
        raise ValueError("Model slope is too close to zero")

    needed_delta = start_cm - target_cm
    cmd_u = (needed_delta - intercept) / slope

    # cmd_u = speed * duration * sign
    # We use fixed speed 0.7
    FIXED_SPEED = 0.7

    if cmd_u > 0:
        direction = "forward"
        duration = cmd_u / FIXED_SPEED
    else:
        direction = "backward"
        duration = abs(cmd_u) / FIXED_SPEED

    # Cap duration for safety
    if duration > 2.0:
        duration = 2.0
    if duration < 0.05:
        duration = 0.05

    print(f"[TUNING TEST] Start={start_cm:.1f}cm Target={target_cm}cm Delta={needed_delta:.1f}cm -> {direction} {duration:.2f}s")

    # 3. Execute move
    controller.execute_command_sequence([
        {"name": direction, "speed": FIXED_SPEED, "duration_s": duration}
    ])

    # 4. Measure ending distance
    time.sleep(0.5)  # Settle
    end_cm = median_distance_cm(sensor, samples=5, pause_s=0.05)

    return {
        "success": True,
        "start_cm": start_cm,
        "target_cm": target_cm,
        "predicted_move": {
            "direction": direction,
            "duration_s": duration,
            "speed": FIXED_SPEED
        },
        "end_cm": end_cm,
        "error_cm": end_cm - target_cm if end_cm is not None else None
    }


if __name__ == "__main__":
    main()

