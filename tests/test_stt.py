"""Speech-to-text runs on the CPU unless PTP_WHISPER_DEVICE says otherwise. "auto" would pick an
NVIDIA GPU whenever one exists, and without cuBLAS/cuDNN installed that can crash the whole app."""
import os
import sys
import types
import unittest
from unittest import mock

from ptp import stt


class FakeWhisperModel:
    made = []

    def __init__(self, size, device, compute_type):
        FakeWhisperModel.made.append((size, device, compute_type))

    def transcribe(self, path, language=None, vad_filter=False):
        return [types.SimpleNamespace(text=" Balik kayo bukas. ")], None


class Device(unittest.TestCase):
    def setUp(self):
        FakeWhisperModel.made.clear()
        stt._model_cache.clear()
        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = FakeWhisperModel
        patches = [mock.patch.dict(sys.modules, {"faster_whisper": fake}),
                   mock.patch.object(stt, "backend", return_value="faster-whisper")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(stt._model_cache.clear)

    def test_cpu_by_default(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("PTP_WHISPER_DEVICE", None)
            os.environ.pop("PTP_WHISPER_MODEL", None)
            self.assertEqual(stt.transcribe("visit.webm"), "Balik kayo bukas.")
        self.assertEqual(FakeWhisperModel.made, [("small", "cpu", "int8")])

    def test_gpu_only_when_asked(self):
        with mock.patch.dict(os.environ, {"PTP_WHISPER_DEVICE": "cuda", "PTP_WHISPER_MODEL": "base"}):
            stt.transcribe("visit.webm")
            stt.transcribe("visit.webm")  # the loaded model is reused
        self.assertEqual(FakeWhisperModel.made, [("base", "cuda", "int8")])


if __name__ == "__main__":
    unittest.main()
