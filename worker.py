# worker.py
"""Worker subprocess：VAD + ASR + 翻譯 pipeline，不使用任何 X11/GTK。"""
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time
from collections import deque

import numpy as np

from asr import ASRStreamingSession, TranslationDebouncer
from audio import MonitorAudioSource, MicrophoneAudioSource
from constants import TARGET_SR
from languages import parse_direction

log = logging.getLogger(__name__)


def _worker_main(text_q: multiprocessing.SimpleQueue, cmd_q: multiprocessing.SimpleQueue, cfg: dict) -> None:
    """
    在獨立 subprocess 執行：sounddevice + VAD + ASR + 翻譯。
    完全不使用 X11/tkinter，避免與主程序的 XCB 衝突。

    text_q: 送出 {"original": str, "translated": str} 或 {"direction": str}
    cmd_q:  接收 "toggle"（切換翻譯方向）或 "stop"

    架構：
    - on_chunk：非阻塞，只把音訊放入 _vad_q
    - vad_loop：Silero VAD 偵測語音/靜音，累積語音片段，
                靜音 ~0.8s 後把完整語音放入 _speech_q
    - asr_loop：等待 _speech_q，送到 ASR server，更新字幕
    """
    try:
        _worker_main_impl(text_q, cmd_q, cfg)
    except Exception:
        log.exception("[Worker] 未預期的例外，worker 終止")


def _worker_main_impl(text_q: multiprocessing.SimpleQueue, cmd_q: multiprocessing.SimpleQueue, cfg: dict) -> None:
    import onnxruntime as ort
    from pathlib import Path
    import opencc

    os.environ.pop("DISPLAY", None)

    # 簡體→台灣繁體轉換器（s2twp 包含詞彙替換，如「軟件→軟體」）
    _s2tw = opencc.OpenCC("s2twp")

    current_original = ""

    def on_translation(corrected: str, translated: str) -> None:
        text_q.put({"original": corrected, "translated": translated})

    debouncer = TranslationDebouncer(
        api_key=cfg["openai_api_key"],
        callback=on_translation,
        model=cfg["translation_model"],
    )
    debouncer.set_direction(cfg["direction"])

    if cfg["source"] == "monitor":
        audio_source = MonitorAudioSource(device=cfg["monitor_device"])
    else:
        audio_source = MicrophoneAudioSource(device=cfg.get("mic_device"))

    _src_lang, _ = parse_direction(cfg.get("direction", ""))
    _asr_lang = _src_lang if _src_lang else None  # 傳給 ASR server 的語言提示
    _asr_context = cfg.get("context", "")          # 傳給 ASR server 的辨識提示詞

    # Silero VAD 常數（v6 模型）
    VAD_CHUNK = 576               # 36ms @ 16kHz
    VAD_THRESHOLD = 0.5
    RT_SILENCE_CHUNKS = 14        # 0.5s - 靜音後觸發轉錄
    RT_MAX_BUFFER_CHUNKS = 222    # 8s   - 強制 flush（縮短延遲）
    VAD_PAD_CHUNKS = 3            # ~108ms - 語音開始前補入的預滾音訊

    # 載入 VAD 模型（打包後 worker spawn 中 __file__ 不可靠，改用 sys.executable）
    _base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    _vad_model_path = _base_dir / "silero_vad_v6.onnx"
    vad_sess = ort.InferenceSession(str(_vad_model_path))

    _vad_q: queue.Queue = queue.Queue()
    # _speech_q 傳送 streaming 事件 tuple：
    #   ("start",  audio: np.ndarray) — 語音開始（含預滾音訊）
    #   ("audio",  audio: np.ndarray) — 語音進行中的 36ms 片段
    #   ("finish", None)              — 語音結束（靜音 0.5s 或 max buffer）
    _speech_q: queue.Queue = queue.Queue()
    _stop_event = threading.Event()

    def on_chunk(audio: np.ndarray) -> None:
        """非阻塞：只把音訊放入 VAD 佇列。"""
        _vad_q.put(audio)

    def vad_loop() -> None:
        """
        VAD 執行緒：以 streaming 事件通知 asr_loop。

        語音狀態機：
        - IDLE → SPEAKING：偵測到語音，送 ("start", 預滾+首片段)
        - SPEAKING：每個 36ms 片段送 ("audio", chunk)
        - SPEAKING → IDLE：靜音 0.5s 或 max buffer 8s，送 ("finish", None)
        """
        h = np.zeros((1, 1, 128), dtype=np.float32)
        c = np.zeros((1, 1, 128), dtype=np.float32)
        pre_buf: deque = deque(maxlen=VAD_PAD_CHUNKS)  # 語音開始前的預滾緩衝
        in_speech = False
        sil_cnt = 0
        speech_cnt = 0   # 本段已送出的 VAD chunk 數，用於 max buffer 偵測
        leftover = np.zeros(0, dtype=np.float32)

        try:
            while not _stop_event.is_set():
                try:
                    audio = _vad_q.get(timeout=0.1)
                except queue.Empty:
                    continue

                audio = np.concatenate([leftover, audio])
                n_chunks = len(audio) // VAD_CHUNK
                leftover = audio[n_chunks * VAD_CHUNK:]

                for i in range(n_chunks):
                    chunk = audio[i * VAD_CHUNK:(i + 1) * VAD_CHUNK]
                    inp = chunk[np.newaxis, :].astype(np.float32)
                    out = vad_sess.run(
                        ["speech_probs", "hn", "cn"],
                        {"input": inp, "h": h, "c": c},
                    )
                    prob, h, c = out
                    prob = float(prob.flatten()[0])

                    if prob >= VAD_THRESHOLD:
                        if not in_speech:
                            # 語音剛開始：把預滾緩衝 + 首片段一起送出
                            init = np.concatenate(list(pre_buf) + [chunk]) if pre_buf else chunk
                            _speech_q.put(("start", init))
                            in_speech = True
                            speech_cnt = len(pre_buf) + 1
                            pre_buf.clear()
                        else:
                            _speech_q.put(("audio", chunk))
                            speech_cnt += 1
                        sil_cnt = 0
                    elif in_speech:
                        _speech_q.put(("audio", chunk))
                        speech_cnt += 1
                        sil_cnt += 1
                        if sil_cnt >= RT_SILENCE_CHUNKS:
                            dur = speech_cnt * VAD_CHUNK / TARGET_SR
                            print(f"[VAD] streaming flush silence {dur:.2f}s", flush=True)
                            _speech_q.put(("finish", None))
                            in_speech = False
                            sil_cnt = 0
                            speech_cnt = 0
                            pre_buf.clear()
                    else:
                        # 尚未偵測到語音：維持滾動預滾緩衝
                        pre_buf.append(chunk)

                    # Max buffer 8s：強制結束本段，保留 h/c
                    if in_speech and speech_cnt >= RT_MAX_BUFFER_CHUNKS:
                        dur = speech_cnt * VAD_CHUNK / TARGET_SR
                        print(f"[VAD] streaming flush max {dur:.2f}s", flush=True)
                        _speech_q.put(("finish", None))
                        in_speech = False
                        sil_cnt = 0
                        speech_cnt = 0
                        pre_buf.clear()

        except Exception as e:
            print(f"[VAD fatal error] {e}", flush=True)
            import traceback; traceback.print_exc()

    def _to_traditional(text: str, language: str) -> str:
        """若語言為中文（語言標籤或文字內含 CJK），將簡體轉成台灣繁體。"""
        is_chinese = (
            (language and any(kw in language.lower() for kw in ("chinese", "mandarin", "cantonese")))
            or any("\u4e00" <= c <= "\u9fff" for c in text)
        )
        if is_chinese:
            return _s2tw.convert(text)
        return text

    def asr_loop() -> None:
        """ASR 執行緒：streaming 轉錄，處理 ("start"/"audio"/"finish", audio) 事件。"""
        nonlocal current_original
        print("[ASR streaming] thread started", flush=True)

        # 連續段落合併（翻譯用）：VAD 斷句不等於語意句尾，拉長窗口讓更多片段合併
        CONCAT_WINDOW_SEC  = 8.0   # 8s 內的相鄰段落自動合併
        CONCAT_MAX_CHARS   = 200   # 超過此長度強制翻譯（保底）
        SENTENCE_ENDINGS   = set("。？！.?!")  # 偵測句尾的標點
        _last_asr_time = 0.0
        _accumulated_for_translation = ""

        session: ASRStreamingSession | None = None

        def _handle_result(result: dict, is_final: bool) -> None:
            """
            處理 ASR 結果：
            - 中間結果：只更新原文顯示，不翻譯
            - 最終結果：合併相鄰片段；有句尾標點或累積過長才送翻譯
            """
            nonlocal current_original, _last_asr_time, _accumulated_for_translation
            language = result.get("language", "")
            text = _to_traditional(result.get("text", ""), language)
            if not text or text == current_original:
                return
            current_original = text
            text_q.put({"raw": text})
            if is_final:
                now = time.time()
                if (_accumulated_for_translation
                        and now - _last_asr_time < CONCAT_WINDOW_SEC
                        and len(_accumulated_for_translation) < CONCAT_MAX_CHARS):
                    _accumulated_for_translation += text
                else:
                    _accumulated_for_translation = text
                _last_asr_time = now
                # 只在句尾標點或累積過長時才送翻譯，避免翻到半句話
                ends_with_sentence = bool(
                    _accumulated_for_translation
                    and _accumulated_for_translation[-1] in SENTENCE_ENDINGS
                )
                force_translate = len(_accumulated_for_translation) >= CONCAT_MAX_CHARS
                if ends_with_sentence or force_translate:
                    reason = "句尾" if ends_with_sentence else "保底"
                    print(f"[ASR] final [{reason}] lang={language!r} acc={_accumulated_for_translation!r}", flush=True)
                    debouncer.update(_accumulated_for_translation)
                    _accumulated_for_translation = ""  # 送出後清空，避免重複累積
                else:
                    print(f"[ASR] final [等待句尾] lang={language!r} text={text!r}", flush=True)
            else:
                print(f"[ASR] interim lang={language!r} text={text!r}", flush=True)

        while not _stop_event.is_set():
            try:
                event, audio = _speech_q.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                if event == "start":
                    session = ASRStreamingSession(
                        base_url=cfg["asr_server"],
                        language=_asr_lang,
                        context=_asr_context,
                    )
                    session.start()
                    # 推入初始音訊（預滾 + 首片段）
                    if len(audio) >= TARGET_SR // 8:
                        result = session.push(audio)
                        if result:
                            _handle_result(result, is_final=False)

                elif event == "audio":
                    if session is None:
                        continue
                    result = session.push(audio)
                    if result:
                        _handle_result(result, is_final=False)

                elif event == "finish":
                    if session is None:
                        continue
                    result = session.finish()
                    _handle_result(result, is_final=True)
                    session = None

            except Exception as e:
                print(f"[ASR streaming error] {e}", flush=True)
                if "timed out" in str(e).lower():
                    drained = 0
                    while not _speech_q.empty():
                        try:
                            _speech_q.get_nowait()
                            drained += 1
                        except queue.Empty:
                            break
                    if drained:
                        print(f"[ASR] Cleared {drained} stale events after timeout", flush=True)
                session = None

    vad_thread = threading.Thread(target=vad_loop, daemon=True, name="vad-thread")
    asr_thread = threading.Thread(target=asr_loop, daemon=True, name="asr-thread")
    vad_thread.start()
    asr_thread.start()

    audio_source.start(on_chunk)
    print("[Worker] Audio capture started.", flush=True)

    try:
        while True:
            if not cmd_q.empty():
                cmd = cmd_q.get()
                if cmd == "toggle":
                    new_dir = debouncer.toggle_direction()
                    text_q.put({"direction": new_dir})
                elif isinstance(cmd, str) and cmd.startswith("set_direction:"):
                    new_dir = cmd.split(":", 1)[1]
                    debouncer.set_direction(new_dir)
                    text_q.put({"direction": new_dir})
                elif cmd == "switch_source":
                    audio_source.stop()
                    if isinstance(audio_source, MonitorAudioSource):
                        audio_source = MicrophoneAudioSource(device=cfg.get("mic_device"))
                        src_name = "mic"
                    else:
                        audio_source = MonitorAudioSource(device=cfg["monitor_device"])
                        src_name = "monitor"
                    audio_source.start(on_chunk)
                    text_q.put({"source": src_name})
                elif cmd == "stop":
                    break
            else:
                time.sleep(0.1)
    finally:
        _stop_event.set()
        audio_source.stop()
        debouncer.shutdown()
        vad_thread.join(timeout=3)
        asr_thread.join(timeout=5)
        print("[Worker] Stopped.", flush=True)
