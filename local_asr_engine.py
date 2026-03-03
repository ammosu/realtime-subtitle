# local_asr_engine.py
"""
本地 Qwen3-ASR 推理引擎（基於 chatllm.cpp + Vulkan）。

兩種執行模式：
  1. DLL 模式（優先）：ctypes 直接呼叫 libchatllm.so，模型常駐 GPU 記憶體，
     每 chunk ~0.23s（Vulkan shader 暖機後）
  2. Subprocess 模式（後備）：每次轉錄啟動 main 子程序，模型每次重載

輸出格式：language {lang}<asr_text>{transcription}
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

_EXE_NAME = "main.exe" if sys.platform == "win32" else "main"
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_STARTUP_INFO: "subprocess.STARTUPINFO | None" = None
if sys.platform == "win32":
    _STARTUP_INFO = subprocess.STARTUPINFO()
    _STARTUP_INFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUP_INFO.wShowWindow = 0

SAMPLE_RATE = 16000


def _to_path_bytes(path: "str | Path") -> bytes:
    p = str(path)
    if sys.platform != "win32":
        return p.encode("utf-8")
    try:
        n = ctypes.windll.kernel32.GetShortPathNameW(p, None, 0)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n)
            if ctypes.windll.kernel32.GetShortPathNameW(p, buf, n) > 0:
                try:
                    return buf.value.encode("ascii")
                except UnicodeEncodeError:
                    p = buf.value
    except Exception:
        pass
    try:
        cp = ctypes.windll.kernel32.GetACP()
        return p.encode(f"cp{cp}")
    except (UnicodeEncodeError, LookupError):
        return p.encode("utf-8")


def _short_path_str(path: "str | Path") -> str:
    p = str(path)
    if sys.platform != "win32":
        return p
    try:
        n = ctypes.windll.kernel32.GetShortPathNameW(p, None, 0)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n)
            if ctypes.windll.kernel32.GetShortPathNameW(p, buf, n) > 0:
                return buf.value
    except Exception:
        pass
    return p


def detect_vulkan_devices(chatllm_dir: "str | Path") -> list[dict]:
    """
    執行 main --show_devices，回傳非 CPU 的計算裝置清單。
    回傳：[{'id': 0, 'name': 'NVIDIA GeForce RTX 4090', 'vram_free': N}, ...]
    失敗回傳空清單（代表只能用 CPU）。
    """
    exe = Path(chatllm_dir) / _EXE_NAME
    if not exe.exists():
        return []
    try:
        result = subprocess.run(
            [str(exe), "--show_devices"],
            capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=10,
            cwd=str(chatllm_dir),
            creationflags=_CREATE_NO_WINDOW,
            startupinfo=_STARTUP_INFO,
        )
        output = result.stdout + result.stderr
        pending: list[dict] = []
        current: dict | None = None
        for line in output.splitlines():
            m = re.match(r"\s*(\d+):\s*(\S+)\s+-\s+\S+\s+\((.+)\)", line)
            if m:
                backend = m.group(2).upper()
                current = {
                    "id": int(m.group(1)),
                    "name": m.group(3).strip(),
                    "vram_free": 0,
                    "_skip": backend == "CPU",
                }
                pending.append(current)
            elif "memory free" in line and current is not None:
                mf = re.search(r"(\d+)\s*B", line)
                if mf:
                    current["vram_free"] = int(mf.group(1))
        return [
            {"id": d["id"], "name": d["name"], "vram_free": d["vram_free"]}
            for d in pending if not d["_skip"]
        ]
    except Exception:
        return []


# ══════════════════════════════════════════════════════
# Subprocess 模式（後備）
# ══════════════════════════════════════════════════════

class _ChatLLMRunner:
    """每次轉錄啟動 main 子程序（後備模式，每次重載模型）。"""

    def __init__(self, model_path: "str | Path", chatllm_dir: "str | Path",
                 n_gpu_layers: int = 99):
        self._model_path  = Path(model_path).resolve()
        self._chatllm_dir = Path(chatllm_dir).resolve()
        self._n_gpu_layers = n_gpu_layers
        self._lock = threading.Lock()
        exe = self._chatllm_dir / _EXE_NAME
        if not exe.exists():
            raise FileNotFoundError(f"{_EXE_NAME} 不存在：{exe}")
        self._exe = exe
        # 驗證模型可載入
        r = subprocess.run(
            [str(exe), "-m", str(self._model_path), "-ngl", "0",
             "--hide_banner", "--show"],
            capture_output=True, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            timeout=30, cwd=str(self._chatllm_dir),
            creationflags=_CREATE_NO_WINDOW, startupinfo=_STARTUP_INFO,
        )
        if "Qwen3-ASR" not in (r.stdout + r.stderr):
            raise RuntimeError(f"模型驗證失敗：{(r.stdout + r.stderr)[:300]}")

    def transcribe(self, wav_path: str, sys_prompt: str | None = None) -> str:
        gpu_args = ["-ngl", "all"] if self._n_gpu_layers > 0 else ["-ngl", "0"]
        cmd = [str(self._exe), "-m", str(self._model_path), *gpu_args,
               "--hide_banner", "-p", wav_path]
        if sys_prompt:
            cmd += ["-s", sys_prompt]
        with self._lock:
            r = subprocess.run(
                cmd, capture_output=True, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                timeout=120, cwd=str(self._chatllm_dir),
                creationflags=_CREATE_NO_WINDOW, startupinfo=_STARTUP_INFO,
            )
        output = r.stdout + r.stderr
        if "<asr_text>" not in output:
            raise RuntimeError(f"推理失敗，未取得輸出：{output.strip()[:300]}")
        return output.split("<asr_text>", 1)[1].strip()


# ══════════════════════════════════════════════════════
# DLL 模式（優先）
# ══════════════════════════════════════════════════════

class _DLLASRRunner:
    """libchatllm.so ctypes 包裝，模型常駐 GPU 記憶體。"""

    def __init__(self, model_path: "str | Path", chatllm_dir: "str | Path",
                 n_gpu_layers: int = 99, cb=None):
        self._chatllm_dir = Path(chatllm_dir).resolve()
        self._lock = threading.Lock()

        dll_name = "libchatllm.dll" if sys.platform == "win32" else "libchatllm.so"
        dll_path = self._chatllm_dir / dll_name
        if not dll_path.exists():
            raise FileNotFoundError(f"{dll_name} 不存在：{dll_path}")

        _saved_path = os.environ.get("PATH", "")
        _saved_ld   = os.environ.get("LD_LIBRARY_PATH", "")
        _dir_str = str(self._chatllm_dir)
        os.environ["PATH"] = _dir_str + os.pathsep + _saved_path
        os.environ["LD_LIBRARY_PATH"] = _dir_str + os.pathsep + _saved_ld

        if sys.platform == "win32":
            os.add_dll_directory(_dir_str)
            lib = ctypes.windll.LoadLibrary(str(dll_path))
        else:
            lib = ctypes.CDLL(str(dll_path))

        if sys.platform == "win32":
            PRINTFUNC = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p)
            ENDFUNC   = ctypes.WINFUNCTYPE(None, ctypes.c_void_p)
        else:
            PRINTFUNC = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p)
            ENDFUNC   = ctypes.CFUNCTYPE(None, ctypes.c_void_p)

        lib.chatllm_append_init_param.argtypes = [ctypes.c_char_p]
        lib.chatllm_append_init_param.restype  = None
        lib.chatllm_init.argtypes              = []
        lib.chatllm_init.restype               = ctypes.c_int
        lib.chatllm_create.argtypes            = []
        lib.chatllm_create.restype             = ctypes.c_void_p
        lib.chatllm_append_param.argtypes      = [ctypes.c_void_p, ctypes.c_char_p]
        lib.chatllm_append_param.restype       = None
        lib.chatllm_start.argtypes             = [ctypes.c_void_p, PRINTFUNC, ENDFUNC, ctypes.c_void_p]
        lib.chatllm_start.restype              = ctypes.c_int
        lib.chatllm_restart.argtypes           = [ctypes.c_void_p, ctypes.c_char_p]
        lib.chatllm_restart.restype            = None
        lib.chatllm_user_input.argtypes        = [ctypes.c_void_p, ctypes.c_char_p]
        lib.chatllm_user_input.restype         = ctypes.c_int

        self._lib = lib
        self._PRINTFUNC = PRINTFUNC
        self._ENDFUNC   = ENDFUNC

        lib.chatllm_append_init_param(b"--ggml_dir")
        lib.chatllm_append_init_param(_to_path_bytes(self._chatllm_dir))
        if lib.chatllm_init() != 0:
            raise RuntimeError("chatllm_init() 失敗")

        chat = lib.chatllm_create()
        if not chat:
            raise RuntimeError("chatllm_create() 回傳 NULL")
        self._chat = chat

        gpu_arg = "all" if n_gpu_layers > 0 else "0"
        for p_b in [
            b"-m", _to_path_bytes(Path(model_path).resolve()),
            b"-ngl", gpu_arg.encode(),
            b"--multimedia_file_tags", b"{{", b"}}",
        ]:
            lib.chatllm_append_param(chat, p_b)

        self._chunks: list[str] = []
        self._error:  str | None = None

        @PRINTFUNC
        def on_print(user_data, print_type, s_ptr):
            text = s_ptr.decode("utf-8", errors="replace") if s_ptr else ""
            if print_type == 0:
                self._chunks.append(text)
            elif print_type == 2:
                self._error = text

        @ENDFUNC
        def on_end(user_data):
            pass

        self._on_print = on_print
        self._on_end   = on_end

        if cb:
            cb("載入 chatllm 模型（Vulkan GPU）…")
        r = lib.chatllm_start(chat, on_print, on_end, ctypes.c_void_p(0))
        os.environ["PATH"] = _saved_path
        os.environ["LD_LIBRARY_PATH"] = _saved_ld
        if r != 0:
            raise RuntimeError(f"chatllm_start() 失敗: {r}")

    def transcribe(self, wav_path: str, sys_prompt: str | None = None) -> str:
        safe = _short_path_str(str(Path(wav_path).resolve()))
        fwd  = safe.replace("\\", "/")
        try:
            path_b = fwd.encode("ascii")
        except UnicodeEncodeError:
            cp = ctypes.windll.kernel32.GetACP() if sys.platform == "win32" else 65001
            path_b = fwd.encode(f"cp{cp}", errors="replace")
        msg      = b"{{audio:" + path_b + b"}}"
        sys_bytes = sys_prompt.encode("utf-8") if sys_prompt else None

        with self._lock:
            self._lib.chatllm_restart(
                self._chat,
                ctypes.c_char_p(sys_bytes) if sys_bytes else ctypes.c_char_p(None),
            )
            self._chunks.clear()
            self._error = None
            r = self._lib.chatllm_user_input(self._chat, msg)

        if r != 0:
            raise RuntimeError(f"chatllm_user_input() 失敗: {r}")
        if self._error:
            raise RuntimeError(f"DLL 錯誤：{self._error}")
        full = "".join(self._chunks)
        if "<asr_text>" not in full:
            raise RuntimeError(f"推理失敗，未取得輸出：{full.strip()[:300]}")
        return full.split("<asr_text>", 1)[1].strip()


# ══════════════════════════════════════════════════════
# 公開介面
# ══════════════════════════════════════════════════════

# 語系名稱 → ISO 639-1 代碼
_LANG_CODE: dict[str, str] = {
    "zh": "zh", "en": "en", "ja": "ja", "ko": "ko",
    "yue": "yue", "fr": "fr", "de": "de", "es": "es",
    "pt": "pt", "ru": "ru", "ar": "ar", "th": "th",
    "vi": "vi", "id": "id", "ms": "ms",
}


class LocalASREngine:
    """
    本地 Qwen3-ASR 引擎。優先使用 DLL/SO 模式（模型常駐 GPU），
    若 libchatllm.so 不存在則退回 subprocess 模式。

    使用：
        engine = LocalASREngine()
        engine.load(model_path, chatllm_dir, device_id=0, cb=print)
        text = engine.transcribe(audio_float32, language="zh")
    """

    def __init__(self):
        self.ready    = False
        self._runner: "_DLLASRRunner | _ChatLLMRunner | None" = None
        self._cc      = None   # OpenCC 簡→繁

    def load(
        self,
        model_path:   "str | Path",
        chatllm_dir:  "str | Path",
        device_id:    int = 0,
        cb=None,
    ) -> None:
        """載入模型。cb(msg: str) 可選，用於顯示載入進度。"""
        def _s(msg: str):
            print(f"[LocalASR] {msg}", flush=True)
            if cb:
                cb(msg)

        if not Path(model_path).exists():
            raise FileNotFoundError(f"模型不存在：{model_path}")

        # OpenCC 簡→繁
        try:
            import opencc
            self._cc = opencc.OpenCC("s2twp")
        except Exception:
            self._cc = None

        # 優先嘗試 DLL/SO 模式
        dll_name = "libchatllm.dll" if sys.platform == "win32" else "libchatllm.so"
        dll_path = Path(chatllm_dir) / dll_name
        if dll_path.exists():
            try:
                _s("載入 DLL 模式（模型常駐 GPU）…")
                self._runner = _DLLASRRunner(
                    model_path=model_path,
                    chatllm_dir=chatllm_dir,
                    n_gpu_layers=99,
                    cb=cb,
                )
                self.ready = True
                _s("DLL 模式載入完成（每 chunk ~0.23s）")
                return
            except Exception as e:
                _s(f"DLL 模式失敗（{e}），改用 subprocess 模式…")

        _s("subprocess 模式驗證模型…")
        self._runner = _ChatLLMRunner(
            model_path=model_path,
            chatllm_dir=chatllm_dir,
            n_gpu_layers=99,
        )
        self.ready = True
        _s("subprocess 模式載入完成")

    def transcribe(
        self,
        audio:    np.ndarray,
        language: str | None = None,
        context:  str | None = None,
    ) -> str:
        """
        傳入 16kHz float32 音頻，回傳轉錄文字。
        language: ISO 639-1 語言代碼（如 "zh", "en", "ja"），可選
        """
        if not self.ready or self._runner is None:
            raise RuntimeError("引擎尚未載入，請先呼叫 load()")

        # 建立 system prompt 引導語言輸出
        sys_prompt: str | None = None
        if language:
            code = _LANG_CODE.get(language, language[:2])
            lang_names = {"zh": "Chinese", "en": "English", "ja": "Japanese",
                          "ko": "Korean", "fr": "French", "de": "German",
                          "es": "Spanish", "pt": "Portuguese", "ru": "Russian"}
            lang_name = lang_names.get(code, language)
            sys_prompt = (
                f"The audio language is {lang_name}. "
                f"Transcribe it and output strictly in this format: "
                f"language {code}<asr_text>[transcription]. "
                f"Output only {lang_name} text after <asr_text>, no translation."
            )

        # 寫暫存 WAV（chatllm 只接受檔案路徑）
        from scipy.io import wavfile
        audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_path = tf.name
        try:
            wavfile.write(tmp_path, SAMPLE_RATE, audio_int16)
            text = self._runner.transcribe(tmp_path, sys_prompt=sys_prompt)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        # 簡→繁轉換
        if self._cc and text:
            text = self._cc.convert(text)

        return text
