# Video GoPro Metadados Analyzer - GoPro x DNIT SNV

## Instalação (uma única vez)
```bat
conda activate gopro_snv
pip install flask
```

## Inicializar a interface web
```bat
cd C:\Projetos\gopro_snv_validator
conda activate gopro_snv
python app.py
```

Abra o navegador em: **http://localhost:5000**

## Estrutura de arquivos
```
data\raw\     → coloque os arquivos .MP4 aqui
data\snv\     → coloque os .shp, .dbf, .prj, .shx aqui
output\       → resultados gerados (.geojson, .csv)
```
