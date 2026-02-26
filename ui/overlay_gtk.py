# ui/overlay_gtk.py
"""GTK3 + Cairo 字幕覆疊視窗（Linux，真透明背景）。"""
import logging
import sys

from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo
import cairo

from languages import parse_direction, swap_direction

log = logging.getLogger(__name__)


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

    def __init__(self, screen_index: int = 0, on_toggle_direction=None, on_switch_source=None, on_open_settings=None):
        self._on_toggle_direction = on_toggle_direction
        self._on_switch_source = on_switch_source
        self._on_open_settings = on_open_settings
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
        draw_btn("⚙",                           win_w - 55, "settings")
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

        # 工具列按鈕優先：hover 在按鈕上時不顯示縮放游標
        if self._toolbar_visible and y < self.TOOLBAR_HEIGHT:
            for bx, by, bw, bh in self._btn_rects.values():
                if bx <= x <= bx + bw and by <= y <= by + bh:
                    self._set_cursor(None)
                    return

        zone = self._get_resize_zone(x, y)
        if zone:
            self._set_cursor(self._build_cursors().get(zone))
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

        # 工具列按鈕優先：右上角關閉/設定按鈕與縮放區重疊，必須先做 hit-test
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
                    elif key == "settings" and self._on_open_settings:
                        self._on_open_settings()
                    return

        zone = self._get_resize_zone(x, y)
        if zone:
            wx0, wy0 = self._win.get_position()
            w0, h0 = self._win.get_size()
            self._resize_data = (event.x_root, event.y_root, w0, h0, wx0, wy0, zone)
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
