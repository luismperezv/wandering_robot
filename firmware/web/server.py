import http.server
import socket
import json
import os
import queue
import threading
import time
import urllib.parse
import datetime
import csv
from glob import glob
print(f"DEBUG - datetime module available: {hasattr(datetime, 'datetime')}")
from pathlib import Path
from functools import partial
from typing import Optional, Dict, Any, List, Union

try:
    from firmware.web.sse import DashboardHub, _SSEClient
except Exception:
    from web.sse import DashboardHub, _SSEClient  # type: ignore


def _get_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        try:
            sock.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return ip


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    hub: DashboardHub = None
    commands: "queue.Queue[object]" = None
    config_manager = None
    policy_manager = None
    controller = None  # Add controller class variable
    allow_cors_all: bool = True

    def log_message(self, format, *args):
        return

    def _set_cors(self):
        if self.allow_cors_all:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        if not body:
            return None
        try:
            print(f"Raw request body: {body}")
            data = json.loads(body.decode("utf-8"))
            print(f"Parsed JSON data: {data}")
            return data
        except Exception as e:
            print(f"ERROR in _read_json: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
            
    def _handle_command_sequence(self, controller, commands):
        """Process a sequence of movement commands and return the execution log.
        
        Args:
            commands: List of command objects with 'name', 'speed', and 'duration_s' or 'duration_ms'
            
        Returns:
            dict: Response with success status and execution log
        """
        print("\n--- Executing Command Sequence ---")
        print(f"Controller type: {type(controller).__name__}")
        print(f"Commands: {commands}")
        try:
            result = self._handle_command_sequence_impl(commands)
            print(f"Command execution result: {result}")
            return result
        except Exception as e:
            print(f"ERROR in _handle_command_sequence: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def _handle_command_sequence_impl(self, commands):
        if not commands or not isinstance(commands, list):
            return {"success": False, "error": "No commands provided"}
            
        log = []
        
        for cmd in commands:
            if not isinstance(cmd, dict) or 'name' not in cmd:
                return {"success": False, "error": f"Invalid command: {cmd}"}
                
            # Prepare the command for the queue
            cmd_data = {
                "type": "cmd",
                "name": cmd["name"],
                "speed": cmd.get("speed"),
                "duration_ms": cmd.get("duration_ms"),
                "duration_s": cmd.get("duration_s")
            }
            
            try:
                # Put the command in the queue
                self.commands.put_nowait(cmd_data)
                
                # Get the current state for logging (this is a simplified version)
                state = self.hub.get_state() if hasattr(self.hub, "get_state") else {}
                
                # Add to log
                log_entry = {
                    "timestamp_iso": datetime.datetime.now().isoformat(),  # Using the datetime module
                    "mode": state.get("mode", "REMOTE"),
                    "front_distance_cm": state.get("front_distance_cm"),
                    "left_distance_cm": state.get("left_distance_cm"),
                    "right_distance_cm": state.get("right_distance_cm"),
                    "executed_motion": cmd["name"],
                    "executed_speed": cmd.get("speed", 0.0),
                    "next_motion": "",  # Not available in this simple implementation
                    "next_speed": 0.0,  # Not available in this simple implementation
                    "notes": f"Executed {cmd['name']}",
                    "stuck_triggered": 0,
                    "queue_len": self.commands.qsize()
                }
                log.append(log_entry)
                
                # Small delay to allow the command to be processed
                time.sleep(0.1)
                
            except Exception as e:
                print(f"ERROR in command execution: {str(e)}")
                import traceback
                traceback.print_exc()
                return {
                    "success": False, 
                    "error": f"Error executing command {cmd}: {str(e)}",
                    "debug": {
                        "error_type": str(type(e).__name__),
                        "error_message": str(e),
                        "available_modules": list(sys.modules.keys())
                    },
                    "log": log
                }
        
        return {"success": True, "log": log}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/tuning/data":
            # Serve a specific or latest distance_tuning CSV + model JSON for plotting
            try:
                file_name = query.get("file", [None])[0]
                data = self._load_tuning(file_name=file_name)
                if not data:
                    self.send_response(404)
                    self._set_cors()
                    self.end_headers()
                    return
                self.send_response(200)
                self._set_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            except Exception as e:
                print(f"[tuning] error: {e}")
                self.send_response(500)
                self._set_cors()
                self.end_headers()
            return
        if parsed.path == "/api/tuning/list":
            try:
                runs = self._list_tuning_runs()
                self.send_response(200)
                self._set_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"runs": runs}).encode("utf-8"))
            except Exception as e:
                print(f"[tuning] list error: {e}")
                self.send_response(500)
                self._set_cors()
                self.end_headers()
            return
        if parsed.path == "/api/events":
            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            client = _SSEClient()
            self.hub.add_client(client)
            try:
                self.wfile.write(b": hello\n\n")
                self.wfile.flush()
                while client.alive:
                    try:
                        msg = client.queue.get(timeout=15)
                        payload = ("data: " + msg + "\n\n").encode("utf-8")
                        self.wfile.write(payload)
                        self.wfile.flush()
                    except queue.Empty:
                        try:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                        except Exception:
                            break
            except Exception:
                pass
            finally:
                client.alive = False
                self.hub.remove_client(client)
            return

        if parsed.path == "/api/status":
            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            state = self.hub.get_state() if hasattr(self.hub, "get_state") else {}
            self.wfile.write(json.dumps(state or {}).encode("utf-8"))
            return

        if parsed.path == "/api/config":
            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            snap = self.config_manager.snapshot() if self.config_manager else {}
            self.wfile.write(json.dumps(snap).encode("utf-8"))
            return

        if parsed.path == "/api/policy":
            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            status = self.policy_manager.status() if self.policy_manager else {"name": "default"}
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        if parsed.path == "/api/openapi.yaml":
            # First try the project root (where the server is started from)
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            path = os.path.join(root, "openapi.yaml")
            
            # If not found, try one more level up (in case we're in firmware/web/)
            if not os.path.exists(path):
                root = os.path.dirname(root)
                path = os.path.join(root, "openapi.yaml")
                
            if os.path.exists(path):
                print(f"[server] Serving OpenAPI spec from: {path}")
                with open(path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self._set_cors()
                self.send_header("Content-Type", "application/yaml")
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404)
            self._set_cors()
            self.end_headers()
            return

        if parsed.path == "/api/docs":
            html = (
                "<!doctype html><html><head><meta charset='utf-8'/>"
                "<title>Wandering Robot API Docs</title>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
                "<style>html,body,#redoc{height:100%;margin:0;background:#ffffff;color:#1b1f23;font-family:ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial}</style>"
                "</head><body>"
                "<div id='redoc'></div>"
                "<script src='https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js'></script>"
                "<script>Redoc.init('/api/openapi.yaml', {theme: {colors: {primary: {main: '#1f6feb'}}, typography: {fontSize: '14px', lineHeight: '1.5'}}}, document.getElementById('redoc'));</script>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)
            return

        return super().do_GET()

    def do_POST(self):
        print("\n=== New POST Request ===")
        print(f"Path: {self.path}")
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/command_seq":
            print("Handling /api/command_seq request")
            obj = self._read_json() or {}
            commands = obj.get("commands", [])
            
            # Get the controller instance from the server
            print("\n--- Controller Check ---")
            controller = getattr(self, 'controller', None)
            print(f"Controller from self: {controller}")
            if not controller:
                print("Controller not found in self, checking class variables...")
                controller = getattr(DashboardHandler, 'controller', None)
                print(f"Controller from class: {controller}")
            
            if not controller:
                self.send_response(500)
                self._set_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Controller not available"}).encode("utf-8"))
                return
                
            if not isinstance(commands, list):
                self.send_response(400)
                self._set_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Commands must be a list"}).encode("utf-8"))
                return
            
            try:
                # Delegate command sequence execution to the controller
                result = controller.execute_command_sequence(commands)
                
                # Send the response
                self.send_response(200 if result.get("success", False) else 400)
                self._set_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._set_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return
            
        if parsed.path == "/api/cmd":
            obj = self._read_json() or {}
            name = obj.get("name")
            if not name:
                self.send_response(400); self._set_cors(); self.end_headers(); return
            try:
                self.commands.put_nowait({
                    "type":"cmd",
                    "name":name,
                    "speed":obj.get("speed"),
                    "duration_ms":obj.get("duration_ms"),
                    "duration_s":obj.get("duration_s"),
                })
            except Exception:
                pass
            self.send_response(204); self._set_cors(); self.end_headers(); return

        if parsed.path == "/api/mode":
            obj = self._read_json() or {}
            mode = obj.get("mode")
            if mode not in ("AUTO","MANUAL","REMOTE"):
                self.send_response(400); self._set_cors(); self.end_headers(); return
            try:
                self.commands.put_nowait({"type":"mode","mode":mode})
            except Exception:
                pass
            self.send_response(200); self._set_cors(); self.send_header("Content-Type","application/json"); self.end_headers()
            self.wfile.write(json.dumps({"ok":True,"mode":mode}).encode("utf-8"))
            return

        self.send_response(404); self._set_cors(); self.end_headers()

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/policy/code":
            obj = self._read_json() or {}
            code = obj.get("code")
            if not isinstance(code, str):
                self.send_response(400); self._set_cors(); self.end_headers(); return
            try:
                if self.policy_manager:
                    self.policy_manager.set_code(code)
                self.send_response(200); self._set_cors(); self.send_header("Content-Type","application/json"); self.end_headers()
                status = self.policy_manager.status() if self.policy_manager else {"name": "default"}
                payload = {"ok": True}
                payload.update(status)
                self.wfile.write(json.dumps(payload).encode("utf-8"))
            except Exception:
                self.send_response(500); self._set_cors(); self.end_headers()
            return
        self.send_response(404); self._set_cors(); self.end_headers()

    def do_PATCH(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/config":
            obj = self._read_json() or {}
            overrides = obj.get("overrides") or {}
            if not isinstance(overrides, dict):
                self.send_response(400); self._set_cors(); self.end_headers(); return
            if self.config_manager:
                self.config_manager.set_overrides(overrides)
            self.send_response(200); self._set_cors(); self.send_header("Content-Type","application/json"); self.end_headers()
            snap = self.config_manager.snapshot() if self.config_manager else {}
            self.wfile.write(json.dumps({"ok":True, **snap}).encode("utf-8"))
            return
        self.send_response(404); self._set_cors(); self.end_headers()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/policy/code":
            if self.policy_manager:
                self.policy_manager.delete_custom()
            self.send_response(204); self._set_cors(); self.end_headers(); return
        if parsed.path == "/api/config/overrides":
            if self.config_manager:
                was_cleared = self.config_manager.clear_overrides()
                if was_cleared:
                    self.send_response(200)
                    self._set_cors()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    snap = self.config_manager.snapshot()
                    self.wfile.write(json.dumps({"ok": True, "message": "Overrides cleared", **snap}).encode("utf-8"))
                    return
            self.send_response(204); self._set_cors(); self.end_headers(); return
        self.send_response(404); self._set_cors(); self.end_headers()

    # -------- Helpers --------
    @staticmethod
    def _safe_float(value):
        try:
            f = float(value)
            if f != f or f in (float("inf"), float("-inf")):
                return None
            return f
        except Exception:
            return None

    def _list_tuning_runs(self):
        """List available tuning CSV/model files (newest first)."""
        root = Path(self.directory or ".")
        logs_dir = root / "logs"
        if not logs_dir.exists():
            return []

        runs = []
        csv_files = sorted(
            glob(str(logs_dir / "distance_tuning_*.csv")),
            key=os.path.getmtime,
            reverse=True,
        )
        for csv_file in csv_files:
            csv_path = Path(csv_file)
            ts = csv_path.stem.replace("distance_tuning_", "")
            model_name = f"distance_tuning_model_{ts}.json"
            model_path = logs_dir / model_name
            runs.append(
                {
                    "csv": csv_path.name,
                    "model": model_name if model_path.exists() else None,
                    "mtime": os.path.getmtime(csv_path),
                    "iso": datetime.datetime.fromtimestamp(os.path.getmtime(csv_path)).isoformat(timespec="seconds"),
                }
            )
        return runs

    def _load_tuning(self, file_name: str | None = None):
        """Return samples + model for a specific or latest distance_tuning run."""
        root = Path(self.directory or ".")
        logs_dir = root / "logs"
        if not logs_dir.exists():
            return None

        runs = self._list_tuning_runs()
        if not runs:
            return None

        if file_name is None:
            csv_name = runs[0]["csv"]
        else:
            csv_name = file_name

        csv_path = logs_dir / csv_name
        if not csv_path.exists():
            return None

        ts = Path(csv_name).stem.replace("distance_tuning_", "")
        model_path = logs_dir / f"distance_tuning_model_{ts}.json"
        model = {}
        if model_path.exists():
            try:
                with open(model_path, "r") as f:
                    raw_model = json.load(f)
                # sanitize model numbers
                model = {
                    "slope_cm_per_speed_sec": self._safe_float(raw_model.get("slope_cm_per_speed_sec")),
                    "intercept_cm": self._safe_float(raw_model.get("intercept_cm")),
                    "r2": self._safe_float(raw_model.get("r2")),
                    "samples": int(raw_model.get("samples", 0)) if raw_model.get("samples") is not None else 0,
                    "created_at": raw_model.get("created_at"),
                    "note": raw_model.get("note"),
                }
            except Exception:
                model = {}

        samples = []
        try:
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        sample = {
                            "trial": int(row["trial"]),
                            "direction": row["direction"],
                            "speed": self._safe_float(row["speed"]),
                            "duration_s": self._safe_float(row["duration_s"]),
                            "start_cm": self._safe_float(row["start_cm"]),
                            "end_cm": self._safe_float(row["end_cm"]),
                            "actual_delta_cm": self._safe_float(row["actual_delta_cm"]),
                            "cmd_delta_u": self._safe_float(row["cmd_delta_u"]),
                        }
                        samples.append(sample)
                    except Exception:
                        continue
        except Exception as e:
            print(f"[tuning] failed to read {csv_path}: {e}")
            return None

        # Filter out samples missing core fields for plotting
        clean_samples = [
            s for s in samples
            if s.get("cmd_delta_u") is not None and s.get("actual_delta_cm") is not None
        ]

        return {"csv": csv_path.name, "model": model, "samples": clean_samples}


def start_dashboard_server(root_dir: str, port: int = 8000, config_manager=None, policy_manager=None, controller=None):
    hub = DashboardHub()
    # Use the controller's existing command queue instead of creating a new one
    commands_q = controller.commands_q if controller and hasattr(controller, 'commands_q') else queue.Queue()
    handler_cls = partial(DashboardHandler, directory=root_dir)
    try:
        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    except OSError:
        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), handler_cls)
        port = httpd.server_address[1]

    DashboardHandler.hub = hub
    DashboardHandler.commands = commands_q
    DashboardHandler.config_manager = config_manager
    DashboardHandler.policy_manager = policy_manager
    DashboardHandler.controller = controller  # Set the controller

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://{_get_local_ip()}:{port}/dashboard.html"
    print(f"Dashboard available at: {url}")
    return httpd, t, hub, commands_q


