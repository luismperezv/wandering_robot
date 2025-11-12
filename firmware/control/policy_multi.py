try:
    from firmware import config
except Exception:
    import config  # type: ignore

import random
from typing import Dict, Tuple


def decide_next_motion(distances: Dict[str, float], prev_motion: str) -> Tuple[str, float, str]:
    """
    Autonomous policy that uses multiple ultrasonic sensors to make decisions.
    
    Args:
        distances: Dictionary of sensor names to distance readings in cm
        prev_motion: The previous motion command that was executed
        
    Returns:
        Tuple of (next_motion, speed, reason)
    """
    # Get distances with fallback to infinity if sensor not found
    front_dist = distances.get('front', float('inf'))
    left_dist = distances.get('left', float('inf'))
    right_dist = distances.get('right', float('inf'))
    
    # Check for immediate obstacles
    if front_dist <= config.STOP_CM:
        # If both sides are blocked, prefer to turn in the direction with more space
        if left_dist > right_dist and left_dist > config.STOP_CM * 1.5:
            return ("left", config.TURN_SPD, f"obstacle front-left@{front_dist:.1f}cm,{left_dist:.1f}cm")
        elif right_dist > config.STOP_CM * 1.5:
            return ("right", config.TURN_SPD, f"obstacle front-right@{front_dist:.1f}cm,{right_dist:.1f}cm")
        elif left_dist > right_dist:
            return ("left", config.TURN_SPD, f"obstacle front@{front_dist:.1f}cm, left turn")
        else:
            return ("right", config.TURN_SPD, f"obstacle front@{front_dist:.1f}cm, right turn")
    
    # If we're in the middle of a turn, continue it
    if prev_motion in ("left", "right"):
        # Only continue the turn if there's enough space in front
        if front_dist > config.STOP_CM * 1.5:
            return (prev_motion, config.TURN_SPD * 0.8, f"continuing {prev_motion} turn")
    
    # Check for obstacles on the sides that might be too close
    if left_dist < config.STOP_CM * 0.7:
        return ("right", config.TURN_SPD * 0.7, f"obstacle too close on left@{left_dist:.1f}cm")
    if right_dist < config.STOP_CM * 0.7:
        return ("left", config.TURN_SPD * 0.7, f"obstacle too close on right@{right_dist:.1f}cm")
    
    # If we have a clear path forward, go forward
    if front_dist > config.CLEAR_CM and min(left_dist, right_dist) > config.STOP_CM * 1.5:
        return ("forward", config.FORWARD_SPD, "clear path")
    
    # If we're close to an obstacle but not too close, proceed with caution
    if front_dist > config.STOP_CM * 1.5:
        return ("forward", config.FORWARD_SPD * 0.7, f"approaching obstacle@{front_dist:.1f}cm")
    
    # Default to a gentle turn if we're not sure what to do
    direction = random.choice(["left", "right"])
    return (direction, config.TURN_SPD * 0.6, f"exploring {direction}")


def get_sensor_status(distances: Dict[str, float]) -> str:
    """Get a human-readable status of all sensors."""
    return ", ".join(f"{name}: {dist:.1f}cm" for name, dist in distances.items())
