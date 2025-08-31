import unittest

from src.core.notifications import (
    create_notification,
    get_in_memory_notifications,
    clear_in_memory_notifications,
)


class TestNotificationsInMemory(unittest.TestCase):
    def setUp(self):
        clear_in_memory_notifications()

    def tearDown(self):
        clear_in_memory_notifications()

    def test_create_and_list_notifications(self):
        user_id = 42
        rec = create_notification(user_id, "building_complete", {"building_type": "metal_mine", "new_level": 2})
        self.assertIsInstance(rec, dict)
        self.assertEqual(rec["user_id"], user_id)
        self.assertEqual(rec["type"], "building_complete")
        self.assertEqual(rec["payload"]["building_type"], "metal_mine")
        self.assertEqual(rec["payload"]["new_level"], 2)
        self.assertEqual(rec["priority"], "normal")
        self.assertIsNone(rec["read_at"])  # unread by default
        self.assertIn("created_at", rec)

        items = get_in_memory_notifications(user_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "building_complete")

    def test_ring_buffer_trim(self):
        user_id = 7
        # Create more than the in-memory limit (100); only last 100 should remain
        for i in range(120):
            create_notification(user_id, "info", {"i": i})
        items = get_in_memory_notifications(user_id, limit=200)
        self.assertEqual(len(items), 100)
        # Oldest 20 should have been dropped; first remaining should have i=20
        self.assertEqual(items[0]["payload"]["i"], 20)


if __name__ == "__main__":
    unittest.main()
