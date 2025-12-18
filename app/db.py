import aiosqlite
import os
import time
import re
from typing import Optional, Tuple, List, Dict, Any
import logging

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
	full_name TEXT,
	last_interaction_at INTEGER
);

CREATE TABLE IF NOT EXISTS user_card (
	user_id INTEGER NOT NULL,
	card_id INTEGER NOT NULL,
	PRIMARY KEY (user_id, card_id),
	FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
	FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS card_delivery_log (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	user_id INTEGER NOT NULL,
	card_id INTEGER NOT NULL,
	delivered_at INTEGER NOT NULL,
	admin_id INTEGER,
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

_logger = logging.getLogger("app.db")


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
		await self._ensure_user_card_multi_bind()
		await self._ensure_users_last_interaction()
		await self._ensure_card_delivery_log()
		await self._ensure_card_columns()
		await self._migrate_card_columns()
		await self._ensure_crypto_columns()
		await self._migrate_crypto_columns()
		await self._ensure_cash_columns()
		await self._ensure_card_groups()
		await self._ensure_google_sheets_settings()
		await self._ensure_card_requisites()
		await self._ensure_rate_history()
		await self._ensure_menu_user()
		await self._ensure_item_usage_log()
		await self._db.commit()

	async def _ensure_menu_user(self) -> None:
		"""Создает специального пользователя для логирования выбора карт в меню"""
		assert self._db
		# Создаем специального пользователя с tg_id = -1 для логирования выбора карт в меню
		await self._db.execute(
			"INSERT OR IGNORE INTO users(tg_id, username, full_name) VALUES(?, ?, ?)",
			(-1, "_menu_user", "Menu Selection User")
		)
		_logger.debug("Ensured menu user exists")

	async def _ensure_cards_user_message(self) -> None:
		assert self._db
		cur = await self._db.execute("PRAGMA table_info(cards)")
		cols = [r[1] for r in await cur.fetchall()]
		if "user_message" not in cols:
			await self._db.execute("ALTER TABLE cards ADD COLUMN user_message TEXT")
			_logger.debug("Applied migration: add cards.user_message")

	async def _ensure_user_card_multi_bind(self) -> None:
		assert self._db
		cur = await self._db.execute("PRAGMA table_info(user_card)")
		rows = await cur.fetchall()
		card_col = next((r for r in rows if r[1] == "card_id"), None)
		if card_col and card_col[5] == 0:
			_logger.debug("Applying migration: expand user_card primary key for multi-binding")
			await self._db.execute(
				"""
				CREATE TABLE IF NOT EXISTS user_card_new (
					user_id INTEGER NOT NULL,
					card_id INTEGER NOT NULL,
					PRIMARY KEY (user_id, card_id),
					FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
					FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
				)
				"""
			)
			await self._db.execute(
				"INSERT OR IGNORE INTO user_card_new(user_id, card_id) SELECT user_id, card_id FROM user_card"
			)
			await self._db.execute("DROP TABLE user_card")
			await self._db.execute("ALTER TABLE user_card_new RENAME TO user_card")
			_logger.debug("Migration completed: user_card now supports multiple cards per user")

	async def _ensure_users_last_interaction(self) -> None:
		assert self._db
		cur = await self._db.execute("PRAGMA table_info(users)")
		cols = [r[1] for r in await cur.fetchall()]
		if "last_interaction_at" not in cols:
			await self._db.execute("ALTER TABLE users ADD COLUMN last_interaction_at INTEGER")
			_logger.debug("Applied migration: add users.last_interaction_at")

	async def _ensure_card_delivery_log(self) -> None:
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='card_delivery_log'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE card_delivery_log (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					user_id INTEGER NOT NULL,
					card_id INTEGER NOT NULL,
					delivered_at INTEGER NOT NULL,
					admin_id INTEGER,
					FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
					FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_card_delivery_user ON card_delivery_log(user_id)"
			)
		await self._db.execute(
			"CREATE INDEX IF NOT EXISTS idx_card_delivery_time ON card_delivery_log(delivered_at)"
		)
		_logger.debug("Created table card_delivery_log")

	async def _ensure_card_columns(self) -> None:
		"""Создает таблицу card_columns для хранения адресов столбцов карт"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='card_columns'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE card_columns (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					card_id INTEGER NOT NULL UNIQUE,
					column TEXT NOT NULL,
					FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_card_columns_card_id ON card_columns(card_id)"
			)
			_logger.debug("Created table card_columns")
		else:
			# Миграция: если таблица существует, проверяем наличие user_name_pattern
			cur = await self._db.execute("PRAGMA table_info(card_columns)")
			cols = [r[1] for r in await cur.fetchall()]
			if "user_name_pattern" in cols:
				# Создаем новую таблицу без user_name_pattern
				await self._db.execute(
					"""
					CREATE TABLE IF NOT EXISTS card_columns_new (
						id INTEGER PRIMARY KEY AUTOINCREMENT,
						card_id INTEGER NOT NULL UNIQUE,
						column TEXT NOT NULL,
						FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
					)
					"""
				)
				# Переносим данные: берем первую запись для каждой карты (если есть дубликаты)
				await self._db.execute(
					"""
					INSERT INTO card_columns_new (card_id, column)
					SELECT card_id, MIN(column) as column
					FROM card_columns
					GROUP BY card_id
					"""
				)
				# Удаляем старую таблицу и переименовываем новую
				await self._db.execute("DROP TABLE card_columns")
				await self._db.execute("ALTER TABLE card_columns_new RENAME TO card_columns")
				await self._db.execute(
					"CREATE INDEX IF NOT EXISTS idx_card_columns_card_id ON card_columns(card_id)"
				)
				_logger.debug("Migrated card_columns table: removed user_name_pattern")

	async def _migrate_card_columns(self) -> None:
		"""Миграция старых данных: добавляет адреса столбцов для существующих карт (устаревший метод, оставлен для совместимости)"""
		# Миграция больше не нужна, так как структура таблицы упрощена
		pass

	async def _ensure_crypto_columns(self) -> None:
		"""Создает таблицу crypto_columns для хранения адресов столбцов криптовалют"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_columns'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE crypto_columns (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					crypto_type TEXT NOT NULL UNIQUE,
					column TEXT NOT NULL
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_crypto_columns_type ON crypto_columns(crypto_type)"
			)
			_logger.debug("Created table crypto_columns")

	async def _migrate_crypto_columns(self) -> None:
		"""Миграция старых данных: добавляет адреса столбцов для криптовалют"""
		assert self._db
		# Проверяем, есть ли уже данные в таблице
		cur = await self._db.execute("SELECT COUNT(*) FROM crypto_columns")
		count = (await cur.fetchone())[0]
		if count > 0:
			_logger.debug(f"Таблица crypto_columns уже содержит {count} записей, пропускаем миграцию")
			return
		
		# Миграция старых данных на основе захардкоженных правил
		# BTC → AS
		# LTC → AY
		# XMR-1 → AU
		# XMR-2 → AV
		# XMR-3 → AW
		# USDT → (пока не используется, но добавим для будущего использования)
		
		default_columns = {
			"BTC": "AS",
			"LTC": "AY",
			"XMR-1": "AU",
			"XMR-2": "AV",
			"XMR-3": "AW",
			"USDT": "AX"  # Предполагаемый адрес для USDT
		}
		
		migrated = 0
		for crypto_type, column in default_columns.items():
			await self.set_crypto_column(crypto_type, column)
			migrated += 1
			_logger.info(f"✅ Мигрирован адрес столбца: crypto_type='{crypto_type}', column='{column}'")
		
		if migrated > 0:
			_logger.info(f"✅ Миграция криптовалют завершена: добавлено {migrated} адресов столбцов")
		else:
			_logger.debug("Миграция криптовалют не добавила новых записей")

	async def _ensure_cash_columns(self) -> None:
		"""Создает таблицу cash_columns для хранения адресов столбцов наличных"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='cash_columns'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE cash_columns (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					cash_name TEXT NOT NULL UNIQUE,
					column TEXT NOT NULL,
					currency TEXT DEFAULT 'RUB',
					display_name TEXT DEFAULT ''
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_cash_columns_name ON cash_columns(cash_name)"
			)
			_logger.debug("Created table cash_columns")
		else:
			# Миграция: добавляем поля currency и display_name, если их нет
			cur = await self._db.execute("PRAGMA table_info(cash_columns)")
			cols = [r[1] for r in await cur.fetchall()]
			if "currency" not in cols:
				await self._db.execute("ALTER TABLE cash_columns ADD COLUMN currency TEXT DEFAULT 'RUB'")
				_logger.debug("Added column 'currency' to cash_columns")
			if "display_name" not in cols:
				await self._db.execute("ALTER TABLE cash_columns ADD COLUMN display_name TEXT DEFAULT ''")
				_logger.debug("Added column 'display_name' to cash_columns")

	async def _ensure_card_groups(self) -> None:
		"""Создает таблицы для группировки карт"""
		assert self._db
		# Создаем таблицу групп
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='card_groups'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE card_groups (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					name TEXT NOT NULL UNIQUE,
					created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_card_groups_name ON card_groups(name)"
			)
			_logger.debug("Created table card_groups")
		
		# Добавляем поле group_id в таблицу cards, если его нет
		cur = await self._db.execute("PRAGMA table_info(cards)")
		cols = [r[1] for r in await cur.fetchall()]
		if "group_id" not in cols:
			await self._db.execute("ALTER TABLE cards ADD COLUMN group_id INTEGER")
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_cards_group_id ON cards(group_id)"
			)
			_logger.debug("Added group_id column to cards table")

	async def close(self) -> None:
		if self._db is not None:
			await self._db.close()
			self._db = None

	async def add_card(self, name: str, details: str) -> int:
		assert self._db
		cur = await self._db.execute("INSERT INTO cards(name, details) VALUES(?, ?)", (name, details))
		await self._db.commit()
		_logger.debug(f"Card added: id={cur.lastrowid} name={name!r}")
		return cur.lastrowid

	async def list_cards(self) -> List[Tuple[int, str, str]]:
		assert self._db
		cur = await self._db.execute("SELECT id, name, details FROM cards ORDER BY id DESC")
		return await cur.fetchall()

	async def delete_card(self, card_id: int) -> None:
		assert self._db
		await self._db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
		await self._db.commit()
		_logger.debug(f"Card deleted: id={card_id}")

	async def create_user_by_name_only(self, full_name: str) -> Optional[int]:
		"""
		Создает пользователя только с именем (без tg_id) для случаев с MessageOriginHiddenUser.
		Когда пользователь напишет боту, его tg_id будет обновлен через get_or_create_user.
		"""
		assert self._db
		_logger.debug(f"create_user_by_name_only: full_name={full_name}")
		
		# Проверяем, нет ли уже пользователя с таким именем и NULL tg_id
		cur = await self._db.execute(
			"SELECT id FROM users WHERE tg_id IS NULL AND full_name = ?",
			(full_name,)
		)
		row = await cur.fetchone()
		if row:
			_logger.debug(f"User with name '{full_name}' and NULL tg_id already exists: id={row[0]}")
			return row[0]
		
		# Создаем нового пользователя с NULL tg_id
		cur = await self._db.execute(
			"INSERT INTO users(tg_id, username, full_name, last_interaction_at) VALUES(?, ?, ?, ?)",
			(None, None, full_name, int(time.time())),
		)
		await self._db.commit()
		_logger.info(f"User created by name only: id={cur.lastrowid} full_name={full_name}")
		return cur.lastrowid

	async def get_or_create_user(self, tg_id: Optional[int], username: Optional[str], full_name: Optional[str]) -> int:
		assert self._db
		_logger.debug(f"get_or_create_user: tg_id={tg_id} username={username} full_name={full_name}")
		if tg_id is None:
			_logger.debug("get_or_create_user skipped: tg_id is None")
			return -1
		
		# Проверяем, нет ли пользователя с таким именем и NULL tg_id (созданного ранее для скрытого пользователя)
		if full_name:
			cur = await self._db.execute(
				"SELECT id FROM users WHERE tg_id IS NULL AND full_name = ?",
				(full_name,)
			)
			row = await cur.fetchone()
			if row:
				# Обновляем существующего пользователя с NULL tg_id, добавляя реальный tg_id
				user_id = row[0]
				_logger.info(f"Updating user with NULL tg_id: id={user_id}, setting tg_id={tg_id}, username={username}")
				await self._db.execute(
					"UPDATE users SET tg_id = ?, username = COALESCE(?, username), last_interaction_at = ? WHERE id = ?",
					(tg_id, username, int(time.time()), user_id),
				)
				await self._db.commit()
				return user_id
		cur = await self._db.execute("SELECT id, username, full_name FROM users WHERE tg_id = ?", (tg_id,))
		row = await cur.fetchone()
		if row:
			user_id, db_username, db_full_name = row
			if (username and not db_username) or (full_name and not db_full_name):
				await self._db.execute(
					"UPDATE users SET username = COALESCE(?, username), full_name = COALESCE(?, full_name) WHERE id = ?",
					(username, full_name, user_id),
				)
				await self._db.commit()
				_logger.debug(f"User updated: id={user_id} username={username} full_name={full_name}")
			else:
				_logger.debug(f"User exists (no update): id={user_id}")
			return user_id
		cur = await self._db.execute(
			"INSERT INTO users(tg_id, username, full_name, last_interaction_at) VALUES(?, ?, ?, ?)",
			(tg_id, username, full_name, int(time.time())),
		)
		await self._db.commit()
		_logger.debug(f"User created: id={cur.lastrowid} tg_id={tg_id}")
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
		users: Dict[int, Dict[str, Any]] = {}
		for r in rows:
			user_id = r[0]
			info = users.setdefault(
				user_id,
				{
					"user_id": r[0],
					"tg_id": r[1],
					"username": r[2],
					"full_name": r[3],
					"cards": [],
				},
			)
			if r[4] is not None:
				info["cards"].append({"card_id": r[4], "card_name": r[5]})
		users_list = list(users.values())
		def sort_key(user: Dict[str, Any]) -> tuple:
			label = ""
			if user.get("full_name"):
				label = user["full_name"]
			elif user.get("username"):
				label = f"@{user['username']}"
			elif user.get("tg_id"):
				label = str(user["tg_id"])
			if not label:
				label = f"ID {user['user_id']}"
			return (label.lower(), user["user_id"])
		users_list.sort(key=sort_key)
		return users_list

	async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
		assert self._db
		query = (
			"SELECT id, tg_id, username, full_name FROM users WHERE id = ?"
		)
		cur = await self._db.execute(query, (user_id,))
		row = await cur.fetchone()
		if not row:
			return None
		return {
			"user_id": row[0],
			"tg_id": row[1],
			"username": row[2],
			"full_name": row[3],
			"cards": await self.list_cards_for_user(row[0]),
		}

	async def get_user_stats(self, user_id: int) -> Optional[Dict[str, Any]]:
		"""
		Получает статистику пользователя: количество доставок и последнюю активность.
		
		Args:
			user_id: ID пользователя
		
		Returns:
			Словарь с ключами: delivery_count, last_interaction_at или None если пользователь не найден
		"""
		assert self._db
		query = (
			"""
			SELECT 
				u.last_interaction_at,
				COUNT(l.id) AS delivery_count
			FROM users u
			LEFT JOIN card_delivery_log l ON l.user_id = u.id
			WHERE u.id = ?
			GROUP BY u.id
			"""
		)
		cur = await self._db.execute(query, (user_id,))
		row = await cur.fetchone()
		if not row:
			return None
		return {
			"last_interaction_at": row[0],
			"delivery_count": row[1] or 0,
		}

	async def bind_user_to_card(self, user_id: int, card_id: int) -> None:
		assert self._db
		await self._db.execute(
			"INSERT OR IGNORE INTO user_card(user_id, card_id) VALUES(?, ?)",
			(user_id, card_id),
		)
		await self._db.commit()
		_logger.debug(f"User bound: user_id={user_id} -> card_id={card_id}")

	async def unbind_user_from_card(self, user_id: int, card_id: int) -> None:
		assert self._db
		await self._db.execute(
			"DELETE FROM user_card WHERE user_id = ? AND card_id = ?",
			(user_id, card_id),
		)
		await self._db.commit()
		_logger.debug(f"User unbound: user_id={user_id} -X-> card_id={card_id}")

	async def touch_user(self, user_id: int, when: Optional[int] = None) -> None:
		assert self._db
		timestamp = int(when or time.time())
		await self._db.execute(
			"UPDATE users SET last_interaction_at = ? WHERE id = ?",
			(timestamp, user_id),
		)
		await self._db.commit()
		_logger.debug(f"Touched user_id={user_id} at {timestamp}")

	async def touch_user_by_tg(self, tg_id: int, when: Optional[int] = None) -> None:
		assert self._db
		cur = await self._db.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
		row = await cur.fetchone()
		if row:
			await self.touch_user(row[0], when)

	async def get_user_id_by_tg(self, tg_id: int) -> Optional[int]:
		assert self._db
		cur = await self._db.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
		row = await cur.fetchone()
		return row[0] if row else None

	async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
		"""Находит пользователя по username (без @)"""
		assert self._db
		if not username:
			_logger.debug("get_user_by_username: username пустой")
			return None
		# Убираем @ если есть
		username_clean = username.lstrip("@").lower()
		_logger.debug(f"get_user_by_username: ищем '{username_clean}' (оригинал: '{username}')")
		
		# Ищем по username - пробуем несколько вариантов
		# Сначала точное совпадение (убираем @ и пробелы)
		cur = await self._db.execute(
			"SELECT id, tg_id, username, full_name FROM users WHERE username IS NOT NULL AND LOWER(REPLACE(REPLACE(username, '@', ''), ' ', '')) = ?",
			(username_clean,)
		)
		row = await cur.fetchone()
		if row:
			_logger.info(f"✅ get_user_by_username: найдено совпадение - tg_id={row[1]}, username={row[2]}")
			return {
				"user_id": row[0],
				"tg_id": row[1],
				"username": row[2],
				"full_name": row[3],
			}
		
		# Пробуем найти все пользователей для отладки
		cur = await self._db.execute("SELECT tg_id, username FROM users WHERE username IS NOT NULL LIMIT 10")
		all_users = await cur.fetchall()
		_logger.warning(f"❌ get_user_by_username: пользователь с username '{username}' (ищем '{username_clean}') не найден. Всего пользователей с username: {len(all_users)}")
		if all_users:
			_logger.debug(f"Примеры username в БД: {[u[1] for u in all_users[:5]]}")
		return None

	async def get_user_by_full_name(self, full_name: str) -> Optional[Dict[str, Any]]:
		"""Находит пользователя по full_name (для случаев с MessageOriginHiddenUser)"""
		assert self._db
		if not full_name:
			_logger.debug("get_user_by_full_name: full_name пустой")
			return None
		
		full_name_clean = full_name.strip()
		_logger.debug(f"get_user_by_full_name: ищем '{full_name_clean}'")
		
		# Ищем точное совпадение full_name (включая записи с NULL tg_id)
		cur = await self._db.execute(
			"SELECT id, tg_id, username, full_name FROM users WHERE full_name = ? ORDER BY CASE WHEN tg_id IS NULL THEN 0 ELSE 1 END",
			(full_name_clean,)
		)
		row = await cur.fetchone()
		if row:
			_logger.info(f"✅ get_user_by_full_name: найдено точное совпадение - tg_id={row[1]}, full_name={row[3]}")
			return {
				"user_id": row[0],
				"tg_id": row[1],
				"username": row[2],
				"full_name": row[3],
			}
		
		# Если не нашли точное совпадение, пробуем поиск без учета регистра
		cur = await self._db.execute(
			"SELECT id, tg_id, username, full_name FROM users WHERE LOWER(full_name) = LOWER(?) ORDER BY CASE WHEN tg_id IS NULL THEN 0 ELSE 1 END",
			(full_name_clean,)
		)
		row = await cur.fetchone()
		if row:
			_logger.info(f"✅ get_user_by_full_name: найдено совпадение (без учета регистра) - tg_id={row[1]}, full_name={row[3]}")
			return {
				"user_id": row[0],
				"tg_id": row[1],
				"username": row[2],
				"full_name": row[3],
			}
		
		_logger.warning(f"❌ get_user_by_full_name: пользователь с full_name '{full_name}' не найден")
		return None

	async def find_similar_users_by_name(self, name: str, limit: int = 10) -> List[Dict[str, Any]]:
		"""
		Находит похожих пользователей по имени (частичное совпадение).
		Используется для случаев, когда точного совпадения нет, но может быть полное имя в БД.
		Например: ищем "Jeka", находим "Jeka Иванов", "Jeka Петров" и т.д.
		"""
		assert self._db
		if not name:
			return []
		
		name_clean = name.strip()
		_logger.debug(f"find_similar_users_by_name: ищем похожих на '{name_clean}'")
		
		# Ищем пользователей, у которых name_clean содержится в full_name
		# Используем LIKE для частичного совпадения (без учета регистра)
		search_pattern = f"%{name_clean}%"
		query = """
			SELECT id, tg_id, username, full_name 
			FROM users 
			WHERE full_name IS NOT NULL 
				AND LOWER(full_name) LIKE ?
			ORDER BY 
				CASE 
					WHEN LOWER(full_name) = ? THEN 1  -- Точное совпадение (без учета регистра)
					WHEN LOWER(full_name) LIKE ? THEN 2  -- Начинается с искомого
					WHEN LOWER(full_name) LIKE ? THEN 3  -- Содержит искомое
					ELSE 4
				END,
				full_name
			LIMIT ?
		"""
		
		name_lower = name_clean.lower()
		cur = await self._db.execute(
			query,
			(search_pattern.lower(), name_lower, f"{name_lower}%", search_pattern.lower(), limit)
		)
		rows = await cur.fetchall()
		
		results = [
			{
				"user_id": row[0],
				"tg_id": row[1],
				"username": row[2],
				"full_name": row[3],
			}
			for row in rows
		]
		
		if results:
			_logger.info(f"✅ find_similar_users_by_name: найдено {len(results)} похожих пользователей для '{name_clean}'")
		else:
			_logger.debug(f"find_similar_users_by_name: похожие пользователи для '{name_clean}' не найдены")
		
		return results

	async def log_card_delivery(
		self,
		user_id: int,
		card_id: int,
		admin_id: Optional[int] = None,
		delivered_at: Optional[int] = None,
	) -> None:
		assert self._db
		timestamp = int(delivered_at or time.time())
		await self._db.execute(
			"INSERT INTO card_delivery_log(user_id, card_id, delivered_at, admin_id) VALUES(?, ?, ?, ?)",
			(user_id, card_id, timestamp, admin_id),
		)
		await self._db.commit()
		_logger.debug(f"Logged delivery: user_id={user_id}, card_id={card_id}, admin_id={admin_id}, ts={timestamp}")

	async def log_card_delivery_by_tg(
		self,
		tg_id: int,
		card_id: int,
		admin_id: Optional[int] = None,
		delivered_at: Optional[int] = None,
	) -> None:
		assert self._db
		user_id = await self.get_user_id_by_tg(tg_id)
		if user_id is None:
			_logger.debug(f"Cannot log delivery: tg_id={tg_id} not found")
			return
		await self.log_card_delivery(user_id, card_id, admin_id, delivered_at)

	async def get_recent_cards_by_admin(self, admin_id: int, limit: int = 4) -> List[Tuple[int, str]]:
		"""
		Получает последние используемые карты по admin_id из card_delivery_log.
		Возвращает последние N уникальных карт, упорядоченных по времени последнего использования.
		
		Args:
			admin_id: ID администратора
			limit: Количество карт для возврата (по умолчанию 4)
		
		Returns:
			Список кортежей (card_id, card_name) последних используемых карт
		"""
		assert self._db
		# Используем подзапрос для получения последнего времени использования каждой карты,
		# затем сортируем по этому времени и ограничиваем результат
		query = """
			SELECT c.id, c.name
			FROM cards c
			INNER JOIN (
				SELECT card_id, MAX(delivered_at) as last_used
				FROM card_delivery_log
				WHERE admin_id = ?
				GROUP BY card_id
			) last_usage ON c.id = last_usage.card_id
			ORDER BY last_usage.last_used DESC
			LIMIT ?
		"""
		cur = await self._db.execute(query, (admin_id, limit))
		rows = await cur.fetchall()
		return [(r[0], r[1]) for r in rows]

	async def log_card_selection(self, card_id: int, admin_id: int, delivered_at: Optional[int] = None) -> None:
		"""
		Логирует выбор карты администратором в /add или /rate.
		Использует специального пользователя с tg_id = -1 для обозначения выбора карты в меню.
		
		Args:
			card_id: ID выбранной карты
			admin_id: ID администратора
			delivered_at: Временная метка (по умолчанию текущее время)
		"""
		assert self._db
		timestamp = int(delivered_at or time.time())
		# Получаем user_id специального пользователя для меню
		cur = await self._db.execute("SELECT id FROM users WHERE tg_id = -1")
		row = await cur.fetchone()
		if not row:
			_logger.warning("Menu user not found, cannot log card selection")
			return
		menu_user_id = row[0]
		# Используем menu_user_id для обозначения выбора карты в меню (не реальная доставка пользователю)
		await self._db.execute(
			"INSERT INTO card_delivery_log(user_id, card_id, delivered_at, admin_id) VALUES(?, ?, ?, ?)",
			(menu_user_id, card_id, timestamp, admin_id),
		)
		# Также логируем в item_usage_log для быстрого доступа
		await self.log_item_usage(admin_id, "card", f"card_id_{card_id}")
		await self._db.commit()
		_logger.debug(f"Logged card selection: card_id={card_id}, admin_id={admin_id}, ts={timestamp}")

	async def list_cards_for_user(self, user_id: int) -> List[Dict[str, Any]]:
		assert self._db
		query = (
			"SELECT c.id, c.name, c.details, c.user_message "
			"FROM user_card uc JOIN cards c ON c.id = uc.card_id "
			"WHERE uc.user_id = ? ORDER BY c.id DESC"
		)
		cur = await self._db.execute(query, (user_id,))
		rows = await cur.fetchall()
		return [
			{
				"card_id": r[0],
				"card_name": r[1],
				"details": r[2],
				"user_message": r[3],
			}
			for r in rows
		]

	async def get_card_for_user_tg(self, tg_id: int) -> Optional[Tuple[int, str, str, Optional[str]]]:
		assert self._db
		query = (
			"SELECT c.id, c.name, c.details, c.user_message FROM users u "
			"JOIN user_card uc ON uc.user_id = u.id "
			"JOIN cards c ON c.id = uc.card_id WHERE u.tg_id = ?"
		)
		cur = await self._db.execute(query, (tg_id,))
		return await cur.fetchone()

	async def get_cards_for_user_tg(self, tg_id: int) -> List[Dict[str, Any]]:
		assert self._db
		query = (
			"SELECT c.id, c.name, c.details, c.user_message "
			"FROM users u "
			"JOIN user_card uc ON uc.user_id = u.id "
			"JOIN cards c ON c.id = uc.card_id "
			"WHERE u.tg_id = ? "
			"ORDER BY c.id DESC"
		)
		cur = await self._db.execute(query, (tg_id,))
		rows = await cur.fetchall()
		return [
			{
				"card_id": r[0],
				"card_name": r[1],
				"details": r[2],
				"user_message": r[3],
			}
			for r in rows
		]

	async def get_stats_summary(self) -> Dict[str, Any]:
		assert self._db
		cur_total_users = await self._db.execute("SELECT COUNT(*) FROM users")
		total_users = (await cur_total_users.fetchone())[0]

		cur_total_deliveries = await self._db.execute("SELECT COUNT(*) FROM card_delivery_log")
		total_deliveries = (await cur_total_deliveries.fetchone())[0]

		cur_users = await self._db.execute(
			"""
			SELECT
				u.id,
				u.tg_id,
				u.username,
				u.full_name,
				u.last_interaction_at,
				COUNT(l.id) AS delivery_count,
				MAX(l.delivered_at) AS last_delivery_at
			FROM users u
			LEFT JOIN card_delivery_log l ON l.user_id = u.id
			GROUP BY u.id
			ORDER BY COALESCE(u.last_interaction_at, 0) DESC, u.id DESC
			"""
		)
		rows = await cur_users.fetchall()
		per_user: List[Dict[str, Any]] = []
		for row in rows:
			per_user.append(
				{
					"user_id": row[0],
					"tg_id": row[1],
					"username": row[2],
					"full_name": row[3],
					"last_interaction_at": row[4],
					"delivery_count": row[5],
					"last_delivery_at": row[6],
				}
			)
		recent_sorted = sorted(
			per_user,
			key=lambda x: (
				-(x["delivery_count"] or 0),
				-(x["last_interaction_at"] or 0),
				-x["user_id"],
			),
		)
		top_recent = [entry for entry in recent_sorted if entry["delivery_count"] > 0][:5]
		if not top_recent:
			top_recent = recent_sorted[:5]
		top_inactive = sorted(per_user, key=lambda x: x["last_interaction_at"] or 0)[:7]
		return {
			"total_users": total_users,
			"total_deliveries": total_deliveries,
			"per_user": per_user,
			"top_recent": top_recent,
			"top_inactive": top_inactive,
		}

	async def add_message_pattern(self, pattern: str, is_regex: bool, card_id: int) -> int:
		assert self._db
		cur = await self._db.execute(
			"INSERT INTO message_card(pattern, is_regex, card_id) VALUES(?, ?, ?)", (pattern, 1 if is_regex else 0, card_id)
		)
		await self._db.commit()
		return cur.lastrowid

	async def find_card_by_text(self, text: str) -> Optional[Tuple[int, str, str]]:
		assert self._db
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

	async def set_card_name(self, card_id: int, name: str) -> None:
		"""
		Обновляет название карты.
		
		Args:
			card_id: ID карты
			name: Новое название карты
		"""
		assert self._db
		await self._db.execute("UPDATE cards SET name = ? WHERE id = ?", (name, card_id))
		await self._db.commit()

	async def get_card_user_message(self, card_id: int) -> Optional[str]:
		assert self._db
		cur = await self._db.execute("SELECT user_message FROM cards WHERE id = ?", (card_id,))
		row = await cur.fetchone()
		return row[0] if row else None

	async def get_card_by_id(self, card_id: int) -> Optional[Dict[str, Any]]:
		assert self._db
		cur = await self._db.execute("SELECT id, name, details, user_message, group_id FROM cards WHERE id = ?", (card_id,))
		row = await cur.fetchone()
		if not row:
			return None
		return {
			"card_id": row[0],
			"name": row[1],
			"details": row[2],
			"user_message": row[3],
			"group_id": row[4] if len(row) > 4 else None,
		}

	async def delete_user(self, user_id: int) -> None:
		assert self._db
		# Удаление пользователя автоматически удалит связанные записи из user_card
		# благодаря ON DELETE CASCADE во внешних ключах
		await self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))
		await self._db.commit()
		_logger.debug(f"User deleted: id={user_id}")

	async def get_all_cards_with_columns_and_groups(self) -> List[Dict[str, Any]]:
		"""
		Получает все карты с их столбцами и группами одним запросом.
		Оптимизированная версия для команды /stat_bk.
		
		Returns:
			Список словарей с ключами: card_id, name, column, group_id
		"""
		assert self._db
		cur = await self._db.execute("""
			SELECT 
				c.id as card_id,
				c.name,
				cc.column,
				c.group_id
			FROM cards c
			LEFT JOIN card_columns cc ON c.id = cc.card_id
			ORDER BY c.id DESC
		""")
		rows = await cur.fetchall()
		result = []
		for row in rows:
			result.append({
				"card_id": row[0],
				"name": row[1],
				"column": row[2],
				"group_id": row[3] if len(row) > 3 else None
			})
		return result

	async def get_card_column(self, card_id: int, user_name: Optional[str] = None) -> Optional[str]:
		"""
		Получает адрес столбца для карты.
		
		Args:
			card_id: ID карты
			user_name: Имя пользователя (не используется, оставлено для обратной совместимости)
		
		Returns:
			Адрес столбца (например, "E", "B", "C", "D", "G") или None, если не найдено
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT column FROM card_columns WHERE card_id = ?",
			(card_id,)
		)
		row = await cur.fetchone()
		if row:
			_logger.debug(f"✅ Найден адрес столбца: card_id={card_id} -> column='{row[0]}'")
			return row[0]
		
		_logger.debug(f"❌ Не найден адрес столбца для card_id={card_id}")
		return None

	async def set_card_column(self, card_id: int, column: str, user_name_pattern: Optional[str] = None) -> int:
		"""
		Устанавливает адрес столбца для карты.
		
		Args:
			card_id: ID карты
			column: Адрес столбца (например, "E", "B", "C", "D", "G")
			user_name_pattern: Паттерн имени пользователя (не используется, оставлено для обратной совместимости)
		
		Returns:
			ID созданной или обновленной записи
		"""
		assert self._db
		cur = await self._db.execute(
			"INSERT OR REPLACE INTO card_columns(card_id, column) VALUES(?, ?)",
			(card_id, column)
		)
		await self._db.commit()
		_logger.debug(f"Card column set: card_id={card_id}, column='{column}'")
		return cur.lastrowid

	async def list_card_columns(self, card_id: Optional[int] = None) -> List[Dict[str, Any]]:
		"""
		Получает список адресов столбцов для карт.
		
		Args:
			card_id: Опциональный ID карты для фильтрации
		
		Returns:
			Список словарей с полями: id, card_id, card_name, column
		"""
		assert self._db
		if card_id is not None:
			query = """
				SELECT cc.id, cc.card_id, c.name, cc.column
				FROM card_columns cc
				JOIN cards c ON c.id = cc.card_id
				WHERE cc.card_id = ?
				ORDER BY cc.id
			"""
			cur = await self._db.execute(query, (card_id,))
		else:
			query = """
				SELECT cc.id, cc.card_id, c.name, cc.column
				FROM card_columns cc
				JOIN cards c ON c.id = cc.card_id
				ORDER BY cc.card_id, cc.id
			"""
			cur = await self._db.execute(query)
		
		rows = await cur.fetchall()
		return [
			{
				"id": row[0],
				"card_id": row[1],
				"card_name": row[2],
				"column": row[3],
			}
			for row in rows
		]

	async def delete_card_column(self, column_id: int) -> None:
		"""
		Удаляет адрес столбца по ID.
		
		Args:
			column_id: ID записи в таблице card_columns
		"""
		assert self._db
		await self._db.execute("DELETE FROM card_columns WHERE id = ?", (column_id,))
		await self._db.commit()
		_logger.debug(f"Card column deleted: id={column_id}")

	async def get_crypto_column(self, crypto_type: str) -> Optional[str]:
		"""
		Получает адрес столбца для криптовалюты.
		
		Args:
			crypto_type: Тип криптовалюты (BTC, LTC, XMR-1, XMR-2, XMR-3, USDT)
		
		Returns:
			Адрес столбца (например, "AS", "AY", "AU") или None, если не найдено
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT column FROM crypto_columns WHERE crypto_type = ?",
			(crypto_type,)
		)
		row = await cur.fetchone()
		if row:
			_logger.debug(f"✅ Найден адрес столбца: crypto_type='{crypto_type}' -> column='{row[0]}'")
			return row[0]
		_logger.debug(f"❌ Не найден адрес столбца для crypto_type='{crypto_type}'")
		return None

	async def set_crypto_column(self, crypto_type: str, column: str) -> int:
		"""
		Устанавливает адрес столбца для криптовалюты.
		
		Args:
			crypto_type: Тип криптовалюты (BTC, LTC, XMR-1, XMR-2, XMR-3, USDT)
			column: Адрес столбца (например, "AS", "AY", "AU")
		
		Returns:
			ID созданной или обновленной записи
		"""
		assert self._db
		# Используем INSERT OR REPLACE для обновления существующей записи
		cur = await self._db.execute(
			"INSERT OR REPLACE INTO crypto_columns (crypto_type, column) VALUES (?, ?)",
			(crypto_type, column)
		)
		await self._db.commit()
		_logger.debug(f"Crypto column set: crypto_type='{crypto_type}', column='{column}'")
		return cur.lastrowid

	async def list_crypto_columns(self) -> List[Dict[str, Any]]:
		"""
		Получает список всех адресов столбцов криптовалют.
		
		Returns:
			Список словарей с ключами: crypto_type, column
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT crypto_type, column FROM crypto_columns ORDER BY crypto_type"
		)
		rows = await cur.fetchall()
		return [
			{
				"crypto_type": row[0],
				"column": row[1],
			}
			for row in rows
		]

	async def delete_crypto_column(self, crypto_type: str) -> None:
		"""
		Удаляет адрес столбца для криптовалюты.
		
		Args:
			crypto_type: Тип криптовалюты (BTC, LTC, XMR-1, XMR-2, XMR-3, USDT)
		"""
		assert self._db
		await self._db.execute("DELETE FROM crypto_columns WHERE crypto_type = ?", (crypto_type,))
		await self._db.commit()
		_logger.debug(f"Crypto column deleted: crypto_type='{crypto_type}'")

	async def get_cash_column(self, cash_name: str) -> Optional[Dict[str, Any]]:
		"""
		Получает полную информацию о наличных.
		
		Args:
			cash_name: Название наличных
		
		Returns:
			Словарь с ключами: column, currency, display_name или None, если не найдено
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT column, currency, display_name FROM cash_columns WHERE cash_name = ?",
			(cash_name,)
		)
		row = await cur.fetchone()
		if row:
			return {
				"column": row[0],
				"currency": row[1] or "RUB",
				"display_name": row[2] or ""
			}
		_logger.debug(f"Cash column not found for cash_name='{cash_name}'")
		return None

	async def set_cash_column(self, cash_name: str, column: str, currency: Optional[str] = None, display_name: Optional[str] = None) -> int:
		"""
		Устанавливает адрес столбца для наличных.
		
		Args:
			cash_name: Название наличных
			column: Адрес столбца (например, "AS", "AY", "AU")
			currency: Номинал валюты (например, "BYN", "RUB", "$") - опционально
			display_name: Имя валюты (например, "🐿", "💵") - опционально
		
		Returns:
			ID созданной или обновленной записи
		"""
		assert self._db
		# Если currency или display_name не указаны, получаем текущие значения
		if currency is None or display_name is None:
			current = await self.get_cash_column(cash_name)
			if current:
				if currency is None:
					currency = current.get("currency", "RUB")
				if display_name is None:
					display_name = current.get("display_name", "")
			else:
				if currency is None:
					currency = "RUB"
				if display_name is None:
					display_name = ""
		
		# Используем INSERT OR REPLACE для обновления существующей записи
		cur = await self._db.execute(
			"INSERT OR REPLACE INTO cash_columns (cash_name, column, currency, display_name) VALUES (?, ?, ?, ?)",
			(cash_name, column, currency, display_name)
		)
		await self._db.commit()
		_logger.debug(f"Cash column set: cash_name='{cash_name}', column='{column}', currency='{currency}', display_name='{display_name}'")
		return cur.lastrowid
	
	async def update_cash_currency(self, cash_name: str, currency: str) -> None:
		"""
		Обновляет номинал валюты для наличных.
		
		Args:
			cash_name: Название наличных
			currency: Номинал валюты (например, "BYN", "RUB", "$")
		"""
		assert self._db
		await self._db.execute(
			"UPDATE cash_columns SET currency = ? WHERE cash_name = ?",
			(currency, cash_name)
		)
		await self._db.commit()
		_logger.debug(f"Cash currency updated: cash_name='{cash_name}', currency='{currency}'")
	
	async def update_cash_display_name(self, cash_name: str, display_name: str) -> None:
		"""
		Обновляет имя валюты (emoji) для наличных.
		
		Args:
			cash_name: Название наличных
			display_name: Имя валюты (например, "🐿", "💵")
		"""
		assert self._db
		await self._db.execute(
			"UPDATE cash_columns SET display_name = ? WHERE cash_name = ?",
			(display_name, cash_name)
		)
		await self._db.commit()
		_logger.debug(f"Cash display_name updated: cash_name='{cash_name}', display_name='{display_name}'")

	async def list_cash_columns(self) -> List[Dict[str, Any]]:
		"""
		Получает список всех адресов столбцов наличных.
		
		Returns:
			Список словарей с ключами: cash_name, column, currency, display_name
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT cash_name, column, currency, display_name FROM cash_columns ORDER BY cash_name"
		)
		rows = await cur.fetchall()
		return [
			{
				"cash_name": row[0],
				"column": row[1],
				"currency": row[2] or "RUB",
				"display_name": row[3] or ""
			}
			for row in rows
		]

	async def delete_cash_column(self, cash_name: str) -> None:
		"""
		Удаляет адрес столбца для наличных.
		
		Args:
			cash_name: Название наличных
		"""
		assert self._db
		await self._db.execute("DELETE FROM cash_columns WHERE cash_name = ?", (cash_name,))
		await self._db.commit()
		_logger.debug(f"Cash column deleted: cash_name='{cash_name}'")

	async def add_card_group(self, name: str) -> int:
		"""
		Создает новую группу карт.
		
		Args:
			name: Название группы
		
		Returns:
			ID созданной группы
		"""
		assert self._db
		cur = await self._db.execute(
			"INSERT INTO card_groups(name) VALUES(?)",
			(name,)
		)
		await self._db.commit()
		_logger.debug(f"Card group added: id={cur.lastrowid} name={name!r}")
		return cur.lastrowid

	async def list_card_groups(self) -> List[Dict[str, Any]]:
		"""
		Получает список всех групп карт.
		
		Returns:
			Список словарей с ключами: id, name, created_at
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT id, name, created_at FROM card_groups ORDER BY name"
		)
		rows = await cur.fetchall()
		return [
			{
				"id": row[0],
				"name": row[1],
				"created_at": row[2]
			}
			for row in rows
		]
	
	async def get_card_group_by_id(self, group_id: int) -> Optional[Dict[str, Any]]:
		"""
		Получает группу карт по ID.
		
		Args:
			group_id: ID группы карт
			
		Returns:
			Словарь с ключами: id, name, created_at или None, если группа не найдена
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT id, name, created_at FROM card_groups WHERE id = ?",
			(group_id,)
		)
		row = await cur.fetchone()
		if row:
			return {
				"id": row[0],
				"name": row[1],
				"created_at": row[2]
			}
		return None
		rows = await cur.fetchall()
		return [
			{
				"id": row[0],
				"name": row[1],
				"created_at": row[2],
			}
			for row in rows
		]

	async def get_card_group(self, group_id: int) -> Optional[Dict[str, Any]]:
		"""
		Получает информацию о группе карт по ID.
		
		Args:
			group_id: ID группы
		
		Returns:
			Словарь с информацией о группе или None, если не найдено
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT id, name, created_at FROM card_groups WHERE id = ?",
			(group_id,)
		)
		row = await cur.fetchone()
		if not row:
			return None
		return {
			"id": row[0],
			"name": row[1],
			"created_at": row[2],
		}

	async def set_card_group(self, card_id: int, group_id: Optional[int]) -> None:
		"""
		Привязывает карту к группе.
		
		Args:
			card_id: ID карты
			group_id: ID группы (None для удаления привязки)
		"""
		assert self._db
		await self._db.execute(
			"UPDATE cards SET group_id = ? WHERE id = ?",
			(group_id, card_id)
		)
		await self._db.commit()
		_logger.debug(f"Card {card_id} set to group {group_id}")

	async def delete_card_group(self, group_id: int) -> None:
		"""
		Удаляет группу карт (привязки карт к группе удаляются автоматически).
		
		Args:
			group_id: ID группы
		"""
		assert self._db
		# Сначала удаляем привязки карт к группе
		await self._db.execute(
			"UPDATE cards SET group_id = NULL WHERE group_id = ?",
			(group_id,)
		)
		# Затем удаляем саму группу
		await self._db.execute("DELETE FROM card_groups WHERE id = ?", (group_id,))
		await self._db.commit()
		_logger.debug(f"Card group deleted: id={group_id}")

	async def get_cards_by_group(self, group_id: int) -> List[Tuple[int, str, str]]:
		"""
		Получает список карт в группе.
		
		Args:
			group_id: ID группы
		
		Returns:
			Список кортежей (id, name, details)
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT id, name, details FROM cards WHERE group_id = ? ORDER BY name",
			(group_id,)
		)
		return await cur.fetchall()

	async def get_cards_without_group(self) -> List[Tuple[int, str, str]]:
		"""
		Получает список карт без группы.
		
		Returns:
			Список кортежей (id, name, details)
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT id, name, details FROM cards WHERE group_id IS NULL ORDER BY name"
		)
		return await cur.fetchall()

	async def _ensure_card_requisites(self) -> None:
		"""Создает таблицу для хранения нескольких реквизитов карты"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='card_requisites'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE card_requisites (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					card_id INTEGER NOT NULL,
					requisite_text TEXT NOT NULL,
					created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
					FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_card_requisites_card_id ON card_requisites(card_id)"
			)
			_logger.debug("Created table card_requisites")

	async def _ensure_rate_history(self) -> None:
		"""Создает таблицу для хранения истории операций /rate"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='rate_history'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE rate_history (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					created_at INTEGER NOT NULL,
					note TEXT,
					operations TEXT NOT NULL
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_rate_history_created_at ON rate_history(created_at DESC)"
			)
			_logger.debug("Created table rate_history")
	
	async def add_rate_history(self, operations: str, note: str = None) -> int:
		"""
		Добавляет запись в историю операций /rate.
		
		Args:
			operations: JSON строка с информацией о записанных ячейках и значениях
			note: Примечание (опционально)
		
		Returns:
			ID созданной записи
		"""
		assert self._db
		import time
		created_at = int(time.time())
		cur = await self._db.execute(
			"INSERT INTO rate_history(created_at, note, operations) VALUES(?, ?, ?)",
			(created_at, note, operations)
		)
		await self._db.commit()
		return cur.lastrowid
	
	async def get_last_rate_history(self) -> Optional[Dict[str, Any]]:
		"""
		Получает последнюю запись из истории операций /rate.
		
		Returns:
			Словарь с данными записи или None, если записей нет
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT id, created_at, note, operations FROM rate_history ORDER BY created_at DESC LIMIT 1"
		)
		row = await cur.fetchone()
		if row:
			return {
				"id": row[0],
				"created_at": row[1],
				"note": row[2],
				"operations": row[3]
			}
		return None
	
	async def delete_rate_history(self, history_id: int) -> bool:
		"""
		Удаляет запись из истории операций /rate.
		
		Args:
			history_id: ID записи для удаления
		
		Returns:
			True если запись удалена, False если не найдена
		"""
		assert self._db
		cur = await self._db.execute(
			"DELETE FROM rate_history WHERE id = ?",
			(history_id,)
		)
		await self._db.commit()
		return cur.rowcount > 0

	async def _ensure_item_usage_log(self) -> None:
		"""Создает таблицу для логирования использования элементов (криптовалют, карт, наличных)"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='item_usage_log'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE item_usage_log (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					admin_id INTEGER NOT NULL,
					item_type TEXT NOT NULL,
					item_id TEXT NOT NULL,
					used_at INTEGER NOT NULL,
					UNIQUE(admin_id, item_type, item_id)
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_item_usage_log_admin_type ON item_usage_log(admin_id, item_type, used_at DESC)"
			)
			_logger.debug("Created table item_usage_log")

	async def log_item_usage(self, admin_id: int, item_type: str, item_id: str) -> None:
		"""
		Логирует использование элемента (криптовалюта, карта, наличные) администратором.
		
		Args:
			admin_id: ID администратора
			item_type: Тип элемента ("crypto", "card", "cash")
			item_id: ID элемента (например, "BTC", "XMR-1", "card_id_123", "BYN")
		"""
		assert self._db
		timestamp = int(time.time())
		await self._db.execute(
			"""
			INSERT OR REPLACE INTO item_usage_log(admin_id, item_type, item_id, used_at)
			VALUES(?, ?, ?, ?)
			""",
			(admin_id, item_type, item_id, timestamp)
		)
		await self._db.commit()
		_logger.debug(f"Logged item usage: admin_id={admin_id}, type={item_type}, id={item_id}")

	async def get_recent_items_by_admin(self, admin_id: int, limit: int = 6) -> List[Dict[str, Any]]:
		"""
		Получает последние используемые элементы всех типов для администратора.
		
		Args:
			admin_id: ID администратора
			limit: Максимальное количество элементов (по умолчанию 6)
		
		Returns:
			Список словарей с ключами: item_type, item_id, used_at
			Отсортирован по времени использования (новые первыми)
		"""
		assert self._db
		cur = await self._db.execute(
			"""
			SELECT item_type, item_id, used_at
			FROM item_usage_log
			WHERE admin_id = ?
			ORDER BY used_at DESC
			LIMIT ?
			""",
			(admin_id, limit)
		)
		rows = await cur.fetchall()
		return [
			{
				"item_type": row[0],
				"item_id": row[1],
				"used_at": row[2]
			}
			for row in rows
		]

	async def _ensure_google_sheets_settings(self) -> None:
		"""Создает таблицу для хранения настроек Google Sheets"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='google_sheets_settings'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE google_sheets_settings (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					key TEXT NOT NULL UNIQUE,
					value TEXT NOT NULL
				)
				"""
			)
			# Устанавливаем значения по умолчанию
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('delete_range', 'A:BB')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('zero_column', 'BC')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('start_row', '5')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('add_max_row', '374')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('rate_start_row', '407')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('rate_max_row', '419')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('rate_start_row', '348')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('balance_row', '4')"
			)
			await self._db.execute(
				"INSERT INTO google_sheets_settings(key, value) VALUES('profit_column', 'BC')"
			)
			# Добавляем настройки дней недели
			day_settings = [
				('add_monday_start', '5'), ('add_monday_max', '54'),
				('add_tuesday_start', '55'), ('add_tuesday_max', '104'),
				('add_wednesday_start', '105'), ('add_wednesday_max', '154'),
				('add_thursday_start', '155'), ('add_thursday_max', '204'),
				('add_friday_start', '205'), ('add_friday_max', '254'),
				('add_saturday_start', '255'), ('add_saturday_max', '304'),
				('add_sunday_start', '305'), ('add_sunday_max', '364'),
				('move_start_row', '375'), ('move_max_row', '406'),
				('profit_monday', 'BD25'),
				('profit_tuesday', 'BD75'),
				('profit_wednesday', 'BD125'),
				('profit_thursday', 'BD175'),
				('profit_friday', 'BD225'),
				('profit_saturday', 'BD275'),
				('profit_sunday', 'BD325'),
			]
			for key, value in day_settings:
				await self._db.execute(
					f"INSERT INTO google_sheets_settings(key, value) VALUES('{key}', '{value}')"
				)
			_logger.debug("Created table google_sheets_settings with default values")
		else:
			# Проверяем наличие всех необходимых ключей
			cur = await self._db.execute("SELECT key FROM google_sheets_settings")
			existing_keys = {row[0] for row in await cur.fetchall()}
			
			if 'delete_range' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('delete_range', 'A:BB')"
				)
			if 'zero_column' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('zero_column', 'BC')"
				)
			if 'start_row' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('start_row', '5')"
				)
			if 'add_max_row' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('add_max_row', '374')"
				)
			if 'rate_start_row' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('rate_start_row', '407')"
				)
			if 'rate_max_row' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('rate_max_row', '419')"
				)
			if 'rate_start_row' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('rate_start_row', '348')"
				)
				# Миграция: если есть старый ключ rate_last_row, удаляем его
				if 'rate_last_row' in existing_keys:
					await self._db.execute(
						"DELETE FROM google_sheets_settings WHERE key = 'rate_last_row'"
					)
			if 'balance_row' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('balance_row', '4')"
				)
			if 'profit_column' not in existing_keys:
				await self._db.execute(
					"INSERT INTO google_sheets_settings(key, value) VALUES('profit_column', 'BC')"
				)
			
			# Добавляем настройки дней недели, если их нет
			day_settings = [
				('add_monday_start', '5'), ('add_monday_max', '54'),
				('add_tuesday_start', '55'), ('add_tuesday_max', '104'),
				('add_wednesday_start', '105'), ('add_wednesday_max', '154'),
				('add_thursday_start', '155'), ('add_thursday_max', '204'),
				('add_friday_start', '205'), ('add_friday_max', '254'),
				('add_saturday_start', '255'), ('add_saturday_max', '304'),
				('add_sunday_start', '305'), ('add_sunday_max', '364'),
				('move_start_row', '375'), ('move_max_row', '406'),
				('profit_monday', 'BD25'),
				('profit_tuesday', 'BD75'),
				('profit_wednesday', 'BD125'),
				('profit_thursday', 'BD175'),
				('profit_friday', 'BD225'),
				('profit_saturday', 'BD275'),
				('profit_sunday', 'BD325'),
			]
			for key, default_value in day_settings:
				if key not in existing_keys:
					await self._db.execute(
						f"INSERT INTO google_sheets_settings(key, value) VALUES('{key}', '{default_value}')"
					)

	async def get_google_sheets_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
		"""
		Получает значение настройки Google Sheets.
		
		Args:
			key: Ключ настройки (delete_range, zero_column, start_row)
			default: Значение по умолчанию, если настройка не найдена
		
		Returns:
			Значение настройки или default
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT value FROM google_sheets_settings WHERE key = ?",
			(key,)
		)
		row = await cur.fetchone()
		if row:
			return row[0]
		return default

	async def set_google_sheets_setting(self, key: str, value: str) -> None:
		"""
		Устанавливает значение настройки Google Sheets.
		
		Args:
			key: Ключ настройки (delete_range, zero_column, start_row)
			value: Значение настройки
		"""
		assert self._db
		await self._db.execute(
			"INSERT OR REPLACE INTO google_sheets_settings(key, value) VALUES(?, ?)",
			(key, value)
		)
		await self._db.commit()
		_logger.debug(f"Google Sheets setting set: {key}={value}")

	async def add_card_requisite(self, card_id: int, requisite_text: str) -> int:
		"""
		Добавляет реквизит для карты.
		
		Args:
			card_id: ID карты
			requisite_text: Текст реквизита
		
		Returns:
			ID созданной записи
		"""
		assert self._db
		cur = await self._db.execute(
			"INSERT INTO card_requisites(card_id, requisite_text) VALUES(?, ?)",
			(card_id, requisite_text)
		)
		await self._db.commit()
		_logger.debug(f"Card requisite added: card_id={card_id}, id={cur.lastrowid}")
		return cur.lastrowid

	async def list_card_requisites(self, card_id: int) -> List[Dict[str, Any]]:
		"""
		Получает список всех реквизитов для карты.
		
		Args:
			card_id: ID карты
		
		Returns:
			Список словарей с полями: id, card_id, requisite_text, created_at
		"""
		assert self._db
		cur = await self._db.execute(
			"SELECT id, card_id, requisite_text, created_at FROM card_requisites WHERE card_id = ? ORDER BY created_at",
			(card_id,)
		)
		rows = await cur.fetchall()
		result = [
			{
				"id": row[0],
				"card_id": row[1],
				"requisite_text": row[2],
				"created_at": row[3],
			}
			for row in rows
		]
		_logger.debug(f"list_card_requisites: card_id={card_id}, found {len(result)} requisites")
		return result

	async def update_card_requisite(self, requisite_id: int, requisite_text: str) -> None:
		"""
		Обновляет текст реквизита по ID.
		
		Args:
			requisite_id: ID реквизита
			requisite_text: Новый текст реквизита
		"""
		assert self._db
		await self._db.execute(
			"UPDATE card_requisites SET requisite_text = ? WHERE id = ?",
			(requisite_text, requisite_id)
		)
		await self._db.commit()
		_logger.debug(f"Card requisite updated: id={requisite_id}")
	
	async def delete_card_requisite(self, requisite_id: int) -> None:
		"""
		Удаляет реквизит по ID.
		
		Args:
			requisite_id: ID реквизита
		"""
		assert self._db
		await self._db.execute("DELETE FROM card_requisites WHERE id = ?", (requisite_id,))
		await self._db.commit()
		_logger.debug(f"Card requisite deleted: id={requisite_id}")
	
