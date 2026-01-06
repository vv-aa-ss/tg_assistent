import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram import F

from app.config import get_settings
from app.db import Database
from app.admin import admin_router, is_admin
from app.keyboards import admin_menu_kb, client_menu_kb, buy_country_kb
from app.di import set_dependencies


class BuyStates(StatesGroup):
	"""Состояния для процесса покупки криптовалюты"""
	waiting_crypto_amount = State()  # Ожидание ввода суммы


async def delete_previous_bot_message(bot: Bot, chat_id: int, message_id: int | None):
	"""
	Безопасно удаляет предыдущее сообщение бота.
	
	Args:
		bot: Экземпляр бота
		chat_id: ID чата
		message_id: ID сообщения для удаления (может быть None)
	"""
	if message_id is None:
		return
	try:
		await bot.delete_message(chat_id=chat_id, message_id=message_id)
	except Exception as e:
		# Игнорируем ошибки удаления (сообщение уже удалено, не найдено и т.д.)
		pass


async def delete_user_message(message: Message):
	"""
	Безопасно удаляет сообщение пользователя.
	Бот может удалять сообщения пользователя только если они были отправлены менее 48 часов назад.
	
	Args:
		message: Объект сообщения пользователя
	"""
	try:
		await message.delete()
	except Exception as e:
		# Игнорируем ошибки удаления (сообщение слишком старое, уже удалено и т.д.)
		pass


async def send_and_save_message(message: Message, text: str, reply_markup=None, state: FSMContext = None):
	"""
	Отправляет сообщение, удаляет предыдущее сообщение бота и сохраняет ID нового сообщения.
	
	Args:
		message: Объект сообщения (для получения bot и chat_id)
		text: Текст сообщения
		reply_markup: Клавиатура (опционально)
		state: Состояние FSM для сохранения message_id (опционально)
	
	Returns:
		Отправленное сообщение
	"""
	bot = message.bot
	chat_id = message.chat.id
	
	# Получаем ID предыдущего сообщения из состояния
	previous_message_id = None
	if state:
		data = await state.get_data()
		previous_message_id = data.get("last_bot_message_id")
	
	# Отправляем новое сообщение с клавиатурой
	# Если у нового сообщения есть клавиатура, отправляем его сразу
	# Telegram автоматически заменит старую клавиатуру новой
	sent_message = await bot.send_message(
		chat_id=chat_id, 
		text=text, 
		reply_markup=reply_markup
	)
	
	# Удаляем предыдущее сообщение только после того, как новое отправлено
	# и клавиатура точно показана (небольшая задержка для стабильности)
	if previous_message_id:
		# Удаляем в фоне с небольшой задержкой, чтобы клавиатура успела появиться
		async def delayed_delete():
			await asyncio.sleep(0.2)
			await delete_previous_bot_message(bot, chat_id, previous_message_id)
		asyncio.create_task(delayed_delete())
	
	# Сохраняем ID нового сообщения в состоянии
	if state:
		await state.update_data(last_bot_message_id=sent_message.message_id)
	
	return sent_message


async def main() -> None:
	os.makedirs("logs", exist_ok=True)
	settings = get_settings()

	# Настройка логирования (с ротацией, чтобы logs/bot.log не раздувался)
	log_level_name = (settings.log_level or "INFO").upper()
	log_level = getattr(logging, log_level_name, logging.INFO)

	log_file_handler = RotatingFileHandler(
		"logs/bot.log",
		maxBytes=5 * 1024 * 1024,  # 5 MB
		backupCount=5,
		encoding="utf-8",
	)

	logging.basicConfig(
		level=log_level,
		format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
		handlers=[log_file_handler],
	)

	# Приглушаем сторонние библиотеки (они часто шумят на DEBUG)
	logging.getLogger("aiosqlite").setLevel(logging.WARNING)
	logging.getLogger("urllib3").setLevel(logging.WARNING)
	logging.getLogger("gspread").setLevel(logging.WARNING)

	logger = logging.getLogger("app.start")
	logger.debug(f"Loaded settings: db={settings.database_path}, admins={settings.admin_ids}")
	if not settings.telegram_bot_token:
		raise RuntimeError("TELEGRAM_BOT_TOKEN не задан. Создайте .env с токеном.")

	db = Database(settings.database_path)
	await db.connect()
	set_dependencies(db, settings.admin_ids, settings.admin_usernames)
	logger.debug("Database connected and dependencies set")

	bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
	dp = Dispatcher(storage=MemoryStorage())
	
	# Определяем команды для админов
	from aiogram.types import BotCommand, BotCommandScopeDefault
	admin_commands = [
		BotCommand(command="add", description="Операция"),
		BotCommand(command="rate", description="Расход"),
		BotCommand(command="del_rate", description="Удалить расход"),
		BotCommand(command="move", description="Пердвижение средств"),
		BotCommand(command="del", description="Удалить последнюю операцию"),
		BotCommand(command="del_move", description="Удалить последнее передвижение"),
		BotCommand(command="stat_bk", description="Балансы карт"),
		BotCommand(command="stat_k", description="Баланс крипты"),
		BotCommand(command="stat_u", description="Статистика пользователей"),
		BotCommand(command="cons", description="Статистика расходов"),
		BotCommand(command="start", description="Меню"),
	]

	# Команды для пользователей (чтобы появлялась кнопка "Меню" в чате)
	user_commands = [
		BotCommand(command="start", description="Меню"),
		BotCommand(command="buy", description="Купить"),
		BotCommand(command="sell", description="Продать"),
	]
	
	# Скрываем команды для всех пользователей по умолчанию
	try:
		await bot.set_my_commands(commands=[], scope=BotCommandScopeDefault())
		logger.info("✅ Команды скрыты для обычных пользователей")
	except Exception as e:
		logger.warning(f"⚠️ Не удалось скрыть команды для обычных пользователей: {e}")
	
	# Middleware для логирования всех сообщений
	class LoggingMiddleware:
		async def __call__(self, handler, event, data):
			if isinstance(event, Message):
				# Чтобы не раздувать лог, пишем это на DEBUG (и только если включен DEBUG)
				logger.debug(f"🟢 DISPATCHER: Получено сообщение message_id={event.message_id}, text='{event.text}', user_id={event.from_user.id if event.from_user else None}")
			return await handler(event, data)
	
	dp.message.middleware(LoggingMiddleware())

	@dp.message(CommandStart())
	async def on_start(message: Message, state):
		logger.debug(f"/start from user_id={getattr(message.from_user,'id',None)} username={getattr(message.from_user,'username',None)}")
		# Всегда регистрируем пользователя как "заявку" (даже если потом не отвечаем)
		if message.from_user:
			from app.di import get_db
			db_local = get_db()
			await db_local.get_or_create_user(
				message.from_user.id,
				message.from_user.username,
				message.from_user.full_name,
			)
			await db_local.touch_user_by_tg(message.from_user.id)

		if message.from_user and is_admin(
			message.from_user.id,
			message.from_user.username,
			settings.admin_ids,
			settings.admin_usernames
		):
			# Очищаем состояние FSM при вызове /start
			current_state = await state.get_state()
			if current_state:
				logger.debug(f"🧹 Очистка состояния при /start. Текущее состояние: {current_state}")
				await state.clear()
				logger.debug(f"✅ Состояние очищено при /start")
			
			# Устанавливаем команды для админа после начала диалога
			# Для личных чатов это работает только после того, как пользователь начал диалог
			try:
				from aiogram.types import BotCommandScopeChat
				await bot.set_my_commands(
					commands=admin_commands,
					scope=BotCommandScopeChat(chat_id=message.from_user.id)
				)
				logger.info(f"✅ Команды установлены для админа {message.from_user.id} после /start")
			except Exception as e:
				logger.warning(f"⚠️ Не удалось установить команды для админа {message.from_user.id}: {e}")
			
			await message.answer("Добро пожаловать, администратор!", reply_markup=admin_menu_kb())
			return

		# Пользователи из группы "Пользователи"
		if message.from_user:
			from app.di import get_db
			db_local = get_db()
			if await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
				# Устанавливаем команды для пользователя (чтобы было "Меню")
				try:
					from aiogram.types import BotCommandScopeChat
					await bot.set_my_commands(
						commands=user_commands,
						scope=BotCommandScopeChat(chat_id=message.from_user.id),
					)
				except Exception as e:
					logger.warning(f"⚠️ Не удалось установить команды для пользователя {message.from_user.id}: {e}")

				# Чистим FSM, чтобы пользователь не попадал в чужие состояния
				current_state = await state.get_state()
				if current_state:
					await state.clear()
				await send_and_save_message(
					message,
					"🔒 Сервис не поддерживает подозрительные или незаконные транзакции.\n"
					"🔞 Только для пользователей старше 18 лет.\n\n"
					"✅Выберите нужную функцию в меню ниже, чтобы начать работу.",
					reply_markup=client_menu_kb(),
					state=state
				)
				return

		# Остальные: игнор (без ответа)

	@dp.message(F.text.in_({"🚀 Купить", "⚡ Продать"}))
	async def on_client_menu_message(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		if message.text == "🚀 Купить":
			await send_and_save_message(message, "Выберите страну:", reply_markup=buy_country_kb(), state=state)
		elif message.text == "⚡ Продать":
			await send_and_save_message(message, "Вы выбрали: Продать", state=state)

	@dp.message(F.text == "⬅️ Назад")
	async def on_client_back(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Проверяем текущее состояние
		current_state = await state.get_state()
		if current_state == BuyStates.waiting_crypto_amount:
			# Если пользователь в процессе ввода суммы, возвращаем к выбору криптовалюты
			# Сохраняем last_bot_message_id перед очисткой состояния
			data = await state.get_data()
			last_bot_message_id = data.get("last_bot_message_id")
			from app.keyboards import buy_crypto_kb
			await state.clear()
			# Восстанавливаем last_bot_message_id после очистки
			if last_bot_message_id:
				await state.update_data(last_bot_message_id=last_bot_message_id)
			await send_and_save_message(message, "Выберите криптовалюту:", reply_markup=buy_crypto_kb(), state=state)
			return
		
		# Иначе возвращаем в главное меню
		# Сохраняем last_bot_message_id перед очисткой состояния
		data = await state.get_data()
		last_bot_message_id = data.get("last_bot_message_id")
		await state.clear()
		# Восстанавливаем last_bot_message_id после очистки
		if last_bot_message_id:
			await state.update_data(last_bot_message_id=last_bot_message_id)
		await send_and_save_message(
			message,
			"🔒 Сервис не поддерживает подозрительные или незаконные транзакции.\n"
			"🔞 Только для пользователей старше 18 лет.\n\n"
			"✅Выберите нужную функцию в меню ниже, чтобы начать работу.",
			reply_markup=client_menu_kb(),
			state=state
		)

	@dp.message(F.text.in_({"🇷🇺 Россия", "🇧🇾 Беларусь"}))
	async def on_buy_country_selected(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		# Сохраняем last_bot_message_id перед очисткой состояния
		data = await state.get_data()
		last_bot_message_id = data.get("last_bot_message_id")
		# Очищаем предыдущее состояние при выборе страны
		await state.clear()
		# Восстанавливаем last_bot_message_id после очистки
		if last_bot_message_id:
			await state.update_data(last_bot_message_id=last_bot_message_id)
		from app.keyboards import buy_crypto_kb
		await send_and_save_message(message, "Выберите криптовалюту:", reply_markup=buy_crypto_kb(), state=state)

	@dp.message(F.text.in_({"Bitcoin - BTC", "Litecoin - LTC", "USDT - TRC20", "Monero - XMR"}))
	async def on_buy_crypto_selected(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		# Сохраняем выбранную криптовалюту
		crypto_name = message.text
		# Извлекаем название монеты из текста (например, "Bitcoin - BTC" -> "Bitcoin" или "BTC")
		if " - " in crypto_name:
			crypto_display = crypto_name.split(" - ")[0]  # "Bitcoin", "Litecoin", "USDT", "Monero"
		else:
			crypto_display = crypto_name
		
		await state.update_data(selected_crypto=crypto_name, crypto_display=crypto_display)
		await state.set_state(BuyStates.waiting_crypto_amount)
		await send_and_save_message(message, f"✅ Введите нужную сумму в {crypto_display} или рублях.", state=state)

	@dp.message(BuyStates.waiting_crypto_amount)
	async def on_buy_amount_entered(message: Message, state: FSMContext):
		"""Обработчик ввода суммы для покупки криптовалюты"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Проверяем, не является ли это командой или кнопкой "Назад"
		if message.text == "⬅️ Назад":
			# Удаляем сообщение пользователя
			await delete_user_message(message)
			# Сохраняем last_bot_message_id перед очисткой состояния
			data = await state.get_data()
			last_bot_message_id = data.get("last_bot_message_id")
			from app.keyboards import buy_crypto_kb
			await state.clear()
			# Восстанавливаем last_bot_message_id после очистки
			if last_bot_message_id:
				await state.update_data(last_bot_message_id=last_bot_message_id)
			await send_and_save_message(message, "Выберите криптовалюту:", reply_markup=buy_crypto_kb(), state=state)
			return
		
		# Удаляем сообщение пользователя с введенной суммой
		await delete_user_message(message)
		
		# Получаем данные о выбранной криптовалюте
		data = await state.get_data()
		crypto_name = data.get("selected_crypto", "криптовалюте")
		crypto_display = data.get("crypto_display", "криптовалюте")
		last_bot_message_id = data.get("last_bot_message_id")
		
		# Здесь можно добавить логику обработки введенной суммы
		amount = message.text.strip()
		await send_and_save_message(message, f"Вы ввели сумму: {amount} для {crypto_display}", state=state)
		
		# Очищаем состояние после обработки, но сохраняем last_bot_message_id
		await state.clear()
		if last_bot_message_id:
			await state.update_data(last_bot_message_id=last_bot_message_id)

	@dp.message(Command("buy"))
	async def cmd_buy(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		# Очищаем состояние при начале новой покупки
		await state.clear()
		await send_and_save_message(message, "Выберите страну:", reply_markup=buy_country_kb(), state=state)

	@dp.message(Command("sell"))
	async def cmd_sell(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		await send_and_save_message(message, "Вы выбрали: Продать", state=state)

	# ВАЖНО: Сначала включаем admin_router, чтобы команды из него обрабатывались первыми
	dp.include_router(admin_router)

	# Регистрировать пользователя только когда нет активного состояния и сообщение не переслано
	# Исключаем команды - они обрабатываются отдельными обработчиками
	# ВАЖНО: Фильтр ~F.text.startswith("/") исключает команды на уровне декоратора
	@dp.message(
		~(F.forward_origin.as_(bool) | F.forward_from.as_(bool)),
		StateFilter(None),
		~(F.text.startswith("/") if F.text else False)
	)
	async def register_user_on_any_message(message: Message):
		logger.debug(f"🟡 MAIN register_user_on_any_message: message_id={message.message_id}, text='{message.text}', user_id={message.from_user.id if message.from_user else None}")
		
		from app.di import get_db
		logger_msg = logging.getLogger("app.msg")
		db_local = get_db()
		if message.from_user:
			logger_msg.debug(f"Ensure user: id={message.from_user.id} username={message.from_user.username} full_name={message.from_user.full_name}")
			await db_local.get_or_create_user(
				message.from_user.id,
				message.from_user.username,
				message.from_user.full_name,
			)
			await db_local.touch_user_by_tg(message.from_user.id)
		# не отвечаем
	logger.debug("Starting polling...")
	try:
		await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
	finally:
		logger.debug("Shutting down, closing DB")
		await db.close()


if __name__ == "__main__":
	asyncio.run(main())
