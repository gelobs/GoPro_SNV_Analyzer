from __future__ import annotations

import math
from typing import List, Optional, Tuple

from backend.models import AccelerometerTelemetryWarning
from backend.telemetry_reader import (
    format_time,
    sample_time,
    scale_value,
    type_size,
    typed_numbers,
    numbers,
    walk_gpmf,
)


MIN_FAILURE_SPACING_SECONDS = 1.0
MIN_ACCELERATION_MPS = 2.0
IGNORE_LAST_ACCELEROMETER_FAILURE = True


def analyze_accelerometer_data(
    data: bytes,
    duration: Optional[float],
) -> Optional[AccelerometerTelemetryWarning]:
    return _accelerometer_warning(_accelerometer_samples(data), duration)


def _accelerometer_samples(data: bytes) -> List[Tuple[float, float, float]]:
    samples: List[Tuple[float, float, float]] = []

    def collect(
        key: str,
        typ: str,
        size: int,
        repeat: int,
        payload: bytes,
        scale: List[int],
        type_format: str,
    ) -> None:
        if key == "ACCL":
            samples.extend(_read_accelerometer(typ, size, repeat, payload, scale, type_format))

    walk_gpmf(data, collect)
    return samples


def _read_accelerometer(
    typ: str,
    size: int,
    repeat: int,
    payload: bytes,
    scale: List[int],
    type_format: str,
) -> List[Tuple[float, float, float]]:
    fields = 3
    if typ == "?":
        values = typed_numbers(type_format, size, repeat, payload)
    else:
        values = numbers(typ, type_size(typ), repeat * fields, payload)

    samples = []
    for i in range(0, len(values), fields):
        sample = values[i : i + fields]
        if len(sample) < fields:
            continue

        x = sample[0] / scale_value(scale, 0)
        y = sample[1] / scale_value(scale, 1)
        z = sample[2] / scale_value(scale, 2)
        samples.append((x, y, z))

    return samples


def _accelerometer_warning(
    samples: List[Tuple[float, float, float]],
    duration: Optional[float],
) -> Optional[AccelerometerTelemetryWarning]:
    failure_times = []
    failure_samples = []

    for index, sample in enumerate(samples):
        value = math.sqrt(sample[0] ** 2 + sample[1] ** 2 + sample[2] ** 2)
        if value >= MIN_ACCELERATION_MPS:
            continue

        seconds = sample_time(index, len(samples), duration)
        if seconds is None:
            failure_samples.append(f"amostra do acelerometro {index + 1}")
            continue
        if failure_times and seconds - failure_times[-1] < MIN_FAILURE_SPACING_SECONDS:
            continue
        failure_times.append(seconds)

    if not failure_times and not failure_samples:
        return None

    if failure_times:
        if IGNORE_LAST_ACCELEROMETER_FAILURE:
            failure_times = failure_times[:-1]
        locations = [format_time(seconds) for seconds in failure_times]
    else:
        if IGNORE_LAST_ACCELEROMETER_FAILURE:
            failure_samples = failure_samples[:-1]
        locations = failure_samples

    if not locations:
        return None

    if len(locations) == 1:
        details = "Erro no acelerometro: valor menor que 2 m/s detectado em:\n- {}".format(
            locations[0]
        )
    else:
        details = "Erros no acelerometro: valores menores que 2 m/s detectados em:\n{}".format(
            "\n".join(f"- {location}" for location in locations),
        )

    return AccelerometerTelemetryWarning(
        message="Erro no acelerometro detectado.",
        details=details,
        failure_times=locations,
    )
