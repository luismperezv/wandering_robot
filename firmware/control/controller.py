from __future__ import annotations
import time
import json
import datetime

try:
    from firmware import config
    from firmware.config_manager import ConfigManager
    from firmware.policy_manager import PolicyManager
except Exception:
    import config  # type: ignore
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
            
    def execute_command_sequence(self, commands):
        """Execute a sequence of commands and return the execution log.
        
        Args:
            commands: List of command dictionaries with 'name', 'speed', 'duration_s' or 'duration_ms'
            
        Returns:
            dict: {
                'success': bool,
                'log': list of command execution logs,
                'error': str (if success is False)
            }
        """
        if not commands or not isinstance(commands, list):
            return {"success": False, "error": "No commands provided"}
            
        log = []

        for cmd in commands:
            if not isinstance(cmd, dict) or 'name' not in cmd:
                return {
                    "success": False, 
                    "error": f"Invalid command: {cmd}",
                    "log": log
                }

            name = cmd["name"]

            # Get duration in seconds (convert from ms if needed)
            duration_s = cmd.get('duration_s')
            if duration_s is None and 'duration_ms' in cmd:
                duration_s = cmd['duration_ms'] / 1000.0
            if duration_s is None:
                # Default duration comes from configured tick values (with overrides applied)
                duration_s = self._duration_for_motion(name)

            # Resolve speed, defaulting to configured values (respecting overrides)
            speed = cmd.get("speed")
            if speed is None:
                if name == "forward":
                    speed = float(self._cfg("FORWARD_SPD", config.FORWARD_SPD))
                elif name == "backward":
                    speed = float(self._cfg("BACK_SPD", config.BACK_SPD))
                elif name in ("left", "right"):
                    speed = float(self._cfg("TURN_SPD", config.TURN_SPD))
                else:
                    # Fallback – use forward speed for unknown motions
                    speed = float(self._cfg("FORWARD_SPD", config.FORWARD_SPD))
            else:
                speed = float(speed)

            # Prepare the command for the queue
            cmd_data = {
                "type": "cmd",
                "name": name,
                "speed": speed,
                "duration_s": duration_s
            }
            
            try:
                # Get state before command execution
                state = self._get_current_state()
                
                # Execute the command
                self.commands_q.put_nowait(cmd_data)
                
                # Wait for the command to complete (duration + small buffer)
                time.sleep(duration_s + 0.1)
                
                # Get state after command execution
                state = self._get_current_state()
                
                # Create log entry
                log_entry = {
                    "timestamp": state.get("timestamp", datetime.datetime.utcnow().isoformat()),
                    "mode": state.get("mode", "REMOTE"),
                    "front_distance_cm": state.get("front_distance_cm"),
                    "left_distance_cm": state.get("left_distance_cm"),
                    "right_distance_cm": state.get("right_distance_cm"),
                    "executed_motion": name,
                    "executed_speed": speed,
                    "next_motion": "",
                    "next_speed": 0.0,
                    "notes": f"Executed {name} for {duration_s:.2f}s",
                    "stuck_triggered": 0,
                    "queue_len": self.commands_q.qsize()
                }
                log.append(log_entry)
                
                # Broadcast the updated state
                self._broadcast({
                    **state,
                    "executed_motion": name,
                    "executed_speed": speed,
                    "log_file": self.log_file
                })
                
            except Exception as e:
                return {
                    "success": False, 
                    "error": f"Error executing command {cmd}: {str(e)}",
                    "log": log
                }
        
        return {"success": True, "log": log}
        
    def _get_current_state(self):
        """Helper method to get the current robot state from the hub."""
        if hasattr(self.hub, 'get_state'):
            state = self.hub.get_state()
            if not isinstance(state, dict):
                state = {}
            return state
        return {}

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
                            print(f"[CMD] Received command: {c}")  # Debug
                            if c.get("type") == "mode":
                                mode = c.get("mode")
                                self.robot.stop()
                                self.auto_mode = (mode == "AUTO")
                            elif c.get("type") == "cmd":
                                name = c.get("name")
                                speed = c.get("speed")
                                duration_ms = c.get("duration_ms")
                                duration_s_req = c.get("duration_s")
                                # Toggle between AUTO and REMOTE modes
                                if name == 'toggle':
                                    self.auto_mode = not self.auto_mode
                                    print(f"[TOGGLE] Mode toggled to: {'AUTO' if self.auto_mode else 'REMOTE'}")
                                    self.robot.stop()
                                    continue
                                if name == 'auto':
                                    self.auto_mode = True
                                    self.robot.stop()
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
                                    
                                    # Execute the move immediately
                                    execute_motion(self.robot, name, float(speed), duration_s)
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
                            elif c == 'auto':
                                self.auto_mode = True
                                self.robot.stop()
                            elif c == 'stop':
                                # Emergency stop - clear all state and stop immediately
                                self.emergency_stop()
                            elif c in ('forward','backward','left','right') and not self.auto_mode:
                                # Only process movement commands in REMOTE mode
                                spd = (self._cfg("FORWARD_SPD", config.FORWARD_SPD) if c == "forward"
                                       else self._cfg("BACK_SPD", config.BACK_SPD) if c == "backward"
                                       else self._cfg("TURN_SPD", config.TURN_SPD))
                                execute_motion(self.robot, c, float(spd), self._duration_for_motion(c))

                # If in emergency stop, stay stopped until explicitly cleared
                if self.emergency_stopped:
                    time.sleep(0.1)
                    continue
                    
                # If in REMOTE mode, idle (no motion)
                if not self.auto_mode:
                    # Only update state if it's changed from the last broadcast
                    if not hasattr(self, '_last_idle_state') or time.time() - getattr(self, '_last_idle_time', 0) > 5.0:
                        # Get readings from all sensors
                        distances = self.sensor.get_distances()
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


                # Auto branch - skip if emergency stopped
                if self.emergency_stopped:
                    time.sleep(0.1)
                    continue
                
                # AUTO MODE LOGIC - only execute when in AUTO mode
                if self.auto_mode:
                    print(f"[AUTO] Starting AUTO mode iteration")  # Debug logging
                    
                    # Get sensor readings for decision making
                    distances = self.sensor.get_distances()
                    front_d = distances.get('front', float('inf'))
                    left_d = distances.get('left', float('inf'))
                    right_d = distances.get('right', float('inf'))
                    
                    # Update policy with current distance reading
                    if self.policy is not None:
                        self.policy.update_distance(front_d)
                        
                        # Get next action from policy
                        next_motion, next_speed, notes, is_recovery = self.policy.get_next_action(
                            self.current_motion, front_d
                        )
                        
                        print(f"[AUTO] Policy decision: {next_motion} @ {next_speed:.2f} (distance: {front_d:.1f}cm, notes: {notes})")  # Debug
                    else:
                        # Fallback if no policy
                        next_motion, next_speed = "stop", 0.0
                        notes = "no_policy"
                        is_recovery = False
                    
                    # Update current motion and speed
                    self.current_motion, self.current_speed = next_motion, next_speed
                    
                    # Execute the motion
                    execute_motion(self.robot, self.current_motion, self.current_speed, 
                                 self._duration_for_motion(self.current_motion))
                    
                    # Get fresh sensor readings for logging
                    distances = self.sensor.get_distances()
                    front_d = distances.get('front', float('inf'))
                    left_d = distances.get('left', float('inf'))
                    right_d = distances.get('right', float('inf'))
                    
                    # Get queue length and stuck status from policy
                    queue_len = self.policy.get_queue_length() if self.policy else 0
                    stuck_triggered = 1 if (self.policy and self.policy.is_stuck_triggered()) else 0
                    mode = "RECOVERY" if is_recovery else "AUTO"
                    
                    # Log the action with all sensor readings
                    self.writer([
                        mode,
                        front_d,
                        left_d,
                        right_d,
                        self.current_motion,
                        self.current_speed,
                        self.current_motion,  # next_motion is current since we just decided
                        self.current_speed,
                        notes,
                        stuck_triggered,
                        queue_len
                    ])
                    # Broadcast the state with all sensor readings
                    state = {
                        "mode": mode,
                        "front_distance_cm": (None if front_d == float('inf') else round(front_d, 2)),
                        "left_distance_cm": (None if left_d == float('inf') else round(left_d, 2)),
                        "right_distance_cm": (None if right_d == float('inf') else round(right_d, 2)),
                        "executed_motion": self.current_motion,
                        "executed_speed": round(self.current_speed, 2),
                        "next_motion": self.current_motion,
                        "next_speed": self.current_speed,
                        "notes": notes,
                        "stuck": stuck_triggered,
                        "queue_len": queue_len,
                        "log_file": self.log_file,
                    }
                    self._broadcast(state)
                    try:
                        self.hub.set_state(state)
                    except Exception:
                        pass
                else:
                    # Not in AUTO mode - reached the bottom of the loop without handling
                    print(f"[DEBUG] End of loop: auto_mode={self.auto_mode}, queued_moves={len(self.queued_moves)}")
                    time.sleep(0.1)  # Prevent busy looping
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
