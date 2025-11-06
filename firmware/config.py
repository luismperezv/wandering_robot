"""Runtime configuration and tunables for the wandering robot."""

# --------- Timing & Speeds ----------
TICK_S           = 0.5   # discrete step duration (seconds)
# Independent per-motion step durations
MOVE_TICK_S      = 0.5   # forward/backward step duration (seconds)
TURN_TICK_S      = 0.5   # left/right step duration (seconds)
FORWARD_SPD      = 0.40
TURN_SPD         = 0.2
BACK_SPD         = 0.2

# --------- Distance Heuristics ----------
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

# --------- Ultrasonic Pins ----------
TRIG = 19
ECHO = 26

# --------- Web ----------
DASHBOARD_PORT = 8000

