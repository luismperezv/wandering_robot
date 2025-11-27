try:
    from firmware import config
except Exception:
    import config  # type: ignore

import random
from collections import deque
from collections.abc import Collection
from typing import Tuple, Optional


class Policy:
    """
    Stateful policy class that manages all decision-making logic for the robot.
    
    This class owns:
    - Distance history tracking for stuck detection
    - Cooldown management after recovery
    - Recovery move queue
    - All navigation and obstacle avoidance decisions
    """
    
    def __init__(self, config_obj=None):
        """
        Initialize the policy with configuration.
        
        Args:
            config_obj: Configuration object or module with settings
        """
        self.config = config_obj if config_obj is not None else config
        
        # State management
        self.dist_hist = deque(maxlen=self.config.STUCK_STEPS)
        self.stuck_cooldown = 0
        self.queued_moves = []  # List of (motion, speed, ticks_remaining) tuples
        self.consecutive_no_echo = 0  # Track consecutive invalid readings
        
    def update_distance(self, front_distance_cm: float):
        """
        Update the distance history with a new reading.
        
        Args:
            front_distance_cm: Latest front distance reading in cm
        """
        if front_distance_cm != float('inf'):
            self.dist_hist.append(front_distance_cm)
            self.consecutive_no_echo = 0  # Reset no-echo counter on valid reading
            
            # Log distance history when we have a full set of readings
            if len(self.dist_hist) == self.config.STUCK_STEPS:
                recent = list(self.dist_hist)
                spread = max(recent) - min(recent)
                if spread < self.config.STUCK_DELTA_CM * 1.5:
                    print(f"[DISTANCE] Spread: {spread:.1f}cm (threshold: {self.config.STUCK_DELTA_CM}cm)")
        else:
            # Track consecutive invalid readings
            self.consecutive_no_echo += 1
            if self.consecutive_no_echo >= self.config.STUCK_STEPS:
                print(f"[NO_ECHO] {self.consecutive_no_echo} consecutive invalid readings (threshold: {self.config.STUCK_STEPS})")
    
    def get_next_action(self, prev_motion: str, front_distance_cm: float) -> Tuple[str, float, str, bool]:
        """
        Decide the next action for the robot.
        
        This method handles:
        - Recovery move execution if queued
        - Stuck detection and recovery queuing
        - Normal navigation decisions
        
        Args:
            prev_motion: Previous motion command
            front_distance_cm: Current front distance reading
            
        Returns:
            Tuple of (motion, speed, notes, is_recovery)
            - motion: The motion command to execute
            - speed: The speed to use
            - notes: Debug/status notes
            - is_recovery: True if this is a recovery move
        """
        # Process queued recovery moves first
        if self.queued_moves:
            next_motion, next_speed, ticks_remaining = self.queued_moves[0]
            
            # Decrement the tick counter
            ticks_remaining -= 1
            
            # Update or remove the move
            if ticks_remaining > 0:
                self.queued_moves[0] = (next_motion, next_speed, ticks_remaining)
            else:
                self.queued_moves.pop(0)
            
            notes = f"recovery_{next_motion}_{ticks_remaining}"
            return (next_motion, next_speed, notes, True)
        
        # Get normal navigation decision
        next_motion, next_speed, notes = self.decide_next_motion(front_distance_cm, prev_motion)
        
        # Check for stuck condition if we're not in cooldown
        is_stuck = False
        stuck_notes = ""
        cooldown = 0
        
        # Check for no-echo stuck (consecutive invalid readings)
        if self.consecutive_no_echo >= self.config.STUCK_STEPS and self.stuck_cooldown <= 0:
            is_stuck = True
            stuck_notes = f"NO_ECHO_STUCK: {self.consecutive_no_echo} invalid readings -> back {self.config.BACK_TICKS} + turn {self.config.NUDGE_TICKS}"
            cooldown = self.config.STUCK_COOLDOWN_STEPS
        # Check for normal stuck (distance not changing)
        elif len(self.dist_hist) >= self.config.STUCK_STEPS and self.stuck_cooldown <= 0:
            is_stuck, stuck_notes, cooldown = self.is_robot_stuck(
                self.dist_hist,
                next_motion,
                self.config
            )
        
        # Trigger recovery if stuck
        if is_stuck:
            # Queue recovery moves
            turn_dir = random.choice(["left", "right"])
            self.queued_moves = [
                ("backward", self.config.BACK_SPD, self.config.BACK_TICKS),
                (turn_dir, self.config.TURN_SPD, self.config.NUDGE_TICKS),
            ]
            
            # Set cooldown and update notes
            self.stuck_cooldown = cooldown
            notes = stuck_notes
            
            # Reset no-echo counter and clear history
            self.consecutive_no_echo = 0
            if len(self.dist_hist) > self.config.STUCK_STEPS:
                self.dist_hist = deque(list(self.dist_hist)[-self.config.STUCK_STEPS:])
            
            print(f"\n[RECOVERY] Executing recovery maneuver: {stuck_notes}")
            
            # Return the first recovery move
            return self.get_next_action(prev_motion, front_distance_cm)
        
        # Decrement cooldown if needed
        if self.stuck_cooldown > 0:
            self.stuck_cooldown -= 1
            notes += f" [cooldown={self.stuck_cooldown}]"
        
        return (next_motion, next_speed, notes, False)
    
    def is_stuck_triggered(self) -> bool:
        """
        Check if the robot is currently executing recovery moves.
        
        Returns:
            True if recovery moves are queued, False otherwise
        """
        return len(self.queued_moves) > 0
    
    def get_queue_length(self) -> int:
        """
        Get the number of queued recovery moves.
        
        Returns:
            Number of queued moves
        """
        return len(self.queued_moves)
    
    def is_robot_stuck(self, distance_history: Collection[float], next_motion: str, config) -> Tuple[bool, str, int]:
        """
        Determine if the robot is stuck based on recent distance readings.
        
        Args:
            distance_history: Collection of recent distance measurements
            next_motion: Next planned motion command
            config: Configuration object with STUCK_* constants
            
        Returns:
            Tuple of (is_stuck, notes, cooldown_steps)
        """
        # Only check when we have exactly STUCK_STEPS measurements and are about to move forward/backward
        if (not distance_history or 
            len(distance_history) != config.STUCK_STEPS or
            next_motion not in ["forward", "backward"]):
            return False, "", 0
        
        # Get the readings (should be exactly STUCK_STEPS long)
        readings = list(distance_history)
        
        # Calculate the spread of the readings
        spread = max(readings) - min(readings)
        
        # Log the stuck check details
        print(f"[STUCK_CHECK] Motion: {next_motion}, Spread: {spread:.1f}cm (threshold: {config.STUCK_DELTA_CM}cm)")
        
        # If the spread is too small, we're not moving much
        if spread < config.STUCK_DELTA_CM:
            notes = f"STUCK: Δ={spread:.1f}cm/{config.STUCK_STEPS}steps -> back {config.BACK_TICKS} + turn {config.NUDGE_TICKS}"
            return True, notes, config.STUCK_COOLDOWN_STEPS
        
        return False, "", 0

    def decide_next_motion(self, distance_cm: float, prev_motion: str) -> tuple[str, float, str]:
        """
        Autonomous policy: returns (next_motion, speed, notes)
        """
        if distance_cm == float('inf'):
            return ("stop", 0.0, "no-echo: waiting for valid reading")

        if distance_cm <= self.config.STOP_CM:
            direction = random.choice(["left", "right"])
            return (direction, self.config.TURN_SPD, f"obstacle@{distance_cm:.1f}cm")

        if distance_cm >= self.config.CLEAR_CM:
            return ("forward", self.config.FORWARD_SPD, "clear")

        if prev_motion in ("left", "right"):
            return (prev_motion, self.config.TURN_SPD * 0.8, f"bias-{prev_motion}@{distance_cm:.1f}cm")

        return ("forward", self.config.FORWARD_SPD * 0.8, f"caution@{distance_cm:.1f}cm")