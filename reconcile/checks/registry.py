"""Check registration and per-check configuration.

A check module registers its class with `@register`; `checks/__init__.py` imports each module
(one line per check, in lane blocks). `build(settings)` instantiates the enabled checks with their
resolved config: the class `defaults`, then legacy top-level keys the class names in `legacy_keys`,
then `settings["checks"][id]`. Unknown check ids and unknown keys are configuration errors.
"""

from __future__ import annotations

import copy

from eb_events import register_event

REGISTRY: dict[str, type] = {}


def register(cls):
    if not cls.id or cls.id in REGISTRY:
        raise ValueError(f"check id {cls.id!r} is empty or already registered")
    REGISTRY[cls.id] = cls
    return cls


def resolve_config(cls, settings: dict) -> dict:
    own = dict(settings.get("checks", {}).get(cls.id, {}))
    own.pop("enabled", None)
    unknown = set(own) - set(cls.defaults)
    if unknown:
        raise SystemExit(f"checks.{cls.id}: unknown key(s) {sorted(unknown)}; accepted: {sorted(cls.defaults)}")
    config = copy.deepcopy(cls.defaults)
    for k in getattr(cls, "legacy_keys", ()):
        if k in settings:
            config[k] = settings[k]
    config.update(own)
    return config


def build(settings: dict) -> list:
    configured = settings.get("checks", {})
    unknown = set(configured) - set(REGISTRY)
    if unknown:
        raise SystemExit(f"checks: unknown check id(s) {sorted(unknown)}; registered: {sorted(REGISTRY)}")
    out = []
    for cid, cls in REGISTRY.items():
        if not configured.get(cid, {}).get("enabled", True):
            continue
        for name, sig in cls.events.items():
            register_event(name, sig)
        out.append(cls(resolve_config(cls, settings), settings))
    return out
