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

        self.SetSize(self.FromDIP(wx.Size(500, 560)))
        self.Layout()

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

        # ── Header ───────────────────────────────────────────────────
        header = wx.Panel(self)
        header.SetBackgroundColour(_HEADER)
        h_sizer = wx.BoxSizer(wx.HORIZONTAL)
        title_lbl = _dark(wx.StaticText(header, label="⚡  Real-time Subtitle"), fg=_ACCENT)
        f = title_lbl.GetFont(); f.SetPointSize(14); f.SetWeight(wx.FONTWEIGHT_BOLD)
        title_lbl.SetFont(f)
        h_sizer.Add(title_lbl, 0, wx.ALL, self.FromDIP(14))
        header.SetSizer(h_sizer)
        outer.Add(header, 0, wx.EXPAND)

        # ── Notebook ─────────────────────────────────────────────────
        nb = wx.Notebook(self)
        nb.SetBackgroundColour(_BG)
        nb.SetForegroundColour(_TEXT)

        tab1 = wx.Panel(nb); tab1.SetBackgroundColour(_BG)
        tab2 = wx.Panel(nb); tab2.SetBackgroundColour(_BG)
        tab3 = wx.Panel(nb); tab3.SetBackgroundColour(_BG)

        nb.AddPage(tab1, "辨識")
        nb.AddPage(tab2, "翻譯")
        nb.AddPage(tab3, "顯示")

        self._build_tab_asr(tab1)
        self._build_tab_translation(tab2)
        self._build_tab_display(tab3)

        outer.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, pad)

        # ── Warning + global buttons ──────────────────────────────────
        self._warn_lbl = _dark(wx.StaticText(self, label=""), fg=_WARN)
        outer.Add(self._warn_lbl, 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = _btn(self, "取消", (-1, 38))
        ok_btn = _btn(self, "啟動字幕辨識", (-1, 38), primary=True)
        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        btn_row.Add(cancel_btn, 1, wx.EXPAND | wx.RIGHT, self.FromDIP(6))
        btn_row.Add(ok_btn, 1, wx.EXPAND)
        outer.Add(btn_row, 0, wx.EXPAND | wx.ALL, pad)

        self.SetSizer(outer)

    def _build_tab_asr(self, panel):
        b = wx.BoxSizer(wx.VERTICAL)
        pad = self.FromDIP(16)

        def _lbl(text):
            return _dark(wx.StaticText(panel, label=text), fg=_SUBTEXT)

        # ── 運算模式 ────────────────────────────────────────────────────
        b.Add(_lbl("運算模式"), 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)
        b.AddSpacer(self.FromDIP(4))
        _saved_mode = self._config.get("backend", "local")
        mode_row = wx.BoxSizer(wx.HORIZONTAL)
        self._rb_local_mode = _dark(
            wx.RadioButton(panel, label="🖥 本地模型", style=wx.RB_GROUP), fg=_TEXT)
        self._rb_server_mode = _dark(
            wx.RadioButton(panel, label="🌐 外部伺服器（QwenASR）"), fg=_TEXT)
        self._rb_local_mode.SetValue(_saved_mode != "remote")
        self._rb_server_mode.SetValue(_saved_mode == "remote")
        mode_row.Add(self._rb_local_mode, 0, wx.RIGHT, self.FromDIP(16))
        mode_row.Add(self._rb_server_mode)
        b.Add(mode_row, 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(8))

        # ── 本地模型 panel ───────────────────────────────────────────────
        self._local_panel = wx.Panel(panel)
        self._local_panel.SetBackgroundColour(_BG)
        lp = wx.BoxSizer(wx.VERTICAL)

        def _lbl_l(text):
            return _dark(wx.StaticText(self._local_panel, label=text), fg=_SUBTEXT)

        lp.Add(_lbl_l("偵測到的裝置"), 0, wx.BOTTOM, self.FromDIP(4))
        lp.Add(_dark(wx.StaticText(self._local_panel, label="✅ CPU（可用）"), fg=_TEXT),
               0, wx.BOTTOM, self.FromDIP(2))
        if self._gpu_devices:
            for _gd in self._gpu_devices:
                _vram_gb = _gd.get("vram_free", 0) / 1_073_741_824
                _vram_str = f"（可用 VRAM {_vram_gb:.1f} GB）" if _vram_gb > 0.1 else ""
                lp.Add(_dark(wx.StaticText(
                    self._local_panel,
                    label=f"✅ GPU：{_gd['name']}{_vram_str}（Vulkan）"), fg=_TEXT),
                    0, wx.BOTTOM, self.FromDIP(2))
        else:
            lp.Add(_dark(wx.StaticText(
                self._local_panel, label="ℹ 未偵測到獨立 GPU，僅 CPU 推理可用"),
                fg=_SUBTEXT), 0, wx.BOTTOM, self.FromDIP(2))

        lp.Add(_lbl_l("推理方式"), 0, wx.TOP | wx.BOTTOM, self.FromDIP(4))
        dev_radio_row = wx.BoxSizer(wx.HORIZONTAL)
        self._rb_cpu = _dark(
            wx.RadioButton(self._local_panel, label="🖥 CPU", style=wx.RB_GROUP), fg=_TEXT)
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
        lp.Add(dev_radio_row, 0, wx.BOTTOM, self.FromDIP(8))

        lp.Add(_lbl_l("模型（qwen3-asr-1.7b.bin）"), 0, wx.BOTTOM, self.FromDIP(4))
        model_row = wx.BoxSizer(wx.HORIZONTAL)
        self._model_status_lbl = _dark(
            wx.StaticText(self._local_panel, label=""), fg=_TEXT)
        self._model_status_lbl.SetMinSize(self.FromDIP(wx.Size(200, -1)))
        self._dl_btn = _btn(self._local_panel, "⬇ 下載（~2.3 GB）", (-1, 28))
        self._dl_btn.Bind(wx.EVT_BUTTON, self._on_download)
        model_row.Add(self._model_status_lbl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(8))
        model_row.Add(self._dl_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        lp.Add(model_row, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(4))
        self._dl_prog_lbl = _dark(wx.StaticText(self._local_panel, label=""), fg=_SUBTEXT)
        lp.Add(self._dl_prog_lbl, 0, wx.BOTTOM, self.FromDIP(8))

        lp.Add(_lbl_l("chatllm 執行環境"), 0, wx.BOTTOM, self.FromDIP(4))
        if self._chatllm_path[0]:
            _ct = f"✅ 已找到：{self._chatllm_path[0]}"
            _cf = _TEXT
        else:
            _default_dir = str(self._default_chatllm_dir) if self._default_chatllm_dir \
                           else "~/.local/share/realtime-subtitle/chatllm/"
            _ct = f"⚠ 未找到 chatllm 執行環境\n請放至：{_default_dir}"
            _cf = wx.Colour(251, 146, 60)
        _chatllm_lbl = _dark(wx.StaticText(self._local_panel, label=_ct), fg=_cf)
        _chatllm_lbl.Wrap(self.FromDIP(400))
        lp.Add(_chatllm_lbl, 0, wx.BOTTOM, self.FromDIP(4))

        self._local_panel.SetSizer(lp)
        b.Add(self._local_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)

        # ── 外部伺服器 panel ─────────────────────────────────────────────
        self._server_panel = wx.Panel(panel)
        self._server_panel.SetBackgroundColour(_BG)
        sp = wx.BoxSizer(wx.VERTICAL)
        sp.Add(_dark(wx.StaticText(self._server_panel, label="QwenASR 伺服器 URL"), fg=_SUBTEXT),
               0, wx.BOTTOM, self.FromDIP(4))
        _server_default = (self._config.get("asr_server", "http://localhost:8765")
                           if self._config.get("backend") == "remote" else "http://localhost:8765")
        _server_wrap, self._server_url_entry = _make_entry(self._server_panel, _server_default)
        sp.Add(_server_wrap, 0, wx.EXPAND)
        self._server_panel.SetSizer(sp)
        b.Add(self._server_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)

        b.AddSpacer(self.FromDIP(8))
        b.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(8))

        # ── 音訊來源 ─────────────────────────────────────────────────────
        b.Add(_lbl("音訊來源"), 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(4))
        src_row = wx.BoxSizer(wx.HORIZONTAL)
        self._rb_monitor = _dark(
            wx.RadioButton(panel, label="🔊 系統音訊", style=wx.RB_GROUP), fg=_TEXT)
        self._rb_mic = _dark(wx.RadioButton(panel, label="🎤 麥克風"), fg=_TEXT)
        _saved_src = self._config.get("source", "monitor")
        self._rb_monitor.SetValue(_saved_src == "monitor")
        self._rb_mic.SetValue(_saved_src == "mic")
        src_row.Add(self._rb_monitor, 0, wx.RIGHT, self.FromDIP(20))
        src_row.Add(self._rb_mic)
        b.Add(src_row, 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(6))

        # Device selector
        self._dev_panel = wx.Panel(panel)
        self._dev_panel.SetBackgroundColour(_BG)
        dev_sizer = wx.BoxSizer(wx.VERTICAL)

        monitor_devices = _list_audio_devices_for_dialog()
        _saved_mon = self._config.get("monitor_device", "")
        if monitor_devices:
            self._mon_choice = _dark(
                _DarkCombo(self._dev_panel, choices=monitor_devices, style=wx.CB_READONLY),
                bg=_ENTRY, fg=_TEXT)
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
                _DarkCombo(self._dev_panel, choices=mic_devices, style=wx.CB_READONLY),
                bg=_ENTRY, fg=_TEXT)
            _idx = mic_devices.index(_saved_mic) if _saved_mic in mic_devices else 0
            self._mic_choice.SetSelection(_idx)
            self._mic_entry = None
            _mic_widget = self._mic_choice
        else:
            self._mic_choice = None
            _mic_widget, self._mic_entry = _make_entry(self._dev_panel, _saved_mic)
        dev_sizer.Add(_mic_widget, 0, wx.EXPAND)

        self._dev_panel.SetSizer(dev_sizer)
        b.Add(self._dev_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(10))

        # ── 降噪 ────────────────────────────────────────────────────────
        b.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(8))
        b.Add(_lbl("降噪"), 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(4))
        from pathlib import Path as _Path
        _base = (_Path(sys.executable).parent if getattr(sys, "frozen", False)
                 else _Path(__file__).parent.parent)
        _dtln_ok = (_base / "dtln_model_1.onnx").exists() and (_base / "dtln_model_2.onnx").exists()
        _denoise_label = ("啟用 DTLN 降噪" if _dtln_ok
                          else "啟用 DTLN 降噪（模型檔未下載，無效）")
        self._chk_denoise = _dark(wx.CheckBox(panel, label=_denoise_label), fg=_TEXT)
        self._chk_denoise.SetValue(self._enable_denoise)
        b.Add(self._chk_denoise, 0, wx.LEFT | wx.RIGHT, pad)

        panel.SetSizer(b)

        # ── Bind events ──────────────────────────────────────────────────
        self._rb_local_mode.Bind(wx.EVT_RADIOBUTTON, self._on_mode_change)
        self._rb_server_mode.Bind(wx.EVT_RADIOBUTTON, self._on_mode_change)
        self._rb_monitor.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)
        self._rb_mic.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)

        self._on_source_change(None)
        self._on_mode_change(None)
        self._refresh_model_status()

    def _build_tab_translation(self, panel):
        b = wx.BoxSizer(wx.VERTICAL)
        pad = self.FromDIP(16)

        def _lbl(text):
            return _dark(wx.StaticText(panel, label=text), fg=_SUBTEXT)

        # ── OpenAI API Key ───────────────────────────────────────────────
        b.Add(_lbl("OpenAI API Key（翻譯用，選填）"), 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)
        b.AddSpacer(self.FromDIP(4))
        _existing_key = (self._config.get("openai_api_key", "")
                         or os.environ.get("OPENAI_API_KEY", ""))
        _key_wrap, self._key_entry = _make_entry(panel, _existing_key, wx.TE_PASSWORD)
        b.Add(_key_wrap, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)

        b.AddSpacer(self.FromDIP(14))
        b.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(14))

        # ── 翻譯方向 ─────────────────────────────────────────────────────
        b.Add(_lbl("翻譯方向"), 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(4))
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        dir_row = wx.BoxSizer(wx.HORIZONTAL)
        self._src_choice = _dark(
            _DarkCombo(panel, choices=LANG_LABELS, style=wx.CB_READONLY), bg=_ENTRY, fg=_TEXT)
        _src_lbl = lang_code_to_label(_src0)
        self._src_choice.SetSelection(
            LANG_LABELS.index(_src_lbl) if _src_lbl in LANG_LABELS else 0)
        swap_btn = _btn(panel, "⇄", (40, 34))
        swap_btn.Bind(wx.EVT_BUTTON, self._on_swap)
        self._tgt_choice = _dark(
            _DarkCombo(panel, choices=LANG_LABELS, style=wx.CB_READONLY), bg=_ENTRY, fg=_TEXT)
        _tgt_lbl = lang_code_to_label(_tgt0)
        self._tgt_choice.SetSelection(
            LANG_LABELS.index(_tgt_lbl) if _tgt_lbl in LANG_LABELS else 1)
        dir_row.Add(self._src_choice, 1, wx.EXPAND | wx.RIGHT, self.FromDIP(6))
        dir_row.Add(swap_btn, 0, wx.RIGHT, self.FromDIP(6))
        dir_row.Add(self._tgt_choice, 1, wx.EXPAND)
        b.Add(dir_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)

        b.AddSpacer(self.FromDIP(14))
        b.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(14))

        # ── 辨識提示詞 ───────────────────────────────────────────────────
        b.Add(_lbl("辨識提示詞（選填）"), 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(4))
        _ctx_wrap, self._ctx_entry = _make_entry(panel, self._context)
        b.Add(_ctx_wrap, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)

        panel.SetSizer(b)
    def _build_tab_display(self, panel):
        b = wx.BoxSizer(wx.VERTICAL)
        pad = self.FromDIP(16)

        def _lbl(text):
            return _dark(wx.StaticText(panel, label=text), fg=_SUBTEXT)

        # ── Font sliders ─────────────────────────────────────────────────
        b.Add(_lbl("字體大小"), 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)
        b.AddSpacer(self.FromDIP(6))

        for label_text, attr_name, lo, hi in [
            ("辨識字體", "_sl_en", 10, 30),
            ("翻譯字體", "_sl_zh", 14, 40),
        ]:
            row = wx.BoxSizer(wx.HORIZONTAL)
            lbl = _dark(wx.StaticText(panel, label=label_text,
                                      size=self.FromDIP(wx.Size(70, -1))), fg=_SUBTEXT)
            row.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
            init_val = self._en_size if attr_name == "_sl_en" else self._zh_size
            sl = wx.Slider(panel, value=init_val, minValue=lo, maxValue=hi,
                           style=wx.SL_HORIZONTAL, size=self.FromDIP(wx.Size(220, -1)))
            setattr(self, attr_name, sl)
            val_lbl = _dark(wx.StaticText(panel, label=str(init_val),
                                          size=self.FromDIP(wx.Size(30, -1))), fg=_TEXT)
            sl.Bind(wx.EVT_SLIDER,
                    lambda evt, _s=sl, _v=val_lbl: (
                        _v.SetLabel(str(_s.GetValue())),
                        self._update_preview(),
                        evt.Skip()))
            row.Add(sl, 1, wx.EXPAND | wx.LEFT, self.FromDIP(8))
            row.Add(val_lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, self.FromDIP(6))
            b.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            b.AddSpacer(self.FromDIP(4))

        # ── Font preview ─────────────────────────────────────────────────
        b.AddSpacer(self.FromDIP(6))
        b.Add(_lbl("預覽"), 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(4))
        _prev_panel = wx.Panel(panel)
        _prev_panel.SetBackgroundColour(wx.Colour(0xc0, 0xc0, 0xc0))
        _prev_box = wx.BoxSizer(wx.VERTICAL)
        self._prev_en = wx.StaticText(_prev_panel, label="Real-time Subtitle — ASR 辨識原文")
        self._prev_en.SetForegroundColour(wx.Colour(0xe0, 0xe0, 0xe0))
        self._prev_zh = wx.StaticText(_prev_panel, label="即時字幕翻譯文字預覽")
        self._prev_zh.SetForegroundColour(wx.Colour(0xff, 0xff, 0xff))
        _prev_box.Add(self._prev_en, 0, wx.ALL, self.FromDIP(8))
        _prev_box.Add(self._prev_zh, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(8))
        _prev_panel.SetSizer(_prev_box)
        b.Add(_prev_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)

        b.AddSpacer(self.FromDIP(14))
        b.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(14))

        # ── 顯示選項 ─────────────────────────────────────────────────────
        b.Add(_lbl("原文顯示"), 0, wx.LEFT | wx.RIGHT, pad)
        b.AddSpacer(self.FromDIP(4))
        self._chk_corrected = _dark(
            wx.CheckBox(panel, label="顯示校正後 ASR 原文"), fg=_TEXT)
        self._chk_corrected.SetValue(self._show_corrected)
        b.Add(self._chk_corrected, 0, wx.LEFT | wx.RIGHT, pad)
        self._chk_raw = _dark(
            wx.CheckBox(panel, label="顯示原始 ASR 辨識"), fg=_TEXT)
        self._chk_raw.SetValue(self._show_raw)
        b.Add(self._chk_raw, 0, wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(4))

        panel.SetSizer(b)
        self._update_preview()

    def _update_preview(self):
        f_en = wx.Font(wx.FontInfo(self._sl_en.GetValue()).FaceName(_UI_FONT_FACE))
        f_zh = wx.Font(wx.FontInfo(self._sl_zh.GetValue()).FaceName(_UI_FONT_FACE))
        self._prev_en.SetFont(f_en)
        self._prev_zh.SetFont(f_zh)
        self._prev_en.GetParent().Layout()

    def _on_mode_change(self, _event):
        is_local = self._rb_local_mode.GetValue()
        self._local_panel.Show(is_local)
        self._server_panel.Show(not is_local)
        self._local_panel.GetParent().Layout()

    def _on_source_change(self, _event):
        is_monitor = self._rb_monitor.GetValue()
        mon = self._mon_choice or self._mon_entry
        mic = self._mic_choice or self._mic_entry
        mon.Show() if is_monitor else mon.Hide()
        mic.Hide() if is_monitor else mic.Show()
        self._dev_panel.Layout()
        self._dev_panel.GetParent().Layout()

    def _refresh_model_status(self):
        if self._model_path[0]:
            self._model_status_lbl.SetLabel(
                f"✅ 已就緒：{os.path.basename(self._model_path[0])}")
            self._model_status_lbl.SetForegroundColour(wx.Colour(74, 222, 128))
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

    def _on_ok(self, _event):
        x, y = self.GetPosition()

        is_monitor = self._rb_monitor.GetValue()
        mon_val = (self._mon_choice.GetStringSelection() if self._mon_choice
                   else self._mon_entry.GetValue().strip())
        mic_val = (self._mic_choice.GetStringSelection() if self._mic_choice
                   else self._mic_entry.GetValue().strip())
        src_code = lang_label_to_code(self._src_choice.GetStringSelection())
        tgt_code = lang_label_to_code(self._tgt_choice.GetStringSelection())

        if self._rb_server_mode.GetValue():
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
                "context":           self._ctx_entry.GetValue().strip(),
                "en_font_size":      self._sl_en.GetValue(),
                "zh_font_size":      self._sl_zh.GetValue(),
                "show_raw":          self._chk_raw.GetValue(),
                "show_corrected":    self._chk_corrected.GetValue(),
                "enable_denoise":    self._chk_denoise.GetValue(),
                "_dialog_x":         x,
                "_dialog_y":         y,
            }
        else:
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
                "asr_server":        "http://localhost:8000",
                "source":            "monitor" if is_monitor else "mic",
                "monitor_device":    mon_val,
                "mic_device":        mic_val,
                "direction":         f"{src_code}→{tgt_code}",
                "openai_api_key":    self._key_entry.GetValue().strip(),
                "context":           self._ctx_entry.GetValue().strip(),
                "en_font_size":      self._sl_en.GetValue(),
                "zh_font_size":      self._sl_zh.GetValue(),
                "show_raw":          self._chk_raw.GetValue(),
                "show_corrected":    self._chk_corrected.GetValue(),
                "enable_denoise":    self._chk_denoise.GetValue(),
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
