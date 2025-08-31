from src.core.game import GameWorld


def test_calculate_building_cost_and_time_known_buildings():
    gw = GameWorld()
    cost0 = gw._calculate_building_cost('metal_mine', 0)
    time0 = gw._calculate_build_time('metal_mine', 0)

    assert isinstance(cost0, dict)
    assert all(k in cost0 for k in ('metal', 'crystal', 'deuterium'))
    assert isinstance(time0, int)

    # Ensure cost grows with level and time increases too
    cost2 = gw._calculate_building_cost('metal_mine', 2)
    time2 = gw._calculate_build_time('metal_mine', 2)

    assert cost2['metal'] >= cost0['metal']
    assert time2 >= time0


def test_calculate_building_cost_unknown_building_safe_defaults():
    gw = GameWorld()
    cost = gw._calculate_building_cost('unknown_building', 3)
    time_s = gw._calculate_build_time('unknown_building', 1)

    assert cost == {'metal': 0, 'crystal': 0, 'deuterium': 0}
    assert isinstance(time_s, int)
