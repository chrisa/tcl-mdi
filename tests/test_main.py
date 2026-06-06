from __future__ import annotations

import sys
import types
from typing import Any, cast

from tcl_lathe_hmi import main as main_module


class FakeApp:
    calls: list[str] = []
    start_maximised_calls: list[bool] = []

    def __init__(self, *, backend_name: str, start_maximised: bool = False):
        self.backend_name = backend_name
        self.start_maximised = start_maximised

    def run(self):
        FakeApp.calls.append(self.backend_name)
        FakeApp.start_maximised_calls.append(self.start_maximised)


def test_main_strips_backend_args_before_kivy_run(monkeypatch):
    FakeApp.calls = []
    FakeApp.start_maximised_calls = []
    fake_ui_app = types.ModuleType("tcl_lathe_hmi.ui.app")
    cast(Any, fake_ui_app).TclLatheHmiApp = FakeApp

    monkeypatch.setitem(sys.modules, "tcl_lathe_hmi.ui.app", fake_ui_app)
    monkeypatch.setattr(sys, "argv", ["python -m tcl_lathe_hmi", "--backend", "fred"])

    assert main_module.main() == 0

    assert FakeApp.calls == ["fred"]
    assert FakeApp.start_maximised_calls == [False]
    assert sys.argv == ["python -m tcl_lathe_hmi"]


def test_main_accepts_separator_before_backend(monkeypatch):
    FakeApp.calls = []
    FakeApp.start_maximised_calls = []
    fake_ui_app = types.ModuleType("tcl_lathe_hmi.ui.app")
    cast(Any, fake_ui_app).TclLatheHmiApp = FakeApp

    monkeypatch.setitem(sys.modules, "tcl_lathe_hmi.ui.app", fake_ui_app)

    assert main_module.main(["--", "--backend", "sim"]) == 0

    assert FakeApp.calls == ["sim"]
    assert FakeApp.start_maximised_calls == [False]


def test_windowed_maximised_configures_maximised_startup(monkeypatch):
    FakeApp.calls = []
    FakeApp.start_maximised_calls = []
    fake_ui_app = types.ModuleType("tcl_lathe_hmi.ui.app")
    cast(Any, fake_ui_app).TclLatheHmiApp = FakeApp
    captured: dict[str, object] = {}

    def fake_configure_kivy(**kwargs):
        captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "tcl_lathe_hmi.ui.app", fake_ui_app)
    monkeypatch.setattr(main_module, "_configure_kivy", fake_configure_kivy)

    assert main_module.main(["--windowed", "--maximised"]) == 0

    assert captured["fullscreen"] is False
    assert FakeApp.start_maximised_calls == [True]


def test_maximised_without_windowed_keeps_fullscreen_startup(monkeypatch):
    FakeApp.calls = []
    FakeApp.start_maximised_calls = []
    fake_ui_app = types.ModuleType("tcl_lathe_hmi.ui.app")
    cast(Any, fake_ui_app).TclLatheHmiApp = FakeApp
    captured: dict[str, object] = {}

    def fake_configure_kivy(**kwargs):
        captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "tcl_lathe_hmi.ui.app", fake_ui_app)
    monkeypatch.setattr(main_module, "_configure_kivy", fake_configure_kivy)

    assert main_module.main(["--maximised"]) == 0

    assert captured["fullscreen"] is True
    assert FakeApp.start_maximised_calls == [False]


def test_input_alias_selects_input_mode(monkeypatch):
    FakeApp.calls = []
    FakeApp.start_maximised_calls = []
    fake_ui_app = types.ModuleType("tcl_lathe_hmi.ui.app")
    cast(Any, fake_ui_app).TclLatheHmiApp = FakeApp
    captured: dict[str, object] = {}

    def fake_configure_kivy(**kwargs):
        captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "tcl_lathe_hmi.ui.app", fake_ui_app)
    monkeypatch.setattr(main_module, "_configure_kivy", fake_configure_kivy)

    assert main_module.main(["--windowed", "--input", "mouse"]) == 0

    assert captured["input_mode"] == "mouse"


def test_configure_kivy_sets_windowed_graphics_options(monkeypatch):
    class FakeConfig:
        values = {
            "input": {
                "mouse": "mouse,disable_on_activity",
                "%(name)s": "probesysfs",
            },
            "graphics": {},
        }

        @classmethod
        def items(cls, section):
            assert section == "input"
            return list(cls.values[section].items())

        @classmethod
        def remove_option(cls, section, key):
            assert section == "input"
            cls.values[section].pop(key)

        @classmethod
        def set(cls, section, key, value):
            cls.values[section][key] = value

    fake_kivy = types.ModuleType("kivy")
    fake_config = types.ModuleType("kivy.config")
    cast(Any, fake_config).Config = FakeConfig

    monkeypatch.setitem(sys.modules, "kivy", fake_kivy)
    monkeypatch.setitem(sys.modules, "kivy.config", fake_config)

    main_module._configure_kivy(
        fullscreen=False,
        show_cursor=True,
        input_mode="mouse",
    )

    assert FakeConfig.values["graphics"] == {
        "fullscreen": "0",
        "borderless": "0",
        "show_cursor": "1",
    }


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


def test_touch_mode_removes_mouse_input_provider():
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

    main_module._configure_touch_only_input(config)

    assert config.values == {"%(name)s": "probesysfs"}


def test_dual_mode_keeps_touch_and_suppressed_mouse():
    class FakeConfig:
        def __init__(self):
            self.values = {"stale": "provider"}

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

    main_module._configure_dual_input(config)

    assert config.values == {
        "%(name)s": "probesysfs",
        "mouse": "mouse,disable_on_activity",
    }
