from __future__ import annotations
import json
import os
from typing import Any, Dict


class ConfigManager:
    def __init__(self, defaults_module, persist_path: str):
        self._defaults = defaults_module
        self._persist_path = persist_path
        self._overrides: Dict[str, Any] = {}
        self._writer = None  # Will be set by the controller
        self._load()
        
    def set_writer(self, writer):
        """Set the writer function for logging configuration changes"""
        self._writer = writer

    def _load(self):
        try:
            if os.path.exists(self._persist_path):
                with open(self._persist_path, "r") as f:
                    self._overrides = json.load(f)
        except Exception:
            self._overrides = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            with open(self._persist_path, "w") as f:
                json.dump(self._overrides, f, indent=2, sort_keys=True)
        except Exception:
            pass

    def get_effective(self) -> Dict[str, Any]:
        eff: Dict[str, Any] = {}
        for k in dir(self._defaults):
            if k.isupper():
                eff[k] = getattr(self._defaults, k)
        eff.update(self._overrides)
        return eff

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return getattr(self._defaults, key, default)

    def set_overrides(self, overrides: Dict[str, Any]):
        changes = []
        
        # Process each override
        for k, v in overrides.items():
            if k.isupper():
                old_value = self._overrides.get(k, 'default')
                if k not in self._overrides or self._overrides[k] != v:
                    changes.append(f"{k}={v}(was:{old_value})")
                    self._overrides[k] = v
        
        if changes:  # Only save if there were actual changes
            self._save()
            
            # Log the changes in a format that fits the CSV notes column
            notes = "CONFIG: " + ", ".join(changes)
            if hasattr(self, '_writer') and callable(self._writer):
                # Log the config change in the CSV
                self._writer([
                    "CONFIG",  # mode
                    "",        # distance_cm
                    "",        # executed_motion
                    "",        # executed_speed
                    "",        # next_motion
                    "",        # next_speed
                    notes,     # notes
                    0,         # stuck_triggered
                    0          # queue_len
                ])
                
        return changes

    def clear_overrides(self):
        was_cleared = False
        
        if self._overrides:  # Only log if there were overrides to clear
            was_cleared = True
            notes = f"CONFIG: Cleared overrides: {', '.join(self._overrides.keys())}"
            self._overrides = {}
            self._save()
            
            if hasattr(self, '_writer') and callable(self._writer):
                self._writer([
                    "CONFIG",  # mode
                    "",        # distance_cm
                    "",        # executed_motion
                    "",        # executed_speed
                    "",        # next_motion
                    "",        # next_speed
                    notes,     # notes
                    0,         # stuck_triggered
                    0          # queue_len
                ])
                
        return was_cleared

    def snapshot(self) -> Dict[str, Any]:
        return {
            "defaults": {k: getattr(self._defaults, k) for k in dir(self._defaults) if k.isupper()},
            "overrides": dict(self._overrides),
            "effective": self.get_effective(),
        }


