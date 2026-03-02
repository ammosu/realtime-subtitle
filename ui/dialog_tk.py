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
        tk.Label(root, text="OpenAI API Key", anchor="w").pack(fill="x", **pad)
        key_var = tk.StringVar(value=_existing_key)
        tk.Entry(root, textvariable=key_var, show="*", width=48).pack(**pad)

        tk.Label(root, text="ASR Server URL", anchor="w").pack(fill="x", **pad)
        url_var = tk.StringVar(value=self._config.get("asr_server", "http://localhost:8000"))
        tk.Entry(root, textvariable=url_var, width=48).pack(**pad)

        _saved_source = self._config.get("source", "monitor")
        source_var = tk.StringVar(value=_saved_source)
        tk.Label(root, text="音訊來源", anchor="w").pack(fill="x", **pad)
        source_frame = tk.Frame(root)
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

        device_frame = tk.Frame(root)
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
            api_key = key_var.get().strip()
            if not api_key:
                _warn_label.configure(text="⚠ 請填入 OpenAI API Key")
                return
            _is_monitor = source_var.get() == "monitor"
            self._result = {
                "asr_server":     url_var.get().strip() or "http://localhost:8000",
                "source":         "monitor" if _is_monitor else "mic",
                "monitor_device": monitor_device_var.get().strip(),
                "mic_device":     mic_device_var.get().strip(),
                "direction":      f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}",
                "openai_api_key": api_key,
                "context":        context_var.get().strip(),
                "en_font_size":   en_size_var.get(),
                "zh_font_size":   zh_size_var.get(),
                "show_raw":       show_raw_var.get(),
                "show_corrected": show_corrected_var.get(),
                "_dialog_x":      root.winfo_x(),
                "_dialog_y":      root.winfo_y(),
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
