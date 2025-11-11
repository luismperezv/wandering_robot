import os
import csv
from datetime import datetime
import atexit

from gpiozero import CamJamKitRobot

try:
    from firmware import config
    from firmware.hardware.ultrasonic import PigpioUltrasonic
    from firmware.web.server import start_dashboard_server
    from firmware.control.keyboard import CbreakKeyboard
    from firmware.control.controller import Controller
    from firmware.config_manager import ConfigManager
    from firmware.policy_manager import PolicyManager
    from firmware.control.policy import decide_next_motion as default_policy
except Exception:
    import config  # type: ignore
    from hardware.ultrasonic import PigpioUltrasonic  # type: ignore
    from web.server import start_dashboard_server  # type: ignore
    from control.keyboard import CbreakKeyboard  # type: ignore
    from control.controller import Controller  # type: ignore
    from config_manager import ConfigManager  # type: ignore
    from policy_manager import PolicyManager  # type: ignore
    from control.policy import decide_next_motion as default_policy  # type: ignore


def main():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    server = None
    hub = None
    commands_q = None
        overrides_path = os.path.join(project_root, "firmware", "config_overrides.json")
    policy_path = os.path.join(project_root, "firmware", "policies", "custom_policy.py")
    cfg_mgr = ConfigManager(config, overrides_path)
    policy_mgr = PolicyManager(default_policy, policy_path)

    # Debug output
    print("\n=== Debug Info ===")
    print(f"Project root: {project_root}")
    print(f"Config file: {config.__file__}")
    print(f"Overrides path: {overrides_path}")
    print(f"Overrides file exists: {os.path.exists(overrides_path)}")

    if os.path.exists(overrides_path):
        try:
            with open(overrides_path, 'r') as f:
                print(f"Current overrides: {f.read()}")
        except Exception as e:
            print(f"Error reading overrides file: {e}")

    print("\n=== Current Configuration ===")
    print(f"FORWARD_SPD = {config.FORWARD_SPD} (from config.py)")
    print(f"FORWARD_SPD = {cfg_mgr.get('FORWARD_SPD')} (from ConfigManager)")
    print(f"TURN_SPD = {config.TURN_SPD} (from config.py)")
    print(f"TURN_SPD = {cfg_mgr.get('TURN_SPD')} (from ConfigManager)")
    print(f"BACK_SPD = {config.BACK_SPD} (from config.py)")
    print(f"BACK_SPD = {cfg_mgr.get('BACK_SPD')} (from ConfigManager)")
    print("=" * 40 + "\n")

    try:
        server, _, hub, commands_q = start_dashboard_server(project_root, port=int(os.environ.get("DASHBOARD_PORT", str(config.DASHBOARD_PORT))), config_manager=cfg_mgr, policy_manager=policy_mgr)
    except Exception as e:
        print(f"[dashboard] failed to start HTTP server: {e}")
        
    robot = CamJamKitRobot()
    sensor = PigpioUltrasonic(config.TRIG, config.ECHO, max_distance_m=config.MAX_DISTANCE_M, samples=config.SAMPLES_PER_READ)

    log_file = f"runlog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    f = open(log_file, "w", newline="")
    writer = csv.writer(f)
    writer.writerow([
        "timestamp_iso", "mode", "distance_cm",
        "executed_motion", "executed_speed",
        "next_motion", "next_speed",
        "notes", "stuck_triggered", "queue_len"
    ])
    f.flush()

    def write_row(row):
        # row: [mode, d, exec_motion, exec_speed, next_motion, next_speed, notes, stuck, qlen]
        def format_value(value, is_numeric=False):
            if value is None:
                return ""
            if is_numeric and value == float('inf'):
                return ""
            if is_numeric:
                return f"{float(value):.2f}"
            return str(value)
            
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            row[0],  # mode
            format_value(row[1], is_numeric=True),  # distance_cm
            row[2],  # executed_motion
            format_value(row[3], is_numeric=True),  # executed_speed
            row[4],  # next_motion
            format_value(row[5], is_numeric=True),  # next_speed
            row[6],  # notes
            row[7],  # stuck_triggered
            row[8]   # queue_len
        ])
        f.flush()

    kb = CbreakKeyboard()
    kb.start()
    atexit.register(kb.stop)
    print("Controls: Enter=toggle MANUAL, WASD=drive. Ctrl+C to quit.")
    print(f"Logging to {log_file}.")

    controller = Controller(robot, sensor, write_row, hub, commands_q, keyboard=kb, log_file=log_file, config_manager=cfg_mgr, policy_manager=policy_mgr)
    try:
        controller.run()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        try:
            kb.stop()
        except Exception:
            pass
        try:
            if server:
                server.shutdown()
        except Exception:
            pass
        f.close()


if __name__ == "__main__":
    main()


