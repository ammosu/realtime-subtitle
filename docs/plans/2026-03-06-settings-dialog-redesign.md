# Settings Dialog Redesign

Date: 2026-03-06

## Problem

The current `_SetupWxDlg` is a single long vertical list with no visual grouping.
All options — ASR backend, audio source, translation direction, font sizes, noise
reduction — are stacked without clear hierarchy. A secondary "_AdvancedDialog" popup
adds a second level of navigation that users find unintuitive.

## Design Decision

Replace the flat layout with a `wx.Notebook` (3 tabs). The advanced dialog is
eliminated; its contents are redistributed into the appropriate tabs.

## Structure

```
┌─────────────────────────────────┐
│  ⚡ Real-time Subtitle          │  header strip
├──────┬──────┬────────────────────┤
│ 辨識 │ 翻譯 │ 顯示               │  wx.Notebook
├──────┴──────┴────────────────────┤
│  (tab content)                   │
├──────────────────────────────────┤
│  warning label                   │
│        [取消]  [啟動字幕辨識]    │  global buttons
└──────────────────────────────────┘
```

## Tab Contents

### Tab 1: 辨識

- 運算模式: "本地模型" / "外部伺服器 (QwenASR)" radio buttons
- Local panel (shown when local selected):
  - Detected devices (CPU + GPU list)
  - Inference device radio (CPU / GPU)
  - Model file status + download button + progress label
  - chatllm path status
- Server panel (shown when remote selected):
  - QwenASR server URL entry
- Separator
- 音訊來源: "系統音訊" / "麥克風" radio buttons
- Device selector (monitor or mic dropdown, context-sensitive)
- 啟用 DTLN 降噪 checkbox (with hint if model files absent)

### Tab 2: 翻譯

- OpenAI API Key entry (password field)
- 翻譯方向: source language combo ⇄ swap button ⇄ target language combo
- 辨識提示詞 entry (optional context hint for ASR)

### Tab 3: 顯示

- 辨識字體大小 slider (10–30) + numeric label
- 翻譯字體大小 slider (14–40) + numeric label
- Font preview panel (gray background, EN + ZH sample text, updates live)
- 顯示校正後 ASR 原文 checkbox
- 顯示原始 ASR 辨識 checkbox

## Removed

- `_AdvancedDialog` class (entire secondary popup)
- "⚙ 進階設定" button in main dialog

## Implementation Notes

- Dark-theme notebook: set `wx.Notebook` background to `_BG`; tab labels inherit
  dialog font. On Windows the tab strip chrome is native but acceptable.
- Tab panels are plain `wx.Panel` with `_BG` background, using existing `_lbl`,
  `_make_entry`, `_DarkCombo`, `_btn` helpers.
- State variables (`_en_size`, `_zh_size`, `_context`, `_show_raw`,
  `_show_corrected`, `_enable_denoise`) remain on `_SetupWxDlg`; sliders and
  checkboxes in Tab 3 / Tab 2 read/write them directly.
- Font preview live-update: bind `wx.EVT_SLIDER` on both sliders to call
  `_update_preview()` (same logic as current `_AdvancedDialog._update_preview`).
- All other logic (mode toggle, source toggle, model download, swap button,
  `_on_ok`) is unchanged.
