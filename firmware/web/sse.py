import threading
import queue
import json
from typing import Optional, Dict, Any


class _SSEClient:
    def __init__(self):
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.alive = True


class DashboardHub:
    def __init__(self):
        self._clients: list[_SSEClient] = []
        self._lock = threading.Lock()
        self._last_state: Optional[Dict[str, Any]] = None

    def add_client(self, client: _SSEClient):
        with self._lock:
            self._clients.append(client)

    def remove_client(self, client: _SSEClient):
        with self._lock:
            try:
                self._clients.remove(client)
            except ValueError:
                pass

    def broadcast(self, data: str):
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            try:
                c.queue.put_nowait(data)
            except Exception:
                c.alive = False

    def set_state(self, state: Dict[str, Any]):
        self._last_state = state
        try:
            self.broadcast(json.dumps(state))
        except Exception:
            pass

    def get_state(self) -> Optional[Dict[str, Any]]:
        return self._last_state


