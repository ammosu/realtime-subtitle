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

from config import _list_audio_devices_for_dialog
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
        root.geometry("460x510")
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
        devices = _list_audio_devices_for_dialog()
        saved = self._config.get("monitor_device", "")
        initial = saved if saved in devices else (devices[0] if devices else saved)
        device_var = tk.StringVar(value=initial)

        if devices:
            ctk.CTkOptionMenu(body, variable=device_var, values=devices,
                              height=36, font=_noto_sm,
                              dynamic_resizing=False).pack(fill="x", pady=(4, 14))
        else:
            ctk.CTkEntry(body, textvariable=device_var, height=36, font=_noto_sm,
                         placeholder_text="裝置名稱或索引").pack(fill="x", pady=(4, 14))

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

        # ── 按鈕列 ─────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(root, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=16)
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        def on_cancel():
            root.destroy()

        _warn_label = ctk.CTkLabel(body, text="", font=_noto_sm, text_color="#f87171")
        _warn_label.pack(fill="x")

        def on_ok():
            api_key = key_var.get().strip()
            if not api_key:
                _warn_label.configure(text="⚠ 請填入 OpenAI API Key")
                return
            self._result = {
                "asr_server": url_var.get().strip() or "http://localhost:8000",
                "monitor_device": device_var.get().strip(),
                "direction": f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}",
                "openai_api_key": api_key,
            }
            root.destroy()

        ctk.CTkButton(btn_frame, text="取消", fg_color="transparent",
                      border_width=1, border_color="#374151",
                      text_color="#9ca3af", hover_color="#1f2937",
                      font=_noto_md, height=38, command=on_cancel).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(btn_frame, text="開始字幕", font=_noto_md, height=38,
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

        tk.Label(root, text="ASR Server URL", anchor="w").pack(fill="x", **pad)
        url_var = tk.StringVar(value=self._config.get("asr_server", "http://localhost:8000"))
        tk.Entry(root, textvariable=url_var, width=48).pack(**pad)

        tk.Label(root, text="音訊來源", anchor="w").pack(fill="x", **pad)
        devices = _list_audio_devices_for_dialog()
        device_var = tk.StringVar()
        saved = self._config.get("monitor_device", "")
        initial = saved if saved in devices else (devices[0] if devices else saved)
        device_var.set(initial)
        combo = tk.OptionMenu(root, device_var, *devices) if devices else tk.Entry(root, textvariable=device_var, width=48)
        combo.pack(fill="x", **pad)

        tk.Label(root, text="OpenAI API Key", anchor="w").pack(fill="x", **pad)
        key_var = tk.StringVar(value=self._config.get("openai_api_key", ""))
        tk.Entry(root, textvariable=key_var, show="*", width=48).pack(**pad)

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

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=12)

        _warn_label = tk.Label(root, text="", fg="red")
        _warn_label.pack()

        def on_ok():
            api_key = key_var.get().strip()
            if not api_key:
                _warn_label.configure(text="⚠ 請填入 OpenAI API Key")
                return
            self._result = {
                "asr_server": url_var.get().strip() or "http://localhost:8000",
                "monitor_device": device_var.get().strip(),
                "direction": f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}",
                "openai_api_key": api_key,
            }
            root.destroy()

        def on_cancel():
            root.destroy()

        tk.Button(btn_frame, text="取消", width=10, command=on_cancel).pack(side="left", padx=4)
        tk.Button(btn_frame, text="開始字幕", width=10, command=on_ok, default="active").pack(side="left", padx=4)
        root.bind("<Return>", lambda e: on_ok())
        root.protocol("WM_DELETE_WINDOW", on_cancel)
        if parent is not None:
            parent.wait_window(root)
        else:
            root.mainloop()
        return self._result
