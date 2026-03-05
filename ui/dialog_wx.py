# ui/dialog_wx.py
"""wxPython 啟動設定對話框（Windows，DPI-aware）。"""
import logging
import os
import sys

import wx
import wx.adv
from wx.lib.buttons import GenButton

from config import _list_audio_devices_for_dialog, _list_mic_devices_for_dialog
from languages import LANG_LABELS, lang_code_to_label, lang_label_to_code, parse_direction

log = logging.getLogger(__name__)

# ── Dark color palette ────────────────────────────────────────────────────────
_BG      = wx.Colour(13,  13,  26)   # body background  #0d0d1a
_HEADER  = wx.Colour(26,  26,  46)   # header strip     #1a1a2e
_TEXT    = wx.Colour(224, 224, 224)  # main text
_SUBTEXT = wx.Colour(156, 163, 175)  # label text       #9ca3af
_ACCENT  = wx.Colour(126, 184, 247)  # title/accent     #7eb8f7
_ENTRY   = wx.Colour(45,  45,  78)   # entry background
_WARN    = wx.Colour(248, 113, 113)  # error text       #f87171
_BTN_BG  = wx.Colour(26,  26,  60)   # cancel button bg
_SEP     = wx.Colour(55,  65,  81)   # separator/border #374151

# ── wx.App singleton ──────────────────────────────────────────────────────────
_wx_app = None

def _get_wx_app() -> wx.App:
    global _wx_app
    if _wx_app is None:
        _wx_app = wx.App(redirect=False)
    return _wx_app


# ── Helper: apply dark colours to any widget ─────────────────────────────────
def _dark(widget, bg=None, fg=None):
    if bg is not None:
        widget.SetBackgroundColour(bg)
    if fg is not None:
        widget.SetForegroundColour(fg)
    return widget


class _DarkCombo(wx.adv.OwnerDrawnComboBox):
    """Combo box with full dark-theme owner drawing (avoids native Win32 color issues)."""

    def OnDrawItem(self, dc, rect, item, flags):
        if item == wx.NOT_FOUND:
            return
        dc.SetFont(self.GetFont())
        is_ctl = bool(flags & wx.adv.ODCB_PAINTING_CONTROL)
        is_sel = bool(flags & wx.adv.ODCB_PAINTING_SELECTED)
        dc.SetTextForeground(_ACCENT if (is_sel and not is_ctl) else _TEXT)
        y = rect.y + max(0, (rect.height - dc.GetCharHeight()) // 2)
        dc.DrawText(self.GetString(item), rect.x + 6, y)

    def OnDrawBackground(self, dc, rect, item, flags):
        is_ctl = bool(flags & wx.adv.ODCB_PAINTING_CONTROL)
        is_sel = bool(flags & wx.adv.ODCB_PAINTING_SELECTED)
        bg = _HEADER if (is_sel and not is_ctl) else _ENTRY
        dc.SetBrush(wx.Brush(bg))
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.DrawRectangle(rect)

    def OnMeasureItem(self, item):
        return self.FromDIP(26)

    def OnMeasureItemWidth(self, item):
        return -1


_BTN_NORMAL  = wx.Colour(26,  26,  55)   # normal button background
_BTN_PRIMARY = wx.Colour(40,  60, 110)   # primary/OK button background

_UI_FONT_FACE = "Noto Sans TC SemiBold"  # loaded into GDI by subtitle_client.py


def _make_entry(parent, value="", style=0):
    """Vertically-centred dark text entry.

    A wx.Panel (with _ENTRY background) wraps a borderless TextCtrl whose
    natural height equals the font height.  Vertical padding from the panel
    provides the visual box height while keeping the text truly centred.
    """
    wrap = wx.Panel(parent)
    wrap.SetBackgroundColour(_ENTRY)
    wrap.SetFont(parent.GetFont())
    sizer = wx.BoxSizer(wx.VERTICAL)
    ctrl = wx.TextCtrl(wrap, value=value, style=style | wx.BORDER_NONE | wx.TE_RICH2)
    ctrl.SetBackgroundColour(_ENTRY)
    ctrl.SetForegroundColour(_TEXT)
    pad = wrap.FromDIP(6)
    sizer.Add(ctrl, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, pad)
    wrap.SetSizer(sizer)
    return wrap, ctrl


def _btn(parent, label, size=(-1, -1), primary=False):
    """Dark-themed GenButton (pure-Python; respects SetBackgroundColour on Windows)."""
    b = GenButton(parent, label=label, size=parent.FromDIP(wx.Size(*size)))
    b.SetBackgroundColour(_BTN_PRIMARY if primary else _BTN_NORMAL)
    b.SetForegroundColour(_TEXT)
    b.SetBezelWidth(1)
    b.SetFont(parent.GetFont())  # GenButton doesn't inherit parent font automatically
    return b


class _AdvancedDialog(wx.Dialog):
    """進階設定彈出視窗：字體大小、提示詞、顯示選項。"""

    def __init__(self, parent, en_size: int, zh_size: int,
                 context: str, show_raw: bool, show_corrected: bool,
                 enable_denoise: bool = True):
        super().__init__(parent, title="進階設定",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.result = None

        self.SetBackgroundColour(_BG)
        self.SetFont(wx.Font(wx.FontInfo(10).FaceName(_UI_FONT_FACE)))

        self._en_size        = en_size
        self._zh_size        = zh_size
        self._context        = context
        self._show_raw       = show_raw
        self._show_corrected = show_corrected
        self._enable_denoise = enable_denoise

        # 偵測 DTLN 模型檔是否存在
        import sys as _sys
        from pathlib import Path as _Path
        _base = (_Path(_sys.executable).parent if getattr(_sys, "frozen", False)
                 else _Path(__file__).parent.parent)
        self._dtln_available = (
            (_base / "dtln_model_1.onnx").exists() and
            (_base / "dtln_model_2.onnx").exists()
        )

        self._build()
        self._update_preview()
        self.SetSize(self.FromDIP(wx.Size(440, -1)))
        self.Layout()
        self.Fit()

    def _build(self):
        outer = wx.BoxSizer(wx.VERTICAL)
        pad = self.FromDIP(16)

        # Title
        title = _dark(wx.StaticText(self, label="⚙  進階設定"), fg=_ACCENT)
        f = title.GetFont()
        f.SetPointSize(12)
        f.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(f)
        outer.Add(title, 0, wx.ALL, pad)

        # Context hint
        outer.Add(_dark(wx.StaticText(self, label="辨識提示詞（選填）"), fg=_SUBTEXT),
                  0, wx.LEFT | wx.RIGHT | wx.TOP, pad)
        _ctx_wrap, self._ctx_entry = _make_entry(self, self._context)
        outer.Add(_ctx_wrap, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, pad)

        outer.AddSpacer(self.FromDIP(10))

        # Show options
        outer.Add(_dark(wx.StaticText(self, label="原文顯示"), fg=_SUBTEXT),
                  0, wx.LEFT | wx.RIGHT, pad)
        self._chk_corrected = _dark(
            wx.CheckBox(self, label="顯示校正後 ASR 原文"), fg=_TEXT)
        self._chk_corrected.SetValue(self._show_corrected)
        outer.Add(self._chk_corrected, 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)

        self._chk_raw = _dark(
            wx.CheckBox(self, label="顯示原始 ASR 辨識"), fg=_TEXT)
        self._chk_raw.SetValue(self._show_raw)
        outer.Add(self._chk_raw, 0, wx.LEFT | wx.RIGHT, pad)

        outer.AddSpacer(self.FromDIP(10))

        # Noise reduction
        outer.Add(_dark(wx.StaticText(self, label="降噪"), fg=_SUBTEXT),
                  0, wx.LEFT | wx.RIGHT, pad)
        _denoise_label = ("啟用 DTLN 降噪" if self._dtln_available
                          else "啟用 DTLN 降噪（模型檔未下載，無效）")
        self._chk_denoise = _dark(wx.CheckBox(self, label=_denoise_label), fg=_TEXT)
        self._chk_denoise.SetValue(self._enable_denoise)
        outer.Add(self._chk_denoise, 0, wx.LEFT | wx.RIGHT, pad)

        outer.AddSpacer(self.FromDIP(10))

        # Font size sliders (no wx.SL_LABELS — its text is black and invisible on dark bg)
        for label_text, attr_name, lo, hi in [
            ("辨識字體大小", "_sl_en", 10, 30),
            ("翻譯字體大小", "_sl_zh", 14, 40),
        ]:
            row = wx.BoxSizer(wx.HORIZONTAL)
            lbl = _dark(wx.StaticText(self, label=label_text,
                                      size=self.FromDIP(wx.Size(110, -1))),
                        fg=_SUBTEXT)
            row.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            init_val = self._en_size if attr_name == "_sl_en" else self._zh_size
            sl = wx.Slider(self, value=init_val, minValue=lo, maxValue=hi,
                           style=wx.SL_HORIZONTAL,
                           size=self.FromDIP(wx.Size(200, -1)))
            setattr(self, attr_name, sl)
            val_lbl = _dark(wx.StaticText(self, label=str(init_val),
                                          size=self.FromDIP(wx.Size(30, -1))),
                            fg=_TEXT)
            sl.Bind(wx.EVT_SLIDER,
                    lambda evt, _s=sl, _v=val_lbl: (
                        _v.SetLabel(str(_s.GetValue())),
                        self._update_preview(),
                        evt.Skip()))
            row.Add(sl, 1, wx.EXPAND | wx.LEFT, self.FromDIP(8))
            row.Add(val_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, self.FromDIP(6))
            outer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            outer.AddSpacer(self.FromDIP(6))

        outer.AddSpacer(self.FromDIP(8))

        # ── 字體預覽 ─────────────────────────────────────────────────────────
        outer.Add(_dark(wx.StaticText(self, label="預覽"), fg=_SUBTEXT),
                  0, wx.LEFT | wx.RIGHT, pad)
        outer.AddSpacer(self.FromDIP(4))
        _prev_panel = wx.Panel(self)
        _prev_panel.SetBackgroundColour(wx.Colour(0xc0, 0xc0, 0xc0))
        _prev_box = wx.BoxSizer(wx.VERTICAL)
        self._prev_en = wx.StaticText(_prev_panel, label="Real-time Subtitle — ASR 辨識原文")
        self._prev_en.SetForegroundColour(wx.Colour(0xe0, 0xe0, 0xe0))
        self._prev_zh = wx.StaticText(_prev_panel, label="即時字幕翻譯文字預覽")
        self._prev_zh.SetForegroundColour(wx.Colour(0xff, 0xff, 0xff))
        _prev_box.Add(self._prev_en, 0, wx.ALL, self.FromDIP(8))
        _prev_box.Add(self._prev_zh, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(8))
        _prev_panel.SetSizer(_prev_box)
        outer.Add(_prev_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)

        outer.AddSpacer(self.FromDIP(8))

        # Buttons
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = _btn(self, "取消", (100, 34))
        ok_btn = _btn(self, "確認", (100, 34), primary=True)
        btn_row.Add(cancel_btn, 0, wx.RIGHT, self.FromDIP(8))
        btn_row.Add(ok_btn)
        outer.Add(btn_row, 0, wx.ALIGN_RIGHT | wx.ALL, pad)

        self.SetSizer(outer)

        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)

    def _update_preview(self):
        f_en = wx.Font(wx.FontInfo(self._sl_en.GetValue()).FaceName(_UI_FONT_FACE))
        f_zh = wx.Font(wx.FontInfo(self._sl_zh.GetValue()).FaceName(_UI_FONT_FACE))
        self._prev_en.SetFont(f_en)
        self._prev_zh.SetFont(f_zh)
        self._prev_en.GetParent().Layout()
        self.Layout()
        self.Fit()

    def _on_key(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self._on_ok(None)
        elif event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        event.Skip()

    def _on_ok(self, _event):
        self.result = {
            "en_font_size":   self._sl_en.GetValue(),
            "zh_font_size":   self._sl_zh.GetValue(),
            "context":        self._ctx_entry.GetValue().strip(),
            "show_raw":       self._chk_raw.GetValue(),
            "show_corrected": self._chk_corrected.GetValue(),
            "enable_denoise": self._chk_denoise.GetValue(),
        }
        self.EndModal(wx.ID_OK)


class _SetupWxDlg(wx.Dialog):
    """Main setup dialog (wx)."""

    def __init__(self, parent, config: dict, initial_pos=None):
        super().__init__(parent, title="Real-time Subtitle",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.STAY_ON_TOP)
        self.result = None
        self._config = config

        self._en_size        = int(config.get("en_font_size", 15))
        self._zh_size        = int(config.get("zh_font_size", 24))
        self._context        = config.get("context", "")
        self._show_raw       = bool(config.get("show_raw", False))
        self._show_corrected = bool(config.get("show_corrected", True))
        self._enable_denoise = bool(config.get("enable_denoise", True))

        # ── 本地 ASR 路徑偵測 ──────────────────────────────────────────
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from local_downloader import (detect_existing_paths as _dep,
                                          DEFAULT_MODEL_PATH as _DMP,
                                          DEFAULT_CHATLLM_DIR as _DCD)
            _det_model, _det_chatllm = _dep()
            self._default_model_path   = _DMP
            self._default_chatllm_dir  = _DCD
        except Exception:
            _det_model = _det_chatllm = ""
            self._default_model_path  = None
            self._default_chatllm_dir = None

        self._model_path   = [config.get("local_model_path",  "") or _det_model]
        self._chatllm_path = [config.get("local_chatllm_dir", "") or _det_chatllm]

        # ── GPU 偵測 ───────────────────────────────────────────────────
        self._gpu_devices: list[dict] = []
        _chatllm_for_detect = self._chatllm_path[0]
        if _chatllm_for_detect:
            try:
                from local_asr_engine import detect_vulkan_devices
                self._gpu_devices = detect_vulkan_devices(_chatllm_for_detect)
            except Exception:
                pass

        self.SetBackgroundColour(_BG)
        self.SetFont(wx.Font(wx.FontInfo(10).FaceName(_UI_FONT_FACE)))
        self._build()

        self.Bind(wx.EVT_DPI_CHANGED, self._on_dpi_changed)
        self.Bind(wx.EVT_CHAR_HOOK,   self._on_key)

        self.SetSize(self.FromDIP(wx.Size(460, -1)))
        self.Layout()
        self.Fit()

        if initial_pos:
            self.SetPosition(wx.Point(*initial_pos))
        else:
            self.CentreOnScreen()
        wx.CallAfter(self.Refresh)

    def _on_dpi_changed(self, event):
        self.Layout()
        event.Skip()

    def _on_key(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self._on_ok(None)
        elif event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        event.Skip()

    def _build(self):
        outer = wx.BoxSizer(wx.VERTICAL)
        pad = self.FromDIP(16)

        # ── Header ───────────────────────────────────────────────────────────
        header = wx.Panel(self)
        header.SetBackgroundColour(_HEADER)
        h_sizer = wx.BoxSizer(wx.HORIZONTAL)
        title_lbl = _dark(wx.StaticText(header, label="⚡  Real-time Subtitle"),
                          fg=_ACCENT)
        f = title_lbl.GetFont()
        f.SetPointSize(14)
        f.SetWeight(wx.FONTWEIGHT_BOLD)
        title_lbl.SetFont(f)
        h_sizer.Add(title_lbl, 0, wx.ALL, self.FromDIP(14))
        header.SetSizer(h_sizer)
        outer.Add(header, 0, wx.EXPAND)

        # ── Body ─────────────────────────────────────────────────────────────
        body = wx.Panel(self)
        body.SetBackgroundColour(_BG)
        b = wx.BoxSizer(wx.VERTICAL)

        def _lbl(text):
            return _dark(wx.StaticText(body, label=text), fg=_SUBTEXT)

        # OpenAI API Key（翻譯用，選填）
        b.Add(_lbl("OpenAI API Key（翻譯用，選填）"), 0, wx.BOTTOM, self.FromDIP(4))
        _existing_key = (
            self._config.get("openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        _key_wrap, self._key_entry = _make_entry(body, _existing_key, wx.TE_PASSWORD)
        b.Add(_key_wrap, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(14))

        # ── 運算模式 ──────────────────────────────────────────────────────────
        b.Add(_lbl("運算模式"), 0, wx.BOTTOM, self.FromDIP(4))
        _saved_mode = self._config.get("backend", "local")
        mode_row = wx.BoxSizer(wx.HORIZONTAL)
        self._rb_local_mode = _dark(
            wx.RadioButton(body, label="🖥 本地模型", style=wx.RB_GROUP), fg=_TEXT)
        self._rb_server_mode = _dark(
            wx.RadioButton(body, label="🌐 外部伺服器（QwenASR）"), fg=_TEXT)
        self._rb_local_mode.SetValue(_saved_mode != "remote")
        self._rb_server_mode.SetValue(_saved_mode == "remote")
        mode_row.Add(self._rb_local_mode, 0, wx.RIGHT, self.FromDIP(16))
        mode_row.Add(self._rb_server_mode, 0)
        b.Add(mode_row, 0, wx.BOTTOM, self.FromDIP(10))

        # ── 本地模型 panel ────────────────────────────────────────────────────
        self._local_panel = wx.Panel(body)
        self._local_panel.SetBackgroundColour(_BG)
        lp = wx.BoxSizer(wx.VERTICAL)

        def _lbl_l(text):
            return _dark(wx.StaticText(self._local_panel, label=text), fg=_SUBTEXT)

        # 偵測到的裝置
        lp.Add(_lbl_l("偵測到的裝置"), 0, wx.BOTTOM, self.FromDIP(4))
        lp.Add(_dark(wx.StaticText(self._local_panel, label="✅ CPU（可用）"), fg=_TEXT),
               0, wx.BOTTOM, self.FromDIP(2))
        if self._gpu_devices:
            for _gd in self._gpu_devices:
                _vram_gb  = _gd.get("vram_free", 0) / 1_073_741_824
                _vram_str = f"（可用 VRAM {_vram_gb:.1f} GB）" if _vram_gb > 0.1 else ""
                lp.Add(_dark(wx.StaticText(
                    self._local_panel,
                    label=f"✅ GPU：{_gd['name']}{_vram_str}（Vulkan）"),
                    fg=_TEXT), 0, wx.BOTTOM, self.FromDIP(2))
        else:
            lp.Add(_dark(wx.StaticText(
                self._local_panel,
                label="ℹ 未偵測到獨立 GPU，僅 CPU 推理可用"),
                fg=_SUBTEXT), 0, wx.BOTTOM, self.FromDIP(2))

        # 推理方式
        lp.Add(_lbl_l("推理方式"), 0, wx.TOP | wx.BOTTOM, self.FromDIP(4))
        dev_radio_row = wx.BoxSizer(wx.HORIZONTAL)
        self._rb_cpu = _dark(
            wx.RadioButton(self._local_panel, label="🖥 CPU", style=wx.RB_GROUP),
            fg=_TEXT)
        dev_radio_row.Add(self._rb_cpu, 0, wx.RIGHT, self.FromDIP(16))
        self._gpu_rbs: list[tuple[wx.RadioButton, int]] = []
        _saved_dev_id = int(self._config.get("local_device_id", 0))
        _gpu_activated = False
        for _gd in self._gpu_devices:
            rb = _dark(wx.RadioButton(
                self._local_panel, label=f"⚡ GPU ({_gd['name']}) [Vulkan]"), fg=_TEXT)
            rb._gpu_id = _gd["id"]
            dev_radio_row.Add(rb, 0, wx.RIGHT, self.FromDIP(8))
            self._gpu_rbs.append((rb, _gd["id"]))
            if _gd["id"] == _saved_dev_id:
                rb.SetValue(True)
                _gpu_activated = True
        if not _gpu_activated:
            self._rb_cpu.SetValue(True)
        lp.Add(dev_radio_row, 0, wx.BOTTOM, self.FromDIP(10))

        # 模型
        lp.Add(_lbl_l("模型（qwen3-asr-1.7b.bin）"), 0, wx.BOTTOM, self.FromDIP(4))
        model_row = wx.BoxSizer(wx.HORIZONTAL)
        self._model_status_lbl = _dark(
            wx.StaticText(self._local_panel, label=""), fg=_TEXT)
        self._model_status_lbl.SetMinSize(self.FromDIP(wx.Size(200, -1)))
        self._dl_btn = _btn(self._local_panel, "⬇ 下載（~2.3 GB）", (-1, 28))
        self._dl_btn.Bind(wx.EVT_BUTTON, self._on_download)
        model_row.Add(self._model_status_lbl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
                      self.FromDIP(8))
        model_row.Add(self._dl_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        lp.Add(model_row, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(4))
        self._dl_prog_lbl = _dark(wx.StaticText(self._local_panel, label=""), fg=_SUBTEXT)
        lp.Add(self._dl_prog_lbl, 0, wx.BOTTOM, self.FromDIP(10))

        # chatllm 執行環境
        lp.Add(_lbl_l("chatllm 執行環境"), 0, wx.BOTTOM, self.FromDIP(4))
        if self._chatllm_path[0]:
            _chatllm_text = f"✅ 已找到：{self._chatllm_path[0]}"
            _chatllm_fg   = _TEXT
        else:
            _default_dir = str(self._default_chatllm_dir) if self._default_chatllm_dir \
                           else "~/.local/share/realtime-subtitle/chatllm/"
            _chatllm_text = (f"⚠ 未找到 chatllm 執行環境\n"
                             f"請從 chatllm.cpp 編譯後放至：{_default_dir}")
            _chatllm_fg   = wx.Colour(251, 146, 60)   # orange
        _chatllm_lbl = _dark(
            wx.StaticText(self._local_panel, label=_chatllm_text), fg=_chatllm_fg)
        _chatllm_lbl.Wrap(self.FromDIP(420))
        lp.Add(_chatllm_lbl, 0, wx.BOTTOM, self.FromDIP(14))

        self._local_panel.SetSizer(lp)
        b.Add(self._local_panel, 0, wx.EXPAND)

        # ── 外部伺服器 panel ──────────────────────────────────────────────────
        self._server_panel = wx.Panel(body)
        self._server_panel.SetBackgroundColour(_BG)
        sp = wx.BoxSizer(wx.VERTICAL)

        def _lbl_s(text):
            return _dark(wx.StaticText(self._server_panel, label=text), fg=_SUBTEXT)

        sp.Add(_lbl_s("QwenASR 伺服器 URL"), 0, wx.BOTTOM, self.FromDIP(4))
        _server_default = (
            self._config.get("asr_server", "http://localhost:8765")
            if _saved_mode == "remote"
            else "http://localhost:8765"
        )
        _server_wrap, self._server_url_entry = _make_entry(
            self._server_panel, _server_default)
        sp.Add(_server_wrap, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(6))

        sp.AddSpacer(self.FromDIP(6))

        self._server_panel.SetSizer(sp)
        b.Add(self._server_panel, 0, wx.EXPAND)

        # ── 音訊來源 ──────────────────────────────────────────────────────────
        b.Add(_lbl("音訊來源"), 0, wx.BOTTOM, self.FromDIP(4))
        src_row = wx.BoxSizer(wx.HORIZONTAL)
        self._rb_monitor = _dark(
            wx.RadioButton(body, label="🔊 系統音訊", style=wx.RB_GROUP), fg=_TEXT)
        self._rb_mic = _dark(
            wx.RadioButton(body, label="🎤 麥克風"), fg=_TEXT)
        _saved_src = self._config.get("source", "monitor")
        self._rb_monitor.SetValue(_saved_src == "monitor")
        self._rb_mic.SetValue(_saved_src == "mic")
        src_row.Add(self._rb_monitor, 0, wx.RIGHT, self.FromDIP(20))
        src_row.Add(self._rb_mic)
        b.Add(src_row, 0, wx.BOTTOM, self.FromDIP(8))

        # Device selector panel
        self._dev_panel = wx.Panel(body)
        self._dev_panel.SetBackgroundColour(_BG)
        dev_sizer = wx.BoxSizer(wx.VERTICAL)

        monitor_devices = _list_audio_devices_for_dialog()
        _saved_mon = self._config.get("monitor_device", "")
        if monitor_devices:
            self._mon_choice = _dark(
                _DarkCombo(self._dev_panel, choices=monitor_devices,
                            style=wx.CB_READONLY), bg=_ENTRY, fg=_TEXT)
            _idx = monitor_devices.index(_saved_mon) if _saved_mon in monitor_devices else 0
            self._mon_choice.SetSelection(_idx)
            self._mon_entry = None
            _mon_widget = self._mon_choice
        else:
            self._mon_choice = None
            _mon_widget, self._mon_entry = _make_entry(self._dev_panel, _saved_mon)
        dev_sizer.Add(_mon_widget, 0, wx.EXPAND)

        mic_devices = _list_mic_devices_for_dialog()
        _saved_mic = self._config.get("mic_device", "")
        if mic_devices:
            self._mic_choice = _dark(
                _DarkCombo(self._dev_panel, choices=mic_devices,
                            style=wx.CB_READONLY), bg=_ENTRY, fg=_TEXT)
            _idx = mic_devices.index(_saved_mic) if _saved_mic in mic_devices else 0
            self._mic_choice.SetSelection(_idx)
            self._mic_entry = None
            _mic_widget = self._mic_choice
        else:
            self._mic_choice = None
            _mic_widget, self._mic_entry = _make_entry(self._dev_panel, _saved_mic)
        dev_sizer.Add(_mic_widget, 0, wx.EXPAND)

        self._dev_panel.SetSizer(dev_sizer)
        b.Add(self._dev_panel, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(14))

        self._rb_monitor.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)
        self._rb_mic.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)

        # ── 翻譯方向 ──────────────────────────────────────────────────────────
        b.Add(_lbl("翻譯方向"), 0, wx.BOTTOM, self.FromDIP(4))
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        dir_row = wx.BoxSizer(wx.HORIZONTAL)
        self._src_choice = _dark(
            _DarkCombo(body, choices=LANG_LABELS, style=wx.CB_READONLY),
            bg=_ENTRY, fg=_TEXT)
        _src_lbl = lang_code_to_label(_src0)
        self._src_choice.SetSelection(
            LANG_LABELS.index(_src_lbl) if _src_lbl in LANG_LABELS else 0)

        swap_btn = _btn(body, "⇄", (40, 34))
        swap_btn.Bind(wx.EVT_BUTTON, self._on_swap)

        self._tgt_choice = _dark(
            _DarkCombo(body, choices=LANG_LABELS, style=wx.CB_READONLY),
            bg=_ENTRY, fg=_TEXT)
        _tgt_lbl = lang_code_to_label(_tgt0)
        self._tgt_choice.SetSelection(
            LANG_LABELS.index(_tgt_lbl) if _tgt_lbl in LANG_LABELS else 1)

        dir_row.Add(self._src_choice, 1, wx.EXPAND | wx.RIGHT, self.FromDIP(6))
        dir_row.Add(swap_btn, 0, wx.RIGHT, self.FromDIP(6))
        dir_row.Add(self._tgt_choice, 1, wx.EXPAND)
        b.Add(dir_row, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(4))

        # Warning label
        self._warn_lbl = _dark(wx.StaticText(body, label=""), fg=_WARN)
        b.Add(self._warn_lbl, 0, wx.BOTTOM, self.FromDIP(4))

        body.SetSizer(b)
        outer.Add(body, 1, wx.EXPAND | wx.ALL, pad)

        # Advanced settings button
        adv_btn = _btn(self, "⚙  進階設定", (-1, 30))
        adv_btn.Bind(wx.EVT_BUTTON, self._open_advanced)
        outer.Add(adv_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                  self.FromDIP(16))

        # Button row
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = _btn(self, "取消", (-1, 38))
        ok_btn = _btn(self, "啟動字幕辨識", (-1, 38), primary=True)
        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        btn_row.Add(cancel_btn, 1, wx.EXPAND | wx.RIGHT, self.FromDIP(6))
        btn_row.Add(ok_btn, 1, wx.EXPAND)
        outer.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                  self.FromDIP(16))

        self.SetSizer(outer)

        # Bind mode toggle
        self._rb_local_mode.Bind(wx.EVT_RADIOBUTTON, self._on_mode_change)
        self._rb_server_mode.Bind(wx.EVT_RADIOBUTTON, self._on_mode_change)

        # Initial state
        self._on_source_change(None)
        self._on_mode_change(None)
        self._refresh_model_status()

    def _on_mode_change(self, _event):
        is_local = self._rb_local_mode.GetValue()
        self._local_panel.Show(is_local)
        self._server_panel.Show(not is_local)
        self.Layout()
        self.Fit()

    def _on_source_change(self, _event):
        is_monitor = self._rb_monitor.GetValue()
        mon = self._mon_choice or self._mon_entry
        mic = self._mic_choice or self._mic_entry
        if is_monitor:
            mon.Show()
            mic.Hide()
        else:
            mon.Hide()
            mic.Show()
        self._dev_panel.Layout()
        self.Layout()

    def _refresh_model_status(self):
        if self._model_path[0]:
            self._model_status_lbl.SetLabel(
                f"✅ 已就緒：{os.path.basename(self._model_path[0])}")
            self._model_status_lbl.SetForegroundColour(wx.Colour(74, 222, 128))  # green
            self._dl_btn.Hide()
        else:
            self._model_status_lbl.SetLabel("⚠ 未找到模型檔案")
            self._model_status_lbl.SetForegroundColour(_WARN)
            self._dl_btn.Show()
        self._model_status_lbl.GetParent().Layout()

    def _on_download(self, _event):
        import threading
        self._dl_btn.Disable()
        try:
            from local_downloader import download_gguf as _dg, DEFAULT_MODEL_PATH as _dmp
        except Exception as ex:
            wx.CallAfter(self._dl_prog_lbl.SetLabel, f"❌ 錯誤：{ex}")
            wx.CallAfter(self._dl_btn.Enable)
            return

        def _cb(pct, msg):
            wx.CallAfter(self._dl_prog_lbl.SetLabel, msg)

        def _run():
            try:
                _dg(progress_cb=_cb)
                def _done():
                    self._model_path[0] = str(_dmp)
                    self._refresh_model_status()
                    self._dl_prog_lbl.SetLabel("✅ 下載完成")
                    self._dl_btn.Enable()
                wx.CallAfter(_done)
            except Exception as ex:
                wx.CallAfter(self._dl_prog_lbl.SetLabel, f"❌ 下載失敗：{ex}")
                wx.CallAfter(self._dl_btn.Enable)

        threading.Thread(target=_run, daemon=True).start()

    def _on_swap(self, _event):
        si = self._src_choice.GetSelection()
        ti = self._tgt_choice.GetSelection()
        self._src_choice.SetSelection(ti)
        self._tgt_choice.SetSelection(si)

    def _open_advanced(self, _event):
        adv = _AdvancedDialog(
            self,
            en_size=self._en_size, zh_size=self._zh_size,
            context=self._context,
            show_raw=self._show_raw,
            show_corrected=self._show_corrected,
            enable_denoise=self._enable_denoise,
        )
        if adv.ShowModal() == wx.ID_OK and adv.result:
            r = adv.result
            self._en_size        = r["en_font_size"]
            self._zh_size        = r["zh_font_size"]
            self._context        = r["context"]
            self._show_raw       = r["show_raw"]
            self._show_corrected = r["show_corrected"]
            self._enable_denoise = r["enable_denoise"]
        adv.Destroy()

    def _on_ok(self, _event):
        is_monitor = self._rb_monitor.GetValue()
        mon_val = (
            self._mon_choice.GetStringSelection()
            if self._mon_choice else
            self._mon_entry.GetValue().strip()
        )
        mic_val = (
            self._mic_choice.GetStringSelection()
            if self._mic_choice else
            self._mic_entry.GetValue().strip()
        )
        src_code = lang_label_to_code(self._src_choice.GetStringSelection())
        tgt_code = lang_label_to_code(self._tgt_choice.GetStringSelection())
        x, y = self.GetPosition()

        if self._rb_server_mode.GetValue():
            # ── 外部伺服器模式 ────────────────────────────────────────────
            server_url = self._server_url_entry.GetValue().strip()
            if not server_url:
                self._warn_lbl.SetLabel("⚠ 請填入 QwenASR 伺服器 URL")
                return
            self.result = {
                "backend":           "remote",
                "asr_server":        server_url,
                "local_model_path":  "",
                "local_chatllm_dir": "",
                "local_device_id":   0,
                "source":            "monitor" if is_monitor else "mic",
                "monitor_device":    mon_val,
                "mic_device":        mic_val,
                "direction":         f"{src_code}→{tgt_code}",
                "openai_api_key":    self._key_entry.GetValue().strip(),
                "context":           self._context,
                "en_font_size":      self._en_size,
                "zh_font_size":      self._zh_size,
                "show_raw":          self._show_raw,
                "show_corrected":    self._show_corrected,
                "enable_denoise":    self._enable_denoise,
                "_dialog_x":         x,
                "_dialog_y":         y,
            }
        else:
            # ── 本地模型模式 ──────────────────────────────────────────────
            if not self._model_path[0]:
                self._warn_lbl.SetLabel("⚠ 請先下載模型檔案")
                return
            if not self._chatllm_path[0]:
                self._warn_lbl.SetLabel("⚠ 未找到 chatllm 執行環境，請手動安裝")
                return
            _local_dev_id = 0
            for rb, gid in self._gpu_rbs:
                if rb.GetValue():
                    _local_dev_id = gid
                    break
            self.result = {
                "backend":           "local",
                "local_model_path":  self._model_path[0],
                "local_chatllm_dir": self._chatllm_path[0],
                "local_device_id":   _local_dev_id,
                "asr_server":        "http://localhost:8765",
                "source":            "monitor" if is_monitor else "mic",
                "monitor_device":    mon_val,
                "mic_device":        mic_val,
                "direction":         f"{src_code}→{tgt_code}",
                "openai_api_key":    self._key_entry.GetValue().strip(),
                "context":           self._context,
                "en_font_size":      self._en_size,
                "zh_font_size":      self._zh_size,
                "show_raw":          self._show_raw,
                "show_corrected":    self._show_corrected,
                "enable_denoise":    self._enable_denoise,
                "_dialog_x":         x,
                "_dialog_y":         y,
            }
        self.EndModal(wx.ID_OK)


class SetupDialogWx:
    """wxPython 啟動設定對話框。公開介面與 SetupDialogTk 相同。"""

    def __init__(self, config: dict):
        self._config = config

    def run(self) -> dict | None:
        """Standalone mode (startup): create wx.App then ShowModal."""
        _get_wx_app()
        dlg = _SetupWxDlg(None, self._config, initial_pos=None)
        dlg.ShowModal()
        result = dlg.result
        dlg.Destroy()
        return result

    def run_as_toplevel(self, parent) -> dict | None:
        """Called from inside tkinter mainloop. ShowModal runs nested Win32 loop.
        Always centres on the monitor where the overlay lives; never passes the
        overlay's corner position (which is at the bottom of the screen for a
        subtitle window, causing the dialog to appear out of reach)."""
        _get_wx_app()
        dlg = _SetupWxDlg(None, self._config, initial_pos=None)
        dlg.ShowModal()
        result = dlg.result
        dlg.Destroy()
        return result
