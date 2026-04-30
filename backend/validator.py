from pathlib import Path
from typing import Optional

from backend.models import TaskRequest
from backend.ffmpeg_service import validar_tempo


def validate_request(request: TaskRequest) -> Optional[str]:
    if not request.source_path:
        return "Selecione um arquivo de origem."

    if not request.target_path:
        return "Defina um arquivo de destino."

    if not request.start_time:
        return "Defina o tempo inicial do corte."

    if not request.end_time:
        return "Defina o tempo final do corte."

    if not validar_tempo(request.start_time):
        return "Tempo inicial invalido. Use MM:SS."

    if not validar_tempo(request.end_time):
        return "Tempo final invalido. Use MM:SS."

    source = Path(request.source_path)
    if not source.exists():
        return f"Arquivo de origem nao encontrado: {source}"

    target = Path(request.target_path)
    if target.exists():
        return f"Ja existe um video com esse nome na pasta de saida: {target.name}"

    return None
