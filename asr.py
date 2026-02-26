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

    def transcribe(self, audio_float32: np.ndarray, language: str | None = None) -> dict:
        """
        One-shot 轉錄：送出整段 16kHz float32 音訊，回傳 {"language": str, "text": str}。
        audio_float32: shape (N,), dtype float32
        language: 可選的語言代碼（如 "zh", "en", "ja"），傳給 server 可提升辨識準確度。
        """
        url = f"{self.base_url}/api/transcribe"
        if language:
            url = f"{url}?language={language}"
        r = requests.post(
            url,
            data=audio_float32.tobytes(),
            headers={"Content-Type": "application/octet-stream"},
            timeout=45,
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

    def __init__(self, api_key: str, callback, model: str = "gpt-4o-mini"):
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.callback = callback
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
        if src == "en" and tgt == "zh":
            system_msg = (
                "你是即時字幕翻譯員。輸入是語音辨識（ASR）的原始文字。\n"
                "請完成兩件事並以 JSON 回傳：\n"
                f"1. corrected：修正同音字/辨識錯誤、移除無意義語氣詞（{_FILLER_ZH} 等），輸出自然書面英文\n"
                "2. translated：將校正後文字翻譯成繁體中文（台灣口語），依中文語法重新組句\n"
                '回傳格式：{"corrected": "校正後英文", "translated": "繁體中文翻譯"}'
            )
        elif src == "zh" and tgt == "en":
            system_msg = (
                "You are a real-time subtitle translator. The input is raw ASR (speech recognition) text.\n"
                "Please do two things and return JSON:\n"
                f"1. corrected: fix homophones/mis-recognized words, remove filler words ({_FILLER_ZH}, {_FILLER_EN}), output clean Chinese\n"
                "2. translated: translate the corrected text to natural, colloquial English\n"
                'Return format: {"corrected": "corrected Chinese", "translated": "English translation"}'
            )
        else:
            src_name = LANG_NAME.get(src, src)
            tgt_name = LANG_NAME.get(tgt, tgt)
            system_msg = (
                f"You are a real-time subtitle translator. The input is raw ASR text.\n"
                f"Please do two things and return JSON:\n"
                f"1. corrected: fix recognition errors, remove filler words, output clean {src_name}\n"
                f"2. translated: translate the corrected text to {tgt_name}, keep it natural\n"
                f'{{"corrected": "corrected {src_name}", "translated": "{tgt_name} translation"}}'
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
