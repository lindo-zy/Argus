"""Embedded durable polling worker; one or several API processes may run it."""
import asyncio
import logging

from .db import SessionLocal
from .services.orchestration import tick

logger = logging.getLogger(__name__)


async def serve(stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick, SessionLocal)
        except Exception:
            # Don't log DB URLs/HTTP authentication. Persisted claims expire for recovery.
            logger.error('Jenkins coordinator iteration failed; durable intent retained for recovery')
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
