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
        self._is_robot_stuck_func = None  # Will be set in reload()
        self.reload()

    def status(self) -> dict:
        return {
            "name": self._active_name,
            "has_custom": os.path.exists(self._storage_path),
            "has_stuck_detection": self._is_robot_stuck_func is not None,
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
            
    def is_robot_stuck(self, distance_history, next_motion, config):
        """
        Check if the robot is stuck based on distance history and next motion.
        
        Args:
            distance_history: Collection of recent distance measurements
            next_motion: Next planned motion command
            config: Configuration object with STUCK_* constants
            
        Returns:
            Tuple of (is_stuck: bool, notes: str, cooldown_steps: int)
        """
        if self._is_robot_stuck_func is not None:
            try:
                return self._is_robot_stuck_func(distance_history, next_motion, config)
            except Exception as e:
                print(f"[ERROR] Error in is_robot_stuck: {e}")
                return False, "", 0
        return False, "", 0

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
            # Try to import is_robot_stuck from the default policy module
            try:
                from firmware.control.policy import is_robot_stuck
                self._is_robot_stuck_func = is_robot_stuck
            except ImportError:
                self._is_robot_stuck_func = None
            return
            
        try:
            spec = importlib.util.spec_from_file_location("custom_policy_module", self._storage_path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore
            assert isinstance(mod, types.ModuleType)
            assert spec and spec.loader
            spec.loader.exec_module(mod)  # type: ignore
            
            # Load decide_next_motion
            fn = getattr(mod, "decide_next_motion", None)
            if not callable(fn):
                raise ValueError("Uploaded module lacks callable decide_next_motion")
            test = fn(100.0, "forward")
            if not (isinstance(test, tuple) and len(test) == 3 and isinstance(test[0], str)):
                raise ValueError("decide_next_motion must return (next_motion:str, next_speed:float, notes:str)")
            
            # Load is_robot_stuck if it exists, otherwise use default
            is_stuck_fn = getattr(mod, "is_robot_stuck", None)
            if not callable(is_stuck_fn):
                try:
                    from firmware.control.policy import is_robot_stuck
                    is_stuck_fn = is_robot_stuck
                except ImportError:
                    is_stuck_fn = None
            
            self._active_func = fn
            self._is_robot_stuck_func = is_stuck_fn
            self._active_name = "custom"
            
        except Exception as e:
            self._last_error = f"load_error: {e!r}"
            self._active_func = self._default_func
            self._is_robot_stuck_func = None
            self._active_name = "default"


