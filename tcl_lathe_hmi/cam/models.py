from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace


class CamValidationError(ValueError):
    pass


@dataclass(frozen=True)
class StockSpec:
    diameter_mm: float = 20.0
    length_mm: float = 60.0
    z_front_mm: float = 0.0
    face_allowance_mm: float = 1.0
    clearance_mm: float = 3.0


@dataclass(frozen=True)
class TurningSpec:
    enabled: bool = False
    face: bool = False
    rough: bool = False
    finish: bool = False
    target_diameter_mm: float = 16.0
    target_length_mm: float = 60.0
    stock_to_leave_mm: float = 0.5
    step_over_mm: float = 0.5
    rough_feed: float = 80.0
    finish_feed: float = 40.0
    spindle_rpm: float = 1200.0
    tool_number: int = 1
    station: int | None = 1
    tool_string: str = "DCMT070204R"
    tool_rotation_deg: float = 0.0


@dataclass(frozen=True)
class TaperSpec:
    enabled: bool = False
    start_diameter_mm: float = 16.0
    end_diameter_mm: float = 12.0
    start_z_mm: float = 0.0
    end_z_mm: float = -40.0


@dataclass(frozen=True)
class HoleSpec:
    center_drill: bool = False
    drill: bool = False
    bore: bool = False
    center_depth_mm: float = 2.0
    drill_diameter_mm: float = 5.0
    drill_depth_mm: float = 30.0
    bore_diameter_mm: float = 10.0
    bore_depth_mm: float = 25.0
    boring_step_over_mm: float = 0.5
    center_feed: float = 30.0
    drill_feed: float = 45.0
    boring_feed: float = 35.0
    spindle_rpm: float = 1000.0
    center_tool_number: int = 5
    center_station: int | None = 5
    drill_tool_number: int = 6
    drill_station: int | None = 6
    boring_tool_number: int = 9
    boring_station: int | None = None


@dataclass(frozen=True, init=False)
class ThreadSpec:
    external: bool = False
    internal: bool = False
    taper: bool = False
    major_diameter_mm: float = 16.0
    pitch_mm: float = 1.0
    length_mm: float = 20.0
    depth_mm: float = 1.23
    passes: int = 10
    spring_passes: int = 1
    start_z_mm: float = 0.0
    clearance_mm: float = 3.0
    spindle_rpm: float = 300.0
    tool_number: int = 4
    station: int | None = 4

    def __init__(
        self,
        external: bool = False,
        internal: bool = False,
        taper: bool = False,
        major_diameter_mm: float = 16.0,
        pitch_mm: float = 1.0,
        length_mm: float = 20.0,
        depth_mm: float = 1.23,
        passes: int = 10,
        spring_passes: int = 1,
        start_z_mm: float = 0.0,
        clearance_mm: float = 3.0,
        spindle_rpm: float = 300.0,
        tool_number: int = 4,
        station: int | None = 4,
        *,
        enabled: bool | None = None,
    ):
        if enabled is not None:
            external = external or enabled
        object.__setattr__(self, "external", external)
        object.__setattr__(self, "internal", internal)
        object.__setattr__(self, "taper", taper)
        object.__setattr__(self, "major_diameter_mm", major_diameter_mm)
        object.__setattr__(self, "pitch_mm", pitch_mm)
        object.__setattr__(self, "length_mm", length_mm)
        object.__setattr__(self, "depth_mm", depth_mm)
        object.__setattr__(self, "passes", passes)
        object.__setattr__(self, "spring_passes", spring_passes)
        object.__setattr__(self, "start_z_mm", start_z_mm)
        object.__setattr__(self, "clearance_mm", clearance_mm)
        object.__setattr__(self, "spindle_rpm", spindle_rpm)
        object.__setattr__(self, "tool_number", tool_number)
        object.__setattr__(self, "station", station)

    @property
    def enabled(self) -> bool:
        return self.external or self.internal or self.taper


@dataclass(frozen=True)
class LatheCamJob:
    stock: StockSpec = field(default_factory=StockSpec)
    turning: TurningSpec = field(default_factory=TurningSpec)
    taper: TaperSpec = field(default_factory=TaperSpec)
    hole: HoleSpec = field(default_factory=HoleSpec)
    thread: ThreadSpec = field(default_factory=ThreadSpec)

    def validate(self) -> None:
        _positive(self.stock.diameter_mm, "stock diameter")
        _positive(self.stock.length_mm, "stock length")
        _non_negative(self.stock.face_allowance_mm, "face allowance")
        _positive(self.stock.clearance_mm, "clearance")

        if self.turning.enabled:
            _positive(self.turning.target_diameter_mm, "target diameter")
            _positive(self.turning.target_length_mm, "target length")
            _positive(self.turning.step_over_mm, "turning stepover")
            _non_negative(self.turning.stock_to_leave_mm, "stock to leave")
            _positive(self.turning.rough_feed, "rough feed")
            _positive(self.turning.finish_feed, "finish feed")
            _positive(self.turning.spindle_rpm, "turning spindle RPM")
            _valid_tool(self.turning.tool_number, self.turning.station, "turning")
            if len(self.turning.tool_string) != 11:
                raise CamValidationError("turning tool string must be an 11 character ISO insert code")
            if self.turning.target_diameter_mm > self.stock.diameter_mm:
                raise CamValidationError("target diameter cannot exceed stock diameter")
            if self.turning.target_length_mm > self.stock.length_mm:
                raise CamValidationError("target length cannot exceed stock length")

        if self.taper.enabled:
            _positive(self.taper.start_diameter_mm, "taper start diameter")
            _positive(self.taper.end_diameter_mm, "taper end diameter")
            if self.taper.start_diameter_mm > self.stock.diameter_mm:
                raise CamValidationError("taper start diameter cannot exceed stock diameter")
            if self.taper.end_diameter_mm > self.stock.diameter_mm:
                raise CamValidationError("taper end diameter cannot exceed stock diameter")
            front = self.stock.z_front_mm
            back = self.stock.z_front_mm - self.turning.target_length_mm
            if not (back <= self.taper.end_z_mm <= self.taper.start_z_mm <= front):
                raise CamValidationError("taper Z range must sit inside the turned length")

        if self.hole.center_drill:
            _positive(self.hole.center_depth_mm, "center drill depth")
            _positive(self.hole.center_feed, "center drill feed")
            _valid_tool(self.hole.center_tool_number, self.hole.center_station, "center drill")
        if self.hole.drill:
            _positive(self.hole.drill_diameter_mm, "drill diameter")
            _positive(self.hole.drill_depth_mm, "drill depth")
            _positive(self.hole.drill_feed, "drill feed")
            _valid_tool(self.hole.drill_tool_number, self.hole.drill_station, "drill")
        if self.hole.bore:
            _positive(self.hole.bore_diameter_mm, "bore diameter")
            _positive(self.hole.bore_depth_mm, "bore depth")
            _positive(self.hole.boring_step_over_mm, "boring stepover")
            _positive(self.hole.boring_feed, "boring feed")
            _valid_tool(self.hole.boring_tool_number, self.hole.boring_station, "boring")
            if self.hole.bore_diameter_mm <= self.hole.drill_diameter_mm:
                raise CamValidationError("bore diameter must exceed drill diameter")
        max_hole_depth = max(
            self.hole.center_depth_mm if self.hole.center_drill else 0.0,
            self.hole.drill_depth_mm if self.hole.drill else 0.0,
            self.hole.bore_depth_mm if self.hole.bore else 0.0,
        )
        if max_hole_depth > self.stock.length_mm:
            raise CamValidationError("hole depth cannot exceed stock length")

        if self.thread.internal:
            raise CamValidationError("internal threading is not available yet")
        if self.thread.taper:
            raise CamValidationError("taper threading is not available yet")

        if self.thread.enabled:
            _positive(self.thread.major_diameter_mm, "thread major diameter")
            _positive(self.thread.pitch_mm, "thread pitch")
            _positive(self.thread.length_mm, "thread length")
            _positive(self.thread.depth_mm, "thread depth")
            _positive(self.thread.clearance_mm, "thread clearance")
            _positive(self.thread.spindle_rpm, "thread spindle RPM")
            _valid_tool(self.thread.tool_number, self.thread.station, "thread")
            if self.thread.passes <= 0:
                raise CamValidationError("thread passes must be positive")
            if self.thread.spring_passes < 0:
                raise CamValidationError("thread spring passes cannot be negative")
            if self.thread.major_diameter_mm > self.stock.diameter_mm:
                raise CamValidationError("thread major diameter cannot exceed stock diameter")
            if self.thread.depth_mm >= self.thread.major_diameter_mm:
                raise CamValidationError("thread depth must be smaller than major diameter")
            thread_end_z = self.thread.start_z_mm - self.thread.length_mm
            stock_back = self.stock.z_front_mm - self.stock.length_mm
            if not (stock_back <= thread_end_z <= self.thread.start_z_mm <= self.stock.z_front_mm):
                raise CamValidationError("thread Z range must sit inside the stock length")


def resolve_tool_stations(
    job: LatheCamJob,
    station_for_tool: Callable[[int], int | None],
) -> LatheCamJob:
    """Return a CAM job whose M06 station words follow the current tool setup."""
    return replace(
        job,
        turning=replace(
            job.turning,
            station=station_for_tool(job.turning.tool_number),
        ),
        hole=replace(
            job.hole,
            center_station=station_for_tool(job.hole.center_tool_number),
            drill_station=station_for_tool(job.hole.drill_tool_number),
            boring_station=station_for_tool(job.hole.boring_tool_number),
        ),
        thread=replace(
            job.thread,
            station=station_for_tool(job.thread.tool_number),
        ),
    )


def finished_profile_points(job: LatheCamJob) -> list[tuple[float, float]]:
    """Return closed finished profile points as display diameter X and Z."""
    front = job.stock.z_front_mm
    if job.turning.enabled or job.taper.enabled:
        back = front - job.turning.target_length_mm
        base_diameter = job.turning.target_diameter_mm
    else:
        back = front - job.stock.length_mm
        base_diameter = job.stock.diameter_mm
    surface: list[tuple[float, float]] = [(base_diameter, front)]

    if job.taper.enabled:
        if job.taper.start_z_mm > back:
            surface.append((job.taper.start_diameter_mm, job.taper.start_z_mm))
        surface.append((job.taper.end_diameter_mm, job.taper.end_z_mm))
        if job.taper.end_z_mm > back:
            surface.append((job.taper.end_diameter_mm, back))
    else:
        surface.append((base_diameter, back))

    points = [(0.0, front), *surface]
    last_diameter, last_z = points[-1]
    if last_z != back:
        points.append((last_diameter, back))
    points.append((0.0, back))
    return _dedupe_points(points)


def _dedupe_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in points:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
    return deduped


def _positive(value: float, label: str) -> None:
    if value <= 0:
        raise CamValidationError(f"{label} must be positive")


def _non_negative(value: float, label: str) -> None:
    if value < 0:
        raise CamValidationError(f"{label} cannot be negative")


def _valid_tool(tool_number: int, station: int | None, label: str) -> None:
    if tool_number < 0:
        raise CamValidationError(f"{label} tool number cannot be negative")
    if station is not None and station < 0:
        raise CamValidationError(f"{label} station cannot be negative")
