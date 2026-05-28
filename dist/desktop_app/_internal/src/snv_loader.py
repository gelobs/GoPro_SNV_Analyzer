"""
src/snv_loader.py
Carregamento e recorte do shapefile do SNV (DNIT).

Download do shapefile:
  https://www.gov.br/dnit/pt-br/assuntos/planejamento-e-pesquisa/dnit-geo
  Seção: Downloads > Sistema Nacional de Viação > Shapefile
"""
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString


def load_snv(shp_path: str) -> gpd.GeoDataFrame:
    """
    Carrega o shapefile do SNV e normaliza para WGS-84 (EPSG:4326).

    Colunas relevantes do SNV:
      geometry   : LineString da rodovia
      ds_sigla   : sigla da rodovia (ex. BR-101)
      ds_jurisdi : jurisdição (Federal, Estadual)
      dt_atuali  : data da última atualização do trecho
    """
    p = Path(shp_path)
    if not p.exists():
        raise FileNotFoundError(
            f"Shapefile não encontrado: {shp_path}\n"
            "Baixe em: https://www.gov.br/dnit -> DNIT GEO -> Downloads -> SNV\n"
            "Coloque os 4 arquivos (.shp .dbf .prj .shx) em data/snv/"
        )

    gdf = gpd.read_file(str(p), engine="pyogrio")

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
        print("[SNV] CRS ausente no shapefile — assumido WGS-84")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    print(f"[SNV] {len(gdf)} segmentos carregados — CRS: EPSG:4326")
    return gdf


def recortar_snv(snv: gpd.GeoDataFrame,
                 gps_df: pd.DataFrame,
                 buffer_km: float = 0.5) -> gpd.GeoDataFrame:
    """
    Filtra apenas os segmentos SNV dentro de um buffer ao redor da rota gravada.
    Reduz o volume de dados antes de calcular distâncias ponto a ponto.

    Parâmetros:
      snv       : GeoDataFrame completo do SNV
      gps_df    : DataFrame com colunas lat e lon da rota GoPro
      buffer_km : raio do buffer em km (padrão 0.5km)
    """
    rota   = LineString(zip(gps_df["lon"], gps_df["lat"]))
    rota_s = gpd.GeoSeries([rota], crs=4326).to_crs(epsg=32722)
    buf    = rota_s.buffer(buffer_km * 1000).to_crs(epsg=4326).iloc[0]

    recortado = snv[snv.intersects(buf)].copy()

    if len(recortado) == 0:
        raise ValueError(
            f"Nenhum segmento SNV encontrado dentro do buffer de {buffer_km}km.\n"
            "Tente aumentar buffer_km para 1.0 ou 2.0 no validar_rota.py."
        )

    print(f"[SNV] {len(recortado)} segmentos dentro do buffer de {buffer_km}km")
    return recortado
