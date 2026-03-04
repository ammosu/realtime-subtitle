# ui/dialog_tk.py
"""Plain tkinter 啟動設定對話框（wxPython 和 GTK 均不可用時的 fallback）。"""
import logging
import os
import sys
import tkinter as tk

from config import _list_audio_devices_for_dialog, _list_mic_devices_for_dialog
from languages import LANG_LABELS, lang_code_to_label, lang_label_to_code, parse_direction

log = logging.getLogger(__name__)


class SetupDialogTk:
    """Plain tkinter fallback 設定對話框（wxPython/GTK 均不可用時使用）。"""

    def __init__(self, config: dict):
        self._config = config
        self._result: dict | None = None

    def run(self) -> dict | None:
        return self._run_tk()

    def run_as_toplevel(self, parent) -> dict | None:
        return self._run_tk(parent=parent)

    def _run_tk(self, parent=None) -> dict | None:
        if parent is not None:
            root = tk.Toplevel(parent)
            root.attributes("-topmost", True)
            parent.update_idletasks()
            root.geometry(f"+{parent.winfo_x()}+{parent.winfo_y()}")
        else:
            root = tk.Tk()
        root.title("Real-time Subtitle — 設定")
        root.resizable(False, False)
        root.grab_set()

        pad = {"padx": 12, "pady": 4}

        _existing_key = (
            self._config.get("openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        tk.Label(root, text="OpenAI API Key（翻譯用，選填）", anchor="w").pack(fill="x", **pad)
        key_var = tk.StringVar(value=_existing_key)
        tk.Entry(root, textvariable=key_var, show="*", width=48).pack(**pad)

        # ── 運算模式 ──────────────────────────────────────────────────────
        tk.Label(root, text="運算模式", anchor="w").pack(fill="x", **pad)
        _saved_mode = self._config.get("backend", "local")
        _mode_var = tk.StringVar(value="remote" if _saved_mode == "remote" else "local")
        mode_frame = tk.Frame(root)
        mode_frame.pack(fill="x", **pad)
        tk.Radiobutton(mode_frame, text="🖥 本地模型",
                       variable=_mode_var, value="local").pack(side="left")
        tk.Radiobutton(mode_frame, text="🌐 外部伺服器（QwenASR）",
                       variable=_mode_var, value="remote").pack(side="left", padx=(8, 0))

        # ── 本地模型區 ─────────────────────────────────────────────────────
        _local_frame = tk.Frame(root)

        # 自動偵測路徑與 GPU 裝置
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from local_downloader import (detect_existing_paths as _dep,
                                          DEFAULT_MODEL_PATH as _DMP,
                                          DEFAULT_CHATLLM_DIR as _DCD)
            _det_model, _det_chatllm = _dep()
        except Exception:
            _det_model, _det_chatllm, _DMP, _DCD = "", "", None, None

        _gpu_devices: list[dict] = []
        _chatllm_for_detect = _det_chatllm or self._config.get("local_chatllm_dir", "")
        if _chatllm_for_detect:
            try:
                from local_asr_engine import detect_vulkan_devices
                _gpu_devices = detect_vulkan_devices(_chatllm_for_detect)
            except Exception:
                pass

        # 可變路徑狀態（閉包用）
        _model_path   = [self._config.get("local_model_path",  "") or _det_model]
        _chatllm_path = [self._config.get("local_chatllm_dir", "") or _det_chatllm]

        lpad = {"padx": 12, "pady": 4}

        # 偵測到的裝置資訊
        tk.Label(_local_frame, text="偵測到的裝置", anchor="w").pack(fill="x", **lpad)
        _dev_info_frame = tk.Frame(_local_frame)
        _dev_info_frame.pack(fill="x", padx=24, pady=(0, 4))
        tk.Label(_dev_info_frame, text="✅ CPU（可用）", anchor="w").pack(fill="x")
        if _gpu_devices:
            for _gd in _gpu_devices:
                _vram_gb  = _gd.get("vram_free", 0) / 1_073_741_824
                _vram_str = f"（可用 VRAM {_vram_gb:.1f} GB）" if _vram_gb > 0.1 else ""
                tk.Label(_dev_info_frame,
                         text=f"✅ GPU：{_gd['name']}{_vram_str}（Vulkan）",
                         anchor="w").pack(fill="x")
        else:
            tk.Label(_dev_info_frame,
                     text="ℹ 未偵測到獨立 GPU，僅 CPU 推理可用",
                     anchor="w", fg="gray").pack(fill="x")

        # 推理方式選擇
        tk.Label(_local_frame, text="推理方式", anchor="w").pack(fill="x", **lpad)
        _saved_dev_id = int(self._config.get("local_device_id", 0))
        _device_var   = tk.StringVar(value="cpu")   # "cpu" | "gpu:<id>"
        _dev_radio_frame = tk.Frame(_local_frame)
        _dev_radio_frame.pack(fill="x", **lpad)
        tk.Radiobutton(_dev_radio_frame, text="🖥 CPU",
                       variable=_device_var, value="cpu").pack(side="left")
        _gpu_id_map: dict[str, int] = {}   # value → gpu_id
        for _gd in _gpu_devices:
            _val = f"gpu:{_gd['id']}"
            _gpu_id_map[_val] = _gd["id"]
            tk.Radiobutton(_dev_radio_frame,
                           text=f"⚡ GPU ({_gd['name']}) [Vulkan]",
                           variable=_device_var, value=_val).pack(side="left")
            if _gd["id"] == _saved_dev_id:
                _device_var.set(_val)

        # 模型狀態 + 下載
        tk.Label(_local_frame, text="模型（qwen3-asr-1.7b.bin）", anchor="w").pack(fill="x", **lpad)
        _model_status_var = tk.StringVar()
        _dl_msg_var = tk.StringVar()
        _model_status_lbl = tk.Label(_local_frame, textvariable=_model_status_var, anchor="w")
        _model_status_lbl.pack(fill="x", padx=24)
        _dl_btn_tk = tk.Button(_local_frame, text="⬇ 下載（~2.3 GB）")
        tk.Label(_local_frame, textvariable=_dl_msg_var, anchor="w", fg="gray").pack(fill="x", padx=24)

        def _refresh_model_status_tk():
            if _model_path[0]:
                _model_status_var.set(f"✅ 已就緒：{os.path.basename(_model_path[0])}")
                _dl_btn_tk.pack_forget()
            else:
                _model_status_var.set("⚠ 未找到模型檔案")
                _dl_btn_tk.pack(padx=24, anchor="w")

        def _on_dl_tk():
            import threading
            _dl_btn_tk.configure(state="disabled")
            try:
                from local_downloader import download_gguf as _dg, DEFAULT_MODEL_PATH as _dmp
            except Exception as ex:
                _dl_msg_var.set(f"❌ 錯誤：{ex}")
                _dl_btn_tk.configure(state="normal")
                return

            def _cb(pct, msg):
                root.after(0, lambda: _dl_msg_var.set(msg))

            def _run():
                try:
                    _dg(progress_cb=_cb)
                    def _done():
                        _model_path[0] = str(_dmp)
                        _refresh_model_status_tk()
                        _dl_msg_var.set("✅ 下載完成")
                        _dl_btn_tk.configure(state="normal")
                    root.after(0, _done)
                except Exception as ex:
                    root.after(0, lambda: (
                        _dl_msg_var.set(f"❌ 下載失敗：{ex}"),
                        _dl_btn_tk.configure(state="normal"),
                    ))
            threading.Thread(target=_run, daemon=True).start()

        _dl_btn_tk.configure(command=_on_dl_tk)
        _refresh_model_status_tk()

        # chatllm 執行環境狀態
        tk.Label(_local_frame, text="chatllm 執行環境", anchor="w").pack(fill="x", **lpad)
        if _chatllm_path[0]:
            _chatllm_txt = f"✅ 已找到：{_chatllm_path[0]}"
            _chatllm_fg  = "green"
        else:
            _default_dir = str(_DCD) if _DCD else "~/.local/share/realtime-subtitle/chatllm/"
            _chatllm_txt = f"⚠ 未找到 chatllm 執行環境\n請從 chatllm.cpp 編譯後放至：{_default_dir}"
            _chatllm_fg  = "orange"
        tk.Label(_local_frame, text=_chatllm_txt, anchor="w", fg=_chatllm_fg,
                 justify="left").pack(fill="x", padx=24, pady=(0, 4))

        # ── 外部伺服器區 ───────────────────────────────────────────────────
        _server_frame = tk.Frame(root)

        _saved_server_url = (
            self._config.get("asr_server", "http://localhost:8765")
            if _saved_mode == "remote"
            else "http://localhost:8765"
        )
        tk.Label(_server_frame, text="QwenASR 伺服器 URL", anchor="w").pack(fill="x", **lpad)
        _server_url_var = tk.StringVar(value=_saved_server_url)
        tk.Entry(_server_frame, textvariable=_server_url_var, width=48).pack(**lpad)
        tk.Label(
            _server_frame,
            text=(
                "先在 QwenASRMiniTool 目錄執行：\n"
                "python server.py --model GPUModel/qwen3-asr-1.7b.bin --chatllm-dir chatllm --gpu"
            ),
            anchor="w", fg="gray", justify="left",
        ).pack(fill="x", padx=24, pady=(0, 4))

        # ── 模式切換邏輯 ────────────────────────────────────────────────────
        def _on_mode_change(*_):
            if _mode_var.get() == "local":
                _server_frame.pack_forget()
                _local_frame.pack(fill="x", before=_audio_anchor)
            else:
                _local_frame.pack_forget()
                _server_frame.pack(fill="x", before=_audio_anchor)

        # 音訊來源區（作為 local/server 區塊的錨點）
        _audio_anchor = tk.Frame(root)
        _audio_anchor.pack(fill="x")

        _mode_var.trace_add("write", _on_mode_change)

        # 初始顯示
        if _saved_mode == "remote":
            _server_frame.pack(fill="x", before=_audio_anchor)
        else:
            _local_frame.pack(fill="x", before=_audio_anchor)

        # ── 音訊來源 ──────────────────────────────────────────────────────
        _saved_source = self._config.get("source", "monitor")
        source_var = tk.StringVar(value=_saved_source)
        tk.Label(_audio_anchor, text="音訊來源", anchor="w").pack(fill="x", **pad)
        source_frame = tk.Frame(_audio_anchor)
        source_frame.pack(fill="x", **pad)
        tk.Radiobutton(source_frame, text="🔊 系統音訊", variable=source_var,
                       value="monitor").pack(side="left")
        tk.Radiobutton(source_frame, text="🎤 麥克風", variable=source_var,
                       value="mic").pack(side="left")

        monitor_devices = _list_audio_devices_for_dialog()
        _saved_mon = self._config.get("monitor_device", "")
        _mon_init = _saved_mon if _saved_mon in monitor_devices else (monitor_devices[0] if monitor_devices else _saved_mon)
        monitor_device_var = tk.StringVar(value=_mon_init)

        mic_devices = _list_mic_devices_for_dialog()
        _saved_mic = self._config.get("mic_device", "")
        _mic_init = _saved_mic if _saved_mic in mic_devices else (mic_devices[0] if mic_devices else _saved_mic)
        mic_device_var = tk.StringVar(value=_mic_init)

        device_frame = tk.Frame(_audio_anchor)
        device_frame.pack(fill="x", **pad)
        if monitor_devices:
            monitor_widget = tk.OptionMenu(device_frame, monitor_device_var, *monitor_devices)
        else:
            monitor_widget = tk.Entry(device_frame, textvariable=monitor_device_var, width=48)
        if mic_devices:
            mic_widget = tk.OptionMenu(device_frame, mic_device_var, *mic_devices)
        else:
            mic_widget = tk.Entry(device_frame, textvariable=mic_device_var, width=48)

        def _on_source_change(*_):
            if source_var.get() == "monitor":
                mic_widget.pack_forget()
                monitor_widget.pack(fill="x")
            else:
                monitor_widget.pack_forget()
                mic_widget.pack(fill="x")

        source_var.trace_add("write", _on_source_change)
        _on_source_change()

        tk.Label(root, text="翻譯方向", anchor="w").pack(fill="x", **pad)
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        src_var = tk.StringVar(value=lang_code_to_label(_src0))
        tgt_var = tk.StringVar(value=lang_code_to_label(_tgt0))
        dir_frame = tk.Frame(root)
        dir_frame.pack(**pad)
        tk.OptionMenu(dir_frame, src_var, *LANG_LABELS).pack(side="left")
        def _tk_swap():
            s, t = src_var.get(), tgt_var.get()
            src_var.set(t); tgt_var.set(s)
        tk.Button(dir_frame, text="⇄", command=_tk_swap).pack(side="left", padx=4)
        tk.OptionMenu(dir_frame, tgt_var, *LANG_LABELS).pack(side="left")

        en_size_var = tk.IntVar(value=int(self._config.get("en_font_size", 15)))
        zh_size_var = tk.IntVar(value=int(self._config.get("zh_font_size", 24)))
        context_var = tk.StringVar(value=self._config.get("context", ""))
        show_raw_var = tk.BooleanVar(value=bool(self._config.get("show_raw", False)))
        show_corrected_var = tk.BooleanVar(value=bool(self._config.get("show_corrected", True)))

        def _open_adv():
            popup = tk.Toplevel(root)
            popup.title("進階設定")
            popup.resizable(False, False)
            popup.attributes("-topmost", True)
            popup.grab_set()
            root.update_idletasks()
            rx, ry = root.winfo_x(), root.winfo_y()
            popup.geometry(f"+{rx + 480}+{ry}")
            apad = {"padx": 16, "pady": 4}
            tmp_en = tk.IntVar(value=en_size_var.get())
            tmp_zh = tk.IntVar(value=zh_size_var.get())
            tmp_context = tk.StringVar(value=context_var.get())
            tmp_show_raw = tk.BooleanVar(value=show_raw_var.get())
            tmp_show_corrected = tk.BooleanVar(value=show_corrected_var.get())
            tk.Label(popup, text="辨識提示詞（選填）", anchor="w").pack(fill="x", **apad)
            tk.Entry(popup, textvariable=tmp_context, width=44).pack(fill="x", **apad)
            tk.Label(popup, text="原文顯示", anchor="w").pack(fill="x", padx=16, pady=(4, 0))
            tk.Checkbutton(popup, text="顯示校正後 ASR 原文",
                           variable=tmp_show_corrected, anchor="w").pack(fill="x", padx=16)
            tk.Checkbutton(popup, text="顯示原始 ASR 辨識",
                           variable=tmp_show_raw, anchor="w").pack(fill="x", padx=16, pady=(0, 4))
            def _make_slider_row(label, var, lo, hi):
                row = tk.Frame(popup)
                row.pack(fill="x", **apad)
                tk.Label(row, text=label, width=14, anchor="w").pack(side="left")
                tk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=var,
                         showvalue=False, length=180).pack(side="left", fill="x", expand=True)
                tk.Label(row, textvariable=var, width=3).pack(side="left")
            _make_slider_row("辨識字體大小", tmp_en, 10, 30)
            _make_slider_row("翻譯字體大小", tmp_zh, 14, 40)
            bf = tk.Frame(popup)
            bf.pack(pady=8)
            def _adv_cancel(): popup.destroy()
            def _adv_ok():
                en_size_var.set(tmp_en.get()); zh_size_var.set(tmp_zh.get())
                context_var.set(tmp_context.get().strip())
                show_raw_var.set(tmp_show_raw.get())
                show_corrected_var.set(tmp_show_corrected.get())
                popup.destroy()
            tk.Button(bf, text="取消", width=10, command=_adv_cancel).pack(side="left", padx=4)
            tk.Button(bf, text="確認", width=10, command=_adv_ok,
                      default="active").pack(side="left", padx=4)
            popup.bind("<Return>", lambda e: _adv_ok())
            popup.protocol("WM_DELETE_WINDOW", _adv_cancel)
            popup.wait_window()

        tk.Button(root, text="⚙ 進階設定", anchor="w", relief="flat",
                  command=_open_adv).pack(fill="x", padx=12, pady=(4, 0))

        _warn_label = tk.Label(root, text="", fg="red")
        _warn_label.pack()

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=12)

        def on_ok():
            _is_monitor = source_var.get() == "monitor"
            _direction  = f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}"

            if _mode_var.get() == "remote":
                # ── 外部伺服器模式 ────────────────────────────────────────
                _server_url = _server_url_var.get().strip()
                if not _server_url:
                    _warn_label.configure(text="⚠ 請填入 QwenASR 伺服器 URL")
                    return
                self._result = {
                    "backend":           "remote",
                    "asr_server":        _server_url,
                    "local_model_path":  "",
                    "local_chatllm_dir": "",
                    "local_device_id":   0,
                    "source":            "monitor" if _is_monitor else "mic",
                    "monitor_device":    monitor_device_var.get().strip(),
                    "mic_device":        mic_device_var.get().strip(),
                    "direction":         _direction,
                    "openai_api_key":    key_var.get().strip(),
                    "context":           context_var.get().strip(),
                    "en_font_size":      en_size_var.get(),
                    "zh_font_size":      zh_size_var.get(),
                    "show_raw":          show_raw_var.get(),
                    "show_corrected":    show_corrected_var.get(),
                    "_dialog_x":         root.winfo_x(),
                    "_dialog_y":         root.winfo_y(),
                }
            else:
                # ── 本地模型模式 ──────────────────────────────────────────
                if not _model_path[0]:
                    _warn_label.configure(text="⚠ 請先下載模型檔案")
                    return
                if not _chatllm_path[0]:
                    _warn_label.configure(text="⚠ 未找到 chatllm 執行環境，請手動安裝")
                    return
                _dev_val      = _device_var.get()
                _local_dev_id = _gpu_id_map.get(_dev_val, 0)
                self._result = {
                    "backend":           "local",
                    "local_model_path":  _model_path[0],
                    "local_chatllm_dir": _chatllm_path[0],
                    "local_device_id":   _local_dev_id,
                    "asr_server":        "http://localhost:8765",
                    "source":            "monitor" if _is_monitor else "mic",
                    "monitor_device":    monitor_device_var.get().strip(),
                    "mic_device":        mic_device_var.get().strip(),
                    "direction":         _direction,
                    "openai_api_key":    key_var.get().strip(),
                    "context":           context_var.get().strip(),
                    "en_font_size":      en_size_var.get(),
                    "zh_font_size":      zh_size_var.get(),
                    "show_raw":          show_raw_var.get(),
                    "show_corrected":    show_corrected_var.get(),
                    "_dialog_x":         root.winfo_x(),
                    "_dialog_y":         root.winfo_y(),
                }
            root.destroy()

        def on_cancel():
            root.destroy()

        tk.Button(btn_frame, text="取消", width=10, command=on_cancel).pack(side="left", padx=4)
        tk.Button(btn_frame, text="啟動字幕辨識", width=14, command=on_ok,
                  default="active").pack(side="left", padx=4)
        root.bind("<Return>", lambda e: on_ok())
        root.protocol("WM_DELETE_WINDOW", on_cancel)
        if parent is not None:
            parent.wait_window(root)
        else:
            root.mainloop()
        return self._result
