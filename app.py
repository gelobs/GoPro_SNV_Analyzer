"""
app.py  —  Servidor Flask para a interface web do Video GoPro Analyzer x DNIT SNV
Uso: python app.py
Acesse: http://localhost:5000
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

# ── Paths do projeto (resolve() garante caminho absoluto) ────────────────────
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR  = BASE_DIR / "src"
DATA_DIR = BASE_DIR / "data"
SNV_DIR  = DATA_DIR / "snv"
RAW_DIR  = DATA_DIR / "raw"
OUT_DIR  = BASE_DIR / "output"

# Cria pastas se não existirem
for d in [SNV_DIR, RAW_DIR, OUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

@app.after_request
def no_cache(response):
    """Desabilita cache do navegador — garante que o JS mais recente seja carregado."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    return response

# Fila de log para streaming SSE
_log_queue: queue.Queue = queue.Queue()
_processo_atual: dict = {"proc": None, "rodando": False}


# ── Mapa firmware GoPro → nome do modelo ─────────────────────────────────────
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

def _detectar_modelo_gopro(tags: dict) -> str:
    """Detecta o modelo da GoPro a partir do campo firmware do ffprobe."""
    fw = tags.get("firmware", tags.get("com.gopro.firmware_version", ""))
    prefixo = fw[:3].upper() if fw else ""
    if prefixo in FIRMWARE_MODELO:
        return FIRMWARE_MODELO[prefixo]
    model = tags.get("com.android.model", tags.get("model", ""))
    if model and model.upper() not in ("GOPRO", ""):
        return model
    return f"GoPro ({prefixo})" if prefixo else "GoPro"


# ── Valores padrão dos limiares do backend (lidos dos arquivos fonte) ─────────
DEFAULTS = {
    # gp12_features.py
    "gpsp_ruim":        250,
    "max_velocidade":   60,
    "max_aceleracao":   15,
    "max_salto_frames": 40,
    # diagnostico_camera.py
    "gap_critico_s":        30,
    "gap_moderado_s":        5,
    "pontos_sem_variacao":  54,
    "janela_bateria_n":    180,
    "delta_gpsp_bateria":  150,
    "queda_vel_bateria":  0.40,
    "vel_encerramento_ms": 5.0,
    "salto_max_m":        25.0,
    "vel_minima_ms":       3.0,
    "vel_maxima_ms":      55.5,
    # avaliador_qualidade.py
    "gpsp_excelente":   200,
    "gpsp_bom":         500,
    "gpsp_aceitavel":  1000,
    "raio_excelente":    5,
    "raio_bom":         15,
    "raio_aceitavel":   50,
    "raio_degradado":  200,
    # comparador_snv.py
    "dist_snv_desatualizado_m": 100,
}


# ── Rotas da API ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/defaults")
def get_defaults():
    return jsonify(DEFAULTS)


@app.route("/api/listar_shp")
def listar_shp():
    # Busca recursiva em todo o projeto por *.shp / *.SHP
    encontrados = {}
    for f in BASE_DIR.rglob("*"):
        if f.suffix.lower() == ".shp" and f.is_file():
            encontrados[f.name] = str(f)
    return jsonify([{"name": k, "path": v} for k, v in sorted(encontrados.items())])


@app.route("/api/listar_mp4")
def listar_mp4():
    # Busca recursiva em todo o projeto por *.mp4 / *.MP4
    encontrados = {}
    for f in BASE_DIR.rglob("*"):
        if f.suffix.lower() == ".mp4" and f.is_file():
            encontrados[f.name] = str(f)
    return jsonify([{"name": k, "path": v} for k, v in sorted(encontrados.items())])




@app.route("/api/geojson/<tipo>")
def servir_geojson(tipo):
    """
    Serve os GeoJSONs gerados pelo processamento.
    tipo: pontos | segmentos | eventos_camera
    """
    # Busca o arquivo mais recente do tipo solicitado
    padrao = f"*_{tipo}.geojson"
    arquivos = sorted(
        BASE_DIR.rglob(padrao),
        key=lambda f: f.stat().st_mtime
    )
    if not arquivos:
        return jsonify({"erro": f"Nenhum arquivo {padrao} encontrado"}), 404

    import json
    arq = arquivos[-1]
    try:
        with open(arq, encoding="utf-8") as f:
            data = json.load(f)

        # Limpa NaN dos properties
        import math
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            for k, v in props.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    props[k] = None
        return jsonify(data)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/listar_geojsons")
def listar_geojsons():
    """Lista os GeoJSONs disponíveis no output."""
    tipos = ["pontos", "segmentos", "eventos_camera"]
    resultado = {}
    for tipo in tipos:
        arqs = sorted(BASE_DIR.rglob(f"*_{tipo}.geojson"), key=lambda f: f.stat().st_mtime)
        resultado[tipo] = str(arqs[-1]) if arqs else None
    return jsonify(resultado)


@app.route("/api/snv_geojson", methods=["POST"])
def snv_geojson():
    """
    Converte o shapefile SNV selecionado em GeoJSON,
    filtrado ao bounding box da rota processada (para não sobrecarregar o mapa).
    """
    shp      = request.json.get("shp")
    shp_full = request.json.get("path")
    bbox     = request.json.get("bbox")   # [minLon, minLat, maxLon, maxLat] opcional

    if not shp:
        return jsonify({"erro": "Arquivo não informado"}), 400

    shp_path = Path(shp_full) if shp_full else SNV_DIR / shp
    if not shp_path.exists():
        return jsonify({"erro": f"Shapefile não encontrado: {shp_path}"}), 404

    try:
        import geopandas as gpd
        import json, math

        gdf = gpd.read_file(str(shp_path), engine="pyogrio")

        # Reprojeta para WGS-84
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        # Filtra por bbox se fornecido (evita enviar o SNV inteiro)
        if bbox and len(bbox) == 4:
            from shapely.geometry import box as sbox
            regiao = sbox(bbox[0]-0.05, bbox[1]-0.05, bbox[2]+0.05, bbox[3]+0.05)
            gdf = gdf[gdf.intersects(regiao)].copy()

        # Simplifica geometrias para reduzir o tamanho do GeoJSON
        gdf["geometry"] = gdf["geometry"].simplify(0.0001, preserve_topology=True)

        # Seleciona apenas colunas úteis para o mapa
        colunas_mapa = []
        for col in ["ds_sigla","nm_rod","nome_rod","sigla","codigo"]:
            if col in gdf.columns:
                colunas_mapa.append(col)
                break
        colunas_mapa = colunas_mapa[:3]  # máximo 3 atributos no popup

        gdf_out = gdf[colunas_mapa + ["geometry"]] if colunas_mapa else gdf[["geometry"]]

        # Converte para GeoJSON dict e limpa NaN
        gj = json.loads(gdf_out.to_json())
        for feat in gj.get("features", []):
            props = feat.get("properties", {})
            for k, v in props.items():
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    props[k] = None

        return jsonify({
            "geojson":  gj,
            "features": len(gj.get("features", [])),
            "shp_name": shp,
        })
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/api/debug")
def debug():
    """Diagnóstico: mostra os paths usados e arquivos encontrados."""
    mp4s = [{"name": f.name, "path": str(f)}
            for f in BASE_DIR.rglob("*")
            if f.suffix.lower() == ".mp4" and f.is_file()]
    shps = [{"name": f.name, "path": str(f)}
            for f in BASE_DIR.rglob("*")
            if f.suffix.lower() == ".shp" and f.is_file()]
    return jsonify({
        "BASE_DIR": str(BASE_DIR),
        "SNV_DIR":  str(SNV_DIR),
        "RAW_DIR":  str(RAW_DIR),
        "mp4_encontrados": mp4s,
        "shp_encontrados": shps,
        "mp4_count": len(mp4s),
        "shp_count": len(shps),
    })

@app.route("/api/info_gopro", methods=["POST"])
def info_gopro():
    """Extrai metadados técnicos do arquivo MP4 via gopro2gpx."""
    mp4      = request.json.get("mp4")
    mp4_full = request.json.get("path")
    if not mp4:
        return jsonify({"erro": "Arquivo não informado"})

    mp4_path = Path(mp4_full) if mp4_full else RAW_DIR / mp4
    if not mp4_path.exists():
        return jsonify({"erro": f"Arquivo não encontrado: {mp4_path}"})

    try:
        # Usa ffprobe para obter metadados
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(mp4_path)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        info = json.loads(r.stdout) if r.stdout else {}

        tags     = info.get("format", {}).get("tags", {})
        streams  = info.get("streams", [])
        duration = float(info.get("format", {}).get("duration", 0))
        size_mb  = mp4_path.stat().st_size / (1024 * 1024)

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        fps_str = video_stream.get("r_frame_rate", "0/1")
        fps_parts = fps_str.split("/")
        fps = round(int(fps_parts[0]) / int(fps_parts[1]), 1) if len(fps_parts) == 2 else 0

        return jsonify({
            "modelo":       _detectar_modelo_gopro(tags),
            "firmware":     tags.get("firmware", tags.get("com.gopro.firmware_version", "—")),
            "duracao_s":    round(duration, 1),
            "duracao_fmt":  _fmt_duracao(duration),
            "tamanho_mb":   round(size_mb, 1),
            "resolucao":    f"{video_stream.get('width','?')}×{video_stream.get('height','?')}",
            "fps":          fps,
            "criacao":      tags.get("creation_time", "—"),
        })
    except Exception as e:
        return jsonify({"erro": str(e)})


@app.route("/api/info_snv", methods=["POST"])
def info_snv():
    """Lê metadados do shapefile SNV."""
    shp      = request.json.get("shp")
    shp_full = request.json.get("path")
    if not shp:
        return jsonify({"erro": "Arquivo não informado"})

    shp_path = Path(shp_full) if shp_full else SNV_DIR / shp
    if not shp_path.exists():
        return jsonify({"erro": f"Arquivo não encontrado: {shp_path}"})

    try:
        import geopandas as gpd
        gdf  = gpd.read_file(str(shp_path), engine="pyogrio", rows=5)
        full = gpd.read_file(str(shp_path), engine="pyogrio")
        colunas = list(gdf.columns)
        colunas = [c for c in colunas if c != "geometry"]

        # Detecta campos de data/versão comuns no SNV DNIT
        data_col = next((c for c in colunas
                         if any(k in c.lower() for k in ["data","dt_","date","versao","revisao"])),
                        None)
        sig_col  = next((c for c in colunas
                         if any(k in c.lower() for k in
                            ["sigla","ds_sigla","nome_rod","nm_rod","rodovia",
                             "codigo","cd_rod","br_","snv_","nome"])),
                        None)

        return jsonify({
            "num_segmentos": len(full),
            "crs":           str(full.crs),
            "colunas":       colunas[:10],
            "data_atualizacao": str(full[data_col].max()) if data_col else "—",
            "rodovias":      sorted(full[sig_col].dropna().unique().tolist())[:20]
                             if sig_col else [],
            "tamanho_kb":    round(shp_path.stat().st_size / 1024, 1),
        })
    except Exception as e:
        return jsonify({"erro": str(e)})


@app.route("/api/velocidade_mediana", methods=["POST"])
def velocidade_mediana():
    """Calcula velocidade mediana do vídeo a partir do GPS."""
    mp4      = request.json.get("mp4")
    mp4_full = request.json.get("path")
    if not mp4:
        return jsonify({"velocidade_kmh": None})

    mp4_path = Path(mp4_full) if mp4_full else RAW_DIR / mp4
    if not mp4_path.exists():
        return jsonify({"velocidade_kmh": None, "erro": f"Arquivo não encontrado: {mp4_path}"})

    try:
        sys.path.insert(0, str(SRC_DIR))
        from gp12_gps_extractor import extract_hero12_gps
        df  = extract_hero12_gps(str(mp4_path))
        med = round(df["speed2d"].median() * 3.6, 1)
        return jsonify({"velocidade_kmh": med})
    except Exception as e:
        return jsonify({"velocidade_kmh": None, "erro": str(e)})


@app.route("/api/processar", methods=["POST"])
def processar():
    """Inicia o processamento em thread separada com streaming de log."""
    if _processo_atual["rodando"]:
        return jsonify({"erro": "Processamento já em andamento."}), 409

    params = request.json
    # Limpa a fila
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except queue.Empty:
            break

    thread = threading.Thread(target=_executar_pipeline, args=(params,), daemon=True)
    thread.start()
    return jsonify({"status": "iniciado"})


@app.route("/api/cancelar", methods=["POST"])
def cancelar():
    """Cancela o processamento em andamento."""
    if _processo_atual.get("proc"):
        try:
            _processo_atual["proc"].terminate()
        except Exception:
            pass
    _processo_atual["rodando"] = False
    _log_queue.put({"tipo": "aviso", "msg": "Processamento cancelado pelo usuário."})
    _log_queue.put({"tipo": "fim", "msg": ""})
    return jsonify({"status": "cancelado"})


@app.route("/api/log_stream")
def log_stream():
    """SSE — envia logs do processamento em tempo real."""
    def gerar():
        while True:
            try:
                item = _log_queue.get(timeout=30)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("tipo") == "fim":
                    break
            except queue.Empty:
                yield "data: {\"tipo\": \"ping\"}\n\n"
    return Response(
        stream_with_context(gerar()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/resultado")
def resultado():
    """Retorna o resultado do último processamento."""
    # Busca o CSV mais recente em todo o projeto
    csv_path = sorted(
        list(BASE_DIR.rglob("*_relatorio.csv")),
        key=lambda f: f.stat().st_mtime
    )
    if not csv_path:
        return jsonify({"segmentos": [], "eventos": [], "aviso": "Nenhum CSV encontrado"})

    csv_path = csv_path[-1]
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        # Substitui NaN/Inf por None antes de serializar para JSON
        df = df.where(df.notna(), other=None)

        segs  = df[df["origem"] == "SEGMENTO"].to_dict("records")
        evts  = df[df["origem"] == "EVENTO_CAMERA"].to_dict("records")

        # Limpa NaN remanescentes (float nan não capturado pelo where)
        def limpar(lst):
            import math
            out = []
            for row in lst:
                clean = {}
                for k, v in row.items():
                    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                        clean[k] = None
                    else:
                        clean[k] = v
                out.append(clean)
            return out

        return jsonify({
            "segmentos":    limpar(segs),
            "eventos":      limpar(evts),
            "arquivo":      csv_path.name,
            "csv_path":     str(csv_path),
            "total_linhas": len(df),
        })
    except Exception as e:
        return jsonify({"erro": str(e)})


# ── Pipeline de processamento ─────────────────────────────────────────────────

def _executar_pipeline(params: dict):
    """Executa validar_rota.py como subprocesso com log em tempo real."""
    _processo_atual["rodando"] = True

    def log(tipo, msg):
        _log_queue.put({"tipo": tipo, "msg": msg})

    try:
        mp4      = params.get("mp4")
        mp4_path_str = params.get("mp4_path") or str(RAW_DIR / mp4) if mp4 else None
        shp      = params.get("shp")
        shp_path_str = params.get("shp_path") or str(SNV_DIR / shp) if shp else None
        saida_raw = params.get("saida", "output/validacao")
        # Garante caminho absoluto para o prefixo de saída
        saida = str(BASE_DIR / saida_raw) if not Path(saida_raw).is_absolute() else saida_raw
        seg_km   = params.get("tamanho_seg_km", 1.0)
        vel      = params.get("velocidade_kmh", 80)
        avancado = params.get("avancado", {})

        if not mp4 or not shp:
            log("erro", "MP4 e SNV são obrigatórios.")
            return

        log("info", f"Iniciando processamento...")
        log("info", f"Vídeo  : {mp4_path_str}")
        log("info", f"SNV    : {shp_path_str}")
        log("info", f"Saída  : {saida}")
        log("info", f"Segmento: {seg_km}km | Velocidade ref.: {vel}km/h")

        # Gera validar_rota_temp.py com os parâmetros e limiares do usuário
        script = _gerar_script_temp(
            mp4_path  = mp4_path_str,
            shp_path  = shp_path_str,
            prefixo   = saida,
            seg_km    = seg_km,
            vel_kmh   = vel,
            avancado  = avancado,
        )

        script_path = BASE_DIR / "_validar_temp.py"
        script_path.write_text(script, encoding="utf-8")

        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(BASE_DIR),
        )
        _processo_atual["proc"] = proc

        for line in proc.stdout:
            linha = line.rstrip()
            if not linha:
                continue
            tipo = ("erro"  if any(k in linha for k in ["Traceback","Error","ERRO"]) else
                    "aviso" if any(k in linha for k in ["AVISO","WARNING","⚠"]) else
                    "ok"    if any(k in linha for k in ["✓","OK","EXPORT","Conclu"]) else
                    "info")
            log(tipo, linha)

        proc.wait()
        script_path.unlink(missing_ok=True)

        if proc.returncode == 0:
            log("ok", "Processamento concluído com sucesso.")
        else:
            log("erro", f"Processo encerrou com código {proc.returncode}.")

    except Exception as e:
        log("erro", f"Erro interno: {e}")
    finally:
        _processo_atual["rodando"] = False
        _processo_atual["proc"]    = None
        _log_queue.put({"tipo": "fim", "msg": ""})


def _gerar_script_temp(mp4_path, shp_path, prefixo,
                        seg_km, vel_kmh, avancado) -> str:
    """Gera script Python temporário com os parâmetros do usuário."""
    av = avancado or {}

    # Sobrescritas de limiares (aplicadas via monkey-patch)
    patches = []
    mapa_modulo = {
        "gpsp_ruim":            ("gp12_features",      "GPSP_RUIM"),
        "max_velocidade":       ("gp12_features",      "MAX_VELOCIDADE"),
        "max_aceleracao":       ("gp12_features",      "MAX_ACELERACAO"),
        "gap_critico_s":        ("diagnostico_camera", "GAP_CRITICO_S"),
        "gap_moderado_s":       ("diagnostico_camera", "GAP_MODERADO_S"),
        "pontos_sem_variacao":  ("diagnostico_camera", "PONTOS_SEM_VARIACAO"),
        "janela_bateria_n":     ("diagnostico_camera", "JANELA_BATERIA_N"),
        "delta_gpsp_bateria":   ("diagnostico_camera", "DELTA_GPSP_BATERIA"),
        "queda_vel_bateria":    ("diagnostico_camera", "QUEDA_VEL_BATERIA"),
        "vel_encerramento_ms":  ("diagnostico_camera", "VEL_ENCERRAMENTO_MS"),
        "salto_max_m":          ("diagnostico_camera", "SALTO_MAX_M"),
        "vel_minima_ms":        ("diagnostico_camera", "VEL_MINIMA_MS"),
        "vel_maxima_ms":        ("diagnostico_camera", "VEL_MAXIMA_MS"),
        "gpsp_excelente":       ("avaliador_qualidade","GPSP_EXCELENTE"),
        "gpsp_bom":             ("avaliador_qualidade","GPSP_BOM"),
        "gpsp_aceitavel":       ("avaliador_qualidade","GPSP_ACEITAVEL"),
        "dist_snv_desatualizado_m": ("comparador_snv", "DIST_SNV_DESATUALIZADO_M"),
    }
    for chave, valor in av.items():
        if chave in mapa_modulo and valor is not None:
            modulo, constante = mapa_modulo[chave]
            patches.append(f"import {modulo}; {modulo}.{constante} = {valor}")

    patches_str = "\n".join(patches)

    return f"""import sys
sys.path.insert(0, r'{str(SRC_DIR)}')
{patches_str}

from snv_loader          import load_snv, recortar_snv
from gp12_gps_extractor  import extract_hero12_gps
from validador_snv_gopro import validar_rota
from exportador          import exportar_para_gis

print("Carregando SNV...")
snv = load_snv(r'{shp_path}')
print("Extraindo GPS da GoPro...")
gps = extract_hero12_gps(r'{mp4_path}')
print("Recortando SNV...")
snv_t = recortar_snv(snv, gps, buffer_km=0.5)
df, qual, conf, evts = validar_rota(
    gps, snv_t,
    tamanho_seg_km={seg_km},
    velocidade_esperada_ms={vel_kmh/3.6:.4f},
)
exportar_para_gis(df, qual, conf, evts, prefixo=r'{prefixo}')
print("Concluído.")
"""


def _fmt_duracao(segundos: float) -> str:
    s = int(segundos)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Video GoPro Analyzer X DNIT SNV")
    print("  Servidor: http://localhost:5000")
    print("=" * 55)
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
