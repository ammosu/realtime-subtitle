# ui/dialog_tk.py
"""tkinter / CustomTkinter 啟動設定對話框（Windows / fallback）。"""
import logging
import os
import sys
import tkinter as tk

try:
    import customtkinter as ctk
    _CTK_AVAILABLE = True
except ImportError:
    _CTK_AVAILABLE = False

from config import _list_audio_devices_for_dialog, _list_mic_devices_for_dialog
from languages import LANG_LABELS, lang_code_to_label, lang_label_to_code, parse_direction

log = logging.getLogger(__name__)


class SetupDialogTk:
    """tkinter 啟動設定對話框（Windows / GTK 不可用時）。
    使用 CustomTkinter（若可用）以現代深色主題呈現。
    """

    def __init__(self, config: dict):
        self._config = config
        self._result: dict | None = None

    def run(self) -> dict | None:
        if _CTK_AVAILABLE:
            return self._run_ctk()
        return self._run_tk()

    def run_as_toplevel(self, parent) -> dict | None:
        """覆疊視窗執行中時，以 Toplevel 模式開啟（不建立新的 mainloop）。"""
        if _CTK_AVAILABLE:
            return self._run_ctk(parent=parent)
        return self._run_tk(parent=parent)

    # ------------------------------------------------------------------
    # CustomTkinter 版本
    # ------------------------------------------------------------------
    def _run_ctk(self, parent=None) -> dict | None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        if parent is not None:
            root = ctk.CTkToplevel(parent)
            root.attributes("-topmost", True)
        else:
            root = ctk.CTk()
        root.title("Real-time Subtitle")
        root.resizable(False, False)
        root.geometry("460x600")
        root.grab_set()

        _noto_sm = ctk.CTkFont(family="Noto Sans TC SemiBold", size=12)
        _noto_md = ctk.CTkFont(family="Noto Sans TC SemiBold", size=14)
        _noto_lg = ctk.CTkFont(family="Noto Sans TC SemiBold", size=18)

        # ── 標題列 ─────────────────────────────────────────────────────
        header = ctk.CTkFrame(root, fg_color=("#1a1a2e", "#1a1a2e"), corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="⚡  Real-time Subtitle",
            font=_noto_lg,
            text_color="#7eb8f7",
        ).pack(pady=14, padx=20, anchor="w")

        # ── 內容區 ─────────────────────────────────────────────────────
        body = ctk.CTkFrame(root, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(16, 8))

        # OpenAI API Key（優先讀 config，其次環境變數）
        _existing_key = (
            self._config.get("openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        ctk.CTkLabel(body, text="OpenAI API Key", font=_noto_sm,
                     text_color="#9ca3af", anchor="w").pack(fill="x")
        key_var = tk.StringVar(value=_existing_key)
        ctk.CTkEntry(body, textvariable=key_var, height=36, font=_noto_sm,
                     placeholder_text="sk-...", show="•").pack(fill="x", pady=(4, 14))

        # ASR Server URL
        ctk.CTkLabel(body, text="ASR Server URL", font=_noto_sm,
                     text_color="#9ca3af", anchor="w").pack(fill="x")
        url_var = tk.StringVar(value=self._config.get("asr_server", "http://localhost:8000"))
        ctk.CTkEntry(body, textvariable=url_var, height=36, font=_noto_sm,
                     placeholder_text="http://localhost:8000").pack(fill="x", pady=(4, 14))

        # 音訊來源
        ctk.CTkLabel(body, text="音訊來源", font=_noto_sm,
                     text_color="#9ca3af", anchor="w").pack(fill="x")

        _saved_source = self._config.get("source", "monitor")
        source_var = tk.StringVar(value="🔊 系統音訊" if _saved_source == "monitor" else "🎤 麥克風")
        ctk.CTkSegmentedButton(body, values=["🔊 系統音訊", "🎤 麥克風"],
                               variable=source_var, font=_noto_sm,
                               height=34).pack(fill="x", pady=(4, 8))

        # 裝置選擇容器（固定位置，內部切換 monitor/mic）
        device_container = ctk.CTkFrame(body, fg_color="transparent")
        device_container.pack(fill="x", pady=(0, 14))

        # 系統音訊裝置
        monitor_devices = _list_audio_devices_for_dialog()
        _saved_mon = self._config.get("monitor_device", "")
        _mon_init = _saved_mon if _saved_mon in monitor_devices else (monitor_devices[0] if monitor_devices else _saved_mon)
        monitor_device_var = tk.StringVar(value=_mon_init)
        monitor_frame = ctk.CTkFrame(device_container, fg_color="transparent")
        if monitor_devices:
            ctk.CTkOptionMenu(monitor_frame, variable=monitor_device_var,
                              values=monitor_devices, height=36, font=_noto_sm,
                              dynamic_resizing=False).pack(fill="x")
        else:
            ctk.CTkEntry(monitor_frame, textvariable=monitor_device_var, height=36,
                         font=_noto_sm, placeholder_text="裝置名稱或索引").pack(fill="x")

        # 麥克風裝置
        mic_devices = _list_mic_devices_for_dialog()
        _saved_mic = self._config.get("mic_device", "")
        _mic_init = _saved_mic if _saved_mic in mic_devices else (mic_devices[0] if mic_devices else _saved_mic)
        mic_device_var = tk.StringVar(value=_mic_init)
        mic_frame = ctk.CTkFrame(device_container, fg_color="transparent")
        if mic_devices:
            ctk.CTkOptionMenu(mic_frame, variable=mic_device_var,
                              values=mic_devices, height=36, font=_noto_sm,
                              dynamic_resizing=False).pack(fill="x")
        else:
            ctk.CTkEntry(mic_frame, textvariable=mic_device_var, height=36,
                         font=_noto_sm, placeholder_text="麥克風裝置名稱").pack(fill="x")

        def _on_source_change(*_):
            if source_var.get() == "🔊 系統音訊":
                mic_frame.pack_forget()
                monitor_frame.pack(fill="x")
            else:
                monitor_frame.pack_forget()
                mic_frame.pack(fill="x")

        source_var.trace_add("write", _on_source_change)
        _on_source_change()  # 初始化顯示

        # 翻譯方向
        ctk.CTkLabel(body, text="翻譯方向", font=_noto_sm,
                     text_color="#9ca3af", anchor="w").pack(fill="x")
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        src_var = tk.StringVar(value=lang_code_to_label(_src0))
        tgt_var = tk.StringVar(value=lang_code_to_label(_tgt0))
        dir_row = ctk.CTkFrame(body, fg_color="transparent")
        dir_row.pack(fill="x", pady=(4, 0))
        dir_row.columnconfigure(0, weight=1)
        dir_row.columnconfigure(2, weight=1)
        ctk.CTkOptionMenu(dir_row, variable=src_var, values=LANG_LABELS,
                          height=34, font=_noto_sm,
                          dynamic_resizing=False).grid(row=0, column=0, sticky="ew")
        def _swap():
            s, t = src_var.get(), tgt_var.get()
            src_var.set(t)
            tgt_var.set(s)
        ctk.CTkButton(dir_row, text="⇄", width=40, height=34,
                      fg_color="#1a1a38", hover_color="#2e2e58",
                      text_color="#7eb8f7", font=_noto_sm,
                      command=_swap).grid(row=0, column=1, padx=6)
        ctk.CTkOptionMenu(dir_row, variable=tgt_var, values=LANG_LABELS,
                          height=34, font=_noto_sm,
                          dynamic_resizing=False).grid(row=0, column=2, sticky="ew")

        _warn_label = ctk.CTkLabel(body, text="", font=_noto_sm, text_color="#f87171")
        _warn_label.pack(fill="x")

        # ── 進階設定（跳出獨立小視窗）─────────────────────────────────
        _font_family = "Noto Sans TC SemiBold"
        _en_init = int(self._config.get("en_font_size", 15))
        _zh_init = int(self._config.get("zh_font_size", 24))
        en_size_var = tk.IntVar(value=_en_init)
        zh_size_var = tk.IntVar(value=_zh_init)
        context_var = tk.StringVar(value=self._config.get("context", ""))
        show_raw_var = tk.BooleanVar(value=bool(self._config.get("show_raw", False)))
        show_corrected_var = tk.BooleanVar(value=bool(self._config.get("show_corrected", True)))

        def _open_adv():
            popup = ctk.CTkToplevel(root)
            popup.title("進階設定")
            popup.resizable(False, False)
            popup.attributes("-topmost", True)
            popup.grab_set()
            # 置中於主視窗旁
            root.update_idletasks()
            rx, ry = root.winfo_x(), root.winfo_y()
            popup.geometry(f"440x800+{rx + 470}+{ry}")  # 先給大值，_resize 會調整

            pad = dict(padx=20, pady=(0, 10))

            ctk.CTkLabel(popup, text="⚙  進階設定",
                         font=_noto_md, text_color="#7eb8f7",
                         anchor="w").pack(fill="x", padx=20, pady=(16, 12))

            # 暫存 var（取消時不影響外層）
            tmp_en = tk.IntVar(value=en_size_var.get())
            tmp_zh = tk.IntVar(value=zh_size_var.get())
            tmp_context = tk.StringVar(value=context_var.get())
            tmp_show_corrected = tk.BooleanVar(value=show_corrected_var.get())
            tmp_show_raw = tk.BooleanVar(value=show_raw_var.get())

            # 辨識提示詞
            ctk.CTkLabel(popup, text="辨識提示詞（選填）", font=_noto_sm,
                         text_color="#9ca3af", anchor="w").pack(fill="x", padx=20)
            ctk.CTkEntry(popup, textvariable=tmp_context, height=36, font=_noto_sm,
                         placeholder_text="專有名詞、人名…例：Qwen、vLLM、Jensen Huang"
                         ).pack(fill="x", padx=20, pady=(4, 8))

            ctk.CTkLabel(popup, text="原文顯示", font=_noto_sm,
                         text_color="#9ca3af", anchor="w").pack(fill="x", padx=20)
            ctk.CTkCheckBox(popup, text="顯示校正後 ASR 原文",
                            variable=tmp_show_corrected, font=_noto_sm,
                            ).pack(anchor="w", padx=20, pady=(4, 2))
            ctk.CTkCheckBox(popup, text="顯示原始 ASR 辨識",
                            variable=tmp_show_raw, font=_noto_sm,
                            ).pack(anchor="w", padx=20, pady=(0, 14))

            def _make_row(parent, label: str, var: tk.IntVar, lo: int, hi: int):
                row = ctk.CTkFrame(parent, fg_color="transparent")
                row.pack(fill="x", **pad)
                row.columnconfigure(1, weight=1)
                ctk.CTkLabel(row, text=label, font=_noto_sm,
                             text_color="#9ca3af", width=110, anchor="w").grid(row=0, column=0)
                ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=hi - lo,
                              variable=var, height=18).grid(row=0, column=1, sticky="ew", padx=8)
                ctk.CTkLabel(row, textvariable=var, font=_noto_sm,
                             text_color="#c5d8f8", width=32).grid(row=0, column=2)

            _make_row(popup, "辨識字體大小", tmp_en, 10, 30)
            _make_row(popup, "翻譯字體大小", tmp_zh, 14, 40)

            # 預覽區（高度隨字體與顯示選項動態伸縮）
            _PREVIEW_TEXT = "Hello, this is a subtitle preview."
            _PREVIEW_ZH = "這是即時字幕的預覽文字。"
            preview_outer = ctk.CTkFrame(popup, fg_color="#0d0d1a", corner_radius=6)
            preview_outer.pack(fill="x", padx=20, pady=(4, 12))
            prev_raw = ctk.CTkLabel(preview_outer, text=_PREVIEW_TEXT,
                                    font=ctk.CTkFont(family=_font_family, size=tmp_en.get()),
                                    text_color="#808080", anchor="w", wraplength=390)
            prev_en = ctk.CTkLabel(preview_outer, text=_PREVIEW_TEXT,
                                   font=ctk.CTkFont(family=_font_family, size=tmp_en.get()),
                                   text_color="#e0e0e0", anchor="w", wraplength=390)
            prev_zh = ctk.CTkLabel(preview_outer, text=_PREVIEW_ZH,
                                   font=ctk.CTkFont(family=_font_family, size=tmp_zh.get()),
                                   text_color="#ffffff", anchor="w", wraplength=390)

            def _resize():
                popup.update_idletasks()
                popup.geometry(f"440x{popup.winfo_reqheight()}+{rx + 470}+{ry}")

            def _update(*_):
                prev_raw.configure(font=ctk.CTkFont(family=_font_family, size=tmp_en.get()))
                prev_en.configure(font=ctk.CTkFont(family=_font_family, size=tmp_en.get()))
                prev_zh.configure(font=ctk.CTkFont(family=_font_family, size=tmp_zh.get()))
                # 全部先 forget，再依序 pack 可見的層
                prev_raw.pack_forget()
                prev_en.pack_forget()
                prev_zh.pack_forget()
                first = [True]
                def _pack(lbl, font_var):
                    t = 10 if first[0] else 2
                    lbl.pack(fill="x", padx=12, pady=(t, 2))
                    first[0] = False
                if tmp_show_raw.get():
                    _pack(prev_raw, tmp_en)
                if tmp_show_corrected.get():
                    _pack(prev_en, tmp_en)
                t = 10 if first[0] else 2
                prev_zh.pack(fill="x", padx=12, pady=(t, 10))
                popup.after(50, _resize)

            tmp_en.trace_add("write", _update)
            tmp_zh.trace_add("write", _update)
            tmp_show_raw.trace_add("write", _update)
            tmp_show_corrected.trace_add("write", _update)
            _update()           # 初始渲染
            popup.after(100, _resize)  # 開啟時自動調整高度

            # 按鈕
            bf = ctk.CTkFrame(popup, fg_color="transparent")
            bf.pack(fill="x", padx=20, pady=(0, 16))
            bf.columnconfigure(0, weight=1)
            bf.columnconfigure(1, weight=1)

            def _adv_cancel():
                popup.destroy()

            def _adv_ok():
                en_size_var.set(tmp_en.get())
                zh_size_var.set(tmp_zh.get())
                context_var.set(tmp_context.get().strip())
                show_raw_var.set(tmp_show_raw.get())
                show_corrected_var.set(tmp_show_corrected.get())
                popup.destroy()

            ctk.CTkButton(bf, text="取消", fg_color="transparent",
                          border_width=1, border_color="#374151",
                          text_color="#9ca3af", hover_color="#1f2937",
                          font=_noto_sm, height=34,
                          command=_adv_cancel).grid(row=0, column=0, sticky="ew", padx=(0, 6))
            ctk.CTkButton(bf, text="確認", font=_noto_sm, height=34,
                          command=_adv_ok).grid(row=0, column=1, sticky="ew", padx=(6, 0))

            popup.protocol("WM_DELETE_WINDOW", _adv_cancel)
            popup.wait_window()

        ctk.CTkButton(
            root, text="⚙  進階設定", height=30,
            fg_color="transparent", hover_color="#1e1e3e",
            text_color="#7eb8f7", font=_noto_sm, anchor="w",
            command=_open_adv,
        ).pack(fill="x", padx=24, pady=(0, 8))

        # ── 按鈕列 ─────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(root, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=16)
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        def on_cancel():
            root.destroy()

        def on_ok():
            api_key = key_var.get().strip()
            if not api_key:
                _warn_label.configure(text="⚠ 請填入 OpenAI API Key")
                return
            _is_monitor = source_var.get() == "🔊 系統音訊"
            self._result = {
                "asr_server": url_var.get().strip() or "http://localhost:8000",
                "source": "monitor" if _is_monitor else "mic",
                "monitor_device": monitor_device_var.get().strip(),
                "mic_device": mic_device_var.get().strip(),
                "direction": f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}",
                "openai_api_key": api_key,
                "context": context_var.get().strip(),
                "en_font_size": en_size_var.get(),
                "zh_font_size": zh_size_var.get(),
                "show_raw": show_raw_var.get(),
                "show_corrected": show_corrected_var.get(),
                "_dialog_x": root.winfo_x(),
                "_dialog_y": root.winfo_y(),
            }
            root.destroy()

        ctk.CTkButton(btn_frame, text="取消", fg_color="transparent",
                      border_width=1, border_color="#374151",
                      text_color="#9ca3af", hover_color="#1f2937",
                      font=_noto_md, height=38, command=on_cancel).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(btn_frame, text="啟動字幕辨識", font=_noto_md, height=38,
                      command=on_ok).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        root.bind("<Return>", lambda e: on_ok())
        root.protocol("WM_DELETE_WINDOW", on_cancel)
        if parent is not None:
            parent.wait_window(root)
        else:
            root.mainloop()
        return self._result

    # ------------------------------------------------------------------
    # 純 tkinter fallback
    # ------------------------------------------------------------------
    def _run_tk(self, parent=None) -> dict | None:
        if parent is not None:
            root = tk.Toplevel(parent)
            root.attributes("-topmost", True)
        else:
            root = tk.Tk()
        root.title("Real-time Subtitle — 設定")
        root.resizable(False, False)
        root.grab_set()

        pad = {"padx": 12, "pady": 4}

        # OpenAI API Key（優先讀 config，其次環境變數）
        _existing_key = (
            self._config.get("openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        tk.Label(root, text="OpenAI API Key", anchor="w").pack(fill="x", **pad)
        key_var = tk.StringVar(value=_existing_key)
        tk.Entry(root, textvariable=key_var, show="*", width=48).pack(**pad)

        # ASR Server URL
        tk.Label(root, text="ASR Server URL", anchor="w").pack(fill="x", **pad)
        url_var = tk.StringVar(value=self._config.get("asr_server", "http://localhost:8000"))
        tk.Entry(root, textvariable=url_var, width=48).pack(**pad)

        # 音訊來源：monitor / mic 切換
        _saved_source = self._config.get("source", "monitor")
        source_var = tk.StringVar(value=_saved_source)
        tk.Label(root, text="音訊來源", anchor="w").pack(fill="x", **pad)
        source_frame = tk.Frame(root)
        source_frame.pack(fill="x", **pad)
        tk.Radiobutton(source_frame, text="🔊 系統音訊", variable=source_var,
                       value="monitor").pack(side="left")
        tk.Radiobutton(source_frame, text="🎤 麥克風", variable=source_var,
                       value="mic").pack(side="left")

        # 裝置選擇（monitor / mic 各自一個，依來源切換）
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
        _on_source_change()  # 初始化顯示

        # 翻譯方向
        tk.Label(root, text="翻譯方向", anchor="w").pack(fill="x", **pad)
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        src_var = tk.StringVar(value=lang_code_to_label(_src0))
        tgt_var = tk.StringVar(value=lang_code_to_label(_tgt0))
        dir_frame = tk.Frame(root)
        dir_frame.pack(**pad)
        tk.OptionMenu(dir_frame, src_var, *LANG_LABELS).pack(side="left")
        def _tk_swap():
            s, t = src_var.get(), tgt_var.get()
            src_var.set(t)
            tgt_var.set(s)
        tk.Button(dir_frame, text="⇄", command=_tk_swap).pack(side="left", padx=4)
        tk.OptionMenu(dir_frame, tgt_var, *LANG_LABELS).pack(side="left")

        # 進階設定（跳出獨立小視窗）
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
                           variable=tmp_show_corrected, anchor="w").pack(fill="x", padx=16, pady=(0, 0))
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

            def _adv_cancel():
                popup.destroy()

            def _adv_ok():
                en_size_var.set(tmp_en.get())
                zh_size_var.set(tmp_zh.get())
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
                "asr_server": url_var.get().strip() or "http://localhost:8000",
                "source": "monitor" if _is_monitor else "mic",
                "monitor_device": monitor_device_var.get().strip(),
                "mic_device": mic_device_var.get().strip(),
                "direction": f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}",
                "openai_api_key": api_key,
                "context": context_var.get().strip(),
                "en_font_size": en_size_var.get(),
                "zh_font_size": zh_size_var.get(),
                "show_raw": show_raw_var.get(),
                "show_corrected": show_corrected_var.get(),
                "_dialog_x": root.winfo_x(),
                "_dialog_y": root.winfo_y(),
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
