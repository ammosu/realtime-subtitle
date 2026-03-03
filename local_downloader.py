# local_downloader.py
"""
本地 ASR 後端資源自動下載與路徑偵測。

下載項目：
  - qwen3-asr-1.7b.bin（GGUF INT4，~2.3 GB）來自 HuggingFace dseditor/Collection

chatllm 執行檔（main, libchatllm.so）需另行提供：
  - 優先使用 QwenASRMiniTool 已編譯版本（自動偵測）
  - 或手動指定已編譯目錄
"""
from __future__ import annotations

import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ── 預設目錄 ──────────────────────────────────────────────────────────
_DEFAULT_DIR = Path.home() / ".local" / "share" / "realtime-subtitle"
DEFAULT_MODEL_PATH   = _DEFAULT_DIR / "models" / "qwen3-asr-1.7b.bin"
DEFAULT_CHATLLM_DIR  = _DEFAULT_DIR / "chatllm"

# ── HuggingFace 下載來源 ───────────────────────────────────────────────
_HF_COLLECTION = "https://huggingface.co/dseditor/Collection/resolve/main"
GGUF_URL  = f"{_HF_COLLECTION}/qwen3-asr-1.7b.bin"
GGUF_SIZE = 2_300_000_000   # 約 2.3 GB（估算值，用於進度顯示）

# ── QwenASRMiniTool 常見路徑（自動偵測）──────────────────────────────
_QWEN_CANDIDATES: list[Path] = [
    Path.home() / "git_project" / "QwenASRMiniTool",
    Path.home() / "git" / "QwenASRMiniTool",
    Path("/opt/QwenASRMiniTool"),
]
_EXE_NAME = "main.exe" if sys.platform == "win32" else "main"


def _ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        pass
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


# ══════════════════════════════════════════════════════════════════════
# 路徑偵測
# ══════════════════════════════════════════════════════════════════════

def detect_existing_paths() -> tuple[str, str]:
    """
    自動偵測已存在的模型路徑與 chatllm 目錄。

    回傳 (model_path, chatllm_dir)：
      - 優先使用 QwenASRMiniTool 中已存在的檔案
      - 次選本工具的預設下載目錄
      - 找不到則回傳空字串
    """
    model_path  = ""
    chatllm_dir = ""

    # 1. 搜尋 QwenASRMiniTool 目錄
    for base in _QWEN_CANDIDATES:
        _m = base / "GPUModel" / "qwen3-asr-1.7b.bin"
        _c = base / "chatllm"
        if _m.exists() and (_c / _EXE_NAME).exists():
            model_path  = str(_m)
            chatllm_dir = str(_c)
            return model_path, chatllm_dir

    # 2. 搜尋本工具預設目錄
    if DEFAULT_MODEL_PATH.exists():
        model_path = str(DEFAULT_MODEL_PATH)
    if (DEFAULT_CHATLLM_DIR / _EXE_NAME).exists():
        chatllm_dir = str(DEFAULT_CHATLLM_DIR)

    return model_path, chatllm_dir


def model_exists(model_path: str = "") -> bool:
    """檢查 GGUF 模型是否存在且大小合理（> 100 MB）。"""
    p = Path(model_path) if model_path else DEFAULT_MODEL_PATH
    return p.exists() and p.stat().st_size > 100_000_000


def chatllm_exists(chatllm_dir: str = "") -> bool:
    """檢查 chatllm 執行檔是否存在。"""
    d = Path(chatllm_dir) if chatllm_dir else DEFAULT_CHATLLM_DIR
    return (d / _EXE_NAME).exists()


# ══════════════════════════════════════════════════════════════════════
# 下載
# ══════════════════════════════════════════════════════════════════════

def _download_file(url: str, dest: Path, progress_cb=None):
    """下載單一檔案，支援斷點續傳。progress_cb(done_bytes, total_bytes)"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.exists() else 0

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; realtime-subtitle-downloader)"},
    )
    if existing > 0:
        req.add_header("Range", f"bytes={existing}-")

    try:
        resp = urllib.request.urlopen(req, timeout=30, context=_ssl_ctx())
    except urllib.error.HTTPError as e:
        if e.code == 416:   # 已完整
            return
        raise

    content_length = int(resp.headers.get("Content-Length", 0))
    total = existing + content_length if content_length else GGUF_SIZE
    mode  = "ab" if existing > 0 and resp.status == 206 else "wb"
    done  = existing if mode == "ab" else 0

    with open(dest, mode) as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress_cb and total:
                progress_cb(done, total)
    resp.close()


def download_gguf(dest: Path | None = None, progress_cb=None):
    """
    下載 qwen3-asr-1.7b.bin 至 dest（預設 ~/.local/share/realtime-subtitle/models/）。
    progress_cb(pct: float, msg: str)
    """
    target = dest or DEFAULT_MODEL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    if model_exists(str(target)):
        if progress_cb:
            progress_cb(1.0, "✅ 模型已存在")
        return

    if progress_cb:
        progress_cb(0.0, f"下載 qwen3-asr-1.7b.bin（~2.3 GB）…")

    def _cb(done: int, total: int):
        if progress_cb and total:
            pct = done / total
            progress_cb(pct, f"下載中…  {done/1_048_576:.0f} / {total/1_048_576:.0f} MB")

    _download_file(GGUF_URL, target, progress_cb=_cb)

    if progress_cb:
        progress_cb(1.0, f"✅ 下載完成：{target}")


# ══════════════════════════════════════════════════════════════════════
# 命令列介面
# ══════════════════════════════════════════════════════════════════════

def _cli_bar(pct: float, msg: str):
    filled = int(pct * 40)
    bar    = "█" * filled + "░" * (40 - filled)
    print(f"\r[{bar}] {pct*100:5.1f}%  {msg:<50}", end="", flush=True)


if __name__ == "__main__":
    print("=== realtime-subtitle 本地 ASR 資源偵測 ===\n")

    m, c = detect_existing_paths()
    if m and c:
        print(f"✅ 找到現有模型：{m}")
        print(f"✅ 找到 chatllm：{c}")
    else:
        if not m:
            print(f"⚠ 未找到 GGUF 模型，將下載至：{DEFAULT_MODEL_PATH}")
            download_gguf(progress_cb=_cli_bar)
            print()
        if not c:
            print("⚠ 未找到 chatllm 執行檔")
            print(f"  請將 chatllm 編譯後複製至：{DEFAULT_CHATLLM_DIR}")
            print("  或從 https://github.com/foldl/chatllm.cpp 編譯：")
            print("    cmake -B build -DGGML_VULKAN=ON && cmake --build build -j$(nproc)")
            print(f"    mkdir -p {DEFAULT_CHATLLM_DIR}")
            print(f"    cp build/bin/main build/lib/libchatllm.so build/lib/libggml*.so {DEFAULT_CHATLLM_DIR}/")
