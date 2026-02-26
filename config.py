# config.py
"""設定檔讀寫（~/.config/realtime-subtitle/config.json）與音訊裝置列舉。"""
import json
import logging
import os
import subprocess
import sys

from audio import MonitorAudioSource

log = logging.getLogger(__name__)

_CONFIG_PATH = os.path.expanduser("~/.config/realtime-subtitle/config.json")

_CONFIG_DEFAULTS = {
    "asr_server": "http://localhost:8000",
    "source": "monitor",
    "monitor_device": MonitorAudioSource.DEFAULT_DEVICE or "",
    "mic_device": "",
    "direction": "en→zh",
    "openai_api_key": "",
    "en_font_size": 15,
    "zh_font_size": 24,
}


def load_config() -> dict:
    """讀取 ~/.config/realtime-subtitle/config.json，不存在回傳預設值。"""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {**_CONFIG_DEFAULTS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_CONFIG_DEFAULTS)


def save_config(settings: dict) -> None:
    """儲存設定至 ~/.config/realtime-subtitle/config.json。"""
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    keys = ["asr_server", "source", "monitor_device", "mic_device", "direction", "openai_api_key", "en_font_size", "zh_font_size"]
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({k: settings.get(k, _CONFIG_DEFAULTS.get(k, "")) for k in keys}, f, ensure_ascii=False, indent=2)


def _list_audio_devices_for_dialog() -> list[str]:
    """
    回傳可用於下拉選單的音訊裝置名稱清單。
    Linux：pactl 列出 monitor source，失敗則 fallback sounddevice。
    Windows：sounddevice 列出輸入裝置。
    回傳空清單代表無法偵測（使用者手動填入）。
    """
    devices: list[str] = []
    if sys.platform == "win32":
        try:
            import pyaudiowpatch as pyaudio
            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                if dev.get("isLoopbackDevice"):
                    devices.append(dev["name"])
            pa.terminate()
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and "monitor" in parts[1].lower():
                    devices.append(parts[1])
        except Exception:
            pass
    if not devices:
        try:
            import sounddevice as sd
            for d in sd.query_devices():
                if d.get("max_input_channels", 0) > 0:
                    devices.append(d["name"])
        except Exception:
            pass
    return devices


def _list_mic_devices_for_dialog() -> list[str]:
    """回傳可用麥克風裝置名稱清單（排除 loopback）。"""
    devices: list[str] = []
    if sys.platform == "win32":
        try:
            import pyaudiowpatch as pyaudio
            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                if dev.get("maxInputChannels", 0) > 0 and not dev.get("isLoopbackDevice"):
                    devices.append(dev["name"])
            pa.terminate()
        except Exception:
            pass
    if not devices:
        try:
            import sounddevice as sd
            for d in sd.query_devices():
                if d.get("max_input_channels", 0) > 0:
                    devices.append(d["name"])
        except Exception:
            pass
    return devices
