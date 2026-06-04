from __future__ import annotations

import sys
import types

from tcl_lathe_hmi import main as main_module


class FakeApp:
    calls: list[str] = []

    def __init__(self, *, backend_name: str):
        self.backend_name = backend_name

    def run(self):
        FakeApp.calls.append(self.backend_name)


def test_main_strips_backend_args_before_kivy_run(monkeypatch):
    FakeApp.calls = []
    fake_ui_app = types.ModuleType("tcl_lathe_hmi.ui.app")
    fake_ui_app.TclLatheHmiApp = FakeApp

    monkeypatch.setitem(sys.modules, "tcl_lathe_hmi.ui.app", fake_ui_app)
    monkeypatch.setattr(sys, "argv", ["python -m tcl_lathe_hmi", "--backend", "fred"])

    assert main_module.main() == 0

    assert FakeApp.calls == ["fred"]
    assert sys.argv == ["python -m tcl_lathe_hmi"]


def test_main_accepts_separator_before_backend(monkeypatch):
    FakeApp.calls = []
    fake_ui_app = types.ModuleType("tcl_lathe_hmi.ui.app")
    fake_ui_app.TclLatheHmiApp = FakeApp

    monkeypatch.setitem(sys.modules, "tcl_lathe_hmi.ui.app", fake_ui_app)

    assert main_module.main(["--", "--backend", "sim"]) == 0

    assert FakeApp.calls == ["sim"]


def test_mouse_visible_mode_removes_raw_touch_input_providers():
    class FakeConfig:
        def __init__(self):
            self.values = {
                "mouse": "mouse,disable_on_activity",
                "%(name)s": "probesysfs",
                "device_touchpad": "mtdev,/dev/input/event13",
            }

        def items(self, section):
            assert section == "input"
            return list(self.values.items())

        def remove_option(self, section, key):
            assert section == "input"
            self.values.pop(key)

        def set(self, section, key, value):
            assert section == "input"
            self.values[key] = value

    config = FakeConfig()

    main_module._configure_mouse_only_input(config)

    assert config.values == {"mouse": "mouse"}
