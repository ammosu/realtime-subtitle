"""DTLN ONNX 即時降噪模組。

基於 breizhn/DTLN，使用兩個 ONNX 模型做 streaming overlap-add 降噪。
- block_len  = 512 samples (32ms @ 16kHz)
- block_shift = 128 samples (8ms @ 16kHz)
- 維持 LSTM hidden state，逐 block 處理，支援任意長度輸入
"""
import numpy as np
import onnxruntime as ort


class DTLNDenoiser:
    BLOCK_LEN = 512
    BLOCK_SHIFT = 128

    def __init__(self, model_1_path: str, model_2_path: str):
        self._sess1 = ort.InferenceSession(model_1_path)
        self._sess2 = ort.InferenceSession(model_2_path)

        # 預分配輸入 dict（含 LSTM 初始狀態 = 全零）
        self._inp1 = {
            inp.name: np.zeros(
                [d if isinstance(d, int) else 1 for d in inp.shape], dtype=np.float32
            )
            for inp in self._sess1.get_inputs()
        }
        self._inp2 = {
            inp.name: np.zeros(
                [d if isinstance(d, int) else 1 for d in inp.shape], dtype=np.float32
            )
            for inp in self._sess2.get_inputs()
        }
        self._inp1_names = [inp.name for inp in self._sess1.get_inputs()]
        self._inp2_names = [inp.name for inp in self._sess2.get_inputs()]

        # overlap-add 緩衝
        self._in_buf = np.zeros(self.BLOCK_LEN, dtype=np.float32)
        self._out_buf = np.zeros(self.BLOCK_LEN, dtype=np.float32)
        # 跨呼叫的不足 block_shift 尾端音訊
        self._leftover = np.zeros(0, dtype=np.float32)

    def process(self, audio: np.ndarray) -> np.ndarray:
        """
        處理任意長度的 16kHz float32 音訊，回傳降噪後音訊。
        回傳長度 = (len(audio) // BLOCK_SHIFT) * BLOCK_SHIFT（尾端不足 128 sample 的部分保留至下次）。
        """
        data = np.concatenate([self._leftover, audio.astype(np.float32)])
        n_blocks = len(data) // self.BLOCK_SHIFT
        out_samples = n_blocks * self.BLOCK_SHIFT
        output = np.zeros(out_samples, dtype=np.float32)

        for i in range(n_blocks):
            new_samples = data[i * self.BLOCK_SHIFT:(i + 1) * self.BLOCK_SHIFT]

            # 滑動輸入緩衝
            self._in_buf[:-self.BLOCK_SHIFT] = self._in_buf[self.BLOCK_SHIFT:]
            self._in_buf[-self.BLOCK_SHIFT:] = new_samples

            # FFT → 幅度 + 相位
            in_fft = np.fft.rfft(self._in_buf)
            in_mag = np.abs(in_fft).reshape(1, 1, -1).astype(np.float32)
            in_phase = np.angle(in_fft)

            # Model 1：頻域遮罩
            self._inp1[self._inp1_names[0]] = in_mag
            out1 = self._sess1.run(None, self._inp1)
            self._inp1[self._inp1_names[1]] = out1[1]  # 更新 LSTM state

            # 重建時域估計
            est_complex = in_mag * out1[0] * np.exp(1j * in_phase)
            est_block = np.fft.irfft(est_complex).reshape(1, 1, -1).astype(np.float32)

            # Model 2：時域精化
            self._inp2[self._inp2_names[0]] = est_block
            out2 = self._sess2.run(None, self._inp2)
            self._inp2[self._inp2_names[1]] = out2[1]  # 更新 LSTM state

            # Overlap-add 輸出
            self._out_buf[:-self.BLOCK_SHIFT] = self._out_buf[self.BLOCK_SHIFT:]
            self._out_buf[-self.BLOCK_SHIFT:] = 0.0
            self._out_buf += np.squeeze(out2[0])
            output[i * self.BLOCK_SHIFT:(i + 1) * self.BLOCK_SHIFT] = self._out_buf[:self.BLOCK_SHIFT]

        self._leftover = data[out_samples:]
        return output
