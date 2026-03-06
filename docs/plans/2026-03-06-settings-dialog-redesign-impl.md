# Settings Dialog Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the flat single-page `_SetupWxDlg` layout with a 3-tab `wx.Notebook` (辨識 / 翻譯 / 顯示) and remove the secondary `_AdvancedDialog` popup entirely.

**Architecture:** All content from both `_SetupWxDlg._build()` and `_AdvancedDialog` is redistributed into three notebook tab panels. State variables (`_en_size`, `_zh_size`, etc.) remain on `_SetupWxDlg`. The outer structure (header + notebook + global buttons) replaces the current `outer` BoxSizer.

**Tech Stack:** wxPython (`wx.Notebook`, `wx.Panel`, existing dark-theme helpers in `ui/dialog_wx.py`)

---

## Pre-work: read the file

Before any task, read `ui/dialog_wx.py` in full to understand exact current variable names and helper functions.

---

### Task 1: Remove `_AdvancedDialog` and scaffold `wx.Notebook`

**Files:**
- Modify: `ui/dialog_wx.py`

**What to do:**

1. Delete the entire `_AdvancedDialog` class (lines ~110–225).

2. In `_SetupWxDlg.__init__`, remove the `_AdvancedDialog`-related instance vars that are now redundant (they already exist as `_en_size`, `_zh_size`, etc. on `_SetupWxDlg` — confirm they're there, add any that are missing):
   ```python
   self._en_size        = int(config.get("en_font_size", 15))
   self._zh_size        = int(config.get("zh_font_size", 24))
   self._context        = config.get("context", "")
   self._show_raw       = bool(config.get("show_raw", False))
   self._show_corrected = bool(config.get("show_corrected", True))
   self._enable_denoise = bool(config.get("enable_denoise", True))
   ```

3. Rewrite `_SetupWxDlg._build()` with this skeleton (replace entire method body):
   ```python
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
   ```

4. Add three stub methods so the class doesn't crash:
   ```python
   def _build_tab_asr(self, panel): pass
   def _build_tab_translation(self, panel): pass
   def _build_tab_display(self, panel): pass
   ```

5. Remove `_open_advanced` method and the `_AdvancedDialog` import/usage.

6. Launch app and confirm: dialog opens with 3 empty tabs and global buttons. No crash.

**Commit:**
```bash
git add ui/dialog_wx.py
git commit -m "refactor: scaffold wx.Notebook 3-tab structure, remove _AdvancedDialog"
```

---

### Task 2: Build Tab 1 — 辨識

**Files:**
- Modify: `ui/dialog_wx.py` — implement `_build_tab_asr()`

**What to do:**

Move ASR backend, audio source, and denoise content into this method. The panel is `tab1`.

```python
def _build_tab_asr(self, panel):
    b = wx.BoxSizer(wx.VERTICAL)
    pad = self.FromDIP(16)
    inner_pad = self.FromDIP(12)

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
            rb.SetValue(True); _gpu_activated = True
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
        _ct, _cf = f"✅ 已找到：{self._chatllm_path[0]}", _TEXT
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
    import sys as _sys
    from pathlib import Path as _Path
    _base = (_Path(_sys.executable).parent if getattr(_sys, "frozen", False)
             else _Path(__file__).parent.parent)
    _dtln_ok = (_base / "dtln_model_1.onnx").exists() and (_base / "dtln_model_2.onnx").exists()
    _denoise_label = ("啟用 DTLN 降噪" if _dtln_ok
                      else "啟用 DTLN 降噪（模型檔未下載，無效）")
    self._chk_denoise = _dark(wx.CheckBox(panel, label=_denoise_label), fg=_TEXT)
    self._chk_denoise.SetValue(self._enable_denoise)
    b.Add(self._chk_denoise, 0, wx.LEFT | wx.RIGHT, pad)

    panel.SetSizer(b)

    # Bind events
    self._rb_local_mode.Bind(wx.EVT_RADIOBUTTON, self._on_mode_change)
    self._rb_server_mode.Bind(wx.EVT_RADIOBUTTON, self._on_mode_change)
    self._rb_monitor.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)
    self._rb_mic.Bind(wx.EVT_RADIOBUTTON, self._on_source_change)

    self._on_source_change(None)
    self._on_mode_change(None)
    self._refresh_model_status()
```

**Note:** `_on_mode_change` and `_on_source_change` must no longer call `self.Fit()` (notebook tabs don't resize the dialog). Update them:

```python
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
```

**Launch and verify:** Tab 1 shows all ASR content, switching mode/source toggles panels correctly.

**Commit:**
```bash
git add ui/dialog_wx.py
git commit -m "feat: settings tab 1 — ASR backend, audio source, denoise"
```

---

### Task 3: Build Tab 2 — 翻譯

**Files:**
- Modify: `ui/dialog_wx.py` — implement `_build_tab_translation()`

```python
def _build_tab_translation(self, panel):
    b = wx.BoxSizer(wx.VERTICAL)
    pad = self.FromDIP(16)

    def _lbl(text):
        return _dark(wx.StaticText(panel, label=text), fg=_SUBTEXT)

    # ── API Key ──────────────────────────────────────────────────────
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
```

**Launch and verify:** Tab 2 shows API Key, translation direction with swap button, context hint.

**Commit:**
```bash
git add ui/dialog_wx.py
git commit -m "feat: settings tab 2 — API key, translation direction, context hint"
```

---

### Task 4: Build Tab 3 — 顯示

**Files:**
- Modify: `ui/dialog_wx.py` — implement `_build_tab_display()`

```python
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
```

Add `_update_preview` on `_SetupWxDlg` (replaces the one that was on `_AdvancedDialog`):

```python
def _update_preview(self):
    f_en = wx.Font(wx.FontInfo(self._sl_en.GetValue()).FaceName(_UI_FONT_FACE))
    f_zh = wx.Font(wx.FontInfo(self._sl_zh.GetValue()).FaceName(_UI_FONT_FACE))
    self._prev_en.SetFont(f_en)
    self._prev_zh.SetFont(f_zh)
    self._prev_en.GetParent().Layout()
```

Call `self._update_preview()` at the end of `_build_tab_display()` (after `panel.SetSizer(b)`).

**Launch and verify:** Tab 3 shows sliders with live preview, display option checkboxes.

**Commit:**
```bash
git add ui/dialog_wx.py
git commit -m "feat: settings tab 3 — font sliders, live preview, display options"
```

---

### Task 5: Wire `_on_ok`, clean up `__init__`, fix dialog size

**Files:**
- Modify: `ui/dialog_wx.py`

**Steps:**

1. Update `_on_ok` to read from the new widget locations (sliders are now `self._sl_en` / `self._sl_zh` on `_SetupWxDlg`; checkboxes are `self._chk_corrected`, `self._chk_raw`, `self._chk_denoise`; context is `self._ctx_entry`). The current `_on_ok` already reads `self._en_size` etc. — update it to read directly from widgets:
   - Replace `self._en_size` → `self._sl_en.GetValue()`
   - Replace `self._zh_size` → `self._sl_zh.GetValue()`
   - Replace `self._show_raw` → `self._chk_raw.GetValue()`
   - Replace `self._show_corrected` → `self._chk_corrected.GetValue()`
   - Replace `self._enable_denoise` → `self._chk_denoise.GetValue()`
   - Replace `self._context` → `self._ctx_entry.GetValue().strip()`

2. Update `__init__` size and call sequence:
   ```python
   self._build()
   self._update_preview()
   self.SetSize(self.FromDIP(wx.Size(500, 560)))
   self.Layout()
   ```
   (Remove the `Fit()` call — notebook dialogs should have a fixed size, not auto-fit.)

3. Remove `_open_advanced` method if still present.

4. Remove any remaining references to `_AdvancedDialog`.

**Launch and verify:**
- Open dialog → 3 tabs visible
- Change settings on all tabs → click 啟動字幕辨識 → service starts with correct settings
- Reopen settings via ⚙ button → values pre-filled correctly

**Commit:**
```bash
git add ui/dialog_wx.py
git commit -m "feat: wire _on_ok to new tab widgets, fix dialog sizing"
```

---

### Task 6: Final cleanup and push

**Steps:**

1. Run the app once more end-to-end: open dialog, change settings on all 3 tabs, start service, open settings again via ⚙ button, verify values persist.

2. Check for any dead code: search for `_AdvancedDialog`, `_open_advanced`, `adv_btn` — remove if found.

3. Commit and push:
   ```bash
   git add ui/dialog_wx.py
   git commit -m "refactor: remove dead code after settings dialog redesign"
   git push
   ```
