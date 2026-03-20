"""
src/exportador.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Exportação dos resultados de validação para formatos GIS e tabulares.

Arquivos gerados:
  _pontos.geojson     : todas as amostras GPS com atributos de qualidade
                        e conformidade (abre no QGIS com simbologia por campo)
  _segmentos.geojson  : linha por segmento colorida por conformidade
  _camera.geojson     : marcadores pontuais de eventos de câmera
  _relatorio.csv      : tabela unificada de qualidade + conformidade + câmera
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from comparador_snv import Conformidade
from avaliador_qualidade import IndiceQualidade
from diagnostico_camera import Severidade

# Paleta de cores por conformidade (compatível com QGIS)
CORES_CONFORMIDADE = {
    "dentro_da_tolerancia":    "#2ECC71",   # verde
    "snv_desatualizado":       "#E74C3C",   # vermelho
    "sinal_gps_insuficiente":  "#F39C12",   # laranja
    "inconclusivo":            "#95A5A6",   # cinza
}

# Paleta de cores por qualidade GPS
CORES_IQ = {
    "excelente":  "#1ABC9C",
    "bom":        "#3498DB",
    "aceitavel":  "#F39C12",
    "degradado":  "#E74C3C",
}

# Paleta de cores por severidade de evento de câmera
CORES_EVENTO = {
    "crítica":   "#C0392B",
    "alta":      "#E67E22",
    "moderada":  "#BDC3C7",
}


def exportar_para_gis(
    df: pd.DataFrame,
    qualidades: list,
    conformidades: list,
    eventos: list,
    prefixo: str = "output/validacao",
) -> None:
    """
    Gera todos os arquivos de saída.

    Parâmetros:
      df            : DataFrame com todas as colunas (lat, lon, dist_snv_m, etc.)
      qualidades    : lista de QualidadeSegmento
      conformidades : lista de ConformidadeSegmento
      eventos       : lista de EventoDiagnostico
      prefixo       : prefixo de caminho para os arquivos de saída
    """
    Path(prefixo).parent.mkdir(parents=True, exist_ok=True)

    _exportar_pontos(df, prefixo)
    _exportar_segmentos(df, conformidades, qualidades, prefixo)
    _exportar_eventos_camera(df, eventos, prefixo)
    _exportar_relatorio_csv(qualidades, conformidades, eventos, prefixo)


def _exportar_pontos(df: pd.DataFrame, prefixo: str) -> None:
    """
    GeoJSON de pontos com todos os atributos de análise.
    Colunas úteis para simbologia no QGIS:
      - conformidade : texto (usar regras de simbologia por valor)
      - iq_sinal     : texto
      - dist_snv_m   : numérico (usar gradiente de cores)
      - gps_score    : 0–3 (0 = sinal perfeito)
    """
    cols = [c for c in [
        "timestamp","km","lat","lon",
        "alt","speed2d","precision","gps_fix",
        "dist_snv_m","lat_snv_near","lon_snv_near",
        "gps_score","anomaly","conformidade","iq_sinal"
    ] if c in df.columns]

    out = df[cols].copy()
    out["timestamp"]  = out["timestamp"].astype(str)
    out["cor"]        = out["conformidade"].map(CORES_CONFORMIDADE).fillna("#95A5A6")
    out["vel_kmh"]    = (out["speed2d"] * 3.6).round(1)
    out["dop"]        = (out["precision"] / 100).round(2)

    gdf = gpd.GeoDataFrame(
        out,
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs=4326,
    )

    arq = f"{prefixo}_pontos.geojson"
    gdf.to_file(arq, driver="GeoJSON")
    print(f"[EXPORT] {arq}  ({len(gdf)} pontos)")


def _exportar_segmentos(df: pd.DataFrame, conformidades: list,
                         qualidades: list, prefixo: str) -> None:
    """
    GeoJSON de linhas por segmento com atributos de conformidade e qualidade.
    Ideal para simbologia por categoria no QGIS.
    """
    if not conformidades:
        print("[EXPORT] Sem segmentos para exportar.")
        return

    iq_map = {q.km_inicio: q for q in qualidades}
    linhas = []

    for c in conformidades:
        seg = df[(df["km"] >= c.km_inicio) & (df["km"] < c.km_fim)]
        if len(seg) < 2:
            continue
        q = iq_map.get(c.km_inicio)
        linhas.append({
            "km_inicio":       c.km_inicio,
            "km_fim":          c.km_fim,
            "conformidade":    c.conformidade.value,
            "iq_sinal":        c.iq_sinal.value,
            "dist_media_m":    c.dist_media_m,
            "dist_max_m":      c.dist_max_m,
            "dist_p95_m":      c.dist_p95_m,
            "raio_conf_m":     c.raio_conf_m,
            "sistematico":     c.sistematico,
            "vel_media_kmh":   c.vel_media_kmh,
            "n_amostras":      c.n_amostras,
            "gpsp_medio":      q.gpsp_medio if q else None,
            "dop_medio":       q.dop_medio  if q else None,
            "pct_fix_3d":      q.pct_fix_3d if q else None,
            "justificativa":   c.justificativa,
            "cor":             CORES_CONFORMIDADE.get(c.conformidade.value, "#95A5A6"),
            "geometry":        LineString(zip(seg["lon"], seg["lat"])),
        })

    gdf = gpd.GeoDataFrame(
        linhas,
        geometry=[l["geometry"] for l in linhas],
        crs=4326,
    )
    arq = f"{prefixo}_segmentos.geojson"
    gdf.to_file(arq, driver="GeoJSON")
    print(f"[EXPORT] {arq}  ({len(gdf)} segmentos)")


def _exportar_eventos_camera(df: pd.DataFrame, eventos: list,
                               prefixo: str) -> None:
    """
    GeoJSON de marcadores pontuais de eventos de câmera.
    Posicionado no ponto GPS do km de início do evento.
    """
    arq = f"{prefixo}_eventos_camera.geojson"
    if not eventos:
        print(f"[EXPORT] {arq}  (sem eventos de câmera)")
        return

    marcadores = []
    for e in eventos:
        seg = df[df["km"] >= e.km_inicio]
        if seg.empty:
            continue
        pt = seg.iloc[0]
        marcadores.append({
            "evento":      e.evento.value,
            "severidade":  e.severidade.value,
            "km_inicio":   e.km_inicio,
            "km_fim":      e.km_fim,
            "descricao":   e.descricao,
            "metrica":     e.metrica,
            "acao":        e.acao,
            "cor":         CORES_EVENTO.get(e.severidade.value, "#BDC3C7"),
            "geometry":    Point(pt["lon"], pt["lat"]),
        })

    gdf = gpd.GeoDataFrame(
        marcadores,
        geometry=[m["geometry"] for m in marcadores],
        crs=4326,
    )
    gdf.to_file(arq, driver="GeoJSON")
    print(f"[EXPORT] {arq}  ({len(gdf)} eventos)")


def _exportar_relatorio_csv(qualidades: list, conformidades: list,
                              eventos: list, prefixo: str) -> None:
    """
    CSV unificado com uma linha por segmento (qualidade + conformidade)
    e linhas adicionais para eventos de câmera.
    Abre diretamente no Excel com formatação adequada.
    """
    c_map = {c.km_inicio: c for c in conformidades}
    linhas = []

    for q in qualidades:
        c = c_map.get(q.km_inicio)
        linhas.append({
            "origem":           "SEGMENTO",
            "km_inicio":        q.km_inicio,
            "km_fim":           q.km_fim,
            # Qualidade GPS
            "iq_sinal":         q.iq.value,
            "gpsp_medio":       q.gpsp_medio,
            "dop_medio":        q.dop_medio,
            "pct_fix_3d":       q.pct_fix_3d,
            "pct_anomalos":     q.pct_anomalos,
            "raio_conf_m":      q.raio_confianca_m,
            # Velocidade
            "vel_media_kmh":    q.vel_media_kmh,
            "vel_min_kmh":      q.vel_min_kmh,
            "vel_max_kmh":      q.vel_max_kmh,
            # Conformidade SNV
            "conformidade":     c.conformidade.value if c else "",
            "dist_media_m":     c.dist_media_m       if c else "",
            "dist_max_m":       c.dist_max_m         if c else "",
            "dist_p95_m":       c.dist_p95_m         if c else "",
            "sistematico":      c.sistematico         if c else "",
            "n_amostras":       q.n_amostras,
            "justificativa":    (c.justificativa if c else q.nota),
        })

    for e in eventos:
        linhas.append({
            "origem":          "EVENTO_CAMERA",
            "km_inicio":       e.km_inicio,
            "km_fim":          e.km_fim,
            "iq_sinal":        "",
            "gpsp_medio":      "",
            "dop_medio":       "",
            "pct_fix_3d":      "",
            "pct_anomalos":    "",
            "raio_conf_m":     "",
            "vel_media_kmh":   "",
            "vel_min_kmh":     "",
            "vel_max_kmh":     "",
            "conformidade":    e.evento.value,
            "dist_media_m":    "",
            "dist_max_m":      "",
            "dist_p95_m":      "",
            "sistematico":     "",
            "n_amostras":      "",
            "justificativa":   (
                f"[{e.severidade.value.upper()}] {e.descricao} | "
                f"Métrica: {e.metrica} | Ação: {e.acao}"
            ),
        })

    arq = f"{prefixo}_relatorio.csv"
    (pd.DataFrame(linhas)
       .sort_values(["km_inicio","origem"])
       .to_csv(arq, index=False, encoding="utf-8-sig"))
    print(f"[EXPORT] {arq}  ({len(linhas)} linhas)")
