#!/usr/bin/env python3
# discrete_steps_robot_with_stuck_and_manual.py
# 0.5 s discrete controller + stuck detection + manual override (Enter toggles; arrows drive)

import csv
import sys
import time
import random
import statistics
import os
import threading
import select
import termios
import tty
from collections import deque
from datetime import datetime

import pigpio
from gpiozero import CamJamKitRobot

# --------- Tunables ----------
TICK_S           = 0.5   # discrete step duration
FORWARD_SPD      = 0.40
TURN_SPD         = 0.40
BACK_SPD         = 0.40

STOP_CM          = 15.0  # too close -> evasive turn
CLEAR_CM         = 30.0  # comfortable clear
MAX_DISTANCE_M   = 2.5
SAMPLES_PER_READ = 3

# --- Stuck detection ---
STUCK_DELTA_CM   = 5.0   # consider "no change" if spread < this
STUCK_STEPS      = 4     # look back over this many ticks
BACK_TICKS       = 3     # back up when stuck
NUDGE_TICKS      = 1     # random turn after backoff
STUCK_COOLDOWN_STEPS = 4

LOG_FILE = f"runlog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

# --------- Ultrasonic ----------
TRIG = 19
ECHO = 26
SOUND_SPEED = 343.0  # m/s @ ~20C

class PigpioUltrasonic:
    def __init__(self, trig: int, echo: int, max_distance_m: float = 2.5, samples: int = 3):
        self.pi = pigpio.pi()  # needs pigpiod running
        if not self.pi.connected:
            raise RuntimeError("pigpio daemon not running (start with: sudo pigpiod -g -l)")
        self.trig = trig
        self.echo = echo
        self.max_distance_m = max_distance_m
        self.timeout_s = (2 * max_distance_m) / SOUND_SPEED
        self.samples = max(1, samples)

        self.pi.set_mode(self.trig, pigpio.OUTPUT)
        self.pi.set_mode(self.echo, pigpio.INPUT)
        self.pi.write(self.trig, 0)
        self.pi.set_pull_up_down(self.echo, pigpio.PUD_DOWN)

        self._rise = None
        self._fall = None
        self._cb = self.pi.callback(self.echo, pigpio.EITHER_EDGE, self._edge)

    def _edge(self, gpio, level, tick):
        if level == 1:
            self._rise = tick
        elif level == 0:
            self._fall = tick

    @staticmethod
    def _ticks_to_s(start, end):
        if end < start:
            end += (1 << 32)
        return (end - start) / 1_000_000.0

    def _pulse(self):
        self._rise = None
        self._fall = None
        self.pi.gpio_trigger(self.trig, 10, 1)  # 10 µs HIGH

    def distance_cm(self):
        readings = []
        for _ in range(self.samples):
            self._pulse()

            t0 = time.time()
            while self._rise is None and (time.time() - t0) < self.timeout_s:
                time.sleep(0.00005)
            if self._rise is None:
                continue

            t1 = time.time()
            while self._fall is None and (time.time() - t1) < self.timeout_s:
                time.sleep(0.00005)
            if self._fall is None:
                continue

            dt = self._ticks_to_s(self._rise, self._fall)
            d_m = (dt * SOUND_SPEED) / 2.0
            if 0.0 < d_m <= self.max_distance_m:
                readings.append(d_m * 100.0)  # cm
            time.sleep(0.01)

        if not readings:
            return float('inf')
        return statistics.median(readings)

    def close(self):
        if self._cb:
            self._cb.cancel()
        if self.pi and self.pi.connected:
            self.pi.stop()

## --------- Keyboard (cbreak, non-blocking) ----------

class CbreakKeyboard:
    """
    Cbreak-mode non-blocking reader:
      - Enter toggles manual mode
      - WASD: W=forward, S=backward, A=left, D=right
    Ctrl+C still raises KeyboardInterrupt (we do not intercept it).
    """
    def __init__(self):
        # Prefer controlling TTY directly to work under sudo/SSH
        fd = None
        self._tty_path = None
        try:
            fd = os.open('/dev/tty', os.O_RDONLY)
            self._tty_path = '/dev/tty'
        except Exception:
            # Fallback to stdin
            fd = sys.stdin.fileno()
        self._fd = fd
        self._old = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        self._lock = threading.Lock()
        self._events = []
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        try:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
        except Exception:
            pass
        try:
            # Close if we explicitly opened /dev/tty
            if self._tty_path:
                os.close(self._fd)
        except Exception:
            pass

    def _push(self, ev):
        with self._lock:
            self._events.append(ev)

    def pop_event(self):
        with self._lock:
            return self._events.pop(0) if self._events else None

    def _run(self):
        while not self._stop:
            r, _, _ = select.select([self._fd], [], [], 0.05)
            if not r:
                continue
            try:
                data = os.read(self._fd, 32)
            except OSError:
                continue
            if not data:
                continue
            try:
                buf = data.decode('utf-8', errors='ignore')
            except Exception:
                continue

            for ch in buf:
                if ch in ('\r', '\n'):
                    self._push(('TOGGLE', None))
                    continue

            lower = buf.lower()
            if 'w' in lower:
                self._push(('CMD', 'forward'))
            if 's' in lower:
                self._push(('CMD', 'backward'))
            if 'a' in lower:
                self._push(('CMD', 'left'))
            if 'd' in lower:
                self._push(('CMD', 'right'))

# --------- Motion helpers ----------
def execute_motion(robot: CamJamKitRobot, motion: str, speed: float, duration: float):
    if motion == "forward":
        robot.forward(speed)
    elif motion == "backward":
        robot.backward(speed)
    elif motion == "left":
        robot.left(speed)
    elif motion == "right":
        robot.right(speed)
    else:
        robot.stop()
        motion = "stop"
    time.sleep(duration)
    robot.stop()

def decide_next_motion(distance_cm: float, prev_motion: str) -> tuple[str, float, str]:
    """
    NORMAL autonomous policy (manual/stuck macros handled outside).
    Returns (next_motion, speed, notes)
    """
    if distance_cm == float('inf'):
        return ("forward", FORWARD_SPD, "no-echo/open")

    if distance_cm <= STOP_CM:
        direction = random.choice(["left", "right"])
        return (direction, TURN_SPD, f"obstacle@{distance_cm:.1f}cm")

    if distance_cm >= CLEAR_CM:
        return ("forward", FORWARD_SPD, "clear")

    if prev_motion in ("left", "right"):
        return (prev_motion, TURN_SPD * 0.8, f"bias-{prev_motion}@{distance_cm:.1f}cm")

    return ("forward", FORWARD_SPD * 0.8, f"caution@{distance_cm:.1f}cm")

def main():
    robot = CamJamKitRobot()
    sensor = PigpioUltrasonic(TRIG, ECHO, max_distance_m=MAX_DISTANCE_M, samples=SAMPLES_PER_READ)

    # CSV
    f = open(LOG_FILE, "w", newline="")
    writer = csv.writer(f)
    writer.writerow([
        "timestamp_iso", "mode", "distance_cm",
        "executed_motion", "executed_speed",
        "next_motion", "next_speed",
        "notes", "stuck_triggered", "queue_len"
    ])
    f.flush()

    # Controller state
    current_motion = "forward"
    current_speed  = FORWARD_SPD
    dist_hist = deque(maxlen=STUCK_STEPS)
    stuck_cooldown = 0
    queued_moves = []  # (motion, speed, ticks_remaining)

    # Manual control
    kb = CbreakKeyboard()
    kb.start()
    manual_mode = False
    print("Controls: Enter=toggle MANUAL, WASD=drive. Ctrl+C to quit.")
    print(f"Logging to {LOG_FILE}.")
    try:
        while True:
            # Check keyboard events (once per tick)
            ev = kb.pop_event()
            if ev:
                kind, data = ev
                if kind == 'TOGGLE':
                    manual_mode = not manual_mode
                    robot.stop()
                    tag = "manual_start" if manual_mode else "manual_end"
                    print(f"\n[{tag}]")
                    writer.writerow([
                        datetime.now().isoformat(timespec="seconds"),
                        ("MANUAL" if manual_mode else "AUTO"),
                        "", "", "", "", "", tag, 0, len(queued_moves)
                    ])
                    f.flush()
                    if manual_mode:
                        queued_moves.clear()
                elif kind == 'CMD' and manual_mode:
                    cmd = data
                    speed = (FORWARD_SPD if cmd == "forward"
                             else BACK_SPD if cmd == "backward"
                             else TURN_SPD if cmd in ("left", "right")
                             else 0.0)
                    queued_moves.append((cmd, speed, 1))
                    print(f"[manual cmd] {cmd}")
            tick_start = time.time()
            # 1) Execute queued macro or current motion; in manual, execute queued manual or stop
            if manual_mode:
                if queued_moves:
                    exec_motion, exec_speed, _ = queued_moves.pop(0)
                else:
                    exec_motion, exec_speed = "stop", 0.0
                execute_motion(robot, exec_motion, exec_speed, TICK_S)
                d = sensor.distance_cm()
                notes = "manual_cmd" if exec_motion != "stop" else "manual_idle"
                next_motion, next_speed = ("manual", 0.0)
                writer.writerow([
                    datetime.now().isoformat(timespec="seconds"),
                    "MANUAL",
                    ("" if d == float('inf') else f"{d:.2f}"),
                    exec_motion, f"{exec_speed:.2f}",
                    next_motion, f"{next_speed:.2f}",
                    notes, 0, 0
                ])
                f.flush()
                continue

            if queued_moves:
                q_motion, q_speed, q_ticks = queued_moves[0]
                exec_motion, exec_speed = q_motion, q_speed
            else:
                exec_motion, exec_speed = current_motion, current_speed

            execute_motion(robot, exec_motion, exec_speed, TICK_S)

            # decrement macro ticks
            if queued_moves:
                q_motion, q_speed, q_ticks = queued_moves[0]
                q_ticks -= 1
                if q_ticks <= 0:
                    queued_moves.pop(0)
                else:
                    queued_moves[0] = (q_motion, q_speed, q_ticks)

            # 2) Read distance & update history
            d = sensor.distance_cm()
            if d != float('inf'):
                dist_hist.append(d)

            # 3) Decide next motion (normal policy) if not in a macro
            notes = ""
            stuck_triggered = 0
            if not queued_moves:
                next_motion, next_speed, notes = decide_next_motion(d, exec_motion)

                # 4) Stuck detection
                if stuck_cooldown > 0:
                    stuck_cooldown -= 1
                else:
                    if len(dist_hist) == STUCK_STEPS:
                        spread = max(dist_hist) - min(dist_hist)
                        if spread < STUCK_DELTA_CM:
                            turn_dir = random.choice(["left", "right"])
                            queued_moves = [
                                ("backward", BACK_SPD, BACK_TICKS),
                                (turn_dir,  TURN_SPD,  NUDGE_TICKS),
                            ]
                            notes = f"STUCK: Δ={spread:.1f}cm/{STUCK_STEPS}steps -> back {BACK_TICKS} + {turn_dir} {NUDGE_TICKS}"
                            stuck_triggered = 1
                            stuck_cooldown = STUCK_COOLDOWN_STEPS
                            next_motion, next_speed = ("forward", FORWARD_SPD)
                            dist_hist.clear()

                current_motion, current_speed = next_motion, next_speed

            # 5) Log this tick
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                "AUTO",
                ("" if d == float('inf') else f"{d:.2f}"),
                exec_motion, f"{exec_speed:.2f}",
                (queued_moves[0][0] if queued_moves else current_motion),
                (f"{queued_moves[0][1]:.2f}" if queued_moves else f"{current_speed:.2f}"),
                notes,
                stuck_triggered,
                len(queued_moves)
            ])
            f.flush()

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        robot.stop()
        sensor.close()
        f.close()

if __name__ == "__main__":
    main()
