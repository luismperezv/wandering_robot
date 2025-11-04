import time
import json
from collections import deque

try:
    from firmware import config
    from firmware.control.policy import decide_next_motion
except Exception:
    import config  # type: ignore
    from control.policy import decide_next_motion  # type: ignore


def execute_motion(robot, motion: str, speed: float, duration: float):
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


class Controller:
    def __init__(self, robot, sensor, logger_writer, hub, commands_q, keyboard=None, log_file="runlog.csv"):
        self.robot = robot
        self.sensor = sensor
        self.writer = logger_writer
        self.hub = hub
        self.commands_q = commands_q
        self.keyboard = keyboard
        self.log_file = log_file

        self.current_motion = "forward"
        self.current_speed = config.FORWARD_SPD
        self.dist_hist = deque(maxlen=config.STUCK_STEPS)
        self.stuck_cooldown = 0
        self.queued_moves = []  # (motion, speed, ticks_remaining)
        self.manual_mode = False

    def _broadcast(self, msg: dict):
        try:
            self.hub.broadcast(json.dumps(msg))
        except Exception:
            pass

    def run(self):
        try:
            while True:
                # Drain web commands
                if self.commands_q is not None:
                    while True:
                        try:
                            c = self.commands_q.get_nowait()
                        except Exception:
                            break
                        if c == 'toggle':
                            self.manual_mode = not self.manual_mode
                            self.robot.stop()
                            if self.manual_mode:
                                self.queued_moves.clear()
                        elif c == 'auto':
                            self.manual_mode = False
                            self.robot.stop()
                            self.queued_moves.clear()
                        elif c == 'stop':
                            if self.manual_mode:
                                self.queued_moves.clear()
                                self.queued_moves.append(("stop", 0.0, 1))
                        elif c in ('forward','backward','left','right'):
                            if self.manual_mode:
                                spd = (config.FORWARD_SPD if c == "forward"
                                       else config.BACK_SPD if c == "backward"
                                       else config.TURN_SPD)
                                self.queued_moves.append((c, spd, 1))

                # Keyboard
                if self.keyboard is not None:
                    ev = self.keyboard.pop_event()
                    if ev:
                        kind, data = ev
                        if kind == 'TOGGLE':
                            self.manual_mode = not self.manual_mode
                            self.robot.stop()
                            if self.manual_mode:
                                self.queued_moves.clear()
                        elif kind == 'CMD' and self.manual_mode:
                            cmd = data
                            spd = (config.FORWARD_SPD if cmd == "forward"
                                   else config.BACK_SPD if cmd == "backward"
                                   else config.TURN_SPD if cmd in ("left", "right")
                                   else 0.0)
                            self.queued_moves.append((cmd, spd, 1))

                # Manual branch executes and emits
                if self.manual_mode:
                    if self.queued_moves:
                        exec_motion, exec_speed, _ = self.queued_moves.pop(0)
                    else:
                        exec_motion, exec_speed = "stop", 0.0
                    execute_motion(self.robot, exec_motion, exec_speed, config.TICK_S)
                    d = self.sensor.distance_cm()
                    notes = "manual_cmd" if exec_motion != "stop" else "manual_idle"
                    next_motion, next_speed = ("manual", 0.0)
                    # log
                    self.writer(["MANUAL", d, exec_motion, exec_speed, next_motion, next_speed, notes, 0, 0])
                    # broadcast
                    self._broadcast({
                        "mode": "MANUAL",
                        "distance_cm": (None if d == float('inf') else round(d,2)),
                        "executed_motion": exec_motion,
                        "executed_speed": round(exec_speed,2),
                        "next_motion": next_motion,
                        "next_speed": next_speed,
                        "notes": notes,
                        "stuck": 0,
                        "queue_len": 0,
                        "log_file": self.log_file,
                    })
                    continue

                # Auto branch
                if self.queued_moves:
                    q_motion, q_speed, q_ticks = self.queued_moves[0]
                    exec_motion, exec_speed = q_motion, q_speed
                else:
                    exec_motion, exec_speed = self.current_motion, self.current_speed

                execute_motion(self.robot, exec_motion, exec_speed, config.TICK_S)

                # decrement macro ticks
                if self.queued_moves:
                    q_motion, q_speed, q_ticks = self.queued_moves[0]
                    q_ticks -= 1
                    if q_ticks <= 0:
                        self.queued_moves.pop(0)
                    else:
                        self.queued_moves[0] = (q_motion, q_speed, q_ticks)

                # sensor
                d = self.sensor.distance_cm()
                if d != float('inf'):
                    self.dist_hist.append(d)

                # policy + stuck
                notes = ""
                stuck_triggered = 0
                if not self.queued_moves:
                    next_motion, next_speed, notes = decide_next_motion(d, exec_motion)
                    if self.stuck_cooldown > 0:
                        self.stuck_cooldown -= 1
                    else:
                        if len(self.dist_hist) == config.STUCK_STEPS:
                            spread = max(self.dist_hist) - min(self.dist_hist)
                            if spread < config.STUCK_DELTA_CM:
                                import random
                                turn_dir = random.choice(["left", "right"])
                                self.queued_moves = [
                                    ("backward", config.BACK_SPD, config.BACK_TICKS),
                                    (turn_dir,  config.TURN_SPD,  config.NUDGE_TICKS),
                                ]
                                notes = f"STUCK: Δ={spread:.1f}cm/{config.STUCK_STEPS}steps -> back {config.BACK_TICKS} + {turn_dir} {config.NUDGE_TICKS}"
                                stuck_triggered = 1
                                self.stuck_cooldown = config.STUCK_COOLDOWN_STEPS
                                next_motion, next_speed = ("forward", config.FORWARD_SPD)
                                self.dist_hist.clear()
                    self.current_motion, self.current_speed = next_motion, next_speed

                # log
                self.writer(["AUTO", d, exec_motion, exec_speed, (self.queued_moves[0][0] if self.queued_moves else self.current_motion), (self.queued_moves[0][1] if self.queued_moves else self.current_speed), notes, stuck_triggered, len(self.queued_moves)])
                # broadcast
                self._broadcast({
                    "mode": "AUTO",
                    "distance_cm": (None if d == float('inf') else round(d,2)),
                    "executed_motion": exec_motion,
                    "executed_speed": round(exec_speed,2),
                    "next_motion": (self.queued_moves[0][0] if self.queued_moves else self.current_motion),
                    "next_speed": (self.queued_moves[0][1] if self.queued_moves else self.current_speed),
                    "notes": notes,
                    "stuck": stuck_triggered,
                    "queue_len": len(self.queued_moves),
                    "log_file": self.log_file,
                })
        finally:
            try:
                self.robot.stop()
            except Exception:
                pass
            try:
                self.sensor.close()
            except Exception:
                pass


