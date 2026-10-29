"""Tests for how AceInstance applies RFID replies and per-slot status entries
to the slot inventory: incomplete tag data keeps what the user set, and
slot entries with an index outside the unit are ignored."""
import copy
import unittest
from unittest.mock import Mock, patch

from ace.config import RFID_STATE_IDENTIFIED
from ace.instance import AceInstance, INSTANCE_MANAGERS
from tests.test_feed_assist_recovery import ACE_CONFIG

SLOT = 1
MANUAL_COLOR = [1, 2, 3]


class InventoryTestBase(unittest.TestCase):

    def setUp(self):
        INSTANCE_MANAGERS.clear()
        printer = Mock()
        printer.lookup_object = Mock(return_value=Mock())
        with patch('ace.instance.AceSerialManager'):
            self.instance = AceInstance(0, ACE_CONFIG, printer)
        INSTANCE_MANAGERS[0] = Mock()
        inv = self.instance.inventory[SLOT]
        inv["status"] = "ready"
        inv["material"] = "PETG"
        inv["temp"] = 235
        inv["color"] = list(MANUAL_COLOR)


class TestRfidReplyKeepsManualData(InventoryTestBase):

    def reply(self, **result):
        self.instance._handle_rfid_info_response(SLOT, {
            "code": 0,
            "result": dict({"index": SLOT, "rfid": RFID_STATE_IDENTIFIED}, **result),
        })
        return self.instance.inventory[SLOT]

    def test_tag_without_temps_or_type_keeps_manual_temp_and_material(self):
        inv = self.reply(type="", sku="AHPLBK-101")

        self.assertEqual(inv["temp"], 235)
        self.assertEqual(inv["material"], "PETG")
        self.assertEqual(inv["sku"], "AHPLBK-101")

    def test_colors_entry_shorter_than_three_keeps_colour(self):
        inv = self.reply(type="PLA", colors=[[10, 20]])

        self.assertEqual(inv["color"], MANUAL_COLOR)

    def test_direct_color_used_without_colors_array(self):
        inv = self.reply(type="PLA", color=[10, 20, 30])

        self.assertEqual(inv["color"], [10, 20, 30])

    def test_all_zero_direct_color_is_ignored(self):
        inv = self.reply(type="PLA", color=[0, 0, 0])

        self.assertEqual(inv["color"], MANUAL_COLOR)


class TestStatusUpdateIgnoresForeignSlotIndex(InventoryTestBase):

    def test_out_of_range_slot_indices_leave_inventory_unchanged(self):
        for index in (None, -1, 7):
            with self.subTest(index=index):
                before = copy.deepcopy(self.instance.inventory)
                self.instance._status_update_callback({"result": {
                    "status": "ready",
                    "slots": [{"index": index, "status": "empty", "rfid": 0}],
                }})

                self.assertEqual(self.instance.inventory, before)

    def test_valid_slot_index_still_updates_inventory(self):
        self.instance._status_update_callback({"result": {
            "status": "ready",
            "slots": [{"index": SLOT, "status": "empty", "rfid": 0}],
        }})

        self.assertEqual(self.instance.inventory[SLOT]["status"], "empty")


if __name__ == '__main__':
    unittest.main()
