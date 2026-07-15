# tests/test_ui_smoke.py
import importlib


def test_ui_modules_import():
    for mod in ("kdbmonitor.ui.admin", "kdbmonitor.ui.builder", "kdbmonitor.ui.monitor"):
        assert importlib.import_module(mod) is not None
