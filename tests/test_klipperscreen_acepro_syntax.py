"""Syntax/compile guard for the KlipperScreen ACE Pro panel script."""

from pathlib import Path
import py_compile


def test_klipperscreen_acepro_panel_compiles():
    """
    Ensure the panel script is valid Python syntax.

    This intentionally uses py_compile (not import) to avoid requiring GTK /
    KlipperScreen runtime dependencies in the test environment.
    """
    panel_path = Path(__file__).resolve().parent.parent / "KlipperScreen" / "acepro.py"
    assert panel_path.exists(), f"Missing panel script: {panel_path}"
    py_compile.compile(str(panel_path), doraise=True)

