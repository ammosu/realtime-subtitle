# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Windows Packaging (.exe Installer)

Requires PyInstaller (in venv) and Inno Setup 6 installed at `%LOCALAPPDATA%\Programs\Inno Setup 6`.

```powershell
# Step 1: PyInstaller (outputs to dist\RealtimeSubtitle\)
.venv\Scripts\pyinstaller subtitle_client.spec -y

# Step 2: Inno Setup (outputs to installer_output\RealtimeSubtitle-Setup.exe)
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

Run both from the repo root with the venv activated. The `-y` flag overwrites existing `dist\RealtimeSubtitle` without prompting.

## Environment Setup (Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install sounddevice numpy scipy requests openai onnxruntime opencc-python-reimplemented

# Allow venv to access system GTK3 bindings (already created):
# .venv/lib/python3.12/site-packages/system-gi.pth → /usr/lib/python3/dist-packages
```

`pyaudiowpatch` is **Windows-only** — never install it on Linux.

## Running the Service (Linux)

```bash
# === Recommended: GUI setup dialog (no CLI args needed) ===
DISPLAY=:1 OPENAI_API_KEY=sk-... .venv/bin/python subtitle_client.py

# Background with log (GUI dialog still appears)
DISPLAY=:1 OPENAI_API_KEY=sk-... .venv/bin/python subtitle_client.py \
  >> /tmp/subtitle.log 2>&1 &

# === CLI mode: skip dialog by providing any core arg ===
# List available monitor sources first
DISPLAY=:1 .venv/bin/python subtitle_client.py --list-devices

# Start directly
DISPLAY=:1 OPENAI_API_KEY=sk-... .venv/bin/python subtitle_client.py \
  --asr-server http://localhost:8000 \
  --source monitor \
  --monitor-device alsa_output.usb-Synaptics_HUAWEI_USB-C_HEADSET_0296B2981911266789828239907F1-00.analog-stereo.monitor \
  >> /tmp/subtitle.log 2>&1 &
```

`DISPLAY` must be `:1` on this machine (not `:0`).

### Setup dialog skip logic

The GUI setup dialog is shown on startup **unless** any of these CLI flags are present:
`--asr-server`, `--monitor-device`, `--source`, `--direction`

Settings chosen in the dialog are persisted to `~/.config/realtime-subtitle/config.json` and pre-filled on next launch.

## Architecture

The application is split into focused modules under the repo root and a `ui/` sub-package:

| Module | Responsibility |
|--------|----------------|
| `subtitle_client.py` | Entry point: `main()`, `show_setup_dialog()`, CLI argument parsing |
| `constants.py` | Shared constants (`TARGET_SR`, `CHUNK_SAMPLES`) and logging configuration |
| `asr.py` | `ASRClient` (HTTP POST to ASR server) and `TranslationDebouncer` |
| `audio.py` | `MonitorAudioSource`, `MicrophoneAudioSource`, device enumeration |
| `worker.py` | Worker subprocess entry point (`_worker_main`): VAD + ASR + translation pipeline |
| `config.py` | `load_config()` / `save_config()` and audio-device listing helpers |
| `ui/__init__.py` | GTK3 availability detection (`_GTK3_AVAILABLE`) |
| `ui/overlay_gtk.py` | `SubtitleOverlayGTK` (GTK3 + Cairo, Linux) |
| `ui/overlay_tk.py` | `SubtitleOverlay` (tkinter, Windows / fallback) |
| `ui/dialog_gtk.py` | `SetupDialogGTK` (GTK3 startup config dialog) |
| `ui/dialog_tk.py` | `SetupDialogTk` (tkinter startup config dialog) |

### Process model

The program uses **two OS processes**:

1. **Main process** — creates the GUI window (GTK3 on Linux, tkinter on Windows), then polls `text_q` every 50ms via the UI event loop to update subtitles.
2. **Worker process** (`_worker_main`) — spawned via `multiprocessing.Process(spawn)` *after* the GUI is initialized. Runs the full audio pipeline with no X11/GTK. Uses `text_q` (out) and `cmd_q` (in) for IPC.

The `spawn` start method is mandatory — `fork` causes XCB sequence number conflicts.

### Worker pipeline (inside `_worker_main`)

```
AudioSource  →  on_chunk()  →  _vad_q
                                  ↓
                             vad_loop (thread)   [Silero VAD v6 ONNX]
                             accumulates speech, flushes on silence (0.5s)
                             or max buffer (5s)  →  _speech_q
                                  ↓
                             asr_loop (thread)   [HTTP POST /api/transcribe]
                             sends float32 PCM to ASR server  →  TranslationDebouncer
                                  ↓
                             GPT-4o-mini translation  →  text_q.put({"original", "translated"})
```

### Key timing constants in `_worker_main`

- `VAD_CHUNK = 576` — 36ms frames fed to Silero
- `RT_SILENCE_CHUNKS = 14` — 0.5s silence → flush
- `RT_MAX_BUFFER_CHUNKS = 138` — 5s max → force flush
- `DEBOUNCE_SEC = 0.4` — translation debounce in `TranslationDebouncer`

### GUI classes

Both overlay classes expose the same public interface: `set_text()`, `update_direction_label()`, `update_source_label()`, `run()`.

- **`SubtitleOverlay`** — tkinter, Windows/fallback Linux. Uses `-transparentcolor` on Windows; `-type splash` + `-alpha` on Linux (semi-transparent whole window).
- **`SubtitleOverlayGTK`** — GTK3 + Cairo, Linux only (used when `_GTK3_AVAILABLE=True`). `Gtk.WindowType.POPUP` with RGBA visual + `OPERATOR_CLEAR` → fully transparent background, only text and drag bar are visible. Toolbar drawn in Cairo, button hit-testing done manually via `_btn_rects`.

### Setup dialog classes

Shown on startup when no CLI core args are provided. Both expose `.run() → dict | None`.

- **`SetupDialogGTK`** — GTK3, Linux (preferred when `_GTK3_AVAILABLE=True`).
- **`SetupDialogTk`** — tkinter, Windows/fallback Linux.
- **`show_setup_dialog(config)`** — dispatcher that picks the right class based on platform.

### Config persistence

`load_config()` / `save_config()` read and write `~/.config/realtime-subtitle/config.json`. Stores: `asr_server`, `monitor_device`, `direction`.

### Audio sources

- **`MonitorAudioSource`** — system playback capture. Linux: sets `PULSE_SOURCE` env var, opens ALSA `pulse` device via sounddevice. Windows: WASAPI Loopback via `pyaudiowpatch`.
- **`MicrophoneAudioSource`** — standard mic via sounddevice.

Both resample to 16kHz float32 mono and emit 0.5s chunks (8000 samples) via callback.

### VAD model

`silero_vad_v6.onnx` must be in the same directory as `subtitle_client.py`. The worker loads it at startup; without it the process exits immediately.

### ASR server

HTTP POST to `<base_url>/api/transcribe` with raw float32 PCM bytes (`Content-Type: application/octet-stream`). Returns `{"language": str, "text": str}`. Timeout is 45s. The server must be a Qwen3-ASR instance.

### Simplified Chinese → Traditional Chinese

`opencc` (`s2twp` config) converts ASR output from Simplified to Taiwan Traditional Chinese before displaying/translating. Applied in `_to_traditional()` inside the worker.

## Key files

| File | Purpose |
|------|---------|
| `subtitle_client.py` | 主程式進入點（`main()` + `show_setup_dialog()`，~280 行） |
| `constants.py` | 共用常數：`TARGET_SR`, `CHUNK_SAMPLES`，logging 設定 |
| `asr.py` | ASR HTTP client (`ASRClient`) 與翻譯 debouncer (`TranslationDebouncer`) |
| `audio.py` | 音訊來源：`MonitorAudioSource`, `MicrophoneAudioSource` |
| `worker.py` | Worker subprocess：VAD + ASR + 翻譯 pipeline |
| `config.py` | 設定檔讀寫與音訊裝置列舉 |
| `ui/__init__.py` | GTK3 可用性偵測 (`_GTK3_AVAILABLE`) |
| `ui/overlay_gtk.py` | `SubtitleOverlayGTK`（GTK3 + Cairo，Linux）|
| `ui/overlay_tk.py` | `SubtitleOverlay`（tkinter，Windows / fallback）|
| `ui/dialog_gtk.py` | `SetupDialogGTK`（GTK3 啟動設定對話框）|
| `ui/dialog_tk.py` | `SetupDialogTk`（tkinter 啟動設定對話框）|
| `silero_vad_v6.onnx` | Silero VAD v6 model（runtime 必須存在）|
| `.venv/lib/.../system-gi.pth` | 讓 venv 存取系統 PyGObject/GTK3 |
| `~/.config/realtime-subtitle/config.json` | 使用者設定（自動建立）|
