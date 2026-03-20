"""
src/avaliador_qualidade.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Avaliação de qualidade do sinal GPS da GoPro por segmento.

Fundamentação técnica:
  - GPMF spec (GoPro, 2023): GPS5 @ 18Hz, GPSP = DOP × 100, GPSF ∈ {0,2,3}
  - Nayfeh (2023): features GPSP, fix_type e vel como indicadores de qualidade
  - Newson & Krumm (2009): região de confiança baseada em erro posicional GPS
  - Hero 11 melhorou acurácia GPS vs modelos anteriores (Telemetry Overlay, 2022)

Classificação de qualidade (IQ — Index of Quality):
  IQ_EXCELENTE  : GPSP < 200 (DOP < 2.0), fix=3D, sem anomalias
  IQ_BOM        : GPSP 200–500, fix=3D, < 5% anomalias
  IQ_ACEITAVEL  : GPSP 500–1000 OU fix parcial OU 5–15% anomalias
  IQ_DEGRADADO  : GPSP > 1000 OU fix ausente OU > 15% anomalias

Nota sobre a Hero 12: não possui GPS interno.
  O arquivo MP4 pode conter dados GPS de dispositivo externo pareado.
  Verificar campo 'device_name' no stream GPMF.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class IndiceQualidade(Enum):
    EXCELENTE  = "excelente"    # DOP < 2.0, fix 3D, sem anomalias
    BOM        = "bom"          # DOP < 5.0, fix 3D, < 5% anomalias
    ACEITAVEL  = "aceitavel"    # DOP < 10, fix parcial ou 5–15% anomalias
    DEGRADADO  = "degradado"    # DOP > 10, sem fix ou > 15% anomalias


# Limiares GPSP (DOP × 100) — baseados no GPMF spec e Nayfeh (2023)
GPSP_EXCELENTE  = 200    # DOP < 2.0  — precisão horizontal ~2–5m
GPSP_BOM        = 500    # DOP < 5.0  — precisão horizontal ~5–15m
GPSP_ACEITAVEL  = 1000   # DOP < 10.0 — precisão horizontal ~15–50m
# DOP > 10 = degradado — não usar para georreferenciamento crítico

# Raio de confiança posicional por nível de qualidade (em metros)
# Baseado em: Newson & Krumm (2009) — σ_GPS para receptor civil
RAIO_CONFIANCA = {
    IndiceQualidade.EXCELENTE: 5,
    IndiceQualidade.BOM:       15,
    IndiceQualidade.ACEITAVEL: 50,
    IndiceQualidade.DEGRADADO: 200,
}


@dataclass
class QualidadeSegmento:
    km_inicio:       float
    km_fim:          float
    iq:              IndiceQualidade
    gpsp_medio:      float          # DOP × 100 médio
    gpsp_max:        float
    dop_medio:       float          # gpsp_medio / 100
    pct_fix_3d:      float          # % de amostras com fix 3D
    pct_anomalos:    float          # % marcados pelo Isolation Forest
    raio_confianca_m: int           # raio de confiança posicional (m)
    n_amostras:      int
    vel_media_kmh:   float
    vel_min_kmh:     float
    vel_max_kmh:     float
    nota:            str


def avaliar_qualidade(df: pd.DataFrame,
                      tamanho_seg_km: float = 1.0) -> list:
    """
    Avalia a qualidade do sinal GPS por segmento.

    Parâmetros:
      df             : DataFrame com precision, gps_fix, anomaly, speed2d, km
      tamanho_seg_km : tamanho dos segmentos de análise

    Retorna lista de QualidadeSegmento.
    """
    resultados = []
    km_max = df["km"].max()
    km = 0.0

    while km < km_max:
        seg = df[(df["km"] >= km) & (df["km"] < km + tamanho_seg_km)]
        if len(seg) < 5:
            km += tamanho_seg_km
            continue

        gpsp_med  = seg["precision"].mean()
        gpsp_max  = seg["precision"].max()
        pct_fix   = (seg["gps_fix"] >= 3).mean() * 100
        pct_anom  = (seg["anomaly"] == -1).mean() * 100 if "anomaly" in seg.columns else 0.0
        vel       = seg["speed2d"] * 3.6   # m/s → km/h

        iq = _classificar_iq(gpsp_med, pct_fix, pct_anom)

        resultados.append(QualidadeSegmento(
            km_inicio        = round(km, 2),
            km_fim           = round(min(km + tamanho_seg_km, km_max), 2),
            iq               = iq,
            gpsp_medio       = round(gpsp_med, 0),
            gpsp_max         = round(gpsp_max, 0),
            dop_medio        = round(gpsp_med / 100, 2),
            pct_fix_3d       = round(pct_fix, 1),
            pct_anomalos     = round(pct_anom, 1),
            raio_confianca_m = RAIO_CONFIANCA[iq],
            n_amostras       = len(seg),
            vel_media_kmh    = round(vel.mean(), 1),
            vel_min_kmh      = round(vel.min(), 1),
            vel_max_kmh      = round(vel.max(), 1),
            nota             = _gerar_nota(iq, gpsp_med, pct_fix, pct_anom),
        ))
        km += tamanho_seg_km

    return resultados


def _classificar_iq(gpsp: float, pct_fix: float, pct_anom: float) -> IndiceQualidade:
    """
    Classifica a qualidade do sinal com base em três critérios independentes.
    O pior indicador prevalece (critério conservador).
    """
    # Classifica cada indicador individualmente
    if gpsp > GPSP_ACEITAVEL or pct_fix < 50 or pct_anom > 15:
        return IndiceQualidade.DEGRADADO
    if gpsp > GPSP_BOM or pct_fix < 90 or pct_anom > 5:
        return IndiceQualidade.ACEITAVEL
    if gpsp > GPSP_EXCELENTE or pct_fix < 99 or pct_anom > 0:
        return IndiceQualidade.BOM
    return IndiceQualidade.EXCELENTE


def _gerar_nota(iq: IndiceQualidade, gpsp: float,
                pct_fix: float, pct_anom: float) -> str:
    dop = gpsp / 100
    msgs = {
        IndiceQualidade.EXCELENTE: (
            f"Sinal GPS excelente. DOP={dop:.1f}, fix 3D em {pct_fix:.0f}% das amostras. "
            f"Raio de confiança posicional: ~5m."
        ),
        IndiceQualidade.BOM: (
            f"Sinal GPS bom. DOP={dop:.1f}, fix 3D em {pct_fix:.0f}% das amostras. "
            f"Raio de confiança posicional: ~15m."
        ),
        IndiceQualidade.ACEITAVEL: (
            f"Sinal GPS aceitável. DOP={dop:.1f}, {pct_anom:.0f}% de amostras anômalas. "
            f"Usar com cautela — raio de confiança: ~50m."
        ),
        IndiceQualidade.DEGRADADO: (
            f"Sinal GPS degradado. DOP={dop:.1f}, fix 3D em apenas {pct_fix:.0f}% das amostras, "
            f"{pct_anom:.0f}% de anomalias. Dados desta janela não são confiáveis para validação SNV."
        ),
    }
    return msgs[iq]


IQ_SIMBOLO = {
    IndiceQualidade.EXCELENTE: "●",
    IndiceQualidade.BOM:       "◕",
    IndiceQualidade.ACEITAVEL: "◑",
    IndiceQualidade.DEGRADADO: "○",
}


def imprimir_qualidade(resultados: list) -> None:
    """Imprime o relatório de qualidade do sinal GPS."""
    SEP = "─" * 78
    print(f"\n{SEP}")
    print("  QUALIDADE DO SINAL GPS (GPMF)")
    print(f"  Legenda: ● Excelente  ◕ Bom  ◑ Aceitável  ○ Degradado")
    print(f"  {'Segmento':>14}  {'IQ':>10}  {'DOP':>6}  {'Fix3D':>6}  "
          f"{'Anom%':>6}  {'Conf.R':>7}  {'Vel.(km/h)':>14}")
    print(SEP)

    for r in resultados:
        simb = IQ_SIMBOLO[r.iq]
        print(
            f"  {simb} km {r.km_inicio:5.1f}–{r.km_fim:5.1f}  "
            f"{r.iq.value:>10}  "
            f"{r.dop_medio:>6.2f}  "
            f"{r.pct_fix_3d:>5.0f}%  "
            f"{r.pct_anomalos:>5.0f}%  "
            f"{r.raio_confianca_m:>5}m  "
            f"{r.vel_media_kmh:>5.0f} [{r.vel_min_kmh:.0f}–{r.vel_max_kmh:.0f}]"
        )

    # Resumo
    contagem = {}
    for r in resultados:
        contagem[r.iq.value] = contagem.get(r.iq.value, 0) + 1
    print(SEP)
    print("  Distribuição: " +
          "  ".join(f"{IQ_SIMBOLO[IndiceQualidade(k)]} {k}: {v}" 
                    for k, v in sorted(contagem.items())))
    print(SEP)
