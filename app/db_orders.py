# Временный файл для добавления методов в db.py

async def _ensure_orders_table(self) -> None:
	"""Создает таблицу для хранения заявок на покупку"""
	assert self._db
	cur = await self._db.execute(
		"SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
	)
	if not await cur.fetchone():
		await self._db.execute(
			"""
			CREATE TABLE orders (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				order_number INTEGER NOT NULL,
				user_tg_id INTEGER NOT NULL,
				user_name TEXT,
				user_username TEXT,
				crypto_type TEXT NOT NULL,
				crypto_display TEXT NOT NULL,
				amount REAL NOT NULL,
				wallet_address TEXT NOT NULL,
				amount_currency REAL NOT NULL,
				currency_symbol TEXT NOT NULL,
				delivery_method TEXT,
				proof_photo_file_id TEXT,
				proof_document_file_id TEXT,
				created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
				completed_at INTEGER,
				FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
			)
			"""
		)
		await self._db.execute(
			"CREATE INDEX IF NOT EXISTS idx_orders_user_tg_id ON orders(user_tg_id)"
		)
		await self._db.execute(
			"CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at)"
		)
		_logger.debug("Created table orders")

async def create_order(
	self,
	user_tg_id: int,
	user_name: str,
	user_username: str,
	crypto_type: str,
	crypto_display: str,
	amount: float,
	wallet_address: str,
	amount_currency: float,
	currency_symbol: str,
	delivery_method: str,
	proof_photo_file_id: str = None,
	proof_document_file_id: str = None,
) -> int:
	"""
	Создает новую заявку и возвращает её ID.
	Номер заявки - это количество заявок за сегодня + 1.
	"""
	assert self._db
	
	# Получаем количество заявок за сегодня
	today_start = int(time.time()) - (int(time.time()) % 86400)  # Начало дня (00:00:00)
	cur = await self._db.execute(
		"SELECT COUNT(*) FROM orders WHERE created_at >= ?",
		(today_start,)
	)
	today_orders_count = (await cur.fetchone())[0]
	order_number = today_orders_count + 1
	
	# Создаем заявку
	cur = await self._db.execute(
		"""
		INSERT INTO orders (
			order_number, user_tg_id, user_name, user_username,
			crypto_type, crypto_display, amount, wallet_address,
			amount_currency, currency_symbol, delivery_method,
			proof_photo_file_id, proof_document_file_id
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			order_number, user_tg_id, user_name, user_username,
			crypto_type, crypto_display, amount, wallet_address,
			amount_currency, currency_symbol, delivery_method,
			proof_photo_file_id, proof_document_file_id
		)
	)
	await self._db.commit()
	order_id = cur.lastrowid
	_logger.debug(f"Created order: id={order_id}, order_number={order_number}, user_tg_id={user_tg_id}")
	return order_id

async def get_order_by_id(self, order_id: int) -> Optional[Dict[str, Any]]:
	"""Получает заявку по ID"""
	assert self._db
	cur = await self._db.execute(
		"""
		SELECT id, order_number, user_tg_id, user_name, user_username,
		       crypto_type, crypto_display, amount, wallet_address,
		       amount_currency, currency_symbol, delivery_method,
		       proof_photo_file_id, proof_document_file_id,
		       created_at, completed_at
		FROM orders WHERE id = ?
		""",
		(order_id,)
	)
	row = await cur.fetchone()
	if not row:
		return None
	return {
		"id": row[0],
		"order_number": row[1],
		"user_tg_id": row[2],
		"user_name": row[3],
		"user_username": row[4],
		"crypto_type": row[5],
		"crypto_display": row[6],
		"amount": row[7],
		"wallet_address": row[8],
		"amount_currency": row[9],
		"currency_symbol": row[10],
		"delivery_method": row[11],
		"proof_photo_file_id": row[12],
		"proof_document_file_id": row[13],
		"created_at": row[14],
		"completed_at": row[15],
	}

async def complete_order(self, order_id: int) -> bool:
	"""Отмечает заявку как выполненную"""
	assert self._db
	cur = await self._db.execute(
		"UPDATE orders SET completed_at = ? WHERE id = ?",
		(int(time.time()), order_id)
	)
	await self._db.commit()
	return cur.rowcount > 0

