import unittest
from src.models import Position, Player, Resources, ResourceProduction, Buildings, BuildQueue, Fleet, Research, Planet
from datetime import datetime


class TestComponents(unittest.TestCase):
    def test_player_creation_defaults(self):
        p = Player(name="Alice", user_id=42)
        self.assertEqual(p.name, "Alice")
        self.assertEqual(p.user_id, 42)
        self.assertIsInstance(p.last_active, datetime)

    def test_resources_defaults(self):
        r = Resources()
        self.assertGreaterEqual(r.metal, 0)
        self.assertGreaterEqual(r.crystal, 0)
        self.assertGreaterEqual(r.deuterium, 0)

    def test_buildings_defaults(self):
        b = Buildings()
        self.assertGreaterEqual(b.metal_mine, 0)
        self.assertGreaterEqual(b.crystal_mine, 0)
        self.assertGreaterEqual(b.deuterium_synthesizer, 0)

    def test_build_queue_default_empty(self):
        q = BuildQueue()
        self.assertEqual(q.items, [])

    def test_other_components(self):
        pos = Position(1, 2, 3)
        self.assertEqual((pos.galaxy, pos.system, pos.planet), (1, 2, 3))
        _ = Fleet()
        _ = Research()
        planet = Planet(name="Home", owner_id=1)
        self.assertEqual(planet.name, "Home")
