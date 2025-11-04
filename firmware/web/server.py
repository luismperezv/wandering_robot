import http.server
import socket
import urllib.parse
import json
import threading
import queue
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
    commands: "queue.Queue[str]" = None

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/events":
            self.send_response(200)
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
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/cmd":
            cmd = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            if body:
                try:
                    obj = json.loads(body.decode("utf-8"))
                    cmd = obj.get("name")
                except Exception:
                    cmd = None
            if not cmd:
                qs = urllib.parse.parse_qs(parsed.query)
                vals = qs.get("name")
                if vals:
                    cmd = vals[0]
            if cmd:
                try:
                    self.commands.put_nowait(cmd)
                except Exception:
                    pass
                self.send_response(204)
                self.end_headers()
            else:
                self.send_response(400)
                self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


def start_dashboard_server(root_dir: str, port: int = 8000):
    hub = DashboardHub()
    commands_q: "queue.Queue[str]" = queue.Queue()
    handler_cls = partial(DashboardHandler, directory=root_dir)
    try:
        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    except OSError:
        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), handler_cls)
        port = httpd.server_address[1]

    DashboardHandler.hub = hub
    DashboardHandler.commands = commands_q

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://{_get_local_ip()}:{port}/dashboard.html"
    print(f"Dashboard available at: {url}")
    return httpd, t, hub, commands_q


