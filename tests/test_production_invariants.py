import esper
from datetime import datetime, timedelta

from src.models import Resources, ResourceProduction, Buildings, Research
from src.systems import ResourceProductionSystem
from src.core.config import (
    ENERGY_SOLAR_BASE,
    ENERGY_CONSUMPTION,
    PLASMA_PRODUCTION_BONUS,
    ENERGY_TECH_ENERGY_BONUS_PER_LEVEL,
)


def _tick_once(world: esper.World):
    """Helper to process one tick."""
    world.process()


def test_zero_energy_stops_production():
    """When energy produced is zero and there is any energy required, factor -> 0 and no resources accrue."""
    world = esper.World()
    res = Resources(metal=0, crystal=0, deuterium=0)
    prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    # Require energy (mines on), but produce none (solar_plant=0)
    bld = Buildings(metal_mine=1, crystal_mine=1, deuterium_synthesizer=1, solar_plant=0)

    world.add_processor(ResourceProductionSystem())
    world.create_entity(res, prod, bld)

    _tick_once(world)

    assert res.metal == 0
    assert res.crystal == 0
    assert res.deuterium == 0


def test_partial_energy_scales_proportionally():
    """
    With insufficient energy, production should be scaled by factor = energy_produced / energy_required.
    Choose levels so factor is exactly 0.5.
    """
    world = esper.World()
    res = Resources(metal=0, crystal=0, deuterium=0)
    prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    # energy_required = 3*M + 2*C + 2*D = 3*8 + 2*4 + 2*4 = 40
    # energy_produced = 20 * solar_plant * (1 + 0.02*energy_lvl) = 20 * 1 * 1 = 20
    # factor = 20/40 = 0.5
    bld = Buildings(metal_mine=8, crystal_mine=4, deuterium_synthesizer=4, solar_plant=1)

    world.add_processor(ResourceProductionSystem())
    world.create_entity(res, prod, bld)

    _tick_once(world)

    hours = 1.0
    factor = 0.5
    expected_metal = int(round(60.0 * (1.1 ** 8) * hours * factor))
    expected_crystal = int(round(30.0 * (1.1 ** 4) * hours * factor))
    expected_deut = int(round(15.0 * (1.1 ** 4) * hours * factor))

    assert res.metal == expected_metal
    assert res.crystal == expected_crystal
    assert res.deuterium == expected_deut


def test_energy_and_plasma_apply_multiplicatively():
    """
    Energy tech increases produced energy multiplicatively, affecting the energy factor.
    Plasma tech then multiplies each resource production independently.
    The final production should be: base * (1.1 ** level) * hours * factor_with_energy * (1 + plasma_bonus*plasma_lvl)
    """
    # Baseline world (no research) for reference
    base_world = esper.World()
    base_res = Resources(metal=0, crystal=0, deuterium=0)
    base_prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    # Use the same building levels as proportional test (factor=0.5 at energy_lvl=0)
    bld = Buildings(metal_mine=8, crystal_mine=4, deuterium_synthesizer=4, solar_plant=1)
    base_world.add_processor(ResourceProductionSystem())
    base_world.create_entity(base_res, base_prod, bld)

    _tick_once(base_world)

    # Research-boosted world
    w = esper.World()
    res = Resources(metal=0, crystal=0, deuterium=0)
    prod = ResourceProduction(
        metal_rate=60.0, crystal_rate=30.0, deuterium_rate=15.0,
        last_update=datetime.now() - timedelta(hours=1)
    )
    research = Research(energy=10, plasma=5)
    w.add_processor(ResourceProductionSystem())
    ent = w.create_entity(res, prod, bld, research)

    _tick_once(w)

    # Compute expectations
    hours = 1.0
    # Energy side
    energy_lvl = 10
    plasma_lvl = 5
    energy_bonus_factor = 1.0 + (ENERGY_TECH_ENERGY_BONUS_PER_LEVEL * energy_lvl)
    energy_produced = ENERGY_SOLAR_BASE * max(0, bld.solar_plant) * energy_bonus_factor
    energy_required = (
        ENERGY_CONSUMPTION.get('metal_mine', 0.0) * max(0, bld.metal_mine)
        + ENERGY_CONSUMPTION.get('crystal_mine', 0.0) * max(0, bld.crystal_mine)
        + ENERGY_CONSUMPTION.get('deuterium_synthesizer', 0.0) * max(0, bld.deuterium_synthesizer)
    )
    factor = 1.0 if energy_required <= 0 else min(1.0, energy_produced / energy_required)

    plasma_mult_metal = 1.0 + PLASMA_PRODUCTION_BONUS.get('metal', 0.0) * plasma_lvl
    plasma_mult_crystal = 1.0 + PLASMA_PRODUCTION_BONUS.get('crystal', 0.0) * plasma_lvl
    plasma_mult_deut = 1.0 + PLASMA_PRODUCTION_BONUS.get('deuterium', 0.0) * plasma_lvl

    expected_metal = int(round(60.0 * (1.1 ** bld.metal_mine) * hours * factor * plasma_mult_metal))
    expected_crystal = int(round(30.0 * (1.1 ** bld.crystal_mine) * hours * factor * plasma_mult_crystal))
    expected_deut = int(round(15.0 * (1.1 ** bld.deuterium_synthesizer) * hours * factor * plasma_mult_deut))

    assert res.metal == expected_metal
    assert res.crystal == expected_crystal
    assert res.deuterium == expected_deut

    # And confirm strictly greater than baseline (since factor increased from 0.5 -> 0.6 and plasma > 1)
    assert res.metal > base_res.metal
    assert res.crystal > base_res.crystal
    assert res.deuterium > base_res.deuterium
