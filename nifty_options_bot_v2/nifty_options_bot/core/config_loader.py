"""
config_loader.py
================
Loads and validates the YAML configuration file.
Provides a singleton Config object used across all modules.
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yml"


class Config:
    """
    Singleton configuration container.
    Access any config value via dot-notation helpers or raw dict access.
    """

    _instance = None

    def __new__(cls, config_path: str = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def load(self, config_path: str = None) -> None:
        """Load configuration from YAML file."""
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r") as f:
            self._data: Dict[str, Any] = yaml.safe_load(f)

        self._loaded = True
        logger.info(f"Configuration loaded from {path}")

    def get(self, *keys, default=None):
        """
        Retrieve a nested config value using a chain of keys.
        Example: config.get('risk', 'max_loss_per_day')
        """
        node = self._data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def as_dict(self) -> Dict[str, Any]:
        """Return the full config as a plain dictionary."""
        return self._data

    # ── Convenience accessors ────────────────────────────────

    @property
    def broker(self) -> Dict:
        return self._data.get("broker", {})

    @property
    def instrument(self) -> Dict:
        return self._data.get("instrument", {})

    @property
    def strategy(self) -> Dict:
        return self._data.get("strategy", {})

    @property
    def risk(self) -> Dict:
        return self._data.get("risk", {})

    @property
    def orders(self) -> Dict:
        return self._data.get("orders", {})

    @property
    def dashboard(self) -> Dict:
        return self._data.get("dashboard", {})


def get_config(config_path: str = None) -> Config:
    """
    Module-level factory that returns the global Config singleton.
    Call with a path on first use; subsequent calls ignore the path.
    """
    cfg = Config()
    if not cfg._loaded:
        cfg.load(config_path)
    return cfg
