# correction.py
"""
LLMCorrector — 使用 OpenAI GPT-4o-mini 對即時 ASR 輸出做後處理：
  1. 去除 overlap 重複（0.5s 音頻重疊造成的句首重複）
  2. 修正幻覺/錯字
  3. 以標點為界分割輸出：完整句子立即顯示，不完整片段扣留至下段補完
"""
from __future__ import annotations


class LLMCorrector:
    """
    即時 ASR 後處理器。

    使用方式：
        corrector = LLMCorrector(api_key="sk-...")
        text = corrector.correct(raw_asr_text, prev_raw="...")
        # 停止時強制輸出剩餘片段
        pending = corrector.flush_pending()
    """

    def __init__(self, api_key: str):
        from openai import OpenAI
        self._client          = OpenAI(api_key=api_key)
        self._history:   list[str] = []   # 最近已校正段落（供 LLM 參考上下文）
        self._pending:   str       = ""   # 上段末尾不完整的句子片段

    def correct(self, raw: str, prev_raw: str = "") -> str:
        """
        校正 ASR 原始文字。

        raw:      本段原始 ASR 輸出
        prev_raw: 上一段完整原始 ASR 輸出（用於比對 overlap 重複邊界）
        回傳：完整句子部分（以標點結尾），不完整片段暫存於 _pending
        """
        if not raw.strip():
            return ""

        prev_corrected = "".join(self._history[-2:])
        # 將上段扣住的不完整片段接回本段一起校正
        combined_raw = (self._pending + raw) if self._pending else raw

        prompt = (
            "你是即時語音辨識後處理器。音訊每 8 秒強制切段，相鄰段落間有約 0.5 秒重疊音訊，"
            "因此新段落的句首通常會重複上一段句尾的部分文字。\n\n"
            f"【上一段完整原始辨識（ASR raw，含重複區參考）】\n{prev_raw}\n\n"
            f"【上一段已校正並顯示的文字】\n{prev_corrected}\n\n"
            f"【本段原始辨識（句首含 overlap 重複，末尾可能有上段保留的不完整片段）】\n{combined_raw}\n\n"
            "請依序執行：\n"
            "1. 比對「上一段原始辨識」的句尾與「本段原始辨識」的句首，"
            "找出因 overlap 音訊造成的重複文字並刪除，使本段能自然銜接上一段\n"
            "2. 對照「上一段已校正文字」結尾，確認銜接語意正確，修正明顯錯字或幻覺\n"
            "3. 以句號、問號、驚嘆號、省略號為界，將校正後文字拆成兩部分，用 ||| 分隔：\n"
            "   - 分隔符前：以標點結尾的完整句子（可立即顯示）\n"
            "   - 分隔符後：末尾尚未完整的句子片段（暫扣留，等待下段補完；若無則留空）\n"
            "4. 只輸出「完整部分|||不完整片段」，不附任何說明"
        )

        try:
            resp = self._client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=400,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            result = resp.choices[0].message.content.strip()
            if "|||" in result:
                complete, self._pending = result.split("|||", 1)
                corrected = complete.strip()
            else:
                corrected      = result.strip()
                self._pending  = ""
        except Exception:
            corrected     = combined_raw
            self._pending = ""

        if corrected:
            self._history.append(corrected)
            if len(self._history) > 5:
                self._history.pop(0)

        return corrected

    def flush_pending(self) -> str:
        """停止錄音時，強制輸出暫存的不完整句尾片段。"""
        pending       = self._pending.strip()
        self._pending = ""
        return pending

    def reset(self) -> None:
        """重置狀態（換語言或重啟時使用）。"""
        self._history.clear()
        self._pending = ""
