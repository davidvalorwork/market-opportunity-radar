"""Local speech-to-text for Telegram voice notes (faster-whisper, already-cached models only).

GPU (CUDA) with large-v3-turbo when it loads, else CPU int8 with 'small'. HF_HUB_OFFLINE
forbids model downloads: a missing model is an error, never an implicit download.
"""
import os
from pathlib import Path

os.environ.setdefault('HF_HUB_OFFLINE', '1')
# ponytail: reuse the cuBLAS/cuDNN DLLs shipped with the system Python's torch; set RADAR_CUDA_DLLS
# (or pip install nvidia-cublas-cu12 nvidia-cudnn-cu12) if that install moves.
CUDA_DLLS = os.environ.get('RADAR_CUDA_DLLS', str(Path.home() / 'scoop/apps/python311/current/Lib/site-packages/torch/lib'))
if Path(CUDA_DLLS).is_dir():
    os.environ['PATH'] = CUDA_DLLS + os.pathsep + os.environ.get('PATH', '')
    os.add_dll_directory(CUDA_DLLS)

_model = None
CANDIDATES = (('large-v3-turbo', 'cuda', 'float16'), ('small', 'cpu', 'int8'))


def model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        for name, device, compute in CANDIDATES:
            try:
                candidate = WhisperModel(name, device=device, compute_type=compute)
                import numpy
                list(candidate.transcribe(numpy.zeros(16000, dtype=numpy.float32), language='es')[0])  # forces a real encode
                _model = candidate
                break
            except Exception:  # CUDA DLLs/driver missing or model not cached: try the next one
                continue
        else:
            raise RuntimeError('transcriber_unavailable')
    return _model


def transcribe(path, language='es'):
    """-> text. Spanish by default; Whisper still copes with mixed-language notes."""
    segments, _ = model().transcribe(str(path), language=language, vad_filter=True, beam_size=5)
    return ' '.join(segment.text.strip() for segment in segments).strip()


if __name__ == '__main__':
    # Smoke check: one second of silence must transcribe to (nearly) nothing without errors.
    import tempfile, wave
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'silence.wav')
        with wave.open(path, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b'\0\0' * 16000)
        text = transcribe(path)
        assert isinstance(text, str) and len(text) < 40, text
        print('ok', _model.model.device, repr(text))
