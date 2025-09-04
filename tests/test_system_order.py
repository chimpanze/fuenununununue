from datetime import datetime, timedelta

import esper

from src.core.game import GameWorld
from src.models import Resources, ResourceProduction, Buildings, BuildQueue


def test_processor_order_and_tick_determinism():
    # Instantiate full GameWorld to use its configured processor order
    gw = GameWorld()

    # Verify processor registration order via class names
    processors = getattr(gw.world, "_processors", [])
    order = [type(p).__name__ for p in processors]
    assert order == [
        "ResourceProductionSystem",
        "BuildingConstructionSystem",
        "PlayerActivitySystem",
        "ResearchSystem",
        "ShipyardSystem",
        "FleetMovementSystem",
        "BattleSystem",
    ], f"Unexpected processor order: {order}"

    # Seed deterministic components on a fresh World to validate per-tick side effects
    # (use a standalone esper.World, but the order contract is already checked above)
    world = esper.World()
    world.add_processor(gw.world._processors[0].__class__())  # ResourceProductionSystem
    world.add_processor(gw.world._processors[1].__class__())  # BuildingConstructionSystem

    # Setup: level 1 metal mine, large solar plant to ensure full energy factor = 1.0
    resources = Resources(metal=0, crystal=0, deuterium=0)
    production = ResourceProduction(
        metal_rate=10.0,  # simple rate for exact expectation
        crystal_rate=0.0,
        deuterium_rate=0.0,
        last_update=datetime.now() - timedelta(hours=1),  # exactly 1h elapsed
    )
    buildings = Buildings(
        metal_mine=1,
        crystal_mine=0,
        deuterium_synthesizer=0,
        solar_plant=100,  # plenty of energy so factor == 1
    )
    # Queue: upgrade metal_mine -> completes in the past (so it will complete this tick)
    queue = BuildQueue(items=[{
        "type": "metal_mine",
        "completion_time": datetime.now() - timedelta(seconds=1),
        "cost": {"metal": 0, "crystal": 0, "deuterium": 0},
    }])

    e = world.create_entity(resources, production, buildings, queue)

    # Record execution order by wrapping processor.process
    executed = []
    for p in world._processors:  # type: ignore[attr-defined]
        original = p.process

        def make_wrapper(name, fn):
            def _wrapped():
                executed.append(name)
                fn()
            return _wrapped

        p.process = make_wrapper(type(p).__name__, original)  # type: ignore[assignment]

    # Run one tick
    world.process()

    # Assert processor execution order matches the contract subset we added (first two)
    assert executed[:2] == [
        "ResourceProductionSystem",
        "BuildingConstructionSystem",
    ]

    # Compute expected production for level 1 during exactly 1 hour
    # formula: rate * (1.1 ** level) * hours, rounded to int
    expected_lvl1_metal = int(round(10.0 * (1.1 ** 1) * 1.0))  # = round(11.0) = 11
    expected_lvl2_metal = int(round(10.0 * (1.1 ** 2) * 1.0))  # = round(12.1) = 12

    # Determinism per tick: production must have used the pre-upgrade level (1)
    assert resources.metal == expected_lvl1_metal, (
        f"Production should use pre-construction building level during the tick. "
        f"got={resources.metal} expected={expected_lvl1_metal}"
    )
    assert resources.metal != expected_lvl2_metal, (
        "Production must not depend on a level increased later in the same tick"
    )

    # And the construction should have completed after production
    assert buildings.metal_mine == 2, "Building level should increment after construction completes"
    assert len(queue.items) == 0, "Completed build should be removed from the queue"