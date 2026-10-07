"""
对话历史存储模块 — SQLite 持久化

职责：
  1. 建表（conversations + messages）
  2. 创建/列出/删除对话
  3. 将 LangChain 消息对象存入 messages 表
  4. 从 messages 表恢复为 LangChain 消息对象

LangChain 消息 → DB 行的映射：
  SystemMessage → role="system", content=文本, name="summary"（压缩摘要）
  HumanMessage  → role="human",  content=文本
  AIMessage     → role="ai",     content=文本, tool_calls=JSON
  ToolMessage   → role="tool",   content=文本, tool_call_id=xxx, name=工具名
"""

import sqlite3
import json
import uuid
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage


# 默认数据库路径：项目目录下 data/chat_history.db
DB_PATH = Path(__file__).parent.parent / "data" / "chat_history.db"


# ════════════════════════════════════════════
# 序列化/反序列化：LangChain 消息 <-> dict
# ════════════════════════════════════════════

def _msg_to_row(msg) -> dict:
    """将 LangChain 消息对象转成数据库行 dict"""
    if isinstance(msg, SystemMessage):
        return {
            "role": "system",
            "content": msg.content or "",
            "tool_calls": None,
            "tool_call_id": None,
            "name": getattr(msg, "name", None) or None,
        }

    if isinstance(msg, HumanMessage):
        return {
            "role": "human",
            "content": msg.content or "",
            "tool_calls": None,
            "tool_call_id": None,
            "name": None,
        }

    if isinstance(msg, AIMessage):
        return {
            "role": "ai",
            "content": msg.content or "",
            "tool_calls": json.dumps(msg.tool_calls, ensure_ascii=False)
            if msg.tool_calls else None,
            "tool_call_id": None,
            "name": None,
        }

    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "content": msg.content or "",
            "tool_calls": None,
            "tool_call_id": msg.tool_call_id,
            "name": msg.name,
        }

    raise TypeError(f"不支持的消息类型: {type(msg).__name__}")


def _row_to_msg(row: dict):
    """将数据库行 dict 恢复为 LangChain 消息对象"""
    role = row["role"]

    if role == "system":
        # 摘要的身份标记也要恢复，否则重启后只能依赖正文前缀识别摘要。
        return SystemMessage(content=row["content"] or "", name=row.get("name") or None)

    if role == "human":
        return HumanMessage(content=row["content"] or "")

    if role == "ai":
        tool_calls = json.loads(row["tool_calls"]) if row["tool_calls"] else []
        msg = AIMessage(content=row["content"] or "", tool_calls=tool_calls)
        if tool_calls and not msg.additional_kwargs.get("tool_calls"):
            msg.additional_kwargs["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]
        return msg

    if role == "tool":
        return ToolMessage(
            content=row["content"] or "",
            tool_call_id=row["tool_call_id"] or "",
            name=row["name"] or "",
        )

    raise ValueError(f"未知 role: {role}")


# ════════════════════════════════════════════
# ChatStorage — 核心类
# ════════════════════════════════════════════

class ChatStorage:

    def __init__(self, db_path: str | Path = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── 数据库连接 ──────────────────────────────

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        """建表（幂等）"""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id         TEXT PRIMARY KEY,
                    title      TEXT NOT NULL DEFAULT '新对话',
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id   TEXT NOT NULL REFERENCES conversations(id),
                    role              TEXT NOT NULL,   -- system | human | ai | tool
                    content           TEXT,
                    tool_calls        TEXT,            -- JSON，仅 ai 角色使用
                    tool_call_id      TEXT,            -- 仅 tool 角色使用
                    name              TEXT,            -- tool:工具名 | system:"summary"
                    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id, id);
            """)

    # ── 对话 CRUD ────────────────────────────────

    def create_conversation(self, title: str = "新对话") -> str:
        conv_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title) VALUES (?, ?)",
                (conv_id, title),
            )
        return conv_id

    def update_title(self, conv_id: str, title: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=datetime('now','localtime') WHERE id=?",
                (title, conv_id),
            )

    def list_conversations(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, title, created_at, updated_at,
                          (SELECT COUNT(*) FROM messages WHERE conversation_id=c.id) AS msg_count
                   FROM conversations c
                   ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_conversation(self, conv_id: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    # ── 消息存取 ──────────────────────────────────

    def save_message(self, conv_id: str, msg) -> int:
        """
        保存一条 LangChain 消息到数据库。

        参数:
            conv_id: 对话 ID
            msg: SystemMessage | HumanMessage | AIMessage | ToolMessage

        返回:
            messages 表中的自增 id
        """
        row = _msg_to_row(msg)
        with self._connect() as conn:
            # 如果是摘要消息（system + name=summary + content 含"前情摘要"），先删旧摘要
            if row["role"] == "system" and row["name"] == "summary" and "前情摘要" in (row["content"] or ""):
                conn.execute(
                    "DELETE FROM messages WHERE conversation_id=? AND role='system' AND name='summary' AND content LIKE '%前情摘要%'",
                    (conv_id,),
                )
            cur = conn.execute(
                """INSERT INTO messages
                   (conversation_id, role, content, tool_calls, tool_call_id, name)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conv_id, row["role"], row["content"],
                 row["tool_calls"], row["tool_call_id"], row["name"]),
            )
            conn.execute(
                "UPDATE conversations SET updated_at=datetime('now','localtime') WHERE id=?",
                (conv_id,),
            )
            return cur.lastrowid

    def save_messages(self, conv_id: str, msgs: list) -> list[int]:
        """在同一个事务中保存一组消息。

        一轮对话的用户消息、工具消息和最终回答必须一起提交；其中任何一条
        写入失败时整轮回滚，数据库里就不会留下无法继续的半轮对话。
        """
        ids = []
        with self._connect() as conn:
            for msg in msgs:
                row = _msg_to_row(msg)
                cur = conn.execute(
                    """INSERT INTO messages
                       (conversation_id, role, content, tool_calls, tool_call_id, name)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (conv_id, row["role"], row["content"], row["tool_calls"],
                     row["tool_call_id"], row["name"]),
                )
                ids.append(cur.lastrowid)
            conn.execute(
                "UPDATE conversations SET updated_at=datetime('now','localtime') WHERE id=?",
                (conv_id,),
            )
        return ids

    def replace_all_messages(self, conv_id: str, messages: list):
        """原子替换对话的全部消息（压缩后将 DB 同步到内存状态）

        一个事务内：清空旧消息 → 逐条插入新消息 → 更新时间戳。
        要么全成功，要么全回滚。
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
            for msg in messages:
                row = _msg_to_row(msg)
                conn.execute(
                    """INSERT INTO messages
                       (conversation_id, role, content, tool_calls, tool_call_id, name)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (conv_id, row["role"], row["content"],
                     row["tool_calls"], row["tool_call_id"], row["name"]),
                )
            conn.execute(
                "UPDATE conversations SET updated_at=datetime('now','localtime') WHERE id=?",
                (conv_id,),
            )

    def get_messages(self, conv_id: str) -> list:
        """
        加载对话的全部消息，恢复为 LangChain 消息对象列表。

        消息按 id 升序排列，可直接传给 agent.stream()。
        压缩由 ConversationMemory 按轮次 + token 阈值自动触发，
        不再依赖 get_messages 截断。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, tool_calls, tool_call_id, name "
                "FROM messages WHERE conversation_id=? ORDER BY id",
                (conv_id,),
            ).fetchall()
            return [_row_to_msg(dict(r)) for r in rows]

    def get_message_count(self, conv_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id=?",
                (conv_id,),
            ).fetchone()
        return row["cnt"] if row else 0
