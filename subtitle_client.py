#!/usr/bin/env python3
"""
Real-time subtitle overlay（Linux/Windows）。

Usage:
    python subtitle_client.py --asr-server http://<SERVER_IP>:8000 --openai-api-key sk-...

Requirements:
    pip install sounddevice numpy scipy requests openai
"""
import argparse
import json
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable

# ---------------------------------------------------------------------------
# Logging：主程序 + worker process 都寫到同一個 log 檔
# ---------------------------------------------------------------------------
_LOG_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.path.join(_LOG_DIR, "subtitle.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(processName)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

import numpy as np
import requests
import scipy.signal as signal
import tkinter as tk
if sys.platform == "win32":
    try:
        import ctypes
        _FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "NotoSansTC-SemiBold.ttf")
        ctypes.windll.gdi32.AddFontResourceExW(_FONT_PATH, 0x10, 0)
    except Exception:
        pass
try:
    import customtkinter as ctk
    _CTK_AVAILABLE = True
except ImportError:
    _CTK_AVAILABLE = False
from openai import OpenAI
from languages import (
    LANG_LABELS, LANG_NAME,
    lang_code_to_label, lang_label_to_code,
    parse_direction, swap_direction,
)

# GTK3 透明覆疊（Linux）
_GTK3_AVAILABLE = False
if sys.platform != "win32":
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo
        import cairo
        _GTK3_AVAILABLE = True
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ASR Client
# ---------------------------------------------------------------------------

class ASRClient:
    """HTTP client for Qwen3-ASR server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def transcribe(self, audio_float32: np.ndarray) -> dict:
        """
        One-shot 轉錄：送出整段 16kHz float32 音訊，回傳 {"language": str, "text": str}。
        audio_float32: shape (N,), dtype float32
        """
        r = requests.post(
            f"{self.base_url}/api/transcribe",
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
            direction = self.direction  # snapshot
        # lock 釋放後才呼叫 OpenAI
        src, tgt = parse_direction(direction)
        if src == "en" and tgt == "zh":
            system_msg = (
                "你是即時字幕翻譯員。將英文語音轉錄翻譯成自然流暢的繁體中文（台灣口語用語）。"
                "要求：\n"
                "1. 依照中文語法重新組句，不要逐字翻譯或照搬英文語序\n"
                "2. 使用台灣人日常說話的方式，口語自然\n"
                "3. 專有名詞、人名、品牌可保留英文原文\n"
                "4. 只輸出翻譯結果，不加任何解釋或標注"
            )
        elif src == "zh" and tgt == "en":
            system_msg = (
                "You are a real-time subtitle translator. "
                "Translate the Chinese speech transcript to natural, colloquial English. "
                "Output ONLY the translation, no explanations."
            )
        else:
            src_name = LANG_NAME.get(src, src)
            tgt_name = LANG_NAME.get(tgt, tgt)
            system_msg = (
                f"You are a real-time subtitle translator. "
                f"Translate the following {src_name} speech transcript to {tgt_name}. "
                f"Keep it natural and concise. Output ONLY the translation, no explanations."
            )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": text},
                ],
                max_tokens=200,
                temperature=0.1,
            )
            translated = response.choices[0].message.content.strip()
            print(f"[Translation] {translated}", flush=True)
            self.callback(translated)
        except Exception as e:
            print(f"[Translation error] {e}", flush=True)

    def shutdown(self):
        with self._lock:
            self._cancel_timer()


# ---------------------------------------------------------------------------
# Subtitle Overlay Window
# ---------------------------------------------------------------------------

class SubtitleOverlay:
    """
    Always-on-top 半透明字幕視窗，固定在指定螢幕底部。

    使用方式：
        overlay = SubtitleOverlay(screen_index=0)
        overlay.set_text(original="Hello world", translated="你好世界")
        overlay.run()  # 阻塞，在主執行緒呼叫
    """

    TOOLBAR_HEIGHT = 32
    DRAG_BAR_HEIGHT = 6
    WINDOW_HEIGHT = 150
    WINDOW_WIDTH = 900           # 預設值，__init__ 會依螢幕動態覆蓋
    CORNER_SIZE = 20
    EDGE_SIZE = 6
    TOOLBAR_BG = "#12122a"       # 深藍
    DRAG_BAR_COLOR = "#1e1e3e"   # 深藍灰，非純黑（不會被 transparentcolor 穿透）
    BTN_COLOR = "#c5d8f8"        # 淡藍白
    BTN_BG = "#1a1a38"
    BTN_HOVER = "#2e2e58"
    BG_COLOR = "#000000"
    TEXT_BG_COLOR = "#0d0d0d"    # 近黑但非純黑，作為字幕底板
    EN_COLOR = "#e0e0e0"         # 淡灰英文
    ZH_COLOR = "#ffffff"
    OUTLINE_COLOR = "#060606"    # 近黑描邊
    EN_FONT = ("Noto Sans TC SemiBold", 15)
    ZH_FONT = ("Noto Sans TC SemiBold", 24)  # 開源繁中字體

    def __init__(self, screen_index: int = 0, on_toggle_direction=None, on_switch_source=None):
        self._on_toggle_direction = on_toggle_direction
        self._on_switch_source = on_switch_source

        self._root = tk.Tk()

        # 用 tkinter 取螢幕尺寸（不依賴 screeninfo）
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        # 視窗寬度為螢幕的 80%（最小 900），高度固定
        self._win_w = max(900, int(screen_w * 0.80))
        self._win_h = self.WINDOW_HEIGHT
        self._x = (screen_w - self._win_w) // 2
        self._y = screen_h - self._win_h - 40

        self._root.wm_attributes("-topmost", True)
        if sys.platform == "win32":
            self._root.overrideredirect(True)
            self._root.wm_attributes("-transparentcolor", self.BG_COLOR)
        else:
            # Linux：用 splash 類型讓 Mutter compositor 套用透明度
            # overrideredirect 的視窗不受 WM 管理，compositor 不對其合成
            self._root.wm_attributes("-type", "splash")
            self._root.wm_attributes("-alpha", 0.35)
        self._root.configure(bg=self.BG_COLOR)
        self._root.geometry(
            f"{self._win_w}x{self._win_h}+{self._x}+{self._y}"
        )

        # ── 拖拉條（常駐頂部，提供拖拉控點） ──
        drag_bar = tk.Frame(
            self._root,
            bg=self.DRAG_BAR_COLOR,
            height=self.DRAG_BAR_HEIGHT,
            cursor="",
        )
        drag_bar.pack(fill="x", side="top")
        drag_bar.pack_propagate(False)
        # 中心 grip 點
        grip = tk.Frame(drag_bar, bg="#3a3a70", width=32, height=2)
        grip.place(relx=0.5, rely=0.5, anchor="center")
        grip.lower()
        self._drag_bar = drag_bar
        # 拖拉條：中間拖拉，左右角落縮放
        drag_bar.bind("<Motion>", self._on_bar_motion)
        drag_bar.bind("<ButtonPress-1>", self._on_bar_press)
        drag_bar.bind("<B1-Motion>", self._do_drag)

        # ── Canvas (created after drag bar, fills remaining space) ──
        self._canvas = tk.Canvas(
            self._root,
            bg=self.BG_COLOR,
            highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda e: self._redraw_text())

        # 四角縮放：motion 偵測游標位置，press 開始縮放
        self._canvas.bind("<Motion>", self._on_canvas_motion)
        self._canvas.bind("<ButtonPress-1>", self._on_canvas_press)

        # ── 工具列 (created after canvas so it has higher z-order) ──
        toolbar = tk.Frame(self._root, bg=self.TOOLBAR_BG, height=self.TOOLBAR_HEIGHT)
        toolbar.place(x=0, y=0, relwidth=1.0, height=self.TOOLBAR_HEIGHT)
        toolbar.place_forget()
        self._toolbar = toolbar

        def _make_btn(parent, textvariable=None, text=None, command=None, side="left"):
            btn = tk.Button(
                parent,
                textvariable=textvariable,
                text=text,
                font=("Segoe UI", 10),
                fg=self.BTN_COLOR,
                bg=self.BTN_BG,
                activeforeground="#ffffff",
                activebackground=self.BTN_HOVER,
                relief="flat",
                bd=0,
                padx=10,
                pady=2,
                cursor="hand2",
                command=command,
            )
            btn.bind("<Enter>", lambda e, b=btn: (b.config(bg=self.BTN_HOVER), self._show_toolbar()))
            btn.bind("<Leave>", lambda e, b=btn: (b.config(bg=self.BTN_BG), self._hide_toolbar()))
            btn.bind("<ButtonPress-1>", lambda e: self._show_toolbar())
            btn.pack(side=side, padx=3, pady=3)
            return btn

        self._dir_btn_var = tk.StringVar(value="EN→ZH  ⇄")
        _make_btn(toolbar, textvariable=self._dir_btn_var, command=self._toggle_direction)

        self._src_btn_var = tk.StringVar(value="🔊 Monitor")
        _make_btn(toolbar, textvariable=self._src_btn_var, command=self._switch_source)

        _make_btn(toolbar, text="✕", command=self._do_close, side="right")

        self._toolbar_hide_id = None

        # 工具列由拖拉條觸發（hover 拖拉條 → 工具列展開並覆蓋拖拉條）
        # 工具列本身也支援拖拉（按住工具列空白處拖動）
        drag_bar.bind("<Enter>", self._show_toolbar)
        drag_bar.bind("<Leave>", self._hide_toolbar)
        self._toolbar.bind("<Enter>", self._show_toolbar)
        self._toolbar.bind("<Leave>", self._hide_toolbar)
        self._toolbar.bind("<Motion>", self._on_bar_motion)
        self._toolbar.bind("<ButtonPress-1>", self._on_bar_press)
        self._toolbar.bind("<B1-Motion>", self._do_drag)

        self._en_str = ""
        self._zh_str = ""
        self._drag_x = 0
        self._drag_y = 0
        self._resize_start = None   # (mouse_x, mouse_y, win_w, win_h, win_x, win_y, corner)

        self._root.bind("<Escape>", lambda e: self._do_close())
        self._root.bind("<F9>", lambda e: self._toggle_direction())
        self._root.protocol("WM_DELETE_WINDOW", self._do_close)

    def _apply_x11_opacity(self, alpha: float):
        """透過 xprop 設定 X11 _NET_WM_WINDOW_OPACITY，適用於 overrideredirect 視窗。"""
        try:
            wid = self._root.winfo_id()
            val = int(alpha * 0xFFFFFFFF)
            subprocess.run(
                ["xprop", "-id", str(wid),
                 "-f", "_NET_WM_WINDOW_OPACITY", "32c",
                 "-set", "_NET_WM_WINDOW_OPACITY", str(val)],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass

    def _do_close(self):
        """關閉視窗。"""
        self._root.destroy()

    def _show_toolbar(self, event=None):
        if self._toolbar_hide_id:
            self._root.after_cancel(self._toolbar_hide_id)
            self._toolbar_hide_id = None
        self._toolbar.place(x=0, y=0, relwidth=1.0, height=self.TOOLBAR_HEIGHT)
        self._toolbar.lift()

    def _hide_toolbar(self, event=None):
        self._toolbar_hide_id = self._root.after(
            400, lambda: self._toolbar.place_forget()
        )

    def _start_drag(self, event):
        self._drag_x = event.x_root - self._root.winfo_x()
        self._drag_y = event.y_root - self._root.winfo_y()

    def _do_drag(self, event):
        nx = event.x_root - self._drag_x
        ny = event.y_root - self._drag_y
        self._root.geometry(f"+{nx}+{ny}")

    _RESIZE_CURSORS = {
        "nw": "top_left_corner",  "ne": "top_right_corner",
        "sw": "bottom_left_corner", "se": "bottom_right_corner",
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
    }

    def _get_resize_zone(self, x: int, y: int):
        """Return resize zone ('nw','ne','sw','se','n','s','e','w') or None."""
        w = self._canvas.winfo_width() or self._root.winfo_width()
        h = self._canvas.winfo_height() or self._root.winfo_height()
        s, e = self.CORNER_SIZE, self.EDGE_SIZE
        in_l, in_r = x < s, x > w - s
        in_t, in_b = y < s, y > h - s
        if in_l and in_t:  return "nw"
        if in_r and in_t:  return "ne"
        if in_l and in_b:  return "sw"
        if in_r and in_b:  return "se"
        if x < e:          return "w"
        if x > w - e:      return "e"
        if y < e:          return "n"
        if y > h - e:      return "s"
        return None

    def _on_canvas_motion(self, event):
        zone = self._get_resize_zone(event.x, event.y)
        self._canvas.configure(cursor=self._RESIZE_CURSORS.get(zone, ""))

    def _on_canvas_press(self, event):
        zone = self._get_resize_zone(event.x, event.y)
        if zone:
            self._start_resize(event, zone)
            return "break"

    def _on_bar_motion(self, event):
        """拖拉條/工具列：頂部邊緣垂直縮放、左右角落對角縮放，中間無游標。"""
        bar_w = self._root.winfo_width()
        s, e = self.CORNER_SIZE, self.EDGE_SIZE
        if event.y < e:
            event.widget.configure(cursor="sb_v_double_arrow")
        elif event.x < s:
            event.widget.configure(cursor="top_left_corner")
        elif event.x > bar_w - s:
            event.widget.configure(cursor="top_right_corner")
        else:
            event.widget.configure(cursor="")

    def _on_bar_press(self, event):
        """拖拉條/工具列：頂部邊緣縮放、角落縮放，中間拖拉。"""
        # 若事件來自子元件（按鈕等），忽略，避免誤觸 resize/drag
        if event.widget not in (self._drag_bar, self._toolbar):
            return
        bar_w = self._root.winfo_width()
        s, e = self.CORNER_SIZE, self.EDGE_SIZE
        if event.y < e:
            self._start_resize(event, "n")
        elif event.x < s:
            self._start_resize(event, "nw")
        elif event.x > bar_w - s:
            self._start_resize(event, "ne")
        else:
            self._start_drag(event)

    def _start_resize(self, event, corner: str):
        self._resize_start = (
            event.x_root, event.y_root,
            self._root.winfo_width(), self._root.winfo_height(),
            self._root.winfo_x(), self._root.winfo_y(),
            corner,
        )
        self._root.bind("<B1-Motion>",       self._do_resize)
        self._root.bind("<ButtonRelease-1>", self._stop_resize)

    def _do_resize(self, event):
        if not self._resize_start:
            return
        mx0, my0, w0, h0, wx0, wy0, corner = self._resize_start
        dx = event.x_root - mx0
        dy = event.y_root - my0
        if corner == "se":
            new_w, new_h = max(300, w0 + dx), max(80, h0 + dy)
            self._root.geometry(f"{new_w}x{new_h}+{wx0}+{wy0}")
        elif corner == "sw":
            new_w, new_h = max(300, w0 - dx), max(80, h0 + dy)
            self._root.geometry(f"{new_w}x{new_h}+{wx0 + w0 - new_w}+{wy0}")
        elif corner == "ne":
            new_w, new_h = max(300, w0 + dx), max(80, h0 - dy)
            self._root.geometry(f"{new_w}x{new_h}+{wx0}+{wy0 + h0 - new_h}")
        elif corner == "nw":
            new_w, new_h = max(300, w0 - dx), max(80, h0 - dy)
            self._root.geometry(f"{new_w}x{new_h}+{wx0 + w0 - new_w}+{wy0 + h0 - new_h}")
        elif corner == "e":
            self._root.geometry(f"{max(300, w0 + dx)}x{h0}+{wx0}+{wy0}")
        elif corner == "w":
            new_w = max(300, w0 - dx)
            self._root.geometry(f"{new_w}x{h0}+{wx0 + w0 - new_w}+{wy0}")
        elif corner == "s":
            self._root.geometry(f"{w0}x{max(80, h0 + dy)}+{wx0}+{wy0}")
        elif corner == "n":
            new_h = max(80, h0 - dy)
            self._root.geometry(f"{w0}x{new_h}+{wx0}+{wy0 + h0 - new_h}")

    def _stop_resize(self, event):
        self._resize_start = None
        self._root.unbind("<B1-Motion>")
        self._root.unbind("<ButtonRelease-1>")

    def _toggle_direction(self):
        if self._on_toggle_direction:
            new_dir = self._on_toggle_direction()
            self.update_direction_label(new_dir)

    def update_direction_label(self, direction: str):
        label = f"{direction}  ⇄"
        self._root.after(0, lambda: self._dir_btn_var.set(label))

    def _switch_source(self):
        if self._on_switch_source:
            self._on_switch_source()

    def update_source_label(self, source: str):
        label = "🎤 Mic" if source == "mic" else "🔊 Monitor"
        self._root.after(0, lambda: self._src_btn_var.set(label))

    def set_text(self, original: str = "", translated: str = ""):
        """從任意執行緒安全地更新字幕（用 after() 排程到主執行緒）。"""
        def _update():
            self._en_str = original[-120:] if len(original) > 120 else original
            self._zh_str = translated[-60:] if len(translated) > 60 else translated
            self._redraw_text()
        self._root.after(0, _update)

    def _redraw_text(self):
        """Clear canvas and re-draw subtitle text with background pill and outline."""
        self._canvas.delete("text")

        w = self._canvas.winfo_width() or self._root.winfo_width()
        h = self._canvas.winfo_height() or self._root.winfo_height()
        wrap_w = max(200, w - 60)

        ex, ey = 24, 14

        # EN — 4方向描邊 + 主色
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self._canvas.create_text(ex+ox, ey+oy, text=self._en_str,
                                     fill=self.OUTLINE_COLOR, font=self.EN_FONT,
                                     anchor="nw", width=wrap_w, tags="text")
        self._canvas.create_text(ex, ey, text=self._en_str, fill=self.EN_COLOR,
                                 font=self.EN_FONT, anchor="nw", width=wrap_w, tags="text")

        # ZH — 4方向描邊 + 主色
        zy = ey + 36
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self._canvas.create_text(ex+ox, zy+oy, text=self._zh_str,
                                     fill=self.OUTLINE_COLOR, font=self.ZH_FONT,
                                     anchor="nw", width=wrap_w, tags="text")
        self._canvas.create_text(ex, zy, text=self._zh_str, fill=self.ZH_COLOR,
                                 font=self.ZH_FONT, anchor="nw", width=wrap_w, tags="text")

    def run(self):
        """啟動 tkinter mainloop（阻塞，必須在主執行緒呼叫）。"""
        self._root.mainloop()


# ---------------------------------------------------------------------------
# SubtitleOverlayGTK  ── GTK3 + Cairo 真透明版（Linux）
# ---------------------------------------------------------------------------

class SubtitleOverlayGTK:
    """
    GTK3 + Cairo 即時字幕覆疊，Linux 專用。

    特色：
    - RGBA visual + OPERATOR_CLEAR → 背景完全透明，只顯示文字
    - 拖拉條固定顯示（半透明深色）
    - 滑鼠移入 → 工具列展開；移出 400ms 後收回
    - 四角 + 四邊縮放，中間區域拖拉移動
    - 文字使用黑色外框增加可讀性
    """

    TOOLBAR_HEIGHT = 28
    DRAG_BAR_HEIGHT = 14
    WINDOW_HEIGHT = 160
    CORNER_SIZE = 20
    EDGE_SIZE = 6

    def __init__(self, screen_index: int = 0, on_toggle_direction=None, on_switch_source=None):
        self._on_toggle_direction = on_toggle_direction
        self._on_switch_source = on_switch_source
        self._en_str = ""
        self._zh_str = ""
        self._direction_label = "EN→ZH ⇄"
        self._source_label = "🔊 MON"
        self._toolbar_visible = False
        self._toolbar_hide_id = None
        self._resize_data = None   # (mx0, my0, w0, h0, wx0, wy0, zone)
        self._drag_offset = None   # (offset_x, offset_y)
        self._btn_rects: dict = {}

        self._win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._win.set_skip_taskbar_hint(True)
        self._win.set_skip_pager_hint(True)
        self._win.set_keep_above(True)

        # RGBA visual → per-pixel 透明
        screen = self._win.get_screen()
        rgba = screen.get_rgba_visual()
        if rgba:
            self._win.set_visual(rgba)
        self._win.set_app_paintable(True)

        # 視窗尺寸與位置
        display = Gdk.Display.get_default()
        mon = display.get_monitor(0)
        geo = mon.get_geometry()
        sw, sh = geo.width, geo.height
        ww = max(900, int(sw * 0.80))
        wh = self.WINDOW_HEIGHT
        self._win.set_default_size(ww, wh)
        self._win.move((sw - ww) // 2, sh - wh - 40)

        # DrawingArea：接收所有輸入事件
        da = Gtk.DrawingArea()
        da.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK |
            Gdk.EventMask.KEY_PRESS_MASK,
        )
        da.set_can_focus(True)
        da.grab_focus()
        da.connect("draw",                   self._on_draw)
        da.connect("button-press-event",     self._on_press)
        da.connect("button-release-event",   self._on_release)
        da.connect("motion-notify-event",    self._on_motion)
        da.connect("enter-notify-event",     self._on_enter)
        da.connect("leave-notify-event",     self._on_leave)
        da.connect("key-press-event",        self._on_key)
        self._da = da
        self._win.add(da)
        self._win.show_all()

    # ── Drawing ──────────────────────────────────────────────────────────────

    def _on_draw(self, da, cr):
        w = da.get_allocated_width()
        h = da.get_allocated_height()

        # 完全透明底色
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # 拖拉條（半透明深灰）
        cr.set_source_rgba(0.16, 0.16, 0.16, 0.85)
        cr.rectangle(0, 0, w, self.DRAG_BAR_HEIGHT)
        cr.fill()

        # 工具列
        if self._toolbar_visible:
            cr.set_source_rgba(0.13, 0.13, 0.13, 0.92)
            cr.rectangle(0, 0, w, self.TOOLBAR_HEIGHT)
            cr.fill()
            self._draw_toolbar(cr, w)

        # EN 字幕（黃色）
        ty = self.DRAG_BAR_HEIGHT + 12
        self._draw_outlined_text(cr, self._en_str, 20, ty, w - 40,
                                 (1.0, 0.87, 0.3), "Arial 15")
        # ZH 字幕（白色）
        self._draw_outlined_text(cr, self._zh_str, 20, ty + 35, w - 40,
                                 (1.0, 1.0, 1.0), "Noto Sans CJK TC Bold 22")

    def _draw_outlined_text(self, cr, text: str, x, y, max_w, rgb, font_str: str):
        if not text:
            return
        layout = PangoCairo.create_layout(cr)
        layout.set_text(text, -1)
        layout.set_font_description(Pango.FontDescription.from_string(font_str))
        layout.set_width(int(max_w * Pango.SCALE))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)

        # 黑色陰影（偏移 2px）
        cr.move_to(x + 2, y + 2)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.9)
        PangoCairo.show_layout(cr, layout)

        # 主色
        cr.move_to(x, y)
        cr.set_source_rgba(*rgb, 1.0)
        PangoCairo.show_layout(cr, layout)

    def _draw_toolbar(self, cr, win_w: int):
        """繪製工具列按鈕，同時記錄各按鈕的碰撞矩形。"""
        self._btn_rects = {}

        def draw_btn(text: str, x: int, key: str):
            layout = PangoCairo.create_layout(cr)
            layout.set_text(text, -1)
            layout.set_font_description(Pango.FontDescription.from_string("Arial 10"))
            pw, ph = layout.get_pixel_size()
            pad = 5
            bx, by, bw, bh = x - pad, 3, pw + pad * 2, ph + 4
            # 按鈕背景
            cr.set_source_rgba(0.22, 0.22, 0.22, 0.90)
            cr.rectangle(bx, by, bw, bh)
            cr.fill()
            # 文字
            cr.move_to(x, by + 2)
            cr.set_source_rgba(1, 1, 1, 1)
            PangoCairo.show_layout(cr, layout)
            self._btn_rects[key] = (bx, by, bw, bh)

        draw_btn(f"[{self._direction_label}]", 10,      "direction")
        draw_btn(f"[{self._source_label}]",    155,     "source")
        draw_btn("✕",                           win_w - 25, "close")

    # ── Resize zone ──────────────────────────────────────────────────────────

    def _get_resize_zone(self, x: float, y: float):
        w, h = self._win.get_size()
        s, e = self.CORNER_SIZE, self.EDGE_SIZE
        in_l = x < s;  in_r = x > w - s
        in_t = y < s;  in_b = y > h - s
        if in_l and in_t: return "nw"
        if in_r and in_t: return "ne"
        if in_l and in_b: return "sw"
        if in_r and in_b: return "se"
        if x < e:         return "w"
        if x > w - e:     return "e"
        if y < e:         return "n"
        if y > h - e:     return "s"
        return None

    @staticmethod
    def _build_cursors():
        return {
            "nw": Gdk.CursorType.TOP_LEFT_CORNER,
            "ne": Gdk.CursorType.TOP_RIGHT_CORNER,
            "sw": Gdk.CursorType.BOTTOM_LEFT_CORNER,
            "se": Gdk.CursorType.BOTTOM_RIGHT_CORNER,
            "n":  Gdk.CursorType.TOP_SIDE,
            "s":  Gdk.CursorType.BOTTOM_SIDE,
            "e":  Gdk.CursorType.RIGHT_SIDE,
            "w":  Gdk.CursorType.LEFT_SIDE,
        }

    def _set_cursor(self, ct):
        gw = self._win.get_window()
        if gw is None:
            return
        if ct is None:
            gw.set_cursor(None)
        else:
            gw.set_cursor(Gdk.Cursor.new_for_display(Gdk.Display.get_default(), ct))

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_motion(self, da, event):
        x, y = event.x, event.y

        if self._resize_data:
            mx0, my0, w0, h0, wx0, wy0, zone = self._resize_data
            self._do_resize(zone, event.x_root - mx0, event.y_root - my0,
                            w0, h0, wx0, wy0)
            return

        if self._drag_offset:
            ox, oy = self._drag_offset
            self._win.move(int(event.x_root - ox), int(event.y_root - oy))
            return

        zone = self._get_resize_zone(x, y)
        if zone:
            self._set_cursor(self._build_cursors().get(zone))
        elif y < self.DRAG_BAR_HEIGHT:
            self._set_cursor(Gdk.CursorType.FLEUR)
        else:
            self._set_cursor(None)

    def _do_resize(self, zone: str, dx: float, dy: float,
                   w0: int, h0: int, wx0: int, wy0: int):
        MIN_W, MIN_H = 300, 80
        dx, dy = int(dx), int(dy)
        if zone == "se":
            self._win.resize(max(MIN_W, w0 + dx), max(MIN_H, h0 + dy))
        elif zone == "sw":
            nw = max(MIN_W, w0 - dx)
            self._win.resize(nw, max(MIN_H, h0 + dy))
            self._win.move(wx0 + w0 - nw, wy0)
        elif zone == "ne":
            nh = max(MIN_H, h0 - dy)
            self._win.resize(max(MIN_W, w0 + dx), nh)
            self._win.move(wx0, wy0 + h0 - nh)
        elif zone == "nw":
            nw = max(MIN_W, w0 - dx)
            nh = max(MIN_H, h0 - dy)
            self._win.resize(nw, nh)
            self._win.move(wx0 + w0 - nw, wy0 + h0 - nh)
        elif zone == "e":
            self._win.resize(max(MIN_W, w0 + dx), h0)
        elif zone == "w":
            nw = max(MIN_W, w0 - dx)
            self._win.resize(nw, h0)
            self._win.move(wx0 + w0 - nw, wy0)
        elif zone == "s":
            self._win.resize(w0, max(MIN_H, h0 + dy))
        elif zone == "n":
            nh = max(MIN_H, h0 - dy)
            self._win.resize(w0, nh)
            self._win.move(wx0, wy0 + h0 - nh)

    def _on_press(self, da, event):
        if event.button != 1:
            return
        x, y = event.x, event.y

        zone = self._get_resize_zone(x, y)
        if zone:
            wx0, wy0 = self._win.get_position()
            w0, h0 = self._win.get_size()
            self._resize_data = (event.x_root, event.y_root, w0, h0, wx0, wy0, zone)
            return

        # 工具列按鈕點擊
        if self._toolbar_visible and y < self.TOOLBAR_HEIGHT:
            for key, (bx, by, bw, bh) in self._btn_rects.items():
                if bx <= x <= bx + bw and by <= y <= by + bh:
                    if key == "close":
                        Gtk.main_quit()
                    elif key == "direction" and self._on_toggle_direction:
                        new_dir = self._on_toggle_direction()
                        self._direction_label = new_dir + " ⇄"
                        self._da.queue_draw()
                    elif key == "source" and self._on_switch_source:
                        self._on_switch_source()
                    return

        # 拖拉
        wx0, wy0 = self._win.get_position()
        self._drag_offset = (event.x_root - wx0, event.y_root - wy0)

    def _on_release(self, da, event):
        self._resize_data = None
        self._drag_offset = None

    def _on_enter(self, da, event):
        self._show_toolbar()

    def _on_leave(self, da, event):
        # 過濾掉跨越子部件的假 leave 事件
        if event.detail in (Gdk.NotifyType.INFERIOR, Gdk.NotifyType.VIRTUAL):
            return
        self._schedule_hide_toolbar()

    def _on_key(self, da, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
        elif event.keyval == Gdk.KEY_F9:
            if self._on_toggle_direction:
                new_dir = self._on_toggle_direction()
                self._direction_label = new_dir + " ⇄"
                self._da.queue_draw()

    # ── Toolbar show/hide ─────────────────────────────────────────────────────

    def _show_toolbar(self):
        if self._toolbar_hide_id:
            GLib.source_remove(self._toolbar_hide_id)
            self._toolbar_hide_id = None
        self._toolbar_visible = True
        self._da.queue_draw()

    def _schedule_hide_toolbar(self):
        if self._toolbar_hide_id:
            GLib.source_remove(self._toolbar_hide_id)
        self._toolbar_hide_id = GLib.timeout_add(400, self._hide_toolbar)

    def _hide_toolbar(self):
        self._toolbar_visible = False
        self._toolbar_hide_id = None
        self._da.queue_draw()
        return False  # 不重複

    # ── Public API（與 SubtitleOverlay 相同介面）──────────────────────────────

    def update_direction_label(self, direction: str):
        def _u():
            self._direction_label = direction + " ⇄"
            if self._toolbar_visible:
                self._da.queue_draw()
            return False
        GLib.idle_add(_u)

    def update_source_label(self, source: str):
        def _u():
            self._source_label = "🎤 MIC" if source == "mic" else "🔊 MON"
            if self._toolbar_visible:
                self._da.queue_draw()
            return False
        GLib.idle_add(_u)

    def set_text(self, original: str = "", translated: str = ""):
        def _u():
            self._en_str = original[-120:] if len(original) > 120 else original
            self._zh_str = translated[-60:] if len(translated) > 60 else translated
            self._da.queue_draw()
            return False
        GLib.idle_add(_u)

    def run(self):
        """啟動 GTK mainloop（阻塞，必須在主執行緒呼叫）。"""
        Gtk.main()


# ---------------------------------------------------------------------------
# Audio Sources
# ---------------------------------------------------------------------------

TARGET_SR = 16000
CHUNK_SAMPLES = 8000  # 0.5 秒 @ 16kHz


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
        self._device = device  # None = 系統預設麥克風
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

# ---------------------------------------------------------------------------
# Worker Process（音訊 + ASR + 翻譯，無 X11）
# ---------------------------------------------------------------------------

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

    # Silero VAD 常數（v6 模型）
    VAD_CHUNK = 576               # 36ms @ 16kHz
    VAD_THRESHOLD = 0.5
    RT_SILENCE_CHUNKS = 14        # 0.5s - 靜音後觸發轉錄
    RT_MAX_BUFFER_CHUNKS = 138    # 5s   - 強制 flush（縮短延遲）

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
                result = asr.transcribe(audio)
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


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.expanduser("~/.config/realtime-subtitle/config.json")

_CONFIG_DEFAULTS = {
    "asr_server": "http://localhost:8000",
    "monitor_device": MonitorAudioSource.DEFAULT_DEVICE or "",
    "direction": "en→zh",
    "openai_api_key": "",
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
    keys = ["asr_server", "monitor_device", "direction", "openai_api_key"]
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({k: settings[k] for k in keys}, f, ensure_ascii=False, indent=2)


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


class SetupDialogGTK:
    """GTK3 啟動設定對話框（Linux）。"""

    def __init__(self, config: dict):
        self._result: dict | None = None
        self._config = config

    def run(self) -> dict | None:
        """顯示對話框，回傳設定 dict 或 None（取消）。"""
        win = Gtk.Dialog(title="Real-time Subtitle — 設定", flags=0)
        win.set_default_size(420, 1)
        win.set_border_width(16)
        win.add_button("取消", Gtk.ResponseType.CANCEL)
        win.add_button("開始字幕", Gtk.ResponseType.OK)
        win.set_default_response(Gtk.ResponseType.OK)

        box = win.get_content_area()
        box.set_spacing(12)

        # ASR Server URL
        box.add(Gtk.Label(label="ASR Server URL", xalign=0))
        url_entry = Gtk.Entry()
        url_entry.set_text(self._config.get("asr_server", "http://localhost:8000"))
        url_entry.set_activates_default(True)
        box.add(url_entry)

        # 音訊來源
        box.add(Gtk.Label(label="音訊來源", xalign=0))
        devices = _list_audio_devices_for_dialog()
        combo = Gtk.ComboBoxText.new_with_entry()
        saved_device = self._config.get("monitor_device", "")
        inserted_saved = False
        for i, d in enumerate(devices):
            combo.append_text(d)
            if d == saved_device:
                combo.set_active(i)
                inserted_saved = True
        if not inserted_saved and saved_device:
            combo.get_child().set_text(saved_device)
        elif not inserted_saved and devices:
            combo.set_active(0)

        box.add(combo)

        # 翻譯方向
        box.add(Gtk.Label(label="翻譯方向", xalign=0))
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        dir_box = Gtk.Box(spacing=8, orientation=Gtk.Orientation.HORIZONTAL)
        src_combo = Gtk.ComboBoxText()
        tgt_combo = Gtk.ComboBoxText()
        for i, lbl in enumerate(LANG_LABELS):
            src_combo.append_text(lbl)
            tgt_combo.append_text(lbl)
            if lang_label_to_code(lbl) == _src0:
                src_combo.set_active(i)
            if lang_label_to_code(lbl) == _tgt0:
                tgt_combo.set_active(i)
        def _gtk_swap(_btn):
            si, ti = src_combo.get_active(), tgt_combo.get_active()
            src_combo.set_active(ti)
            tgt_combo.set_active(si)
        swap_btn = Gtk.Button(label="⇄")
        swap_btn.connect("clicked", _gtk_swap)
        dir_box.pack_start(src_combo, True, True, 0)
        dir_box.pack_start(swap_btn, False, False, 0)
        dir_box.pack_start(tgt_combo, True, True, 0)
        box.add(dir_box)

        win.show_all()
        response = win.run()

        if response == Gtk.ResponseType.OK:
            device_text = combo.get_child().get_text().strip()
            _src_lbl = src_combo.get_active_text() or "en (English)"
            _tgt_lbl = tgt_combo.get_active_text() or "zh (中文)"
            self._result = {
                "asr_server": url_entry.get_text().strip() or "http://localhost:8000",
                "monitor_device": device_text,
                "direction": f"{lang_label_to_code(_src_lbl)}→{lang_label_to_code(_tgt_lbl)}",
            }
        win.destroy()
        return self._result


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

    # ------------------------------------------------------------------
    # CustomTkinter 版本
    # ------------------------------------------------------------------
    def _run_ctk(self) -> dict | None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

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
        root.mainloop()
        return self._result

    # ------------------------------------------------------------------
    # 純 tkinter fallback
    # ------------------------------------------------------------------
    def _run_tk(self) -> dict | None:
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

        def on_ok():
            self._result = {
                "asr_server": url_var.get().strip() or "http://localhost:8000",
                "monitor_device": device_var.get().strip(),
                "direction": f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}",
            }
            root.destroy()

        def on_cancel():
            root.destroy()

        tk.Button(btn_frame, text="取消", width=10, command=on_cancel).pack(side="left", padx=4)
        tk.Button(btn_frame, text="開始字幕", width=10, command=on_ok, default="active").pack(side="left", padx=4)
        root.bind("<Return>", lambda e: on_ok())
        root.protocol("WM_DELETE_WINDOW", on_cancel)
        root.mainloop()
        return self._result


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
        args.source = "monitor"   # 對話框目前只支援 monitor
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

    # 建立覆疊視窗（在 fork 之前完成 X11/GTK 初始化）
    log.info("建立字幕覆疊視窗 (screen=%d)", args.screen)
    use_gtk = _GTK3_AVAILABLE and sys.platform != "win32"
    try:
        if use_gtk:
            overlay = SubtitleOverlayGTK(
                screen_index=args.screen,
                on_toggle_direction=on_toggle,
                on_switch_source=on_switch_source,
            )
        else:
            overlay = SubtitleOverlay(
                screen_index=args.screen,
                on_toggle_direction=on_toggle,
                on_switch_source=on_switch_source,
            )
    except Exception:
        log.exception("建立覆疊視窗失敗")
        return
    overlay.update_direction_label(args.direction)
    log.info("覆疊視窗建立成功")

    # 覆疊視窗初始化後才 fork worker（child 不使用 X11/GTK）
    worker = multiprocessing.Process(
        target=_worker_main, args=(text_q, cmd_q, cfg),
        daemon=True, name="subtitle-worker",
    )
    worker.start()

    _last_translated = [""]  # 保留上一筆翻譯，直到新翻譯到來才替換

    def _poll_core():
        while not text_q.empty():
            msg = text_q.get()
            if "direction" in msg:
                overlay.update_direction_label(msg["direction"])
            elif "source" in msg:
                overlay.update_source_label(msg["source"])
            else:
                translated = msg.get("translated", "")
                if translated:
                    _last_translated[0] = translated
                overlay.set_text(
                    original=msg.get("original", ""),
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
        worker.join(timeout=3)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=1)

    import signal
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
