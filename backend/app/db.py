from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, DateTime, Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """SQLite 不存时区：写入时统一转成 UTC 去掉 tzinfo，读出时补回 UTC。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime}


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)


Migration = Callable[[Connection], None]


def add_column(table: str, column: str, ddl: str) -> Migration:
    """给已有的表加一列；列已存在（新库由 create_all 直接建好）时什么也不做，所以重复执行也安全。"""

    def run(conn: Connection) -> None:
        columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        if column not in columns:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

    return run


def scrub_deleted_jobs(conn: Connection) -> None:
    """以前删除任务只打标记，文件名、报错和自定义提示词都留在库里；补清一遍（与 job_ops.scrub_deleted 一致）。"""
    conn.execute(
        text(
            "UPDATE jobs SET filename = '（已删除）', error = NULL, warning = NULL,"
            " options = json_remove(options, '$.custom_system_prompt')"
            " WHERE deleted_at IS NOT NULL"
        )
    )


MIGRATIONS: list[tuple[int, list[Migration]]] = [
    (1, [add_column("meetings", "llm_preset_id", "INTEGER")]),
    (2, [scrub_deleted_jobs]),
]


def init_db(engine: Engine) -> None:
    from . import models  # noqa: F401  注册表结构

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        version = conn.execute(text("PRAGMA user_version")).scalar() or 0
        for target, steps in MIGRATIONS:
            if target > version:
                for step in steps:
                    step(conn)
                conn.execute(text(f"PRAGMA user_version = {target}"))
