from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class GpmdTelemetry:
    data: bytes
    duration: Optional[float]


GpmfHandler = Callable[[str, str, int, int, bytes, List[int], str], None]


def extract_gpmd(source_path: str) -> Optional[GpmdTelemetry]:
    source = Path(source_path)
    ffmpeg = _which("ffmpeg")
    ffprobe = _which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None

    probe = _probe(ffprobe, source)
    stream = _gpmd_stream(probe)
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
        return GpmdTelemetry(data=temp.read_bytes(), duration=_duration(probe, stream))
    finally:
        try:
            temp.unlink(missing_ok=True)
        except PermissionError:
            pass


def walk_gpmf(data: bytes, handler: GpmfHandler) -> None:
    def walk(block: bytes, scale: Optional[List[int]] = None, type_format: str = "") -> None:
        current_scale = list(scale or [])
        current_type_format = type_format
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
                walk(payload, current_scale, current_type_format)
            elif key == "SCAL":
                current_scale = numbers(typ, size, repeat, payload)
            elif key == "TYPE":
                current_type_format = payload.decode("ascii", errors="ignore").strip("\x00")
            else:
                handler(key, typ, size, repeat, payload, current_scale, current_type_format)

    walk(data)


def sample_time(sample_index: int, point_count: int, duration: Optional[float]) -> Optional[float]:
    if duration is None or point_count <= 1:
        return None

    return duration * sample_index / (point_count - 1)


def format_time(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def type_size(typ: str) -> int:
    return {"b": 1, "B": 1, "s": 2, "S": 2, "l": 4, "L": 4}.get(typ, 0)


def scale_value(scale: List[int], index: int) -> int:
    if len(scale) == 1:
        return scale[0] or 1
    if len(scale) > index:
        return scale[index] or 1

    return 1


def typed_numbers(fmt: str, sample_size: int, repeat: int, payload: bytes) -> List[int]:
    values = []
    for i in range(repeat):
        sample = payload[i * sample_size : (i + 1) * sample_size]
        pos = 0
        for typ in fmt:
            size = type_size(typ)
            if not size or pos + size > len(sample):
                break
            values.extend(numbers(typ, size, 1, sample[pos : pos + size]))
            pos += size
    return values


def numbers(typ: str, size: int, repeat: int, payload: bytes) -> List[int]:
    if size <= 0:
        return []

    unsigned = typ in {"B", "S", "L"}
    return [
        int.from_bytes(payload[i * size : (i + 1) * size], "big", signed=not unsigned)
        for i in range(repeat)
        if len(payload[i * size : (i + 1) * size]) == size
    ]


def _which(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found

    candidate = Path(f"C:/ffmpeg/bin/{name}.exe")
    return str(candidate) if candidate.exists() else None


def _probe(ffprobe: str, source: Path) -> Dict[str, Any]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(source),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return {}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def _gpmd_stream(probe: Dict[str, Any]) -> Optional[int]:
    for stream in probe.get("streams", []):
        tag = str(stream.get("codec_tag_string", "")).lower()
        handler = stream.get("tags", {}).get("handler_name")
        if tag == "gpmd" or handler == "GoPro MET":
            return int(stream["index"])

    return None


def _duration(probe: Dict[str, Any], stream_index: int) -> Optional[float]:
    stream_duration = None
    for stream in probe.get("streams", []):
        if int(stream.get("index", -1)) == stream_index:
            stream_duration = stream.get("duration")
            break

    for value in (stream_duration, probe.get("format", {}).get("duration")):
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration

    return None
