#!/usr/bin/env python3
"""
Real-time subtitle overlay（Linux/Windows）。

Usage:
    python subtitle_client.py --asr-server http://<SERVER_IP>:8000 --openai-api-key sk-...

Requirements:
    pip install sounddevice numpy scipy requests openai
"""
import argparse
import logging
import multiprocessing
import os
import signal
import sys

# ── Windows：將 NotoSansTC-SemiBold.ttf 載入 GDI，讓 tkinter/customtkinter 可用 ──
if sys.platform == "win32":
    try:
        import ctypes
        _FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NotoSansTC-SemiBold.ttf")
        ctypes.windll.gdi32.AddFontResourceExW(_FONT_PATH, 0x10, 0)
    except Exception:
        pass

from constants import _LOG_PATH
from audio import AudioSource, MonitorAudioSource
from worker import _worker_main
from config import load_config, save_config
from ui import _GTK3_AVAILABLE
from ui.overlay_gtk import SubtitleOverlayGTK
from ui.overlay_tk import SubtitleOverlay
from ui.dialog_gtk import SetupDialogGTK
from ui.dialog_tk import SetupDialogTk
from languages import swap_direction

if _GTK3_AVAILABLE:
    from gi.repository import GLib

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Setup Dialog Dispatcher
# ---------------------------------------------------------------------------

def show_setup_dialog(config: dict) -> dict | None:
    """選擇正確的對話框實作並顯示，回傳設定 dict 或 None（取消）。"""
    if _GTK3_AVAILABLE and sys.platform != "win32":
        return SetupDialogGTK(config).run()
    return SetupDialogTk(config).run()


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== Real-time Subtitle 啟動 (pid=%d) ===", os.getpid())
    log.info("Log 檔位置: %s", _LOG_PATH)
    parser = argparse.ArgumentParser(description="Real-time subtitle overlay")
    parser.add_argument("--asr-server", default="http://localhost:8000",
                        help="Qwen3-ASR streaming server URL")
    parser.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY", ""),
                        help="OpenAI API key (or set OPENAI_API_KEY env var)")
    parser.add_argument("--screen", type=int, default=0,
                        help="Display screen index (0=primary, 1=secondary)")
    parser.add_argument("--list-devices", action="store_true",
                        help="List available audio devices and exit")
    parser.add_argument("--translation-model", default="gpt-4o-mini",
                        help="OpenAI model for translation")
    parser.add_argument("--source", choices=["monitor", "mic"], default="monitor",
                        help="Audio source: monitor（系統音訊）or mic（麥克風）")
    parser.add_argument("--monitor-device", default=MonitorAudioSource.DEFAULT_DEVICE,
                        help="音訊擷取裝置：Linux=PulseAudio monitor source 名稱；"
                             "Windows=WASAPI 輸出裝置名稱或索引（None=自動偵測預設輸出）。"
                             "用 --list-devices 查詢可用裝置")
    parser.add_argument("--mic-device", default=None,
                        help="麥克風裝置名稱或索引（None = 系統預設麥克風）")
    parser.add_argument("--direction", default="en→zh",
                        help="Initial translation direction, e.g. en→zh, zh→en, ja→en")
    parser.add_argument("--context", default="",
                        help="ASR 辨識提示詞，列出專有名詞、人名等可提升辨識準確度，"
                             "例如：'Qwen、vLLM、RAG、LangChain'")
    args = parser.parse_args()

    # CLI 是否已明確指定核心設定（可略過對話框）
    _cli_args = sys.argv[1:]
    _has_cli_config = (
        "--asr-server" in _cli_args or
        "--monitor-device" in _cli_args or
        "--source" in _cli_args or
        "--direction" in _cli_args
    )

    if not _has_cli_config and not args.list_devices:
        _file_config = load_config()
        _settings = show_setup_dialog(_file_config)
        if _settings is None:
            return  # 使用者取消
        save_config(_settings)
        # 把對話框結果回填進 args（後續程式碼繼續用 args.xxx）
        args.asr_server = _settings["asr_server"]
        args.monitor_device = _settings["monitor_device"]
        args.direction = _settings["direction"]
        args.source = _settings.get("source", "monitor")
        args.mic_device = _settings.get("mic_device", "")
        args.context = _settings.get("context", args.context)
        # dialog 填入的 key 優先，其次是 CLI/環境變數
        if _settings.get("openai_api_key"):
            args.openai_api_key = _settings["openai_api_key"]

    if args.list_devices:
        AudioSource.list_devices()
        return

    if not args.openai_api_key:
        log.error("OpenAI API Key 未設定，請在設定介面填入或設定 OPENAI_API_KEY 環境變數")
        return

    cfg = {
        "asr_server": args.asr_server,
        "openai_api_key": args.openai_api_key,
        "translation_model": args.translation_model,
        "source": args.source,
        "monitor_device": args.monitor_device,
        "mic_device": args.mic_device,
        "direction": args.direction,
        "context": args.context,
    }

    # 準備 IPC queues（用 SimpleQueue，不會在主程序產生 feeder 背景執行緒）
    text_q: multiprocessing.SimpleQueue = multiprocessing.SimpleQueue()
    cmd_q: multiprocessing.SimpleQueue = multiprocessing.SimpleQueue()

    # 本地方向追蹤（UI 用，與 worker 同步）
    current_direction = [args.direction]

    def on_toggle() -> str:
        current_direction[0] = swap_direction(current_direction[0])
        cmd_q.put("toggle")
        return current_direction[0]

    def on_switch_source() -> None:
        cmd_q.put("switch_source")

    # current_config 供設定 dialog 預填
    _current_config = {
        "asr_server": args.asr_server,
        "monitor_device": args.monitor_device,
        "mic_device": args.mic_device,
        "source": args.source,
        "direction": args.direction,
        "openai_api_key": args.openai_api_key,
        "context": args.context,
    }

    # 建立覆疊視窗（在 fork 之前完成 X11/GTK 初始化）
    log.info("建立字幕覆疊視窗 (screen=%d)", args.screen)
    use_gtk = _GTK3_AVAILABLE and sys.platform != "win32"
    _cfg_fonts = load_config()
    _en_font_size = int(_cfg_fonts.get("en_font_size", 15))
    _zh_font_size = int(_cfg_fonts.get("zh_font_size", 24))

    def _drain_queue(q: multiprocessing.SimpleQueue) -> None:
        """清空 SimpleQueue 中的殘留訊息。"""
        while not q.empty():
            try:
                q.get()
            except Exception:
                break

    def _start_worker(new_cfg: dict) -> multiprocessing.Process:
        w = multiprocessing.Process(
            target=_worker_main, args=(text_q, cmd_q, new_cfg),
            daemon=True, name="subtitle-worker",
        )
        w.start()
        return w

    def on_open_settings() -> None:
        if use_gtk:
            new_settings = SetupDialogGTK(_current_config).run()
        else:
            new_settings = SetupDialogTk(_current_config).run_as_toplevel(overlay._root)
        if new_settings is None:
            return
        save_config(new_settings)
        _current_config.update(new_settings)
        current_direction[0] = new_settings.get("direction", current_direction[0])

        # 停止舊 worker
        log.info("[Settings] 停止舊 worker...")
        cmd_q.put("stop")
        worker_ref[0].join(timeout=3)
        if worker_ref[0].is_alive():
            worker_ref[0].terminate()
            worker_ref[0].join(timeout=1)

        # 清空 queues 殘留訊息
        _drain_queue(text_q)
        _drain_queue(cmd_q)

        # 用新設定重啟 worker
        new_cfg = dict(cfg)
        for k in ("asr_server", "source", "monitor_device", "mic_device", "direction", "openai_api_key", "context"):
            if k in _current_config:
                new_cfg[k] = _current_config[k]
        worker_ref[0] = _start_worker(new_cfg)
        log.info("[Settings] Worker 重啟完成：asr=%s device=%s dir=%s",
                 new_cfg["asr_server"], new_cfg["monitor_device"], new_cfg["direction"])

        # 清空字幕畫面 + 同步 UI 標籤
        _last_original[0] = ""
        _last_translated[0] = ""
        overlay.set_text("", "")
        overlay.update_direction_label(current_direction[0])
        overlay.update_source_label(_current_config.get("source", "monitor"))

    try:
        if use_gtk:
            overlay = SubtitleOverlayGTK(
                screen_index=args.screen,
                on_toggle_direction=on_toggle,
                on_switch_source=on_switch_source,
                on_open_settings=on_open_settings,
            )
        else:
            overlay = SubtitleOverlay(
                screen_index=args.screen,
                on_toggle_direction=on_toggle,
                on_switch_source=on_switch_source,
                on_open_settings=on_open_settings,
                en_font_size=_en_font_size,
                zh_font_size=_zh_font_size,
            )
    except Exception:
        log.exception("建立覆疊視窗失敗")
        return
    overlay.update_direction_label(args.direction)
    log.info("覆疊視窗建立成功")

    # 覆疊視窗初始化後才 fork worker（child 不使用 X11/GTK）
    # 用 list 包裝，讓 on_open_settings closure 可以重新賦值
    worker_ref: list = [_start_worker(cfg)]

    _last_original   = [""]  # 保留上一筆原文，翻譯到來時不清掉
    _last_translated = [""]  # 保留上一筆翻譯，直到新翻譯到來才替換

    def _poll_core():
        while not text_q.empty():
            msg = text_q.get()
            if "direction" in msg:
                overlay.update_direction_label(msg["direction"])
            elif "source" in msg:
                overlay.update_source_label(msg["source"])
            else:
                # "original" 出現時更新原文；"translated" 出現時更新翻譯
                # 兩者各自獨立，互不清除
                if "original" in msg:
                    _last_original[0] = msg["original"]
                if msg.get("translated"):
                    _last_translated[0] = msg["translated"]
                overlay.set_text(
                    original=_last_original[0],
                    translated=_last_translated[0],
                )

    if use_gtk:
        def poll_gtk() -> bool:
            _poll_core()
            return True  # GLib：回傳 True 持續排程
        GLib.timeout_add(50, poll_gtk)
    else:
        def poll() -> None:
            _poll_core()
            overlay._root.after(50, poll)
        overlay._root.after(50, poll)

    def _cleanup():
        cmd_q.put("stop")
        worker_ref[0].join(timeout=3)
        if worker_ref[0].is_alive():
            worker_ref[0].terminate()
            worker_ref[0].join(timeout=1)

    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))
    signal.signal(signal.SIGINT,  lambda *_: (_cleanup(), sys.exit(0)))

    try:
        overlay.run()  # blocking，直到視窗關閉
    finally:
        _cleanup()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # PyInstaller 打包必需
    # spawn：全新 Python 程序，不繼承 X11 socket fd，避免 XCB 序號衝突
    multiprocessing.set_start_method("spawn")
    main()
