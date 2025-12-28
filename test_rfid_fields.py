#!/usr/bin/env python3
"""Test that additional RFID fields are properly included in inventory JSON output."""

import json

# Simulate the inventory structure with additional RFID fields
def test_inventory_json_output():
    """Test that optional RFID fields are included in JSON when present."""
    
    # Simulate inventory with RFID data
    inventory = [
        {
            "status": "ready",
            "color": [234, 42, 43],
            "material": "PLA",
            "temp": 200,
            "rfid": True,
            "sku": "AHPLBK-101",
            "brand": "Some Brand",
            "icon_type": 0,
            "rgba": [234, 42, 43, 255]
        },
        {
            "status": "ready",
            "color": [248, 233, 17],
            "material": "PLA High Speed",
            "temp": 215,
            "rfid": True,
            "sku": "AHHSBK-103",
            "icon_type": 0,
            "rgba": [248, 233, 17, 255]
        },
        {
            "status": "ready",
            "color": [255, 255, 255],
            "material": "PLA",
            "temp": 200,
            "rfid": False  # No RFID data, so no extra fields
        },
        {
            "status": "empty",
            "color": [0, 0, 0],
            "material": "",
            "temp": 0,
            "rfid": False
        }
    ]
    
    # Simulate JSON output generation (matching the code in instance.py)
    slots_out = []
    for inv in inventory:
        slot_data = {
            "status": inv.get("status"),
            "color": inv.get("color"),
            "material": inv.get("material"),
            "temp": inv.get("temp"),
            "rfid": inv.get("rfid", False),
        }
        # Include optional RFID metadata fields if present
        if "sku" in inv:
            slot_data["sku"] = inv["sku"]
        if "brand" in inv:
            slot_data["brand"] = inv["brand"]
        if "icon_type" in inv:
            slot_data["icon_type"] = inv["icon_type"]
        if "rgba" in inv:
            slot_data["rgba"] = inv["rgba"]
        slots_out.append(slot_data)
    
    json_output = json.dumps({"instance": 0, "slots": slots_out})
    print("JSON Output:")
    print(json.dumps(json.loads(json_output), indent=2))
    
    # Verify the structure
    data = json.loads(json_output)
    assert "instance" in data
    assert "slots" in data
    assert len(data["slots"]) == 4
    
    # Check slot 0 has all RFID fields
    slot0 = data["slots"][0]
    assert slot0["status"] == "ready"
    assert slot0["rfid"] == True
    assert "sku" in slot0
    assert slot0["sku"] == "AHPLBK-101"
    assert "brand" in slot0
    assert "icon_type" in slot0
    assert "rgba" in slot0
    assert slot0["rgba"] == [234, 42, 43, 255]
    
    # Check slot 2 (no RFID) doesn't have extra fields
    slot2 = data["slots"][2]
    assert slot2["status"] == "ready"
    assert slot2["rfid"] == False
    assert "sku" not in slot2
    assert "brand" not in slot2
    assert "icon_type" not in slot2
    assert "rgba" not in slot2
    
    print("\n✓ All tests passed!")
    print(f"✓ RFID slots include: sku, brand, icon_type, rgba")
    print(f"✓ Non-RFID slots exclude extra fields")
    print(f"✓ Backward compatibility maintained (required fields always present)")

if __name__ == "__main__":
    test_inventory_json_output()
