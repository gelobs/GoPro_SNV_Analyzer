"""
src/gp12_gps_extractor.py
Extrai metadados GPS de arquivos .MP4 da GoPro (Hero 11, 12, 13).
Compativel com Python 3.13 usando gopro2gpx 0.3 do GitHub.
"""
import argparse
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
    from gopro2gpx.gopro2gpx import setup_environment, BuildGPSPoints
    from gopro2gpx.ffmpegtools import FFMpegTools

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

    config  = setup_environment(args)
    fftools = FFMpegTools(
        ffprobe=config.ffprobe_cmd,
        ffmpeg=config.ffmpeg_cmd,
    )

    reader   = gpmf.GpmfFileReader(fftools, verbose=0)
    raw_data = reader.readRawTelemetryFromMP4(str(mp4))
    data     = gpmf.parseStream(raw_data, verbose=0)

    points, start_time, device_name = BuildGPSPoints(
        data,
        skip     = args.skip,
        skipDop  = args.skip_dop,
        dopLimit = args.dop_limit,
        timeShift= args.time_shift,
    )

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
            "speed3d":   float(getattr(pt, "speed",     0.0) or 0.0),
            "precision": 100.0,
            "gps_fix":   3,
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
