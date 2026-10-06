"""备份与恢复用的数据库快照和校验（deploy/remote.sh 经 python -m app.cli db ... 调用）。

备份定时器以 root 在几乎空白的环境里调用，所以路径全部由参数传入：不读配置（load_config）；
直接用 sqlite3 打开，不跑迁移（init_db 会改动交给它的备份库）；直接读 secret.key 的字节，
不经过 SecretBox（文件不存在时它会生成新密钥）。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .models import ENCRYPTED_COLUMNS, AsrProvider, Job, Meeting, MeetingLlmModel, MeetingLlmPreset, ModelProfile, User

# 摘要里统计行数的表，按这个顺序输出（remote.sh 的日志里看的就是这一行）
COUNTED_TABLES = tuple(
    m.__tablename__ for m in (User, ModelProfile, Job, Meeting, AsrProvider, MeetingLlmModel, MeetingLlmPreset)
)


def snapshot(src: Path, dest: Path) -> tuple[bool, str]:
    """把 src 一致地复制到 dest，返回快照的校验结果（是否完好, 摘要）。"""
    with closing(sqlite3.connect(dest)) as conn:
        with closing(sqlite3.connect(src)) as source:
            source.backup(conn)  # 在线备份 API：服务运行中也能得到一致快照，直接 cp 可能拿到半截事务
        conn.execute("pragma journal_mode=delete")
        return _summary(conn)


def verify(db: Path, key: Path | None = None) -> tuple[bool, str]:
    """校验数据库；给了 key 就顺带看这把 secret.key 能解开几个加密存放的密钥。"""
    with closing(sqlite3.connect(db)) as conn:
        return _summary(conn, key)


def _summary(conn: sqlite3.Connection, key: Path | None = None) -> tuple[bool, str]:
    check = conn.execute("pragma integrity_check").fetchone()[0]
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type = ?", ("table",))}
    counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in COUNTED_TABLES if t in tables}
    line = " ".join(f"{k}={v}" for k, v in counts.items())
    if key is not None:
        line += _key_note(conn, tables, key)
    return check == "ok", f"{check} {line}"


def _key_note(conn: sqlite3.Connection, tables: set[str], key: Path) -> str:
    # 备份不含 secret.key：看本机现有的密钥能解开多少个；旧版本的备份里没有的表直接跳过
    fernet = Fernet(key.read_bytes().strip())
    tokens: list[str] = []
    for column in ENCRYPTED_COLUMNS:
        table, name = column.class_.__tablename__, column.key
        if table in tables:
            tokens += [r[0] for r in conn.execute(f"select {name} from {table} where length({name}) > 0")]
    usable = 0
    for token in tokens:
        try:
            fernet.decrypt(token.encode())
            usable += 1
        except InvalidToken:
            pass
    note = f" keys={usable}/{len(tokens)}"
    if usable < len(tokens):
        note += f"（{len(tokens) - usable} 个密钥用本机 secret.key 解不开，恢复后要在后台重新填写）"
    return note
