# Design: Replace CustomTkinter Setup Dialog with wxPython

**Date:** 2026-03-02
**Status:** Approved

## Problem

The setup dialog (`ui/dialog_tk.py`) uses CustomTkinter (CTk) for its dark-themed UI.
When the dialog is dragged between monitors with different DPI (e.g. 125% ↔ 100%),
CTk must call `set_widget_scaling()` and `set_window_scaling()` globally, which triggers
a full widget re-layout. Even with a WM_DPICHANGED WndProc hook + withdraw/deiconify,
the re-layout takes ~100–300ms, causing a visible flash.

## Solution

Replace the CTk portion of the dialog with **wxPython**, which handles per-monitor DPI
natively via `window.FromDIP()` and fires `wx.EVT_DPI_CHANGED` automatically—no ctypes
WndProc subclassing required. Bind `EVT_DPI_CHANGED` to call `Layout()` only.

## Scope

- **Replace:** `ui/dialog_tk.py` CTk code → new `ui/dialog_wx.py` (wxPython)
- **Keep:** overlay (`ui/overlay_tk.py`), GTK dialog (`ui/dialog_gtk.py`), worker, audio
- **Simplify:** `ui/dialog_tk.py` → strip CTk, keep only the plain-tkinter fallback

## Architecture

### File Changes

| File | Action |
|------|--------|
| `ui/dialog_wx.py` | **CREATE** — `SetupDialogWx` class |
| `ui/dialog_tk.py` | **SIMPLIFY** — remove CTk code; keep `_run_tk()` only |
| `subtitle_client.py` | **UPDATE** — import wxPython dialog with CTk/tk fallback |
| `requirements.txt` | **ADD** `wxPython` |

### Import Priority (subtitle_client.py)

```python
try:
    from ui.dialog_wx import SetupDialogWx
    _WX_AVAILABLE = True
except ImportError:
    _WX_AVAILABLE = False

def show_setup_dialog(config):
    if _GTK3_AVAILABLE and sys.platform != "win32":
        return SetupDialogGTK(config).run()
    if _WX_AVAILABLE:
        return SetupDialogWx(config).run()
    return SetupDialogTk(config).run()
```

### wx.App Lifecycle

- Lazy singleton `_wx_app` in `dialog_wx.py` module scope
- Created once on first use: `wx.App(redirect=False)`
- Never call `app.MainLoop()` — `dialog.ShowModal()` handles its own event loop
- Works both at startup (before tkinter) and from overlay (inside tkinter mainloop)

### DPI Handling

```python
class _SetupWxDialog(wx.Dialog):
    def __init__(self, parent, config):
        super().__init__(parent, title="Real-time Subtitle",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.Bind(wx.EVT_DPI_CHANGED, self._on_dpi_changed)
        # Use FromDIP() for all sizes
        self.SetSize(self.FromDIP(wx.Size(460, 600)))

    def _on_dpi_changed(self, event):
        self.Layout()
        event.Skip()
```

### Widget Mapping

| CTk Widget | wxPython Widget |
|-----------|-----------------|
| `CTkFrame` | `wx.Panel` + `SetBackgroundColour` |
| `CTkLabel` | `wx.StaticText` |
| `CTkEntry` (password) | `wx.TextCtrl(style=wx.TE_PASSWORD)` |
| `CTkEntry` (text) | `wx.TextCtrl` |
| `CTkSegmentedButton` | Two `wx.RadioButton` in a `wx.BoxSizer` |
| `CTkOptionMenu` | `wx.Choice` |
| `CTkButton` | `wx.Button` (native style, accepted) |
| `CTkCheckBox` | `wx.CheckBox` |
| `CTkSlider` | `wx.Slider` |
| `CTkFont` | `wx.Font` |

### Dark Color Scheme

```python
BG      = wx.Colour(13,  13,  26)   # body background  #0d0d1a
HEADER  = wx.Colour(26,  26,  46)   # header panel     #1a1a2e
TEXT    = wx.Colour(224, 224, 224)  # main text        #e0e0e0
SUBTEXT = wx.Colour(156, 163, 175)  # label text       #9ca3af
ACCENT  = wx.Colour(126, 184, 247)  # accent/title     #7eb8f7
ENTRY   = wx.Colour(45,  45,  78)   # entry background
WARN    = wx.Colour(248, 113, 113)  # error text       #f87171
```

Buttons retain native Windows appearance (user-approved).

### Public Interface (unchanged)

```python
class SetupDialogWx:
    def __init__(self, config: dict): ...
    def run(self) -> dict | None:
        """Standalone: create wx.App + ShowModal. Used at startup."""
    def run_as_toplevel(self, parent) -> dict | None:
        """From overlay: ShowModal only (wx.App already exists or created)."""
```

Result dict keys (unchanged):
`asr_server`, `source`, `monitor_device`, `mic_device`, `direction`,
`openai_api_key`, `context`, `en_font_size`, `zh_font_size`,
`show_raw`, `show_corrected`, `_dialog_x`, `_dialog_y`

### Advanced Dialog

Same structure as current: opens as child `wx.Dialog` with sliders, checkboxes,
preview text labels, OK/Cancel.

## What Is Removed

- `customtkinter` dependency (remove from `requirements.txt`)
- `_hook_dpi_changed()` function (ctypes WndProc subclassing)
- `_get_dpi_scale()` function
- `_run_ctk()` method in `SetupDialogTk`
- `_CTK_AVAILABLE` flag

## Risk & Mitigation

| Risk | Mitigation |
|------|-----------|
| wx + tkinter in same process | wx.App singleton + ShowModal (no MainLoop conflict) |
| Native button style in dark dialog | User accepted; partial dark is fine |
| wxPython not installed | Fallback to plain tkinter dialog |
| PyInstaller bundling | Add `wx` to `hiddenimports` in `subtitle_client.spec` |
