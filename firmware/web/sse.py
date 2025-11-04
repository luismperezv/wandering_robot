import threading
import queue


class _SSEClient:
    def __init__(self):
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.alive = True


class DashboardHub:
    def __init__(self):
        self._clients: list[_SSEClient] = []
        self._lock = threading.Lock()

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


