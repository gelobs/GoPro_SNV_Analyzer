"""
src/validador_snv_gopro.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Orquestrador do pipeline de validação GoPro × SNV.

Responsabilidade: coordenar os módulos especializados e produzir
o relatório consolidado. Não contém lógica de análise — delega
para os módulos específicos.

Pipeline:
  1. Limpeza de anomalias de sinal GPS       (gp12_features)
  2. Avaliação de qualidade GPS por segmento  (avaliador_qualidade)
  3. Cálculo de distância ao SNV             (shapely/geopandas)
  4. Classificação de conformidade SNV       (comparador_snv)
  5. Diagnóstico de câmera                   (diagnostico_camera)
  6. Impressão dos relatórios
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import nearest_points

from gp12_features     import build_features, detect_anomalies
from avaliador_qualidade import (
    QualidadeSegmento, avaliar_qualidade, imprimir_qualidade
)
from comparador_snv    import (
    ConformidadeSegmento, classificar_conformidade, imprimir_conformidade
)
from diagnostico_camera import (
    EventoDiagnostico, diagnosticar, imprimir_diagnostico
)


def calcular_distancia_ao_snv(gps_df: pd.DataFrame,
                               snv_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Calcula a distância métrica (m) de cada amostra GPS ao trecho SNV
    mais próximo, usando projeção UTM zona 22S para o Sul do Brasil.

    Fundamentação: nearest_points (Shapely) implementa o algoritmo de
    ponto mais próximo em polilinha — equivalente ao step de candidate
    road segments do map-matching probabilístico (Newson & Krumm, 2009).
    """
    df = gps_df.copy()

    gps_utm = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs=4326,
    ).to_crs(epsg=32722)

    snv_utm   = snv_gdf.to_crs(epsg=32722)
    snv_uniao = snv_utm.geometry.union_all()

    dists, lat_near, lon_near = [], [], []
    for pt in gps_utm.geometry:
        d       = pt.distance(snv_uniao)
        pt_prox = nearest_points(pt, snv_uniao)[1]
        dists.append(d)
        lat_near.append(pt_prox.y)
        lon_near.append(pt_prox.x)

    near_gs = gpd.GeoSeries(
        gpd.points_from_xy(lon_near, lat_near), crs=32722
    ).to_crs(epsg=4326)

    df["dist_snv_m"]   = dists
    df["lat_snv_near"] = near_gs.y.values
    df["lon_snv_near"] = near_gs.x.values
    return df


def validar_rota(
    gps_raw:                pd.DataFrame,
    snv_gdf:                gpd.GeoDataFrame,
    tamanho_seg_km:         float = 1.0,
    contamination:          float = 0.04,
    velocidade_esperada_ms: Optional[float] = None,
) -> tuple:
    """
    Executa o pipeline completo de validação.

    Retorna:
      df           : DataFrame com todas as colunas de análise por amostra
      qualidades   : lista de QualidadeSegmento (sinal GPS por segmento)
      conformidades: lista de ConformidadeSegmento (SNV por segmento)
      eventos      : lista de EventoDiagnostico (câmera)
    """
    SEP = "═" * 70
    print(f"\n{SEP}")
    print("  VALIDADOR DE TRAJETÓRIA GoPro × SNV/DNIT")
    print(f"  Segmentos de {tamanho_seg_km}km | "
          f"Limiar SNV: 100m | "
          f"Taxa de contaminação IF: {contamination*100:.0f}%")
    print(SEP)

    # 1. Anomalias de sinal GPS (Isolation Forest + regras físicas)
    print("\n[1/5] Processando sinal GPS (Isolation Forest)...")
    df = build_features(gps_raw)
    df = detect_anomalies(df, contamination=contamination)
    df["gps_score"] = (
        (df["gps_fix"]   < 3    ).astype(int) +
        (df["precision"] > 500  ).astype(int) +
        (df["anomaly"]   == -1  ).astype(int)
    )
    n_anom = (df["anomaly"] == -1).sum()
    print(f"    {n_anom} amostras anômalas detectadas "
          f"({n_anom/len(df)*100:.1f}% do total)")

    # 2. Qualidade GPS por segmento
    print("[2/5] Avaliando qualidade do sinal por segmento...")
    qualidades = avaliar_qualidade(df, tamanho_seg_km)

    # 3. Distância ao SNV
    print("[3/5] Calculando distância ao SNV (projeção UTM 22S)...")
    df = calcular_distancia_ao_snv(df, snv_gdf)
    d_med = df["dist_snv_m"].mean()
    d_max = df["dist_snv_m"].max()
    print(f"    Distância ao SNV — média: {d_med:.1f}m | máxima: {d_max:.1f}m")

    # 4. Conformidade por segmento
    print("[4/5] Classificando conformidade com o SNV...")
    conformidades = classificar_conformidade(df, qualidades, tamanho_seg_km)

    # 5. Diagnóstico de câmera
    print("[5/5] Diagnosticando câmera e gravação...")
    eventos = diagnosticar(df, velocidade_esperada_ms, tamanho_seg_km)

    # Propaga conformidade e qualidade amostra a amostra (para exportação GIS)
    df["conformidade"] = ""
    df["iq_sinal"]     = ""
    for c in conformidades:
        mask = (df["km"] >= c.km_inicio) & (df["km"] < c.km_fim)
        df.loc[mask, "conformidade"] = c.conformidade.value
        df.loc[mask, "iq_sinal"]     = c.iq_sinal.value

    # Imprime relatórios
    imprimir_qualidade(qualidades)
    imprimir_conformidade(conformidades)
    imprimir_diagnostico(eventos)
    _imprimir_sumario(conformidades, qualidades, eventos)

    return df, qualidades, conformidades, eventos


def _imprimir_sumario(conformidades, qualidades, eventos) -> None:
    """Sumário executivo ao final do relatório."""
    from comparador_snv import Conformidade, CONF_LABEL, CONF_SIMBOLO
    from avaliador_qualidade import IndiceQualidade, IQ_SIMBOLO

    SEP = "═" * 70
    print(f"\n{SEP}")
    print("  SUMÁRIO EXECUTIVO")
    print(SEP)

    total_seg = len(conformidades)
    n_conf    = sum(1 for c in conformidades
                    if c.conformidade == Conformidade.DENTRO_TOLERANCIA)
    n_snv     = sum(1 for c in conformidades
                    if c.conformidade == Conformidade.SNV_DESATUALIZADO)
    n_insuf   = sum(1 for c in conformidades
                    if c.conformidade == Conformidade.SINAL_GPS_INSUFICIENTE)
    n_inc     = sum(1 for c in conformidades
                    if c.conformidade == Conformidade.INCONCLUSIVO)

    iq_counts = {}
    for q in qualidades:
        iq_counts[q.iq] = iq_counts.get(q.iq, 0) + 1

    ev_criticos = [e for e in eventos if e.severidade.value == "crítica"]

    dist_maxima_snv = max((c.dist_max_m for c in conformidades), default=0)

    print(f"  Segmentos analisados   : {total_seg}")
    print(f"  Dentro da tolerância   : {n_conf}/{total_seg} "
          f"({n_conf/total_seg*100:.0f}%)")
    if n_snv:
        print(f"  SNV desatualizado      : {n_snv}/{total_seg} segmento(s) — "
              f"dist. máxima: {dist_maxima_snv:.0f}m")
    if n_insuf:
        print(f"  Sinal GPS insuficiente : {n_insuf}/{total_seg} segmento(s)")
    if n_inc:
        print(f"  Inconclusivo           : {n_inc}/{total_seg} segmento(s)")

    print(f"\n  Qualidade do sinal GPS :")
    for iq, n in sorted(iq_counts.items(), key=lambda x: x[0].value):
        print(f"    {IQ_SIMBOLO[iq]} {iq.value:<12} : {n} segmento(s)")

    if ev_criticos:
        print(f"\n  Eventos críticos de câmera ({len(ev_criticos)}):")
        for e in ev_criticos:
            print(f"    ✗ km {e.km_inicio:.1f}–{e.km_fim:.1f}: {e.descricao}")
    else:
        print(f"\n  Câmera: nenhum evento crítico detectado.")

    print(SEP)
