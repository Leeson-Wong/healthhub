"""APScheduler AsyncIOScheduler — one daily report job, APP_TZ timezone."""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("healthhub.scheduler")


def make_scheduler(settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.app_tz)
    hour, minute = 7, 30
    try:
        parts = settings.daily_report_time.split(":")
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        log.warning("invalid DAILY_REPORT_TIME %r, falling back to 07:30", settings.daily_report_time)

    def _job() -> list[dict]:
        from app.daily_report import run_daily_for_all
        from app.db import Database

        db = Database(settings)
        try:
            with db.SessionLocal() as session:
                return run_daily_for_all(session, settings)
        finally:
            db.engine.dispose()

    async def _ajob():
        result = await asyncio.to_thread(_job)
        log.info("daily report finished: %s", result)

    scheduler.add_job(_ajob, CronTrigger(hour=hour, minute=minute, timezone=settings.app_tz),
                      id="daily_report", max_instances=1, coalesce=True)
    return scheduler
