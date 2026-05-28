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
from pyproj import Geod


class EventoCamera(Enum):
    GAP_STREAM           = "gap_stream"
    GPS_BLOQUEADO        = "gps_bloqueado"
    BATERIA_FRACA        = "bateria_fraca"
    ENCERRAMENTO_ABRUPTO = "encerramento_abrupto"
    DESCONTINUIDADE      = "descontinuidade_espacial"
    VELOCIDADE_ATIPICA   = "velocidade_atipica"
    VEICULO_PARADO       = "veiculo_parado"
    AZIMUTE_IRREGULAR    = "azimute_irregular"
    KM_SEGUNDO_BAIXO     = "km_por_segundo_baixo"
    ERRO_SNV             = "erro_snv"


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
    km_pico:     Optional[float] = None  # ponto exato usado para posicionar no mapa


# ── Limiares de detecção ──────────────────────────────────────────────────────
GAP_CRITICO_S         = 30     # gap > 30s = interrupção crítica
GAP_MODERADO_S        =  5     # gap > 5s = interrupção moderada
PONTOS_SEM_VARIACAO   = 54     # 3s × 18Hz = GPS possivelmente bloqueado
JANELA_BATERIA_N      = 180    # últimas N amostras (~10s) para análise de bateria
DELTA_GPSP_BATERIA    = 150    # aumento de GPSP indicativo de bateria fraca
QUEDA_VEL_BATERIA     = 0.40   # queda relativa de velocidade no fim
VEL_ENCERRAMENTO_MS   =  5.0   # câmera em movimento ao fim da gravação (m/s)
ENCERRAMENTO_TOL_FINAL_KM = 0.05  # ignora encerramento nos ultimos X km
SALTO_MAX_M           = 25.0   # deslocamento impossível entre pontos adjacentes
VEL_MAXIMA_MS         = 55.5   # > 200 km/h = spike impossível em rodovia
VEL_PARADO_MS         =  0.50  # tolera jitter da GoPro em parada (~1.8 km/h)
PARADA_VEICULO_MIN_S  =  5.0
AZIMUTE_DIST_MAX_M    =  1.0   # virada brusca em distancia curta = oscilacao GPS
AZIMUTE_MIN_GRAUS     = 20.0
AZIMUTE_PASSO_MIN_M   =  0.05
AZIMUTE_INTERVALO_S   =  1.0
AVANCO_KM_MS_MIN      =  1.0
KM_SEGUNDO_BAIXO_N    = 18


def diagnosticar(df: pd.DataFrame, tamanho_seg_km: float = 1.0) -> list:
    """
    Executa todos os detectores e retorna lista de EventoDiagnostico.

    Parâmetros:
      df                : DataFrame com timestamp, lat, lon, km, speed2d, precision
      tamanho_seg_km    : tamanho dos segmentos para análise de velocidade
    """
    eventos = []
    eventos += _detectar_gaps(df)
    eventos += _detectar_gps_bloqueado(df)
    eventos += _detectar_bateria(df)
    eventos += _detectar_encerramento_abrupto(df)
    eventos += _detectar_descontinuidades(df, tamanho_seg_km)
    eventos += _detectar_km_por_segundo_baixo(df)
    eventos += _detectar_azimute_irregular(df)
    eventos += _detectar_velocidade_atipica(df, tamanho_seg_km)

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
                km_pico    = round(km, 2),
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
                km_pico = df.iloc[bloco_i + cont // 2]["km"]
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
                    km_pico    = round(km_pico, 2),
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
        km_pico = round(fim.loc[fim["precision"].idxmax(), "km"], 2),
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
    km_i = max(km_f - 0.05, 0)
    if km_f - km_i <= ENCERRAMENTO_TOL_FINAL_KM:
        return []

    return [EventoDiagnostico(
        evento     = EventoCamera.ENCERRAMENTO_ABRUPTO,
        severidade = Severidade.ALTA,
        km_inicio  = round(km_i, 2),
        km_fim     = round(km_f, 2),
        descricao  = "Gravação encerrada com câmera em movimento",
        metrica    = f"Velocidade média no último segundo: {vel_final*3.6:.1f} km/h",
        acao       = (
            "Câmera desligada abruptamente: bateria zerada, botão acionado "
            "acidentalmente ou queda. O último segmento pode estar incompleto."
        ),
        km_pico    = round(km_f, 2),
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
                km_pico = cabeca["km"].iloc[0]
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
                    km_pico = round(km_pico, 2),
                ))
        seg_ant = seg
        km_ant  = km
        km += tamanho_seg_km
    return eventos


def _detectar_azimute_irregular(df: pd.DataFrame) -> list:
    """
    Detecta oscilacao brusca de azimute em deslocamentos muito curtos.

    A regra reaproveita a mesma ideia do diagnostico de telemetria GPS:
    se dois passos consecutivos somam pouca distancia, mas a direcao muda
    muitos graus, o ponto intermediario pode indicar jitter/buffer irregular
    no GPS ou multipath.
    """
    if len(df) < 3:
        return []

    eventos = []
    geod = Geod(ellps="WGS84")
    pontos = df[["lat", "lon"]].to_numpy()
    segmentos = []

    for index, (first, second) in enumerate(zip(pontos, pontos[1:])):
        azimuth, _, distance = geod.inv(first[1], first[0], second[1], second[0])
        if distance >= AZIMUTE_PASSO_MIN_M:
            segmentos.append((azimuth % 360, distance, index))

    ultimo_evento_ts = None
    for previous, current in zip(segmentos, segmentos[1:]):
        delta = abs((current[0] - previous[0] + 180) % 360 - 180)
        distance = previous[1] + current[1]
        if distance > AZIMUTE_DIST_MAX_M or delta + 0.001 < AZIMUTE_MIN_GRAUS:
            continue

        sample_index = previous[2] + 1
        row = df.iloc[sample_index]
        timestamp = row["timestamp"]
        if ultimo_evento_ts is not None:
            dt = (timestamp - ultimo_evento_ts).total_seconds()
            if dt < AZIMUTE_INTERVALO_S:
                continue
        ultimo_evento_ts = timestamp

        km = float(row["km"])
        sev = Severidade.ALTA if delta >= 90 else Severidade.MODERADA
        eventos.append(EventoDiagnostico(
            evento     = EventoCamera.AZIMUTE_IRREGULAR,
            severidade = sev,
            km_inicio  = round(max(km - 0.02, 0), 2),
            km_fim     = round(km + 0.02, 2),
            descricao  = (
                f"Mudanca brusca de azimute de {delta:.0f} graus "
                f"com deslocamento de apenas {distance:.2f}m"
            ),
            metrica    = (
                f"azimute anterior = {previous[0]:.1f} graus | "
                f"azimute atual = {current[0]:.1f} graus | "
                f"distancia combinada = {distance:.2f}m"
            ),
            acao       = (
                "Revisar a telemetria neste trecho. Pode haver jitter no GPS, "
                "multipath, ou irregularidade no buffer GPMF da GoPro."
            ),
            km_pico    = round(km, 2),
        ))

    return eventos


def _detectar_km_por_segundo_baixo(df: pd.DataFrame) -> list:
    """
    Detecta sequencias em que o avanco do km convertido para m/s fica baixo.

    O marcador do evento cai no ponto com menor km/s dentro de cada bloco,
    para ficar em cima do ponto mais suspeito do trecho.
    """
    if len(df) < 3:
        return []

    dt = df["timestamp"].diff().dt.total_seconds().replace(0, np.nan)
    dkm = df["km"].diff()
    avanco_ms = ((dkm * 1000) / dt).replace([np.inf, -np.inf], np.nan)
    mask = (avanco_ms <= AVANCO_KM_MS_MIN) & avanco_ms.notna()

    eventos = []
    bloco_i = None
    for pos, val in enumerate(mask.to_numpy()):
        if val:
            bloco_i = pos if bloco_i is None else bloco_i
            continue

        if bloco_i is not None:
            _adicionar_evento_km_s_baixo(df, avanco_ms, bloco_i, pos - 1, eventos)
            bloco_i = None

    if bloco_i is not None:
        _adicionar_evento_km_s_baixo(df, avanco_ms, bloco_i, len(df) - 1, eventos)

    return eventos


def _adicionar_evento_km_s_baixo(
    df: pd.DataFrame,
    avanco_ms: pd.Series,
    inicio: int,
    fim: int,
    eventos: list,
) -> None:
    if fim - inicio + 1 < KM_SEGUNDO_BAIXO_N:
        return

    trecho = avanco_ms.iloc[inicio:fim + 1]
    pico_pos = trecho.idxmin()
    km_i = float(df.iloc[inicio]["km"])
    km_f = float(df.iloc[fim]["km"])
    km_pico = float(df.loc[pico_pos, "km"])
    valor_pico = float(avanco_ms.loc[pico_pos])
    seg = df.iloc[inicio:fim + 1]
    tempo_parado_s = _maior_tempo_velocidade_zero(seg)

    if tempo_parado_s >= PARADA_VEICULO_MIN_S:
        eventos.append(EventoDiagnostico(
            evento     = EventoCamera.VEICULO_PARADO,
            severidade = Severidade.ALTA,
            km_inicio  = round(km_i, 2),
            km_fim     = round(km_f, 2),
            descricao  = "Veículo parado por tempo prolongado",
            metrica    = (
                f"tempo parado = {tempo_parado_s:.1f}s | "
                f"menor avanco = {valor_pico:.3f} m/s"
            ),
            acao       = (
                "Revisar o vídeo neste trecho. Pode ser parada real "
                "(semáforo, pedágio, obra) ou interrupção da gravação."
            ),
            km_pico    = round(km_pico, 2),
        ))
        return

    eventos.append(EventoDiagnostico(
        evento     = EventoCamera.KM_SEGUNDO_BAIXO,
        severidade = Severidade.ALTA,
        km_inicio  = round(km_i, 2),
        km_fim     = round(km_f, 2),
        descricao  = (
            "Avanco do km convertido para m/s igual ou menor que 1 em sequencia"
        ),
        metrica    = (
            f"menor avanco = {valor_pico:.3f} m/s | "
            f"amostras consecutivas = {fim - inicio + 1}"
        ),
        acao       = (
            "Revisar o ponto marcado no mapa. Pode haver trecho embolado, "
            "jitter de GPS ou buffer irregular."
        ),
        km_pico    = round(km_pico, 2),
    ))


def _detectar_velocidade_atipica(df: pd.DataFrame,
                                   tamanho_seg_km: float) -> list:
    """
    Detecta segmentos com velocidade média fisicamente impossível.
    """
    eventos = []
    km_max  = df["km"].max()
    km = 0.0

    while km < km_max:
        seg = df[(df["km"] >= km) & (df["km"] < km + tamanho_seg_km)]
        if len(seg) < 5:
            km += tamanho_seg_km
            continue

        vel_seg = seg["speed2d"].mean()

        if vel_seg > VEL_MAXIMA_MS:
            km_pico = seg.loc[seg["speed2d"].idxmax(), "km"]
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
                km_pico = round(km_pico, 2),
            ))
        km += tamanho_seg_km
    return eventos


def _maior_tempo_velocidade_zero(seg: pd.DataFrame) -> float:
    if "timestamp" not in seg.columns or "speed2d" not in seg.columns:
        return 0.0

    maior = 0.0
    inicio = None
    ultimo = None

    for _, row in seg.iterrows():
        timestamp = row["timestamp"]
        parado = float(row["speed2d"]) <= VEL_PARADO_MS
        if parado:
            inicio = timestamp if inicio is None else inicio
            ultimo = timestamp
            continue

        if inicio is not None and ultimo is not None:
            maior = max(maior, (ultimo - inicio).total_seconds())
        inicio = None
        ultimo = None

    if inicio is not None and ultimo is not None:
        maior = max(maior, (ultimo - inicio).total_seconds())

    return maior


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
