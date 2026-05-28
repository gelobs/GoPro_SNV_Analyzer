from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, Tuple

from backend.ffmpeg_service import (
    build_map_args,
    check_disk_space,
    cut_segment,
    duration_error,
    get_video_duration,
    inspect_input,
    probe_streams,
    resolve_ffmpeg_path,
    seconds_to_mmss,
    time_to_seconds,
    validar_tempo,
)


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
    ok, details, _ = split_video_on_cut(
        input_path=input_path,
        output_path=output_path,
        start_time=start_time,
        end_time=end_time,
        log=log,
    )
    return ok, details


def split_video_on_cut(
    input_path: str,
    output_path: str,
    start_time: str,
    end_time: str,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str, List[str]]:
    _log_step(log, "Validando tempos informados.")
    if not validar_tempo(start_time) or not validar_tempo(end_time):
        return False, "Formato de tempo invalido. Use MM:SS.", []

    start_seconds = time_to_seconds(start_time)
    end_seconds = time_to_seconds(end_time)

    ffmpeg_path = resolve_ffmpeg_path()
    if not ffmpeg_path:
        return False, "FFmpeg nao foi encontrado no PATH.", []

    source = Path(input_path)
    target = Path(output_path)

    _log_step(log, "Verificando arquivo de entrada.")
    if not source.exists():
        return False, f"Arquivo de origem nao encontrado: {source}", []

    target.parent.mkdir(parents=True, exist_ok=True)

    _log_step(log, "Verificando espaco em disco.")
    ok, error_msg = check_disk_space(target, source.stat().st_size)
    if not ok:
        return False, error_msg, []

    _log_step(log, "Inspecionando video e telemetria.")
    inspect_output = inspect_input(ffmpeg_path, source)
    map_args = build_map_args(probe_streams(inspect_output))
    duration_seconds = get_video_duration(inspect_output)

    if not map_args:
        return False, "Nao foi possivel identificar streams compativeis para o corte.", []

    if duration_seconds is None:
        return False, "Nao foi possivel identificar a duracao do video.", []

    if start_seconds >= duration_seconds:
        return False, duration_error("inicial", start_time, duration_seconds), []

    if end_seconds > duration_seconds:
        return False, duration_error("final", end_time, duration_seconds), []

    cut_start = start_seconds
    cut_end = duration_seconds if end_seconds == 0 else end_seconds

    if cut_start == 0 and end_seconds == 0:
        return False, "Informe um trecho para remover.", []

    if cut_end <= cut_start:
        return False, "O tempo final deve ser maior que o tempo inicial.", []

    first_segment = target.parent / f"{target.stem}_comeco.mp4"
    second_segment = target.parent / f"{target.stem}_fim.mp4"
    segment_specs = []

    if cut_start > 0:
        segment_specs.append((first_segment, 0, cut_start))

    if cut_end < duration_seconds:
        segment_specs.append((second_segment, cut_end, duration_seconds - cut_end))

    if not segment_specs:
        return False, "O trecho informado remove o video inteiro.", []

    output_paths = [segment_path for segment_path, _, _ in segment_specs]

    success = False
    try:
        for index, (segment_path, segment_start, segment_duration) in enumerate(
            segment_specs,
            start=1,
        ):
            _log_step(
                log,
                f"Cortando segmento {index}/{len(segment_specs)} ({segment_path.name}) a partir de "
                f"{seconds_to_mmss(segment_start)} por {seconds_to_mmss(segment_duration)}.",
            )
            ok, output = cut_segment(
                ffmpeg_path,
                source,
                segment_path,
                map_args,
                start_seconds=segment_start,
                duration_seconds=segment_duration,
            )
            if not ok:
                return False, output, []
        success = True
    finally:
        _log_step(log, "Limpando arquivos temporarios.")
        if not success:
            for output_file in output_paths:
                try:
                    output_file.unlink(missing_ok=True)
                except PermissionError:
                    pass

    _log_step(log, "Corte finalizado com sucesso.")
    output_text = "\n".join(str(path) for path in output_paths)
    return True, f"Arquivos gerados:\n{output_text}", [str(path) for path in output_paths]
