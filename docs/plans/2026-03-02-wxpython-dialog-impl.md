# wxPython Setup Dialog Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the CustomTkinter setup dialog with a wxPython dialog that handles per-monitor DPI natively without re-layout flash.

**Architecture:** New `ui/dialog_wx.py` provides `SetupDialogWx` with the same `.run()` / `.run_as_toplevel(parent)` interface. `subtitle_client.py` tries wxPython first, falls back to plain tkinter. `ui/dialog_tk.py` is stripped down to plain-tkinter only (CTk code removed).

**Tech Stack:** wxPython ≥ 4.2.0, Python 3.10+, Win32 (primary), plain tkinter fallback

---

## Before You Start

- Working directory: `C:\Users\pang2\realtime-subtitle`
- Active venv: `.venv\Scripts\activate` (PowerShell) or `source .venv/bin/activate` (bash)
- Kill any running subtitle service before testing:
  ```powershell
  powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"
  ```
- Run the app to test:
  ```bash
  .venv/Scripts/python subtitle_client.py
  ```

---

## Task 1: Install wxPython and update requirements.txt

**Files:**
- Modify: `requirements.txt`

**Step 1: Install wxPython in the venv**

```bash
.venv/Scripts/pip install wxPython
```

Expected: Successfully installed wxPython-4.2.x (or later)

**Step 2: Update requirements.txt**

Replace `customtkinter>=5.2.0` with `wxPython>=4.2.0`:

```
sounddevice>=0.4.6
numpy>=1.24.0
scipy>=1.11.0
requests>=2.31.0
openai>=1.30.0
onnxruntime>=1.16.0
pyaudiowpatch>=0.2.12
opencc-python-reimplemented>=0.1.7
wxPython>=4.2.0
```

**Step 3: Verify wxPython works**

```bash
.venv/Scripts/python -c "import wx; print(wx.version())"
```

Expected: prints something like `4.2.1 msw (phoenix) wxWidgets 3.2.4`

**Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: replace customtkinter with wxPython"
```

---

## Task 2: Create ui/dialog_wx.py — skeleton + wx.App singleton

**Files:**
- Create: `ui/dialog_wx.py`

**Step 1: Create the file with imports, colors, app singleton, and class skeleton**

Write `ui/dialog_wx.py` with the following content:

```python
# ui/dialog_wx.py
"""wxPython 啟動設定對話框（Windows，DPI-aware）。"""
import logging
import os
import sys

import wx

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


# ── Helper: apply dark colours to any widget tree ────────────────────────────
def _dark(widget, bg=None, fg=None):
    if bg is not None:
        widget.SetBackgroundColour(bg)
    if fg is not None:
        widget.SetForegroundColour(fg)
    return widget


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
        """Called from inside tkinter mainloop. parent is a tkinter widget.
        ShowModal() runs its own nested Win32 message loop; tkinter pauses."""
        _get_wx_app()
        px, py = None, None
        if parent is not None:
            try:
                parent.update_idletasks()
                px, py = parent.winfo_x(), parent.winfo_y()
            except Exception:
                pass
        dlg = _SetupWxDlg(None, self._config, initial_pos=(px, py) if px is not None else None)
        dlg.ShowModal()
        result = dlg.result
        dlg.Destroy()
        return result
```

**Step 2: Verify import works**

```bash
.venv/Scripts/python -c "from ui.dialog_wx import SetupDialogWx; print('OK')"
```

Expected: `OK`

**Step 3: Commit skeleton**

```bash
git add ui/dialog_wx.py
git commit -m "feat: add ui/dialog_wx.py skeleton (SetupDialogWx, wx.App singleton)"
```

---

## Task 3: Implement _AdvancedDialog (advanced settings popup)

**Files:**
- Modify: `ui/dialog_wx.py` — add `_AdvancedDialog` class before `SetupDialogWx`

**Step 1: Add _AdvancedDialog class**

Insert this class after the `_dark` helper and before `SetupDialogWx` in `ui/dialog_wx.py`:

```python
class _AdvancedDialog(wx.Dialog):
    """進階設定彈出視窗：字體大小、提示詞、顯示選項。"""

    def __init__(self, parent, en_size: int, zh_size: int,
                 context: str, show_raw: bool, show_corrected: bool):
        super().__init__(parent, title="進階設定",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.result = None
        self._font_family = "Noto Sans TC SemiBold"

        self.SetBackgroundColour(_BG)

        # Working copies (cancel does not affect caller's vars)
        self._en_size      = en_size
        self._zh_size      = zh_size
        self._context      = context
        self._show_raw     = show_raw
        self._show_corrected = show_corrected

        self._build()
        self.SetSize(self.FromDIP(wx.Size(440, -1)))
        self.Layout()
        self.Fit()

    def _build(self):
        outer = wx.BoxSizer(wx.VERTICAL)
        pad = self.FromDIP(16)

        # Title
        title = _dark(wx.StaticText(self, label="⚙  進階設定"),
                      fg=_ACCENT)
        f = title.GetFont()
        f.SetPointSize(12)
        f.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(f)
        outer.Add(title, 0, wx.ALL, pad)

        # Context hint
        outer.Add(_dark(wx.StaticText(self, label="辨識提示詞（選填）"), fg=_SUBTEXT),
                  0, wx.LEFT | wx.RIGHT | wx.TOP, pad)
        self._ctx_entry = _dark(
            wx.TextCtrl(self, value=self._context,
                        size=self.FromDIP(wx.Size(-1, 32))),
            bg=_ENTRY, fg=_TEXT,
        )
        outer.Add(self._ctx_entry, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, pad)

        outer.AddSpacer(self.FromDIP(10))

        # Show options
        outer.Add(_dark(wx.StaticText(self, label="原文顯示"), fg=_SUBTEXT),
                  0, wx.LEFT | wx.RIGHT, pad)
        self._chk_corrected = _dark(
            wx.CheckBox(self, label="顯示校正後 ASR 原文"),
            fg=_TEXT,
        )
        self._chk_corrected.SetValue(self._show_corrected)
        outer.Add(self._chk_corrected, 0, wx.LEFT | wx.RIGHT | wx.TOP, pad)

        self._chk_raw = _dark(
            wx.CheckBox(self, label="顯示原始 ASR 辨識"),
            fg=_TEXT,
        )
        self._chk_raw.SetValue(self._show_raw)
        outer.Add(self._chk_raw, 0, wx.LEFT | wx.RIGHT, pad)

        outer.AddSpacer(self.FromDIP(10))

        # Font size sliders
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
                           style=wx.SL_HORIZONTAL | wx.SL_LABELS,
                           size=self.FromDIP(wx.Size(250, -1)))
            setattr(self, attr_name, sl)
            row.Add(sl, 1, wx.EXPAND | wx.LEFT, self.FromDIP(8))
            outer.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, pad)
            outer.AddSpacer(self.FromDIP(6))

        outer.AddSpacer(self.FromDIP(8))

        # Buttons
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "取消",
                               size=self.FromDIP(wx.Size(100, 34)))
        ok_btn = wx.Button(self, wx.ID_OK, "確認",
                           size=self.FromDIP(wx.Size(100, 34)))
        btn_row.Add(cancel_btn, 0, wx.RIGHT, self.FromDIP(8))
        btn_row.Add(ok_btn)
        outer.Add(btn_row, 0, wx.ALIGN_RIGHT | wx.ALL, pad)

        self.SetSizer(outer)

        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)

    def _on_key(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self._on_ok(None)
        elif event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()

    def _on_ok(self, _event):
        self.result = {
            "en_font_size":    self._sl_en.GetValue(),
            "zh_font_size":    self._sl_zh.GetValue(),
            "context":         self._ctx_entry.GetValue().strip(),
            "show_raw":        self._chk_raw.GetValue(),
            "show_corrected":  self._chk_corrected.GetValue(),
        }
        self.EndModal(wx.ID_OK)
```

**Step 2: Commit**

```bash
git add ui/dialog_wx.py
git commit -m "feat: add _AdvancedDialog for wxPython settings popup"
```

---

## Task 4: Implement _SetupWxDlg (main dialog)

**Files:**
- Modify: `ui/dialog_wx.py` — add `_SetupWxDlg` class before `SetupDialogWx`

**Step 1: Add _SetupWxDlg class**

Insert this class between `_AdvancedDialog` and `SetupDialogWx`:

```python
class _SetupWxDlg(wx.Dialog):
    """Main setup dialog (wx)."""

    def __init__(self, parent, config: dict, initial_pos=None):
        super().__init__(parent, title="Real-time Subtitle",
                         style=wx.DEFAULT_DIALOG_STYLE)
        self.result = None
        self._config = config
        self._font_family = "Noto Sans TC SemiBold"

        # Advanced settings vars (updated by advanced dialog)
        self._en_size       = int(config.get("en_font_size", 15))
        self._zh_size       = int(config.get("zh_font_size", 24))
        self._context       = config.get("context", "")
        self._show_raw      = bool(config.get("show_raw", False))
        self._show_corrected = bool(config.get("show_corrected", True))

        self.SetBackgroundColour(_BG)
        self._build()

        self.Bind(wx.EVT_DPI_CHANGED, self._on_dpi_changed)
        self.Bind(wx.EVT_CHAR_HOOK,   self._on_key)

        self.SetSize(self.FromDIP(wx.Size(460, 600)))
        self.Layout()

        if initial_pos:
            self.SetPosition(wx.Point(*initial_pos))
        else:
            self.CentreOnScreen()

    # ── DPI ──────────────────────────────────────────────────────────────────
    def _on_dpi_changed(self, event):
        self.Layout()
        event.Skip()

    # ── Keyboard ─────────────────────────────────────────────────────────────
    def _on_key(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self._on_ok(None)
        elif event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()

    # ── Layout ───────────────────────────────────────────────────────────────
    def _lbl(self, text, fg=None):
        return _dark(wx.StaticText(self, label=text), fg=fg or _SUBTEXT)

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

        # Helper to add a label + widget pair
        def _row(label, widget, top=14):
            lbl = _dark(wx.StaticText(body, label=label), fg=_SUBTEXT)
            b.Add(lbl, 0, wx.BOTTOM, self.FromDIP(4))
            b.Add(widget, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(top))

        # OpenAI API Key
        _existing_key = (
            self._config.get("openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self._key_entry = _dark(
            wx.TextCtrl(body, value=_existing_key,
                        style=wx.TE_PASSWORD,
                        size=self.FromDIP(wx.Size(-1, 36))),
            bg=_ENTRY, fg=_TEXT,
        )
        _row("OpenAI API Key", self._key_entry)

        # ASR Server URL
        self._url_entry = _dark(
            wx.TextCtrl(body, value=self._config.get("asr_server", "http://localhost:8000"),
                        size=self.FromDIP(wx.Size(-1, 36))),
            bg=_ENTRY, fg=_TEXT,
        )
        _row("ASR Server URL", self._url_entry)

        # Audio source selection
        b.Add(_dark(wx.StaticText(body, label="音訊來源"), fg=_SUBTEXT),
              0, wx.BOTTOM, self.FromDIP(4))
        src_row = wx.BoxSizer(wx.HORIZONTAL)
        self._rb_monitor = _dark(
            wx.RadioButton(body, label="🔊 系統音訊", style=wx.RB_GROUP),
            fg=_TEXT,
        )
        self._rb_mic = _dark(
            wx.RadioButton(body, label="🎤 麥克風"),
            fg=_TEXT,
        )
        _saved_src = self._config.get("source", "monitor")
        self._rb_monitor.SetValue(_saved_src == "monitor")
        self._rb_mic.SetValue(_saved_src == "mic")
        src_row.Add(self._rb_monitor, 0, wx.RIGHT, self.FromDIP(20))
        src_row.Add(self._rb_mic)
        b.Add(src_row, 0, wx.BOTTOM, self.FromDIP(8))

        # Device selector panel (switches between monitor/mic)
        self._dev_panel = wx.Panel(body)
        self._dev_panel.SetBackgroundColour(_BG)
        dev_sizer = wx.BoxSizer(wx.VERTICAL)

        # Monitor device choice
        monitor_devices = _list_audio_devices_for_dialog()
        _saved_mon = self._config.get("monitor_device", "")
        if monitor_devices:
            self._mon_choice = _dark(
                wx.Choice(self._dev_panel, choices=monitor_devices),
                fg=_TEXT,
            )
            _idx = monitor_devices.index(_saved_mon) if _saved_mon in monitor_devices else 0
            self._mon_choice.SetSelection(_idx)
            self._mon_entry = None
        else:
            self._mon_choice = None
            self._mon_entry = _dark(
                wx.TextCtrl(self._dev_panel, value=_saved_mon,
                            size=self.FromDIP(wx.Size(-1, 36))),
                bg=_ENTRY, fg=_TEXT,
            )

        mon_widget = self._mon_choice or self._mon_entry
        dev_sizer.Add(mon_widget, 0, wx.EXPAND)

        # Mic device choice
        mic_devices = _list_mic_devices_for_dialog()
        _saved_mic = self._config.get("mic_device", "")
        if mic_devices:
            self._mic_choice = _dark(
                wx.Choice(self._dev_panel, choices=mic_devices),
                fg=_TEXT,
            )
            _idx = mic_devices.index(_saved_mic) if _saved_mic in mic_devices else 0
            self._mic_choice.SetSelection(_idx)
            self._mic_entry = None
        else:
            self._mic_choice = None
            self._mic_entry = _dark(
                wx.TextCtrl(self._dev_panel, value=_saved_mic,
                            size=self.FromDIP(wx.Size(-1, 36))),
                bg=_ENTRY, fg=_TEXT,
            )

        mic_widget = self._mic_choice or self._mic_entry
        dev_sizer.Add(mic_widget, 0, wx.EXPAND)

        self._dev_panel.SetSizer(dev_sizer)
        b.Add(self._dev_panel, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(14))

        # Bind radio buttons to show/hide device widgets
        self._rb_monitor.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)
        self._rb_mic.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)
        self._on_source_change(None)  # initialize visibility

        # Translation direction
        b.Add(_dark(wx.StaticText(body, label="翻譯方向"), fg=_SUBTEXT),
              0, wx.BOTTOM, self.FromDIP(4))
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        dir_row = wx.BoxSizer(wx.HORIZONTAL)
        self._src_choice = _dark(
            wx.Choice(body, choices=LANG_LABELS),
            fg=_TEXT,
        )
        _sidx = LANG_LABELS.index(lang_code_to_label(_src0)) if lang_code_to_label(_src0) in LANG_LABELS else 0
        self._src_choice.SetSelection(_sidx)

        swap_btn = wx.Button(body, label="⇄",
                             size=self.FromDIP(wx.Size(40, 34)))
        swap_btn.Bind(wx.EVT_BUTTON, self._on_swap)

        self._tgt_choice = _dark(
            wx.Choice(body, choices=LANG_LABELS),
            fg=_TEXT,
        )
        _tidx = LANG_LABELS.index(lang_code_to_label(_tgt0)) if lang_code_to_label(_tgt0) in LANG_LABELS else 1
        self._tgt_choice.SetSelection(_tidx)

        dir_row.Add(self._src_choice, 1, wx.EXPAND | wx.RIGHT, self.FromDIP(6))
        dir_row.Add(swap_btn, 0, wx.RIGHT, self.FromDIP(6))
        dir_row.Add(self._tgt_choice, 1, wx.EXPAND)
        b.Add(dir_row, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(4))

        # Warning label
        self._warn_lbl = _dark(wx.StaticText(body, label=""), fg=_WARN)
        b.Add(self._warn_lbl, 0, wx.BOTTOM, self.FromDIP(4))

        body.SetSizer(b)
        outer.Add(body, 1, wx.EXPAND | wx.ALL, pad)

        # ── Advanced settings button ──────────────────────────────────────────
        adv_btn = wx.Button(self, label="⚙  進階設定",
                            size=self.FromDIP(wx.Size(-1, 30)),
                            style=wx.BORDER_NONE)
        adv_btn.Bind(wx.EVT_BUTTON, self._open_advanced)
        outer.Add(adv_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                  self.FromDIP(16))

        # ── Button row ────────────────────────────────────────────────────────
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "取消",
                               size=self.FromDIP(wx.Size(-1, 38)))
        ok_btn = wx.Button(self, wx.ID_OK, "啟動字幕辨識",
                           size=self.FromDIP(wx.Size(-1, 38)))
        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        btn_row.Add(cancel_btn, 1, wx.EXPAND | wx.RIGHT, self.FromDIP(6))
        btn_row.Add(ok_btn, 1, wx.EXPAND)
        outer.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
                  self.FromDIP(16))

        self.SetSizer(outer)

    # ── Event handlers ────────────────────────────────────────────────────────
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
        )
        if adv.ShowModal() == wx.ID_OK and adv.result:
            r = adv.result
            self._en_size       = r["en_font_size"]
            self._zh_size       = r["zh_font_size"]
            self._context       = r["context"]
            self._show_raw      = r["show_raw"]
            self._show_corrected = r["show_corrected"]
        adv.Destroy()

    def _on_ok(self, _event):
        api_key = self._key_entry.GetValue().strip()
        if not api_key:
            self._warn_lbl.SetLabel("⚠ 請填入 OpenAI API Key")
            return
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
        self.result = {
            "asr_server":      self._url_entry.GetValue().strip() or "http://localhost:8000",
            "source":          "monitor" if is_monitor else "mic",
            "monitor_device":  mon_val,
            "mic_device":      mic_val,
            "direction":       f"{src_code}→{tgt_code}",
            "openai_api_key":  api_key,
            "context":         self._context,
            "en_font_size":    self._en_size,
            "zh_font_size":    self._zh_size,
            "show_raw":        self._show_raw,
            "show_corrected":  self._show_corrected,
            "_dialog_x":       x,
            "_dialog_y":       y,
        }
        self.EndModal(wx.ID_OK)
```

**Step 2: Verify the file imports cleanly**

```bash
.venv/Scripts/python -c "from ui.dialog_wx import SetupDialogWx; print('OK')"
```

Expected: `OK` (no errors)

**Step 3: Quick smoke test (launches the dialog)**

```bash
.venv/Scripts/python -c "
from ui.dialog_wx import SetupDialogWx
r = SetupDialogWx({'asr_server':'http://localhost:8000','direction':'en→zh'}).run()
print(r)
"
```

Expected: dialog opens, fill fields, click "啟動字幕辨識", result dict prints. Cancel prints `None`.

**Step 4: Commit**

```bash
git add ui/dialog_wx.py
git commit -m "feat: implement _SetupWxDlg and _AdvancedDialog in dialog_wx.py"
```

---

## Task 5: Update subtitle_client.py — import priority

**Files:**
- Modify: `subtitle_client.py`

**Step 1: Read the current imports section (lines 27-39)**

Current:
```python
from ui.dialog_gtk import SetupDialogGTK
from ui.dialog_tk import SetupDialogTk
```

**Step 2: Replace dialog imports to add wxPython priority**

Find and replace the block:
```python
from ui.dialog_gtk import SetupDialogGTK
from ui.dialog_tk import SetupDialogTk
```

With:
```python
from ui.dialog_gtk import SetupDialogGTK
from ui.dialog_tk import SetupDialogTk
try:
    from ui.dialog_wx import SetupDialogWx as _SetupDialogWx
    _WX_AVAILABLE = True
except ImportError:
    _WX_AVAILABLE = False
```

**Step 3: Update show_setup_dialog() function**

Find:
```python
def show_setup_dialog(config: dict) -> dict | None:
    """選擇正確的對話框實作並顯示，回傳設定 dict 或 None（取消）。"""
    if _GTK3_AVAILABLE and sys.platform != "win32":
        return SetupDialogGTK(config).run()
    return SetupDialogTk(config).run()
```

Replace with:
```python
def show_setup_dialog(config: dict) -> dict | None:
    """選擇正確的對話框實作並顯示，回傳設定 dict 或 None（取消）。"""
    if _GTK3_AVAILABLE and sys.platform != "win32":
        return SetupDialogGTK(config).run()
    if _WX_AVAILABLE:
        return _SetupDialogWx(config).run()
    return SetupDialogTk(config).run()
```

**Step 4: Update on_open_settings() to use wx when available**

Find in `on_open_settings()`:
```python
        if use_gtk:
            new_settings = SetupDialogGTK(_current_config).run()
        else:
            new_settings = SetupDialogTk(_current_config).run_as_toplevel(overlay._root)
```

Replace with:
```python
        if use_gtk:
            new_settings = SetupDialogGTK(_current_config).run()
        elif _WX_AVAILABLE:
            new_settings = _SetupDialogWx(_current_config).run_as_toplevel(overlay._root)
        else:
            new_settings = SetupDialogTk(_current_config).run_as_toplevel(overlay._root)
```

**Step 5: Verify no import errors**

```bash
.venv/Scripts/python -c "import subtitle_client; print('OK')"
```

Expected: `OK`

**Step 6: Commit**

```bash
git add subtitle_client.py
git commit -m "feat: use SetupDialogWx when wxPython available (priority over CTk/tk)"
```

---

## Task 6: Simplify ui/dialog_tk.py (remove CTk code)

**Files:**
- Modify: `ui/dialog_tk.py`

**Step 1: Remove CTk-specific code**

The file is 663 lines. Strip it down to keep only:
- The `SetupDialogTk` class
- Only the `_run_tk()` method (plain tkinter, currently lines 467–662)
- The `run()` and `run_as_toplevel()` methods pointing to `_run_tk`

Remove entirely:
- `import customtkinter as ctk` block and `_CTK_AVAILABLE` flag
- `_hook_dpi_changed()` function
- `_get_dpi_scale()` function
- `_run_ctk()` method (lines 122–462)

The simplified file should look like this (complete replacement):

```python
# ui/dialog_tk.py
"""Plain tkinter 啟動設定對話框（wxPython 和 GTK 均不可用時的 fallback）。"""
import logging
import os
import sys
import tkinter as tk

from config import _list_audio_devices_for_dialog, _list_mic_devices_for_dialog
from languages import LANG_LABELS, lang_code_to_label, lang_label_to_code, parse_direction

log = logging.getLogger(__name__)


class SetupDialogTk:
    """Plain tkinter fallback 設定對話框（wxPython/GTK 均不可用時使用）。"""

    def __init__(self, config: dict):
        self._config = config
        self._result: dict | None = None

    def run(self) -> dict | None:
        return self._run_tk()

    def run_as_toplevel(self, parent) -> dict | None:
        return self._run_tk(parent=parent)

    def _run_tk(self, parent=None) -> dict | None:
        if parent is not None:
            root = tk.Toplevel(parent)
            root.attributes("-topmost", True)
            parent.update_idletasks()
            root.geometry(f"+{parent.winfo_x()}+{parent.winfo_y()}")
        else:
            root = tk.Tk()
        root.title("Real-time Subtitle — 設定")
        root.resizable(False, False)
        root.grab_set()

        pad = {"padx": 12, "pady": 4}

        _existing_key = (
            self._config.get("openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        tk.Label(root, text="OpenAI API Key", anchor="w").pack(fill="x", **pad)
        key_var = tk.StringVar(value=_existing_key)
        tk.Entry(root, textvariable=key_var, show="*", width=48).pack(**pad)

        tk.Label(root, text="ASR Server URL", anchor="w").pack(fill="x", **pad)
        url_var = tk.StringVar(value=self._config.get("asr_server", "http://localhost:8000"))
        tk.Entry(root, textvariable=url_var, width=48).pack(**pad)

        _saved_source = self._config.get("source", "monitor")
        source_var = tk.StringVar(value=_saved_source)
        tk.Label(root, text="音訊來源", anchor="w").pack(fill="x", **pad)
        source_frame = tk.Frame(root)
        source_frame.pack(fill="x", **pad)
        tk.Radiobutton(source_frame, text="🔊 系統音訊", variable=source_var,
                       value="monitor").pack(side="left")
        tk.Radiobutton(source_frame, text="🎤 麥克風", variable=source_var,
                       value="mic").pack(side="left")

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
        _on_source_change()

        tk.Label(root, text="翻譯方向", anchor="w").pack(fill="x", **pad)
        _src0, _tgt0 = parse_direction(self._config.get("direction", "en→zh"))
        src_var = tk.StringVar(value=lang_code_to_label(_src0))
        tgt_var = tk.StringVar(value=lang_code_to_label(_tgt0))
        dir_frame = tk.Frame(root)
        dir_frame.pack(**pad)
        tk.OptionMenu(dir_frame, src_var, *LANG_LABELS).pack(side="left")
        def _tk_swap():
            s, t = src_var.get(), tgt_var.get()
            src_var.set(t); tgt_var.set(s)
        tk.Button(dir_frame, text="⇄", command=_tk_swap).pack(side="left", padx=4)
        tk.OptionMenu(dir_frame, tgt_var, *LANG_LABELS).pack(side="left")

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
                           variable=tmp_show_corrected, anchor="w").pack(fill="x", padx=16)
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
            def _adv_cancel(): popup.destroy()
            def _adv_ok():
                en_size_var.set(tmp_en.get()); zh_size_var.set(tmp_zh.get())
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
                "asr_server":     url_var.get().strip() or "http://localhost:8000",
                "source":         "monitor" if _is_monitor else "mic",
                "monitor_device": monitor_device_var.get().strip(),
                "mic_device":     mic_device_var.get().strip(),
                "direction":      f"{lang_label_to_code(src_var.get())}→{lang_label_to_code(tgt_var.get())}",
                "openai_api_key": api_key,
                "context":        context_var.get().strip(),
                "en_font_size":   en_size_var.get(),
                "zh_font_size":   zh_size_var.get(),
                "show_raw":       show_raw_var.get(),
                "show_corrected": show_corrected_var.get(),
                "_dialog_x":      root.winfo_x(),
                "_dialog_y":      root.winfo_y(),
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
```

**Step 2: Verify**

```bash
.venv/Scripts/python -c "from ui.dialog_tk import SetupDialogTk; print('OK')"
```

Expected: `OK`

**Step 3: Commit**

```bash
git add ui/dialog_tk.py
git commit -m "refactor: strip CustomTkinter from dialog_tk.py, keep plain-tk fallback only"
```

---

## Task 7: Update subtitle_client.spec for PyInstaller

**Files:**
- Modify: `subtitle_client.spec`

**Step 1: Update spec file**

Find:
```python
# customtkinter 需要帶入其 data files（主題 JSON 等）
ctk_datas = collect_data_files("customtkinter")
```
and
```python
    datas=[
        ("silero_vad_v6.onnx", "."),
        ("NotoSansTC-SemiBold.ttf", "."),
        *ctk_datas,
    ],
```
and
```python
    hiddenimports=[
        "constants", "asr", "audio", "worker", "config", "languages",
        "ui", "ui.overlay_gtk", "ui.overlay_tk",
        "ui.dialog_gtk", "ui.dialog_tk",
        "pyaudiowpatch",
        "scipy.signal",
        "scipy._lib.messagestream",
        "opencc",
    ],
```

Replace the entire `Analysis` block:
```python
a = Analysis(
    ["subtitle_client.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("silero_vad_v6.onnx", "."),
        ("NotoSansTC-SemiBold.ttf", "."),
    ],
    hiddenimports=[
        "constants", "asr", "audio", "worker", "config", "languages",
        "ui", "ui.overlay_gtk", "ui.overlay_tk",
        "ui.dialog_gtk", "ui.dialog_tk", "ui.dialog_wx",
        "wx",
        "pyaudiowpatch",
        "scipy.signal",
        "scipy._lib.messagestream",
        "opencc",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
```

Also remove the two lines at the top:
```python
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# customtkinter 需要帶入其 data files（主題 JSON 等）
ctk_datas = collect_data_files("customtkinter")
```

(The `collect_data_files` import is no longer needed since we removed CTk.)

**Step 2: Commit**

```bash
git add subtitle_client.spec
git commit -m "build: update spec for wxPython (remove customtkinter, add wx hidden import)"
```

---

## Task 8: Integration Test — run the full app

**Step 1: Kill any existing python processes**

```powershell
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"
```

**Step 2: Start the app**

```bash
.venv/Scripts/python subtitle_client.py
```

**Expected behavior:**
1. wxPython setup dialog appears (dark background, native buttons)
2. Fill in API key and ASR server URL, click "啟動字幕辨識"
3. Subtitle overlay appears
4. Drag the dialog (during next open from overlay) between monitors — no visible re-layout flash

**Step 3: Test from-overlay settings**

- Click ⚙ in overlay toolbar
- Settings dialog opens near overlay
- Drag it to the other monitor
- Confirm: instant DPI adjustment, no jarring re-layout

**Step 4: Test fallback**

```bash
.venv/Scripts/python -c "
import sys; sys.modules['wx'] = None  # simulate wx missing
from ui.dialog_tk import SetupDialogTk
print('fallback OK')
"
```

Expected: `fallback OK`

**Step 5: Commit if not already done, then final commit message**

```bash
git log --oneline -6
```

Should show the chain of commits for this feature.

---

## Task 9: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update the dialog_tk.py description row in the architecture table**

Find:
```
| `ui/dialog_tk.py` | `SetupDialogTk`（tkinter 啟動設定對話框）|
```

Update to:
```
| `ui/dialog_wx.py` | `SetupDialogWx`（wxPython 啟動設定對話框，Windows 主要使用）|
| `ui/dialog_tk.py` | `SetupDialogTk`（plain-tkinter fallback，wxPython 不可用時）|
```

**Step 2: Update the dialog priority note in Setup dialog classes section**

Find the `show_setup_dialog` description and update to:
> GTK3 (Linux) → wxPython (Windows) → plain tkinter (fallback)

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for wxPython dialog and import priority"
```

---

## Quick Reference: What Each File Does After This Change

| File | Role |
|------|------|
| `ui/dialog_wx.py` | NEW — wxPython dialog, used on Windows when wx installed |
| `ui/dialog_tk.py` | SIMPLIFIED — plain-tk fallback only, no CTk |
| `ui/dialog_gtk.py` | UNCHANGED — GTK3 Linux dialog |
| `subtitle_client.py` | UPDATED — import priority: GTK > wx > tk |
| `requirements.txt` | UPDATED — wx instead of customtkinter |
| `subtitle_client.spec` | UPDATED — no ctk_datas, add wx hiddenimport |
