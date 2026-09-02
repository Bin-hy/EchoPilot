"""M2 内置 VAD：silero-vad（本地，CPU 实时）。

为 question_detector 提供静音信号：speaking 状态与 silence_ms 累计。
输入：16kHz mono int16 PCM，任意长度帧（内部缓冲到 512 样本判定窗）。
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
WINDOW = 512  # silero 在 16kHz 下的判定窗（32ms）


class ChannelVAD:
    def __init__(self, threshold: float = 0.5):
        from silero_vad import load_silero_vad
        self.model = load_silero_vad(onnx=True)
        self.threshold = threshold
        self.speaking = False
        self.silence_ms = 0
        self._buf = np.zeros(0, dtype=np.float32)

    def feed(self, pcm_int16: bytes) -> None:
        """喂入 PCM 帧，更新 speaking / silence_ms。"""
        chunk = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, chunk])
        while len(self._buf) >= WINDOW:
            window, self._buf = self._buf[:WINDOW], self._buf[WINDOW:]
            import torch
            prob = self.model(
                torch.from_numpy(window), SAMPLE_RATE).item()
            window_ms = int(WINDOW / SAMPLE_RATE * 1000)
            if prob >= self.threshold:
                self.speaking = True
                self.silence_ms = 0
            else:
                self.silence_ms += window_ms
                if self.silence_ms >= 300:  # 300ms 无语音才判为停止说话
                    self.speaking = False

    def state(self) -> dict:
        return {"speaking": self.speaking, "silence_ms": self.silence_ms}

    def reset(self) -> None:
        self.speaking = False
        self.silence_ms = 0
        self._buf = np.zeros(0, dtype=np.float32)
