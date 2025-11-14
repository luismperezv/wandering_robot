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
    # Debug print to help diagnose stuck detection
    print(f"Stuck check - motion: {current_motion}, history len: {len(distance_history) if distance_history else 0}")
    if distance_history:
        print(f"Distance history (last {min(5, len(distance_history))}): {list(distance_history)[-5:]}")
    
    # Only check when moving forward/backward and we have enough history
    if (not distance_history or 
        current_motion not in ["forward", "backward"] or 
        len(distance_history) < config.STUCK_STEPS):
        return False, "", 0
    
    # Calculate the spread of recent distance measurements
    recent_history = list(distance_history)[-config.STUCK_STEPS:]
    spread = max(recent_history) - min(recent_history)
    
    # Debug print
    print(f"Stuck check - spread: {spread:.2f}cm (threshold: {config.STUCK_DELTA_CM}cm), motion: {current_motion}")
    
    # If the spread is too small, we're not moving much
    if spread < config.STUCK_DELTA_CM:
        notes = f"STUCK: Δ={spread:.1f}cm/{len(recent_history)}steps -> back {config.BACK_TICKS} + turn {config.NUDGE_TICKS}"
        print(f"Stuck detected! {notes}")
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