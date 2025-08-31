from __future__ import annotations

from datetime import datetime
import esper

from src.models import Resources, ResourceProduction, Buildings, Research, Player
from src.core.sync import sync_planet_resources
from src.core.config import ENERGY_SOLAR_BASE, ENERGY_CONSUMPTION, PLASMA_PRODUCTION_BONUS, ENERGY_TECH_ENERGY_BONUS_PER_LEVEL
from src.api.ws import send_to_user


class ResourceProductionSystem(esper.Processor):
    """ECS processor that accrues resources based on production rates and building levels."""

    def process(self) -> None:
        """Run one tick of the resource production system."""
        current_time = datetime.now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)
        for ent, (resources, production, buildings) in getter(
            Resources, ResourceProduction, Buildings
        ):
            # Calculate time difference in hours
            time_diff = (current_time - production.last_update).total_seconds() / 3600.0

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
                energy_produced = ENERGY_SOLAR_BASE * max(0, buildings.solar_plant) * energy_bonus_factor
                energy_required = 0.0
                energy_required += ENERGY_CONSUMPTION.get('metal_mine', 0.0) * max(0, buildings.metal_mine)
                energy_required += ENERGY_CONSUMPTION.get('crystal_mine', 0.0) * max(0, buildings.crystal_mine)
                energy_required += ENERGY_CONSUMPTION.get('deuterium_synthesizer', 0.0) * max(0, buildings.deuterium_synthesizer)
                factor = 1.0 if energy_required <= 0 else min(1.0, energy_produced / energy_required)

                # Calculate production based on building levels and energy factor (+plasma bonus)
                metal_production = production.metal_rate * (1.1 ** buildings.metal_mine) * time_diff * factor
                crystal_production = production.crystal_rate * (1.1 ** buildings.crystal_mine) * time_diff * factor
                deuterium_production = production.deuterium_rate * (1.1 ** buildings.deuterium_synthesizer) * time_diff * factor

                if plasma_lvl > 0:
                    metal_production *= (1.0 + PLASMA_PRODUCTION_BONUS.get('metal', 0.0) * plasma_lvl)
                    crystal_production *= (1.0 + PLASMA_PRODUCTION_BONUS.get('crystal', 0.0) * plasma_lvl)
                    deuterium_production *= (1.0 + PLASMA_PRODUCTION_BONUS.get('deuterium', 0.0) * plasma_lvl)

                # Update resources (round to nearest to reflect fractional accrual)
                d_metal = int(round(metal_production))
                d_crystal = int(round(crystal_production))
                d_deut = int(round(deuterium_production))
                if d_metal or d_crystal or d_deut:
                    resources.metal += d_metal
                    resources.crystal += d_crystal
                    resources.deuterium += d_deut

                    # Emit real-time resource update to the owning user (best-effort)
                    try:
                        player = self.world.component_for_entity(ent, Player)
                        user_id = int(getattr(player, 'user_id', 0))
                        if user_id:
                            send_to_user(user_id, {
                                "type": "resource_update",
                                "deltas": {"metal": d_metal, "crystal": d_crystal, "deuterium": d_deut},
                                "totals": {"metal": resources.metal, "crystal": resources.crystal, "deuterium": resources.deuterium},
                                "ts": current_time.isoformat(),
                            })
                    except Exception:
                        pass

                # Update last update time
                production.last_update = current_time

                # Persist to database (best-effort, non-blocking)
                try:
                    sync_planet_resources(self.world, ent)
                except Exception:
                    # Swallow any sync errors to keep the loop stable
                    pass
