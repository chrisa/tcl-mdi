from __future__ import annotations

from tcl_lathe_hmi.backends.sim import SimBackend
from tcl_lathe_hmi.config import MachineConfig
from tcl_lathe_hmi.machine import MachineService


def test_service_rejects_commands_while_backend_is_busy():
    config = MachineConfig(sim_motion_time_s=0.05)
    backend = SimBackend(config)
    service = MachineService(backend)

    service.connect()
    assert service.state.can_accept_commands

    assert service.jog_delta(x_mm=1.0, mode="rapid")
    assert service.state.busy

    assert not service.jog_delta(z_mm=1.0, mode="rapid")
    assert "not ready" in service.state.status_message

    backend.wait_idle(timeout_ms=500)
    service.poll()
    assert service.state.x_mm == 1.0
    assert service.state.can_accept_commands


def test_service_disconnect_resets_state():
    service = MachineService(SimBackend(MachineConfig()))

    service.connect()
    assert service.state.connected

    service.disconnect()
    assert not service.state.connected
    assert not service.state.busy
    assert not service.state.error
