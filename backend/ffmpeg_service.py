from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union


def _check_disk_space(target_path: Path, estimated_size: int) -> Tuple[bool, str]:
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


def _resolve_ffmpeg_path() -> Optional[str]:
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
    match = re.fullmatch(r"\d{2}:\d{2}", time_value)
    if not match:
        return False

    _, seconds = time_value.split(":")
    return int(seconds) < 60


def _time_to_seconds(time_value: str) -> int:
    minutes, seconds = time_value.split(":")
    return int(minutes) * 60 + int(seconds)


def _seconds_to_mmss(total_seconds: float) -> str:
    total_seconds_int = max(0, int(total_seconds))
    minutes = total_seconds_int // 60
    seconds = total_seconds_int % 60
    return f"{minutes:02d}:{seconds:02d}"


def _inspect_input(ffmpeg_path: str, source: Path) -> str:
    completed = subprocess.run(
        [ffmpeg_path, "-i", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stderr + completed.stdout


def _get_video_duration(output: str) -> Optional[float]:
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


def _probe_streams(output: str) -> List[Dict[str, Union[str, int]]]:
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


def _build_map_args(streams: List[Dict[str, Union[str, int]]]) -> List[str]:
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


def _duration_error(field_name: str, field_value: str, duration_seconds: float) -> str:
    return (
        f"O tempo {field_name} informado ({field_value}) e invalido para este video. "
        f"A duracao do video e {_seconds_to_mmss(duration_seconds)}."
    )


def _run_command(command: List[str]) -> Tuple[bool, str]:
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


def _cut_segment(
    ffmpeg_path: str,
    source: Path,
    target: Path,
    map_args: List[str],
    start_seconds: float,
    duration_seconds: float,
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
        *map_args,
        "-map_metadata",
        "0",
        "-c",
        "copy",
        "-copy_unknown",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "use_metadata_tags",
        str(target),
    ]
    return _run_command(command)


def _concat_segments(
    ffmpeg_path: str,
    segments: List[Path],
    target: Path,
    concat_file: Path,
) -> Tuple[bool, str]:
    concat_lines = "\n".join(f"file '{segment.as_posix()}'" for segment in segments)
    concat_file.write_text(concat_lines, encoding="utf-8")

    command = [
        ffmpeg_path,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-map",
        "0:0",
        "-map",
        "0:1?",
        "-map",
        "0:2?",
        "-c",
        "copy",
        "-copy_unknown",
        "-movflags",
        "use_metadata_tags",
        str(target),
    ]
    return _run_command(command)


def _log_step(log: Optional[Callable[[str], None]], message: str) -> None:
    if log:
        log(message)


def cut_video(
    input_path: str,
    output_path: str,
    start_time: str,
    end_time: str,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    _log_step(log, "Validando tempos informados.")
    if not validar_tempo(start_time) or not validar_tempo(end_time):
        return False, "Formato de tempo invalido. Use MM:SS."

    start_seconds = _time_to_seconds(start_time)
    end_seconds = _time_to_seconds(end_time)

    if end_seconds <= start_seconds:
        return False, "O tempo final deve ser maior que o tempo inicial."

    ffmpeg_path = _resolve_ffmpeg_path()
    if not ffmpeg_path:
        return False, "FFmpeg nao foi encontrado no PATH."

    source = Path(input_path)
    target = Path(output_path)

    _log_step(log, "Verificando arquivo de entrada.")
    if not source.exists():
        return False, f"Arquivo de origem nao encontrado: {source}"

    target.parent.mkdir(parents=True, exist_ok=True)

    _log_step(log, "Verificando espaco em disco.")
    ok, error_msg = _check_disk_space(target, source.stat().st_size)
    if not ok:
        return False, error_msg

    _log_step(log, "Inspecionando video e telemetria.")
    inspect_output = _inspect_input(ffmpeg_path, source)
    map_args = _build_map_args(_probe_streams(inspect_output))
    duration_seconds = _get_video_duration(inspect_output)

    if not map_args:
        return False, "Nao foi possivel identificar streams compativeis para o corte."

    if duration_seconds is None:
        return False, "Nao foi possivel identificar a duracao do video."

    if start_seconds >= duration_seconds:
        return False, _duration_error("inicial", start_time, duration_seconds)

    if end_seconds > duration_seconds:
        return False, _duration_error("final", end_time, duration_seconds)

    temp_id = uuid.uuid4().hex
    first_segment = target.parent / f"{target.stem}_{temp_id}_antes.mp4"
    second_segment = target.parent / f"{target.stem}_{temp_id}_depois.mp4"
    concat_file = target.parent / f"{target.stem}_{temp_id}_partes.txt"
    temp_files = [first_segment, second_segment, concat_file]

    try:
        segments: List[Path] = []
        segment_specs = []

        if start_seconds > 0:
            segment_specs.append((first_segment, 0, start_seconds))

        if end_seconds < duration_seconds:
            segment_specs.append(
                (second_segment, end_seconds, duration_seconds - end_seconds)
            )

        for segment_path, segment_start, segment_duration in segment_specs:
            _log_step(
                log,
                f"Cortando segmento {segment_path.name} a partir de "
                f"{_seconds_to_mmss(segment_start)} por {_seconds_to_mmss(segment_duration)}.",
            )
            ok, output = _cut_segment(
                ffmpeg_path,
                source,
                segment_path,
                map_args,
                start_seconds=segment_start,
                duration_seconds=segment_duration,
            )
            if not ok:
                return False, output
            segments.append(segment_path)

        if not segments:
            return False, "O intervalo informado remove o video inteiro."

        _log_step(log, "Juntando segmentos finais.")
        ok, output = _concat_segments(ffmpeg_path, segments, target, concat_file)
        if not ok:
            return False, output
    finally:
        _log_step(log, "Limpando arquivos temporarios.")
        for temp_file in temp_files:
            try:
                temp_file.unlink(missing_ok=True)
            except PermissionError:
                pass

    _log_step(log, "Corte finalizado com sucesso.")
    return True, f"Arquivo gerado em: {target}"
