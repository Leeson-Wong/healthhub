"""Daily report: generate-on-read for a local date, idempotent by (person, date)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.insights import generate_insight
from app.llm import LLM
from app.models import InsightReport, Observation, Person


def local_day_bounds_utc(settings, date_str: str) -> tuple[str, str, str]:
    from datetime import timezone as _tz

    tz = ZoneInfo(settings.app_tz)
    day = datetime.fromisoformat(date_str).replace(tzinfo=tz, hour=0, minute=0, second=0, microsecond=0)
    start_utc = day.astimezone(_tz.utc)
    end_utc = (day + timedelta(days=1)).astimezone(_tz.utc)
    return (start_utc.isoformat(timespec="seconds"), end_utc.isoformat(timespec="seconds"),
            day.date().isoformat())


def get_daily_report(db: Session, settings, llm: LLM, person: Person, date_str: str,
                     force: bool = False) -> InsightReport:
    existing = db.query(InsightReport).filter_by(
        person_id=person.id, kind="daily", report_date=date_str).order_by(InsightReport.id.desc()).first()
    if existing and existing.status == "ok" and not force:
        return existing

    day_start, day_end, _ = local_day_bounds_utc(settings, date_str)
    has_data = db.query(Observation).filter(
        Observation.person_id == person.id,
        Observation.effective_at >= day_start, Observation.effective_at < day_end).count() > 0
    # 趋势回看窗口：最早记录起，但最多回看 7 天（控制 prompt 体积，防内存峰值）
    first_obs = db.query(func.min(Observation.effective_at)).filter(
        Observation.person_id == person.id).scalar()
    trend_floor = datetime.fromisoformat(day_start) - timedelta(days=6)
    if first_obs is None:
        first_obs = trend_floor
    else:
        fo = datetime.fromisoformat(str(first_obs))
        if fo.tzinfo is None:
            from datetime import timezone as _utz
            fo = fo.replace(tzinfo=_utz.utc)
        first_obs = fo if fo < trend_floor else trend_floor
    if not has_data:
        if existing and existing.status == "skipped":
            return existing
        row = InsightReport(person_id=person.id, kind="daily", report_date=date_str,
                            window_from=day_start, window_to=day_end,
                            model="none", prompt_md="(no observations on this date)",
                            response_md="当日无新增检验数据，跳过生成。", status="skipped")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    return generate_insight(db, llm, person, kind="daily",
                            date_from=first_obs.isoformat(timespec="seconds"),
                            date_to=day_end, report_date=date_str,
                            tz_name=settings.app_tz)


def run_daily_for_all(db: Session, settings, date_str: str | None = None) -> list[dict]:
    """Scheduler entrypoint: generate for every person who has data that local day."""
    tz = ZoneInfo(settings.app_tz)
    today = date_str or datetime.now(tz).date().isoformat()
    llm = LLM(settings)
    results = []
    for person in db.query(Person).all():
        row = get_daily_report(db, settings, llm, person, today)
        results.append({"person_id": person.id, "report_date": today,
                        "status": row.status, "id": row.id})
    return results
