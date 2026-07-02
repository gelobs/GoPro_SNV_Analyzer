import math


def iter_segmentos_km(km_min: float, km_max: float, tamanho_seg_km: float):
    """Gera segmentos alinhados aos marcos inteiros do tamanho configurado."""
    if tamanho_seg_km <= 0:
        raise ValueError("tamanho_seg_km deve ser maior que zero")

    inicio = float(km_min)
    fim_rota = float(km_max)
    passo = float(tamanho_seg_km)

    while inicio < fim_rota:
        proximo_marco = math.ceil((inicio + 1e-9) / passo) * passo
        fim = proximo_marco if proximo_marco > inicio else inicio + passo
        fim = min(fim, fim_rota)
        yield inicio, fim
        inicio = fim
