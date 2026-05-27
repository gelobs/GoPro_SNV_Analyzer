"""
validar_rota.py  —  ponto de entrada do sistema de validação GoPro × DNIT SNV
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uso: python validar_rota.py

Edite apenas as variáveis da seção CONFIGURAÇÃO abaixo.

Arquivos de saída gerados em output/:
  _pontos.geojson      — amostras GPS com qualidade e conformidade (QGIS)
  _segmentos.geojson   — segmentos coloridos por conformidade (QGIS)
  _eventos_camera.geojson — marcadores de eventos de câmera (QGIS)
  _relatorio.csv       — tabela unificada (Excel)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from snv_loader           import load_snv, recortar_snv
from gp12_gps_extractor   import extract_hero12_gps
from validador_snv_gopro  import validar_rota
from exportador           import exportar_para_gis


# ══════════════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════════════
MP4_PATH                = r"data\raw\GX12345.MP4"
SNV_PATH                = r"data\snv\SNV_202401.shp"
PREFIXO_SAIDA           = r"output\validacao_BR"

# Tamanho de cada segmento de análise (km)
TAMANHO_SEG_KM          = 1.0
# ══════════════════════════════════════════════════════════════════════


def main() -> None:
    print("Carregando SNV...")
    snv = load_snv(SNV_PATH)

    print("\nExtraindo GPS da GoPro...")
    gps = extract_hero12_gps(MP4_PATH)

    print("\nRecortando SNV ao trecho gravado...")
    snv_trecho = recortar_snv(snv, gps, buffer_km=0.5)

    df, qualidades, conformidades, eventos = validar_rota(
        gps,
        snv_trecho,
        tamanho_seg_km          = TAMANHO_SEG_KM,
    )

    print("\nExportando resultados...")
    exportar_para_gis(df, qualidades, conformidades, eventos,
                      prefixo=PREFIXO_SAIDA)

    print("\nArquivos em output\\:")
    for arq in sorted(Path("output").glob("*")):
        print(f"  {arq.name:<55} {arq.stat().st_size/1024:7.1f} KB")

    criticos = [e for e in eventos if e.severidade.value == "crítica"]
    if criticos:
        print(f"\n  ✗ ATENÇÃO: {len(criticos)} evento(s) crítico(s) de câmera.")
        for e in criticos:
            print(f"    km {e.km_inicio:.1f}–{e.km_fim:.1f}: {e.descricao}")

    print("\nConcluído.")


if __name__ == "__main__":
    main()
