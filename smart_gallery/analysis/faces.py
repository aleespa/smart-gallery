"""GPU face detection + embedding via InsightFace (the ``faces`` extra).

``FaceScanner`` wraps InsightFace's ``buffalo_l`` pack (SCRFD detector + ArcFace
embeddings). Everything heavy (insightface / onnxruntime / cv2 / rawpy) is
imported lazily here so the core CLI stays light and import-clean without the
extra installed.

Verified working on an RTX 5060 (Blackwell sm_120) with onnxruntime-gpu 1.27
(CUDA 13) + nvidia-cudnn-cu13. The two non-obvious requirements proven during
testing and enforced below:
  * call ``onnxruntime.preload_dlls()`` before building the session, else the
    CUDA provider can't find cuDNN and **silently falls back to CPU**;
  * assert the bound provider is CUDA and warn loudly otherwise — 90k photos on
    CPU is hours, not minutes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, NamedTuple

from loguru import logger

from smart_gallery.config import FACE_MODEL_NAME

# Extensions whose embedded JPEG preview we decode instead of demosaicing the
# full RAW (the slow path). Detection works fine on the preview.
RAW_EXTENSIONS = {".cr2", ".cr3", ".arw", ".nef", ".raf", ".rw2", ".dng"}


class Detection(NamedTuple):
    bbox: tuple  # (x1, y1, x2, y2) in original-image pixels
    det_score: float
    embedding: "object"  # np.ndarray float32[D], L2-normalized


def _providers_from_env() -> list:
    raw = os.getenv("SG_FACES_PROVIDERS")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return ["CUDAExecutionProvider", "CPUExecutionProvider"]


def _det_size_from_env() -> tuple:
    raw = os.getenv("SG_FACES_DET_SIZE")
    if raw:
        try:
            n = int(raw)
            return (n, n)
        except ValueError:
            pass
    return (640, 640)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


class FaceScannerUnavailable(RuntimeError):
    """Raised when the optional ``faces`` extra is not installed."""


class FaceScanner:
    """Loads the model once, then ``scan_image(path)`` returns its faces."""

    def __init__(self) -> None:
        try:
            import cv2  # noqa: F401
            import numpy as np  # noqa: F401
            import onnxruntime as ort
            from insightface.app import FaceAnalysis
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise FaceScannerUnavailable(
                "Face recognition needs the 'faces' extra. Install it with:\n"
                "    uv sync --extra faces"
            ) from exc

        # Load CUDA/cuDNN DLLs from the nvidia-* wheels onto the search path.
        # Without this the CUDA provider can't load cuDNN and falls back to CPU.
        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls()
            except Exception as exc:  # pragma: no cover
                logger.debug(f"onnxruntime.preload_dlls() failed: {exc}")

        providers = _providers_from_env()
        det_size = _det_size_from_env()
        ctx_id = int(os.getenv("SG_FACES_CTX_ID", "0"))
        self.min_score = _env_float("SG_FACES_MIN_SCORE", 0.5)
        self.min_px = _env_float("SG_FACES_MIN_PX", 24.0)

        logger.info(f"Loading face model '{FACE_MODEL_NAME}' (providers={providers})")
        self.app = FaceAnalysis(name=FACE_MODEL_NAME, providers=providers)
        self.app.prepare(ctx_id=ctx_id, det_size=det_size)

        self.provider = self._resolve_provider()
        logger.info(f"Face model ready — execution provider: {self.provider}")
        if self.provider == "CPUExecutionProvider":
            logger.warning(
                "Face inference is running on the CPU — this is MANY times "
                "slower (hours for a large library). Check the CUDA/cuDNN setup: "
                "install the 'faces' extra's nvidia-cudnn-cu13 wheel and ensure "
                "only onnxruntime-gpu (not onnxruntime) is installed."
            )
            if os.getenv("SG_FACES_REQUIRE_GPU") == "1":
                raise RuntimeError(
                    "SG_FACES_REQUIRE_GPU=1 but the model bound to the CPU."
                )
        self._warmup()

    def _resolve_provider(self) -> str:
        for model in self.app.models.values():
            session = getattr(model, "session", None)
            if session is not None:
                return session.get_providers()[0]
        return "unknown"

    def _warmup(self) -> None:
        """Trigger one-time cuDNN algorithm search so the first real image's
        ETA isn't skewed by the ~1-2s warmup cost."""
        import numpy as np

        det = _det_size_from_env()
        try:
            self.app.get(np.zeros((det[1], det[0], 3), dtype=np.uint8))
        except Exception as exc:  # pragma: no cover
            logger.debug(f"Face model warmup skipped: {exc}")

    # ── image decoding (CPU, thread-safe — run on a worker pool) ─────────────
    def decode(self, abspath: str):
        """Decode a file to a BGR numpy image, or None if unreadable. Pure CPU
        and releases the GIL (cv2), so the scan pool can overlap many decodes
        with GPU inference. RAW files use their fast embedded JPEG preview."""
        import cv2
        import numpy as np

        path = Path(abspath)
        if path.suffix.lower() in RAW_EXTENSIONS:
            try:
                import rawpy

                with rawpy.imread(str(path)) as raw:
                    thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    return cv2.imdecode(
                        np.frombuffer(thumb.data, np.uint8), cv2.IMREAD_COLOR
                    )
                return cv2.cvtColor(thumb.data, cv2.COLOR_RGB2BGR)
            except Exception as exc:
                logger.debug(f"RAW preview decode failed for {path.name}: {exc}")
                return None
        try:
            return cv2.imread(str(path))
        except Exception as exc:  # pragma: no cover
            logger.debug(f"Decode failed for {path.name}: {exc}")
            return None

    # ── inference (GPU, single-owner — call from one thread only) ────────────
    def detect(self, img) -> List[Detection]:
        """Detect + embed faces in an already-decoded BGR image."""
        import numpy as np

        if img is None:
            return []
        out: List[Detection] = []
        for f in self.app.get(img):
            score = float(getattr(f, "det_score", 0.0) or 0.0)
            if score < self.min_score:
                continue
            x1, y1, x2, y2 = (float(v) for v in f.bbox)
            if min(x2 - x1, y2 - y1) < self.min_px:
                continue
            emb = np.asarray(f.normed_embedding, dtype=np.float32)
            out.append(Detection((x1, y1, x2, y2), score, emb))
        return out

    def scan_image(self, abspath: str) -> List[Detection]:
        """Convenience: decode + detect one image (used by tests)."""
        return self.detect(self.decode(abspath))
