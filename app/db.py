import aiosqlite
import os
from typing import Optional, Tuple, List, Dict, Any

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cards (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	name TEXT NOT NULL,
	details TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	tg_id INTEGER UNIQUE,
	username TEXT,
	full_name TEXT
);

CREATE TABLE IF NOT EXISTS user_card (
	user_id INTEGER NOT NULL,
	card_id INTEGER NOT NULL,
	PRIMARY KEY (user_id),
	FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
	FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS message_card (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	pattern TEXT NOT NULL,
	is_regex INTEGER NOT NULL DEFAULT 0,
	card_id INTEGER NOT NULL,
	FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);
"""


class Database:
	def __init__(self, path: str) -> None:
		self._path = path
		self._db: Optional[aiosqlite.Connection] = None

	@property
	def path(self) -> str:
		return self._path

	async def connect(self) -> None:
		os.makedirs(os.path.dirname(self._path), exist_ok=True)
		self._db = await aiosqlite.connect(self._path)
		await self._db.execute("PRAGMA journal_mode=WAL;")
		await self._db.executescript(SCHEMA_SQL)
		# migrate: add user_message to cards if missing
		await self._ensure_cards_user_message()
		await self._db.commit()

	async def _ensure_cards_user_message(self) -> None:
		assert self._db
		cur = await self._db.execute("PRAGMA table_info(cards)")
		cols = [r[1] for r in await cur.fetchall()]
		if "user_message" not in cols:
			await self._db.execute("ALTER TABLE cards ADD COLUMN user_message TEXT")

	async def close(self) -> None:
		if self._db is not None:
			await self._db.close()
			self._db = None

	async def add_card(self, name: str, details: str) -> int:
		assert self._db
		cur = await self._db.execute("INSERT INTO cards(name, details) VALUES(?, ?)", (name, details))
		await self._db.commit()
		return cur.lastrowid

	async def list_cards(self) -> List[Tuple[int, str, str]]:
		assert self._db
		cur = await self._db.execute("SELECT id, name, details FROM cards ORDER BY id DESC")
		return await cur.fetchall()

	async def delete_card(self, card_id: int) -> None:
		assert self._db
		await self._db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
		await self._db.commit()

	async def get_or_create_user(self, tg_id: Optional[int], username: Optional[str], full_name: Optional[str]) -> int:
		assert self._db
		if tg_id is not None:
			cur = await self._db.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
			row = await cur.fetchone()
			if row:
				return row[0]
		cur = await self._db.execute(
			"INSERT INTO users(tg_id, username, full_name) VALUES(?, ?, ?)", (tg_id, username, full_name)
		)
		await self._db.commit()
		return cur.lastrowid

	async def list_users_with_binding(self) -> List[Dict[str, Any]]:
		assert self._db
		query = (
			"SELECT u.id, u.tg_id, u.username, u.full_name, c.id, c.name "
			"FROM users u LEFT JOIN user_card uc ON uc.user_id = u.id "
			"LEFT JOIN cards c ON c.id = uc.card_id "
			"ORDER BY u.id DESC"
		)
		cur = await self._db.execute(query)
		rows = await cur.fetchall()
		result: List[Dict[str, Any]] = []
		for r in rows:
			result.append(
				{
					"user_id": r[0],
					"tg_id": r[1],
					"username": r[2],
					"full_name": r[3],
					"card_id": r[4],
					"card_name": r[5],
				}
			)
		return result

	async def bind_user_to_card(self, user_id: int, card_id: int) -> None:
		assert self._db
		await self._db.execute(
			"INSERT INTO user_card(user_id, card_id) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET card_id=excluded.card_id",
			(user_id, card_id),
		)
		await self._db.commit()

	async def get_card_for_user_tg(self, tg_id: int) -> Optional[Tuple[int, str, str, Optional[str]]]:
		assert self._db
		query = (
			"SELECT c.id, c.name, c.details, c.user_message FROM users u "
			"JOIN user_card uc ON uc.user_id = u.id "
			"JOIN cards c ON c.id = uc.card_id WHERE u.tg_id = ?"
		)
		cur = await self._db.execute(query, (tg_id,))
		return await cur.fetchone()

	async def add_message_pattern(self, pattern: str, is_regex: bool, card_id: int) -> int:
		assert self._db
		cur = await self._db.execute(
			"INSERT INTO message_card(pattern, is_regex, card_id) VALUES(?, ?, ?)", (pattern, 1 if is_regex else 0, card_id)
		)
		await self._db.commit()
		return cur.lastrowid

	async def find_card_by_text(self, text: str) -> Optional[Tuple[int, str, str]]:
		assert self._db
		# naive implementation: substring patterns first, regex second (done in Python)
		cur = await self._db.execute("SELECT id, name, details FROM cards")
		cards = await cur.fetchall()
		cur = await self._db.execute("SELECT pattern, is_regex, card_id FROM message_card ORDER BY id DESC")
		patterns = await cur.fetchall()
		import re
		for pattern, is_regex, card_id in patterns:
			if not is_regex and pattern.lower() in text.lower():
				for c in cards:
					if c[0] == card_id:
						return c
		for pattern, is_regex, card_id in patterns:
			if is_regex:
				try:
					if re.search(pattern, text):
						for c in cards:
							if c[0] == card_id:
								return c
				except re.error:
					continue
		return None

	async def set_card_user_message(self, card_id: int, text: Optional[str]) -> None:
		assert self._db
		await self._db.execute("UPDATE cards SET user_message = ? WHERE id = ?", (text, card_id))
		await self._db.commit()

	async def get_card_user_message(self, card_id: int) -> Optional[str]:
		assert self._db
		cur = await self._db.execute("SELECT user_message FROM cards WHERE id = ?", (card_id,))
		row = await cur.fetchone()
		return row[0] if row else None
