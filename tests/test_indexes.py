import unittest

from src.models.database import User, Planet


class TestDatabaseIndexes(unittest.TestCase):
    def test_users_timestamp_indexes_exist(self):
        users_table = User.__table__
        index_names = {ix.name for ix in users_table.indexes}
        self.assertIn("ix_users_created_at", index_names)
        self.assertIn("ix_users_last_login", index_names)

    def test_planets_last_update_index_exists(self):
        planets_table = Planet.__table__
        index_names = {ix.name for ix in planets_table.indexes}
        self.assertIn("ix_planets_last_update", index_names)


if __name__ == "__main__":
    unittest.main()