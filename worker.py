# worker.py
"""Worker subprocess：VAD + ASR + 翻譯 pipeline，不使用任何 X11/GTK。"""
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time

import numpy as np

from asr import ASRClient, TranslationDebouncer
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

    def on_translation(translated: str) -> None:
        text_q.put({"original": current_original, "translated": translated})

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

    asr = ASRClient(cfg["asr_server"])
    _src_lang, _ = parse_direction(cfg.get("direction", ""))
    _asr_lang = _src_lang if _src_lang else None  # 傳給 ASR server 的語言提示

    # Silero VAD 常數（v6 模型）
    VAD_CHUNK = 576               # 36ms @ 16kHz
    VAD_THRESHOLD = 0.5
    RT_SILENCE_CHUNKS = 14        # 0.5s - 靜音後觸發轉錄
    RT_MAX_BUFFER_CHUNKS = 222    # 8s   - 強制 flush（縮短延遲）

    # 載入 VAD 模型（打包後 worker spawn 中 __file__ 不可靠，改用 sys.executable）
    _base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    _vad_model_path = _base_dir / "silero_vad_v6.onnx"
    vad_sess = ort.InferenceSession(str(_vad_model_path))

    _vad_q: queue.Queue = queue.Queue()
    # _speech_q 傳送 (audio: np.ndarray, event: str)
    # event = "probe" - 短靜音，檢查是否句末再決定要不要顯示
    # event = "force" - 強制 flush（長靜音或 max buffer）
    _speech_q: queue.Queue = queue.Queue()
    _stop_event = threading.Event()

    def on_chunk(audio: np.ndarray) -> None:
        """非阻塞：只把音訊放入 VAD 佇列。"""
        _vad_q.put(audio)

    def vad_loop() -> None:
        """
        VAD 執行緒：靜音偵測。

        語音結束（靜音 ≥ 0.8s）或 buffer 達 10s 上限時，把整段語音送到 _speech_q。
        """
        h = np.zeros((1, 1, 128), dtype=np.float32)
        c = np.zeros((1, 1, 128), dtype=np.float32)
        buf: list[np.ndarray] = []
        sil_cnt = 0
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
                        buf.append(chunk)
                        sil_cnt = 0
                    elif buf:
                        buf.append(chunk)
                        sil_cnt += 1
                        if sil_cnt >= RT_SILENCE_CHUNKS:
                            # 靜音 0.8s：送出整段語音，保留 h/c 以免下句開頭被漏偵測
                            seg = np.concatenate(buf)
                            print(f"[VAD] flush silence {len(seg)/TARGET_SR:.2f}s", flush=True)
                            _speech_q.put(seg)
                            buf = []
                            sil_cnt = 0

                    # Max buffer 10s：強制送出，保留 h/c
                    if len(buf) >= RT_MAX_BUFFER_CHUNKS:
                        seg = np.concatenate(buf)
                        print(f"[VAD] flush max {len(seg)/TARGET_SR:.2f}s", flush=True)
                        _speech_q.put(seg)
                        buf = []
                        sil_cnt = 0

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
        """ASR 執行緒：one-shot 轉錄，收到整段語音就直接送 server 辨識。"""
        nonlocal current_original
        print("[ASR] thread started", flush=True)

        while not _stop_event.is_set():
            try:
                audio = _speech_q.get(timeout=0.5)
            except queue.Empty:
                continue

            if len(audio) < TARGET_SR // 8:   # < 0.125s，跳過
                continue

            try:
                result = asr.transcribe(audio, language=_asr_lang)
                language = result.get("language", "")
                text = _to_traditional(result.get("text", ""), language)
                print(f"[ASR] lang={language!r} text={text!r} same={text == current_original}", flush=True)

                if text and text != current_original:
                    current_original = text
                    text_q.put({"original": text, "translated": ""})
                    debouncer.update(text)  # 翻譯開啟

            except Exception as e:
                print(f"[Worker ASR error] {e}", flush=True)
                # timeout 後清空積壓的舊 chunk，避免 server 持續過載
                if "timed out" in str(e).lower():
                    drained = 0
                    while not _speech_q.empty():
                        try:
                            _speech_q.get_nowait()
                            drained += 1
                        except queue.Empty:
                            break
                    if drained:
                        print(f"[ASR] Cleared {drained} stale chunks after timeout", flush=True)

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
