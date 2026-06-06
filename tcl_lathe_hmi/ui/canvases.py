from __future__ import annotations

from collections.abc import Callable
import math

from kivy.graphics import Color, Line, Mesh, Rectangle
from kivy.uix.widget import Widget

from tcl_lathe_hmi.cam import (
    CamSolidError,
    CamValidationError,
    LatheCamJob,
    build_part_mesh,
)
from tcl_lathe_hmi.gcode import PreviewPath, PreviewSegment
from tcl_lathe_hmi.ui.controls import AMBER, GREEN, THREAD, THREAD_TOOLPATH


class PartIsoCanvas(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mesh = None
        self.job: LatheCamJob | None = None
        self.error_message = ""
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    def set_job(self, job: LatheCamJob) -> str:
        try:
            self.mesh = build_part_mesh(job)
            self.job = job
            self.error_message = ""
        except (CamSolidError, CamValidationError) as exc:
            self.mesh = None
            self.job = None
            self.error_message = str(exc)
        self._redraw()
        return self.error_message

    def _redraw(self) -> None:
        self.canvas.clear()
        with self.canvas:
            Color(0.045, 0.047, 0.05, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.24, 0.25, 0.27, 1)
            Line(rectangle=(self.x + 8, self.y + 8, max(0, self.width - 16), max(0, self.height - 16)), width=1)

            if self.mesh is None:
                return

            vertices = self.mesh.vertices
            faces = self.mesh.faces
            if len(vertices) == 0 or len(faces) == 0:
                return

            projected = [self._project(vertex) for vertex in vertices]
            min_x = min(point[0] for point in projected)
            max_x = max(point[0] for point in projected)
            min_y = min(point[1] for point in projected)
            max_y = max(point[1] for point in projected)
            if max_x - min_x <= 1e-9 or max_y - min_y <= 1e-9:
                return

            pad = 20
            draw_w = max(1.0, self.width - 2 * pad)
            draw_h = max(1.0, self.height - 2 * pad)
            scale = min(draw_w / (max_x - min_x), draw_h / (max_y - min_y))
            offset_x = self.x + pad + (draw_w - (max_x - min_x) * scale) / 2.0
            offset_y = self.y + pad + (draw_h - (max_y - min_y) * scale) / 2.0

            def screen_point(vertex_index: int) -> tuple[float, float]:
                px, py, _depth = projected[vertex_index]
                return (
                    offset_x + (px - min_x) * scale,
                    offset_y + (py - min_y) * scale,
                )

            face_items = []
            normals = self.mesh.face_normals
            for face_index, face in enumerate(faces):
                points = [screen_point(int(vertex_index)) for vertex_index in face]
                if _triangle_area(points) < 0.25:
                    continue
                depth = sum(projected[int(vertex_index)][2] for vertex_index in face) / 3.0
                normal = normals[face_index] if face_index < len(normals) else (0.0, 0.0, 1.0)
                shade = self._face_shade(normal)
                face_items.append((depth, points, shade))

            for _depth, points, shade in sorted(face_items, key=lambda item: item[0]):
                Color(0.50 * shade, 0.58 * shade, 0.64 * shade, 1)
                Mesh(
                    vertices=[
                        points[0][0],
                        points[0][1],
                        0,
                        0,
                        points[1][0],
                        points[1][1],
                        0,
                        0,
                        points[2][0],
                        points[2][1],
                        0,
                        0,
                    ],
                    indices=[0, 1, 2],
                    mode="triangles",
                )

            self._draw_reference_edges(projected, vertices)
            self._draw_thread_overlay(projected)

    @staticmethod
    def _project(vertex) -> tuple[float, float, float]:
        lathe_z = float(vertex[0])
        radial_y = float(vertex[1])
        radial_x = float(vertex[2])
        recede = -lathe_z
        projected_x = recede * 0.86 + radial_y * 0.36
        projected_y = radial_x * 0.94 + radial_y * 0.26 + recede * 0.14
        depth = lathe_z * 1.0 + radial_y * 0.42 + radial_x * 0.12
        return projected_x, projected_y, depth

    @staticmethod
    def _face_shade(normal) -> float:
        axial = float(normal[0])
        radial_y = float(normal[1])
        radial_x = float(normal[2])
        if axial > 0.55:
            return 0.95
        if axial < -0.55:
            return 0.50
        return max(0.62, min(0.84, 0.72 + radial_x * 0.10 + radial_y * 0.05))

    def _draw_reference_edges(self, projected, vertices) -> None:
        stations = self._ring_stations(vertices)
        if not stations:
            return

        front_z = max(stations)
        back_z = min(stations)
        front_outer = stations[front_z]
        front_inner = self._front_inner_radius()

        Color(0.78, 0.86, 0.91, 1)
        self._draw_ring(front_z, front_outer, width=2.6)
        if front_inner > 0.0:
            Color(0.035, 0.04, 0.045, 1)
            self._fill_ring_disc(front_z, front_inner)
            Color(0.86, 0.70, 0.38, 1)
            self._draw_ring(front_z, front_inner, width=2.2)

        Color(0.38, 0.45, 0.50, 0.78)
        self._draw_ring(back_z, stations[back_z], width=1.4)

        for angle, width in ((math.pi / 2.0, 1.9), (-math.pi / 2.0, 1.5)):
            points: list[float] = []
            for z_mm in sorted(stations, reverse=True):
                radius = stations[z_mm]
                px, py, _depth = self._project(
                    (
                        z_mm,
                        radius * math.cos(angle),
                        radius * math.sin(angle),
                    )
                )
                screen_x, screen_y = self._screen_from_projected(px, py, projected)
                points.extend([screen_x, screen_y])
            Color(0.70, 0.78, 0.83, 0.92)
            Line(points=points, width=width)

    def _draw_thread_overlay(self, projected) -> None:
        if self.job is None or not self.job.thread.external:
            return
        thread = self.job.thread
        if thread.pitch_mm <= 0.0 or thread.length_mm <= 0.0 or thread.major_diameter_mm <= 0.0:
            return

        major_radius = thread.major_diameter_mm / 2.0
        minor_radius = max(0.0, (thread.major_diameter_mm - thread.depth_mm) / 2.0)
        if major_radius <= 0.0 or minor_radius <= 0.0 or minor_radius >= major_radius:
            return

        turns = max(1.0, thread.length_mm / thread.pitch_mm)
        steps = max(24, min(720, math.ceil(turns * 28)))
        Color(*THREAD)
        self._draw_thread_helix(
            thread.start_z_mm,
            thread.length_mm,
            thread.pitch_mm,
            major_radius,
            steps,
            projected,
            phase=0.0,
            width=1.6,
        )
        Color(0.10, 0.38, 0.48, 0.78)
        self._draw_thread_helix(
            thread.start_z_mm - thread.pitch_mm / 2.0,
            max(0.0, thread.length_mm - thread.pitch_mm / 2.0),
            thread.pitch_mm,
            minor_radius,
            steps,
            projected,
            phase=math.pi,
            width=1.0,
        )

    def _draw_thread_helix(
        self,
        start_z: float,
        length: float,
        pitch: float,
        radius: float,
        steps: int,
        projected,
        *,
        phase: float,
        width: float,
    ) -> None:
        if length <= 0.0 or pitch <= 0.0:
            return
        points: list[float] = []
        for index in range(steps + 1):
            fraction = index / steps
            z_mm = start_z - length * fraction
            angle = phase + math.tau * (length * fraction / pitch)
            px, py, _depth = self._project(
                (
                    z_mm,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                )
            )
            screen_x, screen_y = self._screen_from_projected(px, py, projected)
            points.extend([screen_x, screen_y])
        if len(points) >= 4:
            Line(points=points, width=width)

    def _ring_stations(self, vertices) -> dict[float, float]:
        stations: dict[float, float] = {}
        for vertex in vertices:
            z_mm = round(float(vertex[0]), 6)
            radius = math.hypot(float(vertex[1]), float(vertex[2]))
            stations[z_mm] = max(stations.get(z_mm, 0.0), radius)
        return stations

    def _front_inner_radius(self) -> float:
        if self.job is None:
            return 0.0
        if self.job.hole.bore:
            return self.job.hole.bore_diameter_mm / 2.0
        if self.job.hole.drill:
            return self.job.hole.drill_diameter_mm / 2.0
        if self.job.hole.center_drill:
            return min(self.job.hole.drill_diameter_mm / 2.0, self.job.stock.diameter_mm / 8.0)
        return 0.0

    def _draw_ring(self, z_mm: float, radius: float, *, width: float) -> None:
        points: list[float] = []
        for index in range(65):
            angle = math.tau * index / 64
            px, py, _depth = self._project(
                (
                    z_mm,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                )
            )
            screen_x, screen_y = self._screen_from_projected(px, py)
            points.extend([screen_x, screen_y])
        Line(points=points, width=width)

    def _fill_ring_disc(self, z_mm: float, radius: float) -> None:
        center_x, center_y = self._screen_from_projected(*self._project((z_mm, 0.0, 0.0))[:2])
        vertices = [center_x, center_y, 0, 0]
        indices: list[int] = []
        for index in range(64):
            angle = math.tau * index / 64
            px, py, _depth = self._project(
                (
                    z_mm,
                    radius * math.cos(angle),
                    radius * math.sin(angle),
                )
            )
            screen_x, screen_y = self._screen_from_projected(px, py)
            vertices.extend([screen_x, screen_y, 0, 0])
            if index < 63:
                indices.extend([0, index + 1, index + 2])
            else:
                indices.extend([0, index + 1, 1])
        Mesh(vertices=vertices, indices=indices, mode="triangles")

    def _screen_from_projected(
        self,
        px: float,
        py: float,
        projected: list[tuple[float, float, float]] | None = None,
    ) -> tuple[float, float]:
        if projected is None:
            if self.mesh is None:
                return self.x, self.y
            projected = [self._project(vertex) for vertex in self.mesh.vertices]
        min_x = min(point[0] for point in projected)
        max_x = max(point[0] for point in projected)
        min_y = min(point[1] for point in projected)
        max_y = max(point[1] for point in projected)
        pad = 20
        draw_w = max(1.0, self.width - 2 * pad)
        draw_h = max(1.0, self.height - 2 * pad)
        scale = min(draw_w / max(1e-9, max_x - min_x), draw_h / max(1e-9, max_y - min_y))
        offset_x = self.x + pad + (draw_w - (max_x - min_x) * scale) / 2.0
        offset_y = self.y + pad + (draw_h - (max_y - min_y) * scale) / 2.0
        return (
            offset_x + (px - min_x) * scale,
            offset_y + (py - min_y) * scale,
        )


class PreviewCanvas(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.preview_path: PreviewPath | None = None
        self.part_outline: list[PreviewSegment] = []
        self.tool_x_mm: float | None = None
        self.tool_z_mm: float | None = None
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    def set_preview(
        self,
        preview_path: PreviewPath | None,
        *,
        part_outline: list[PreviewSegment] | None = None,
    ) -> None:
        self.preview_path = preview_path
        self.part_outline = part_outline or []
        self._redraw()

    def set_tool_position(self, *, x_mm: float, z_mm: float) -> None:
        self.tool_x_mm = x_mm
        self.tool_z_mm = z_mm
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.clear()
        with self.canvas:
            Color(0.05, 0.05, 0.05, 1)
            Rectangle(pos=self.pos, size=self.size)
            Color(0.24, 0.25, 0.27, 1)
            Line(rectangle=(self.x + 8, self.y + 8, max(0, self.width - 16), max(0, self.height - 16)), width=1)

            tool_x_mm = self.tool_x_mm
            tool_z_mm = self.tool_z_mm
            has_tool = tool_x_mm is not None and tool_z_mm is not None
            if (
                (self.preview_path is None or not self.preview_path.segments)
                and not self.part_outline
                and not has_tool
            ):
                return

            all_segments = []
            if self.preview_path is not None:
                all_segments.extend(self.preview_path.segments)
            all_segments.extend(self.part_outline)
            xs = [value for segment in all_segments for value in (segment.start_x_mm, segment.end_x_mm)]
            zs = [value for segment in all_segments for value in (segment.start_z_mm, segment.end_z_mm)]
            if has_tool:
                assert tool_x_mm is not None
                assert tool_z_mm is not None
                xs.append(tool_x_mm)
                zs.append(tool_z_mm)
            min_z, max_z = min(zs), max(zs)
            min_x, max_x = min(xs), max(xs)
            if min_z == max_z:
                min_z -= 1.0
                max_z += 1.0
            if min_x == max_x:
                min_x -= 1.0
                max_x += 1.0

            pad = 24
            draw_w = max(1.0, self.width - 2 * pad)
            draw_h = max(1.0, self.height - 2 * pad)

            def map_point(x_mm: float, z_mm: float) -> tuple[float, float]:
                sx = self.x + pad + ((z_mm - min_z) / (max_z - min_z)) * draw_w
                sy = self.y + pad + ((x_mm - min_x) / (max_x - min_x)) * draw_h
                return sx, sy

            Color(0.23, 0.24, 0.25, 1)
            zero_z_x, _ = map_point(0.0, 0.0)
            _, zero_x_y = map_point(0.0, 0.0)
            if self.x + pad <= zero_z_x <= self.x + self.width - pad:
                Line(points=[zero_z_x, self.y + pad, zero_z_x, self.y + self.height - pad], width=1)
            if self.y + pad <= zero_x_y <= self.y + self.height - pad:
                Line(points=[self.x + pad, zero_x_y, self.x + self.width - pad, zero_x_y], width=1)

            for segment in self.part_outline:
                if segment.mode == "thread":
                    Color(*THREAD)
                    width = 1.8
                elif segment.mode == "hole":
                    Color(0.65, 0.48, 0.22, 1)
                    width = 2.4
                else:
                    Color(0.42, 0.55, 0.66, 1)
                    width = 2.4
                x0, y0 = map_point(segment.start_x_mm, segment.start_z_mm)
                x1, y1 = map_point(segment.end_x_mm, segment.end_z_mm)
                Line(points=[x0, y0, x1, y1], width=width)

            if self.preview_path is None:
                self._draw_tool_marker(map_point)
                return

            for segment in self.preview_path.segments:
                if segment.mode == "thread":
                    Color(*THREAD_TOOLPATH)
                    width = 0.9
                elif segment.mode == "rapid":
                    Color(*AMBER)
                    width = 0.8
                else:
                    Color(*GREEN)
                    width = 1.1
                x0, y0 = map_point(segment.start_x_mm, segment.start_z_mm)
                x1, y1 = map_point(segment.end_x_mm, segment.end_z_mm)
                Line(points=[x0, y0, x1, y1], width=width)

            self._draw_tool_marker(map_point)

    def _draw_tool_marker(self, map_point: Callable[[float, float], tuple[float, float]]) -> None:
        if self.tool_x_mm is None or self.tool_z_mm is None:
            return
        tool_x, tool_y = map_point(self.tool_x_mm, self.tool_z_mm)
        size = max(8.0, min(16.0, min(self.width, self.height) * 0.035))
        Color(0.96, 0.88, 0.22, 1)
        base_y = tool_y - size * 1.72
        Mesh(
            vertices=[
                tool_x,
                tool_y,
                0,
                0,
                tool_x - size * 0.86,
                base_y,
                0,
                0,
                tool_x + size * 0.86,
                base_y,
                0,
                0,
            ],
            indices=[0, 1, 2],
            mode="triangles",
        )
        Color(0.08, 0.08, 0.06, 1)
        Line(
            points=[
                tool_x,
                tool_y,
                tool_x - size * 0.86,
                base_y,
                tool_x + size * 0.86,
                base_y,
                tool_x,
                tool_y,
            ],
            width=1.4,
        )


def _triangle_area(points: list[tuple[float, float]]) -> float:
    return abs(
        (
            points[0][0] * (points[1][1] - points[2][1])
            + points[1][0] * (points[2][1] - points[0][1])
            + points[2][0] * (points[0][1] - points[1][1])
        )
        / 2.0
    )
