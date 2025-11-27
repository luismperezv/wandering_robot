from __future__ import annotations
import importlib.util
import os
import types
from typing import Callable, Tuple, Optional


class PolicyManager:
    """
    Manages policy instances and allows hot-reloading of custom policies.
    
    This wrapper allows the controller to use a policy that can be dynamically
    updated via the dashboard without restarting the robot.
    """
    
    def __init__(self, default_policy_class, storage_path: str, config_obj=None):
        """
        Initialize the policy manager.
        
        Args:
            default_policy_class: The default Policy class to use
            storage_path: Path to store custom policy code
            config_obj: Configuration object to pass to policies
        """
        self._default_policy_class = default_policy_class
        self._storage_path = storage_path
        self._config = config_obj
        self._active_policy = None
        self._active_name = "default"
        self._last_error: Optional[str] = None
        self.reload()

    def status(self) -> dict:
        """Get the current status of the policy manager."""
        return {
            "name": self._active_name,
            "has_custom": os.path.exists(self._storage_path),
            "error": self._last_error,
        }

    def update_distance(self, front_distance_cm: float):
        """
        Update the distance history in the active policy.
        
        Args:
            front_distance_cm: Latest front distance reading in cm
        """
        if self._active_policy and hasattr(self._active_policy, 'update_distance'):
            self._active_policy.update_distance(front_distance_cm)
    
    def get_next_action(self, prev_motion: str, front_distance_cm: float) -> Tuple[str, float, str, bool]:
        """
        Get the next action from the active policy.
        
        Args:
            prev_motion: Previous motion command
            front_distance_cm: Current front distance reading
            
        Returns:
            Tuple of (motion, speed, notes, is_recovery)
        """
        try:
            if self._active_policy and hasattr(self._active_policy, 'get_next_action'):
                return self._active_policy.get_next_action(prev_motion, front_distance_cm)
            else:
                # Fallback for policies without get_next_action
                motion, speed, notes = self._active_policy.decide_next_motion(front_distance_cm, prev_motion)
                return (motion, speed, notes, False)
        except Exception as e:
            self._last_error = f"active_policy_error: {e!r}"
            print(f"[ERROR] Policy error: {e}")
            # Reload default policy
            self._active_policy = self._default_policy_class(self._config)
            self._active_name = "default"
            return ("stop", 0.0, f"policy_error: {e}", False)
    
    def is_stuck_triggered(self) -> bool:
        """
        Check if the robot is currently executing recovery moves.
        
        Returns:
            True if recovery moves are queued, False otherwise
        """
        if self._active_policy and hasattr(self._active_policy, 'is_stuck_triggered'):
            return self._active_policy.is_stuck_triggered()
        return False
    
    def get_queue_length(self) -> int:
        """
        Get the number of queued recovery moves.
        
        Returns:
            Number of queued moves
        """
        if self._active_policy and hasattr(self._active_policy, 'get_queue_length'):
            return self._active_policy.get_queue_length()
        return 0

    def set_code(self, code_text: str) -> None:
        """Save custom policy code and reload."""
        os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
        with open(self._storage_path, "w", encoding="utf-8") as f:
            f.write(code_text)
        self.reload()

    def delete_custom(self) -> None:
        """Delete custom policy and reload default."""
        try:
            if os.path.exists(self._storage_path):
                os.remove(self._storage_path)
        except Exception:
            pass
        self.reload()

    def reload(self) -> None:
        """Reload the active policy from storage or use default."""
        self._last_error = None
        
        # Use default policy if no custom policy exists
        if not os.path.exists(self._storage_path):
            self._active_policy = self._default_policy_class(self._config)
            self._active_name = "default"
            return
            
        try:
            # Load custom policy module
            spec = importlib.util.spec_from_file_location("custom_policy_module", self._storage_path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore
            assert isinstance(mod, types.ModuleType)
            assert spec and spec.loader
            spec.loader.exec_module(mod)  # type: ignore
            
            # Try to get Policy class from custom module
            policy_class = getattr(mod, "Policy", None)
            if policy_class is not None:
                # Use the custom Policy class
                self._active_policy = policy_class(self._config)
                self._active_name = "custom"
            else:
                # Fallback: try to get decide_next_motion function for compatibility
                fn = getattr(mod, "decide_next_motion", None)
                if not callable(fn):
                    raise ValueError("Uploaded module lacks callable decide_next_motion or Policy class")
                
                # Create a simple wrapper policy
                class LegacyPolicyWrapper:
                    def __init__(self, config_obj):
                        self.config = config_obj
                        self._decide_func = fn
                    
                    def decide_next_motion(self, distance_cm: float, prev_motion: str):
                        return self._decide_func(distance_cm, prev_motion)
                    
                    def get_next_action(self, prev_motion: str, front_distance_cm: float):
                        motion, speed, notes = self._decide_func(front_distance_cm, prev_motion)
                        return (motion, speed, notes, False)
                    
                    def update_distance(self, front_distance_cm: float):
                        pass  # No-op for legacy policies
                    
                    def is_stuck_triggered(self) -> bool:
                        return False
                    
                    def get_queue_length(self) -> int:
                        return 0
                
                self._active_policy = LegacyPolicyWrapper(self._config)
                self._active_name = "custom_legacy"
            
        except Exception as e:
            self._last_error = f"load_error: {e!r}"
            print(f"[ERROR] Failed to load custom policy: {e}")
            self._active_policy = self._default_policy_class(self._config)
            self._active_name = "default"
