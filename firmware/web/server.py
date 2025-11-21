import http.server
import socket
import urllib.parse
import json
import threading
import queue
import os
import time
import datetime
from functools import partial

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
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None
            
    def _handle_command_sequence(self, commands):
        """Process a sequence of movement commands and return the execution log.
        
        Args:
            commands: List of command objects with 'name', 'speed', and 'duration_s' or 'duration_ms'
            
        Returns:
            dict: Response with success status and execution log
        """
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
                    "timestamp": state.get("timestamp", datetime.datetime.utcnow().isoformat()),
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
                return {"success": False, "error": f"Error executing command {cmd}: {str(e)}"}
        
        return {"success": True, "log": log}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/events":
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
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/command_seq":
            obj = self._read_json() or {}
            commands = obj.get("commands", [])
            
            # Get the controller instance from the server
            controller = getattr(self, 'controller', None)
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


def start_dashboard_server(root_dir: str, port: int = 8000, config_manager=None, policy_manager=None, controller=None):
    hub = DashboardHub()
    commands_q: "queue.Queue[object]" = queue.Queue()
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


