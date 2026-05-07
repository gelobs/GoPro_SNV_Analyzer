from __future__ import annotations

from typing import Optional

from backend.models import AccelerometerTelemetryWarning, GpsTelemetryWarning
from backend.telemetry_accelerometer import analyze_accelerometer_data
from backend.telemetry_gps import analyze_gps_data
from backend.telemetry_reader import extract_gpmd


def analyze_gps_telemetry(source_path: str) -> Optional[GpsTelemetryWarning]:
    telemetry = extract_gpmd(source_path)
    if telemetry is None:
        return None

    return analyze_gps_data(telemetry.data, telemetry.duration)


def analyze_accelerometer_telemetry(
    source_path: str,
) -> Optional[AccelerometerTelemetryWarning]:
    telemetry = extract_gpmd(source_path)
    if telemetry is None:
        return None

    return analyze_accelerometer_data(telemetry.data, telemetry.duration)
