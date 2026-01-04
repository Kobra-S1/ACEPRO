import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock


def _load_core_module():
    """Load extras/ace/__init__.py as a package module for testing."""
    path = Path(__file__).resolve().parents[1] / "extras" / "ace" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "ace.core_init_test",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_config_registers_commands_and_instances():
    module = _load_core_module()
    config = Mock()
    printer = Mock()
    config.get_printer.return_value = printer

    manager = Mock()
    manager.instances = [Mock(), Mock()]
    module.AceManager = Mock(return_value=manager)
    module.register_all_commands = Mock()

    result = module.load_config(config)

    module.AceManager.assert_called_once_with(config)
    module.register_all_commands.assert_called_once_with(printer)
    printer.add_object.assert_any_call("ace_instance_0", manager.instances[0])
    printer.add_object.assert_any_call("ace_instance_1", manager.instances[1])
    assert result is manager


def test_load_config_handles_no_instances():
    module = _load_core_module()
    config = Mock()
    printer = Mock()
    config.get_printer.return_value = printer

    manager = Mock()
    manager.instances = []
    module.AceManager = Mock(return_value=manager)
    module.register_all_commands = Mock()

    result = module.load_config(config)

    printer.add_object.assert_not_called()
    assert result is manager
