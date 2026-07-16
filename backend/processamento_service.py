import contextlib
import importlib
import json
import queue
import sys
from pathlib import Path

from backend.app_paths import BASE_DIR, SNV_DIR, SRC_DIR
from backend.snv_service import rodovias_snv_processadas


DEFAULTS = {
    "gpsp_ruim": 250,
    "max_velocidade": 60,
    "max_aceleracao": 15,
    "gap_critico_s": 30,
    "gap_moderado_s": 5,
    "pontos_sem_variacao": 54,
    "janela_bateria_n": 180,
    "delta_gpsp_bateria": 150,
    "queda_vel_bateria": 0.40,
    "vel_encerramento_ms": 5.0,
    "encerramento_tol_final_km": 0.05,
    "salto_max_m": 100.0,
    "vel_maxima_ms": 55.5,
    "gpsp_excelente": 200,
    "gpsp_bom": 500,
    "gpsp_aceitavel": 1000,
    "dist_snv_desatualizado_m": 100,
}

LOG_QUEUE: queue.Queue = queue.Queue()
PROCESSO_ATUAL: dict = {"proc": None, "rodando": False}


def parse_float_param(valor, padrao: float) -> float:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return padrao


def executar_pipeline(params: dict):
    PROCESSO_ATUAL["rodando"] = True

    def log(tipo, msg):
        LOG_QUEUE.put({"tipo": tipo, "msg": msg})

    try:
        mp4 = params.get("mp4")
        mp4_path_str = params.get("mp4_path") or str((BASE_DIR / "data" / "raw") / mp4) if mp4 else None
        shp = params.get("shp")
        shp_path_str = params.get("shp_path") or str(SNV_DIR / shp) if shp else None
        saida_raw = params.get("saida", "output/validacao")
        saida_dir = resolver_diretorio_saida(saida_raw)
        saida = str(saida_dir / "validacao")
        seg_km = parse_float_param(params.get("tamanho_seg_km", 1.0), 1.0)
        avancado = params.get("avancado", {})

        if not mp4 or not shp:
            log("erro", "MP4 e SNV sao obrigatorios.")
            return

        log("info", "Iniciando processamento...")
        log("info", f"Video  : {mp4_path_str}")
        log("info", f"SNV    : {shp_path_str}")
        log("info", f"Saida  : {saida_dir}")
        log("info", f"Segmento: {seg_km}km")

        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))

        aplicar_limiares_avancados(avancado)

        from snv_loader import load_snv, recortar_snv
        from gp12_gps_extractor import extract_hero12_gps
        from validador_snv_gopro import validar_rota
        from exportador import exportar_para_gis

        writer = LogWriter(log)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            print("Carregando SNV...")
            snv = load_snv(shp_path_str)
            print("Extraindo GPS da GoPro...")
            gps = extract_hero12_gps(mp4_path_str)
            print("Recortando SNV...")
            snv_t = recortar_snv(snv, gps, buffer_km=0.5)
            rodovias_processadas = rodovias_snv_processadas(snv_t)
            df, qual, conf, evts = validar_rota(
                gps, snv_t,
                tamanho_seg_km=seg_km,
            )
            exportar_para_gis(df, qual, conf, evts, prefixo=saida)
            meta_path = Path(f"{saida}_meta.json")
            meta_path.write_text(
                json.dumps({"rodovias_processadas": rodovias_processadas}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print("Concluido.")

        log("ok", "Processamento concluido com sucesso.")

    except Exception as e:
        log("erro", f"Erro interno: {e}")
    finally:
        PROCESSO_ATUAL["rodando"] = False
        PROCESSO_ATUAL["proc"] = None
        LOG_QUEUE.put({"tipo": "fim", "msg": ""})


class LogWriter:
    def __init__(self, log):
        self.log = log
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._emit(line)

    def flush(self):
        if self.buffer:
            self._emit(self.buffer)
            self.buffer = ""

    def _emit(self, line):
        linha = line.rstrip()
        if not linha:
            return
        tipo = ("erro" if any(k in linha for k in ["Traceback", "Error", "ERRO"]) else
                "aviso" if any(k in linha for k in ["AVISO", "WARNING", "⚠"]) else
                "ok" if any(k in linha for k in ["✓", "OK", "EXPORT", "Conclu"]) else
                "info")
        self.log(tipo, linha)


def aplicar_limiares_avancados(avancado):
    av = avancado or {}
    mapa_modulo = {
        "gpsp_ruim": ("gp12_features", "GPSP_RUIM"),
        "max_velocidade": ("gp12_features", "MAX_VELOCIDADE"),
        "max_aceleracao": ("gp12_features", "MAX_ACELERACAO"),
        "gap_critico_s": ("diagnostico_camera", "GAP_CRITICO_S"),
        "gap_moderado_s": ("diagnostico_camera", "GAP_MODERADO_S"),
        "pontos_sem_variacao": ("diagnostico_camera", "PONTOS_SEM_VARIACAO"),
        "janela_bateria_n": ("diagnostico_camera", "JANELA_BATERIA_N"),
        "delta_gpsp_bateria": ("diagnostico_camera", "DELTA_GPSP_BATERIA"),
        "queda_vel_bateria": ("diagnostico_camera", "QUEDA_VEL_BATERIA"),
        "vel_encerramento_ms": ("diagnostico_camera", "VEL_ENCERRAMENTO_MS"),
        "encerramento_tol_final_km": ("diagnostico_camera", "ENCERRAMENTO_TOL_FINAL_KM"),
        "salto_max_m": ("diagnostico_camera", "SALTO_MAX_M"),
        "vel_maxima_ms": ("diagnostico_camera", "VEL_MAXIMA_MS"),
        "gpsp_excelente": ("avaliador_qualidade", "GPSP_EXCELENTE"),
        "gpsp_bom": ("avaliador_qualidade", "GPSP_BOM"),
        "gpsp_aceitavel": ("avaliador_qualidade", "GPSP_ACEITAVEL"),
        "dist_snv_desatualizado_m": ("comparador_snv", "DIST_SNV_DESATUALIZADO_M"),
    }
    for chave, valor in av.items():
        if chave in mapa_modulo and valor is not None:
            modulo, constante = mapa_modulo[chave]
            setattr(importlib.import_module(modulo), constante, valor)


def resolver_diretorio_saida(saida_raw: str) -> Path:
    saida = Path(saida_raw or "output/validacao")
    if not saida.is_absolute():
        saida = BASE_DIR / saida
    saida.mkdir(parents=True, exist_ok=True)
    return saida
