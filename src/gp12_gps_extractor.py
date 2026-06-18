"""
src/gp12_gps_extractor.py
Extrai metadados GPS de arquivos .MP4 da GoPro (Hero 11, 12, 13).
Compativel com Python 3.13 usando gopro2gpx 0.3 do GitHub.
"""
import argparse
from datetime import datetime, timedelta
import inspect
import json
import os
import sys
import array
import subprocess
import struct
from pathlib import Path

import numpy as np
import pandas as pd


def extract_hero12_gps(mp4_path: str) -> pd.DataFrame:
    """
    Extrai GPS de qualquer GoPro Hero (11, 12, 13) via gopro2gpx 0.3.
    Usa BuildGPSPoints diretamente — sem gravar arquivo GPX intermediario.
    """
    mp4 = Path(mp4_path)
    if not mp4.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {mp4_path}")

    from gopro2gpx import gpmf
    import gopro2gpx.gopro2gpx as gopro_api

    args = argparse.Namespace(
        files      = [str(mp4)],
        outputfile = str(mp4.with_suffix("")),
        verbose    = 0,
        binary     = False,
        skip       = True,
        skip_dop   = False,
        dop_limit  = 2000,
        time_shift = 0,
        gpx        = False,
        kml        = False,
        csv        = False,
        gui        = True,
    )

    pontos_resultado = _extrair_pontos_gopro2gpx(mp4, args, gpmf, gopro_api)
    points = pontos_resultado[0] if isinstance(pontos_resultado, tuple) else pontos_resultado

    if not points:
        raise RuntimeError(
            f"Nenhum ponto GPS extraido de {mp4.name}.\n"
            "Verifique se o GPS estava ativado e se o video foi gravado ao ar livre."
        )

    df = _points_para_dataframe(points)

    dur = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds()
    print(f"[GPS] {mp4.name} -> {len(df)} amostras | "
          f"{dur:.0f}s | {df['km'].max():.2f} km")
    return df


def _extrair_pontos_gopro2gpx(mp4: Path, args, gpmf, gopro_api):
    build = gopro_api.BuildGPSPoints
    if hasattr(gopro_api, "setup_environment") and hasattr(gpmf, "GpmfFileReader"):
        from gopro2gpx.ffmpegtools import FFMpegTools

        config = gopro_api.setup_environment(args)
        fftools = FFMpegTools(
            ffprobe=config.ffprobe_cmd,
            ffmpeg=config.ffmpeg_cmd,
        )
        reader = gpmf.GpmfFileReader(fftools, verbose=0)
        raw_data = reader.readRawTelemetryFromMP4(str(mp4))
        data = gpmf.parseStream(raw_data, verbose=0)
        return build(
            data,
            skip=args.skip,
            skipDop=args.skip_dop,
            dopLimit=args.dop_limit,
            timeShift=args.time_shift,
        )

    from gopro2gpx.config import Config
    from gopro2gpx.ffmpegtools import FFMpegTools

    ffmpeg_cmd, ffprobe_cmd = _resolver_ffmpeg()
    os.environ["FFMPEG_PATH"] = ffmpeg_cmd
    os.environ["FFPROBE_PATH"] = ffprobe_cmd
    config = Config(str(mp4), str(mp4.with_suffix("")), "gpx", 0, args.skip)
    raw_data = _read_raw_gpmd(mp4, ffmpeg_cmd, ffprobe_cmd)
    data = _parse_stream_compat(raw_data)
    points = _build_gps_points_compat(data, skip=args.skip)
    if points:
        return points

    params = inspect.signature(build).parameters
    if "skipDop" in params:
        return build(
            data,
            skip=args.skip,
            skipDop=args.skip_dop,
            dopLimit=args.dop_limit,
            timeShift=args.time_shift,
        )
    return build(data, skip=args.skip)


class _GPSPoint:
    def __init__(
        self,
        latitude=0.0,
        longitude=0.0,
        elevation=0.0,
        time=None,
        speed=0.0,
        speed3d=0.0,
        precision=100.0,
        gps_fix=3,
    ):
        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.time = time or datetime.now()
        self.speed = speed
        self.speed3d = speed3d
        self.precision = precision
        self.gps_fix = gps_fix


def _build_gps_points_compat(data, skip=False):
    points = []
    scal = None
    gpsu = None
    gpsfix = 0
    stats = {
        "ok": 0,
        "badfix": 0,
        "badfixskip": 0,
        "empty": 0,
    }

    for item in data:
        if item.fourCC == "SCAL":
            scal = item.data
        elif item.fourCC == "GPSU":
            gpsu = item.data
        elif item.fourCC == "GPSF":
            gpsfix = int(item.data or 0)
        elif item.fourCC == "GPS5":
            point = _gps5_point(item, scal, gpsu, gpsfix, skip, stats)
            if point is not None:
                points.append(point)
        elif item.fourCC == "GPS9":
            points.extend(_gps9_points(item, scal, skip, stats))

    if points:
        print("-- stats -----------------")
        print("- Ok:              %5d" % stats["ok"])
        print("- GPSFIX=0 (bad):  %5d (skipped: %d)" % (stats["badfix"], stats["badfixskip"]))
        print("- Empty (No data): %5d" % stats["empty"])
        print("Total points:      %5d" % (stats["ok"] + stats["badfix"] + stats["empty"]))
        print("--------------------------")

    return points


def _gps5_point(item, scal, gpsu, gpsfix, skip, stats):
    if item.data is None:
        return None

    raw_values = list(item.data._asdict().values())
    if raw_values[0] == raw_values[1] == raw_values[2] == 0:
        stats["empty"] += 1
        return None

    if gpsfix == 0:
        stats["badfix"] += 1
        if skip:
            stats["badfixskip"] += 1
            return None

    scale_values = _scale_values(scal, len(raw_values))
    lat, lon, alt, speed, speed3d = [
        float(value) / float(scale)
        for value, scale in zip(raw_values, scale_values)
    ]
    when = datetime.fromtimestamp(0)
    if gpsu is not None:
        import time as _time
        when = datetime.fromtimestamp(_time.mktime(gpsu))

    stats["ok"] += 1
    return _GPSPoint(lat, lon, alt, when, speed, speed3d, 100.0, gpsfix)


def _gps9_points(item, scal, skip, stats):
    if not item.data:
        return []

    scale_values = _scale_values(
        scal,
        9,
        default=(10_000_000, 10_000_000, 1000, 1000, 100, 1, 1000, 100, 1),
    )
    base_time = datetime(2000, 1, 1)
    points = []

    for record in item.data:
        lat_raw, lon_raw, alt_raw, speed_raw, speed3d_raw, days_raw, secs_raw, dop_raw, fix_raw = record
        if lat_raw == lon_raw == alt_raw == 0:
            stats["empty"] += 1
            continue

        fix = int(fix_raw / scale_values[8])
        if fix == 0:
            stats["badfix"] += 1
            if skip:
                stats["badfixskip"] += 1
                continue

        lat = lat_raw / scale_values[0]
        lon = lon_raw / scale_values[1]
        alt = alt_raw / scale_values[2]
        speed = speed_raw / scale_values[3]
        speed3d = speed3d_raw / scale_values[4]
        days = days_raw / scale_values[5]
        seconds = secs_raw / scale_values[6]
        precision = dop_raw / scale_values[7]
        when = base_time + timedelta(days=days, seconds=seconds)

        stats["ok"] += 1
        points.append(_GPSPoint(lat, lon, alt, when, speed, speed3d, precision, fix))

    return points


def _scale_values(scal, count, default=None):
    if default is None:
        default = tuple(1 for _ in range(count))
    if scal is None:
        return default
    if isinstance(scal, (int, float)):
        return tuple(float(scal) for _ in range(count))
    values = tuple(scal)
    if len(values) < count:
        values = values + tuple(default[len(values):])
    return values[:count]


def _read_raw_gpmd(mp4: Path, ffmpeg_cmd: str, ffprobe_cmd: str) -> bytes:
    probe = subprocess.run(
        [
            ffprobe_cmd,
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            str(mp4),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError((probe.stderr or probe.stdout or "ffprobe falhou").strip())

    try:
        data = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe retornou JSON invalido: {exc}") from exc

    stream_index = None
    for stream in data.get("streams", []):
        tags = stream.get("tags") or {}
        codec_type = str(stream.get("codec_type") or "").lower()
        tag = str(stream.get("codec_tag_string") or "").lower()
        codec_tag = str(stream.get("codec_tag") or "").lower()
        handler = str(tags.get("handler_name") or "").lower()
        if codec_type == "data" and (
            tag == "gpmd" or
            codec_tag == "0x646d7067" or
            handler == "gopro met"
        ):
            stream_index = stream.get("index")
            break

    if stream_index is None:
        raise RuntimeError(f"O video {mp4.name} nao possui faixa de telemetria GPMD.")

    extract = subprocess.run(
        [
            ffmpeg_cmd,
            "-v", "error",
            "-i", str(mp4),
            "-codec", "copy",
            "-map", f"0:{stream_index}",
            "-f", "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if extract.returncode != 0:
        erro = extract.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(erro or "Falha ao extrair telemetria GPMD com ffmpeg.")
    return extract.stdout


def _resolver_ffmpeg() -> tuple[str, str]:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    ffmpeg_dir = base_dir / "ffmpeg" / "bin"
    ffmpeg = ffmpeg_dir / "ffmpeg.exe"
    ffprobe = ffmpeg_dir / "ffprobe.exe"
    return (
        str(ffmpeg) if ffmpeg.exists() else os.environ.get("FFMPEG_PATH", "ffmpeg"),
        str(ffprobe) if ffprobe.exists() else os.environ.get("FFPROBE_PATH", "ffprobe"),
    )


def _parse_stream_compat(data_raw):
    data = array.array("b")
    data.frombytes(data_raw)
    offset = 0
    klvlist = []

    while offset < len(data):
        klv = _KLVDataCompat(data, offset)
        if not klv.skip():
            klvlist.append(klv)
        offset += 8
        if klv.type != 0:
            offset += klv.padded_length

    return klvlist


class _KLVDataCompat:
    _header = struct.Struct(">4sBBH")

    def __init__(self, data, offset):
        from gopro2gpx.fourCC import Manage

        self.fourCC, self.type, self.size, self.repeat = self._header.unpack_from(data, offset=offset)
        self.fourCC = self.fourCC.decode(errors="replace")
        self.type = int(self.type)
        self.length = self.size * self.repeat
        self.padded_length = self._pad(self.length)
        self.rawdata = self._read_raw_data(data, offset)
        try:
            self.data = Manage(self)
        except Exception:
            if self.fourCC == "GPS9":
                self.data = self._read_gps9_records()
            else:
                self.data = None

    def skip(self):
        from gopro2gpx.fourCC import skip_labels
        return self.fourCC in skip_labels or self.data is None

    def _read_gps9_records(self):
        if not self.rawdata:
            return []
        record = struct.Struct(">lllllllHH")
        records = []
        for offset in range(0, min(len(self.rawdata), self.length), self.size):
            if offset + record.size <= len(self.rawdata):
                records.append(record.unpack_from(self.rawdata, offset))
        return records

    def _read_raw_data(self, data, offset):
        if self.type == 0:
            return None
        num_bytes = self._pad(self.size * self.repeat)
        if num_bytes == 0:
            return None
        fmt = ">" + str(num_bytes) + "s"
        rawdata, = struct.Struct(fmt).unpack_from(data, offset=offset + 8)
        return rawdata

    @staticmethod
    def _pad(value, base=4):
        while value % base != 0:
            value += 1
        return value


def _points_para_dataframe(points) -> pd.DataFrame:
    """
    Converte lista de GPSPoint do gopro2gpx em DataFrame.
    GPSPoint tem: latitude, longitude, elevation, time (datetime), speed
    NAO tem: epoch, altitude, dop, fix
    """
    rows = []
    for pt in points:
        rows.append({
            "timestamp": pd.Timestamp(pt.time),
            "lat":       float(pt.latitude),
            "lon":       float(pt.longitude),
            "alt":       float(getattr(pt, "elevation", 0.0) or 0.0),
            "speed2d":   float(getattr(pt, "speed",     0.0) or 0.0),
            "speed3d":   float(getattr(pt, "speed3d", getattr(pt, "speed", 0.0)) or 0.0),
            "precision": float(getattr(pt, "precision", 100.0) or 100.0),
            "gps_fix":   int(getattr(pt, "gps_fix", 3) or 0),
        })

    df = pd.DataFrame(rows)

    # Normaliza timezone — Hero 11 mistura tz-aware e tz-naive no mesmo arquivo
    def _norm(ts):
        if ts.tzinfo is not None:
            return ts.tz_convert("UTC").tz_localize(None)
        return ts

    df["timestamp"] = df["timestamp"].apply(_norm)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = _calcular_km(df)
    return df


def _calcular_km(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula distancia acumulada (km) e velocidade via Haversine."""
    R     = 6_371_000
    lat_r = np.radians(df["lat"].values)
    lon_r = np.radians(df["lon"].values)
    dlat  = np.diff(lat_r, prepend=lat_r[0])
    dlon  = np.diff(lon_r, prepend=lon_r[0])
    a     = (np.sin(dlat / 2) ** 2
             + np.cos(np.roll(lat_r, 1)) * np.cos(lat_r)
             * np.sin(dlon / 2) ** 2)
    a[0]   = 0
    dist_m = R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

    # to_numpy(copy=True) evita ValueError: assignment destination is read-only
    dt_s    = (df["timestamp"].diff()
               .dt.total_seconds()
               .fillna(0).clip(lower=0.01)
               .to_numpy(copy=True))
    dt_s[0] = 1

    if df["speed2d"].abs().max() < 0.1:
        df["speed2d"] = dist_m / dt_s
        df["speed3d"] = df["speed2d"]

    df["dist_m"] = dist_m
    df["km"]     = np.cumsum(dist_m) / 1000
    return df
