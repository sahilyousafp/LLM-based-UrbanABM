"""
PluginRegistry — auto-discovers all ExternalDataPlugin subclasses in this package.

No manual registration needed: just drop a new *_plugin.py file here and it
will appear in GET /api/external/sources automatically.
"""
from __future__ import annotations
import importlib
import logging
import os
from pathlib import Path
from typing import Any

from .base_plugin import ExternalDataPlugin

logger = logging.getLogger(__name__)

_registry: dict[str, ExternalDataPlugin] = {}
_discovered = False


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    plugins_dir = Path(__file__).parent
    for path in sorted(plugins_dir.glob("*_plugin.py")):
        module_name = path.stem
        try:
            mod = importlib.import_module(f".{module_name}", package=__package__)
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, ExternalDataPlugin)
                    and attr is not ExternalDataPlugin
                    and attr.name  # skip classes without a name
                ):
                    instance = attr()
                    _registry[instance.name] = instance
                    logger.debug(f"Registered external plugin: {instance.name}")
        except Exception as e:
            logger.warning(f"Failed to load plugin {module_name}: {e}")
    _discovered = True


def all_plugins() -> list[ExternalDataPlugin]:
    _discover()
    return list(_registry.values())


def get_plugin(name: str) -> ExternalDataPlugin | None:
    _discover()
    return _registry.get(name)


def list_with_status(con: Any) -> list[dict]:
    """Return all plugins as dicts, each enriched with loaded status from DuckDB."""
    result = []
    for plugin in all_plugins():
        info = plugin.to_dict()
        try:
            info["status"] = plugin.get_status(con)
        except Exception:
            info["status"] = {"loaded": False, "row_count": 0, "last_updated": None}
        result.append(info)
    return result
