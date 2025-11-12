try:
    from firmware import config
except Exception:
    import config  # type: ignore

import random


def decide_next_motion(distance_cm: float, prev_motion: str) -> tuple[str, float, str]:
    """
    Autonomous policy: returns (next_motion, speed, notes)
    """
    if distance_cm == float('inf'):
        return ("forward", config.FORWARD_SPD, "no-echo/open")

    if distance_cm <= config.STOP_CM:
        direction = random.choice(["left", "right"])
        return (direction, config.TURN_SPD, f"obstacle@{distance_cm:.1f}cm")

    if distance_cm >= config.CLEAR_CM:
        return ("forward", config.FORWARD_SPD, "clear")

    if prev_motion in ("left", "right"):
        return (prev_motion, config.TURN_SPD * 0.8, f"bias-{prev_motion}@{distance_cm:.1f}cm")

    return ("forward", config.FORWARD_SPD * 0.8, f"caution@{distance_cm:.1f}cm")