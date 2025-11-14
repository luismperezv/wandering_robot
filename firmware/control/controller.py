from __future__ import annotations
import time
import json
from collections import deque

try:
    from firmware import config
    from firmware.control.policy import decide_next_motion
    from firmware.config_manager import ConfigManager
    from firmware.policy_manager import PolicyManager
except Exception:
    import config  # type: ignore
    from control.policy import decide_next_motion  # type: ignore
    from config_manager import ConfigManager  # type: ignore
    from policy_manager import PolicyManager  # type: ignore


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
    def __init__(self, robot, sensor, logger_writer, hub, commands_q, keyboard=None, log_file="runlog.csv", config_manager: "ConfigManager | None" = None, policy_manager: "PolicyManager | None" = None):
        self.robot = robot
        # Store all sensors in a dict
        if isinstance(sensor, dict):
            self.sensors = sensor
            # For backward compatibility, keep a reference to the front sensor
            self.sensor = sensor.get('front')
        else:
            # Handle case where only one sensor is passed
            self.sensor = sensor
            self.sensors = {'front': sensor}
            
        self.writer = logger_writer
        self.hub = hub
        self.commands_q = commands_q
        self.keyboard = keyboard
        self.log_file = log_file
        self.cfg = config_manager
        self.policy = policy_manager
        
        # Set the writer for config changes if config manager is available
        if self.cfg is not None and hasattr(self.cfg, 'set_writer'):
            self.cfg.set_writer(self.writer)

        self.current_motion = "stop"  # Start in stopped state
        self.current_speed = 0.0
        self.dist_hist = deque(maxlen=config.STUCK_STEPS)
        self.stuck_cooldown = 0
        self.queued_moves = []  # (motion, speed, ticks_remaining)
        self.auto_mode = False  # True = AUTO mode, False = MANUAL/REMOTE mode
        self.emergency_stopped = False  # Track if we're in emergency stop state

    def _cfg(self, key, default):
        if self.cfg is not None:
            return self.cfg.get(key, default)
        return getattr(config, key, default)

    def emergency_stop(self):
        """Immediately stop all robot movement and clear all state."""
        try:
            self.robot.stop()
            self.queued_moves.clear()
            self.current_motion = "stop"
            self.current_speed = 0.0
            self.emergency_stopped = True  # Set emergency stop flag
            
            # Broadcast the emergency stop
            self._broadcast({
                "mode": "STOPPED",
                "distance_cm": None,
                "executed_motion": "stop",
                "executed_speed": 0.0,
                "next_motion": "stop",
                "next_speed": 0.0,
                "notes": "EMERGENCY_STOP",
                "stuck": 0,
                "queue_len": 0,
                "log_file": self.log_file,
            })
            return True
        except Exception as e:
            print(f"Emergency stop error: {e}")
            return False

    def _broadcast(self, msg: dict):
        try:
            # Ensure mode is set correctly based on auto_mode
            if 'mode' not in msg:
                msg = msg.copy()  # Don't modify the original
                msg['mode'] = 'AUTO' if self.auto_mode else 'REMOTE'
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
                        # New dict-based commands
                        if isinstance(c, dict):
                            if c.get("type") == "mode":
                                mode = c.get("mode")
                                self.robot.stop()
                                self.queued_moves.clear()
                                self.auto_mode = (mode == "AUTO")
                            elif c.get("type") == "cmd":
                                name = c.get("name")
                                speed = c.get("speed")
                                duration_ms = c.get("duration_ms")
                                duration_s_req = c.get("duration_s")
                                # Toggle between AUTO and REMOTE modes
                                if name == 'toggle':
                                    self.auto_mode = not self.auto_mode
                                    self.robot.stop()
                                    self.queued_moves.clear()
                                    continue
                                if name == 'auto':
                                    self.auto_mode = True
                                    self.robot.stop()
                                    self.queued_moves.clear()
                                    continue
                                # Handle movement commands in REMOTE mode
                                if not self.auto_mode and name in ("forward", "backward", "left", "right"):
                                    if speed is None:
                                        if name == "forward":
                                            speed = float(self._cfg("FORWARD_SPD", config.FORWARD_SPD))
                                        elif name == "backward":
                                            speed = float(self._cfg("BACK_SPD", config.BACK_SPD))
                                        else:
                                            speed = float(self._cfg("TURN_SPD", config.TURN_SPD))
                                    
                                    duration_s = (
                                        float(duration_ms) / 1000.0 if duration_ms is not None
                                        else float(duration_s_req) if duration_s_req is not None
                                        else float(self._cfg("TICK_S", config.TICK_S))
                                    )
                                    
                                    # Queue the move
                                    self.queued_moves.append((name, float(speed), 1))
                                elif name == 'stop':
                                    # Emergency stop - clear all state and stop immediately
                                    self.emergency_stop()
                                # ignore if not in REMOTE
                        else:
                            # Legacy string commands from dashboard keyboard
                            if c == 'toggle':
                                if self.emergency_stopped:
                                    self.emergency_stopped = False  # Clear emergency stop
                                self.auto_mode = not self.auto_mode
                                self.robot.stop()
                                self.queued_moves.clear()
                            elif c == 'auto':
                                self.auto_mode = True
                                self.robot.stop()
                                self.queued_moves.clear()
                            elif c == 'stop':
                                # Emergency stop - clear all state and stop immediately
                                self.emergency_stop()
                            elif c in ('forward','backward','left','right') and not self.auto_mode:
                                # Only process movement commands in REMOTE mode
                                spd = (self._cfg("FORWARD_SPD", config.FORWARD_SPD) if c == "forward"
                                       else self._cfg("BACK_SPD", config.BACK_SPD) if c == "backward"
                                       else self._cfg("TURN_SPD", config.TURN_SPD))
                                self.queued_moves.append((c, float(spd), 1))

                # If in emergency stop, stay stopped until explicitly cleared
                if self.emergency_stopped:
                    time.sleep(0.1)
                    continue
                    
                # If in REMOTE mode and no immediate command is running, idle (no motion)
                if not self.auto_mode and not self.queued_moves:
                    # Only update state if it's changed from the last broadcast
                    if not hasattr(self, '_last_idle_state') or time.time() - getattr(self, '_last_idle_time', 0) > 5.0:
                        # Get readings from all sensors
                        distances = {name: s.distance_cm() for name, s in self.sensors.items()}
                        front_d = distances.get('front', float('inf'))
                        left_d = distances.get('left', float('inf'))
                        right_d = distances.get('right', float('inf'))
                        
                        state = {
                            "mode": "REMOTE",
                            "front_distance_cm": (None if front_d == float('inf') else round(front_d, 2)),
                            "left_distance_cm": (None if left_d == float('inf') else round(left_d, 2)),
                            "right_distance_cm": (None if right_d == float('inf') else round(right_d, 2)),
                            "executed_motion": "stop",
                            "executed_speed": 0.0,
                            "next_motion": "idle",
                            "next_speed": 0.0,
                            "notes": "remote_idle",
                            "stuck": 0,
                            "queue_len": 0,
                            "log_file": self.log_file,
                        }
                        self._broadcast(state)
                        try:
                            self.hub.set_state(state)
                        except Exception:
                            pass
                        self._last_idle_state = state
                        self._last_idle_time = time.time()
                    time.sleep(0.1)  # Prevent busy-waiting
                    continue

                # Handle remote commands
                if not self.auto_mode and self.queued_moves:
                    exec_motion, exec_speed, _ = self.queued_moves.pop(0)
                    execute_motion(self.robot, exec_motion, exec_speed, self._duration_for_motion(exec_motion))
                    # Get readings from all sensors
                    distances = {name: s.distance_cm() for name, s in self.sensors.items()}
                    front_d = distances.get('front', float('inf'))
                    left_d = distances.get('left', float('inf'))
                    right_d = distances.get('right', float('inf'))
                    
                    notes = f"remote_cmd_{exec_motion}" if exec_motion != "stop" else "remote_idle"
                    
                    # Log the action with all sensor readings
                    self.writer([
                        "REMOTE",
                        front_d,
                        left_d,
                        right_d,
                        exec_motion,
                        exec_speed,
                        "idle",
                        0.0,
                        notes,
                        0,
                        0
                    ])
                    
                    # Broadcast the state
                    state = {
                        "mode": "REMOTE",
                        "front_distance_cm": (None if front_d == float('inf') else round(front_d, 2)),
                        "left_distance_cm": (None if left_d == float('inf') else round(left_d, 2)),
                        "right_distance_cm": (None if right_d == float('inf') else round(right_d, 2)),
                        "executed_motion": exec_motion,
                        "executed_speed": round(exec_speed, 2),
                        "next_motion": "idle",
                        "next_speed": 0.0,
                        "notes": notes,
                        "stuck": 0,
                        "queue_len": len(self.queued_moves),
                        "log_file": self.log_file,
                    }
                    self._broadcast(state)
                    try:
                        self.hub.set_state(state)
                    except Exception:
                        pass
                    continue

                # Auto branch - skip if emergency stopped
                if self.emergency_stopped:
                    time.sleep(0.1)
                    continue
                    
                if self.queued_moves:
                    q_motion, q_speed, q_ticks = self.queued_moves[0]
                    exec_motion, exec_speed = q_motion, q_speed
                else:
                    exec_motion, exec_speed = self.current_motion, self.current_speed

                execute_motion(self.robot, exec_motion, exec_speed, self._duration_for_motion(exec_motion))

                # decrement macro ticks
                if self.queued_moves:
                    q_motion, q_speed, q_ticks = self.queued_moves[0]
                    q_ticks -= 1
                    if q_ticks <= 0:
                        self.queued_moves.pop(0)
                    else:
                        self.queued_moves[0] = (q_motion, q_speed, q_ticks)

                # Get readings from all sensors
                distances = {name: s.distance_cm() for name, s in self.sensors.items()}
                front_d = distances.get('front', float('inf'))
                left_d = distances.get('left', float('inf'))
                right_d = distances.get('right', float('inf'))
                
                # Use front sensor for navigation and stuck detection
                if front_d != float('inf'):
                    self.dist_hist.append(front_d)

                # policy + stuck
                notes = ""
                stuck_triggered = 0
                if not self.queued_moves:
                    if self.policy is not None:
                        next_motion, next_speed, notes = self.policy.decide_next_motion(front_d, exec_motion)
                    else:
                        next_motion, next_speed, notes = decide_next_motion(front_d, exec_motion)
                    
                    # Apply speed overrides based on motion type
                    if next_motion == "forward":
                        next_speed = float(self._cfg("FORWARD_SPD", config.FORWARD_SPD))
                    elif next_motion == "backward":
                        next_speed = float(self._cfg("BACK_SPD", config.BACK_SPD))
                    elif next_motion in ["left", "right"]:
                        next_speed = float(self._cfg("TURN_SPD", config.TURN_SPD))
                    if self.stuck_cooldown > 0:
                        self.stuck_cooldown -= 1
                    else:
                        if len(self.dist_hist) == config.STUCK_STEPS:
                            spread = max(self.dist_hist) - min(self.dist_hist)
                            if spread < config.STUCK_DELTA_CM:
                                import random
                                turn_dir = random.choice(["left", "right"])
                                self.queued_moves = [
                                    ("backward", self._cfg("BACK_SPD", config.BACK_SPD), self._cfg("BACK_TICKS", config.BACK_TICKS)),
                                    (turn_dir,  self._cfg("TURN_SPD", config.TURN_SPD),  self._cfg("NUDGE_TICKS", config.NUDGE_TICKS)),
                                ]
                                notes = f"STUCK: Δ={spread:.1f}cm/{config.STUCK_STEPS}steps -> back {config.BACK_TICKS} + {turn_dir} {config.NUDGE_TICKS}"
                                stuck_triggered = 1
                                self.stuck_cooldown = self._cfg("STUCK_COOLDOWN_STEPS", config.STUCK_COOLDOWN_STEPS)
                                next_motion, next_speed = ("forward", self._cfg("FORWARD_SPD", config.FORWARD_SPD))
                                self.dist_hist.clear()
                    self.current_motion, self.current_speed = next_motion, next_speed

                # log
                self.writer([
                    "AUTO",
                    front_d,
                    left_d,
                    right_d,
                    exec_motion,
                    exec_speed,
                    next_motion if not self.queued_moves else self.queued_moves[0][0],
                    next_speed if not self.queued_moves else self.queued_moves[0][1],
                    notes,
                    stuck_triggered,
                    len(self.queued_moves)
                ])
                # broadcast
                # Broadcast the state with all sensor readings
                # Get fresh sensor readings
                distances = {name: s.distance_cm() for name, s in self.sensors.items()}
                front_d = distances.get('front', float('inf'))
                left_d = distances.get('left', float('inf'))
                right_d = distances.get('right', float('inf'))
                
                print(f"[DEBUG] Sensor readings - Front: {front_d:.1f}cm, Left: {left_d:.1f}cm, Right: {right_d:.1f}cm")
                
                state = {
                    "mode": "AUTO" if self.auto_mode else "REMOTE",
                    "front_distance_cm": (None if front_d == float('inf') else round(front_d, 2)),
                    "left_distance_cm": (None if left_d == float('inf') else round(left_d, 2)),
                    "right_distance_cm": (None if right_d == float('inf') else round(right_d, 2)),
                    "executed_motion": exec_motion,
                    "executed_speed": round(exec_speed, 2),
                    "next_motion": (next_motion if not self.queued_moves else self.queued_moves[0][0]),
                    "next_speed": (next_speed if not self.queued_moves else self.queued_moves[0][1]),
                    "notes": notes,
                    "stuck": stuck_triggered,
                    "queue_len": len(self.queued_moves),
                    "log_file": self.log_file,
                }
                print(f"[DEBUG] Sending to dashboard: {state}")
                self._broadcast(state)
                try:
                    self.hub.set_state({
                        "mode": "AUTO" if self.auto_mode else "REMOTE",
                        "front_distance_cm": (None if front_d == float('inf') else round(front_d, 2)),
                        "left_distance_cm": (None if left_d == float('inf') else round(left_d, 2)),
                        "right_distance_cm": (None if right_d == float('inf') else round(right_d, 2)),
                        "executed_motion": exec_motion,
                        "executed_speed": round(exec_speed, 2),
                        "next_motion": (self.queued_moves[0][0] if self.queued_moves else self.current_motion),
                        "next_speed": (self.queued_moves[0][1] if self.queued_moves else self.current_speed),
                        "notes": notes,
                        "stuck": stuck_triggered,
                        "queue_len": len(self.queued_moves),
                        "log_file": self.log_file,
                    })
                except Exception:
                    pass
        finally:
            try:
                self.robot.stop()
            except Exception:
                pass
            try:
                self.sensor.close()
            except Exception:
                pass

    def _duration_for_motion(self, motion: str):
        if motion in ["forward", "backward"]:
            return float(self._cfg("MOVE_TICK_S", config.MOVE_TICK_S))
        elif motion in ["left", "right"]:
            return float(self._cfg("TURN_TICK_S", config.TURN_TICK_S))
        else:
            return float(self._cfg("TICK_S", config.TICK_S))
