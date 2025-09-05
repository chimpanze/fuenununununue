from __future__ import annotations

from datetime import datetime
from src.core.time_utils import utc_now, ensure_aware_utc, isoformat_utc
import esper

from src.models import Resources, ResourceProduction, Buildings, Research, Player, Planet
from src.core.config import (
    ENERGY_SOLAR_BASE,
    ENERGY_CONSUMPTION,
    PLASMA_PRODUCTION_BONUS,
    ENERGY_TECH_ENERGY_BONUS_PER_LEVEL,
    ENERGY_SOLAR_GROWTH,
    ENERGY_CONSUMPTION_GROWTH,
    FUSION_ENERGY_BASE,
    FUSION_ENERGY_GROWTH,
    FUSION_DEUTERIUM_CONSUMPTION_PER_LEVEL,
    BASE_PRODUCTION_RATES,
    USE_CONFIG_PRODUCTION_RATES,
    temperature_multiplier,
    size_multiplier,
    STORAGE_BASE_CAPACITY,
    STORAGE_CAPACITY_GROWTH,
)
from src.api.ws import send_to_user
from src.core.metrics import metrics


class ResourceProductionSystem(esper.Processor):
    """ECS processor that accrues resources based on production rates and building levels."""

    def process(self) -> None:
        """Run one tick of the resource production system."""
        current_time = utc_now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)
        for ent, (resources, production, buildings) in getter(
            Resources, ResourceProduction, Buildings
        ):
            # Calculate time difference in hours (normalize to aware UTC)
            last_update_utc = ensure_aware_utc(production.last_update)
            time_diff = (current_time - last_update_utc).total_seconds() / 3600.0

            if time_diff > 0:
                # Attempt to fetch research; optional for production effects
                plasma_lvl = 0
                energy_lvl = 0
                try:
                    research = self.world.component_for_entity(ent, Research)
                    plasma_lvl = int(getattr(research, 'plasma', 0))
                    energy_lvl = int(getattr(research, 'energy', 0))
                except Exception:
                    pass

                # Energy balance: production and consumption (+energy tech bonus)
                energy_bonus_factor = 1.0 + (ENERGY_TECH_ENERGY_BONUS_PER_LEVEL * energy_lvl)
                sp_lvl = max(0, int(getattr(buildings, 'solar_plant', 0)))
                solar_rate = ENERGY_SOLAR_BASE * sp_lvl * (ENERGY_SOLAR_GROWTH ** max(0, sp_lvl - 1))
                fr_lvl = max(0, int(getattr(buildings, 'fusion_reactor', 0)))
                fusion_rate = FUSION_ENERGY_BASE * fr_lvl * (FUSION_ENERGY_GROWTH ** max(0, fr_lvl - 1))
                energy_produced = (solar_rate + fusion_rate) * energy_bonus_factor
                # Consumption with optional non-linear growth per level
                def _consumption(base: float, lvl: int) -> float:
                    lvl = max(0, int(lvl))
                    return base * lvl * (ENERGY_CONSUMPTION_GROWTH ** max(0, lvl - 1))
                energy_required = 0.0
                energy_required += _consumption(ENERGY_CONSUMPTION.get('metal_mine', 0.0), getattr(buildings, 'metal_mine', 0))
                energy_required += _consumption(ENERGY_CONSUMPTION.get('crystal_mine', 0.0), getattr(buildings, 'crystal_mine', 0))
                energy_required += _consumption(ENERGY_CONSUMPTION.get('deuterium_synthesizer', 0.0), getattr(buildings, 'deuterium_synthesizer', 0))
                # Apply energy factor with soft floor when there is some production and some requirement
                if energy_required <= 0:
                    factor_raw = 1.0
                    factor = 1.0
                elif energy_produced <= 0:
                    factor_raw = 0.0
                    factor = 0.0
                else:
                    factor_raw = min(1.0, energy_produced / energy_required)
                    from src.core.config import ENERGY_DEFICIT_SOFT_FLOOR, ENERGY_DEFICIT_NOTIFY_THRESHOLD
                    factor = max(float(ENERGY_DEFICIT_SOFT_FLOOR), float(factor_raw))
                    # Emit a warning notification when severe deficit occurs (below or equal to threshold)
                    if float(factor_raw) < 1.0 and float(factor_raw) <= float(ENERGY_DEFICIT_NOTIFY_THRESHOLD):
                        # Record an energy deficit occurrence for telemetry
                        try:
                            metrics.increment_event("energy.deficit.count", 1)
                        except Exception:
                            pass
                        try:
                            from src.core.notifications import create_notification_with_cooldown as _notify_cd
                            # Attempt to fetch player and planet for context
                            user_id = 0
                            planet_name = None
                            try:
                                player = self.world.component_for_entity(ent, Player)
                                user_id = int(getattr(player, 'user_id', 0))
                            except Exception:
                                user_id = 0
                            try:
                                planet = self.world.component_for_entity(ent, Planet)
                                planet_name = getattr(planet, 'name', None)
                            except Exception:
                                planet_name = None
                            if user_id:
                                _notify_cd(
                                    user_id,
                                    "energy_deficit",
                                    {
                                        "planet": planet_name,
                                        "energy_produced": round(float(energy_produced), 3),
                                        "energy_required": round(float(energy_required), 3),
                                        "factor_raw": round(float(factor_raw), 4),
                                        "factor_applied": round(float(factor), 4),
                                    },
                                    priority="warning",
                                    key=f"energy_deficit:{planet_name or ent}",
                                )
                        except Exception:
                            pass

                # Determine base production rates (config-driven if enabled)
                if USE_CONFIG_PRODUCTION_RATES:
                    base_metal = BASE_PRODUCTION_RATES.get('metal_mine', production.metal_rate)
                    base_crystal = BASE_PRODUCTION_RATES.get('crystal_mine', production.crystal_rate)
                    base_deut = BASE_PRODUCTION_RATES.get('deuterium_synthesizer', production.deuterium_rate)
                else:
                    base_metal = production.metal_rate
                    base_crystal = production.crystal_rate
                    base_deut = production.deuterium_rate

                # Planet modifiers (neutral 1.0 by default)
                temp_mult = 1.0
                size_mult = 1.0
                try:
                    planet = self.world.component_for_entity(ent, Planet)
                    temp_mult = float(temperature_multiplier(int(getattr(planet, 'temperature', 25))))
                    size_mult = float(size_multiplier(int(getattr(planet, 'size', 163))))
                except Exception:
                    pass
                # Apply size multiplier to all resources; temperature only to deuterium (docs/tasks.md #71)
                planet_mult_size = size_mult

                # Calculate production based on building levels and energy factor (+plasma bonus)
                metal_production = base_metal * (1.1 ** max(0, int(getattr(buildings, 'metal_mine', 0)))) * time_diff * factor * planet_mult_size
                crystal_production = base_crystal * (1.1 ** max(0, int(getattr(buildings, 'crystal_mine', 0)))) * time_diff * factor * planet_mult_size
                deuterium_production = base_deut * (1.1 ** max(0, int(getattr(buildings, 'deuterium_synthesizer', 0)))) * time_diff * factor * planet_mult_size * temp_mult

                if plasma_lvl > 0:
                    metal_production *= (1.0 + PLASMA_PRODUCTION_BONUS.get('metal', 0.0) * plasma_lvl)
                    crystal_production *= (1.0 + PLASMA_PRODUCTION_BONUS.get('crystal', 0.0) * plasma_lvl)
                    deuterium_production *= (1.0 + PLASMA_PRODUCTION_BONUS.get('deuterium', 0.0) * plasma_lvl)

                # Update resources with capacity clamping
                raw_dm = int(round(metal_production))
                raw_dc = int(round(crystal_production))
                raw_dd = int(round(deuterium_production))

                before_m = resources.metal
                before_c = resources.crystal
                before_d = resources.deuterium

                # Compute capacities based on storage building levels (scaled by planet size)
                ms_lvl = max(0, int(getattr(buildings, 'metal_storage', 0)))
                cs_lvl = max(0, int(getattr(buildings, 'crystal_storage', 0)))
                dt_lvl = max(0, int(getattr(buildings, 'deuterium_tank', 0)))
                cap_m = int(STORAGE_BASE_CAPACITY.get('metal', 0) * (STORAGE_CAPACITY_GROWTH.get('metal', 1.0) ** ms_lvl) * planet_mult_size)
                cap_c = int(STORAGE_BASE_CAPACITY.get('crystal', 0) * (STORAGE_CAPACITY_GROWTH.get('crystal', 1.0) ** cs_lvl) * planet_mult_size)
                cap_d = int(STORAGE_BASE_CAPACITY.get('deuterium', 0) * (STORAGE_CAPACITY_GROWTH.get('deuterium', 1.0) ** dt_lvl) * planet_mult_size)

                add_m = max(0, min(raw_dm, max(0, cap_m - before_m)))
                add_c = max(0, min(raw_dc, max(0, cap_c - before_c)))
                add_d = max(0, min(raw_dd, max(0, cap_d - before_d)))

                # Optional storage-full notification (best-effort, rate-limited)
                try:
                    from src.core.notifications import create_notification_with_cooldown as _notify_cd
                    # Attempt to fetch player and planet for context
                    _uid = 0
                    _pname = None
                    try:
                        _player = self.world.component_for_entity(ent, Player)
                        _uid = int(getattr(_player, 'user_id', 0))
                    except Exception:
                        _uid = 0
                    try:
                        _planet = self.world.component_for_entity(ent, Planet)
                        _pname = getattr(_planet, 'name', None)
                    except Exception:
                        _pname = None
                    if _uid:
                        if before_m < cap_m and before_m + add_m >= cap_m:
                            _notify_cd(_uid, "storage_full", {"resource": "metal", "capacity": cap_m}, priority="info", key=f"storage_full:metal:{_pname or ent}")
                        if before_c < cap_c and before_c + add_c >= cap_c:
                            _notify_cd(_uid, "storage_full", {"resource": "crystal", "capacity": cap_c}, priority="info", key=f"storage_full:crystal:{_pname or ent}")
                        if before_d < cap_d and before_d + add_d >= cap_d:
                            _notify_cd(_uid, "storage_full", {"resource": "deuterium", "capacity": cap_d}, priority="info", key=f"storage_full:deuterium:{_pname or ent}")
                except Exception:
                    pass

                # Fusion reactor deuterium consumption over the elapsed time
                cons_d = 0
                try:
                    fr_lvl_local = max(0, int(getattr(buildings, 'fusion_reactor', 0)))
                    cons_d = int(round(FUSION_DEUTERIUM_CONSUMPTION_PER_LEVEL * fr_lvl_local * time_diff))
                except Exception:
                    cons_d = 0

                if add_m or add_c or add_d:
                    resources.metal = before_m + add_m
                    resources.crystal = before_c + add_c
                    resources.deuterium = before_d + add_d

                # Apply fusion reactor deuterium consumption after accrual
                if cons_d > 0:
                    try:
                        resources.deuterium = max(0, int(resources.deuterium) - int(cons_d))
                    except Exception:
                        pass

                # Record production and consumption metrics (best-effort)
                try:
                    if add_m:
                        metrics.increment_event("production.metal", int(add_m))
                    if add_c:
                        metrics.increment_event("production.crystal", int(add_c))
                    if add_d:
                        metrics.increment_event("production.deuterium", int(add_d))
                    if cons_d:
                        metrics.increment_event("consumption.deuterium.fusion", int(cons_d))
                except Exception:
                    pass

                # Emit real-time resource update to the owning user (best-effort)
                try:
                    player = self.world.component_for_entity(ent, Player)
                    user_id = int(getattr(player, 'user_id', 0))
                    if user_id:
                        send_to_user(user_id, {
                            "type": "resource_update",
                            "deltas": {"metal": add_m, "crystal": add_c, "deuterium": add_d - cons_d},
                            "totals": {"metal": resources.metal, "crystal": resources.crystal, "deuterium": resources.deuterium},
                            "ts": current_time.isoformat(),
                        })
                except Exception:
                    pass

                # Update last update time
                production.last_update = current_time

                # Persistence is centralized in GameWorld.save_player_data (periodic ~60s)
                # Throttling remains enforced in sync._should_persist as a safety net.
