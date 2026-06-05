from __future__ import annotations

from dataclasses import dataclass


JOG_INCREMENTS_MM = (1.0, 0.1, 0.01, 0.001)


@dataclass(frozen=True)
class MachineConfig:
    usb_vid: int = 0x2E8A
    usb_pid: int = 0x000A
    usb_timeout_ms: int = 1000
    fred_poll_period_ms: int = 20

    x_counts_per_mm: float = 100.0
    z_counts_per_mm: float = 100.0

    jog_slew: int = 61
    jog_feed: int = 100
    jog_accumulate_delay_s: float = 0.25
    default_spindle_rpm: float = 1200.0
    spindle_at_speed_tolerance_rpm: float = 100.0

    x_min_limit_mm: float = -100.0
    x_max_limit_mm: float = 100.0
    z_min_limit_mm: float = -100.0
    z_max_limit_mm: float = 100.0
    soft_limits_enabled: bool = True

    ui_poll_interval_s: float = 0.1
    sim_motion_time_s: float = 0.18
    sim_tool_change_time_s: float = 0.35
    sim_spindle_command_time_s: float = 0.1
    sim_spindle_ramp_rpm_per_s: float = 3500.0
