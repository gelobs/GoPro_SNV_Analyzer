import os
import sys
from pathlib import Path


BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
SRC_DIR = BASE_DIR / "src"
DATA_DIR = BASE_DIR / "data"
SNV_DIR = DATA_DIR / "snv"
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = BASE_DIR / "output"
CUT_UPLOAD_DIR = RAW_DIR / "_corte_uploads"
FFMPEG_BIN_DIR = BASE_DIR / "ffmpeg" / "bin"


def preparar_ambiente() -> None:
    if FFMPEG_BIN_DIR.exists():
        os.environ["PATH"] = str(FFMPEG_BIN_DIR) + os.pathsep + os.environ.get("PATH", "")

    for directory in [SNV_DIR, RAW_DIR, OUT_DIR, CUT_UPLOAD_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
