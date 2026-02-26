# constants.py
"""共用常數：音訊取樣率、chunk 大小、log 設定。"""
import logging
import os
import sys

TARGET_SR     = 16000
CHUNK_SAMPLES = 8000   # 0.5 秒 @ 16kHz

# ---------------------------------------------------------------------------
# Logging：主程序 + worker process 都寫到同一個 log 檔
# ---------------------------------------------------------------------------
_LOG_DIR  = (os.path.dirname(sys.executable)
             if getattr(sys, "frozen", False)
             else os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_LOG_DIR, "subtitle.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(processName)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
