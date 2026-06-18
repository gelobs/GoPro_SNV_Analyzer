from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union


def check_disk_space(target_path: Path, estimated_size: int) -> Tuple[bool, str]:
    try:
        disk_usage = shutil.disk_usage(str(target_path.parent))
        available_space = disk_usage.free

        if available_space < estimated_size:
            available_gb = available_space / (1024 ** 3)
            required_gb = estimated_size / (1024 ** 3)
            return False, (
                f"Espaco em disco insuficiente. "
                f"Disponivel: {available_gb:.2f} GB, "
                f"Necessario: {required_gb:.2f} GB."
            )
        return True, ""
    except Exception as exc:
        return False, f"Erro ao verificar espaco em disco: {str(exc)}"


def resolve_ffmpeg_path() -> Optional[str]:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    common_paths = [
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe"),
    ]

    for candidate in common_paths:
        if candidate.exists():
            return str(candidate)

    return None


def validar_tempo(time_value: str) -> bool:
    match = re.fullmatch(r"\d+:\d{2}", time_value)
    if not match:
        return False

    _, seconds = time_value.split(":")
    return int(seconds) < 60


def time_to_seconds(time_value: str) -> int:
    minutes, seconds = time_value.split(":")
    return int(minutes) * 60 + int(seconds)


def seconds_to_mmss(total_seconds: float) -> str:
    total_seconds_int = max(0, int(total_seconds))
    minutes = total_seconds_int // 60
    seconds = total_seconds_int % 60
    return f"{minutes:02d}:{seconds:02d}"


def inspect_input(ffmpeg_path: str, source: Path) -> str:
    completed = subprocess.run(
        [ffmpeg_path, "-i", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stderr + completed.stdout


def get_video_duration(output: str) -> Optional[float]:
    match = re.search(
        r"Duration: (?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}(?:\.\d+)?)",
        output,
    )
    if not match:
        return None

    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = float(match.group("s"))
    return hours * 3600 + minutes * 60 + seconds


def probe_streams(output: str) -> List[Dict[str, Union[str, int]]]:
    streams: List[Dict[str, Union[str, int]]] = []
    current_stream: Optional[Dict[str, Union[str, int]]] = None
    stream_pattern = re.compile(
        r"Stream #\d+:(?P<index>\d+).*?: (?P<codec_type>\w+): (?P<codec_name>[^,(]+)"
    )

    for line in output.splitlines():
        stream_match = stream_pattern.search(line)
        if stream_match:
            current_stream = {
                "index": int(stream_match.group("index")),
                "codec_type": stream_match.group("codec_type").lower(),
                "codec_name": stream_match.group("codec_name").strip().lower(),
                "raw_line": line,
                "handler_name": "",
            }
            streams.append(current_stream)
            continue

        if current_stream and "handler_name" in line:
            _, _, handler_name = line.partition(":")
            current_stream["handler_name"] = handler_name.strip()

    return streams


def build_map_args(streams: List[Dict[str, Union[str, int]]]) -> List[str]:
    map_args: List[str] = []

    for stream in streams:
        codec_type = str(stream["codec_type"])
        codec_name = str(stream["codec_name"])
        handler_name = str(stream["handler_name"])
        raw_line = str(stream["raw_line"]).lower()
        index = int(stream["index"])

        keep_stream = codec_type in {"video", "audio"}
        keep_stream = keep_stream or "(gpmd /" in raw_line
        keep_stream = keep_stream or handler_name == "GoPro MET"
        keep_stream = keep_stream or codec_name == "bin_data"

        if keep_stream:
            map_args.extend(["-map", f"0:{index}"])

    return map_args


def duration_error(field_name: str, field_value: str, duration_seconds: float) -> str:
    return (
        f"O tempo {field_name} informado ({field_value}) e invalido para este video. "
        f"A duracao do video e {seconds_to_mmss(duration_seconds)}."
    )


def run_command(command: List[str]) -> Tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)

    output = completed.stderr.strip() or completed.stdout.strip()
    if completed.returncode != 0:
        return False, output or "FFmpeg retornou erro sem detalhes."

    return True, output


def _parse_ffmpeg_time_to_seconds(value: str) -> Optional[float]:
    match = re.fullmatch(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2}(?:\.\d+)?)", value)
    if not match:
        return None

    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + float(match.group("s"))
    )


def cut_segment(
    ffmpeg_path: str,
    source: Path,
    target: Path,
    map_args: List[str],
    start_seconds: float,
    duration_seconds: float,
    log: Optional[Callable[[str], None]] = None,
    progress_start: int = 70,
    progress_end: int = 95,
) -> Tuple[bool, str]:
    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        str(source),
        "-t",
        str(duration_seconds),
        "-nostats",
        *map_args,
        "-map_metadata",
        "0",
        "-c",
        "copy",
        "-c:v",
        "libx264",
        "-vf",
        "scale=1920:1080",
        "-r",
        "60000/1001",
        "-crf",
        "10",
        "-copy_unknown",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "use_metadata_tags",
        "-progress",
        "pipe:1",
        str(target),
    ]

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception as exc:
        return False, str(exc)

    output_lines: List[str] = []
    last_progress = -1

    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        output_lines.append(line)

        if not line.startswith("out_time="):
            continue

        elapsed = _parse_ffmpeg_time_to_seconds(line.partition("=")[2])
        if elapsed is None or duration_seconds <= 0:
            continue

        ratio = min(max(elapsed / duration_seconds, 0), 1)
        progress = int(progress_start + ratio * (progress_end - progress_start))
        if log and progress > last_progress:
            last_progress = progress
            log(f"Progresso FFmpeg: {progress}%")

    return_code = process.wait()
    output = "\n".join(output_lines).strip()
    if return_code != 0:
        return False, output or "FFmpeg retornou erro sem detalhes."

    return True, output
