import os
import sys
import threading
import termios
import tty
import select


class CbreakKeyboard:
    """
    Cbreak-mode non-blocking reader:
      - Enter toggles manual mode
      - WASD: W=forward, S=backward, A=left, D=right
    Ctrl+C still raises KeyboardInterrupt (we do not intercept it).
    """
    def __init__(self):
        # Prefer controlling TTY directly to work under sudo/SSH
        fd = None
        self._tty_path = None
        try:
            fd = os.open('/dev/tty', os.O_RDONLY)
            self._tty_path = '/dev/tty'
        except Exception:
            # Fallback to stdin
            fd = sys.stdin.fileno()
        self._fd = fd
        self._old = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        self._lock = threading.Lock()
        self._events = []
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        try:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
        except Exception:
            pass
        try:
            # Close if we explicitly opened /dev/tty
            if self._tty_path:
                os.close(self._fd)
        except Exception:
            pass

    def _push(self, ev):
        with self._lock:
            self._events.append(ev)

    def pop_event(self):
        with self._lock:
            return self._events.pop(0) if self._events else None

    def _run(self):
        while not self._stop:
            r, _, _ = select.select([self._fd], [], [], 0.05)
            if not r:
                continue
            try:
                data = os.read(self._fd, 32)
            except OSError:
                continue
            if not data:
                continue
            try:
                buf = data.decode('utf-8', errors='ignore')
            except Exception:
                continue

            for ch in buf:
                if ch in ('\r', '\n'):
                    self._push(('TOGGLE', None))
                    continue

            lower = buf.lower()
            if 'w' in lower:
                self._push(('CMD', 'forward'))
            if 's' in lower:
                self._push(('CMD', 'backward'))
            if 'a' in lower:
                self._push(('CMD', 'left'))
            if 'd' in lower:
                self._push(('CMD', 'right'))


