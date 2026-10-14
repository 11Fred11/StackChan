"""Opus framing and voice-activity detection for the device audio stream."""

import warnings

import numpy as np

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    import opuslib

import webrtcvad

UPLINK_RATE = 16000  # device microphone stream
DOWNLINK_RATE = 24000  # what we send back for playback
FRAME_MS = 60


class OpusIn:
    """Decodes the device's 16 kHz mono Opus frames to int16 PCM."""

    def __init__(self) -> None:
        self._dec = opuslib.Decoder(UPLINK_RATE, 1)
        self._frame_samples = UPLINK_RATE * FRAME_MS // 1000

    def decode(self, packet: bytes) -> bytes:
        return self._dec.decode(packet, self._frame_samples)


class OpusOut:
    """Encodes float PCM at 24 kHz into 60 ms Opus frames for the device."""

    def __init__(self) -> None:
        self._enc = opuslib.Encoder(DOWNLINK_RATE, 1, opuslib.APPLICATION_VOIP)
        self._frame_samples = DOWNLINK_RATE * FRAME_MS // 1000

    def encode(self, pcm: np.ndarray, sample_rate: int) -> list[bytes]:
        if sample_rate != DOWNLINK_RATE:
            pcm = resample(pcm, sample_rate, DOWNLINK_RATE)
        pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767).astype(np.int16)
        pad = (-len(pcm16)) % self._frame_samples
        pcm16 = np.concatenate([pcm16, np.zeros(pad, dtype=np.int16)])
        n = self._frame_samples
        return [self._enc.encode(pcm16[i : i + n].tobytes(), n) for i in range(0, len(pcm16), n)]


def resample(pcm: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return pcm
    positions = np.arange(0, len(pcm), src / dst)
    return np.interp(positions, np.arange(len(pcm)), pcm).astype(np.float32)


class Endpointer:
    """Tracks speech in the uplink and decides when the user has finished a turn."""

    CHUNK_MS = 30

    def __init__(self, end_silence_ms: int) -> None:
        self._vad = webrtcvad.Vad(2)
        self._chunk_bytes = UPLINK_RATE * self.CHUNK_MS // 1000 * 2
        self._end_chunks = end_silence_ms // self.CHUNK_MS
        self.reset()

    def reset(self) -> None:
        self._pending = b""
        self.audio = bytearray()
        self.heard_speech = False
        self._speech_chunks = 0
        self._silent_chunks = 0

    def feed(self, pcm: bytes) -> bool:
        """Adds PCM; returns True when a finished utterance is in self.audio."""
        self._pending += pcm
        done = False
        while len(self._pending) >= self._chunk_bytes:
            chunk, self._pending = self._pending[: self._chunk_bytes], self._pending[self._chunk_bytes :]
            speech = self._vad.is_speech(chunk, UPLINK_RATE)
            if speech:
                self._speech_chunks += 1
                self._silent_chunks = 0
                # Require ~150 ms of speech so clicks and servo noise don't count.
                if self._speech_chunks >= 5:
                    self.heard_speech = True
            else:
                self._silent_chunks += 1
            if self.heard_speech or speech:
                self.audio += chunk
            if self.heard_speech and self._silent_chunks >= self._end_chunks:
                done = True
        return done

    def utterance(self) -> np.ndarray:
        return np.frombuffer(bytes(self.audio), dtype=np.int16).astype(np.float32) / 32768.0
