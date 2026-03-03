# ui/dialog_gtk.py
"""GTK3 啟動設定對話框（Linux）。"""
import logging
import os
import sys

if sys.platform != "win32":
    from gi.repository import Gtk, Gdk, Pango

from config import _list_audio_devices_for_dialog, _list_mic_devices_for_dialog
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
        btn_ok = win.add_button("▶  啟動字幕辨識", Gtk.ResponseType.OK)
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

        # OpenAI API Key（翻譯用，local 模式下為選填）
        _add_label("OpenAI API Key（翻譯用）", first=True)
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

        # ASR 後端選擇
        _add_label("ASR 後端")
        _saved_backend = self._config.get("backend", "remote")
        backend_box = Gtk.Box(spacing=8, orientation=Gtk.Orientation.HORIZONTAL)
        rb_local  = Gtk.RadioButton.new_with_label(None, "🖥 本地模型")
        rb_remote = Gtk.RadioButton.new_with_label_from_widget(rb_local, "🌐 遠端伺服器")
        if _saved_backend == "local":
            rb_local.set_active(True)
        else:
            rb_remote.set_active(True)
        backend_box.pack_start(rb_local,  False, False, 0)
        backend_box.pack_start(rb_remote, False, False, 0)
        body.add(backend_box)

        # ── 本地後端設定區 ──────────────────────────────────────────────
        local_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        local_model_lbl = Gtk.Label(label="模型路徑 (.bin)", xalign=0)
        local_model_lbl.get_style_context().add_class("field-label")
        local_box.add(local_model_lbl)
        # 自動偵測現有路徑作為預設值
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from local_downloader import detect_existing_paths as _dep, DEFAULT_MODEL_PATH as _DMP
            _det_model, _det_dir = _dep()
        except Exception:
            _det_model, _det_dir, _DMP = "", "", None

        local_model_entry = Gtk.Entry()
        local_model_entry.set_text(self._config.get("local_model_path", "") or _det_model)
        local_model_entry.set_placeholder_text("/path/to/qwen3-asr-1.7b.bin")
        local_box.add(local_model_entry)

        local_dir_lbl = Gtk.Label(label="chatllm 目錄", xalign=0)
        local_dir_lbl.get_style_context().add_class("field-label")
        local_box.add(local_dir_lbl)
        local_dir_entry = Gtk.Entry()
        local_dir_entry.set_text(self._config.get("local_chatllm_dir", "") or _det_dir)
        local_dir_entry.set_placeholder_text("/path/to/chatllm/")
        local_box.add(local_dir_entry)

        # 自動偵測 / 下載按鈕行
        _lact_box = Gtk.Box(spacing=6, orientation=Gtk.Orientation.HORIZONTAL)
        _auto_btn = Gtk.Button(label="⚡ 重新偵測")
        _dl_btn   = Gtk.Button(label="⬇ 下載模型")
        _lact_box.pack_start(_auto_btn, False, False, 0)
        _lact_box.pack_start(_dl_btn,   False, False, 0)
        local_box.add(_lact_box)
        _dl_status = Gtk.Label(label="", xalign=0)
        _dl_status.get_style_context().add_class("field-label")
        local_box.add(_dl_status)

        def _on_auto_detect(_b):
            try:
                from local_downloader import detect_existing_paths as _dep2
                m, c = _dep2()
            except Exception:
                m, c = "", ""
            if m:
                local_model_entry.set_text(m)
            if c:
                local_dir_entry.set_text(c)
            _dl_status.set_text(
                "✅ 偵測完成" if (m and c) else "⚠ 未找到路徑，請手動填入或下載模型"
            )
        _auto_btn.connect("clicked", _on_auto_detect)

        def _on_dl(_b):
            import threading
            from gi.repository import GLib
            _dl_btn.set_sensitive(False)
            _auto_btn.set_sensitive(False)
            try:
                from local_downloader import download_gguf as _dg, DEFAULT_MODEL_PATH as _dmp
            except Exception as ex:
                _dl_status.set_text(f"❌ 載入失敗：{ex}")
                _dl_btn.set_sensitive(True)
                return

            def _cb(pct, msg):
                GLib.idle_add(lambda: _dl_status.set_text(msg) or False)

            def _run():
                try:
                    _dg(progress_cb=_cb)
                    def _done():
                        local_model_entry.set_text(str(_dmp))
                        _dl_btn.set_sensitive(True)
                        _auto_btn.set_sensitive(True)
                        return False
                    GLib.idle_add(_done)
                except Exception as ex:
                    def _err():
                        _dl_status.set_text(f"❌ 下載失敗：{ex}")
                        _dl_btn.set_sensitive(True)
                        _auto_btn.set_sensitive(True)
                        return False
                    GLib.idle_add(_err)
            threading.Thread(target=_run, daemon=True).start()
        _dl_btn.connect("clicked", _on_dl)

        local_dev_lbl = Gtk.Label(label="GPU 裝置（留空 = CPU / 自動）", xalign=0)
        local_dev_lbl.get_style_context().add_class("field-label")
        local_box.add(local_dev_lbl)
        local_dev_combo = Gtk.ComboBoxText.new_with_entry()
        local_dev_combo.append_text("CPU（僅使用 CPU）")
        _saved_dev_id = int(self._config.get("local_device_id", 0))
        # 嘗試偵測 Vulkan 裝置
        _chatllm_dir = self._config.get("local_chatllm_dir", "")
        if _chatllm_dir:
            try:
                import sys as _sys
                _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from local_asr_engine import detect_vulkan_devices
                for dev in detect_vulkan_devices(_chatllm_dir):
                    local_dev_combo.append_text(f"GPU:{dev['id']} {dev['name']}")
            except Exception:
                pass
        local_dev_combo.set_active(min(_saved_dev_id + 1, 0))  # +1 因為第 0 項是 CPU
        local_box.add(local_dev_combo)

        body.add(local_box)

        # ── 遠端後端設定區 ──────────────────────────────────────────────
        remote_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        remote_url_lbl = Gtk.Label(label="ASR Server URL", xalign=0)
        remote_url_lbl.get_style_context().add_class("field-label")
        remote_box.add(remote_url_lbl)
        url_entry = Gtk.Entry()
        url_entry.set_text(self._config.get("asr_server", "http://localhost:8000"))
        url_entry.set_placeholder_text("http://localhost:8000")
        url_entry.set_activates_default(True)
        remote_box.add(url_entry)

        body.add(remote_box)

        def _on_backend_toggle(_btn):
            if rb_local.get_active():
                remote_box.hide()
                local_box.show_all()
            else:
                local_box.hide()
                remote_box.show_all()

        rb_local.connect("toggled", _on_backend_toggle)
        _on_backend_toggle(None)  # 初始化顯示

        # 音訊來源：系統音訊 / 麥克風 切換
        _add_label("音訊來源")
        _saved_source = self._config.get("source", "monitor")
        source_box = Gtk.Box(spacing=8, orientation=Gtk.Orientation.HORIZONTAL)
        rb_monitor = Gtk.RadioButton.new_with_label(None, "🔊 系統音訊")
        rb_mic = Gtk.RadioButton.new_with_label_from_widget(rb_monitor, "🎤 麥克風")
        if _saved_source == "mic":
            rb_mic.set_active(True)
        else:
            rb_monitor.set_active(True)
        source_box.pack_start(rb_monitor, False, False, 0)
        source_box.pack_start(rb_mic, False, False, 0)
        body.add(source_box)

        # 裝置選擇：monitor / mic 各自一個 ComboBoxText，依切換顯示
        def _make_device_combo(devices, saved_device):
            c = Gtk.ComboBoxText.new_with_entry()
            inserted = False
            for i, d in enumerate(devices):
                c.append_text(d)
                if d == saved_device:
                    c.set_active(i)
                    inserted = True
            if not inserted and saved_device:
                c.get_child().set_text(saved_device)
            elif not inserted and devices:
                c.set_active(0)
            return c

        monitor_devices = _list_audio_devices_for_dialog()
        mic_devices = _list_mic_devices_for_dialog()
        monitor_combo = _make_device_combo(monitor_devices, self._config.get("monitor_device", ""))
        mic_combo = _make_device_combo(mic_devices, self._config.get("mic_device", ""))

        device_stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        device_stack.add(monitor_combo)
        device_stack.add(mic_combo)
        body.add(device_stack)

        def _on_source_toggle(_btn):
            if rb_monitor.get_active():
                mic_combo.hide()
                monitor_combo.show()
            else:
                monitor_combo.hide()
                mic_combo.show()

        rb_monitor.connect("toggled", _on_source_toggle)
        _on_source_toggle(None)  # 初始化顯示

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

        # 進階設定
        _en_font_size = [int(self._config.get("en_font_size", 15))]
        _zh_font_size = [int(self._config.get("zh_font_size", 24))]
        _context = [self._config.get("context", "")]
        _show_raw = [bool(self._config.get("show_raw", False))]
        _show_corrected = [bool(self._config.get("show_corrected", True))]

        def _open_adv(_btn):
            adv = Gtk.Dialog(title="進階設定", flags=0, transient_for=win, modal=True)
            adv.set_default_size(400, -1)
            adv.set_border_width(0)
            adv.add_button("取消", Gtk.ResponseType.CANCEL)
            adv.add_button("確認", Gtk.ResponseType.OK)
            adv.set_default_response(Gtk.ResponseType.OK)

            adv_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            adv_body.set_border_width(20)

            def _add_adv_label(text):
                l = Gtk.Label(label=text, xalign=0)
                l.get_style_context().add_class("field-label")
                adv_body.add(l)

            def _make_slider(lo, hi, init):
                row = Gtk.Box(spacing=10, orientation=Gtk.Orientation.HORIZONTAL)
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
                scale.set_value(init)
                scale.set_digits(0)
                scale.set_draw_value(False)
                scale.set_hexpand(True)
                val_lbl = Gtk.Label(label=str(init), width_chars=3, xalign=1.0)
                scale.connect("value-changed", lambda s: val_lbl.set_text(str(int(s.get_value()))))
                row.pack_start(scale, True, True, 0)
                row.pack_start(val_lbl, False, False, 0)
                return row, scale

            _add_adv_label("辨識提示詞（選填）")
            adv_context = Gtk.Entry()
            adv_context.set_text(_context[0])
            adv_context.set_placeholder_text("專有名詞、人名…例：Qwen、vLLM、Jensen Huang")
            adv_body.add(adv_context)

            adv_raw_label = Gtk.Label(label="原文顯示", xalign=0)
            adv_raw_label.get_style_context().add_class("field-label")
            adv_body.add(adv_raw_label)
            show_corrected_cb = Gtk.CheckButton(label="顯示校正後 ASR 原文")
            show_corrected_cb.set_active(_show_corrected[0])
            adv_body.add(show_corrected_cb)
            show_raw_cb = Gtk.CheckButton(label="顯示原始 ASR 辨識")
            show_raw_cb.set_active(_show_raw[0])
            adv_body.add(show_raw_cb)

            _add_adv_label("辨識字體大小（原文）")
            en_row, en_scale = _make_slider(10, 30, _en_font_size[0])
            adv_body.add(en_row)

            _add_adv_label("翻譯字體大小")
            zh_row, zh_scale = _make_slider(14, 40, _zh_font_size[0])
            adv_body.add(zh_row)

            # 預覽區
            _add_adv_label("預覽")
            preview = Gtk.Frame()
            preview.set_shadow_type(Gtk.ShadowType.IN)
            pv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            pv_box.set_border_width(10)
            _PREVIEW_TEXT = "Hello, this is a subtitle preview."
            pv_raw = Gtk.Label(label=_PREVIEW_TEXT, xalign=0)
            pv_en = Gtk.Label(label=_PREVIEW_TEXT, xalign=0)
            pv_zh = Gtk.Label(label="這是即時字幕的預覽文字。", xalign=0)
            pv_raw.set_line_wrap(True)
            pv_en.set_line_wrap(True)
            pv_zh.set_line_wrap(True)

            def _update_preview(*_):
                pv_raw.override_font(
                    Pango.FontDescription.from_string(f"Sans {int(en_scale.get_value())}"))
                pv_en.override_font(
                    Pango.FontDescription.from_string(f"Sans {int(en_scale.get_value())}"))
                pv_zh.override_font(
                    Pango.FontDescription.from_string(f"Sans Bold {int(zh_scale.get_value())}"))

            en_scale.connect("value-changed", _update_preview)
            zh_scale.connect("value-changed", _update_preview)
            _update_preview()

            def _on_raw_toggle(cb):
                if cb.get_active():
                    pv_raw.show()
                else:
                    pv_raw.hide()

            def _on_corrected_toggle(cb):
                if cb.get_active():
                    pv_en.show()
                else:
                    pv_en.hide()

            show_raw_cb.connect("toggled", _on_raw_toggle)
            show_corrected_cb.connect("toggled", _on_corrected_toggle)

            pv_box.add(pv_raw)
            pv_box.add(pv_en)
            pv_box.add(pv_zh)
            preview.add(pv_box)
            adv_body.add(preview)

            adv.get_content_area().add(adv_body)
            adv.show_all()
            # show_all 後依 checkbox 狀態決定預覽可見性
            _on_raw_toggle(show_raw_cb)
            _on_corrected_toggle(show_corrected_cb)

            if adv.run() == Gtk.ResponseType.OK:
                _en_font_size[0] = int(en_scale.get_value())
                _zh_font_size[0] = int(zh_scale.get_value())
                _context[0] = adv_context.get_text().strip()
                _show_raw[0] = show_raw_cb.get_active()
                _show_corrected[0] = show_corrected_cb.get_active()
                _hint = f"  ✎ {_context[0][:12]}…" if len(_context[0]) > 12 else (f"  ✎ {_context[0]}" if _context[0] else "")
                adv_btn.set_label(f"⚙ 進階設定  （原文 {_en_font_size[0]}pt / 翻譯 {_zh_font_size[0]}pt{_hint}）")
            adv.destroy()

        adv_btn = Gtk.Button(label=f"⚙ 進階設定  （原文 {_en_font_size[0]}pt / 翻譯 {_zh_font_size[0]}pt）")
        adv_btn.set_relief(Gtk.ReliefStyle.NONE)
        adv_btn.connect("clicked", _open_adv)
        body.add(adv_btn)

        # 警告訊息
        warn_label = Gtk.Label(xalign=0)
        warn_label.get_style_context().add_class("warn-label")
        body.add(warn_label)

        win.show_all()
        _on_source_toggle(None)  # show_all 後重設裝置顯示

        while True:
            response = win.run()
            if response != Gtk.ResponseType.OK:
                break
            _is_local = rb_local.get_active()
            # 遠端模式必填 API Key；本地模式 API Key 選填（翻譯用）
            if not _is_local and not key_entry.get_text().strip():
                warn_label.set_markup('<span color="red">⚠ 遠端模式需填入 OpenAI API Key</span>')
                continue
            if _is_local:
                if not local_model_entry.get_text().strip():
                    warn_label.set_markup('<span color="red">⚠ 請填入本地模型路徑</span>')
                    continue
                if not local_dir_entry.get_text().strip():
                    warn_label.set_markup('<span color="red">⚠ 請填入 chatllm 目錄</span>')
                    continue
            _is_monitor = rb_monitor.get_active()
            _src_lbl = src_combo.get_active_text() or "en (English)"
            _tgt_lbl = tgt_combo.get_active_text() or "zh (中文)"
            # GPU 裝置 ID：下拉第 0 項為 CPU，第 1+ 項為 GPU:N
            _dev_idx = local_dev_combo.get_active()
            _local_dev_id = max(0, _dev_idx - 1) if _dev_idx > 0 else 0
            self._result = {
                "backend": "local" if _is_local else "remote",
                "local_model_path": local_model_entry.get_text().strip(),
                "local_chatllm_dir": local_dir_entry.get_text().strip(),
                "local_device_id": _local_dev_id,
                "asr_server": url_entry.get_text().strip() or "http://localhost:8000",
                "source": "monitor" if _is_monitor else "mic",
                "monitor_device": monitor_combo.get_child().get_text().strip(),
                "mic_device": mic_combo.get_child().get_text().strip(),
                "direction": f"{lang_label_to_code(_src_lbl)}→{lang_label_to_code(_tgt_lbl)}",
                "openai_api_key": key_entry.get_text().strip(),
                "context": _context[0],
                "en_font_size": _en_font_size[0],
                "zh_font_size": _zh_font_size[0],
                "show_raw": _show_raw[0],
                "show_corrected": _show_corrected[0],
            }
            break

        if self._result:
            wx, wy = win.get_position()
            self._result["_dialog_x"] = wx
            self._result["_dialog_y"] = wy
        win.destroy()
        return self._result
