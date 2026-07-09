"""Shared bounded thread pools for ML inference and disk/IO work.

Why this exists
---------------
On a low-spec CPU box (no CUDA) every camera loop and the plate engine were
each calling ``loop.run_in_executor(None, ...)``, which dispatches onto
asyncio's DEFAULT ThreadPoolExecutor. That pool is effectively unbounded
(``min(32, os.cpu_count()+4)`` workers), so with 3-4 cameras plus the plate
pipeline plus test endpoints, many heavy inferences could run at once,
oversubscribing the few physical cores and thrashing caches — making the
whole system slower than running them back-to-back.

This module funnels work through TWO small, explicit pools:

* **inference pool** (``INFERENCE_WORKERS``, default 1) — all model
  inference (ALPR + person detection + motion). Default 1 because
  ``PersonDetector`` already serializes YOLO behind a single lock and ONNX
  Runtime's intra-op threads provide the real parallelism *inside* one
  inference. One outer worker runs inferences sequentially → best cache
  behavior, no oversubscription. Bump it on 6+ core machines.
* **io pool** (``IO_WORKERS``, default 2) — screenshot rendering (PIL),
  JPEG/clip writes, and the fuzzy DB lookup. Kept separate so disk/IO work
  never blocks the single inference worker.

Call :func:`init_pools` once at startup (main.py lifespan). Submodules use
:func:`get_inference_pool` / :func:`get_io_pool` with
``loop.run_in_executor(pool, fn, ...)``.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger("gateguard.app")

_inference_pool: Optional[ThreadPoolExecutor] = None
_io_pool: Optional[ThreadPoolExecutor] = None


def init_pools(inference_workers: int = 1, io_workers: int = 2) -> None:
    """Create the shared pools. Idempotent — safe to call once at startup."""
    global _inference_pool, _io_pool
    if _inference_pool is None:
        _inference_pool = ThreadPoolExecutor(
            max_workers=max(1, inference_workers),
            thread_name_prefix="infer",
        )
    if _io_pool is None:
        _io_pool = ThreadPoolExecutor(
            max_workers=max(1, io_workers),
            thread_name_prefix="io",
        )
    logger.info(
        "Inference pools ready: inference_workers=%d io_workers=%d",
        max(1, inference_workers), max(1, io_workers),
    )


def get_inference_pool() -> ThreadPoolExecutor:
    """Bounded pool for all model inference. Lazily inits with defaults."""
    if _inference_pool is None:
        init_pools()
    return _inference_pool


def get_io_pool() -> ThreadPoolExecutor:
    """Bounded pool for disk/IO work (screenshots, clips, DB lookups)."""
    if _io_pool is None:
        init_pools()
    return _io_pool


def shutdown_pools() -> None:
    """Stop both pools — called on app shutdown."""
    global _inference_pool, _io_pool
    for pool in (_inference_pool, _io_pool):
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.exception("Error shutting down a thread pool")
    _inference_pool = None
    _io_pool = None
