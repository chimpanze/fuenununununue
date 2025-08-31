from __future__ import annotations

from datetime import datetime
import logging
import esper

from src.models import Battle
from src.core.config import BASE_SHIP_STATS, BASE_SHIP_COSTS

logger = logging.getLogger(__name__)


class BattleSystem(esper.Processor):
    """Processor that resolves scheduled battles.

    Deterministic single-round implementation based on Ogame principles:
    - Compute total attack and shield using BASE_SHIP_STATS.
    - Compute structure (hull) points from BASE_SHIP_COSTS as (metal+crystal)/10 per ship.
    - Apply proportional losses: damage_to_defender = max(0, atk_attack - def_shield),
      fraction_destroyed_def = clamp(damage/total_def_structure, 0..1), destroyed_per_type = floor(count * fraction).
      Same for attacker using def_attack vs atk_shield.
    - Winner is the side with higher remaining power; ties are draws. If equal, fall back to initial power.
    - Outcome is stored on the Battle component; this system does not yet mutate actual fleets.
    """

    def process(self) -> None:
        now = datetime.now()

        world_obj = getattr(self, "world", None)
        getter = getattr(world_obj, "get_components", esper.get_components)

        for ent, (battle,) in getter(Battle):
            # Skip already resolved or not yet due battles
            if battle.resolved or now < battle.scheduled_time:
                continue

            # Initial powers using base attack for backward-compatibility with tests
            atk_power = self._compute_power(battle.attacker_ships)
            def_power = self._compute_power(battle.defender_ships)

            # Totals
            atk_attack = self._compute_total_attack(battle.attacker_ships)
            def_attack = self._compute_total_attack(battle.defender_ships)
            atk_shield = self._compute_total_shield(battle.attacker_ships)
            def_shield = self._compute_total_shield(battle.defender_ships)
            atk_struct = self._compute_total_structure(battle.attacker_ships)
            def_struct = self._compute_total_structure(battle.defender_ships)

            # Damage after shields
            damage_to_def = max(0, atk_attack - def_shield)
            damage_to_atk = max(0, def_attack - atk_shield)

            # Proportional losses
            def_loss_frac = min(1.0, (damage_to_def / def_struct)) if def_struct > 0 else 0.0
            atk_loss_frac = min(1.0, (damage_to_atk / atk_struct)) if atk_struct > 0 else 0.0

            attacker_losses, attacker_remaining = self._apply_losses(battle.attacker_ships, atk_loss_frac)
            defender_losses, defender_remaining = self._apply_losses(battle.defender_ships, def_loss_frac)

            # Remaining power for winner decision
            atk_remaining_power = self._compute_power(attacker_remaining)
            def_remaining_power = self._compute_power(defender_remaining)

            if atk_remaining_power > def_remaining_power:
                winner = "attacker"
            elif def_remaining_power > atk_remaining_power:
                winner = "defender"
            else:
                # Fall back to initial power comparison if exact tie remains
                if atk_power > def_power:
                    winner = "attacker"
                elif def_power > atk_power:
                    winner = "defender"
                else:
                    winner = "draw"

            battle.outcome = {
                "winner": winner,
                "attacker_power": atk_power,
                "defender_power": def_power,
                "attacker_remaining_power": atk_remaining_power,
                "defender_remaining_power": def_remaining_power,
                "attacker_losses": attacker_losses,
                "defender_losses": defender_losses,
                "attacker_remaining": attacker_remaining,
                "defender_remaining": defender_remaining,
                "resolved_at": now.isoformat(),
                "location": {
                    "galaxy": getattr(battle.location, "galaxy", None),
                    "system": getattr(battle.location, "system", None),
                    "planet": getattr(battle.location, "planet", None),
                },
            }
            battle.resolved = True

            # Emit battle report to world handler if available (no-op if not set)
            try:
                handler = getattr(self.world, "handle_battle_report", None)
                if callable(handler):
                    handler({
                        "attacker_user_id": getattr(battle, "attacker_id", None),
                        "defender_user_id": getattr(battle, "defender_id", None),
                        "location": {
                            "galaxy": getattr(battle.location, "galaxy", None),
                            "system": getattr(battle.location, "system", None),
                            "planet": getattr(battle.location, "planet", None),
                        },
                        "outcome": dict(battle.outcome or {}),
                        "entity_id": ent,
                    })
            except Exception:
                # Do not break processing if report emission fails
                pass

            # Structured log for audit/telemetry
            try:
                logger.info(
                    "battle_resolved",
                    extra={
                        "action_type": "battle_resolved",
                        "entity": ent,
                        "attacker_id": getattr(battle, "attacker_id", None),
                        "defender_id": getattr(battle, "defender_id", None),
                        "winner": winner,
                        "timestamp": now.isoformat(),
                    },
                )
            except Exception:
                pass

    @staticmethod
    def _compute_power(ships: dict[str, int] | None) -> int:
        if not ships:
            return 0
        total = 0
        for ship_type, count in ships.items():
            try:
                base_attack = int(BASE_SHIP_STATS.get(ship_type, {}).get("attack", 1))
                total += int(count) * base_attack
            except Exception:
                # If any malformed entry, ignore and continue
                continue
        return total

    @staticmethod
    def _compute_total_attack(ships: dict[str, int] | None) -> int:
        if not ships:
            return 0
        total = 0
        for ship_type, count in ships.items():
            try:
                base_attack = int(BASE_SHIP_STATS.get(ship_type, {}).get("attack", 0))
                total += int(count) * base_attack
            except Exception:
                continue
        return total

    @staticmethod
    def _compute_total_shield(ships: dict[str, int] | None) -> int:
        if not ships:
            return 0
        total = 0
        for ship_type, count in ships.items():
            try:
                base_shield = int(BASE_SHIP_STATS.get(ship_type, {}).get("shield", 0))
                total += int(count) * base_shield
            except Exception:
                continue
        return total

    @staticmethod
    def _structure_points(ship_type: str) -> float:
        costs = BASE_SHIP_COSTS.get(ship_type, {})
        metal = float(costs.get("metal", 0))
        crystal = float(costs.get("crystal", 0))
        return (metal + crystal) / 10.0

    def _compute_total_structure(self, ships: dict[str, int] | None) -> float:
        if not ships:
            return 0.0
        total = 0.0
        for ship_type, count in ships.items():
            try:
                sp = self._structure_points(ship_type)
                total += float(count) * sp
            except Exception:
                continue
        return total

    def _apply_losses(self, ships: dict[str, int] | None, fraction: float) -> tuple[dict[str, int], dict[str, int]]:
        """Return (losses_dict, remaining_dict) applying proportional losses with floor rounding."""
        if not ships or fraction <= 0:
            return {}, dict(ships or {})
        fraction = max(0.0, min(1.0, float(fraction)))
        losses: dict[str, int] = {}
        remaining: dict[str, int] = {}
        for ship_type, count in ships.items():
            try:
                c = int(count)
                destroyed = int(c * fraction)
                if destroyed > c:
                    destroyed = c
                losses[ship_type] = destroyed
                remaining_count = c - destroyed
                if remaining_count > 0:
                    remaining[ship_type] = remaining_count
            except Exception:
                continue
        return losses, remaining
