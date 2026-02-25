# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
# List available monitor sources first
DISPLAY=:1 .venv/bin/python subtitle_client.py --list-devices

# Start (monitor = system audio capture via PulseAudio)
DISPLAY=:1 OPENAI_API_KEY=sk-... .venv/bin/python subtitle_client.py \
  --asr-server http://localhost:8000 \
  --source monitor \
  --monitor-device <pactl-source-name>

# Background with log
DISPLAY=:1 OPENAI_API_KEY=sk-... .venv/bin/python subtitle_client.py \
  --asr-server http://localhost:8000 --source monitor \
  --monitor-device alsa_output.usb-Huawei_HUAWEI_USB-C_HEADSET-00.analog-stereo.monitor \
  >> /tmp/subtitle.log 2>&1 &
```

`DISPLAY` must be `:1` on this machine (not `:0`).

## Architecture

Everything lives in a single file: `subtitle_client.py`.

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

Both classes expose the same public interface: `set_text()`, `update_direction_label()`, `update_source_label()`, `run()`.

- **`SubtitleOverlay`** — tkinter, Windows/fallback Linux. Uses `-transparentcolor` on Windows; `-type splash` + `-alpha` on Linux (semi-transparent whole window).
- **`SubtitleOverlayGTK`** — GTK3 + Cairo, Linux only (used when `_GTK3_AVAILABLE=True`). `Gtk.WindowType.POPUP` with RGBA visual + `OPERATOR_CLEAR` → fully transparent background, only text and drag bar are visible. Toolbar drawn in Cairo, button hit-testing done manually via `_btn_rects`.

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
| `subtitle_client.py` | Entire application (~1500 lines) |
| `silero_vad_v6.onnx` | Silero VAD v6 model (required at runtime) |
| `.venv/lib/.../system-gi.pth` | Lets venv access system PyGObject/GTK3 |
