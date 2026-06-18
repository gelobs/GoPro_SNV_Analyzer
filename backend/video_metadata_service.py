from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional


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


def _project_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def resolve_exiftool_path() -> Optional[str]:
    exiftool_path = shutil.which("exiftool")
    if exiftool_path:
        return exiftool_path

    root = _project_root()
    candidates = [
        root / "exiftool.exe",
        root / "exiftool" / "exiftool.exe",
        root / "tools" / "exiftool.exe",
        Path.cwd() / "exiftool.exe",
        Path.cwd() / "tools" / "exiftool.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return None


def _read_exiftool_json(path: Path) -> dict:
    exiftool_path = resolve_exiftool_path()
    if not exiftool_path:
        return {}

    completed = subprocess.run(
        [exiftool_path, "-j", "-G", "-s", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        return {}

    data = json.loads(completed.stdout)
    return data[0] if data else {}


def _read_ffprobe_json(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(completed.stdout) if completed.stdout else {}


def _tag_by_suffix(tags: dict, suffixes: tuple[str, ...]) -> str:
    suffixes_lower = tuple(s.lower() for s in suffixes)
    for key, value in tags.items():
        key_name = str(key).split(":")[-1].lower()
        if key_name in suffixes_lower and value not in (None, ""):
            return str(value)
    return ""


def _flatten_ffprobe_tags(info: dict) -> dict:
    tags = dict(info.get("format", {}).get("tags", {}) or {})
    for stream in info.get("streams", []) or []:
        tags.update(stream.get("tags", {}) or {})
    return tags


def _normalizar_modelo(modelo: str) -> str:
    texto = modelo.strip()
    match = re.search(r"hero\s*(\d+)", texto, re.IGNORECASE)
    if match:
        return f"Hero {match.group(1)} Black"
    return texto


def _modelo_por_firmware(tags: dict) -> str:
    firmware = _tag_by_suffix(tags, ("Firmware", "FirmwareVersion", "GoProFirmwareVersion"))
    prefixo = firmware[:3].upper() if firmware else ""
    return FIRMWARE_MODELO.get(prefixo, "")


def _detectar_modelo(tags: dict) -> str:
    modelo = _tag_by_suffix(
        tags,
        (
            "Model",
            "CameraModelName",
            "DeviceModelName",
            "AndroidModel",
        ),
    )
    if modelo and modelo.upper() not in {"GOPRO", "GOPRO MET"}:
        return _normalizar_modelo(modelo)

    modelo_firmware = _modelo_por_firmware(tags)
    if modelo_firmware:
        return modelo_firmware

    return "GoPro"


def _detectar_ano(tags: dict) -> str:
    ano, _, _ = _detectar_data_gravacao(tags)
    return ano


def _detectar_data_gravacao(tags: dict) -> tuple[str, str, str]:
    data = _tag_by_suffix(
        tags,
        (
            "CreateDate",
            "MediaCreateDate",
            "TrackCreateDate",
            "CreationDate",
            "CreationTime",
            "DateTimeOriginal",
        ),
    )
    match = re.search(r"\b((?:19|20)\d{2})[:/-](\d{1,2})[:/-](\d{1,2})\b", data)
    if match:
        ano, mes, dia = match.groups()
        return ano, mes.zfill(2), dia.zfill(2)

    match = re.search(r"\b((?:19|20)\d{2})\b", data)
    return (match.group(1), "", "") if match else ("", "", "")


def _detectar_video_frame_rate(tags: dict, ffprobe_info: dict) -> str:
    frame_rate = _tag_by_suffix(tags, ("VideoFrameRate", "FrameRate"))
    if frame_rate:
        match = re.search(r"\d+(?:[.,]\d+)?", frame_rate)
        return match.group(0).replace(",", ".") if match else ""

    for stream in ffprobe_info.get("streams", []) or []:
        if stream.get("codec_type") != "video":
            continue

        for field in ("avg_frame_rate", "r_frame_rate"):
            raw_value = str(stream.get(field, ""))
            if "/" in raw_value:
                numerator, denominator = raw_value.split("/", 1)
                try:
                    value = float(numerator) / float(denominator)
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
                return f"{value:.2f}".rstrip("0").rstrip(".")

            match = re.search(r"\d+(?:[.,]\d+)?", raw_value)
            if match:
                return match.group(0).replace(",", ".")

    return ""


def _detectar_location(tags: dict) -> str:
    return _tag_by_suffix(
        tags,
        (
            "Location",
            "LocationName",
            "LocationInformation",
            "LocationBody",
            "GPSPosition",
        ),
    )


def _metadata_origem(source: Path) -> tuple[str, str, str, str, str, str]:
    tags = _read_exiftool_json(source)
    ffprobe_info = _read_ffprobe_json(source)
    ffprobe_tags = _flatten_ffprobe_tags(ffprobe_info)
    if not tags:
        tags = ffprobe_tags
    else:
        tags = {**ffprobe_tags, **tags}

    ano, mes, dia = _detectar_data_gravacao(tags)
    return (
        _detectar_modelo(tags),
        ano,
        mes,
        dia,
        _detectar_video_frame_rate(tags, ffprobe_info),
        _detectar_location(tags),
    )


def aplicar_metadata_corte(
    source: Path,
    outputs: list[Path],
    log: Optional[Callable[[str], None]] = None,
) -> None:
    exiftool_path = resolve_exiftool_path()
    if not exiftool_path:
        if log:
            log("ExifTool nao encontrado. Metadados do corte nao foram preenchidos.")
        return

    modelo, ano, mes, dia, video_frame_rate, location = _metadata_origem(source)
    if not ano:
        if log:
            log("Ano de gravacao nao identificado nos metadados do video original.")

    args = [
        exiftool_path,
        "-overwrite_original",
        f"-Artist={modelo}",
        f"-ItemList:Artist={modelo}",
        f"-Keys:Artist={modelo}",
        f"-AlbumArtist={modelo}",
        f"-ItemList:AlbumArtist={modelo}",
        f"-Author={modelo}",
        f"-Keys:Author={modelo}",
        f"-QuickTime:Performer={modelo}",
        f"-QuickTime:Performers={modelo}",
        f"-ItemList:Performer={modelo}",
        f"-Microsoft:AlbumArtist={modelo}",
        "-Genre=Rodovia",
        "-ItemList:Genre=Rodovia",
        "-Keys:Genre=Rodovia",
        "-Microsoft:Category=Rodovia",
    ]
    if video_frame_rate:
        args.append(f"-BeatsPerMinute={video_frame_rate}")
        args.append(f"-QuickTime:BeatsPerMinute={video_frame_rate}")
        args.append(f"-ItemList:BeatsPerMinute={video_frame_rate}")

    if location:
        args.append(f"-Comment={location}")
        args.append(f"-Keys:Comment={location}")
        args.append(f"-ItemList:Comment={location}")
        args.append(f"-Description={location}")
        args.append(f"-Keys:Description={location}")
        args.append(f"-ItemList:Description={location}")

    if ano:
        args.append(f"-Year={ano}")
        args.append(f"-ItemList:Year={ano}")
        args.append(f"-Keys:Year={ano}")
        args.append(f"-Date={ano}")
        args.append(f"-Conductor={ano}")
        args.append(f"-ItemList:Conductor={ano}")
        args.append(f"-Microsoft:Conductor={ano}")

    if mes:
        args.append(f"-Period={mes}")
        args.append(f"-Microsoft:Period={mes}")

    if dia:
        args.append(f"-Mood={dia}")
        args.append(f"-QuickTime:Mood={dia}")
        args.append(f"-Microsoft:Mood={dia}")

    updated_count = 0
    failed_count = 0
    for output in outputs:
        exiftool_temp = output.with_name(f"{output.name}_exiftool_tmp")
        for stale_temp in (exiftool_temp,):
            try:
                stale_temp.unlink(missing_ok=True)
            except PermissionError:
                if log:
                    log(f"Nao foi possivel remover arquivo temporario antigo: {stale_temp.name}")

        completed = subprocess.run(
            [*args, str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            if exiftool_temp.exists():
                try:
                    output.unlink()
                    exiftool_temp.rename(output)
                    updated_count += 1
                    continue
                except PermissionError as exc:
                    if log:
                        log(f"Nao foi possivel substituir {output.name} apos metadados: {exc}")

            failed_count += 1
            if log:
                detail = completed.stderr.strip() or completed.stdout.strip()
                log(f"Nao foi possivel preencher metadados de {output.name}: {detail}")
        else:
            updated_count += 1

    if log and updated_count:
        ano_texto = ano if ano else "nao identificado"
        bpm_texto = video_frame_rate if video_frame_rate else "nao identificado"
        log(
            f"Metadados aplicados: artista={modelo}; ano={ano_texto}; "
            f"genero=Rodovia; bpm={bpm_texto}."
        )
    elif log and failed_count:
        log("Metadados do corte nao foram preenchidos.")
