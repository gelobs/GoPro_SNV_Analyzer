"""
src/gp12_features.py
Engenharia de features e detecção de anomalias GPS para GoPro Hero 12 Black.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


# ── Limiares físicos calibrados para Hero 12 em rodovia ──────────────────────
GPSP_RUIM        = 250   # precisão DOP > 2.5
MAX_VELOCIDADE   = 60    # m/s  (~216 km/h) — acima = spike
MAX_ACELERACAO   = 15    # m/s² — impossível em tráfego normal
MAX_SALTO_FRAMES = 40    # m/s entre frames — salto de posição impossível

FEATURE_COLS = [
    "lat", "lon", "alt",
    "speed2d", "speed3d",
    "dist_m", "speed_pos", "speed_diff",
    "accel_ms2", "delta_alt", "cog_deg",
    "dop", "fix_ok",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deriva features temporais e de qualidade de sinal.
    Equivale à Seção 2.3.1 do paper (Nayfeh, 2023).
    """
    df = df.copy()

    dt = (df["timestamp"].diff()
          .dt.total_seconds()
          .fillna(1 / 18)
          .clip(lower=0.01))
    df["dt_s"] = dt

    # ── Variação de posição (Haversine simplificado) ──────────────────────────
    R = 6_371_000
    lat_r = np.radians(df["lat"])
    lon_r = np.radians(df["lon"])
    dlat  = lat_r.diff().fillna(0)
    dlon  = lon_r.diff().fillna(0)
    a = (np.sin(dlat / 2) ** 2
         + np.cos(lat_r.shift()) * np.cos(lat_r) * np.sin(dlon / 2) ** 2)
    df["dist_m"] = R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

    # ── Velocidade implícita pela posição ─────────────────────────────────────
    df["speed_pos"]  = df["dist_m"] / dt
    df["speed_diff"] = (df["speed2d"] - df["speed_pos"]).abs()

    # ── Aceleração e variação de altitude ────────────────────────────────────
    df["accel_ms2"] = df["speed2d"].diff().fillna(0) / dt
    df["delta_alt"] = df["alt"].diff().fillna(0)

    # ── Course over ground (COG) ─────────────────────────────────────────────
    df["cog_deg"] = np.degrees(
        np.arctan2(
            np.radians(df["lon"].diff().fillna(0)),
            np.radians(df["lat"].diff().fillna(0)),
        )
    ) % 360

    # ── Features de qualidade do sinal (exclusivas Hero 12) ──────────────────
    df["precision"] = df["precision"].fillna(9999)
    df["dop"]       = df["precision"] / 100.0
    df["fix_ok"]    = (df["gps_fix"] == 3).astype(int)

    return df


def detect_anomalies(df: pd.DataFrame,
                     contamination: float = 0.05) -> pd.DataFrame:
    """
    Combina regras físicas com Isolation Forest para rotular amostras ruins.
    Score 0 = sinal perfeito, 3 = máximo suspeito.

    Baseado na abordagem de dois datasets do paper (Seção 2.3.2):
    location-dependent (com lat/lon/alt) para rodovias de rota fixa.
    """
    df = df.copy()

    # ── Isolation Forest ─────────────────────────────────────────────────────
    X = df[FEATURE_COLS].fillna(0)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    modelo = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=42,
    )
    df["anomaly_ml"] = modelo.fit_predict(X_scaled)   # 1=normal, -1=outlier

    # ── Regras físicas ────────────────────────────────────────────────────────
    regra_ruim = (
        (df["gps_fix"]    < 3                       ) |
        (df["precision"]  > GPSP_RUIM               ) |
        (df["speed2d"]    > MAX_VELOCIDADE           ) |
        (df["accel_ms2"].abs() > MAX_ACELERACAO      ) |
        (df["speed_diff"] > 10                       )
    )

    # ── Anomalia final ────────────────────────────────────────────────────────
    df["anomaly"] = np.where(
        (df["anomaly_ml"] == -1) | regra_ruim, -1, 1
    )

    # ── Score de qualidade para o árbitro (0 = ok) ───────────────────────────
    df["gps_score"] = (
        (df["gps_fix"]    < 3           ).astype(int) +
        (df["precision"]  > GPSP_RUIM   ).astype(int) +
        (df["anomaly"]    == -1         ).astype(int)
    )

    n_ruim = (df["anomaly"] == -1).sum()
    print(f"[DETECTOR] {n_ruim}/{len(df)} amostras anômalas "
          f"({n_ruim / len(df) * 100:.1f}%)")

    return df
