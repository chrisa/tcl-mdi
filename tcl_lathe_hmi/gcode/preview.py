from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .actions import CanonicalAction, MoveAction, ThreadSyncAction


@dataclass(frozen=True)
class PreviewSegment:
    start_x_mm: float
    start_z_mm: float
    end_x_mm: float
    end_z_mm: float
    mode: str
    line_number: int


@dataclass(frozen=True)
class PreviewPath:
    segments: list[PreviewSegment]
    min_x_mm: float
    max_x_mm: float
    min_z_mm: float
    max_z_mm: float


def build_preview(
    actions: Sequence[CanonicalAction],
    *,
    start_x_mm: float = 0.0,
    start_z_mm: float = 0.0,
) -> PreviewPath:
    x = start_x_mm
    z = start_z_mm
    segments: list[PreviewSegment] = []
    xs = [x]
    zs = [z]

    for action in actions:
        if isinstance(action, ThreadSyncAction):
            segment = PreviewSegment(
                start_x_mm=x,
                start_z_mm=z,
                end_x_mm=x,
                end_z_mm=action.target_z_mm,
                mode="thread",
                line_number=action.line_number,
            )
            segments.append(segment)
            z = action.target_z_mm
            xs.extend([segment.start_x_mm, segment.end_x_mm])
            zs.extend([segment.start_z_mm, segment.end_z_mm])
            continue
        if not isinstance(action, MoveAction):
            continue
        segment = PreviewSegment(
            start_x_mm=x,
            start_z_mm=z,
            end_x_mm=action.target_x_mm,
            end_z_mm=action.target_z_mm,
            mode=action.mode,
            line_number=action.line_number,
        )
        segments.append(segment)
        x = action.target_x_mm
        z = action.target_z_mm
        xs.extend([segment.start_x_mm, segment.end_x_mm])
        zs.extend([segment.start_z_mm, segment.end_z_mm])

    return PreviewPath(
        segments=segments,
        min_x_mm=min(xs),
        max_x_mm=max(xs),
        min_z_mm=min(zs),
        max_z_mm=max(zs),
    )
