import json
import shutil
import subprocess
from pathlib import Path

from backend.app_paths import BASE_DIR


FIRMWARE_MODELO = {
    "H24": "Hero 13 Black",
    "H23": "Hero 12 Black",
    "H22": "Hero 11 Black",
    "H21": "Hero 10 Black",
    "H20": "Hero 10 Black",
    "H19": "Hero 10 Black",
    "H18": "Hero 9 Black",
    "HD9": "Hero 9 Black",
    "HD8": "Hero 8 Black",
    "HD7": "Hero 7 Black",
    "HD6": "Hero 6 Black",
    "HD5": "Hero 5 Black",
    "H17": "Hero 7 Black",
}


def detectar_modelo_gopro(tags: dict) -> str:
    fw = tags.get("firmware", tags.get("com.gopro.firmware_version", ""))
    prefixo = fw[:3].upper() if fw else ""
    if prefixo in FIRMWARE_MODELO:
        return FIRMWARE_MODELO[prefixo]
    model = tags.get("com.android.model", tags.get("model", ""))
    if model and model.upper() not in ("GOPRO", ""):
        return model
    return f"GoPro ({prefixo})" if prefixo else "GoPro"


def resolucao_video(path: Path) -> tuple[int | None, int | None]:
    info = ffprobe_json(path, show_format=False)
    video_stream = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    width = video_stream.get("width")
    height = video_stream.get("height")
    return (
        int(width) if isinstance(width, int) or str(width).isdigit() else None,
        int(height) if isinstance(height, int) or str(height).isdigit() else None,
    )


def fps_video(path: Path) -> float | None:
    info = ffprobe_json(path, show_format=False)
    video_stream = next(
        (s for s in info.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    fps_str = video_stream.get("r_frame_rate", "0/1")
    fps_parts = str(fps_str).split("/")
    if len(fps_parts) != 2:
        return None
    numerator = float(fps_parts[0])
    denominator = float(fps_parts[1])
    return numerator / denominator if denominator else None


def ffprobe_json(path: Path, show_format: bool = True) -> dict:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json"]
    if show_format:
        cmd += ["-show_format", "-show_streams"]
    else:
        cmd += ["-show_streams"]
    cmd.append(str(path))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return json.loads(r.stdout) if r.stdout else {}


def resolve_exiftool_path() -> str | None:
    exiftool_path = shutil.which("exiftool")
    if exiftool_path:
        return exiftool_path

    candidates = [
        BASE_DIR / "exiftool.exe",
        BASE_DIR / "exiftool" / "exiftool.exe",
        BASE_DIR / "tools" / "exiftool.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return None


def exiftool_json(path: Path) -> dict:
    exiftool_path = resolve_exiftool_path()
    if not exiftool_path:
        return {}

    cmd = [exiftool_path, "-j", "-G", "-s", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode != 0 or not r.stdout:
        return {}

    data = json.loads(r.stdout)
    return data[0] if data else {}


def _iter_text_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_text_values(item)
    elif value is not None:
        yield str(value)


def _iter_fov_text_values(value):
    key_hints = (
        "fov", "fieldofview", "field of view", "lens", "lente", "view",
        "digital", "mode", "modo", "setting", "superview", "hyperview",
    )

    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower().replace("_", " ")
            is_relevant = any(hint in key_lower for hint in key_hints)
            if is_relevant:
                yield key_text
                yield from _iter_text_values(item)
            else:
                yield from _iter_fov_text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_fov_text_values(item)


def detectar_fov_linear(info: dict) -> tuple[bool | None, str]:
    textos = [texto.lower() for texto in _iter_fov_text_values(info)]
    texto_total = " ".join(textos)

    if "linear" in texto_total:
        return True, "Linear"

    modos_nao_lineares = [
        ("superview", "SuperView"),
        ("hyperview", "HyperView"),
        ("wide", "Wide"),
        ("amplo", "Amplo"),
        ("narrow", "Narrow"),
        ("estreito", "Estreito"),
    ]
    for chave, nome in modos_nao_lineares:
        if chave in texto_total:
            return False, nome

    return None, "Nao identificado"


def detectar_fov_video(path: Path, ffprobe_info: dict | None = None) -> tuple[bool | None, str, str]:
    exif_info = exiftool_json(path)
    if exif_info:
        fov_linear, fov_nome = detectar_fov_linear(exif_info)
        if fov_linear is not None:
            return fov_linear, fov_nome, "ExifTool"

    info = ffprobe_info if ffprobe_info is not None else ffprobe_json(path)
    fov_linear, fov_nome = detectar_fov_linear(info)
    if fov_linear is not None:
        return fov_linear, fov_nome, "ffprobe"

    return None, "Nao identificado", "ExifTool/ffprobe"


def validar_fov_linear(path: Path) -> tuple[bool, str, bool | None, str]:
    try:
        fov_linear, fov_nome, _ = detectar_fov_video(path)
    except Exception as exc:
        return False, f"Nao foi possivel identificar o FOV do video: {exc}", None, "Nao identificado"

    if fov_linear is True:
        return True, "", fov_linear, fov_nome
    if fov_linear is False:
        return False, f"O FOV do video e {fov_nome}. O processamento so e permitido para videos com FOV linear.", fov_linear, fov_nome
    return False, "Nao foi possivel identificar se o FOV do video e linear.", fov_linear, fov_nome


def fmt_duracao(segundos: float) -> str:
    s = int(segundos)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"
