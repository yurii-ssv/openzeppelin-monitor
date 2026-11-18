"""Check registration and per-check configuration.

A check module registers its class with `@register`; `checks/__init__.py` imports each module
(one line per check, in lane blocks). `build(settings)` instantiates the enabled checks with their
resolved config: the class `defaults`, then legacy top-level keys the class names in `legacy_keys`,
then `settings["checks"][id]`. Unknown check ids and unknown keys are configuration errors. `disabled(settings)`
names the registered checks the configuration switches off (`checks.<id>.enabled: false`); `checks.<id>.enabled`,
when present, must be a JSON boolean (`validate_switches`, D14).
"""

from __future__ import annotations

import copy
import json

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


def build(settings: dict, only=None) -> list:
    """The enabled checks, in registration order. `only`: build just these ids (tests build the checks they name
    instead of disabling every other registered one)."""
    configured = settings.get("checks", {})
    unknown = (set(configured) | set(only or ())) - set(REGISTRY)
    if unknown:
        raise SystemExit(f"checks: unknown check id(s) {sorted(unknown)}; registered: {sorted(REGISTRY)}")
    out = []
    for cid, cls in REGISTRY.items():
        if only is not None and cid not in only:
            continue
        if not configured.get(cid, {}).get("enabled", True):
            continue
        for name, sig in cls.events.items():
            register_event(name, sig)
        out.append(cls(resolve_config(cls, settings), settings))
    return out


def validate_switches(settings: dict) -> None:
    """`checks.<id>.enabled`, when present, must be a JSON boolean (D14): any other value (`0`, `null`, `""`, a
    string, ...) is a configuration error, like an unknown check id, so a malformed value never closes incidents and
    never silently skips a check. Every configured id is checked (registered or not); `Worker()` calls this first."""
    for cid, own in settings.get("checks", {}).items():
        if not isinstance(own, dict):
            raise SystemExit(f"checks.{cid}: must be an object, got {json.dumps(own)}")
        if "enabled" in own and not isinstance(own["enabled"], bool):
            raise SystemExit(f"checks.{cid}.enabled: must be true or false, got {json.dumps(own['enabled'])}")


def disabled(settings: dict) -> list[str]:
    """The registered ids switched off by `checks.<id>.enabled: false` (sorted), whatever `only` says; validates the
    switches first (`validate_switches`), so `false` is the only value that disables. A registered id absent from
    `checks` is enabled; a check that is merely not built (tests' `only=`, stub instances) is not disabled. Unknown
    ids are `build`'s error."""
    validate_switches(settings)
    configured = settings.get("checks", {})
    return sorted(cid for cid in REGISTRY if configured.get(cid, {}).get("enabled", True) is False)
