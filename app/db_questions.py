# Методы для работы с вопросами - добавить в конец app/db.py

	async def _ensure_questions_table(self) -> None:
		"""Создает таблицу для хранения вопросов пользователей"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='questions'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE questions (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					question_number INTEGER NOT NULL,
					user_tg_id INTEGER NOT NULL,
					user_name TEXT,
					user_username TEXT,
					question_text TEXT NOT NULL,
					created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
					completed_at INTEGER,
					admin_message_id INTEGER,
					user_message_id INTEGER,
					FOREIGN KEY (user_tg_id) REFERENCES users(tg_id)
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_questions_user_tg_id ON questions(user_tg_id)"
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_questions_created_at ON questions(created_at)"
			)
			_logger.debug("Created table questions")
		else:
			# Проверяем наличие полей и добавляем их, если нужно
			cur = await self._db.execute("PRAGMA table_info(questions)")
			columns = [row[1] for row in await cur.fetchall()]
			if 'admin_message_id' not in columns:
				await self._db.execute("ALTER TABLE questions ADD COLUMN admin_message_id INTEGER")
				_logger.debug("Added column admin_message_id to questions table")
			if 'user_message_id' not in columns:
				await self._db.execute("ALTER TABLE questions ADD COLUMN user_message_id INTEGER")
				_logger.debug("Added column user_message_id to questions table")
	
	async def _ensure_question_messages_table(self) -> None:
		"""Создает таблицу для хранения сообщений по вопросам"""
		assert self._db
		cur = await self._db.execute(
			"SELECT name FROM sqlite_master WHERE type='table' AND name='question_messages'"
		)
		if not await cur.fetchone():
			await self._db.execute(
				"""
				CREATE TABLE question_messages (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					question_id INTEGER NOT NULL,
					sender_type TEXT NOT NULL,
					message_text TEXT NOT NULL,
					created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
					FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
				)
				"""
			)
			await self._db.execute(
				"CREATE INDEX IF NOT EXISTS idx_question_messages_question_id ON question_messages(question_id)"
			)
			_logger.debug("Created table question_messages")
	
	async def create_question(
		self,
		user_tg_id: int,
		user_name: str,
		user_username: str,
		question_text: str,
		admin_message_id: int = None,
	) -> int:
		"""
		Создает новый вопрос и возвращает его ID.
		Номер вопроса - это количество вопросов за сегодня + 1.
		"""
		assert self._db
		
		# Получаем количество вопросов за сегодня
		today_start = int(time.time()) - (int(time.time()) % 86400)
		cur = await self._db.execute(
			"SELECT COUNT(*) FROM questions WHERE created_at >= ?",
			(today_start,)
		)
		count = (await cur.fetchone())[0]
		question_number = count + 1
		
		# Создаем вопрос
		cur = await self._db.execute(
			"""
			INSERT INTO questions (question_number, user_tg_id, user_name, user_username, question_text, admin_message_id)
			VALUES (?, ?, ?, ?, ?, ?)
			""",
			(question_number, user_tg_id, user_name, user_username, question_text, admin_message_id)
		)
		await self._db.commit()
		question_id = cur.lastrowid
		
		# Сохраняем первое сообщение (вопрос пользователя)
		await self.add_question_message(question_id, "user", question_text)
		
		return question_id
	
	async def get_question_by_id(self, question_id: int) -> Optional[Dict[str, Any]]:
		"""Получает вопрос по ID"""
		assert self._db
		cur = await self._db.execute(
			"""
			SELECT id, question_number, user_tg_id, user_name, user_username, question_text,
				created_at, completed_at, admin_message_id, user_message_id
			FROM questions
			WHERE id = ?
			""",
			(question_id,)
		)
		row = await cur.fetchone()
		if not row:
			return None
		return {
			"id": row[0],
			"question_number": row[1],
			"user_tg_id": row[2],
			"user_name": row[3],
			"user_username": row[4],
			"question_text": row[5],
			"created_at": row[6],
			"completed_at": row[7],
			"admin_message_id": row[8],
			"user_message_id": row[9] if len(row) > 9 else None,
		}
	
	async def add_question_message(
		self,
		question_id: int,
		sender_type: str,
		message_text: str,
	) -> int:
		"""Добавляет сообщение в историю вопроса"""
		assert self._db
		cur = await self._db.execute(
			"""
			INSERT INTO question_messages (question_id, sender_type, message_text)
			VALUES (?, ?, ?)
			""",
			(question_id, sender_type, message_text)
		)
		await self._db.commit()
		message_id = cur.lastrowid
		_logger.debug(f"Added question message: question_id={question_id}, sender_type={sender_type}, message_id={message_id}")
		return message_id
	
	async def get_question_messages(self, question_id: int) -> List[Dict[str, Any]]:
		"""Получает все сообщения по вопросу"""
		assert self._db
		cur = await self._db.execute(
			"""
			SELECT id, sender_type, message_text, created_at
			FROM question_messages
			WHERE question_id = ?
			ORDER BY created_at ASC
			""",
			(question_id,)
		)
		rows = await cur.fetchall()
		return [
			{
				"id": row[0],
				"sender_type": row[1],
				"message_text": row[2],
				"created_at": row[3],
			}
			for row in rows
		]
	
	async def update_question_admin_message_id(self, question_id: int, admin_message_id: int) -> bool:
		"""Обновляет admin_message_id для вопроса"""
		assert self._db
		cur = await self._db.execute(
			"UPDATE questions SET admin_message_id = ? WHERE id = ?",
			(admin_message_id, question_id)
		)
		await self._db.commit()
		return cur.rowcount > 0
	
	async def update_question_user_message_id(self, question_id: int, user_message_id: int) -> bool:
		"""Обновляет user_message_id для вопроса"""
		assert self._db
		cur = await self._db.execute(
			"UPDATE questions SET user_message_id = ? WHERE id = ?",
			(user_message_id, question_id)
		)
		await self._db.commit()
		return cur.rowcount > 0
	
	async def get_active_question_by_user(self, user_tg_id: int) -> Optional[int]:
		"""Получает ID последнего активного (незавершенного) вопроса пользователя"""
		assert self._db
		cur = await self._db.execute(
			"""
			SELECT id FROM questions
			WHERE user_tg_id = ? AND completed_at IS NULL
			ORDER BY created_at DESC
			LIMIT 1
			""",
			(user_tg_id,)
		)
		row = await cur.fetchone()
		return row[0] if row else None
