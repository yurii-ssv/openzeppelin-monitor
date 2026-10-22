"""Registered checks. Each lane appends its import lines in its own block (merges stay textual)."""

from checks.registry import REGISTRY, build, register  # noqa: F401

# --- CP1B: framework port of the #94775 worker
from checks import m5_eb_desync  # noqa: F401, E402

# --- CP2a: liquidation family (M3, M4, M14)
from checks import m3_liquidation_zone, m4_conservation_deficit, m14_deposit_frontrun  # noqa: F401, E402

# --- CP2b: oracle family (M9, M10, M11)

# --- CP2c: wallets and supply (M12, M8 floor)
from checks import m8_supply_floor, m12_wallet_burn, m13_retired_keys  # noqa: F401, E402

# --- CP2d: cluster integrity and capacity (M1, M2)
from checks import m1_stale_tuple, m2_operator_capacity  # noqa: F401, E402

# --- CP2e: legacy SSV (M6, M7)
from checks import m6_legacy_coverage, m7_ssv_solvency  # noqa: F401, E402
