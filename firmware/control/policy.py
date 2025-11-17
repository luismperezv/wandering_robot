try:
    from firmware import config
except Exception:
    import config  # type: ignore

import random
from collections.abc import Collection
from typing import Tuple, Optional


def is_robot_stuck(distance_history: Collection[float], current_motion: str, config) -> Tuple[bool, str, int]:
    """
    Determine if the robot is stuck based on recent distance readings.
    
    Args:
        distance_history: Collection of recent distance measurements
        current_motion: Current motion command
        config: Configuration object with STUCK_* constants
        
    Returns:
        Tuple of (is_stuck, notes, cooldown_steps)
    """
    # Only check when we have exactly STUCK_STEPS measurements and are moving forward/backward
    if (not distance_history or 
        len(distance_history) != config.STUCK_STEPS or
        current_motion not in ["forward", "backward"]):
        return False, "", 0
    
    # Get the readings (should be exactly STUCK_STEPS long)
    readings = list(distance_history)
    
    # Calculate the spread of the readings
    spread = max(readings) - min(readings)
    
    # Debug output
    print("\n" + "="*60)
    print(f"[STUCK_CHECK] Motion: {current_motion}")
    print(f"Last {config.STUCK_STEPS} readings: {[f'{r:.1f}' for r in readings]}")
    print(f"Spread: {spread:.1f}cm, Threshold: {config.STUCK_DELTA_CM}cm")
    print("="*60 + "\n")
    
    # If the spread is too small, we're not moving much
    if spread < config.STUCK_DELTA_CM:
        notes = f"STUCK: Δ={spread:.1f}cm/{config.STUCK_STEPS}steps -> back {config.BACK_TICKS} + turn {config.NUDGE_TICKS}"
        return True, notes, config.STUCK_COOLDOWN_STEPS
    
    return False, "", 0


def decide_next_motion(distance_cm: float, prev_motion: str) -> tuple[str, float, str]:
    """
    Autonomous policy: returns (next_motion, speed, notes)
    """
    if distance_cm == float('inf'):
        return ("stop", 0.0, "no-echo/open: waiting for valid reading")

    if distance_cm <= config.STOP_CM:
        direction = random.choice(["left", "right"])
        return (direction, config.TURN_SPD, f"obstacle@{distance_cm:.1f}cm")

    if distance_cm >= config.CLEAR_CM:
        return ("forward", config.FORWARD_SPD, "clear")

    if prev_motion in ("left", "right"):
        return (prev_motion, config.TURN_SPD * 0.8, f"bias-{prev_motion}@{distance_cm:.1f}cm")

    return ("forward", config.FORWARD_SPD * 0.8, f"caution@{distance_cm:.1f}cm")