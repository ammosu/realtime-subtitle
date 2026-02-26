# ui/dialog_gtk.py
"""GTK3 啟動設定對話框（Linux）。"""
import logging
import os
import sys

if sys.platform != "win32":
    from gi.repository import Gtk, Gdk

from config import _list_audio_devices_for_dialog
from languages import LANG_LABELS, lang_code_to_label, lang_label_to_code, parse_direction

log = logging.getLogger(__name__)


class SetupDialogGTK:
    """GTK3 啟動設定對話框（Linux）。"""

    def __init__(self, config: dict):
        self._result: dict | None = None
        self._config = config

    def run(self) -> dict | None:
        """顯示對話框，回傳設定 dict 或 None（取消）。"""
        # ── CSS 樣式 ────────────────────────────────────────────────────
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .app-header {
                padding: 16px 20px 12px 20px;
                border-bottom: 1px solid alpha(#000, 0.1);
            }
            .field-label {
                font-size: 11px;
                font-weight: bold;
                color: alpha(currentColor, 0.6);
                margin-top: 14px;
                margin-bottom: 3px;
            }
            .field-label.first {
                margin-top: 0;
            }
            entry {
                padding: 6px 10px;
                min-height: 34px;
            }
            combobox {
                min-height: 34px;
            }
            .warn-label {
                font-size: 11px;
                margin-top: 6px;
            }
            .dialog-body {
                padding: 20px 20px 8px 20px;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # ── 視窗 ────────────────────────────────────────────────────────
        win = Gtk.Dialog(title="Real-time Subtitle", flags=0)
        win.set_default_size(460, -1)
        win.set_border_width(0)
        win.set_resizable(False)

        btn_cancel = win.add_button("取消", Gtk.ResponseType.CANCEL)
        btn_ok = win.add_button("▶  開始字幕", Gtk.ResponseType.OK)
        btn_ok.get_style_context().add_class("suggested-action")
        win.set_default_response(Gtk.ResponseType.OK)

        outer = win.get_content_area()
        outer.set_spacing(0)

        # ── 標題區 ──────────────────────────────────────────────────────
        header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header_box.get_style_context().add_class("app-header")
        title_lbl = Gtk.Label(xalign=0)
        title_lbl.set_markup('<span size="large" weight="bold">⚡  Real-time Subtitle</span>')
        header_box.add(title_lbl)
        outer.add(header_box)

        # ── 表單區 ──────────────────────────────────────────────────────
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.get_style_context().add_class("dialog-body")
        outer.add(body)

        def _add_label(text, first=False):
            lbl = Gtk.Label(label=text, xalign=0)
            lbl.get_style_context().add_class("field-label")
            if first:
                lbl.get_style_context().add_class("first")
            body.add(lbl)

        # OpenAI API Key
        _add_label("OpenAI API Key", first=True)
        key_entry = Gtk.Entry()
        key_entry.set_visibility(False)
        key_entry.set_placeholder_text("sk-...")
        _existing_key = (
            self._config.get("openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        key_entry.set_text(_existing_key)
        key_entry.set_activates_default(True)
        body.add(key_entry)

        # ASR Server URL
        _add_label("ASR Server URL")
        url_entry = Gtk.Entry()
        url_entry.set_text(self._config.get("asr_server", "http://localhost:8000"))
        url_entry.set_placeholder_text("http://localhost:8000")
        url_entry.set_activates_default(True)
        body.add(url_entry)

        # 音訊來源
        _add_label("音訊來源")
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
        body.add(combo)

        # 翻譯方向
        # wrap_width=3 → GtkGrid 三欄模式，避免 GtkTreeView 置中造成頂部空白
        _add_label("翻譯方向")
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        dir_box = Gtk.Box(spacing=8, orientation=Gtk.Orientation.HORIZONTAL)
        src_combo = Gtk.ComboBoxText()
        tgt_combo = Gtk.ComboBoxText()
        src_combo.set_wrap_width(3)
        tgt_combo.set_wrap_width(3)
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
        body.add(dir_box)

        # 警告訊息
        warn_label = Gtk.Label(xalign=0)
        warn_label.get_style_context().add_class("warn-label")
        body.add(warn_label)

        win.show_all()

        while True:
            response = win.run()
            if response != Gtk.ResponseType.OK:
                break
            if not key_entry.get_text().strip():
                warn_label.set_markup('<span color="red">⚠ 請填入 OpenAI API Key</span>')
                continue
            device_text = combo.get_child().get_text().strip()
            _src_lbl = src_combo.get_active_text() or "en (English)"
            _tgt_lbl = tgt_combo.get_active_text() or "zh (中文)"
            self._result = {
                "asr_server": url_entry.get_text().strip() or "http://localhost:8000",
                "monitor_device": device_text,
                "direction": f"{lang_label_to_code(_src_lbl)}→{lang_label_to_code(_tgt_lbl)}",
                "openai_api_key": key_entry.get_text().strip(),
            }
            break

        win.destroy()
        return self._result
