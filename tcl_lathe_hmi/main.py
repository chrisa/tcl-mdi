from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the TCL lathe Kivy HMI")
    parser.add_argument(
        "--backend",
        choices=("sim", "fred"),
        default="sim",
        help="machine backend to use at startup",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="run in a normal window instead of taking over the display",
    )
    parser.add_argument(
        "--show-cursor",
        action="store_true",
        help="show the mouse cursor",
    )
    raw_args = sys.argv[1:] if argv is None else list(argv)
    if raw_args and raw_args[0] == "--":
        raw_args = raw_args[1:]
    args = parser.parse_args(raw_args)

    # Set before the first Kivy import so Kivy leaves application arguments to us.
    os.environ.setdefault("KIVY_NO_ARGS", "1")
    if argv is None:
        sys.argv = [sys.argv[0]]

    _configure_kivy(fullscreen=not args.windowed, show_cursor=args.show_cursor)

    try:
        from tcl_lathe_hmi.ui.app import TclLatheHmiApp
    except ModuleNotFoundError as exc:
        if exc.name == "kivy":
            print(
                "Kivy is not installed. Install this project with its dependencies, "
                "then run tcl-lathe-hmi again.",
                file=sys.stderr,
            )
            return 1
        raise

    TclLatheHmiApp(backend_name=args.backend).run()
    return 0


def _configure_kivy(*, fullscreen: bool, show_cursor: bool) -> None:
    from kivy.config import Config

    Config.set("graphics", "fullscreen", "auto" if fullscreen else "0")
    Config.set("graphics", "borderless", "1" if fullscreen else "0")
    Config.set("graphics", "show_cursor", "1" if show_cursor else "0")
    # Many touchscreens also emit mouse compatibility events. In cursor-visible
    # debug mode, keep normal mouse input so the pointer remains authoritative.
    if show_cursor:
        _configure_mouse_only_input(Config)
    else:
        Config.set("input", "mouse", "mouse,disable_on_activity")


def _configure_mouse_only_input(config) -> None:
    for key, _value in list(config.items("input")):
        config.remove_option("input", key)
    config.set("input", "mouse", "mouse")
