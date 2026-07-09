"""SimpleIouTracker — lightweight per-camera track-ID assignment.

Why: the production multi-camera setup ran ALL cameras through one shared
YOLO model with model.track(persist=True). ByteTrack's persistent state is a
SINGLE timeline, so interleaving frames from 9 different cameras cross-
contaminates tracks and can stop confirming them — which silently drops/
fragments detections (the exact failure that surfaced in testing).

Fix: each camera detects with the STATELESS detect_raw() (model.predict) and
owns its own SimpleIouTracker, which assigns stable IDs across frames by
greedy association. Association is IoU-first with a center-distance fallback,
so it survives the large frame-to-frame motion you get at low FPS (2 fps) —
where plain IoU overlap would drop to zero and break the consensus rule.

Track IDs are offset per camera (id + camera_id * 1_000_000) so the classifier
keys (camera_id, zone_id, track_id) never collide across cameras.
"""

from __future__ import annotations

from typing import Optional


def iou_xywh(a: tuple, b: tuple) -> float:
    """IoU of two (x, y, w, h) boxes. 0 on degenerate input."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _center(b: tuple) -> tuple:
    x, y, w, h = b
    return (x + w / 2.0, y + h / 2.0)


class SimpleIouTracker:
    CAMERA_ID_OFFSET = 1_000_000

    def __init__(
        self,
        camera_id: int = 0,
        iou_threshold: float = 0.2,
        max_age: int = 8,
        dist_factor: float = 1.5,
    ):
        self.camera_id = camera_id
        self.iou_threshold = iou_threshold
        self.max_age = max_age          # frames a track survives without a match
        self.dist_factor = dist_factor  # center-distance match window = factor * box size
        self._tracks: list[dict] = []   # {id, bbox, misses}
        self._next = 1

    def update(self, observations: list, timestamp=None) -> list:
        """Assign stable track_id + camera_id to each observation (in place).

        observations: list[PersonObservation] from PersonDetector.detect_raw
        (track_id is a throwaway per-frame value; we overwrite it). Returns the
        same list for convenience.
        """
        for t in self._tracks:
            t["matched"] = False

        for o in observations:
            best, best_score = None, 0.0
            # 1) IoU-first
            for t in self._tracks:
                if t["matched"]:
                    continue
                s = iou_xywh(o.bbox, t["bbox"])
                if s >= self.iou_threshold and s > best_score:
                    best, best_score = t, s
            # 2) center-distance fallback (handles big motion at low FPS)
            if best is None:
                ocx, ocy = _center(o.bbox)
                ow, oh = o.bbox[2], o.bbox[3]
                best_dist = self.dist_factor * max(ow, oh)
                for t in self._tracks:
                    if t["matched"]:
                        continue
                    tcx, tcy = _center(t["bbox"])
                    d = ((ocx - tcx) ** 2 + (ocy - tcy) ** 2) ** 0.5
                    if d < best_dist:
                        best_dist, best = d, t
            if best is not None:
                best["matched"] = True
                best["bbox"] = o.bbox
                best["misses"] = 0
                tid = best["id"]
            else:
                tid = self._next
                self._next += 1
                self._tracks.append({"id": tid, "bbox": o.bbox, "misses": 0, "matched": True})

            o.track_id = tid + self.camera_id * self.CAMERA_ID_OFFSET
            o.camera_id = self.camera_id
            if timestamp is not None:
                o.timestamp = timestamp

        # Age out unmatched tracks
        survivors = []
        for t in self._tracks:
            if not t["matched"]:
                t["misses"] += 1
            if t["misses"] <= self.max_age:
                survivors.append(t)
        self._tracks = survivors
        return observations
