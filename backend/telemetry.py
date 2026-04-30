from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from pyproj import Geod

from backend.models import GpsTelemetryWarning


MAX_DISTANCE_M = 1.0
MIN_TURN_DEGREES = 20.0
MIN_STEP_M = 0.05


def analyze_gps_telemetry(source_path: str) -> Optional[GpsTelemetryWarning]:
    source = Path(source_path)
    ffmpeg = _which("ffmpeg")
    ffprobe = _which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None

    stream = _gpmd_stream(ffprobe, source)
    if stream is None:
        return None

    temp = source.parent / f"{source.stem}_{uuid.uuid4().hex}_gps.gpmd"
    try:
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-map",
            f"0:{stream}",
            "-c",
            "copy",
            "-f",
            "rawvideo",
            str(temp),
        ]
        if subprocess.run(command, capture_output=True, text=True).returncode != 0:
            return None
        return _gps_warning(_gps_points(temp.read_bytes()))
    finally:
        try:
            temp.unlink(missing_ok=True)
        except PermissionError:
            pass


def _which(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found

    candidate = Path(f"C:/ffmpeg/bin/{name}.exe")
    return str(candidate) if candidate.exists() else None


def _gpmd_stream(ffprobe: str, source: Path) -> Optional[int]:
    command = [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(source)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return None

    for stream in json.loads(result.stdout).get("streams", []):
        tag = str(stream.get("codec_tag_string", "")).lower()
        handler = stream.get("tags", {}).get("handler_name")
        if tag == "gpmd" or handler == "GoPro MET":
            return int(stream["index"])

    return None


def _gps_points(data: bytes) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []

    def walk(block: bytes, scale: List[int] = [], type_format: str = "") -> None:
        pos = 0
        while pos + 8 <= len(block):
            key = block[pos : pos + 4].decode("ascii", errors="ignore")
            typ = chr(block[pos + 4])
            size = block[pos + 5]
            repeat = int.from_bytes(block[pos + 6 : pos + 8], "big")
            end = pos + 8 + size * repeat
            payload = block[pos + 8 : end]
            pos = end + ((4 - (size * repeat) % 4) % 4)

            if end > len(block) or not key.strip("\x00"):
                break
            if typ == "\x00":
                walk(payload, scale, type_format)
            elif key == "SCAL":
                scale = _numbers(typ, size, repeat, payload)
            elif key == "TYPE":
                type_format = payload.decode("ascii", errors="ignore").strip("\x00")
            elif key in {"GPS5", "GPS9"}:
                points.extend(_read_gps(key, typ, size, repeat, payload, scale, type_format))

    walk(data)
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
        values = _typed_numbers(type_format, size, repeat, payload)
    else:
        values = _numbers(typ, 4, repeat * fields, payload)

    points = []
    for i in range(0, len(values), fields):
        sample = values[i : i + fields]
        if len(sample) < 2:
            continue

        lat = sample[0] / (scale[0] if len(scale) > 0 and scale[0] else 1)
        lon = sample[1] / (scale[1] if len(scale) > 1 and scale[1] else 1)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            points.append((lat, lon))
    return points


def _typed_numbers(fmt: str, sample_size: int, repeat: int, payload: bytes) -> List[int]:
    sizes = {"b": 1, "B": 1, "s": 2, "S": 2, "l": 4, "L": 4}
    values = []
    for i in range(repeat):
        sample = payload[i * sample_size : (i + 1) * sample_size]
        pos = 0
        for typ in fmt:
            size = sizes.get(typ, 0)
            if not size or pos + size > len(sample):
                break
            values.extend(_numbers(typ, size, 1, sample[pos : pos + size]))
            pos += size
    return values


def _numbers(typ: str, size: int, repeat: int, payload: bytes) -> List[int]:
    unsigned = typ in {"B", "S", "L"}
    return [
        int.from_bytes(payload[i * size : (i + 1) * size], "big", signed=not unsigned)
        for i in range(repeat)
        if len(payload[i * size : (i + 1) * size]) == size
    ]


def _gps_warning(points: List[Tuple[float, float]]) -> Optional[GpsTelemetryWarning]:
    geod = Geod(ellps="WGS84")
    segments = []

    for first, second in zip(points, points[1:]):
        azimuth, _, distance = geod.inv(first[1], first[0], second[1], second[0])
        if distance >= MIN_STEP_M:
            segments.append((azimuth % 360, distance))

    for previous, current in zip(segments, segments[1:]):
        delta = abs((current[0] - previous[0] + 180) % 360 - 180)
        distance = previous[1] + current[1]
        if distance <= MAX_DISTANCE_M and delta + 0.001 >= MIN_TURN_DEGREES:
            return GpsTelemetryWarning(
                message="Possivel falha no GPS detectada.",
                details="Foi detectada uma falha no GPS.",
            )

    return None
