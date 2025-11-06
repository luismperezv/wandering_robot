from __future__ import annotations
import json
import os
from typing import Any, Dict


class ConfigManager:
    def __init__(self, defaults_module, persist_path: str):
        self._defaults = defaults_module
        self._persist_path = persist_path
        self._overrides: Dict[str, Any] = {}
        self._load()

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
        for k, v in overrides.items():
            if k.isupper():
                self._overrides[k] = v
        self._save()

    def clear_overrides(self):
        self._overrides = {}
        self._save()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "defaults": {k: getattr(self._defaults, k) for k in dir(self._defaults) if k.isupper()},
            "overrides": dict(self._overrides),
            "effective": self.get_effective(),
        }


