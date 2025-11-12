from __future__ import annotations
import importlib.util
import os
import types
from typing import Callable, Tuple, Optional


class PolicyManager:
    def __init__(self, default_func: Callable[[float, str], Tuple[str, float, str]], storage_path: str):
        self._default_func = default_func
        self._storage_path = storage_path
        self._active_func: Callable[[float, str], Tuple[str, float, str]] = default_func
        self._active_name = "default"
        self._last_error: Optional[str] = None
        self.reload()

    def status(self) -> dict:
        return {
            "name": self._active_name,
            "has_custom": os.path.exists(self._storage_path),
            "error": self._last_error,
        }

    def decide_next_motion(self, distance_cm: float, current_motion: str):
        try:
            return self._active_func(distance_cm, current_motion)
        except Exception as e:
            self._last_error = f"active_policy_error: {e!r}"
            self._active_func = self._default_func
            self._active_name = "default"
            return self._default_func(distance_cm, current_motion)

    def set_code(self, code_text: str) -> None:
        os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
        with open(self._storage_path, "w", encoding="utf-8") as f:
            f.write(code_text)
        self.reload()

    def delete_custom(self) -> None:
        try:
            if os.path.exists(self._storage_path):
                os.remove(self._storage_path)
        except Exception:
            pass
        self.reload()

    def reload(self) -> None:
        self._last_error = None
        if not os.path.exists(self._storage_path):
            self._active_func = self._default_func
            self._active_name = "default"
            return
        try:
            spec = importlib.util.spec_from_file_location("custom_policy_module", self._storage_path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore
            assert isinstance(mod, types.ModuleType)
            assert spec and spec.loader
            spec.loader.exec_module(mod)  # type: ignore
            fn = getattr(mod, "decide_next_motion", None)
            if not callable(fn):
                raise ValueError("Uploaded module lacks callable decide_next_motion")
            test = fn(100.0, "forward")
            if not (isinstance(test, tuple) and len(test) == 3 and isinstance(test[0], str)):
                raise ValueError("decide_next_motion must return (next_motion:str, next_speed:float, notes:str)")
            self._active_func = fn
            self._active_name = "custom"
        except Exception as e:
            self._last_error = f"load_error: {e!r}"
            self._active_func = self._default_func
            self._active_name = "default"


