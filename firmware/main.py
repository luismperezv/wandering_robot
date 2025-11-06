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
except Exception:
    import config  # type: ignore
    from hardware.ultrasonic import PigpioUltrasonic  # type: ignore
    from web.server import start_dashboard_server  # type: ignore
    from control.keyboard import CbreakKeyboard  # type: ignore
    from control.controller import Controller  # type: ignore
    from config_manager import ConfigManager  # type: ignore


def main():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    server = None
    hub = None
    commands_q = None
    overrides_path = os.path.join(project_root, "firmware", "config_overrides.json")
    cfg_mgr = ConfigManager(config, overrides_path)
    try:
        server, _, hub, commands_q = start_dashboard_server(project_root, port=int(os.environ.get("DASHBOARD_PORT", str(config.DASHBOARD_PORT))), config_manager=cfg_mgr)
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
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            row[0],
            ("" if row[1] == float('inf') else f"{row[1]:.2f}"),
            row[2], f"{row[3]:.2f}",
            row[4], f"{row[5]:.2f}",
            row[6], row[7], row[8]
        ])
        f.flush()

    kb = CbreakKeyboard()
    kb.start()
    atexit.register(kb.stop)
    print("Controls: Enter=toggle MANUAL, WASD=drive. Ctrl+C to quit.")
    print(f"Logging to {log_file}.")

    controller = Controller(robot, sensor, write_row, hub, commands_q, keyboard=kb, log_file=log_file, config_manager=cfg_mgr)
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


