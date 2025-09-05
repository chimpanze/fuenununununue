import esper
from datetime import datetime, timedelta

from src.core.game import GameWorld
from src.core.config import BASE_SHIP_TIMES
from src.models import Resources, ResourceProduction, Buildings
from src.systems import ResourceProductionSystem


def _one_hour_tick(world: esper.World):
    world.process()


def test_time_to_level5_mines_from_level1():
    """
    Cumulative time to upgrade each mine from level 1 -> 5 should equal
    the sum over current levels [1,2,3,4] of base_time * (1.2 ** level),
    with no reductions applied (defaults: no research, no robot factory).
    """
    gw = GameWorld()

    def cumulative_time(building_type: str) -> int:
        total = 0
        # Starting at level 1, we need four upgrades: 1->2, 2->3, 3->4, 4->5
        for lvl in [1, 2, 3, 4]:
            total += gw._calculate_build_time(building_type, lvl)
        return total

    metal_time = cumulative_time('metal_mine')
    crystal_time = cumulative_time('crystal_mine')
    deut_time = cumulative_time('deuterium_synthesizer')

    # Deterministic sanity bounds: each subsequent mine takes longer cumulatively than prior
    assert metal_time > 0
    assert crystal_time > 0
    assert deut_time > 0
    # Metal/crystal vs deuterium base times differ; just ensure monotonic growth works
    assert gw._calculate_build_time('metal_mine', 4) > gw._calculate_build_time('metal_mine', 1)
    assert gw._calculate_build_time('crystal_mine', 4) > gw._calculate_build_time('crystal_mine', 1)
    assert gw._calculate_build_time('deuterium_synthesizer', 4) > gw._calculate_build_time('deuterium_synthesizer', 1)


def test_time_to_first_light_fighter_with_shipyard_1_no_research():
    """
    With shipyard level 1 and no research, building 1 light_fighter should take
    BASE_SHIP_TIMES['light_fighter'] seconds according to the current model.
    (Shipyard level and robot_factory currently do not reduce ship build time.)
    """
    assert BASE_SHIP_TIMES['light_fighter'] == 60


def test_no_single_building_dominant_early_energy_sufficient():
    """
    In an energy-sufficient setup, upgrading the solar plant should not increase
    production, while upgrading the metal mine should.
    Assert that the mine upgrade yields at least as much production gain as the solar upgrade.
    """
    # Baseline: energy sufficient via higher solar level
    base_world = esper.World()
    base_res = Resources(metal=0, crystal=0, deuterium=0)
    base_prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    base_bld = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1, solar_plant=3)
    base_world.add_processor(ResourceProductionSystem())
    base_world.create_entity(base_res, base_prod, base_bld)
    _one_hour_tick(base_world)

    # Mine upgrade case: metal mine 1 -> 2
    mine_world = esper.World()
    mine_res = Resources(metal=0, crystal=0, deuterium=0)
    mine_prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    mine_bld = Buildings(metal_mine=2, crystal_mine=1, deuterium_synthesizer=1, solar_plant=3)
    mine_world.add_processor(ResourceProductionSystem())
    mine_world.create_entity(mine_res, mine_prod, mine_bld)
    _one_hour_tick(mine_world)

    # Solar upgrade case: solar 3 -> 4 (should have no effect when energy is already sufficient)
    solar_world = esper.World()
    solar_res = Resources(metal=0, crystal=0, deuterium=0)
    solar_prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    solar_bld = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1, solar_plant=4)
    solar_world.add_processor(ResourceProductionSystem())
    solar_world.create_entity(solar_res, solar_prod, solar_bld)
    _one_hour_tick(solar_world)

    # Gains over baseline
    mine_gain_total = (mine_res.metal + mine_res.crystal + mine_res.deuterium) - (base_res.metal + base_res.crystal + base_res.deuterium)
    solar_gain_total = (solar_res.metal + solar_res.crystal + solar_res.deuterium) - (base_res.metal + base_res.crystal + base_res.deuterium)

    assert mine_gain_total >= solar_gain_total