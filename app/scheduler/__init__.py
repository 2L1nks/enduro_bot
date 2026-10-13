"""Фоновые задачи."""
from __future__ import annotations

import asyncio

from aiogram import Bot

from app.scheduler.worker import flush_now, process_queue, run_worker

__all__ = [
    "flush_now",
    "process_queue",
    "run_worker",
    "start_scheduler",
    "stop_scheduler",
]

_task: asyncio.Task | None = None


def start_scheduler(bot: Bot) -> asyncio.Task:
    """Запускает воркер фоновой задачей."""
    global _task
    _task = asyncio.create_task(run_worker(bot), name="notification-worker")
    return _task


async def stop_scheduler() -> None:
    """Корректно гасит воркер при остановке бота."""
    global _task
    if _task is None or _task.done():
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
