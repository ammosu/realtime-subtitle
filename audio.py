# audio.py
"""音訊來源：Monitor（系統播放音）與 Microphone。"""
import logging
import os
import queue
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from typing import Callable

import numpy as np
import scipy.signal as signal

from constants import TARGET_SR, CHUNK_SAMPLES

log = logging.getLogger(__name__)


class AudioSource(ABC):
    """音訊來源抽象介面。未來可新增 MicrophoneAudioSource、NetworkAudioSource 等。"""

    @abstractmethod
    def start(self, callback: Callable[[np.ndarray], None]) -> None:
        """開始擷取音訊，每 0.5 秒以 16kHz float32 mono ndarray 呼叫 callback。"""

    @abstractmethod
    def stop(self) -> None:
        """停止擷取。"""

    @staticmethod
    def list_devices() -> None:
        """列出系統音訊裝置。"""
        import sounddevice as sd
        print("=== 音訊裝置清單 ===")
        print(sd.query_devices())
        if sys.platform == "win32":
            print("\n=== WASAPI Loopback 可用裝置（可用於 --monitor-device）===")
            try:
                wasapi_idx = next(
                    (i for i, api in enumerate(sd.query_hostapis()) if "wasapi" in api["name"].lower()),
                    None,
                )
                if wasapi_idx is not None:
                    for i, dev in enumerate(sd.query_devices()):
                        if dev["hostapi"] == wasapi_idx and dev["max_output_channels"] > 0:
                            print(f"  [{i}] {dev['name']} "
                                  f"({dev['max_output_channels']}ch, {int(dev['default_samplerate'])}Hz)")
                else:
                    print("  （找不到 WASAPI host API）")
            except Exception as e:
                print(f"  （無法列出 WASAPI 裝置：{e}）")
        else:
            print("\n=== PulseAudio Monitor Sources（可用於 --monitor-device）===")
            try:
                result = subprocess.run(
                    ["pactl", "list", "sources", "short"],
                    capture_output=True, text=True, timeout=3,
                )
                for line in result.stdout.splitlines():
                    if "monitor" in line.lower():
                        print(" ", line)
            except Exception:
                print("  （無法取得 PulseAudio sources，請確認 pactl 已安裝）")


class MonitorAudioSource(AudioSource):
    """
    擷取系統播放音訊。

    - Linux:   PipeWire/PulseAudio monitor source（透過 PULSE_SOURCE + ALSA pulse）
    - Windows: WASAPI Loopback（透過 sounddevice WasapiSettings）

    使用 queue.Queue 解耦音訊 callback 與 ASR HTTP 請求，避免
    阻塞操作污染即時音訊執行緒。
    """

    # Linux 預設 monitor source；Windows 為 None（自動偵測預設輸出裝置）
    DEFAULT_DEVICE = None if sys.platform == "win32" else "alsa_output.pci-0000_00_1f.3.iec958-stereo.monitor"
    ALSA_PULSE_DEVICE = "pulse"  # Linux only：ALSA pulse plugin

    def __init__(self, device: str | None = None):
        # Linux: PulseAudio source 名稱（None → DEFAULT_DEVICE）
        # Windows: 輸出裝置名稱或索引（None → 自動偵測預設輸出）
        self._device = device if sys.platform == "win32" else (device or self.DEFAULT_DEVICE)
        self._stream = None
        self._pa = None          # pyaudiowpatch instance (Windows only)
        self._buf: np.ndarray = np.zeros(0, dtype=np.float32)
        self._native_sr: int = 0
        self._callback: Callable[[np.ndarray], None] | None = None
        self._queue: queue.Queue = queue.Queue()
        self._running: bool = False
        self._consumer_thread: threading.Thread | None = None

    def start(self, callback: Callable[[np.ndarray], None]) -> None:
        if self._stream is not None:
            raise RuntimeError("MonitorAudioSource is already running; call stop() first.")

        import sounddevice as sd

        self._callback = callback
        self._buf = np.zeros(0, dtype=np.float32)
        self._running = True

        if sys.platform == "win32":
            self._setup_windows(sd)
        else:
            self._setup_linux(sd)

        # 消費者執行緒：從 queue 取音訊、resample、送 callback
        self._consumer_thread = threading.Thread(target=self._consumer, daemon=True)
        self._consumer_thread.start()
        self._stream.start()

    def _setup_linux(self, sd) -> None:
        """Linux：透過 PULSE_SOURCE + ALSA pulse device 擷取 monitor source。"""
        os.environ["PULSE_SOURCE"] = self._device
        dev_info = sd.query_devices(self.ALSA_PULSE_DEVICE, kind="input")
        self._native_sr = int(dev_info["default_samplerate"])  # 通常 44100 或 48000
        self._stream = sd.InputStream(
            samplerate=self._native_sr,
            channels=1,
            dtype="float32",
            blocksize=int(self._native_sr * 0.05),  # 50ms 固定 buffer
            device=self.ALSA_PULSE_DEVICE,
            callback=self._sd_callback,
        )

    def _setup_windows(self, sd) -> None:
        """Windows：透過 pyaudiowpatch WASAPI Loopback 擷取系統播放音訊。"""
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        wasapi_info = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)

        if self._device is not None:
            try:
                loopback_idx = int(self._device)
                dev_info = self._pa.get_device_info_by_index(loopback_idx)
            except ValueError:
                # device name string — search by name
                loopback_idx = None
                for i in range(self._pa.get_device_count()):
                    dev = self._pa.get_device_info_by_index(i)
                    if dev.get("isLoopbackDevice") and self._device in dev["name"]:
                        loopback_idx = i
                        dev_info = dev
                        break
                if loopback_idx is None:
                    raise RuntimeError(f"找不到裝置名稱含 '{self._device}' 的 WASAPI Loopback 裝置")
        else:
            # 自動：找預設輸出裝置對應的 loopback 裝置
            default_out_idx = wasapi_info["defaultOutputDevice"]
            default_out = self._pa.get_device_info_by_index(default_out_idx)
            loopback_idx = None
            for i in range(self._pa.get_device_count()):
                dev = self._pa.get_device_info_by_index(i)
                if dev.get("isLoopbackDevice") and dev["name"].startswith(default_out["name"]):
                    loopback_idx = i
                    dev_info = dev
                    break
            if loopback_idx is None:
                raise RuntimeError(
                    f"找不到 '{default_out['name']}' 的 WASAPI Loopback 裝置"
                )

        self._native_sr = int(dev_info["defaultSampleRate"])
        channels = max(int(dev_info["maxInputChannels"]), 1)
        print(f"[Monitor] WASAPI Loopback: {dev_info['name']}  sr={self._native_sr}  ch={channels}", flush=True)

        def _pa_callback(in_data, frame_count, time_info, status):
            audio = np.frombuffer(in_data, dtype=np.float32)
            if channels > 1:
                audio = audio.reshape(-1, channels)[:, 0]
            self._queue.put(audio.copy())
            return (None, pyaudio.paContinue)

        pa_stream = self._pa.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=self._native_sr,
            input=True,
            input_device_index=loopback_idx,
            frames_per_buffer=int(self._native_sr * 0.05),
            stream_callback=_pa_callback,
        )

        # 包裝成相容 sounddevice 介面的物件
        class _StreamWrapper:
            def __init__(self, s): self._s = s
            def start(self): self._s.start_stream()
            def stop(self): self._s.stop_stream()
            def close(self): self._s.close()

        self._stream = _StreamWrapper(pa_stream)

    def _sd_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """音訊執行緒 callback：只做最輕量的 enqueue，不做任何阻塞操作。"""
        if status:
            print(f"[Audio] {status}")
        self._queue.put(indata[:, 0].copy())

    def _consumer(self) -> None:
        """消費者執行緒：resample + 累積 buffer + 呼叫 ASR callback。"""
        while self._running:
            try:
                raw = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                # resample native_sr → 16kHz（在非即時執行緒中進行）
                target_len = int(len(raw) * TARGET_SR / self._native_sr)
                if target_len == 0:
                    continue
                resampled = signal.resample(raw, target_len).astype(np.float32)
                self._buf = np.concatenate([self._buf, resampled])

                # 每累積 CHUNK_SAMPLES 就送出一次
                while len(self._buf) >= CHUNK_SAMPLES:
                    chunk = self._buf[:CHUNK_SAMPLES].copy()
                    self._buf = self._buf[CHUNK_SAMPLES:]
                    if self._callback:
                        self._callback(chunk)
            except Exception as e:
                print(f"[Consumer error] {e}", flush=True)
                import traceback; traceback.print_exc()

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None
        if self._consumer_thread:
            self._consumer_thread.join(timeout=1.0)
            self._consumer_thread = None
        self._buf = np.zeros(0, dtype=np.float32)


class MicrophoneAudioSource(AudioSource):
    """麥克風音訊來源。"""

    def __init__(self, device=None):
        self._device = device or None  # 空字串或 None 都視為系統預設麥克風
        self._stream = None
        self._buf: np.ndarray = np.zeros(0, dtype=np.float32)
        self._native_sr: int = 0
        self._callback: Callable[[np.ndarray], None] | None = None
        self._queue: queue.Queue = queue.Queue()
        self._running: bool = False
        self._consumer_thread: threading.Thread | None = None

    def start(self, callback: Callable[[np.ndarray], None]) -> None:
        if self._stream is not None:
            raise RuntimeError("MicrophoneAudioSource is already running; call stop() first.")
        import sounddevice as sd
        dev_info = sd.query_devices(self._device, kind="input")
        self._native_sr = int(dev_info["default_samplerate"])
        self._callback = callback
        self._buf = np.zeros(0, dtype=np.float32)
        self._running = True
        self._consumer_thread = threading.Thread(target=self._consumer, daemon=True)
        self._consumer_thread.start()
        self._stream = sd.InputStream(
            samplerate=self._native_sr,
            channels=1,
            dtype="float32",
            blocksize=int(self._native_sr * 0.05),
            device=self._device,
            callback=self._sd_callback,
        )
        self._stream.start()

    def _sd_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            print(f"[Audio] {status}")
        self._queue.put(indata[:, 0].copy())

    def _consumer(self) -> None:
        while self._running:
            try:
                raw = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            target_len = int(len(raw) * TARGET_SR / self._native_sr)
            resampled = signal.resample(raw, target_len).astype(np.float32)
            self._buf = np.concatenate([self._buf, resampled])
            while len(self._buf) >= CHUNK_SAMPLES:
                chunk = self._buf[:CHUNK_SAMPLES].copy()
                self._buf = self._buf[CHUNK_SAMPLES:]
                if self._callback:
                    self._callback(chunk)

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._consumer_thread:
            self._consumer_thread.join(timeout=1.0)
            self._consumer_thread = None
        self._buf = np.zeros(0, dtype=np.float32)
