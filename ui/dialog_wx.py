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

    def _build_tab_asr(self, panel): pass
    def _build_tab_translation(self, panel): pass
    def _build_tab_display(self, panel): pass

    def _on_ok(self, _event):
        # Placeholder — will be fully implemented in Task 5
        x, y = self.GetPosition()
        self.result = {
            "backend":           self._config.get("backend", "local"),
            "asr_server":        self._config.get("asr_server", "http://localhost:8765"),
            "local_model_path":  self._config.get("local_model_path", ""),
            "local_chatllm_dir": self._config.get("local_chatllm_dir", ""),
            "local_device_id":   self._config.get("local_device_id", 0),
            "source":            self._config.get("source", "monitor"),
            "monitor_device":    self._config.get("monitor_device", ""),
            "mic_device":        self._config.get("mic_device", ""),
            "direction":         self._config.get("direction", "en→zh"),
            "openai_api_key":    self._config.get("openai_api_key", ""),
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
