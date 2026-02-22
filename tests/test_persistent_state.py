"""Direct unit tests for PersistentState branch coverage."""

import configparser
from unittest.mock import Mock, patch

from ace.persistent_state import PersistentState


def _make_state(all_variables=None, persistence_mode="deferred", filename="saved_variables.cfg"):
    """Create a PersistentState with mocked printer/gcode/save_variables."""
    gcode = Mock()
    gcode.run_script_from_command = Mock()

    save_vars = Mock()
    save_vars.allVariables = {} if all_variables is None else all_variables
    save_vars.filename = filename

    printer = Mock()
    printer.lookup_object = Mock(side_effect=lambda name, default=None: {
        "save_variables": save_vars,
    }.get(name, default))

    state = PersistentState(printer, gcode, persistence_mode=persistence_mode)
    return state, printer, gcode, save_vars


class TestPersistentStateImmediateMode:
    """Covers set_and_save() immediate-mode branch."""

    def test_set_and_save_immediate_writes_immediately_and_not_dirty(self):
        state, _printer, gcode, save_vars = _make_state(
            all_variables={"ace_current_index": -1},
            persistence_mode="immediate",
        )

        state.set_and_save("ace_current_index", 2)

        assert save_vars.allVariables["ace_current_index"] == 2
        gcode.run_script_from_command.assert_called_once_with(
            "SAVE_VARIABLE VARIABLE=ace_current_index VALUE=2"
        )
        assert not state.has_pending

    def test_set_and_save_immediate_clears_existing_dirty_marker(self):
        state, _printer, gcode, save_vars = _make_state(
            all_variables={"ace_current_index": -1},
            persistence_mode="immediate",
        )

        state.set("ace_current_index", 1)
        assert state.has_pending
        assert "ace_current_index" in state._dirty

        state.set_and_save("ace_current_index", 3)

        assert save_vars.allVariables["ace_current_index"] == 3
        gcode.run_script_from_command.assert_called_once_with(
            "SAVE_VARIABLE VARIABLE=ace_current_index VALUE=3"
        )
        assert "ace_current_index" not in state._dirty
        assert not state.has_pending


class TestPersistentStateFlushErrorHandling:
    """Covers flush() exception logging branch."""

    def test_flush_logs_exception_and_continues(self):
        state, _printer, _gcode, save_vars = _make_state(
            all_variables={"ok_var": 1, "bad_var": 2}
        )
        state.set("ok_var", 10)
        state.set("bad_var", 20)
        save_vars.allVariables["ok_var"] = 10
        save_vars.allVariables["bad_var"] = 20

        def fail_one(varname, value):
            if varname == "bad_var":
                raise RuntimeError("boom")

        with patch("ace.persistent_state.logging.exception") as log_exc:
            with patch.object(state, "_write_to_disk", side_effect=fail_one) as write_mock:
                state.flush()

        # flush attempts both writes and does not re-raise
        assert write_mock.call_count == 2
        log_exc.assert_called_once()
        assert not state.has_pending


class TestPersistentStateFlushDirect:
    """Covers flush_direct() success and failure branches."""

    def test_flush_direct_writes_all_variables_and_clears_dirty(self, tmp_path):
        filename = tmp_path / "saved_variables.cfg"
        variables = {
            "b_key": True,
            "a_key": {"nested": [1, None, False]},
            "c_key": "text",
        }
        state, _printer, _gcode, _save_vars = _make_state(
            all_variables=variables,
            filename=str(filename),
        )
        state._dirty.update({"a_key", "b_key"})
        assert state.has_pending

        with patch("ace.persistent_state.logging.info") as log_info:
            state.flush_direct()

        assert filename.exists()
        assert not state.has_pending
        log_info.assert_called_once()

        cfg = configparser.ConfigParser()
        cfg.read(filename)
        assert cfg.has_section("Variables")
        # sorted() write order is implementation detail; parse values instead
        assert cfg.get("Variables", "a_key") == repr(variables["a_key"])
        assert cfg.get("Variables", "b_key") == repr(variables["b_key"])
        assert cfg.get("Variables", "c_key") == repr(variables["c_key"])

    def test_flush_direct_logs_exception_when_open_fails(self):
        state, _printer, _gcode, _save_vars = _make_state(
            all_variables={"ace_current_index": 1},
            filename="/unused/path.cfg",
        )
        state._dirty.add("ace_current_index")

        with patch("builtins.open", side_effect=OSError("disk full")):
            with patch("ace.persistent_state.logging.exception") as log_exc:
                state.flush_direct()

        log_exc.assert_called_once_with("ACE: flush_direct failed")
        # dirty state remains when flush_direct fails before clear()
        assert state.has_pending

