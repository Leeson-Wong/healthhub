from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def migrate(engine) -> None:
    """In-place schema upkeep for pre-existing SQLite databases (idempotent)."""
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_obs_person_code_time "
        "ON observations(person_id, canonical_code, effective_at)",
        # 溯源：存量 source_reports 的渠道回填（api=历史脚本推送 / csv_import=历史 CSV 导入）
        "UPDATE source_reports SET provenance='api' WHERE provenance IS NULL OR provenance=''",
        # 用药：存量记录全部来自费用清单 → 日粒度；假时刻规整为纯日期
        "UPDATE medications SET time_precision='day' WHERE time_precision IS NULL OR time_precision=''",
        "UPDATE medications SET started_at=substr(started_at,1,10) WHERE started_at LIKE '%T00:00:00%'",
        "UPDATE medications SET ended_at=substr(ended_at,1,10) WHERE ended_at LIKE '%T23:59:59%'",
        # 药物状态语义：费用清单推断（ended_at 空=清单最后一天仍计费→在用推断）
        "UPDATE medications SET status_inference='billed_ongoing' WHERE ended_at IS NULL AND status_inference IS NULL",
        "UPDATE medications SET status_inference='billed_ended' WHERE ended_at IS NOT NULL AND status_inference IS NULL",
        # 关键转折点：小图（sparkline）只钉这些
        "UPDATE clinical_events SET pivotal=1 WHERE title LIKE '胆道引流'",
        "UPDATE clinical_events SET pivotal=1 WHERE title LIKE '%头孢他啶/阿维巴坦%万古%'",
        "UPDATE clinical_events SET pivotal=1 WHERE title LIKE '脓毒性休克%'",
    ]
    with engine.begin() as conn:
        for col in ("encounter_id INTEGER", "provenance VARCHAR(16) DEFAULT 'api'"):
            try:
                conn.execute(text(f"ALTER TABLE source_reports ADD COLUMN {col}"))
            except Exception:
                pass  # column already exists
        try:
            conn.execute(text("ALTER TABLE medications ADD COLUMN time_precision VARCHAR(8) DEFAULT 'day'"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE clinical_events ADD COLUMN pivotal INTEGER DEFAULT 0"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE persons ADD COLUMN phase_mode VARCHAR(8)"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE medications ADD COLUMN status_inference VARCHAR(16)"))
        except Exception:
            pass
        for s in stmts:
            conn.execute(text(s))


def make_engine(settings: Settings):
    db_path = Path(settings.database_path)
    if db_path.parent and str(db_path.parent) not in ("", "."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = make_engine(settings)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def session(self) -> Session:
        return self.SessionLocal()

    def get_session(self) -> Generator[Session, None, None]:
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()
