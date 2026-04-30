import importlib.util
import math
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "slicer" / "orca_flush_to_purgelength.py"
    spec = importlib.util.spec_from_file_location("ace.orca_flush_to_purgelength_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_number_list_handles_commas_and_spaces():
    module = _load_module()
    assert module.parse_number_list("1, 2  3\t4") == [1.0, 2.0, 3.0, 4.0]


def test_infer_tool_count_prefers_square_matrix():
    module = _load_module()
    # 3x3 matrix implies 3 tools, even if vector has fewer entries.
    assert module.infer_tool_count([0.0] * 9, [1.0, 2.0]) == 3


def test_compute_flush_volume_first_tool_uses_vector_and_multiplier():
    module = _load_module()
    volume = module.compute_flush_volume(
        old_tool=None,
        new_tool=1,
        n_tools=2,
        matrix=[0.0, 100.0, 200.0, 0.0],
        vector=[10.0, 20.0],
        multiplier=1.5,
    )
    assert volume == 30.0


def test_compute_flush_volume_toolchange_uses_matrix_entry():
    module = _load_module()
    volume = module.compute_flush_volume(
        old_tool=0,
        new_tool=1,
        n_tools=2,
        matrix=[0.0, 100.0, 200.0, 0.0],
        vector=[10.0, 20.0],
        multiplier=1.5,
    )
    assert volume == 150.0


def test_process_gcode_inserts_expected_purge_commands():
    module = _load_module()
    lines = [
        "; flush_multiplier = 1.5\n",
        "; flush_volumes_matrix = 0,100,200,0\n",
        "; flush_volumes_vector = 10,20\n",
        "G28\n",
        "T0\n",
        "G1 X1\n",
        "T1 ; change\n",
        "T1\n",
        "T0\n",
    ]

    out_lines, info = module.process_gcode(lines, filament_diameter=1.75, round_digits=2)

    purge_lines = [line for line in out_lines if line.startswith("ACE_SET_PURGE_AMOUNT")]
    assert purge_lines == [
        "ACE_SET_PURGE_AMOUNT PURGELENGTH=6.24\n",
        "ACE_SET_PURGE_AMOUNT PURGELENGTH=62.36\n",
        "ACE_SET_PURGE_AMOUNT PURGELENGTH=0.00\n",
        "ACE_SET_PURGE_AMOUNT PURGELENGTH=124.73\n",
    ]

    assert info == {
        "flush_multiplier": 1.5,
        "tools": 2,
        "matrix_len": 4,
        "vector_len": 2,
        "toolchanges": 4,
        "filament_diameter": 1.75,
    }


def test_main_rewrites_file_with_header_and_purge_lengths(tmp_path, monkeypatch):
    module = _load_module()
    gcode = tmp_path / "sample.gcode"
    gcode.write_text(
        "; flush_multiplier = 1\n"
        "; flush_volumes_matrix = 0,100,200,0\n"
        "; flush_volumes_vector = 12,34\n"
        "G28\n"
        "T0\n"
        "T1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["orca_flush_to_purgelength.py", str(gcode), "--diameter", "1.75", "--round", "1"])
    monkeypatch.delenv("FLUSH_FILAMENT_DIAMETER", raising=False)

    module.main()

    content = gcode.read_text(encoding="utf-8")
    assert content.startswith("; === flush2len postprocess begin ===\n")
    assert "; multiplier=1.0 tools=2 matrix=4 vector=2 diameter=1.75 toolchanges=2\n" in content
    assert "ACE_SET_PURGE_AMOUNT PURGELENGTH=5.0\nT0\n" in content
    assert "ACE_SET_PURGE_AMOUNT PURGELENGTH=41.6\nT1\n" in content


def test_volume_to_length_mm_returns_zero_for_zero_diameter():
    module = _load_module()
    assert module.volume_to_length_mm(100.0, 0.0) == 0.0


def test_volume_to_length_mm_negative_diameter_matches_positive():
    module = _load_module()
    assert module.volume_to_length_mm(100.0, -1.0) == module.volume_to_length_mm(100.0, 1.0)


def test_volume_to_length_mm_uses_circular_filament_area():
    module = _load_module()
    expected = 100.0 / (math.pi * (1.75 * 0.5) ** 2)
    assert module.volume_to_length_mm(100.0, 1.75) == expected
