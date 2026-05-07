from __future__ import annotations

from typing import List, Optional, Tuple

from pyproj import Geod

from backend.models import GpsTelemetryWarning
from backend.telemetry_reader import (
    format_time,
    sample_time,
    scale_value,
    type_size,
    typed_numbers,
    numbers,
    walk_gpmf,
)


MAX_DISTANCE_M = 1.0
MIN_TURN_DEGREES = 20.0
MIN_STEP_M = 0.05
MIN_FAILURE_SPACING_SECONDS = 1.0


def analyze_gps_data(data: bytes, duration: Optional[float]) -> Optional[GpsTelemetryWarning]:
    return _gps_warning(_gps_points(data), duration)


def _gps_points(data: bytes) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []

    def collect(
        key: str,
        typ: str,
        size: int,
        repeat: int,
        payload: bytes,
        scale: List[int],
        type_format: str,
    ) -> None:
        if key in {"GPS5", "GPS9"}:
            points.extend(_read_gps(key, typ, size, repeat, payload, scale, type_format))

    walk_gpmf(data, collect)
    return points


def _read_gps(
    key: str,
    typ: str,
    size: int,
    repeat: int,
    payload: bytes,
    scale: List[int],
    type_format: str,
) -> List[Tuple[float, float]]:
    fields = 9 if key == "GPS9" else 5
    if typ == "?":
        values = typed_numbers(type_format, size, repeat, payload)
    else:
        values = numbers(typ, type_size(typ), repeat * fields, payload)

    points = []
    for i in range(0, len(values), fields):
        sample = values[i : i + fields]
        if len(sample) < 2:
            continue

        lat = sample[0] / scale_value(scale, 0)
        lon = sample[1] / scale_value(scale, 1)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            points.append((lat, lon))
    return points


def _gps_warning(
    points: List[Tuple[float, float]],
    duration: Optional[float],
) -> Optional[GpsTelemetryWarning]:
    geod = Geod(ellps="WGS84")
    segments = []

    for index, (first, second) in enumerate(zip(points, points[1:])):
        azimuth, _, distance = geod.inv(first[1], first[0], second[1], second[0])
        if distance >= MIN_STEP_M:
            segments.append((azimuth % 360, distance, index))

    failure_times = []
    failure_samples = []
    for previous, current in zip(segments, segments[1:]):
        delta = abs((current[0] - previous[0] + 180) % 360 - 180)
        distance = previous[1] + current[1]
        if distance <= MAX_DISTANCE_M and delta + 0.001 >= MIN_TURN_DEGREES:
            sample_index = previous[2] + 1
            seconds = sample_time(sample_index, len(points), duration)
            if seconds is None:
                failure_samples.append(f"amostra GPS {sample_index + 1}")
                continue
            if failure_times and seconds - failure_times[-1] < MIN_FAILURE_SPACING_SECONDS:
                continue
            failure_times.append(seconds)

    if not failure_times and not failure_samples:
        return None

    if failure_times:
        locations = [format_time(seconds) for seconds in failure_times]
    else:
        locations = failure_samples

    if len(locations) == 1:
        details = "Possivel falha no GPS detectada em:\n- {}".format(locations[0])
    else:
        details = "Possiveis falhas no GPS detectadas em:\n{}".format(
            "\n".join(f"- {location}" for location in locations),
        )

    return GpsTelemetryWarning(
        message="Possivel falha no GPS detectada.",
        details=details,
        failure_times=locations,
    )
