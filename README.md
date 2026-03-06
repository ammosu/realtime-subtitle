# Real-time Subtitle

即時語音辨識字幕疊加工具。支援本地 AI 模型與遠端 ASR 伺服器兩種推理模式，自動翻譯（OpenAI GPT-4o mini），透明浮動字幕顯示於畫面任意位置。

## 功能

- **雙推理模式**：本地模型（Qwen3-ASR + chatllm，無需伺服器）或遠端 QwenASR 伺服器
- **自動翻譯**：OpenAI GPT-4o mini，支援多語言方向（設定頁可調整）
- **即時降噪**：DTLN ONNX 模型，客戶端降噪後再送辨識
- **透明浮動字幕**：Windows tkinter（-transparentcolor 穿透）；Linux GTK3 + Cairo 真透明
- **字幕顯示模式**：原文 + 翻譯 / 僅原文 / 僅翻譯，可即時切換
- **字幕背景透明度**：可調整 20–100%
- **歷史捲動**：滑鼠滾輪瀏覽最近 200 筆字幕記錄
- **圖形設定對話框**：三分頁（辨識 / 翻譯 / 顯示），設定自動儲存

---

## Windows 安裝

### 1. 安裝 Python 3.10+

至 [python.org](https://www.python.org/downloads/) 下載。安裝時勾選「Add Python to PATH」。

### 2. 安裝 Visual C++ Redistributable

```powershell
# 下載並安裝
https://aka.ms/vs/17/release/vc_redist.x64.exe
```

### 3. 建立虛擬環境並安裝套件

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install sounddevice numpy scipy requests openai onnxruntime pyaudiowpatch opencc-python-reimplemented wxPython
```

### 4. 確認必要檔案

```
subtitle_client.py
silero_vad_v6.onnx          ← Silero VAD 模型（必須）
NotoSansTC-SemiBold.ttf     ← 字幕字體
ui/
  __init__.py
  overlay_tk.py
  dialog_wx.py
  dialog_tk.py
  overlay_gtk.py
  dialog_gtk.py
```

### 5. 本地模型（選用）

如使用本地推理模式，需另外準備：

| 檔案 | 說明 | 大小 |
|------|------|------|
| `qwen3-asr-1.7b.bin` | Qwen3-ASR GGUF 量化模型 | ~2.3 GB |
| `chatllm/chatllm.exe` | chatllm 推理執行環境 | ~50 MB |
| `dtln_model_1.onnx`<br>`dtln_model_2.onnx` | DTLN 降噪模型（選用） | ~4 MB |

模型可在設定對話框「辨識」頁籤點「下載」自動取得，或手動放置於程式根目錄。

---

## 執行

```powershell
# 圖形設定對話框（推薦）
python subtitle_client.py

# CLI 直接啟動（跳過對話框）
python subtitle_client.py --asr-server http://<SERVER_IP>:8000

# 查詢可用音訊裝置
python subtitle_client.py --list-devices
```

提供任一核心參數（`--asr-server`、`--monitor-device`、`--source`、`--direction`）即跳過設定對話框。

---

## 設定對話框

啟動後自動彈出，設定分為三個頁籤：

### 辨識頁籤

| 設定 | 說明 |
|------|------|
| 運算模式 | 本地模型 / 外部伺服器（QwenASR） |
| 本地模型設定 | 偵測裝置、CPU/GPU 選擇、模型下載 |
| 伺服器 URL | 遠端 QwenASR 伺服器位址 |
| 音訊來源 | 系統音訊（Monitor）/ 麥克風 |
| DTLN 降噪 | 啟用/停用客戶端降噪 |

### 翻譯頁籤

| 設定 | 說明 |
|------|------|
| OpenAI API Key | 翻譯用，留空則停用翻譯 |
| 翻譯方向 | 來源語言 ⇄ 目標語言 |
| 辨識提示詞 | 提供專有名詞提升辨識準確度 |

### 顯示頁籤

| 設定 | 說明 |
|------|------|
| 辨識字體大小 | 10–30pt，即時預覽 |
| 翻譯字體大小 | 14–40pt，即時預覽 |
| 字幕背景透明度 | 20–100% |
| 字幕顯示 | 原文+翻譯 / 僅原文 / 僅翻譯 |

---

## 字幕視窗操作

| 操作 | 功能 |
|------|------|
| 滑鼠移到視窗頂部 | 顯示工具列 |
| 拖拉頂部拖拉條 | 移動視窗位置 |
| 拖拉四邊 / 四角 | 調整視窗大小 |
| 滑鼠滾輪 | 捲動歷史字幕（最多 200 筆） |
| 工具列 `EN→ZH ⇄` | 切換翻譯方向 |
| 工具列 `Monitor / Mic` | 切換音源 |
| 工具列 `⏸ 暫停` | 暫停 / 繼續更新字幕 |
| 工具列 `⚙` | 重新開啟設定對話框（不需重啟） |
| 工具列 `✕` 或 `Esc` | 關閉 |
| `F9` | 快速切換翻譯方向 |

---

## 常見問題

| 問題 | 解法 |
|------|------|
| `pyaudiowpatch` 安裝失敗 | 確認已安裝 Visual C++ Redistributable |
| Monitor 模式沒有聲音 | 用 `--list-devices` 確認裝置，以 `--monitor-device` 指定 |
| 字幕視窗看不到 | 視窗為透明背景，需有辨識文字才可見；調高透明度設定 |
| ASR 連線失敗（遠端模式） | 確認 SERVER_IP 正確，防火牆已開放 TCP 8000 |
| 本地模型找不到 chatllm | 將 `chatllm.exe` 放至 `chatllm/` 子目錄，或在設定中確認路徑 |
| `opencc` 找不到 | `pip install opencc-python-reimplemented` |
| DTLN 降噪無效 | 確認 `dtln_model_1.onnx` 和 `dtln_model_2.onnx` 存在於程式根目錄 |

---

## Windows 打包成安裝檔 (.exe)

需要 PyInstaller（在 venv 中）與 Inno Setup 6。

```powershell
# Step 1：PyInstaller（輸出到 dist\RealtimeSubtitle\）
.venv\Scripts\pyinstaller subtitle_client.spec -y

# Step 2：Inno Setup（輸出到 installer_output\RealtimeSubtitle-Setup.exe）
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

---

## Linux 安裝

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install sounddevice numpy scipy requests openai onnxruntime opencc-python-reimplemented

# 讓 venv 存取系統 GTK3 bindings
echo /usr/lib/python3/dist-packages > .venv/lib/python3.*/site-packages/system-gi.pth
```

> `pyaudiowpatch` 為 Windows 專用，Linux 請勿安裝。

```bash
# 啟動（GTK3 介面）
DISPLAY=:1 OPENAI_API_KEY=sk-... .venv/bin/python subtitle_client.py
```

---

## 架構

```
subtitle_client.py   主程式、對話框排程、overlay 建立、worker 管理
constants.py         共用常數（TARGET_SR、CHUNK_SAMPLES）、logging 設定
asr.py               ASRClient（HTTP POST）、TranslationDebouncer
audio.py             MonitorAudioSource、MicrophoneAudioSource
worker.py            Worker subprocess：VAD + ASR + 翻譯 pipeline
config.py            load_config / save_config、音訊裝置列舉
denoise.py           DTLNDenoiser（ONNX 串流降噪）
ui/overlay_tk.py     SubtitleOverlay（tkinter，Windows / fallback）
ui/overlay_gtk.py    SubtitleOverlayGTK（GTK3 + Cairo，Linux）
ui/dialog_wx.py      SetupDialogWx（wxPython，Windows 主要使用）
ui/dialog_tk.py      SetupDialogTk（plain-tkinter fallback）
ui/dialog_gtk.py     SetupDialogGTK（GTK3，Linux）
```

Worker pipeline：`AudioSource → DTLN降噪 → Silero VAD → ASR → 翻譯 → text_q`
