# Subtitle UX: Raw→Corrected Transition + 3-Line History

## Goal
Improve subtitle overlay UX: raw ASR text appears first (dimmed), replaced by corrected text when LLM correction arrives; show 3 lines (current + 2 history) with mouse-wheel scroll for older history.

## Architecture

### State changes in `ui/overlay_tk.py`
- `_history: deque(maxlen=200)` — stores finalized entries as `{"original": str, "translated": str}`
- `_scroll_offset: int` — 0 = latest view; wheel-up increments, wheel-down decrements to 0
- `_current_raw: str` — pending raw text (dim display)
- `_current_original / _current_translated: str` — cleared after finalize

### API changes
Replace `set_text(raw, original, translated)` with two methods:
- `update_raw(raw: str)` — sets current slot to raw (dim), resets scroll to 0
- `finalize(original: str, translated: str)` — pushes to history, clears current, redraws

### Rendering (`_redraw_text`)
Three visible slots top→bottom:
1. `history[-(2+offset)]` — normal style
2. `history[-(1+offset)]` — normal style
3. Current slot — raw (dim `#909090`, no bg pill) OR blank when offset > 0

Raw dim style: gray text color `#909090`, no background pill rectangle.
Normal style: existing white/light-gray text + `SUBTITLE_BG` pill (unchanged).

### subtitle_client.py changes
- `"raw" in msg` → call `overlay.update_raw(raw)`
- `"original"/"translated" in msg` → call `overlay.finalize(original, translated)`
- Remove old `set_text()` call sites; keep `set_text(raw="", original="", translated="")` for reset only

### Mouse wheel
Bind `<MouseWheel>` (Windows) and `<Button-4/5>` (Linux) on canvas → adjust `_scroll_offset`, redraw.

## Tech Stack
Python 3.12, tkinter, Canvas-based rendering (existing)
