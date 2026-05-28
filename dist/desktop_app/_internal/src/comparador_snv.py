"""
src/comparador_snv.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Comparação métrica entre trajetória GoPro e geometria do SNV (DNIT).

Fundamentação técnica:
  - Map matching probabilístico: região de confiança centrada no ponto GPS
    (Newson & Krumm, ACM GIS 2009; Bierlaire et al., 2013)
  - ST-Matching: restrições geométricas + temporais de velocidade
    (Lou et al., ACM GIS 2009)
  - Ahmed et al. (2015): F-score para avaliação de qualidade de rede viária
  - Nayfeh (2023): sistematicidade da divergência como critério de classificação

Decisão de conformidade por segmento:
  ┌─────────────────┬────────────────────────┬─────────────────────────────┐
  │  dist_max (m)   │  IQ do sinal GPS       │  Conformidade               │
  ├─────────────────┼────────────────────────┼─────────────────────────────┤
  │  < raio_conf    │  qualquer              │  DENTRO_TOLERANCIA          │
  │  < 100m         │  Excelente / Bom       │  DENTRO_TOLERANCIA          │
  │  < 100m         │  Aceitável / Degradado │  INCONCLUSIVO               │
  │  >= 100m        │  Excelente / Bom       │  SNV_DESATUALIZADO          │
  │  >= 100m        │  Degradado             │  SINAL_GPS_INSUFICIENTE     │
  └─────────────────┴────────────────────────┴─────────────────────────────┘

Regra principal: dist_max < 100m → sempre dentro da tolerância.
                 dist_max >= 100m + sinal bom → SNV desatualizado.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from avaliador_qualidade import IndiceQualidade, RAIO_CONFIANCA


class Conformidade(Enum):
    DENTRO_TOLERANCIA       = "dentro_da_tolerancia"
    SNV_DESATUALIZADO       = "snv_desatualizado"
    SINAL_GPS_INSUFICIENTE  = "sinal_gps_insuficiente"
    INCONCLUSIVO            = "inconclusivo"


# Limiar de divergência para SNV desatualizado (user-specified: 100m)
DIST_SNV_DESATUALIZADO_M = 100


@dataclass
class ConformidadeSegmento:
    km_inicio:      float
    km_fim:         float
    conformidade:   Conformidade
    dist_media_m:   float       # distância média GoPro → SNV (m)
    dist_max_m:     float       # distância máxima GoPro → SNV (m)
    dist_p95_m:     float       # percentil 95 das distâncias (m)
    iq_sinal:       IndiceQualidade
    raio_conf_m:    int
    sistematico:    bool        # divergência sistemática (baixo CV, longa extensão)
    cv_distancias:  float       # coeficiente de variação das distâncias
    vel_media_kmh:  float
    n_amostras:     int
    justificativa:  str


def classificar_conformidade(
    df: pd.DataFrame,
    qualidades: list,
    tamanho_seg_km: float = 1.0,
) -> list:
    """
    Classifica a conformidade de cada segmento da rota com o SNV.

    Parâmetros:
      df             : DataFrame com dist_snv_m, km, gps_score, speed2d
      qualidades     : lista de QualidadeSegmento (do avaliador_qualidade)
      tamanho_seg_km : tamanho dos segmentos

    Retorna lista de ConformidadeSegmento.
    """
    # Mapa de qualidade por km_inicio para lookup rápido
    iq_map = {q.km_inicio: q for q in qualidades}

    resultados = []
    km_max = df["km"].max()
    km = 0.0

    while km < km_max:
        seg = df[(df["km"] >= km) & (df["km"] < km + tamanho_seg_km)]
        if len(seg) < 5:
            km += tamanho_seg_km
            continue

        dists     = seg["dist_snv_m"].values
        dist_med  = dists.mean()
        dist_max  = dists.max()
        dist_p95  = np.percentile(dists, 95)
        cv        = dists.std() / (dists.mean() + 1e-9)
        ext_m     = (seg["km"].max() - seg["km"].min()) * 1000
        sistematico = cv < 1.2 and ext_m >= 200 and dist_med > 30

        # Qualidade do sinal neste segmento
        q = iq_map.get(round(km, 2))
        iq = q.iq if q else IndiceQualidade.BOM
        raio = RAIO_CONFIANCA[iq]

        vel_kmh = (seg["speed2d"] * 3.6).mean()

        # Regra de conformidade
        conformidade = _decidir_conformidade(dist_max, iq, raio)

        resultados.append(ConformidadeSegmento(
            km_inicio     = round(km, 2),
            km_fim        = round(min(km + tamanho_seg_km, km_max), 2),
            conformidade  = conformidade,
            dist_media_m  = round(dist_med, 1),
            dist_max_m    = round(dist_max, 1),
            dist_p95_m    = round(dist_p95, 1),
            iq_sinal      = iq,
            raio_conf_m   = raio,
            sistematico   = sistematico,
            cv_distancias = round(cv, 3),
            vel_media_kmh = round(vel_kmh, 1),
            n_amostras    = len(seg),
            justificativa = _justificar(conformidade, dist_med, dist_max,
                                         dist_p95, iq, sistematico, raio),
        ))
        km += tamanho_seg_km

    return resultados


def _decidir_conformidade(dist_max: float,
                           iq: IndiceQualidade,
                           raio_conf_m: int) -> Conformidade:
    """
    Lógica de decisão baseada na distância máxima e qualidade do sinal.

    Usa dist_max (não dist_media) para ser conservador — se qualquer ponto
    do segmento excede 100m do SNV com sinal bom, o SNV está desatualizado.
    """
    # Sinal degradado: não é possível fazer afirmações sobre o SNV
    if iq == IndiceQualidade.DEGRADADO:
        return Conformidade.SINAL_GPS_INSUFICIENTE

    # Dentro do raio de confiança posicional: tolerância do GPS
    if dist_max <= raio_conf_m:
        return Conformidade.DENTRO_TOLERANCIA

    # Abaixo do limiar de 100m: dentro da tolerância operacional
    if dist_max < DIST_SNV_DESATUALIZADO_M:
        if iq == IndiceQualidade.ACEITAVEL:
            return Conformidade.INCONCLUSIVO   # sinal médio + dist moderada
        return Conformidade.DENTRO_TOLERANCIA

    # Acima de 100m com sinal bom/excelente: SNV desatualizado
    return Conformidade.SNV_DESATUALIZADO


def _justificar(conf: Conformidade, dist_med: float, dist_max: float,
                 dist_p95: float, iq: IndiceQualidade,
                 sistematico: bool, raio: int) -> str:
    dop_str = f"(raio de confiança GPS: {raio}m)"
    msgs = {
        Conformidade.DENTRO_TOLERANCIA: (
            f"Distância máxima ao SNV: {dist_max:.0f}m, média: {dist_med:.0f}m. "
            f"Dentro da tolerância operacional {dop_str}. "
            f"Trajetória GoPro compatível com o traçado SNV."
        ),
        Conformidade.SNV_DESATUALIZADO: (
            f"Distância máxima ao SNV: {dist_max:.0f}m (P95: {dist_p95:.0f}m), "
            f"média: {dist_med:.0f}m. "
            f"Sinal GPS {iq.value} {dop_str}. "
            + ("Divergência sistemática — " if sistematico else "Divergência irregular — ")
            + "indicativo de traçado SNV desatualizado ou variante de traçado não catalogada."
        ),
        Conformidade.SINAL_GPS_INSUFICIENTE: (
            f"Sinal GPS {iq.value} — raio de confiança posicional: {raio}m. "
            f"Distância ao SNV ({dist_max:.0f}m) não pode ser avaliada com confiança. "
            f"Verificar condições de captação (cobertura vegetal, viadutos, tuneis)."
        ),
        Conformidade.INCONCLUSIVO: (
            f"Distância máxima: {dist_max:.0f}m. Sinal GPS {iq.value} com raio de confiança {raio}m. "
            f"Não é possível afirmar se a divergência é do SNV ou do GPS. "
            f"Recomenda-se repetir a gravação com sinal de melhor qualidade."
        ),
    }
    return msgs[conf]


# Símbolos e cores para impressão
CONF_SIMBOLO = {
    Conformidade.DENTRO_TOLERANCIA:      "✓",
    Conformidade.SNV_DESATUALIZADO:      "✗",
    Conformidade.SINAL_GPS_INSUFICIENTE: "⚠",
    Conformidade.INCONCLUSIVO:           "?",
}

CONF_LABEL = {
    Conformidade.DENTRO_TOLERANCIA:      "Dentro da tolerância",
    Conformidade.SNV_DESATUALIZADO:      "SNV desatualizado",
    Conformidade.SINAL_GPS_INSUFICIENTE: "Sinal GPS insuficiente",
    Conformidade.INCONCLUSIVO:           "Inconclusivo",
}


def imprimir_conformidade(resultados: list) -> None:
    """Imprime o relatório de conformidade SNV."""
    SEP = "─" * 82
    print(f"\n{SEP}")
    print("  CONFORMIDADE DA TRAJETÓRIA COM O SNV/DNIT")
    print(f"  Limiar de divergência: {DIST_SNV_DESATUALIZADO_M}m  "
          f"| ✓ Conforme  ✗ SNV desatualizado  ⚠ Sinal insuficiente  ? Inconclusivo")
    print(f"  {'Segmento':>14}  {'Conform.':>22}  "
          f"{'dist_med':>8}  {'dist_max':>8}  {'P95':>6}  {'IQ':>10}  "
          f"{'Sist.':>5}  {'Vel.(km/h)':>14}")
    print(SEP)

    for r in resultados:
        simb  = CONF_SIMBOLO[r.conformidade]
        label = CONF_LABEL[r.conformidade]
        sist  = "Sim" if r.sistematico else "Não"
        print(
            f"  {simb} km {r.km_inicio:5.1f}–{r.km_fim:5.1f}  "
            f"{label:>22}  "
            f"{r.dist_media_m:>7.1f}m  "
            f"{r.dist_max_m:>7.1f}m  "
            f"{r.dist_p95_m:>5.1f}m  "
            f"{r.iq_sinal.value:>10}  "
            f"{sist:>5}  "
            f"{r.vel_media_kmh:>5.0f} km/h"
        )

    # Resumo
    SEP2 = "─" * 82
    contagem = {}
    for r in resultados:
        contagem[r.conformidade] = contagem.get(r.conformidade, 0) + 1

    total = sum(contagem.values())
    print(SEP2)
    print("  Resumo de conformidade:")
    for conf, n in contagem.items():
        pct = n / total * 100
        print(f"    {CONF_SIMBOLO[conf]} {CONF_LABEL[conf]:<28} "
              f"{n:>3} segmento(s)  ({pct:.0f}%)")
    print(SEP2)
