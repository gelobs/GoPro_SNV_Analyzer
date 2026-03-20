"""
testar_ambiente.py
Verifica se todas as dependências do projeto estão instaladas corretamente.
Execute: python testar_ambiente.py
"""
import sys

print("=" * 50)
print(f"Python: {sys.version}")
print("=" * 50)

TESTES = [
    # (nome exibido, módulo a importar, obrigatório)
    ("pandas",        "pandas",       True),
    ("numpy",         "numpy",        True),
    ("scipy",         "scipy",        True),
    ("scikit-learn",  "sklearn",      True),
    ("geopandas",     "geopandas",    True),
    ("pyogrio",       "pyogrio",      True),
    ("shapely",       "shapely",      True),
    ("pyproj",        "pyproj",       True),
    ("gpxpy",         "gpxpy",        True),
    ("ffmpeg-python", "ffmpeg",       True),
    ("gopro2gpx",     "gopro2gpx",    True),
    ("folium",        "folium",       False),
    ("matplotlib",    "matplotlib",   False),
]

ok = 0
erros = []

print("\nPacotes:\n")
for nome, modulo, obrigatorio in TESTES:
    try:
        m = __import__(modulo)
        versao = getattr(m, "__version__", "?")
        print(f"  [OK]   {nome:<18} {versao}")
        ok += 1
    except ImportError as e:
        tag = "OBRIGATORIO" if obrigatorio else "opcional"
        print(f"  [ERRO] {nome:<18} ({tag}) — {e}")
        if obrigatorio:
            erros.append(nome)

print("\nVerificando GeoPandas + pyogrio (leitura shapefile)...")
try:
    import geopandas as gpd
    import geodatasets
    gdf = gpd.read_file(geodatasets.get_path("naturalearth.land"))
    print(f"  [OK]   GeoPandas leu {len(gdf)} features com pyogrio")
except Exception as e:
    print(f"  [ERRO] {e}")
    erros.append("geopandas/pyogrio")

print("\nVerificando reprojeção UTM zona 22S (Sul do Brasil)...")
try:
    import geopandas as gpd
    gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([-48.5], [-27.5]),
        crs=4326
    ).to_crs(epsg=32722)
    x = gdf.geometry.x[0]
    y = gdf.geometry.y[0]
    print(f"  [OK]   (-48.5, -27.5) → UTM ({x:.0f}m E, {y:.0f}m N)")
except Exception as e:
    print(f"  [ERRO] {e}")
    erros.append("reprojecao_utm")

print("\nVerificando Isolation Forest (sklearn)...")
try:
    import numpy as np
    from sklearn.ensemble import IsolationForest
    X = np.random.randn(100, 5)
    pred = IsolationForest(n_estimators=10, random_state=42).fit_predict(X)
    print(f"  [OK]   IsolationForest rodou — {(pred==1).sum()} normais, {(pred==-1).sum()} outliers")
except Exception as e:
    print(f"  [ERRO] {e}")
    erros.append("sklearn")

print("\n" + "=" * 50)
if not erros:
    print("AMBIENTE PRONTO — todos os testes passaram.")
    print("Próximo passo: python validar_rota.py")
else:
    print(f"ATENÇÃO — {len(erros)} pacote(s) com problema:")
    for e in erros:
        print(f"  - {e}")
    print("\nComando para reinstalar:")
    print("  pip install " + " ".join(erros))
print("=" * 50)
