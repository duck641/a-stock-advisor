"""板块轮动数据库。

轮动数据与聊天记录分开保存。删除或压缩一段对话时，不会破坏长期积累的
市场状态数据；以后新增板块周期和事件表时也统一放在这个数据库中。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROTATION_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sector_rotation.db"


class RotationStorage:
    """保存市场状态、板块日线状态和轮动周期。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else ROTATION_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """事务结束后显式关闭连接，避免 Windows 一直占用数据库文件。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """创建轮动模块需要的表；重复启动不会清空已有数据。"""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_daily (
                    trade_date          TEXT PRIMARY KEY,
                    benchmark_code      TEXT NOT NULL,
                    benchmark_name      TEXT NOT NULL,
                    close               REAL NOT NULL,
                    ma20                REAL NOT NULL,
                    ma60                REAL NOT NULL,
                    ma20_change_3d      REAL NOT NULL,
                    return_20d          REAL,
                    sector_up_ratio     REAL,
                    sector_count        INTEGER NOT NULL DEFAULT 0,
                    raw_regime          TEXT NOT NULL,
                    confirmed_regime    TEXT NOT NULL,
                    confirmation_days   INTEGER NOT NULL,
                    data_status         TEXT NOT NULL,
                    reasons_json        TEXT NOT NULL,
                    data_source         TEXT NOT NULL,
                    created_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at          TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_market_daily_regime
                    ON market_daily(confirmed_regime, trade_date);

                CREATE TABLE IF NOT EXISTS sector_master (
                    sector_id       TEXT PRIMARY KEY,
                    sector_name     TEXT NOT NULL UNIQUE,
                    source          TEXT NOT NULL,
                    active          INTEGER NOT NULL DEFAULT 1,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS sector_daily (
                    trade_date          TEXT NOT NULL,
                    sector_id           TEXT NOT NULL REFERENCES sector_master(sector_id),
                    open                REAL,
                    high                REAL,
                    low                 REAL,
                    close               REAL NOT NULL,
                    volume              REAL,
                    amount              REAL,
                    return_3d           REAL,
                    return_5d           REAL,
                    return_10d          REAL,
                    return_20d          REAL,
                    relative_3d         REAL,
                    relative_5d         REAL,
                    relative_10d        REAL,
                    ma5                 REAL,
                    ma10                REAL,
                    ma20                REAL,
                    volume_ratio_20d    REAL,
                    drawdown_20d_high   REAL,
                    new_5d_low          INTEGER NOT NULL DEFAULT 0,
                    strength_percentile REAL,
                    rotation_heat       INTEGER NOT NULL,
                    price_pattern       TEXT,
                    volume_price_pattern TEXT,
                    cooling_priority    INTEGER NOT NULL DEFAULT 0,
                    raw_state           TEXT NOT NULL,
                    confirmed_state     TEXT NOT NULL,
                    state_start_date    TEXT NOT NULL,
                    cooling_days        INTEGER NOT NULL DEFAULT 0,
                    in_cooling_window   INTEGER NOT NULL DEFAULT 0,
                    market_regime       TEXT NOT NULL,
                    data_quality        TEXT NOT NULL,
                    reasons_json        TEXT NOT NULL,
                    created_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (trade_date, sector_id)
                );

                CREATE INDEX IF NOT EXISTS idx_sector_daily_state
                    ON sector_daily(confirmed_state, trade_date);

                CREATE TABLE IF NOT EXISTS sector_cycles (
                    cycle_id        TEXT PRIMARY KEY,
                    sector_id       TEXT NOT NULL REFERENCES sector_master(sector_id),
                    start_date      TEXT NOT NULL,
                    peak_date       TEXT NOT NULL,
                    end_date        TEXT,
                    start_close     REAL NOT NULL,
                    peak_close      REAL NOT NULL,
                    max_return      REAL NOT NULL,
                    max_heat        INTEGER NOT NULL,
                    duration_days   INTEGER NOT NULL,
                    end_reason      TEXT,
                    status          TEXT NOT NULL,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_sector_cycles_sector
                    ON sector_cycles(sector_id, start_date);

                CREATE TABLE IF NOT EXISTS sector_update_runs (
                    trade_date       TEXT PRIMARY KEY,
                    planned_count    INTEGER NOT NULL,
                    success_count    INTEGER NOT NULL,
                    failed_count     INTEGER NOT NULL,
                    coverage         REAL NOT NULL,
                    failed_json      TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                """
            )

            # 采用增量迁移，保留已有数据库；NULL 值会让刷新入口自动执行历史回放。
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(sector_daily)")}
            migrations = {
                "entry_days": "INTEGER",
                "exit_days": "INTEGER",
                "price_pattern": "TEXT",
                "volume_price_pattern": "TEXT",
            }
            for name, column_type in migrations.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE sector_daily ADD COLUMN {name} {column_type}")

    def upsert_market_day(self, record: dict) -> None:
        """按交易日写入状态；同一天重复更新时覆盖当日计算结果。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_daily (
                    trade_date, benchmark_code, benchmark_name, close, ma20, ma60,
                    ma20_change_3d, return_20d, sector_up_ratio, sector_count,
                    raw_regime, confirmed_regime, confirmation_days, data_status,
                    reasons_json, data_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    benchmark_code=excluded.benchmark_code,
                    benchmark_name=excluded.benchmark_name,
                    close=excluded.close,
                    ma20=excluded.ma20,
                    ma60=excluded.ma60,
                    ma20_change_3d=excluded.ma20_change_3d,
                    return_20d=excluded.return_20d,
                    sector_up_ratio=excluded.sector_up_ratio,
                    sector_count=excluded.sector_count,
                    raw_regime=excluded.raw_regime,
                    confirmed_regime=excluded.confirmed_regime,
                    confirmation_days=excluded.confirmation_days,
                    data_status=excluded.data_status,
                    reasons_json=excluded.reasons_json,
                    data_source=excluded.data_source,
                    updated_at=datetime('now','localtime')
                """,
                (
                    record["trade_date"], record["benchmark_code"],
                    record["benchmark_name"], record["close"], record["ma20"],
                    record["ma60"], record["ma20_change_3d"], record["return_20d"],
                    record["sector_up_ratio"], record["sector_count"],
                    record["raw_regime"], record["confirmed_regime"],
                    record["confirmation_days"], record["data_status"],
                    json.dumps(record["reasons"], ensure_ascii=False),
                    record["data_source"],
                ),
            )

    def get_recent_before(self, trade_date: str, limit: int = 10) -> list[dict]:
        """读取指定日期以前的数据，供连续信号确认使用。"""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM market_daily
                   WHERE trade_date < ? ORDER BY trade_date DESC LIMIT ?""",
                (trade_date, limit),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_latest(self) -> dict | None:
        """返回最新一个交易日的市场状态。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM market_daily ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
        return self._decode_row(row) if row else None

    def get_market_on_or_before(self, trade_date: str) -> dict | None:
        """读取不晚于板块数据日期的市场状态。"""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM market_daily WHERE trade_date <= ?
                   ORDER BY trade_date DESC LIMIT 1""",
                (trade_date,),
            ).fetchone()
        return self._decode_row(row) if row else None

    def save_sector_analysis(
        self,
        masters: list[dict],
        daily_records: list[dict],
        cycles: list[dict],
        run: dict,
    ) -> None:
        """在一个事务中保存板块资料、每日状态、周期和更新结果。"""
        with self._connect() as conn:
            for item in masters:
                conn.execute(
                    """INSERT INTO sector_master (sector_id, sector_name, source)
                       VALUES (?, ?, ?)
                       ON CONFLICT(sector_id) DO UPDATE SET
                           sector_name=excluded.sector_name,
                           source=excluded.source,
                           active=1,
                           updated_at=datetime('now','localtime')""",
                    (item["sector_id"], item["sector_name"], item["source"]),
                )

            columns = (
                "trade_date", "sector_id", "open", "high", "low", "close",
                "volume", "amount", "return_3d", "return_5d", "return_10d",
                "return_20d", "relative_3d", "relative_5d", "relative_10d",
                "ma5", "ma10", "ma20", "volume_ratio_20d",
                "drawdown_20d_high", "new_5d_low", "strength_percentile",
                "rotation_heat", "price_pattern", "volume_price_pattern",
                "cooling_priority", "raw_state",
                "confirmed_state", "state_start_date", "cooling_days",
                "in_cooling_window", "market_regime", "data_quality",
                "reasons_json", "entry_days", "exit_days",
            )
            placeholders = ",".join("?" for _ in columns)
            updates = ",".join(
                f"{column}=excluded.{column}" for column in columns
                if column not in ("trade_date", "sector_id")
            )
            for item in daily_records:
                values = []
                for column in columns:
                    value = item[column]
                    if column == "reasons_json":
                        value = json.dumps(value, ensure_ascii=False)
                    values.append(value)
                conn.execute(
                    f"""INSERT INTO sector_daily ({','.join(columns)})
                        VALUES ({placeholders})
                        ON CONFLICT(trade_date, sector_id) DO UPDATE SET
                            {updates}, updated_at=datetime('now','localtime')""",
                    values,
                )

            # 最近窗口会被重新回放；先删除该窗口内的旧周期，避免算法或源数据
            # 更新后遗留已经不存在的周期记录。更早的长期历史仍然保留。
            replay_start: dict[str, str] = {}
            for item in daily_records:
                sector_id = item["sector_id"]
                trade_date = item["trade_date"]
                replay_start[sector_id] = min(replay_start.get(sector_id, trade_date), trade_date)
            for sector_id, start_date in replay_start.items():
                conn.execute(
                    "DELETE FROM sector_cycles WHERE sector_id=? AND start_date>=?",
                    (sector_id, start_date),
                )

            for item in cycles:
                conn.execute(
                    """INSERT INTO sector_cycles (
                           cycle_id, sector_id, start_date, peak_date, end_date,
                           start_close, peak_close, max_return, max_heat,
                           duration_days, end_reason, status
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(cycle_id) DO UPDATE SET
                           peak_date=excluded.peak_date,
                           end_date=excluded.end_date,
                           peak_close=excluded.peak_close,
                           max_return=excluded.max_return,
                           max_heat=excluded.max_heat,
                           duration_days=excluded.duration_days,
                           end_reason=excluded.end_reason,
                           status=excluded.status,
                           updated_at=datetime('now','localtime')""",
                    tuple(item[key] for key in (
                        "cycle_id", "sector_id", "start_date", "peak_date", "end_date",
                        "start_close", "peak_close", "max_return", "max_heat",
                        "duration_days", "end_reason", "status",
                    )),
                )

            conn.execute(
                """INSERT INTO sector_update_runs (
                       trade_date, planned_count, success_count, failed_count,
                       coverage, failed_json, status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(trade_date) DO UPDATE SET
                       planned_count=excluded.planned_count,
                       success_count=excluded.success_count,
                       failed_count=excluded.failed_count,
                       coverage=excluded.coverage,
                       failed_json=excluded.failed_json,
                       status=excluded.status,
                       updated_at=datetime('now','localtime')""",
                (
                    run["trade_date"], run["planned_count"], run["success_count"],
                    run["failed_count"], run["coverage"],
                    json.dumps(run["failed"], ensure_ascii=False), run["status"],
                ),
            )

    def get_sector_state(self, sector_name: str) -> dict | None:
        """按名称读取某个板块最新状态。"""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT d.*, m.sector_name
                   FROM sector_daily d JOIN sector_master m USING (sector_id)
                   WHERE m.sector_name=? ORDER BY d.trade_date DESC LIMIT 1""",
                (sector_name,),
            ).fetchone()
        return self._decode_sector_row(row) if row else None

    def list_sector_states(self, state: str | None = None) -> list[dict]:
        """读取所有板块各自最新一天的状态，可按三种状态筛选。"""
        sql = """
            SELECT d.*, m.sector_name
            FROM sector_daily d JOIN sector_master m USING (sector_id)
            JOIN (
                SELECT sector_id, MAX(trade_date) AS latest_date
                FROM sector_daily GROUP BY sector_id
            ) latest ON latest.sector_id=d.sector_id AND latest.latest_date=d.trade_date
        """
        params: tuple = ()
        if state:
            sql += " WHERE d.confirmed_state=?"
            params = (state,)
        sql += " ORDER BY d.rotation_heat DESC, d.cooling_priority DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._decode_sector_row(row) for row in rows]

    def get_sector_states_before(self, trade_date: str) -> dict[str, dict]:
        """读取目标日前每个板块的最后状态，供下一交易日增量计算继承。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.*, m.sector_name
                FROM sector_daily d JOIN sector_master m USING (sector_id)
                JOIN (
                    SELECT sector_id, MAX(trade_date) AS latest_date
                    FROM sector_daily WHERE trade_date < ? GROUP BY sector_id
                ) latest ON latest.sector_id=d.sector_id AND latest.latest_date=d.trade_date
                """,
                (trade_date,),
            ).fetchall()
        return {row["sector_id"]: self._decode_sector_row(row) for row in rows}

    def get_cycle_context(self) -> dict[str, dict]:
        """读取每个板块最近结束日期和当前周期，恢复跨进程的状态机。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sector_cycles ORDER BY sector_id, start_date"
            ).fetchall()
        context: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            sector = context.setdefault(item["sector_id"], {
                "last_cycle_end": None,
                "active_cycle": None,
            })
            if item["status"] == "进行中":
                sector["active_cycle"] = item
            elif item["end_date"]:
                sector["last_cycle_end"] = item["end_date"]
        return context

    def get_latest_update_run(self) -> dict | None:
        """返回最近一次板块更新结果，用于标记当次获取失败的行业。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sector_update_runs ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["failed"] = json.loads(result.pop("failed_json"))
        return result

    @staticmethod
    def _decode_sector_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["reasons"] = json.loads(result.pop("reasons_json"))
        return result

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["reasons"] = json.loads(result.pop("reasons_json"))
        return result


# 保留第一阶段使用的名称，避免已有导入失效。
MarketRegimeStorage = RotationStorage
