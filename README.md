# Subtitle Client

即時字幕疊加工具，搭配 Qwen3-ASR 伺服器使用，支援自動翻譯（中英互譯）。

## 功能

- 即時語音辨識（透過遠端 Qwen3-ASR 伺服器）
- 自動翻譯（OpenAI GPT-4o mini）
- 透明浮動字幕視窗，可拖拉、可縮放（Linux：GTK3 真透明；Windows：tkinter）
- 支援系統播放音擷取（monitor）及麥克風（mic）兩種音源
- **圖形化啟動設定對話框**，免 CLI 參數即可設定（設定自動儲存）

---

## Windows 安裝步驟

### 1. 安裝 Python 3.10+

至 [python.org](https://www.python.org/downloads/) 下載安裝。
**重要**：安裝時勾選「Add Python to PATH」。

### 2. 安裝 Visual C++ Redistributable

`pyaudiowpatch` 需要 VC++ Runtime，從以下連結安裝：
[Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)

### 3. 安裝 Python 套件

```powershell
pip install sounddevice numpy scipy requests openai onnxruntime pyaudiowpatch opencc-python-reimplemented customtkinter
```

> `pyaudiowpatch` 是 Windows WASAPI Loopback 音訊擷取的核心套件，用於擷取系統播放音。

或直接使用 requirements.txt：

```powershell
pip install -r requirements.txt
pip install opencc-python-reimplemented
```

### 4. 準備必要檔案

確認以下檔案在同一目錄（含 `ui/` 子套件）：

```
subtitle_client.py
silero_vad_v6.onnx          ← VAD 模型，缺少則無法運作
NotoSansTC-SemiBold.ttf     ← 字幕字體，缺少時退回系統預設字體
ui/
  __init__.py
  overlay_tk.py
  overlay_gtk.py
  dialog_tk.py
  dialog_gtk.py
```

### 5. 確認 ASR Server 可連線

ASR 伺服器需在有 GPU 的 Linux 機器上執行，確認從 Windows 電腦可以連線：

```powershell
curl http://<SERVER_IP>:8000/
```

回傳 HTML 表示連線正常。

### 6. 設定 OpenAI API Key

啟動後在圖形設定對話框中填入即可，設定會自動儲存到 `%APPDATA%/../.config/realtime-subtitle/config.json`，下次不需重新填寫。

---

## 執行

### 方法 A：圖形設定對話框（推薦）

不帶任何參數啟動，程式會自動彈出設定視窗，填入後即可開始：

```powershell
python subtitle_client.py
```

設定（ASR 伺服器位址、音訊裝置、翻譯方向）會自動儲存，下次啟動時自動帶入。

### 方法 B：CLI 參數直接啟動

提供任一核心參數（`--asr-server`、`--monitor-device`、`--source`、`--direction`）即跳過對話框：

```powershell
# 查詢可用 WASAPI 裝置（用於 --monitor-device 指定）
python subtitle_client.py --list-devices

# 指定輸出裝置（用 --list-devices 查到的索引或名稱）
python subtitle_client.py --asr-server http://<SERVER_IP>:8000 --monitor-device 3

# 使用麥克風（不擷取系統播放音）
python subtitle_client.py --asr-server http://<SERVER_IP>:8000 --source mic

# 翻譯方向改為中→英
python subtitle_client.py --asr-server http://<SERVER_IP>:8000 --direction zh→en
```

---

## 操作說明

| 操作 | 功能 |
|------|------|
| 滑鼠移到視窗頂部 | 顯示工具列 |
| 拖拉頂部拖拉條 | 移動視窗位置 |
| 拖拉右下角三角形或四邊/四角 | 調整視窗大小 |
| 工具列「EN→ZH ⇄」按鈕 | 切換翻譯方向 |
| 工具列「🎤 MIC / 🔊 MON」按鈕 | 切換音源（麥克風/系統音） |
| 工具列「✕」或 `Esc` | 關閉 |
| `F9` | 切換翻譯方向 |

---

## 常見問題

| 問題 | 解法 |
|------|------|
| `pyaudiowpatch` 安裝失敗 | 確認已安裝 Visual C++ Redistributable |
| Monitor 模式沒有聲音 | 用 `--list-devices` 確認裝置索引，以 `--monitor-device <索引>` 指定 |
| 找不到 loopback 裝置 | 確認 Windows 音訊驅動正常，重新啟動音訊服務 |
| 字幕視窗看不到 | 檢查是否被其他視窗遮住；視窗為透明背景，需有文字才可見 |
| ASR 連線失敗 | 確認 SERVER_IP 正確，防火牆已開放 TCP 8000 |
| `opencc` 找不到 | `pip install opencc-python-reimplemented` |

---

## Windows 打包成安裝檔（.exe）

使用 PyInstaller + Inno Setup 6 打包。

### 前置需求

1. 虛擬環境中已安裝 PyInstaller：
   ```powershell
   .venv\Scripts\activate
   pip install pyinstaller
   ```

2. 安裝 [Inno Setup 6](https://jrsoftware.org/isdl.php)（選擇 User 安裝，預設路徑為 `%LOCALAPPDATA%\Programs\Inno Setup 6`）

### 打包步驟

**Step 1 — PyInstaller**

```powershell
.venv\Scripts\activate
.venv\Scripts\pyinstaller subtitle_client.spec -y
```

輸出到 `dist\RealtimeSubtitle\`

**Step 2 — Inno Setup**

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

輸出到 `installer_output\RealtimeSubtitle-Setup.exe`

---

## Linux 安裝步驟

> **注意**：`requirements.txt` 中的 `pyaudiowpatch` 是 Windows 專用套件，在 Linux 上無法安裝。請勿直接執行 `pip install -r requirements.txt`，改用以下方式建置環境。

### 1. 建立虛擬環境（建議）

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. 安裝 Linux 相容套件

```bash
pip install sounddevice numpy scipy requests openai onnxruntime opencc-python-reimplemented
```

### 3. 確認 Monitor 音源

```bash
pactl list sources short | grep monitor
```

執行：

### 方法 A：圖形設定對話框（推薦）

```bash
DISPLAY=:1 OPENAI_API_KEY=sk-... .venv/bin/python subtitle_client.py
```

### 方法 B：CLI 直接啟動

```bash
# 查詢可用 monitor source
.venv/bin/python subtitle_client.py --list-devices

.venv/bin/python subtitle_client.py --asr-server http://<SERVER_IP>:8000 \
  --monitor-device alsa_output.pci-0000_00_1f.3.iec958-stereo.monitor
```

> `DISPLAY` 視環境而定（本機為 `:1`）。
