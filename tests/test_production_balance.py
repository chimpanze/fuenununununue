import esper
from datetime import datetime, timedelta

from src.models import Resources, ResourceProduction, Buildings
from src.systems import ResourceProductionSystem


def _tick_once(world: esper.World):
    world.process()


def test_early_vs_mid_game_outputs_increase():
    """Mid-game building levels should produce more than early-game under sufficient energy."""
    hours = 1.0

    # Early game: level 2 mines, solar 5 (ample energy)
    w_early = esper.World()
    res_e = Resources(metal=0, crystal=0, deuterium=0)
    prod_e = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=hours)
    )
    bld_e = Buildings(metal_mine=2, crystal_mine=2, deuterium_synthesizer=2, solar_plant=5)
    w_early.add_processor(ResourceProductionSystem())
    w_early.create_entity(res_e, prod_e, bld_e)

    _tick_once(w_early)

    # Mid game: level 6 mines, solar 8 (ample energy)
    w_mid = esper.World()
    res_m = Resources(metal=0, crystal=0, deuterium=0)
    prod_m = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=hours)
    )
    bld_m = Buildings(metal_mine=6, crystal_mine=6, deuterium_synthesizer=6, solar_plant=8)
    w_mid.add_processor(ResourceProductionSystem())
    w_mid.create_entity(res_m, prod_m, bld_m)

    _tick_once(w_mid)

    # Total outputs
    early_total = res_e.metal + res_e.crystal + res_e.deuterium
    mid_total = res_m.metal + res_m.crystal + res_m.deuterium

    assert early_total > 0
    assert mid_total > 0
    assert mid_total > early_total


def test_energy_deficit_reduces_outputs():
    """With insufficient energy, production should be lower than with sufficient energy."""
    hours = 1.0

    # Sufficient energy
    w_ok = esper.World()
    res_ok = Resources(metal=0, crystal=0, deuterium=0)
    prod_ok = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=hours)
    )
    bld_ok = Buildings(metal_mine=5, crystal_mine=5, deuterium_synthesizer=5, solar_plant=8)
    w_ok.add_processor(ResourceProductionSystem())
    w_ok.create_entity(res_ok, prod_ok, bld_ok)
    _tick_once(w_ok)

    # Deficit: same mines, much less solar
    w_def = esper.World()
    res_def = Resources(metal=0, crystal=0, deuterium=0)
    prod_def = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=hours)
    )
    bld_def = Buildings(metal_mine=5, crystal_mine=5, deuterium_synthesizer=5, solar_plant=1)
    w_def.add_processor(ResourceProductionSystem())
    w_def.create_entity(res_def, prod_def, bld_def)
    _tick_once(w_def)

    total_ok = res_ok.metal + res_ok.crystal + res_ok.deuterium
    total_def = res_def.metal + res_def.crystal + res_def.deuterium

    assert total_ok > total_def