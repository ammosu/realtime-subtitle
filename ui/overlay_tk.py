# ui/overlay_tk.py
"""tkinter 字幕覆疊視窗（Windows / GTK 不可用時的 fallback）。"""
import logging
import subprocess
import sys
import tkinter as tk
from collections import deque

from languages import parse_direction, swap_direction

log = logging.getLogger(__name__)


class SubtitleOverlay:
    """
    Always-on-top 半透明字幕視窗，固定在指定螢幕底部。

    使用方式：
        overlay = SubtitleOverlay(screen_index=0)
        overlay.set_text(original="Hello world", translated="你好世界")
        overlay.run()  # 阻塞，在主執行緒呼叫
    """

    TOOLBAR_HEIGHT = 48
    DRAG_BAR_HEIGHT = 6
    WINDOW_HEIGHT = 310
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
    SUBTITLE_BG    = "#c0c0c0"   # 字幕底板：淺灰色（非黑，不會被 transparentcolor 穿透）
    SUBTITLE_ALPHA = 0.82        # 視窗整體透明度（讓底板呈半透明效果）
    _FONT_FAMILY = "Noto Sans TC SemiBold"
    DISCLAIMER_TEXT = "安富財經科技 ｜ AI 即時辨識，內容僅供參考"
    DISCLAIMER_COLOR = "#606060"
    DISCLAIMER_FONT = (_FONT_FAMILY, 10)

    def __init__(self, screen_index: int = 0, on_toggle_direction=None, on_switch_source=None, on_open_settings=None,
                 en_font_size: int = 15, zh_font_size: int = 24,
                 show_raw: bool = False, show_corrected: bool = True,
                 monitor_hint: tuple | None = None):
        self._on_toggle_direction = on_toggle_direction
        self._on_switch_source = on_switch_source
        self._on_open_settings = on_open_settings
        self.EN_FONT = (self._FONT_FAMILY, en_font_size)
        self.ZH_FONT = (self._FONT_FAMILY, zh_font_size)
        self._show_raw = show_raw
        self._show_corrected = show_corrected

        self._root = tk.Tk()

        # 視窗尺寸與位置：若有 monitor_hint 則定位到提示座標所在的螢幕
        mon_left, mon_top, mon_right, mon_bottom = self._resolve_monitor(monitor_hint)
        screen_w = mon_right - mon_left
        screen_h = mon_bottom - mon_top
        self._win_w = max(900, int(screen_w * 0.80))
        self._win_h = self.WINDOW_HEIGHT
        self._x = mon_left + (screen_w - self._win_w) // 2
        self._y = mon_bottom - self._win_h - 40

        self._root.wm_attributes("-topmost", True)
        if sys.platform == "win32":
            self._root.overrideredirect(True)
            self._root.wm_attributes("-transparentcolor", self.BG_COLOR)
            self._root.wm_attributes("-alpha", self.SUBTITLE_ALPHA)
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
        self._canvas.bind("<MouseWheel>", self._on_scroll)   # Windows
        self._canvas.bind("<Button-4>",   self._on_scroll)   # Linux scroll up
        self._canvas.bind("<Button-5>",   self._on_scroll)   # Linux scroll down

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
                font=("Segoe UI", 13),
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

        self._pause_btn_var = tk.StringVar(value="⏸ 暫停")
        _make_btn(toolbar, textvariable=self._pause_btn_var, command=self._toggle_pause)

        _make_btn(toolbar, text="✕", command=self._do_close, side="right")
        _make_btn(toolbar, text="⚙", command=self._open_settings, side="right")

        self._toolbar_hide_id = None

        # 工具列由拖拉條觸發（hover 拖拉條 → 工具列展開並覆蓋拖拉條）
        # 工具列本身也支援拖拉（按住工具列空白處拖動）
        drag_bar.bind("<Enter>", self._show_toolbar)
        drag_bar.bind("<Leave>", self._hide_toolbar)
        self._toolbar.bind("<Enter>", self._show_toolbar)
        self._toolbar.bind("<Leave>", self._hide_toolbar)
        self._toolbar.bind("<Motion>", self._on_bar_motion)
        self._toolbar.bind("<ButtonPress-1>", self._on_bar_press)

        self._paused = False
        # history & current-slot state
        self._history: deque = deque(maxlen=200)   # finalized entries {"original","translated"}
        self._scroll_offset: int = 0                 # 0=latest; wheel-up increases
        self._current_raw: str = ""                  # dim placeholder (raw ASR)
        # drag/resize state (unchanged)
        self._drag_x = 0
        self._drag_y = 0
        self._resize_start = None   # (mouse_x, mouse_y, win_w, win_h, win_x, win_y, corner)

        self._root.bind("<Escape>", lambda e: self._do_close())
        self._root.bind("<F9>", lambda e: self._toggle_direction())
        self._root.protocol("WM_DELETE_WINDOW", self._do_close)

    def _resolve_monitor(self, hint: tuple | None) -> tuple[int, int, int, int]:
        """回傳 (left, top, right, bottom)：hint 所在螢幕或主螢幕。"""
        if hint and sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class _MI(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint),
                                 ("rcMonitor", wintypes.RECT),
                                 ("rcWork", wintypes.RECT),
                                 ("dwFlags", ctypes.c_uint)]

                pt = wintypes.POINT(*hint)
                hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)
                mi = _MI()
                mi.cbSize = ctypes.sizeof(_MI)
                ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
                r = mi.rcMonitor
                return r.left, r.top, r.right, r.bottom
            except Exception:
                pass
        # fallback：主螢幕
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        return 0, 0, sw, sh

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
        self._root.bind("<B1-Motion>", self._do_drag)
        self._root.bind("<ButtonRelease-1>", self._stop_drag)

    def _stop_drag(self, event):
        self._root.unbind("<B1-Motion>")
        self._root.unbind("<ButtonRelease-1>")

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

    def _on_scroll(self, event) -> None:
        """滾輪：scroll up = 往歷史；scroll down = 往最新。"""
        # Windows: event.delta (+120 = up, -120 = down)
        # Linux:   event.num (4 = up, 5 = down)
        if sys.platform == "win32":
            going_older = event.delta > 0
        else:
            going_older = event.num == 4
        if going_older:
            max_off = max(0, len(self._history) - 1)
            self._scroll_offset = min(max_off, self._scroll_offset + 1)
        else:
            self._scroll_offset = max(0, self._scroll_offset - 1)
        self._redraw_text()

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

    def _open_settings(self):
        if self._on_open_settings:
            self._on_open_settings()

    def update_source_label(self, source: str):
        label = "🎤 Mic" if source == "mic" else "🔊 Monitor"
        self._root.after(0, lambda: self._src_btn_var.set(label))

    def _toggle_pause(self):
        self._paused = not self._paused
        if self._paused:
            self._pause_btn_var.set("▶ 繼續")
            self._drag_bar.configure(bg="#7a4a00")
        else:
            self._pause_btn_var.set("⏸ 暫停")
            self._drag_bar.configure(bg=self.DRAG_BAR_COLOR)
            self._redraw_text()

    def update_raw(self, raw: str) -> None:
        """任意執行緒：顯示原始 ASR 佔位文字（灰色淡顯）。"""
        def _update():
            if self._paused:
                return
            self._current_raw = raw
            self._scroll_offset = 0   # 新語音到來，自動回到最新
            self._redraw_text()
        self._root.after(0, _update)

    def finalize(self, original: str, translated: str) -> None:
        """任意執行緒：校正文字到來，推入歷史並清空 current。"""
        def _update():
            if self._paused:
                return
            if original or translated:
                self._history.append({"original": original, "translated": translated})
            self._current_raw = ""
            self._scroll_offset = 0
            self._redraw_text()
        self._root.after(0, _update)

    def reset(self) -> None:
        """清空所有字幕（換設定、暫停恢復時呼叫）。"""
        def _update():
            self._history.clear()
            self._current_raw = ""
            self._scroll_offset = 0
            self._redraw_text()
        self._root.after(0, _update)

    def set_text(self, raw: str = "", original: str = "", translated: str = "") -> None:
        """向下相容：僅用於清空（reset）場景。"""
        if not raw and not original and not translated:
            self.reset()

    def _redraw_text(self):
        """Clear canvas and re-draw up to 3 subtitle slots (2 history + current)."""
        self._canvas.delete("text")

        w = self._canvas.winfo_width() or self._root.winfo_width()
        h = self._canvas.winfo_height() or self._root.winfo_height()
        wrap_w = max(200, w - 60)
        ex = 24
        cur_y = 14

        def _draw_slot(original: str, translated: str, is_raw: bool = False) -> None:
            """Draw one subtitle slot (EN line + ZH line). Updates cur_y via nonlocal."""
            nonlocal cur_y
            en_color = "#909090" if is_raw else self.EN_COLOR
            zh_color = "#909090" if is_raw else self.ZH_COLOR

            # EN / original line
            if original:
                if not is_raw:
                    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        self._canvas.create_text(
                            ex + ox, cur_y + oy, text=original,
                            fill=self.OUTLINE_COLOR, font=self.EN_FONT,
                            anchor="nw", width=wrap_w, tags="text")
                item = self._canvas.create_text(
                    ex, cur_y, text=original, fill=en_color,
                    font=self.EN_FONT, anchor="nw", width=wrap_w, tags="text")
                bbox = self._canvas.bbox(item)
                cur_y = (bbox[3] if bbox else cur_y + 20) + 4

            # ZH / translated line
            if translated:
                if not is_raw:
                    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        self._canvas.create_text(
                            ex + ox, cur_y + oy, text=translated,
                            fill=self.OUTLINE_COLOR, font=self.ZH_FONT,
                            anchor="nw", width=wrap_w, tags="text")
                item = self._canvas.create_text(
                    ex, cur_y, text=translated, fill=zh_color,
                    font=self.ZH_FONT, anchor="nw", width=wrap_w, tags="text")
                bbox = self._canvas.bbox(item)
                cur_y = (bbox[3] if bbox else cur_y + 30) + 14

        # ── Determine which 3 slots to display ───────────────────────────
        hist = list(self._history)
        off  = self._scroll_offset

        idx_a = len(hist) - 2 - off   # oldest visible history slot
        idx_b = len(hist) - 1 - off   # newest visible history slot

        if idx_a >= 0:
            e = hist[idx_a]
            _draw_slot(e["original"], e["translated"])

        if idx_b >= 0:
            e = hist[idx_b]
            _draw_slot(e["original"], e["translated"])

        if off == 0 and self._current_raw:
            _draw_slot(self._current_raw, "", is_raw=True)

        # ── Background pill behind all visible subtitle text ─────────────
        has_content = (idx_a >= 0 or idx_b >= 0 or bool(self._current_raw))
        if has_content:
            all_bbox = self._canvas.bbox("text")
            if all_bbox:
                pad = 10
                bg = self._canvas.create_rectangle(
                    max(0, all_bbox[0] - pad), max(0, all_bbox[1] - pad),
                    min(w, all_bbox[2] + pad), min(h, all_bbox[3] + pad),
                    fill=self.SUBTITLE_BG, outline="", tags="text",
                )
                self._canvas.tag_lower(bg)

        # ── Scroll hint (shown when viewing history) ──────────────────────
        if off > 0:
            hint = f"↑ 歷史 -{off}"
            self._canvas.create_text(
                w - 14, 8, text=hint, fill="#a0a0c0",
                font=(self._FONT_FAMILY, 9), anchor="ne", tags="text")

        # ── Disclaimer (bottom-right, always) ─────────────────────────────
        self._canvas.create_text(
            w - 10, h - 6, text=self.DISCLAIMER_TEXT,
            fill=self.DISCLAIMER_COLOR, font=self.DISCLAIMER_FONT,
            anchor="se", tags="text")

    def run(self):
        """啟動 tkinter mainloop（阻塞，必須在主執行緒呼叫）。"""
        self._root.mainloop()
