"""
Tests for the topology-first, name-agnostic port/instance/protocol
resolution added to fix the ACE instance-mixup bug.

ACE units are physically daisy-chained through a hub built into each unit.
`/dev/ttyACMx` numbers are assigned by kernel re-enumeration timing (whichever
device registers first grabs the lowest free number) and can point at a
*different* physical unit after every reset. The USB `LOCATION=` string is
tied to the physical port/hub wiring and is stable across resets.

These tests cover:
- protocol.py: parse_usb_location / get_port_usb_location / sort_ace_candidate_ports
- serial_manager.py: location-based port lookup, survives /dev/ttyACMx
  renumbering, falls back to legacy index-based matching, learns location
  after first connect, faster reconnect backoff once location is known
- manager.py: _resolve_daisy_chain_topology assigns instance numbers by
  physical daisy-chain order across mixed ACE1/ACE2 hardware, independent of
  per-protocol description counts
"""

import unittest
from unittest.mock import Mock, patch

from ace.protocol import (
    get_port_usb_location,
    parse_usb_location,
    sort_ace_candidate_ports,
)
from ace.serial_manager import AceSerialManager
from ace.manager import AceManager
from ace.config import ACE_INSTANCES, INSTANCE_MANAGERS, SLOTS_PER_ACE


def _make_port(device, description, location):
    port = Mock()
    port.device = device
    port.description = description
    port.hwid = f"USB VID:PID=1234:5678 LOCATION={location}"
    return port


class TestParseUsbLocation(unittest.TestCase):
    """parse_usb_location() sort-key behavior (shared with serial_manager)."""

    def test_simple_location(self):
        self.assertEqual(parse_usb_location("1-1.4.3"), (1, 1, 4, 3))

    def test_strips_interface_suffix(self):
        self.assertEqual(parse_usb_location("1-1.4.3:1.0"), (1, 1, 4, 3))

    def test_acm_fallback_sorts_after_real_locations(self):
        self.assertEqual(parse_usb_location("acm.2"), (999998, 2))
        self.assertLess(parse_usb_location("1-1.9.9"), parse_usb_location("acm.0"))

    def test_none_sorts_last(self):
        self.assertEqual(parse_usb_location(None), (999999,))


class TestGetPortUsbLocation(unittest.TestCase):
    """get_port_usb_location() extraction from a comports() entry."""

    def test_extracts_location_from_hwid(self):
        port = _make_port("/dev/ttyACM0", "ACE", "6-1.3")
        self.assertEqual(get_port_usb_location(port), "6-1.3")

    def test_falls_back_to_acm_number(self):
        port = Mock(device="/dev/ttyACM2", description="ACE", hwid="USB VID:PID=1234:5678")
        self.assertEqual(get_port_usb_location(port), "acm.2")


class TestSortAceCandidatePorts(unittest.TestCase):
    """
    sort_ace_candidate_ports() must order physically, independent of which
    protocol/description a given daisy-chain position uses.
    """

    def test_mixed_protocol_ports_ordered_by_physical_location(self):
        # Depth-differing layout (e.g. the second device sits behind an extra
        # hub level): the shallower root unit must sort first even though the
        # deeper device enumerated its /dev/ttyACMx path earlier. Note a
        # directly-chained ACE2 adapter is hubless and appears as a sibling,
        # not deeper - that layout is covered by
        # test_real_hardware_layout_ace2_adapter_is_sibling_not_deeper.
        ace2_port = _make_port("/dev/ttyACM0", "USB Single Serial", "6-1.3.1")
        ace1_port = _make_port("/dev/ttyACM1", "ACE", "6-1.3")
        unrelated_port = _make_port("/dev/ttyUSB5", "Arduino Uno", "6-1.4")

        matches = sort_ace_candidate_ports([ace2_port, ace1_port, unrelated_port])

        self.assertEqual(len(matches), 2)
        # Root position (shallower location) must come first regardless of
        # /dev/ttyACMx numbering or protocol.
        self.assertEqual(matches[0][2], "/dev/ttyACM1")
        self.assertEqual(matches[0][3], "ace1_json")
        self.assertEqual(matches[1][2], "/dev/ttyACM0")
        self.assertEqual(matches[1][3], "ace2_proto")

    def test_real_hardware_layout_ace2_adapter_is_sibling_not_deeper(self):
        """Layout observed on real hardware (BTT CB2, ACE1 with chained ACE2).

        The Gen1's built-in hub enumerates at 3-1; its own MCU hangs on hub
        port 3 (3-1.3) and chain-out is hub port 4. The hubless ACE2 RS485
        adapter plugged into chain-out therefore appears as a SIBLING at
        3-1.4 - same hop depth, NOT one hop deeper. The upstream ACE1 must
        still sort first (numeric port tiebreak within equal depth), and the
        unrelated shallower probe must not enter the candidate list at all.
        """
        probe_port = _make_port("/dev/ttyACM0", "stm32g431xx", "1-1")
        ace1_port = _make_port("/dev/ttyACM1", "ACE", "3-1.3")
        ace2_port = _make_port("/dev/ttyACM2", "USB Single Serial", "3-1.4")

        matches = sort_ace_candidate_ports([probe_port, ace2_port, ace1_port])

        self.assertEqual(
            [(match[2], match[3]) for match in matches],
            [("/dev/ttyACM1", "ace1_json"), ("/dev/ttyACM2", "ace2_proto")],
        )

    def test_ignores_non_ace_ports(self):
        unrelated_port = _make_port("/dev/ttyUSB5", "Arduino Uno", "6-1.4")
        matches = sort_ace_candidate_ports([unrelated_port])
        self.assertEqual(matches, [])


class TestFindPortByLocation(unittest.TestCase):
    """AceSerialManager.find_port_by_location() - the immune-to-renumbering lookup."""

    def setUp(self):
        self.mock_gcode = Mock()
        self.mock_reactor = Mock()

    @patch('ace.serial_manager.serial.tools.list_ports.comports')
    def test_finds_port_at_exact_location_regardless_of_device_path(self, mock_comports):
        mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0)
        port = _make_port("/dev/ttyACM7", "ACE", "6-1.3")
        mock_comports.return_value = [port]

        result = mgr.find_port_by_location("ACE", "6-1.3")
        self.assertEqual(result, "/dev/ttyACM7")

    @patch('ace.serial_manager.serial.tools.list_ports.comports')
    def test_survives_ttyacm_renumbering(self, mock_comports):
        """The same physical unit is found even if its /dev/ttyACMx changes."""
        mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0)

        # First boot: root ACE is ACM0
        mock_comports.return_value = [_make_port("/dev/ttyACM0", "ACE", "6-1.3")]
        self.assertEqual(mgr.find_port_by_location("ACE", "6-1.3"), "/dev/ttyACM0")

        # After a reset race, the same physical unit re-enumerates as ACM1
        mock_comports.return_value = [_make_port("/dev/ttyACM1", "ACE", "6-1.3")]
        self.assertEqual(mgr.find_port_by_location("ACE", "6-1.3"), "/dev/ttyACM1")

    @patch('ace.serial_manager.serial.tools.list_ports.comports')
    def test_returns_none_when_location_not_visible(self, mock_comports):
        mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0)
        mock_comports.return_value = [_make_port("/dev/ttyACM0", "ACE", "6-1.9")]

        self.assertIsNone(mgr.find_port_by_location("ACE", "6-1.3"))

    def test_returns_none_for_no_target_location(self):
        mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0)
        self.assertIsNone(mgr.find_port_by_location("ACE", None))


class TestFindConnectionPortPrefersLocation(unittest.TestCase):
    """find_connection_port() must prefer target_usb_location when set."""

    def setUp(self):
        self.mock_gcode = Mock()
        self.mock_reactor = Mock()

    @patch('ace.serial_manager.serial.tools.list_ports.comports')
    def test_uses_target_location_ignoring_index(self, mock_comports):
        # Two ACE1-type ports visible; instance is bound to the second one by
        # location, even though index-based matching (instance=0) would
        # normally pick the first.
        mgr = AceSerialManager(
            self.mock_gcode, self.mock_reactor, 0, target_usb_location="6-1.4"
        )
        port0 = _make_port("/dev/ttyACM0", "ACE", "6-1.3")
        port1 = _make_port("/dev/ttyACM1", "ACE", "6-1.4")
        mock_comports.return_value = [port0, port1]

        result = mgr.find_connection_port(instance=0)
        self.assertEqual(result, "/dev/ttyACM1")

    @patch('ace.serial_manager.serial.tools.list_ports.comports')
    def test_falls_back_to_legacy_when_location_not_visible(self, mock_comports):
        mgr = AceSerialManager(
            self.mock_gcode, self.mock_reactor, 0, target_usb_location="6-1.9"
        )
        port0 = _make_port("/dev/ttyACM0", "ACE", "6-1.3")
        mock_comports.return_value = [port0]

        result = mgr.find_connection_port(instance=0)
        self.assertEqual(result, "/dev/ttyACM0")

    @patch('ace.serial_manager.serial.tools.list_ports.comports')
    def test_legacy_behavior_unchanged_without_target_location(self, mock_comports):
        mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 1)
        port0 = _make_port("/dev/ttyACM0", "ACE", "6-1.3")
        port1 = _make_port("/dev/ttyACM1", "ACE", "6-1.4")
        mock_comports.return_value = [port0, port1]

        result = mgr.find_connection_port(instance=1)
        self.assertEqual(result, "/dev/ttyACM1")


class TestLocationLearningAndBackoff(unittest.TestCase):
    """Location is learned after first connect; backoff floor speeds up once known."""

    def setUp(self):
        self.mock_gcode = Mock()
        self.mock_reactor = Mock()

    def test_backoff_floor_is_slow_without_known_location(self):
        mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0)
        self.assertIsNone(mgr._target_usb_location)
        self.assertEqual(mgr._reconnect_backoff_floor(), mgr.RECONNECT_BACKOFF_MIN)

    def test_backoff_floor_is_fast_with_known_location(self):
        mgr = AceSerialManager(
            self.mock_gcode, self.mock_reactor, 0, target_usb_location="6-1.3"
        )
        self.assertEqual(mgr._reconnect_backoff_floor(), mgr.LOCATION_KNOWN_RECONNECT_BACKOFF_MIN)
        self.assertLess(mgr.LOCATION_KNOWN_RECONNECT_BACKOFF_MIN, mgr.RECONNECT_BACKOFF_MIN)

    @patch('ace.serial_manager.serial.tools.list_ports.comports')
    def test_learns_location_after_successful_connect(self, mock_comports):
        mgr = AceSerialManager(self.mock_gcode, self.mock_reactor, 0)
        self.assertIsNone(mgr._target_usb_location)

        port = _make_port("/dev/ttyACM0", "ACE", "6-1.3")
        mock_comports.return_value = [port]
        mgr.connect = Mock(return_value=True)
        mgr.send_request = Mock()

        result = mgr.auto_connect(0, 115200)

        self.assertTrue(result)
        self.assertEqual(mgr._target_usb_location, "6-1.3")


class TestDaisyChainTopologyResolution(unittest.TestCase):
    """AceManager._resolve_daisy_chain_topology() - the core instance-mixup fix."""

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.captured_instance_kwargs = []

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": 0,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={"value": 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": 2}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            return default

        def get(key, default=None):
            return {
                "filament_runout_sensor_name_rdm": "return_module",
                "filament_runout_sensor_name_nozzle": "toolhead_sensor",
                "protocol": "auto",
                "baud": "auto",
            }.get(key, default)

        def getboolean(key, default=None):
            return {
                "feed_assist_active_after_ace_connect": True,
                "rfid_inventory_sync_enabled": True,
                "ace_connection_supervision": True,
                "moonraker_lane_sync_enabled": False,
            }.get(key, default if default is not None else False)

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = get
        self.mock_config.getboolean.side_effect = getboolean

    def _instance_factory(self, instance_num, instance_config, printer, ace_enabled, **kwargs):
        self.captured_instance_kwargs.append((instance_num, instance_config, kwargs))
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.serial_mgr = kwargs.get("serial_mgr") or Mock(
            is_connected=Mock(return_value=False),
            get_connection_status=Mock(return_value={"last_connected_time": 0.0}),
        )
        inst.bus_session = kwargs.get("bus_session")
        return inst

    def _build_manager(self, ports):
        with patch("ace.manager.AceInstance", side_effect=self._instance_factory), \
             patch("ace.manager.EndlessSpool"), \
             patch("ace.manager.RunoutMonitor"), \
             patch("ace.manager.serial.tools.list_ports.comports", return_value=ports):
            return AceManager(self.mock_config)

    def test_mixed_protocol_daisy_chain_assigns_by_physical_order(self):
        """
        Root ACE1 (native serial) + downstream ACE2 Pro (CH340 bridge) - the
        exact hardware topology confirmed via lsusb. Instance 0 must be the
        physically-closest unit (ACE1), instance 1 the ACE2 Pro, regardless
        of how many ports match each description or /dev/ttyACMx ordering.
        """
        ace1_port = _make_port("/dev/ttyACM1", "ACE", "6-1.3")
        ace2_port = _make_port("/dev/ttyACM0", "USB Single Serial", "6-1.3.1")

        manager = self._build_manager([ace2_port, ace1_port])

        resolution = manager._topology_resolution
        self.assertEqual(resolution[0]["protocol_name"], "ace1_json")
        self.assertEqual(resolution[0]["target_location"], "6-1.3")
        self.assertFalse(resolution[0]["shared_bus"])

        self.assertEqual(resolution[1]["protocol_name"], "ace2_proto")
        self.assertEqual(resolution[1]["target_location"], "6-1.3.1")
        self.assertTrue(resolution[1]["shared_bus"])

        # Verify the resolved location was threaded through to AceInstance.
        kwargs_by_instance = {num: kwargs for num, _cfg, kwargs in self.captured_instance_kwargs}
        self.assertEqual(kwargs_by_instance[0]["target_usb_location"], "6-1.3")

    def test_no_hardware_connected_yields_empty_resolution(self):
        manager = self._build_manager([])
        self.assertEqual(manager._topology_resolution, {})

    def test_incomplete_ace1_scan_does_not_misbind_instance0(self):
        """
        Regression (Finding A): at startup only ONE of two expected ACE1 units
        is visible (the other is mid-watchdog-reset / still re-enumerating).

        The single visible unit sits at "6-1.4.4.3", i.e. it is physically the
        *second* unit in the chain (its upstream neighbour "6-1.3" is the one
        that's currently invisible). Nothing in a single LOCATION= string tells
        the manager whether the one port it can see is unit #0 or unit #1, so
        binding instance 0 to it is a coin-flip that, when wrong, makes
        instance 0 permanently drive the wrong physical unit for the whole
        session (and no re-resolution ever corrects it, since instance 0's
        target_usb_location is then pinned).

        Safe behaviour when fewer non-shared candidates than ace_count are
        visible: do NOT lock in a shifted mapping. Leave the non-shared
        instances unresolved so the claim-protected index fallback (and a
        future re-resolution once the full set appears) can place them without
        risking a misbind. This mirrors ``test_no_hardware_connected_yields_
        empty_resolution`` for the partially-visible case.
        """
        # ace_count == 2, but only the *second* ACE1 unit has enumerated so far.
        only_second_unit = _make_port("/dev/ttyACM0", "ACE", "6-1.4.4.3")

        manager = self._build_manager([only_second_unit])

        # Instance 0 must not be bound to the (possibly-second) lone unit.
        self.assertNotIn(
            "6-1.4.4.3",
            [
                entry.get("target_location")
                for entry in manager._topology_resolution.values()
            ],
            "instance was bound to an ambiguous single unit on an incomplete "
            "ACE1 scan - risks permanently driving the wrong physical unit",
        )
        self.assertEqual(
            manager._topology_resolution,
            {},
            "incomplete non-shared scan must not lock in a shifted mapping",
        )


# ---------------------------------------------------------------------------
# Exhaustive USB topology matrix
# ---------------------------------------------------------------------------
#
# Physical constraints these tests enforce (from real hardware, not just
# software assumptions):
#
# - Each ACE1 unit has its own internal USB hub. Its own USB-serial MCU is
#   wired to one fixed port of that hub (observed: port 3); the "daisy
#   chain out" jack on the back of the unit is wired to another fixed port
#   of the same hub (observed: port 4), which leads to the next unit's hub.
#   So the Nth ACE1 unit (0-indexed) in a chain sits at USB location
#   "<bus>-<root>.4.4...(N times)...3".
#
# - ACE2 does not speak USB; its "USB Single Serial" identity is a CH340
#   RS-485-over-USB bridge adapter with no hub of its own. It can only ever
#   be the LAST device in a chain, plugged into the daisy-chain-out port of
#   either the final ACE1 unit, or directly into the host if there are no
#   ACE1 units at all. It is therefore never a valid *upstream* device -
#   nothing can be chained "after" it - and at most one ACE2 adapter exists
#   per chain (its shared RS-485 bus backs every remaining logical
#   instance, it doesn't need a second adapter for a second logical unit).
#
# - Chains longer than 3 daisy-chained ACE1 units are out of scope: a 4th
#   ACE1 on the same chain exceeds what the internal hub silicon reliably
#   enumerates on real hardware.
#
# These helpers and tests build every valid (n_ace1, n_ace2) combination in
# that space - n_ace1 in 0..3, n_ace2 in 0..1 - and confirm topology
# resolution is correct regardless of /dev/ttyACMx enumeration order, and
# stays correct after a simulated reconnect that reassigns every
# /dev/ttyACMx path without changing physical USB locations.

BUS = "6"
ROOT_PORT = 1
CHAIN_OUT_PORT = 4
OWN_MCU_PORT = 3

# Every physically valid (n_ace1, n_ace2) combination given the hardware
# constraints above.
VALID_TOPOLOGIES = [
    (n_ace1, n_ace2)
    for n_ace1 in range(0, 4)   # 0-3 daisy-chained ACE1 units
    for n_ace2 in range(0, 2)   # 0-1 trailing ACE2 adapters
]


def _ace1_location(index):
    """USB LOCATION= for the (index)'th ACE1 unit (0-based) in a daisy chain."""
    hops = ".".join([str(CHAIN_OUT_PORT)] * index)
    if hops:
        return f"{BUS}-{ROOT_PORT}.{hops}.{OWN_MCU_PORT}"
    return f"{BUS}-{ROOT_PORT}.{OWN_MCU_PORT}"


def _ace2_location(n_ace1):
    """USB LOCATION= for a trailing ACE2 adapter after n_ace1 ACE1 units."""
    if n_ace1 == 0:
        return f"{BUS}-{ROOT_PORT}"
    hops = ".".join([str(CHAIN_OUT_PORT)] * n_ace1)
    return f"{BUS}-{ROOT_PORT}.{hops}"


def _expected_locations(n_ace1, n_ace2):
    """Expected physical instance order: all ACE1s (closest-first), then ACE2."""
    locations = [_ace1_location(i) for i in range(n_ace1)]
    if n_ace2:
        locations.append(_ace2_location(n_ace1))
    return locations


# Up to 3 additional ACE2 units can be RS-485-daisy-chained behind the first
# (last-in-USB-chain) ACE2 unit's own bus. Those extra units have no USB
# presence of their own at all - they're discovered by the first ACE2 via an
# RS-485 device-id discovery command, and all (up to) 4 logical ACE2
# instances are addressed over the SAME single "USB Single Serial" adapter
# port that the first ACE2 exposes. So from AceManager's point of view there
# is still exactly one physical candidate for the whole shared bus,
# regardless of how many logical ACE2 instances (1-4) sit behind it.
SHARED_BUS_LOGICAL_ACE2_TOPOLOGIES = [
    (n_ace1, n_ace2_logical)
    for n_ace1 in range(0, 4)          # 0-3 daisy-chained ACE1 units in front
    for n_ace2_logical in range(0, 5)  # 0-4 logical ACE2 units behind one adapter
]


def _expected_locations_with_logical_ace2(n_ace1, n_ace2_logical):
    """Like _expected_locations, but models 0-4 logical ACE2 units, all
    sharing the SAME physical adapter location (RS-485 device-id discovery)
    instead of at most one ACE2 "slot"."""
    locations = [_ace1_location(i) for i in range(n_ace1)]
    if n_ace2_logical:
        ace2_loc = _ace2_location(n_ace1)
        locations.extend([ace2_loc] * n_ace2_logical)
    return locations


def _build_topology_ports_with_logical_ace2(n_ace1, n_ace2_logical, device_order=None):
    """Same physical port model as _build_topology_ports: there is still at
    most one physical ACE2 "USB Single Serial" port no matter how many
    logical instances (1-4) ace_count asks the shared bus to back."""
    return _build_topology_ports(n_ace1, 1 if n_ace2_logical else 0, device_order=device_order)


def _build_topology_ports(n_ace1, n_ace2, device_order=None):
    """
    Build fake comports() entries for n_ace1 daisy-chained ACE1 units plus
    an optional trailing ACE2 adapter.

    `device_order` is an optional list of indices (into the physical-order
    entry list) controlling which /dev/ttyACMx number each entry gets -
    this is how enumeration-order independence and post-reconnect
    renumbering are simulated. When None, ports are assigned in physical
    order (ACM0=first unit, ACM1=second, ...).
    """
    entries = []
    for i in range(n_ace1):
        entries.append(("ACE", _ace1_location(i)))
    if n_ace2:
        entries.append(("USB Single Serial", _ace2_location(n_ace1)))

    total = len(entries)
    if device_order is None:
        device_order = list(range(total))
    assert sorted(device_order) == list(range(total)), (
        "device_order must be a permutation of range(len(entries))"
    )

    ports = []
    for physical_index, acm_number in zip(range(total), device_order):
        description, location = entries[physical_index]
        ports.append(_make_port(f"/dev/ttyACM{acm_number}", description, location))
    return ports


class TestFullUsbTopologyMatrixPureSort(unittest.TestCase):
    """
    Exhaustive (n_ace1=0..3) x (n_ace2=0..1) matrix at the
    sort_ace_candidate_ports() level, across every possible /dev/ttyACMx
    enumeration order (not just physical order) for each combination.
    """

    def test_every_valid_topology_sorts_correctly_regardless_of_enumeration_order(self):
        import itertools

        for n_ace1, n_ace2 in VALID_TOPOLOGIES:
            total = n_ace1 + n_ace2
            expected = _expected_locations(n_ace1, n_ace2)

            # For small counts, exhaustively try every possible /dev/ttyACMx
            # assignment permutation; that's the only variable that must
            # never affect resolved order.
            permutations = list(itertools.permutations(range(total))) if total else [()]
            # Cap runtime for the largest case (4! = 24) - still exhaustive.
            for device_order in permutations:
                with self.subTest(n_ace1=n_ace1, n_ace2=n_ace2, device_order=device_order):
                    ports = _build_topology_ports(n_ace1, n_ace2, device_order=list(device_order))
                    matches = sort_ace_candidate_ports(ports)

                    locations = [m[1] for m in matches]
                    protocols = [m[3] for m in matches]

                    self.assertEqual(locations, expected)
                    self.assertEqual(
                        protocols,
                        ["ace1_json"] * n_ace1 + (["ace2_proto"] if n_ace2 else [])
                    )

    def test_ace2_never_sorts_before_any_ace1(self):
        """Hardware-enforced invariant: ACE2 (shared_bus) must never precede
        an ACE1 unit in sort order, for any topology in the matrix."""
        for n_ace1, n_ace2 in VALID_TOPOLOGIES:
            if not (n_ace1 and n_ace2):
                continue
            with self.subTest(n_ace1=n_ace1, n_ace2=n_ace2):
                ports = _build_topology_ports(n_ace1, n_ace2)
                matches = sort_ace_candidate_ports(ports)
                shared_flags = [m[4].shared_bus for m in matches]
                # Once shared_bus=True appears, every remaining entry must
                # also be shared_bus=True (i.e. all False before all True).
                first_shared = shared_flags.index(True)
                self.assertTrue(all(shared_flags[first_shared:]))
                self.assertFalse(any(shared_flags[:first_shared]))


class TestFullTopologyMatrixEndToEnd(unittest.TestCase):
    """
    Exhaustive (n_ace1=0..3) x (n_ace2=0..1) matrix at the
    AceManager._resolve_daisy_chain_topology() level, plus reconnect/
    renumbering simulation: every device gets a brand-new /dev/ttyACMx path
    (as happens after a supervision-triggered reconnect-all or a fresh
    kernel re-enumeration) while physical USB locations stay identical, and
    resolution must still assign the same physical unit to the same logical
    instance.
    """

    def setUp(self):
        ACE_INSTANCES.clear()
        INSTANCE_MANAGERS.clear()

        self.mock_config = Mock()
        self.mock_printer = Mock()
        self.mock_reactor = Mock()
        self.mock_gcode = Mock()
        self.mock_save_vars = Mock()
        self.captured_instance_kwargs = []
        self._ace_count = 1  # overridden per test

        self.mock_config.get_printer.return_value = self.mock_printer
        self.mock_printer.get_reactor.return_value = self.mock_reactor
        self.mock_reactor.monotonic.return_value = 0.0
        self.mock_reactor.register_timer = Mock(return_value=None)
        self.mock_reactor.pause = Mock()

        self.variables = {
            "ace_global_enabled": True,
            "ace_current_index": -1,
            "ace_filament_pos": 0,
        }
        self.mock_save_vars.allVariables = self.variables

        def lookup(name, default=None):
            if name == "gcode":
                return self.mock_gcode
            if name == "save_variables":
                return self.mock_save_vars
            if name == "output_pin ACE_Pro":
                pin = Mock()
                pin.get_status = Mock(return_value={"value": 1})
                return pin
            return default

        self.mock_printer.lookup_object.side_effect = lookup

        def getint(key, default=None):
            vals = {"ace_count": self._ace_count}
            val = vals.get(key, default)
            return int(val) if val is not None else default

        def getfloat(key, default=None):
            return default

        def get(key, default=None):
            return {
                "filament_runout_sensor_name_rdm": "return_module",
                "filament_runout_sensor_name_nozzle": "toolhead_sensor",
                "protocol": "auto",
                "baud": "auto",
            }.get(key, default)

        def getboolean(key, default=None):
            return {
                "feed_assist_active_after_ace_connect": True,
                "rfid_inventory_sync_enabled": True,
                "ace_connection_supervision": True,
                "moonraker_lane_sync_enabled": False,
            }.get(key, default if default is not None else False)

        self.mock_config.getint.side_effect = getint
        self.mock_config.getfloat.side_effect = getfloat
        self.mock_config.get.side_effect = get
        self.mock_config.getboolean.side_effect = getboolean

    def _instance_factory(self, instance_num, instance_config, printer, ace_enabled, **kwargs):
        self.captured_instance_kwargs.append((instance_num, instance_config, kwargs))
        inst = Mock()
        inst.instance_num = instance_num
        inst.SLOT_COUNT = SLOTS_PER_ACE
        inst.tool_offset = instance_num * SLOTS_PER_ACE
        inst.serial_mgr = kwargs.get("serial_mgr") or Mock(
            is_connected=Mock(return_value=False),
            get_connection_status=Mock(return_value={"last_connected_time": 0.0}),
        )
        inst.bus_session = kwargs.get("bus_session")
        return inst

    def _build_manager(self, ports, ace_count):
        self._ace_count = ace_count
        with patch("ace.manager.AceInstance", side_effect=self._instance_factory), \
             patch("ace.manager.EndlessSpool"), \
             patch("ace.manager.RunoutMonitor"), \
             patch("ace.manager.serial.tools.list_ports.comports", return_value=ports):
            return AceManager(self.mock_config)

    def test_every_valid_topology_resolves_correctly(self):
        for n_ace1, n_ace2 in VALID_TOPOLOGIES:
            total = n_ace1 + n_ace2
            if total == 0:
                continue  # nothing to resolve; covered by the empty-hardware test
            with self.subTest(n_ace1=n_ace1, n_ace2=n_ace2):
                ports = _build_topology_ports(n_ace1, n_ace2)
                manager = self._build_manager(ports, ace_count=total)

                resolution = manager._topology_resolution
                expected_locations = _expected_locations(n_ace1, n_ace2)

                self.assertEqual(len(resolution), total)
                for instance_num, expected_location in enumerate(expected_locations):
                    entry = resolution[instance_num]
                    self.assertEqual(entry["target_location"], expected_location)
                    if n_ace2 and instance_num >= n_ace1:
                        self.assertEqual(entry["protocol_name"], "ace2_proto")
                        self.assertTrue(entry["shared_bus"])
                    else:
                        self.assertEqual(entry["protocol_name"], "ace1_json")
                        self.assertFalse(entry["shared_bus"])

    def test_reconnect_all_renumbers_ttyacm_but_resolution_stays_correct(self):
        """
        Simulate a reconnect-all event (e.g. supervision-triggered reconnect,
        or a fresh kernel re-enumeration) that hands out completely different
        /dev/ttyACMx paths, with the real physical USB wiring (and therefore
        LOCATION=) unchanged. Re-resolving topology from scratch must still
        assign each physical unit to the same logical instance number.
        """
        for n_ace1, n_ace2 in VALID_TOPOLOGIES:
            total = n_ace1 + n_ace2
            if total < 2:
                continue  # need at least 2 devices for renumbering to be meaningful
            with self.subTest(n_ace1=n_ace1, n_ace2=n_ace2):
                # Initial boot: physical order happens to match ACM numbering.
                ports_before = _build_topology_ports(n_ace1, n_ace2)
                manager = self._build_manager(ports_before, ace_count=total)
                resolution_before = dict(manager._topology_resolution)

                # Reconnect-all: every device gets a new /dev/ttyACMx (here,
                # fully reversed), same physical LOCATION= strings.
                reversed_order = list(reversed(range(total)))
                ports_after = _build_topology_ports(n_ace1, n_ace2, device_order=reversed_order)

                # Prove the /dev/ttyACMx paths actually did change.
                before_devices = {p.hwid: p.device for p in ports_before}
                after_devices = {p.hwid: p.device for p in ports_after}
                self.assertNotEqual(before_devices, after_devices)

                with patch("ace.manager.serial.tools.list_ports.comports", return_value=ports_after):
                    resolution_after = manager._resolve_daisy_chain_topology()

                expected_locations = _expected_locations(n_ace1, n_ace2)
                for instance_num, expected_location in enumerate(expected_locations):
                    self.assertEqual(
                        resolution_before[instance_num]["target_location"], expected_location
                    )
                    self.assertEqual(
                        resolution_after[instance_num]["target_location"], expected_location,
                        f"Instance {instance_num} location changed after renumbering-only "
                        f"reconnect (n_ace1={n_ace1}, n_ace2={n_ace2})"
                    )

    def test_partial_ace_count_only_resolves_requested_instances(self):
        """If ace_count is smaller than the number of physically connected
        units, only that many logical instances should be resolved, always
        starting from the physically-closest unit."""
        n_ace1, n_ace2 = 3, 1
        ports = _build_topology_ports(n_ace1, n_ace2)
        manager = self._build_manager(ports, ace_count=2)

        resolution = manager._topology_resolution
        self.assertEqual(len(resolution), 2)
        self.assertEqual(resolution[0]["target_location"], _ace1_location(0))
        self.assertEqual(resolution[1]["target_location"], _ace1_location(1))
        self.assertEqual(resolution[0]["protocol_name"], "ace1_json")
        self.assertEqual(resolution[1]["protocol_name"], "ace1_json")

    def test_shared_bus_backs_multiple_logical_ace2_instances(self):
        """
        Up to 4 ACE2 units can be RS-485-daisy-chained behind the first ACE2
        unit's adapter - discovered by device-id, never appearing as separate
        USB devices. Regardless of how many logical ACE2 instances ace_count
        requests (1-4), they must all resolve to the SAME physical
        target_location with shared_bus=True, and every logical ACE1
        instance in front of them must stay unaffected.
        """
        for n_ace1, n_ace2_logical in SHARED_BUS_LOGICAL_ACE2_TOPOLOGIES:
            total = n_ace1 + n_ace2_logical
            if total == 0:
                continue
            with self.subTest(n_ace1=n_ace1, n_ace2_logical=n_ace2_logical):
                ports = _build_topology_ports_with_logical_ace2(n_ace1, n_ace2_logical)
                manager = self._build_manager(ports, ace_count=total)

                resolution = manager._topology_resolution
                expected_locations = _expected_locations_with_logical_ace2(n_ace1, n_ace2_logical)

                self.assertEqual(len(resolution), total)
                for instance_num, expected_location in enumerate(expected_locations):
                    entry = resolution[instance_num]
                    self.assertEqual(entry["target_location"], expected_location)
                    if n_ace2_logical and instance_num >= n_ace1:
                        self.assertEqual(entry["protocol_name"], "ace2_proto")
                        self.assertTrue(entry["shared_bus"])
                    else:
                        self.assertEqual(entry["protocol_name"], "ace1_json")
                        self.assertFalse(entry["shared_bus"])

                if n_ace2_logical:
                    # All logical ACE2 instances must share the exact same
                    # physical location (one exposed adapter port) - never
                    # distinct locations, since there is only one USB device
                    # backing all of them.
                    ace2_locations = {
                        resolution[i]["target_location"] for i in range(n_ace1, total)
                    }
                    self.assertEqual(len(ace2_locations), 1)

    def test_shared_bus_multi_instance_survives_reconnect_renumbering(self):
        """After a reconnect-all that renumbers /dev/ttyACMx (but not the
        physical USB wiring), every logical ACE2 instance behind the shared
        bus must still resolve to the same physical adapter location."""
        for n_ace1, n_ace2_logical in SHARED_BUS_LOGICAL_ACE2_TOPOLOGIES:
            total = n_ace1 + n_ace2_logical
            if total < 2:
                continue
            with self.subTest(n_ace1=n_ace1, n_ace2_logical=n_ace2_logical):
                ports_before = _build_topology_ports_with_logical_ace2(n_ace1, n_ace2_logical)
                manager = self._build_manager(ports_before, ace_count=total)
                resolution_before = dict(manager._topology_resolution)

                physical_count = n_ace1 + (1 if n_ace2_logical else 0)
                reversed_order = list(reversed(range(physical_count)))
                ports_after = _build_topology_ports_with_logical_ace2(
                    n_ace1, n_ace2_logical, device_order=reversed_order
                )

                with patch("ace.manager.serial.tools.list_ports.comports", return_value=ports_after):
                    resolution_after = manager._resolve_daisy_chain_topology()

                expected_locations = _expected_locations_with_logical_ace2(n_ace1, n_ace2_logical)
                for instance_num, expected_location in enumerate(expected_locations):
                    self.assertEqual(
                        resolution_before[instance_num]["target_location"], expected_location
                    )
                    self.assertEqual(
                        resolution_after[instance_num]["target_location"], expected_location,
                        f"Instance {instance_num} location changed after renumbering-only "
                        f"reconnect (n_ace1={n_ace1}, n_ace2_logical={n_ace2_logical})"
                    )


if __name__ == "__main__":
    unittest.main()
