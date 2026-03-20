"""
src/diagnostico_camera.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Diagnóstico de problemas de hardware e gravação da câmera GoPro.

Este módulo detecta EXCLUSIVAMENTE problemas originados na câmera ou
no processo de gravação — não realiza avaliação do SNV nem do sinal GPS.

Fundamentação técnica:
  - GPMF spec (GoPro, 2023): stream GPS a 18Hz; gaps = perda de frames
  - Nayfeh (2023): anomaly_ml via Isolation Forest; speed_diff como indicador
  - GPMF à 18Hz: deslocamento máximo entre frames a 200 km/h = 3.09m
    → salto > 25m implica velocidade impossível (~1620 km/h)
  - Diagnóstico de bateria: padrão de degradação GPSP crescente + queda de vel.

Eventos detectados:
  GAP_STREAM       : interrupção no fluxo de dados (pausa, arquivo cortado)
  GPS_BLOQUEADO    : coordenadas estáticas — GPS desativado ou obstruído
  BATERIA_FRACA    : degradação de GPSP e velocidade nos últimos segmentos
  ENCERRAMENTO_ABRUPTO : câmera ainda em movimento ao fim da gravação
  DESCONTINUIDADE  : salto espacial impossível entre segmentos consecutivos
  VELOCIDADE_ATIPICA : segmento com velocidade muito fora do padrão da rota
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class EventoCamera(Enum):
    GAP_STREAM           = "gap_stream"
    GPS_BLOQUEADO        = "gps_bloqueado"
    BATERIA_FRACA        = "bateria_fraca"
    ENCERRAMENTO_ABRUPTO = "encerramento_abrupto"
    DESCONTINUIDADE      = "descontinuidade_espacial"
    VELOCIDADE_ATIPICA   = "velocidade_atipica"


class Severidade(Enum):
    CRITICA  = "crítica"
    ALTA     = "alta"
    MODERADA = "moderada"


@dataclass
class EventoDiagnostico:
    evento:      EventoCamera
    severidade:  Severidade
    km_inicio:   float
    km_fim:      float
    descricao:   str
    metrica:     str     # valor medido que disparou o evento
    acao:        str     # ação recomendada


# ── Limiares de detecção ──────────────────────────────────────────────────────
GAP_CRITICO_S         = 30     # gap > 30s = interrupção crítica
GAP_MODERADO_S        =  5     # gap > 5s = interrupção moderada
PONTOS_SEM_VARIACAO   = 54     # 3s × 18Hz = GPS possivelmente bloqueado
JANELA_BATERIA_N      = 180    # últimas N amostras (~10s) para análise de bateria
DELTA_GPSP_BATERIA    = 150    # aumento de GPSP indicativo de bateria fraca
QUEDA_VEL_BATERIA     = 0.40   # queda relativa de velocidade no fim
VEL_ENCERRAMENTO_MS   =  5.0   # câmera em movimento ao fim da gravação (m/s)
SALTO_MAX_M           = 25.0   # deslocamento impossível entre pontos adjacentes
VEL_MINIMA_MS         =  3.0   # < 3 m/s em todo o segmento = parada/anomalia
VEL_MAXIMA_MS         = 55.5   # > 200 km/h = spike impossível em rodovia


def diagnosticar(df: pd.DataFrame,
                  vel_referencia_ms: Optional[float] = None,
                  tamanho_seg_km: float = 1.0) -> list:
    """
    Executa todos os detectores e retorna lista de EventoDiagnostico.

    Parâmetros:
      df                : DataFrame com timestamp, lat, lon, km, speed2d, precision
      vel_referencia_ms : velocidade esperada (None = usa mediana da rota)
      tamanho_seg_km    : tamanho dos segmentos para análise de velocidade
    """
    eventos = []
    eventos += _detectar_gaps(df)
    eventos += _detectar_gps_bloqueado(df)
    eventos += _detectar_bateria(df)
    eventos += _detectar_encerramento_abrupto(df)
    eventos += _detectar_descontinuidades(df, tamanho_seg_km)
    eventos += _detectar_velocidade_atipica(df, vel_referencia_ms, tamanho_seg_km)

    eventos.sort(key=lambda e: (e.km_inicio, e.severidade.value))
    return eventos


# ── Detectores ────────────────────────────────────────────────────────────────

def _detectar_gaps(df: pd.DataFrame) -> list:
    """
    Detecta intervalos anormalmente grandes entre timestamps consecutivos.
    Causas: pausa manual, falha no cartão SD, arquivo segmentado.
    """
    eventos = []
    dt = df["timestamp"].diff().dt.total_seconds().fillna(0)

    for sev, limiar in [(Severidade.CRITICA, GAP_CRITICO_S),
                         (Severidade.MODERADA, GAP_MODERADO_S)]:
        mask = dt > limiar if sev == Severidade.CRITICA else \
               (dt > limiar) & (dt <= GAP_CRITICO_S)
        for idx in df[mask].index:
            km = df.loc[idx, "km"]
            g  = dt[idx]
            eventos.append(EventoDiagnostico(
                evento     = EventoCamera.GAP_STREAM,
                severidade = sev,
                km_inicio  = round(max(km - 0.05, 0), 2),
                km_fim     = round(km, 2),
                descricao  = f"Interrupção no stream GPMF de {g:.1f}s",
                metrica    = f"Δt = {g:.1f}s (limiar: {limiar}s)",
                acao       = (
                    "Verificar se o arquivo foi cortado, se houve pausa manual "
                    "na gravação ou falha de escrita no cartão SD."
                ),
            ))
    return eventos


def _detectar_gps_bloqueado(df: pd.DataFrame) -> list:
    """
    Detecta sequências de coordenadas estáticas.
    Indica GPS desativado nas configurações ou obstrução total (túnel, garagem).
    """
    eventos = []
    dlat = df["lat"].diff().abs().fillna(0)
    dlon = df["lon"].diff().abs().fillna(0)
    estatico = (dlat < 1e-7) & (dlon < 1e-7)

    bloco_i = None
    cont    = 0
    for i, val in enumerate(estatico):
        if val:
            bloco_i = i if bloco_i is None else bloco_i
            cont += 1
        else:
            if cont >= PONTOS_SEM_VARIACAO:
                km_i = df.iloc[bloco_i]["km"]
                km_f = df.iloc[i-1]["km"]
                dur  = (df.iloc[i-1]["timestamp"]
                        - df.iloc[bloco_i]["timestamp"]).total_seconds()
                eventos.append(EventoDiagnostico(
                    evento     = EventoCamera.GPS_BLOQUEADO,
                    severidade = Severidade.CRITICA,
                    km_inicio  = round(km_i, 2),
                    km_fim     = round(km_f, 2),
                    descricao  = f"Coordenadas GPS estáticas por {dur:.0f}s "
                                 f"({cont} amostras sem variação de posição)",
                    metrica    = f"{cont} amostras, Δlat≈0, Δlon≈0",
                    acao       = (
                        "GPS pode estar desativado nas configurações da câmera, "
                        "ou o sinal foi totalmente bloqueado (túnel, subsolo, garagem). "
                        "Os dados de km deste trecho foram estimados por velocidade."
                    ),
                ))
            bloco_i = None
            cont    = 0
    return eventos


def _detectar_bateria(df: pd.DataFrame) -> list:
    """
    Detecta padrão de bateria fraca: GPSP crescente + queda de velocidade
    no trecho final da gravação.
    """
    eventos = []
    if len(df) < JANELA_BATERIA_N * 2:
        return eventos

    meio = df.iloc[len(df)//2 : -JANELA_BATERIA_N]
    fim  = df.iloc[-JANELA_BATERIA_N:]

    prec_meio  = meio["precision"].mean()
    prec_fim   = fim["precision"].mean()
    vel_meio   = meio["speed2d"].mean()
    vel_fim    = fim["speed2d"].mean()
    delta_prec = prec_fim - prec_meio
    queda_vel  = (vel_meio - vel_fim) / (vel_meio + 1e-9)

    if delta_prec > DELTA_GPSP_BATERIA and queda_vel > QUEDA_VEL_BATERIA:
        sev = Severidade.ALTA
        desc = "Padrão de bateria fraca detectado no trecho final"
    elif delta_prec > DELTA_GPSP_BATERIA:
        sev = Severidade.MODERADA
        desc = "Degradação progressiva de precisão GPS no trecho final"
    else:
        return eventos

    eventos.append(EventoDiagnostico(
        evento     = EventoCamera.BATERIA_FRACA,
        severidade = sev,
        km_inicio  = round(fim.iloc[0]["km"], 2),
        km_fim     = round(df["km"].max(), 2),
        descricao  = desc,
        metrica    = (
            f"ΔGPSP = +{delta_prec:.0f} (meio→fim: {prec_meio:.0f}→{prec_fim:.0f}) | "
            f"Δvel = {queda_vel*100:.0f}% de queda "
            f"({vel_meio*3.6:.0f}→{vel_fim*3.6:.0f} km/h)"
        ),
        acao = (
            "A câmera provavelmente desligou por bateria ao final. "
            "Os últimos segmentos podem ter dados GPS degradados. "
            "Verificar se a gravação está completa."
        ),
    ))
    return eventos


def _detectar_encerramento_abrupto(df: pd.DataFrame) -> list:
    """
    Detecta encerramento da gravação com câmera em movimento.
    Indica desligamento por bateria zerada, botão acidental ou queda.
    """
    if len(df) < 20:
        return []
    vel_final = df.iloc[-18:]["speed2d"].mean()
    if vel_final <= VEL_ENCERRAMENTO_MS:
        return []

    km_f = df["km"].max()
    return [EventoDiagnostico(
        evento     = EventoCamera.ENCERRAMENTO_ABRUPTO,
        severidade = Severidade.ALTA,
        km_inicio  = round(km_f - 0.05, 2),
        km_fim     = round(km_f, 2),
        descricao  = "Gravação encerrada com câmera em movimento",
        metrica    = f"Velocidade média no último segundo: {vel_final*3.6:.1f} km/h",
        acao       = (
            "Câmera desligada abruptamente: bateria zerada, botão acionado "
            "acidentalmente ou queda. O último segmento pode estar incompleto."
        ),
    )]


def _detectar_descontinuidades(df: pd.DataFrame,
                                 tamanho_seg_km: float) -> list:
    """
    Detecta descontinuidade espacial entre segmentos consecutivos.

    Método: compara a média das últimas N amostras do segmento anterior
    com a média das primeiras N amostras do segmento seguinte.
    N = min(10, 5% do segmento) — robusto a ruído pontual.

    Calibração: GoPro @ 18Hz, velocidade máxima de rodovia 200 km/h →
    deslocamento máximo entre frames = 3.09m.
    Limiar de 25m → velocidade implícita ~1620 km/h (fisicamente impossível).
    """
    eventos = []
    R = 6_371_000
    km_max = df["km"].max()
    km = 0.0
    seg_ant = None
    km_ant  = None

    while km < km_max:
        seg = df[(df["km"] >= km) & (df["km"] < km + tamanho_seg_km)]
        if len(seg) < 10:
            km += tamanho_seg_km
            continue

        N = min(10, max(3, len(seg)//20))
        cabeca = seg.iloc[:N]

        if seg_ant is not None:
            cauda = seg_ant.iloc[-N:]
            lat_a, lon_a = cauda["lat"].mean(), cauda["lon"].mean()
            lat_b, lon_b = cabeca["lat"].mean(), cabeca["lon"].mean()

            dlat   = np.radians(lat_b - lat_a)
            dlon   = np.radians(lon_b - lon_a)
            a      = (np.sin(dlat/2)**2
                      + np.cos(np.radians(lat_a))
                      * np.cos(np.radians(lat_b))
                      * np.sin(dlon/2)**2)
            dist_m = R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))

            if dist_m > SALTO_MAX_M:
                t_a    = cauda["timestamp"].iloc[-1]
                t_b    = cabeca["timestamp"].iloc[0]
                dt     = max((t_b - t_a).total_seconds(), 1/18)
                vel    = (dist_m / dt) * 3.6
                sev    = Severidade.CRITICA if dist_m > 100 else Severidade.ALTA
                eventos.append(EventoDiagnostico(
                    evento     = EventoCamera.DESCONTINUIDADE,
                    severidade = sev,
                    km_inicio  = round(km_ant, 2),
                    km_fim     = round(km + tamanho_seg_km, 2),
                    descricao  = (
                        f"Descontinuidade espacial de {dist_m:.0f}m "
                        f"na junção dos segmentos km {km_ant:.1f}–{km:.1f}"
                    ),
                    metrica    = (
                        f"distância na junção = {dist_m:.1f}m | "
                        f"velocidade implícita = {vel:.0f} km/h "
                        f"(limite físico: ~{SALTO_MAX_M*18*3.6:.0f} km/h @ 18Hz)"
                    ),
                    acao = (
                        "Verificar se o arquivo é concatenação de gravações "
                        "diferentes, ou se houve corte e retomada em local distinto."
                    ),
                ))
        seg_ant = seg
        km_ant  = km
        km += tamanho_seg_km
    return eventos


def _detectar_velocidade_atipica(df: pd.DataFrame,
                                   vel_ref_ms: Optional[float],
                                   tamanho_seg_km: float) -> list:
    """
    Detecta segmentos com velocidade média muito fora do padrão da rota.
    Usa a mediana da rota como referência se vel_ref_ms não for fornecida.
    """
    eventos = []
    vel_ref = vel_ref_ms if vel_ref_ms else df["speed2d"].median()
    km_max  = df["km"].max()
    km = 0.0

    while km < km_max:
        seg = df[(df["km"] >= km) & (df["km"] < km + tamanho_seg_km)]
        if len(seg) < 5:
            km += tamanho_seg_km
            continue

        vel_seg = seg["speed2d"].mean()

        if vel_seg < VEL_MINIMA_MS and vel_ref > 5:
            eventos.append(EventoDiagnostico(
                evento     = EventoCamera.VELOCIDADE_ATIPICA,
                severidade = Severidade.MODERADA,
                km_inicio  = round(km, 2),
                km_fim     = round(km + tamanho_seg_km, 2),
                descricao  = "Segmento com velocidade muito abaixo do padrão da rota",
                metrica    = (
                    f"média do segmento = {vel_seg*3.6:.1f} km/h | "
                    f"referência = {vel_ref*3.6:.1f} km/h"
                ),
                acao = (
                    "Câmera pode ter parado (semáforo, pedágio, obra) ou "
                    "houve corte e retomada. Revisar o vídeo neste trecho."
                ),
            ))
        elif vel_seg > VEL_MAXIMA_MS:
            eventos.append(EventoDiagnostico(
                evento     = EventoCamera.VELOCIDADE_ATIPICA,
                severidade = Severidade.ALTA,
                km_inicio  = round(km, 2),
                km_fim     = round(km + tamanho_seg_km, 2),
                descricao  = "Segmento com velocidade fisicamente impossível em rodovia",
                metrica    = (
                    f"média = {vel_seg*3.6:.1f} km/h | "
                    f"máximo esperado = {VEL_MAXIMA_MS*3.6:.0f} km/h"
                ),
                acao = (
                    "Spike de velocidade GPS — provável multipath ou perda momentânea "
                    "de fix. Os dados de posição deste segmento são suspeitos."
                ),
            ))
        km += tamanho_seg_km
    return eventos


def imprimir_diagnostico(eventos: list) -> None:
    """Imprime o diagnóstico de câmera com formatação profissional."""
    SEP    = "─" * 78
    SIMB   = {
        Severidade.CRITICA:  "✗",
        Severidade.ALTA:     "⚠",
        Severidade.MODERADA: "·",
    }

    print(f"\n{SEP}")
    print("  DIAGNÓSTICO DE GRAVAÇÃO — CÂMERA GoPro")
    print(f"  Legenda: ✗ Crítica  ⚠ Alta  · Moderada")
    print(SEP)

    if not eventos:
        print("  Nenhum problema de hardware ou gravação detectado.")
        print(SEP)
        return

    por_sev = {
        Severidade.CRITICA:  [e for e in eventos if e.severidade == Severidade.CRITICA],
        Severidade.ALTA:     [e for e in eventos if e.severidade == Severidade.ALTA],
        Severidade.MODERADA: [e for e in eventos if e.severidade == Severidade.MODERADA],
    }

    for sev, lista in por_sev.items():
        for e in lista:
            s = SIMB[sev]
            print(f"  {s} [{sev.value.upper():<8}]  km {e.km_inicio:.1f}–{e.km_fim:.1f}  "
                  f"[{e.evento.value}]")
            print(f"    Evento  : {e.descricao}")
            print(f"    Métrica : {e.metrica}")
            print(f"    Ação    : {e.acao}")
            print()

    total  = len(eventos)
    criticos = len(por_sev[Severidade.CRITICA])
    print(f"  Total: {total} evento(s) | "
          f"{criticos} crítico(s) | "
          f"{len(por_sev[Severidade.ALTA])} alto(s) | "
          f"{len(por_sev[Severidade.MODERADA])} moderado(s)")
    print(SEP)
