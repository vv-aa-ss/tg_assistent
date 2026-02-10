"""
SQLite-хранилище для FSM-состояний aiogram.
Замена MemoryStorage — состояния сохраняются при перезапуске бота.
"""

import json
import logging
from typing import Any, Dict, Optional

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.fsm.strategy import FSMStrategy

logger = logging.getLogger("app.fsm_storage")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fsm_states (
    bot_id   INTEGER NOT NULL,
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    state    TEXT,
    data     TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (bot_id, chat_id, user_id)
);
"""


class SQLiteStorage(BaseStorage):
    """
    Персистентное SQLite-хранилище для FSM-состояний aiogram 3.x.

    Данные сериализуются в JSON.  Таблица создаётся автоматически при
    первом подключении.
    """

    def __init__(self, db_path: str = "./data/fsm_storage.db") -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    # ---------- lifecycle ----------

    async def _ensure_connection(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL;")
            await self._db.execute("PRAGMA busy_timeout=5000;")
            await self._db.execute(_CREATE_TABLE_SQL)
            await self._db.commit()
            logger.info("SQLiteStorage: подключено к %s", self._db_path)
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("SQLiteStorage: соединение закрыто")

    # ---------- helpers ----------

    @staticmethod
    def _key(key: StorageKey):
        return key.bot_id, key.chat_id, key.user_id

    # ---------- state ----------

    async def set_state(
        self, key: StorageKey, state: Optional[str] = None
    ) -> None:
        db = await self._ensure_connection()
        bot_id, chat_id, user_id = self._key(key)
        # aiogram может передать объект State — извлекаем строку состояния для SQLite
        if state is not None:
            if isinstance(state, State):
                state = state.state
            elif not isinstance(state, str):
                state = str(state)
        await db.execute(
            """
            INSERT INTO fsm_states (bot_id, chat_id, user_id, state)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(bot_id, chat_id, user_id)
            DO UPDATE SET state = excluded.state
            """,
            (bot_id, chat_id, user_id, state),
        )
        await db.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        db = await self._ensure_connection()
        bot_id, chat_id, user_id = self._key(key)
        cursor = await db.execute(
            "SELECT state FROM fsm_states WHERE bot_id=? AND chat_id=? AND user_id=?",
            (bot_id, chat_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["state"]

    # ---------- data ----------

    async def set_data(
        self, key: StorageKey, data: Dict[str, Any]
    ) -> None:
        db = await self._ensure_connection()
        bot_id, chat_id, user_id = self._key(key)
        json_data = json.dumps(data, ensure_ascii=False, default=str)
        await db.execute(
            """
            INSERT INTO fsm_states (bot_id, chat_id, user_id, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(bot_id, chat_id, user_id)
            DO UPDATE SET data = excluded.data
            """,
            (bot_id, chat_id, user_id, json_data),
        )
        await db.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        db = await self._ensure_connection()
        bot_id, chat_id, user_id = self._key(key)
        cursor = await db.execute(
            "SELECT data FROM fsm_states WHERE bot_id=? AND chat_id=? AND user_id=?",
            (bot_id, chat_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["data"])
        except (json.JSONDecodeError, TypeError):
            return {}
