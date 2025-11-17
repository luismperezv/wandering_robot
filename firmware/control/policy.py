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
    # Only check when we have at least STUCK_STEPS measurements
    if not distance_history or len(distance_history) < config.STUCK_STEPS:
        return False, "", 0
    
    # Get the last STUCK_STEPS readings
    recent_readings = list(distance_history)[-config.STUCK_STEPS:]
    
    # Calculate the spread and average of the most recent distance measurements
    spread = max(recent_readings) - min(recent_readings)
    avg_distance = sum(recent_readings) / len(recent_readings)
    
    # Calculate the percentage change from the average
    max_change = max(abs(r - avg_distance) for r in recent_readings)
    percent_change = (max_change / avg_distance * 100) if avg_distance > 0 else 100
    
    # Debug print to help diagnose issues - make it very visible
    print("\n" + "="*80)
    print(f"[STUCK_DEBUG] Motion: {current_motion}")
    print(f"Recent distances: {[f'{r:.1f}' for r in recent_readings]}")
    print(f"Spread: {spread:.1f}cm, Threshold: {config.STUCK_DELTA_CM}cm")
    print(f"Average: {avg_distance:.1f}cm, Max change: {max_change:.1f}cm ({percent_change:.1f}%)")
    print("="*80 + "\n")
    
    # If the spread is too small and we have a reasonable distance reading
    if spread < config.STUCK_DELTA_CM and avg_distance > 10:  # Ignore if too close to an object
        notes = (f"STUCK: Δ={spread:.1f}cm/{config.STUCK_STEPS}steps "
                f"(avg={avg_distance:.1f}cm, maxΔ={max_change:.1f}cm) -> "
                f"back {config.BACK_TICKS} + turn {config.NUDGE_TICKS}")
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