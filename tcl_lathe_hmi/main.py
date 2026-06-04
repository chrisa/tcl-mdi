from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the TCL lathe Kivy HMI")
    parser.add_argument(
        "--backend",
        choices=("sim", "fred"),
        default="sim",
        help="machine backend to use at startup",
    )
    raw_args = sys.argv[1:] if argv is None else list(argv)
    if raw_args and raw_args[0] == "--":
        raw_args = raw_args[1:]
    args = parser.parse_args(raw_args)

    # Kivy also parses sys.argv unless KIVY_NO_ARGS is set. Remove our
    # application arguments before importing Kivy so --backend is not rejected
    # by Kivy's parser.
    if argv is None:
        sys.argv = [sys.argv[0]]

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
