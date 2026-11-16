"""SQLite 连接级 PRAGMA 契约。

其中 temp_store=MEMORY 是 2026-11-14 日志页 500 的修复点：深翻页的
ORDER BY created_at DESC 会退化成 temp b-tree，一旦 %TEMP% 不可写，
SQLite 就抛 "unable to open database file"，接口直接 500。这条断言把
pragma 钉住，避免有人顺手删掉。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from l02_infra.db import build_engine


@pytest.mark.asyncio
async def test_sqlite_pragmas_are_applied_to_every_pooled_connection():
    # 与 conftest.database_url 同样的取舍：有的沙箱拒绝在运行期新建的目录里
    # 打开 SQLite 文件（也拒绝列目录），因此数据库落在仓库内已存在的目录下。
    root = Path(
        os.environ.get("PYTEST_DB_DIR")
        or Path(__file__).resolve().parents[1] / ".pytest-dbs"
    )
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / f"pragmas-{uuid.uuid4().hex}.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        # 同时持有两条连接：串行取两次会从池里拿回同一条，证明不了「每个连接都生效」。
        async with engine.connect() as first, engine.connect() as second:
            # 两条并发连接必须来自池里两个不同的底层连接。
            first_raw = await first.get_raw_connection()
            second_raw = await second.get_raw_connection()
            assert first_raw.driver_connection is not second_raw.driver_connection
            for conn in (first, second):
                journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
                temp_store = (await conn.execute(text("PRAGMA temp_store"))).scalar()
                busy_timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
                synchronous = (await conn.execute(text("PRAGMA synchronous"))).scalar()
                foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()

                assert str(journal_mode).lower() == "wal"
                # 2 = MEMORY：排序不再落临时文件。
                assert int(temp_store) == 2
                assert int(busy_timeout) == 15000
                assert int(synchronous) == 1  # NORMAL
                assert int(foreign_keys) == 1
    finally:
        await engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
