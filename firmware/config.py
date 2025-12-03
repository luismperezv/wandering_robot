"""Runtime configuration and tunables for the wandering robot."""

# --------- Timing & Speeds ----------
TICK_S           = 0.5   # discrete step duration (seconds)
# Independent per-motion step durations
MOVE_TICK_S      = 0.5   # forward/backward step duration (seconds)
TURN_TICK_S      = 0.5   # left/right step duration (seconds)
FORWARD_SPD      = 0.7
TURN_SPD         = 0.45
BACK_SPD         = 0.6

# --------- Distance Heuristics ----------
STOP_CM          = 15.0  # too close -> evasive turn
CLEAR_CM         = 30.0  # comfortable clear
MAX_DISTANCE_M   = 3.0
SAMPLES_PER_READ = 3

# --- Stuck detection ---
STUCK_DELTA_CM   = 5.0   # consider "no change" if spread < this
STUCK_STEPS      = 3     # look back over this many ticks
BACK_TICKS       = 1     # back up when stuck
NUDGE_TICKS      = 1     # random turn after backoff
STUCK_COOLDOWN_STEPS = 3

# --------- Ultrasonic Pins ----------
# Front sensor (using original pins)
FRONT_TRIG = 19
FRONT_ECHO = 26

# Left sensor
LEFT_TRIG = 6
LEFT_ECHO = 13

# Right sensor
RIGHT_TRIG = 20
RIGHT_ECHO = 21

# --------- Web ----------
DASHBOARD_PORT = 8000

