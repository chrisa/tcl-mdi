from __future__ import annotations

from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.machine import MachineBackend


def create_backend(name: str, config: MachineConfig | None = None) -> MachineBackend:
    normalized = name.strip().lower().replace("_", "-").replace(" ", "-")
    machine_config = config or MachineConfig()

    if normalized in {"sim", "simulator"}:
        from .sim import SimBackend

        return SimBackend(machine_config)

    if normalized in {"fred", "fred-usb", "usb"}:
        from .fred import FredBackend

        return FredBackend(machine_config)

    raise ValueError(f"unknown backend: {name}")


__all__ = ["create_backend"]
