# asr.py
"""ASR HTTP client 與翻譯 debouncer。"""
import json
import logging
import threading

import numpy as np
import requests
from openai import OpenAI

from languages import LANG_NAME, parse_direction, swap_direction

log = logging.getLogger(__name__)


class ASRClient:
    """HTTP client for Qwen3-ASR server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def transcribe(self, audio_float32: np.ndarray, language: str | None = None, context: str = "") -> dict:
        """
        One-shot 轉錄：送出整段 16kHz float32 音訊，回傳 {"language": str, "text": str}。
        audio_float32: shape (N,), dtype float32
        language: 可選的語言代碼（如 "zh", "en", "ja"），傳給 server 可提升辨識準確度。
        context: 可選的辨識提示詞（專有名詞、人名等），提升特定詞彙辨識準確度。
        """
        url = f"{self.base_url}/api/transcribe"
        params: dict = {}
        if language:
            params["language"] = language
        if context:
            params["context"] = context
        r = requests.post(
            url,
            data=audio_float32.tobytes(),
            headers={"Content-Type": "application/octet-stream"},
            params=params or None,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Streaming ASR Session
# ---------------------------------------------------------------------------

class ASRStreamingSession:
    """
    Streaming ASR session via /api/start → /api/chunk（多次）→ /api/finish。

    使用方式：
        session = ASRStreamingSession("http://localhost:8000", language="zh")
        session.start()

        # VAD 偵測到語音時，逐步推入 36ms 片段：
        result = session.push(chunk)  # 累積滿 1s 才送出；回傳中間結果或 None
        ...
        # VAD 偵測到靜音時取最終結果：
        final = session.finish()  # {"language": str, "text": str}
    """
    PUSH_SAMPLES = 16000  # 自動送出閾值：1s @ 16kHz

    def __init__(self, base_url: str, language: str | None = None, context: str = ""):
        self.base_url = base_url.rstrip("/")
        self._params: dict = {}
        if language:
            self._params["language"] = language
        if context:
            self._params["context"] = context
        self._session_id: str | None = None
        self._pending = np.zeros(0, dtype=np.float32)

    def start(self) -> None:
        """在 ASR server 建立新 session。push/finish 前必須先呼叫。"""
        r = requests.post(f"{self.base_url}/api/start", params=self._params or None, timeout=10)
        r.raise_for_status()
        self._session_id = r.json()["session_id"]
        self._pending = np.zeros(0, dtype=np.float32)
        log.debug("[Streaming] session started: %s", self._session_id)

    def push(self, audio: np.ndarray) -> dict | None:
        """
        緩衝音訊；累積滿 PUSH_SAMPLES 時自動 POST /api/chunk。
        回傳中間辨識結果 {"language", "text"}，或 None（仍在緩衝中）。
        """
        if self._session_id is None:
            raise RuntimeError("Session not started; call start() first")
        self._pending = np.concatenate([self._pending, audio])
        if len(self._pending) >= self.PUSH_SAMPLES:
            to_send = self._pending
            self._pending = np.zeros(0, dtype=np.float32)
            return self._post_chunk(to_send)
        return None

    def finish(self) -> dict:
        """送出剩餘緩衝音訊，呼叫 /api/finish，回傳最終辨識結果。"""
        if self._session_id is None:
            raise RuntimeError("Session not started; call start() first")
        if len(self._pending) > 0:
            try:
                self._post_chunk(self._pending)
            except Exception as e:
                log.warning("[Streaming] final chunk push failed: %s", e)
            self._pending = np.zeros(0, dtype=np.float32)
        r = requests.post(f"{self.base_url}/api/finish", params={"session_id": self._session_id}, timeout=30)
        r.raise_for_status()
        log.debug("[Streaming] session finished: %s", self._session_id)
        return r.json()

    def _post_chunk(self, audio: np.ndarray) -> dict:
        r = requests.post(
            f"{self.base_url}/api/chunk",
            data=audio.tobytes(),
            headers={"Content-Type": "application/octet-stream"},
            params={"session_id": self._session_id},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Translation Debouncer
# ---------------------------------------------------------------------------

class TranslationDebouncer:
    """
    將英文 ASR 文字 debounce 後送 GPT-4o mini 翻譯成繁體中文。

    使用方式：
        def on_translation(zh_text):
            print(zh_text)

        debouncer = TranslationDebouncer(api_key="sk-...", callback=on_translation)
        debouncer.update("Hello world")  # 每次 ASR 更新時呼叫
        debouncer.shutdown()
    """

    SENTENCE_ENDINGS = {".", "?", "!", "。", "？", "！"}
    DEBOUNCE_SEC = 0.4

    def __init__(self, api_key: str, callback, model: str = "gpt-4o-mini", context: str = ""):
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.callback = callback
        self.context = context           # 專有名詞提示詞（同步自 ASR context）
        self.direction: str = "en→zh"   # 目前翻譯方向

        self._last_translated = ""
        self._pending_text = ""
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._translate_seq = 0   # 每次 _do_translate 遞增；回呼時比對，過時結果直接丟棄

    def update(self, text: str):
        """每次 ASR 更新時呼叫。text 是目前的完整轉錄文字。"""
        translate_now = None
        with self._lock:
            if text == self._pending_text:
                return
            self._pending_text = text

            # 句尾立即翻譯（注意：_do_translate 必須在 lock 釋放後呼叫）
            if text and text[-1] in self.SENTENCE_ENDINGS:
                self._cancel_timer()
                translate_now = text
            else:
                # 一般 debounce
                self._cancel_timer()
                self._timer = threading.Timer(self.DEBOUNCE_SEC, self._on_timer)
                self._timer.daemon = True
                self._timer.start()

        # lock 已釋放，才可呼叫 OpenAI（否則 _do_translate 內的 with self._lock 會死鎖）
        if translate_now:
            self._do_translate(translate_now)

    def _cancel_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _on_timer(self):
        with self._lock:
            text = self._pending_text
        self._do_translate(text)

    def toggle_direction(self) -> str:
        """交換來源/目標語言，回傳新方向字串。"""
        with self._lock:
            self.direction = swap_direction(self.direction)
            self._last_translated = ""  # 清空快取，強制重新翻譯
            return self.direction

    def set_direction(self, direction: str) -> None:
        """直接設定方向（'en→zh' 或 'zh→en'）。"""
        with self._lock:
            self.direction = direction
            self._last_translated = ""

    def _do_translate(self, text: str):
        with self._lock:
            if not text or text == self._last_translated:
                return
            self._last_translated = text
            self._translate_seq += 1
            my_seq = self._translate_seq
            direction = self.direction  # snapshot
        # lock 釋放後才呼叫 OpenAI
        src, tgt = parse_direction(direction)
        _FILLER_ZH = "痾、阿、喔、嗯、啊、那個、就是、對對對、然後、所以說"
        _FILLER_EN = "um, uh, like, you know, so, right, basically"
        _context_hint = f"\n背景知識（請參考以修正專有名詞）：{self.context}" if self.context else ""
        if src == "en" and tgt == "zh":
            system_msg = (
                "你是即時字幕翻譯員。輸入是語音辨識（ASR）的原始文字。\n"
                "請完成兩件事並以 JSON 回傳：\n"
                f"1. corrected：修正同音字/辨識錯誤、移除無意義語氣詞（{_FILLER_ZH} 等），輸出自然書面英文\n"
                "2. translated：將校正後文字翻譯成繁體中文（台灣口語），依中文語法重新組句\n"
                f'回傳格式：{{"corrected": "校正後英文", "translated": "繁體中文翻譯"}}'
                f"{_context_hint}"
            )
        elif src == "zh" and tgt == "en":
            system_msg = (
                "You are a real-time subtitle translator. The input is raw ASR (speech recognition) text.\n"
                "Please do two things and return JSON:\n"
                f"1. corrected: fix homophones/mis-recognized words, remove filler words ({_FILLER_ZH}, {_FILLER_EN}), output clean Chinese\n"
                "2. translated: translate the corrected text to natural, colloquial English\n"
                f'Return format: {{"corrected": "corrected Chinese", "translated": "English translation"}}'
                f"{_context_hint}"
            )
        else:
            _ZH_PROMPT = "繁體中文（台灣口語）"
            src_name = _ZH_PROMPT if src == "zh" else LANG_NAME.get(src, src)
            tgt_name = _ZH_PROMPT if tgt == "zh" else LANG_NAME.get(tgt, tgt)
            system_msg = (
                f"You are a real-time subtitle translator. The input is raw ASR text.\n"
                f"Please do two things and return JSON:\n"
                f"1. corrected: fix recognition errors, remove filler words, output clean {src_name}\n"
                f"2. translated: translate the corrected text to {tgt_name}, keep it natural\n"
                f'{{"corrected": "corrected {src_name}", "translated": "{tgt_name} translation"}}'
                f"{_context_hint}"
            )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": text},
                ],
                max_tokens=400,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content.strip()
            try:
                data = json.loads(content)
                corrected = data.get("corrected") or text
                translated = data.get("translated", "")
            except (json.JSONDecodeError, AttributeError):
                corrected = text
                translated = content
            log.info("[Translation] corrected=%r translated=%r", corrected, translated)
            # 若有更新的翻譯請求已發出，丟棄本次過時結果
            with self._lock:
                if my_seq != self._translate_seq:
                    log.debug("[Translation] stale (seq=%d vs %d), discarded", my_seq, self._translate_seq)
                    return
            self.callback(corrected, translated)
        except Exception as e:
            log.warning("[Translation error] %s", e)

    def shutdown(self):
        with self._lock:
            self._cancel_timer()
