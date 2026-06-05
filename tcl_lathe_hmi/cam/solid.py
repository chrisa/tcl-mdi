from __future__ import annotations

from dataclasses import dataclass

from .models import LatheCamJob, finished_profile_points


class CamSolidError(RuntimeError):
    pass


@dataclass(frozen=True)
class PartMesh:
    vertices: object
    faces: object
    bounds: object


def build_part_mesh(job: LatheCamJob, *, sections: int = 56):
    """Build a mesh of the finished part using display-diameter CAM inputs."""
    if sections < 12:
        raise CamSolidError("3D preview needs at least 12 radial sections")
    job.validate()
    try:
        import numpy as np
        import trimesh
    except Exception as exc:
        raise CamSolidError("3D preview requires trimesh; run `python -m pip install trimesh`") from exc

    stations = _mesh_stations(job)
    angles = np.linspace(0.0, 2.0 * np.pi, sections, endpoint=False)
    cosines = np.cos(angles)
    sines = np.sin(angles)
    vertices: list[tuple[float, float, float]] = []

    for station in stations:
        for radius in (station.outer_radius_mm, station.inner_radius_mm):
            for cos_value, sin_value in zip(cosines, sines):
                vertices.append(
                    (
                        station.z_mm,
                        radius * cos_value,
                        radius * sin_value,
                    )
                )

    faces: list[tuple[int, int, int]] = []
    ring_stride = sections * 2

    def ring_index(station_index: int, inner: bool, section_index: int) -> int:
        return station_index * ring_stride + (sections if inner else 0) + section_index % sections

    for station_index in range(len(stations) - 1):
        for section_index in range(sections):
            next_section = (section_index + 1) % sections
            outer_a = ring_index(station_index, False, section_index)
            outer_b = ring_index(station_index, False, next_section)
            outer_c = ring_index(station_index + 1, False, next_section)
            outer_d = ring_index(station_index + 1, False, section_index)
            faces.extend([(outer_a, outer_b, outer_c), (outer_a, outer_c, outer_d)])

            inner_a = ring_index(station_index, True, section_index)
            inner_b = ring_index(station_index, True, next_section)
            inner_c = ring_index(station_index + 1, True, next_section)
            inner_d = ring_index(station_index + 1, True, section_index)
            if stations[station_index].inner_radius_mm > 0.0 or stations[station_index + 1].inner_radius_mm > 0.0:
                faces.extend([(inner_a, inner_c, inner_b), (inner_a, inner_d, inner_c)])

    _add_annular_cap(faces, ring_index, 0, sections, front=True)
    _add_annular_cap(faces, ring_index, len(stations) - 1, sections, front=False)

    mesh = trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=float),
        faces=np.asarray(faces, dtype=int),
        process=False,
    )
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    mesh.visual.face_colors = [166, 181, 194, 255]
    return mesh


@dataclass(frozen=True)
class _Station:
    z_mm: float
    outer_radius_mm: float
    inner_radius_mm: float


def _mesh_stations(job: LatheCamJob) -> list[_Station]:
    outer_profile = _outer_profile(job)
    hole_breaks = _hole_breakpoints(job)
    stations: list[_Station] = []

    for index, point in enumerate(outer_profile):
        z_mm, outer_radius = point
        stations.append(_Station(z_mm, outer_radius, _inner_radius(job, z_mm)))
        if index + 1 >= len(outer_profile):
            continue

        next_z, next_radius = outer_profile[index + 1]
        if abs(z_mm - next_z) < 1e-9:
            continue
        for hole_z in sorted(hole_breaks, reverse=True):
            if next_z < hole_z < z_mm:
                fraction = (z_mm - hole_z) / (z_mm - next_z)
                inserted_radius = outer_radius + (next_radius - outer_radius) * fraction
                stations.append(_Station(hole_z, inserted_radius, _inner_radius(job, hole_z)))
                bottom_inner = _inner_radius_below(job, hole_z)
                if abs(bottom_inner - _inner_radius(job, hole_z)) > 1e-9:
                    stations.append(_Station(hole_z, inserted_radius, bottom_inner))

    return _dedupe_stations(stations)


def _outer_profile(job: LatheCamJob) -> list[tuple[float, float]]:
    front_z = job.stock.z_front_mm
    stock_back_z = front_z - job.stock.length_mm
    stock_radius = job.stock.diameter_mm / 2.0
    surface = [
        (z_mm, diameter_mm / 2.0)
        for diameter_mm, z_mm in finished_profile_points(job)
        if diameter_mm > 0.0
    ]
    if not surface:
        return [(front_z, stock_radius), (stock_back_z, stock_radius)]

    profile: list[tuple[float, float]] = []
    if surface[0][0] != front_z:
        profile.append((front_z, stock_radius))
    profile.extend(surface)

    last_z, last_radius = profile[-1]
    if last_z > stock_back_z:
        if abs(last_radius - stock_radius) > 1e-9:
            profile.append((last_z, stock_radius))
        profile.append((stock_back_z, stock_radius))
    return profile


def _hole_breakpoints(job: LatheCamJob) -> set[float]:
    front_z = job.stock.z_front_mm
    values = {front_z}
    if job.hole.center_drill:
        values.add(front_z - job.hole.center_depth_mm)
    if job.hole.drill:
        values.add(front_z - job.hole.drill_depth_mm)
    if job.hole.bore:
        values.add(front_z - job.hole.bore_depth_mm)
    return values


def _inner_radius(job: LatheCamJob, z_mm: float) -> float:
    front_z = job.stock.z_front_mm
    depth = front_z - z_mm
    if depth < -1e-9:
        return 0.0
    if job.hole.bore and depth <= job.hole.bore_depth_mm + 1e-9:
        return job.hole.bore_diameter_mm / 2.0
    if job.hole.drill and depth <= job.hole.drill_depth_mm + 1e-9:
        return job.hole.drill_diameter_mm / 2.0
    if job.hole.center_drill and depth <= job.hole.center_depth_mm + 1e-9:
        center_radius = min(job.hole.drill_diameter_mm / 2.0, job.stock.diameter_mm / 8.0)
        return max(0.0, center_radius * (1.0 - depth / job.hole.center_depth_mm))
    return 0.0


def _inner_radius_below(job: LatheCamJob, z_mm: float) -> float:
    return _inner_radius(job, z_mm - 1e-6)


def _dedupe_stations(stations: list[_Station]) -> list[_Station]:
    deduped: list[_Station] = []
    for station in stations:
        if (
            deduped
            and abs(deduped[-1].z_mm - station.z_mm) < 1e-9
            and abs(deduped[-1].outer_radius_mm - station.outer_radius_mm) < 1e-9
            and abs(deduped[-1].inner_radius_mm - station.inner_radius_mm) < 1e-9
        ):
            continue
        deduped.append(station)
    return deduped


def _add_annular_cap(
    faces: list[tuple[int, int, int]],
    ring_index,
    station_index: int,
    sections: int,
    *,
    front: bool,
) -> None:
    for section_index in range(sections):
        next_section = (section_index + 1) % sections
        outer_a = ring_index(station_index, False, section_index)
        outer_b = ring_index(station_index, False, next_section)
        inner_a = ring_index(station_index, True, section_index)
        inner_b = ring_index(station_index, True, next_section)
        if front:
            faces.extend([(outer_a, inner_b, outer_b), (outer_a, inner_a, inner_b)])
        else:
            faces.extend([(outer_a, outer_b, inner_b), (outer_a, inner_b, inner_a)])
