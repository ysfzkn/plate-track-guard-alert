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
_plate_pool: Optional[ThreadPoolExecutor] = None


def init_pools(inference_workers: int = 1, io_workers: int = 2) -> None:
    """Create the shared pools. Idempotent — safe to call once at startup."""
    global _inference_pool, _io_pool, _plate_pool
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
    # Dedicated single worker for the PLATE/barrier ALPR. The plate read is
    # time-critical (a car is waiting at the gate) but a car only appears
    # occasionally, and the motion gate skips static frames — so it is idle
    # almost all the time. Giving it its own worker means a plate read never
    # queues behind the 7 always-on intrusion cameras on the shared inference
    # pool (which would add up to a second of latency). The brief 2-inference
    # overlap while a car passes is rare and short → no sustained
    # oversubscription of the weak CPU.
    if _plate_pool is None:
        _plate_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plate")
    logger.info(
        "Inference pools ready: inference_workers=%d io_workers=%d plate_workers=1",
        max(1, inference_workers), max(1, io_workers),
    )


def get_inference_pool() -> ThreadPoolExecutor:
    """Bounded pool for all model inference. Lazily inits with defaults."""
    if _inference_pool is None:
        init_pools()
    return _inference_pool


def get_plate_pool() -> ThreadPoolExecutor:
    """Dedicated single-worker pool for the barrier plate ALPR so it never
    queues behind the intrusion cameras. Lazily inits with defaults."""
    if _plate_pool is None:
        init_pools()
    return _plate_pool


def get_io_pool() -> ThreadPoolExecutor:
    """Bounded pool for disk/IO work (screenshots, clips, DB lookups)."""
    if _io_pool is None:
        init_pools()
    return _io_pool


def shutdown_pools() -> None:
    """Stop all pools — called on app shutdown."""
    global _inference_pool, _io_pool, _plate_pool
    for pool in (_inference_pool, _io_pool, _plate_pool):
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.exception("Error shutting down a thread pool")
    _inference_pool = None
    _io_pool = None
    _plate_pool = None
