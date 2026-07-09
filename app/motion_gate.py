"""MotionGate — cheap "did anything change?" skip for inference loops.

Running a detector on a frame that is essentially identical to the previous
one is wasted CPU — the dominant cost on a GPU-less box. This gate compares
consecutive frames with a downsampled grayscale absdiff (~1ms vs ~hundreds of
ms for a CPU inference) and tells the caller to skip detection on static
scenes, while still forcing a detection every Nth frame so slow drifts
(a vehicle creeping up, gradual lighting change) are never missed.

The intrusion engine has an equivalent inline check; this is the shared,
reusable version used by the plate detection engine.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("gateguard.app")


class MotionGate:
    def __init__(
        self,
        skip_threshold: float = 0.005,   # fraction of pixels that must change
        mandatory_every_n: int = 10,     # force a detection every N static frames
        diff_level: int = 25,            # grey-level delta that counts as "changed"
        downscale: tuple[int, int] = (160, 90),
    ):
        self.skip_threshold = skip_threshold
        self.mandatory_every_n = max(1, mandatory_every_n)
        self.diff_level = diff_level
        self.downscale = downscale
        self._prev_gray = None
        self._static_streak = 0

    def should_skip(self, frame) -> bool:
        """Return True if the frame is ~identical to the previous one AND this
        isn't a mandatory detection tick. Any error → False (never skip)."""
        try:
            import cv2
            import numpy as np
            small = cv2.resize(frame, self.downscale)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        except Exception:
            return False

        if self._prev_gray is None:
            self._prev_gray = gray
            return False

        try:
            import cv2
            diff = cv2.absdiff(gray, self._prev_gray)
            changed = float((diff > self.diff_level).sum()) / float(diff.size)
        except Exception:
            self._prev_gray = gray
            return False

        self._prev_gray = gray
        if changed < self.skip_threshold:
            self._static_streak += 1
            if self._static_streak >= self.mandatory_every_n:
                self._static_streak = 0
                return False   # force a detection to catch slow drifts
            return True
        self._static_streak = 0
        return False
