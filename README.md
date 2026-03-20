# Video GoPro Analyzer X SNV DNIT

> **Validação de Trajetória GPS de Câmeras GoPro contra o SNV/DNIT**

Comparação entre a trajetória GPS registrada por câmeras de ação GoPro e o traçado oficial do Sistema Nacional de Viação (SNV). O sistema classifica cada segmento da rodovia como **dentro da tolerância** ou **SNV desatualizado**, com base na qualidade do sinal GPS medido diretamente da telemetria interna dos vídeos.

---

## Sumário

- [Visão Geral](#visão-geral)
- [Arquitetura do Sistema](#arquitetura-do-sistema)
- [Requisitos de Sistema](#requisitos-de-sistema)
- [Instalação](#instalação)
- [Estrutura de Diretórios](#estrutura-de-diretórios)
- [Uso](#uso)
  - [Interface Web](#interface-web)
  - [Linha de Comando](#linha-de-comando)
- [Pipeline de Processamento](#pipeline-de-processamento)
- [Módulos](#módulos)
- [Configurações Avançadas](#configurações-avançadas)
- [Arquivos de Saída](#arquivos-de-saída)
- [Fundamentação Técnica e Adaptações do Trabalho de Nayfeh (2023)](#fundamentação-técnica-e-adaptações-do-trabalho-de-nayfeh-2023)
- [Referências Bibliográficas](#referências-bibliográficas)
- [Limitações Conhecidas](#limitações-conhecidas)
- [Compatibilidade de Equipamentos](#compatibilidade-de-equipamentos)
- [Licença](#licença)

---

## Visão Geral

Campanhas de inventário rodoviário do DNIT dependem de dados GPS precisos e de um mapa de referência atualizado. Quando a trajetória registrada diverge do traçado oficial, surge a dúvida: **o erro está no sinal GPS da câmera, ou o traçado do SNV está desatualizado?**

Este sistema responde a essa pergunta de forma automatizada:

1. Extrai os metadados GPS diretamente dos arquivos `.MP4` da GoPro via stream GPMF
2. Avalia a qualidade do sinal GPS amostra a amostra (18 Hz) usando aprendizado de máquina
3. Calcula a distância de cada ponto GPS ao traçado SNV mais próximo
4. Classifica cada segmento de 1 km com base na combinação de qualidade de sinal e magnitude da divergência
5. Gera relatórios, arquivos GIS (GeoJSON) prontos para o QGIS e uma interface web interativa com mapa

**Limiar operacional adotado:** divergências abaixo de **100 m** são consideradas dentro da tolerância conjunta GPS + digitalização do SNV. Divergências acima de 100 m com sinal GPS de boa qualidade indicam SNV desatualizado ou variante de traçado não catalogada.

---

## Arquitetura do Sistema

```
gopro_snv_analyzer/
├── app.py                    # Servidor Flask (interface web)
├── validar_rota.py           # Ponto de entrada CLI
├── templates/
│   └── index.html            # Frontend (HTML + CSS + JS + Leaflet)
├── src/
│   ├── gp12_gps_extractor.py # Extração GPS do stream GPMF (gopro2gpx)
│   ├── gp12_features.py      # Features + Isolation Forest (detecção de anomalias)
│   ├── avaliador_qualidade.py# Índice de Qualidade GPS por segmento (IQ)
│   ├── comparador_snv.py     # Conformidade trajetória × SNV
│   ├── diagnostico_camera.py # Diagnóstico de hardware e gravação
│   ├── snv_loader.py         # Carregamento e recorte do shapefile SNV
│   ├── validador_snv_gopro.py# Orquestrador do pipeline
│   └── exportador.py         # Exportação GeoJSON + CSV
├── data/
│   ├── raw/                  # Arquivos .MP4 da GoPro
│   └── snv/                  # Shapefiles do SNV (DNIT)
└── output/                   # Resultados gerados
```

**Stack tecnológico:** Python 3.13 · Flask · GeoPandas · Shapely · Scikit-learn · Leaflet.js · OpenStreetMap

---

## Requisitos de Sistema

| Componente | Versão mínima | Notas |
|---|---|---|
| Python | 3.11+ | Testado em 3.13 |
| Conda | 24+ | Recomendado para GeoPandas no Windows |
| ffprobe / ffmpeg | 6+ | Incluído no pacote `ffmpeg` |
| RAM | 4 GB | 8 GB recomendado para SNV nacional |
| SO | Windows 10+, Ubuntu 22+, macOS 13+ | |

### Câmeras suportadas

|      Modelo      |GPS|  GPMF |   Suporte    |
|------------------|---|-------|--------------|
| Hero 11 Black    | ✓ | 18 Hz | ✅ Completo |
| Hero 13 Black    | ✓ | 18 Hz | ✅ Completo |
| Hero 10 Black    | ✓ | 18 Hz | ✅ Completo |
| Hero 9 Black     | ✓ | 18 Hz | ✅ Completo |
| Hero 7 / 8 Black | ✓ | 18 Hz | ✅ Completo |
| Hero 12 Black    | ✗ |   —   | ⚠️ Sem GPS interno¹ |

> ¹ A Hero 12 Black não possui módulo GPS interno. Arquivos gravados com ela não contêm dados de telemetria GPS no stream GPMF.

---

## Instalação

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/gopro_snv_analyzer.git
cd gopro_snv_analyzer
```

### 2. Crie o ambiente Conda

```bash
conda create -n gopro_snv python=3.13
conda activate gopro_snv
```

### 3. Instale as dependências GIS via Conda

> Este passo é obrigatório antes do `pip install`. O GeoPandas no Windows requer as bibliotecas nativas do Conda para evitar conflitos de dependência.

```bash
conda install geopandas pyogrio shapely pyproj -c conda-forge
```

### 4. Instale o restante via pip

```bash
pip install -r requirements.txt
```

### 5. Instale o gopro2gpx do GitHub

```bash
pip install gopro2gpx @ git+https://github.com/juanmcasillas/gopro2gpx.git
```

### 6. Verifique o ambiente

```bash
python testar_ambiente.py
```

---

## Estrutura de Diretórios de Dados

Coloque os arquivos nas pastas correspondentes:

```
data/
├── raw/
│   ├── GX010062.MP4          # Vídeos GoPro (.MP4 ou .mp4)
│   └── GX010063.MP4
└── snv/
    ├── 202602A/
    │   ├── SNV_202602A.shp
    │   ├── SNV_202602A.dbf
    │   ├── SNV_202602A.prj
    │   └── SNV_202602A.shx
    └── 202308A/
        └── SNV_202308A.shp   # (+ .dbf .prj .shx)
```

**Download do SNV:** [gov.br/dnit → DNIT GEO → Downloads → Sistema Nacional de Viação](https://www.gov.br/dnit/pt-br/assuntos/planejamento-e-pesquisa/dnit-geo)

> O sistema busca recursivamente todos os `.MP4` e `.shp` dentro da pasta do projeto, incluindo subpastas.

---

## Uso

### Interface Web

```bash
conda activate gopro_snv
python app.py
```

Acesse `http://localhost:5000` no navegador.

**Fluxo de uso:**
1. Selecione o **SNV** na lista suspensa (detectado automaticamente)
2. Selecione o **vídeo** GoPro (a velocidade mediana é calculada automaticamente)
3. Ajuste o **tamanho do segmento** (padrão: 1,0 km)
4. Clique em **Processar**
5. Acompanhe o log em tempo real e navegue pelas abas ao concluir

### Linha de Comando

Edite as variáveis no topo de `validar_rota.py`:

```python
MP4_PATH                = r"data\raw\GX010062.MP4"
SNV_PATH                = r"data\snv\202602A\SNV_202602A.shp"
PREFIXO_SAIDA           = r"output\validacao_BRxxxx"
TAMANHO_SEG_KM          = 1.0
VELOCIDADE_KMH          = 80
```

```bash
python validar_rota.py
```

---

## Pipeline de Processamento

O sistema executa 5 etapas sequenciais:

```
┌─────────────────────────────────────────────────────────────┐
│  ETAPA 1 — Extração GPS (gp12_gps_extractor.py)             │
│  MP4 → GPMF stream → GPS5 (lat/lon/alt/speed2d/speed3d)     │
│  + GPSP (DOP×100) + GPSF (fix type) @ 18 Hz                 │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  ETAPA 2 — Qualidade GPS (gp12_features + avaliador)        │
│  Isolation Forest (n=200) + regras físicas →                │
│  IQ: Excelente / Bom / Aceitável / Degradado                │
│  Raio de confiança posicional: 5 / 15 / 50 / 200 m         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  ETAPA 3 — Distância ao SNV (validador_snv_gopro.py)        │
│  nearest_points (Shapely) · projeção UTM 22S (EPSG:32722)   │
│  → dist_media, dist_max, P95 por segmento                   │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  ETAPA 4 — Conformidade SNV (comparador_snv.py)             │
│  dist_max × IQ → Dentro da tolerância / SNV desatualizado   │
│              / Sinal GPS insuficiente / Inconclusivo         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  ETAPA 5 — Diagnóstico de câmera (diagnostico_camera.py)    │
│  Gap no stream · GPS bloqueado · Bateria fraca              │
│  Encerramento abrupto · Descontinuidade · Vel. atípica      │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
              Relatórios + GeoJSONs + Interface Web
```

---

## Módulos

### `gp12_gps_extractor.py`
Extrai o stream GPMF dos arquivos `.MP4` via biblioteca `gopro2gpx`. Utiliza a estrutura interna `BuildGPSPoints()` para acessar os campos nativos do GPMF: **GPS5** (coordenadas + velocidades), **GPSP** (precisão DOP×100) e **GPSF** (tipo de fix). Normaliza timestamps mistos tz-aware/tz-naive presentes em arquivos Hero 11. Calcula distância acumulada (km) via fórmula de Haversine.

### `gp12_features.py`
Constrói o vetor de features para cada amostra GPS e executa o **Isolation Forest** (200 estimadores, contamination=4%) para detecção não-supervisionada de anomalias. Features: lat, lon, alt, speed2d, speed3d, dist_m, speed_pos (velocidade derivada da posição), speed_diff (discrepância GPS vs. posição), accel_ms2, delta_alt, cog_deg, dop, fix_ok. O gps_score final combina o resultado do IF com regras físicas (GPSP alto, fix < 3D).

> **Adaptação de Nayfeh (2023):** ver seção [Fundamentação Técnica](#fundamentação-técnica-e-adaptações-do-trabalho-de-nayfeh-2023).

### `avaliador_qualidade.py`
Classifica a qualidade do sinal GPS em quatro níveis (Índice de Qualidade — IQ) com base em GPSP médio, percentual de fix 3D e taxa de anomalias. Associa a cada nível um **raio de confiança posicional** baseado em Newson & Krumm (2009): Excelente = 5 m, Bom = 15 m, Aceitável = 50 m, Degradado = 200 m.

### `comparador_snv.py`
Realiza a comparação geométrica entre a trajetória GoPro e o SNV. A decisão de conformidade cruza `dist_max` com o IQ do sinal:

| Condição | Conformidade |
|---|---|
| `dist_max < raio_confiança` | Dentro da tolerância |
| `dist_max < 100 m` + sinal ≥ Bom | Dentro da tolerância |
| `dist_max < 100 m` + sinal Aceitável | Inconclusivo |
| `dist_max ≥ 100 m` + sinal ≥ Bom | SNV desatualizado |
| Sinal Degradado | GPS insuficiente — não avaliável |

Calcula também o P95 das distâncias (mais robusto que a média para detectar outliers geométricos) e a sistematicidade da divergência (CV < 1,2 + extensão ≥ 200 m).

### `diagnostico_camera.py`
Detecta exclusivamente problemas de hardware e gravação. Não interfere na análise SNV.

| Evento | Critério de detecção |
|---|---|
| `gap_stream` | Intervalo entre timestamps > 5 s (moderado) ou > 30 s (crítico) |
| `gps_bloqueado` | ≥ 54 amostras consecutivas sem variação de posição (3 s × 18 Hz) |
| `bateria_fraca` | ΔGPSP > 150 na janela final + queda de velocidade > 40% |
| `encerramento_abrupto` | Velocidade média > 5 m/s nos últimos 18 frames |
| `descontinuidade_espacial` | Salto > 25 m entre pontos adjacentes (~1.620 km/h @ 18 Hz) |
| `velocidade_atipica` | Média do segmento < 3 m/s ou > 55,5 m/s |

### `snv_loader.py`
Carrega o shapefile SNV/DNIT, reprojeta para WGS-84 (EPSG:4326) e recorta ao buffer de 0,5 km ao redor da rota gravada, reduzindo o volume de dados antes do cálculo de distâncias ponto a ponto.

### `validador_snv_gopro.py`
Orquestrador: coordena os módulos acima em sequência, calcula distâncias via `nearest_points` (Shapely) com projeção UTM 22S (EPSG:32722 — adequada para o Sul e Sudeste do Brasil), e imprime o sumário executivo ao final.

### `exportador.py`
Gera quatro arquivos de saída por processamento:
- `_pontos.geojson` — amostras GPS com atributos de qualidade e conformidade
- `_segmentos.geojson` — linhas por segmento coloridas por conformidade
- `_eventos_camera.geojson` — marcadores de eventos de câmera
- `_relatorio.csv` — tabela unificada (Excel-compatível, UTF-8 BOM)

---

## Configurações Avançadas

Disponíveis na interface web (seção **Configurações Avançadas**) e editáveis sem alterar o código-fonte. Os valores padrão refletem os parâmetros dos arquivos backend:

| Grupo | Parâmetro | Padrão | Descrição |
|---|---|---|---|
| Físicos | `GPSP_RUIM` | 250 | DOP × 100 acima do qual o sinal é considerado ruim |
| Físicos | `MAX_VELOCIDADE` | 60 m/s | Limite de velocidade GPS (~216 km/h) |
| Físicos | `MAX_ACELERACAO` | 15 m/s² | Aceleração máxima física em rodovia |
| Câmera | `GAP_CRITICO_S` | 30 s | Interrupção crítica no stream GPMF |
| Câmera | `SALTO_MAX_M` | 25 m | Deslocamento impossível entre frames |
| GPS | `GPSP_EXCELENTE` | 200 | Limite superior DOP × 100 para IQ Excelente |
| GPS | `RAIO_EXCELENTE` | 5 m | Raio de confiança posicional — IQ Excelente |
| SNV | `DIST_SNV_DESATUALIZADO_M` | 100 m | Limiar de divergência para SNV desatualizado |

---

## Arquivos de Saída

### GeoJSONs (QGIS)

Abra os arquivos no QGIS e aplique simbologia por campo:

| Arquivo | Campo de simbologia | Valores |
|---|---|---|
| `_segmentos.geojson` | `conformidade` | `dentro_da_tolerancia`, `snv_desatualizado`, `sinal_gps_insuficiente`, `inconclusivo` |
| `_pontos.geojson` | `iq_sinal` | `excelente`, `bom`, `aceitavel`, `degradado` |
| `_eventos_camera.geojson` | `severidade` | `crítica`, `alta`, `moderada` |

### CSV (`_relatorio.csv`)

Uma linha por segmento com as colunas:

```
origem, km_inicio, km_fim, iq_sinal, gpsp_medio, dop_medio,
pct_fix_3d, pct_anomalos, raio_conf_m, vel_media_kmh,
vel_min_kmh, vel_max_kmh, conformidade, dist_media_m,
dist_max_m, dist_p95_m, sistematico, n_amostras, justificativa
```

Linhas com `origem = EVENTO_CAMERA` contêm os diagnósticos de câmera na coluna `justificativa`.

---

## Fundamentação Técnica e Adaptações do Trabalho de Nayfeh (2023)

Este projeto utiliza **conceitos e metodologias** do trabalho acadêmico de Nayfeh (2023), adaptados para o contexto de validação de rodovias brasileiras com câmeras GoPro. As adaptações são substanciais e o contexto de aplicação é distinto (rodovias terrestres vs. drones; validação cartográfica vs. detecção de spoofing GPS).

**Referência completa:**
> Nayfeh, M. (2023). *Artificial Intelligence-Based GPS Spoofing Detection and Implementation with Applications to Unmanned Aerial Vehicles*. Master's Thesis, Purdue University. Disponível em: https://github.com/mnayfeh/gps_spoofing_detection

### O que foi adaptado e como

#### 1. Conjunto de features GPS (Seção 2.3.1 de Nayfeh, 2023)

O paper identifica as features mais discriminantes para separar sinal GPS limpo de sinal manipulado/degradado: velocidade (`speed2d`, `speed3d`), diferença entre velocidade GPS e velocidade derivada da posição (`speed_diff`), aceleração (`accel_ms2`) e indicadores de precisão como DOP e tipo de fix.

**Adaptação neste projeto:** as mesmas features são usadas no módulo `gp12_features.py` para detecção de anomalias de sinal GPS em gravações GoPro a 18 Hz. O vetor de features foi estendido com campos específicos do GPMF da GoPro (`cog_deg`, `delta_alt`, `fix_ok`) e adaptado para o domínio de rodovias (sem altitude barométrica de drones, sem dados de IMU).

#### 2. Isolation Forest para detecção não-supervisionada (Seção 2.3.2 de Nayfeh, 2023)

Nayfeh aplica o Isolation Forest como classificador não-supervisionado para identificar amostras de sinal GPS anômalas, justificando a escolha pelo bom desempenho com dados desbalanceados (poucos pontos ruins em meio a muitos pontos bons) e pela ausência de necessidade de dados rotulados.

**Adaptação neste projeto:** o Isolation Forest é aplicado com os mesmos princípios (`n_estimators=200`, `random_state=42`). A `contamination` padrão foi ajustada para 4% (vs. valores usados no paper para UAVs), calibrada empiricamente para gravações de rodovias onde a taxa esperada de anomalias GPS é baixa. O resultado é combinado com regras físicas determinísticas (GPSP, fix) para produzir o `gps_score` final — abordagem híbrida não presente no paper original.

#### 3. Conceito de sistematicidade da divergência

O paper discute a distinção entre erros aleatórios (ruído de sinal) e erros sistemáticos (trajetória divergente) como critério de classificação (Tabela 2.5, Nayfeh, 2023).

**Adaptação neste projeto:** o conceito de sistematicidade é implementado no `comparador_snv.py` por meio do Coeficiente de Variação (CV) das distâncias ao SNV: CV < 1,2 com extensão ≥ 200 m indica divergência sistemática — sinal de SNV desatualizado ou variante de traçado. CV alto indica divergência irregular — sinal de erro GPS ou evento pontual (obra, desvio de tráfego).

#### 4. O que NÃO foi utilizado

O trabalho de Nayfeh (2023) abrange detecção de *GPS spoofing* (falsificação intencional de sinal), modelos de ataque (meaconing, replay), validação com dados de IMU de drones e métricas de Detection Rate (DR) e False Alarm Rate (FAR) para ataques. Nenhum desses aspectos foi incorporado neste projeto, cujo objetivo é exclusivamente a **validação de qualidade de gravação GPS em câmeras de ação em ambiente de rodovia**.

---

## Referências Bibliográficas

As seguintes obras fundamentaram decisões técnicas do sistema:

**[1]** Nayfeh, M. (2023). *Artificial Intelligence-Based GPS Spoofing Detection and Implementation with Applications to Unmanned Aerial Vehicles*. Master's Thesis, Purdue University.
→ Adaptações: features GPS, Isolation Forest, conceito de sistematicidade (ver seção acima).

**[2]** Newson, P., & Krumm, J. (2009). Hidden Markov map matching through noise and sparseness. *Proceedings of the 17th ACM SIGSPATIAL International Conference on Advances in Geographic Information Systems (ACM GIS 2009)*, 336–343.
→ Usado em: raio de confiança posicional por nível de qualidade GPS (`avaliador_qualidade.py`).

**[3]** Lou, Y., Zhang, C., Zheng, Y., Xie, X., Wang, W., & Huang, Y. (2009). Map-matching for low-sampling-rate GPS trajectories. *Proceedings of ACM GIS 2009*, 352–361.
→ Usado em: restrições geométricas e de velocidade na comparação trajetória × SNV (`comparador_snv.py`).

**[4]** Ahmed, M., Karagiorgou, S., Pfoser, D., & Wenk, C. (2015). A comparison and evaluation of map construction algorithms using vehicle tracking data. *GeoInformatica*, 19(3), 601–632.
→ Usado em: métrica de sistematicidade da divergência (CV e extensão mínima) (`comparador_snv.py`).

**[5]** Bierlaire, M., Chen, J., & Newman, J. (2013). A probabilistic map matching method for smartphone GPS data. *Transportation Research Part C: Emerging Technologies*, 26, 78–98.
→ Usado em: fundamento do conceito de região de confiança no map-matching probabilístico.

**[6]** GoPro Inc. (2023). *GoPro Metadata Format Specification (GPMF)*. Disponível em: https://github.com/gopro/gpmf-parser
→ Especificação dos streams GPS5, GPSP e GPSF usados na extração de telemetria.

**[7]** Casillas, J. (2023). *gopro2gpx — Extract GPS data from GoPro videos*. GitHub: https://github.com/juanmcasillas/gopro2gpx
→ Biblioteca base para extração do stream GPMF dos arquivos `.MP4`.

**[8]** DNIT (2024). *Sistema Nacional de Viação (SNV) — Documentação e Download*. Disponível em: https://www.gov.br/dnit/pt-br/assuntos/planejamento-e-pesquisa/dnit-geo

---

## Limitações Conhecidas

- **Hero 12 Black:** sem GPS interno — não gera stream GPMF de posição.
- **Tuneis e viadutos:** o GPS pode ser total ou parcialmente bloqueado; esses trechos são classificados como `sinal_gps_insuficiente` e não são avaliados contra o SNV.
- **UTM 22S:** a projeção EPSG:32722 é adequada para as regiões Sul e Sudeste do Brasil. Para Norte e Nordeste (a oeste do meridiano 42°W), considere usar EPSG:32723 (UTM 23S).
- **SNV nacional:** o shapefile completo do SNV tem ~200 MB. O sistema recorta automaticamente ao buffer de 0,5 km da rota, mas a leitura inicial pode levar alguns segundos.
- **Vídeos longos:** gravações acima de 4 horas podem consumir mais de 4 GB de RAM no processamento de distâncias ponto a ponto. Divida em segmentos menores se necessário.

---

## Compatibilidade de Equipamentos Testada

| Câmera | Firmware | Resultado |
|---|---|---|
| GoPro Hero 11 Black | H22.01.01.xx | ✅ Validado |
| GoPro Hero 13 Black | H24.01.02.xx | ✅ Validado |

---

## Licença

Este projeto é disponibilizado sob a licença **MIT**. Uso livre para fins acadêmicos, de pesquisa e comerciais, com atribuição.

As obras de terceiros citadas nas referências bibliográficas são propriedade de seus respectivos autores e estão sujeitas às suas próprias licenças.

---

*Contribuições, issues e pull requests são bem-vindos.*
