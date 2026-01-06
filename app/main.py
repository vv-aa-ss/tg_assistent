import asyncio
import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher
from aiogram.types import Message, ReplyKeyboardRemove, CallbackQuery
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
from app.keyboards import admin_menu_kb, client_menu_kb, buy_country_kb, buy_delivery_method_kb, buy_payment_confirmed_kb, order_action_kb
from app.di import get_admin_ids
from app.di import set_dependencies


class BuyStates(StatesGroup):
	"""Состояния для процесса покупки криптовалюты"""
	waiting_crypto_amount = State()  # Ожидание ввода суммы
	waiting_confirmation = State()  # Ожидание подтверждения сделки
	waiting_wallet_address = State()  # Ожидание ввода адреса кошелька
	waiting_delivery_method = State()  # Ожидание выбора способа доставки
	waiting_payment_confirmation = State()  # Ожидание подтверждения оплаты
	waiting_payment_proof = State()  # Ожидание скриншота/чека оплаты


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


def validate_wallet_address(address: str, crypto_type: str) -> bool:
	"""
	Валидирует адрес кошелька для указанной криптовалюты.
	
	Args:
		address: Адрес кошелька
		crypto_type: Тип криптовалюты (BTC, LTC, USDT, XMR)
	
	Returns:
		True если адрес валиден, False иначе
	"""
	address = address.strip()
	
	if crypto_type == "BTC":
		# Bitcoin адреса: начинаются с 1, 3, или bc1, длина 26-62 символа
		# Legacy (P2PKH): начинается с 1, 26-35 символов
		# P2SH: начинается с 3, 26-35 символов
		# Bech32 (P2WPKH/P2WSH): начинается с bc1, 14-74 символа
		if re.match(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$', address):
			return True
		if re.match(r'^bc1[a-z0-9]{13,62}$', address, re.IGNORECASE):
			return True
		return False
	
	elif crypto_type == "LTC":
		# Litecoin адреса: начинаются с L, M, или ltc1, длина 26-62 символа
		# Legacy: начинается с L, 26-34 символа
		# P2SH: начинается с M, 26-34 символа
		# Bech32: начинается с ltc1, 14-62 символа
		if re.match(r'^[LM][a-km-zA-HJ-NP-Z1-9]{25,33}$', address):
			return True
		if re.match(r'^ltc1[a-z0-9]{13,62}$', address, re.IGNORECASE):
			return True
		return False
	
	elif crypto_type == "USDT":
		# USDT TRC20 использует адреса Tron (TRX)
		# Tron адреса: начинаются с T, 34 символа
		if re.match(r'^T[A-Za-z1-9]{33}$', address):
			return True
		return False
	
	elif crypto_type == "XMR":
		# Monero адреса: начинаются с 4, 95 символов (стандартные) или 106 (интегрированные)
		# Формат: 95 символов (стандартный) или 106 (интегрированный)
		if re.match(r'^4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}$', address):
			return True
		if re.match(r'^4[0-9AB][1-9A-HJ-NP-Za-km-z]{104}$', address):
			return True
		return False
	
	return False


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
		# Сохраняем выбранную страну
		selected_country = "RUB" if message.text == "🇷🇺 Россия" else "BYN"
		# Сохраняем last_bot_message_id перед очисткой состояния
		data = await state.get_data()
		last_bot_message_id = data.get("last_bot_message_id")
		# Очищаем предыдущее состояние при выборе страны
		await state.clear()
		# Восстанавливаем last_bot_message_id и сохраняем выбранную страну
		if last_bot_message_id:
			await state.update_data(last_bot_message_id=last_bot_message_id, selected_country=selected_country)
		else:
			await state.update_data(selected_country=selected_country)
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
		crypto_name = data.get("selected_crypto", "")
		crypto_display = data.get("crypto_display", "")
		
		# Валидация и обработка введенной суммы
		amount_str = message.text.strip().replace(",", ".")  # Заменяем запятую на точку
		
		try:
			amount = float(amount_str)
			if amount <= 0:
				await send_and_save_message(message, "❌ Сумма должна быть больше нуля. Введите корректную сумму:", state=state)
				return
		except ValueError:
			await send_and_save_message(message, "❌ Неверный формат суммы. Введите число (например: 0.008 или 100):", state=state)
			return
		
		# Определяем тип криптовалюты для получения курса
		crypto_type = None
		if "BTC" in crypto_name or "Bitcoin" in crypto_name:
			crypto_type = "BTC"
			crypto_symbol = "₿"
		elif "LTC" in crypto_name or "Litecoin" in crypto_name:
			crypto_type = "LTC"
			crypto_symbol = "Ł"
		elif "USDT" in crypto_name:
			crypto_type = "USDT"
			crypto_symbol = "₮"
		elif "XMR" in crypto_name or "Monero" in crypto_name:
			crypto_type = "XMR"
			crypto_symbol = "ɱ"
		
		# Получаем курс криптовалюты в USD
		from app.google_sheets import get_btc_price_usd, get_ltc_price_usd, get_xmr_price_usd
		
		crypto_price_usd = None
		if crypto_type == "BTC":
			crypto_price_usd = await get_btc_price_usd()
		elif crypto_type == "LTC":
			crypto_price_usd = await get_ltc_price_usd()
		elif crypto_type == "USDT":
			crypto_price_usd = 1.0  # USDT равен 1 USD
		elif crypto_type == "XMR":
			crypto_price_usd = await get_xmr_price_usd()
		
		if crypto_price_usd is None:
			await send_and_save_message(message, "❌ Не удалось получить курс криптовалюты. Попробуйте позже.", state=state)
			return
		
		# Получаем выбранную страну из состояния
		selected_country = data.get("selected_country", "RUB")
		
		# Получаем курс USD к валюте (сколько единиц валюты за 1 USD)
		# Формула: (цена_монеты_в_USD + процент) × количество_монет × курс_валюты_к_USD
		if selected_country == "BYN":
			# Курс USD к BYN (1 USD = 3.00 BYN)
			usd_to_currency_rate = 3.0  # Можно получать из API
			currency_symbol = "Br"
		else:  # RUB
			# Курс USD к RUB (1 USD = ~95 RUB)
			usd_to_currency_rate = 95.0  # Можно получать из API
			currency_symbol = "₽"
		
		# Рассчитываем сумму заказа в USD для определения процента наценки
		amount_usd = amount * crypto_price_usd
		
		# Определяем процент наценки в зависимости от суммы заказа
		# Если сумма < 100 USD: используем markup_percent_small (по умолчанию 20%)
		# Если сумма >= 100 USD: используем markup_percent_large (по умолчанию 15%)
		if amount_usd < 100:
			markup_percent_key = "markup_percent_small"
			default_markup = 20
		else:
			markup_percent_key = "markup_percent_large"
			default_markup = 15
		
		# Получаем процент наценки из БД
		markup_percent_str = await db_local.get_google_sheets_setting(markup_percent_key, str(default_markup))
		try:
			markup_percent = float(markup_percent_str) if markup_percent_str else default_markup
		except (ValueError, TypeError):
			markup_percent = default_markup
		
		# Рассчитываем цену монеты с наценкой: цена_USD × (1 + процент/100)
		crypto_price_with_markup = crypto_price_usd * (1 + markup_percent / 100)
		
		# Рассчитываем итоговую сумму: (цена_с_наценкой) × количество × курс_валюты
		amount_currency = crypto_price_with_markup * amount * usd_to_currency_rate
		
		# Логируем расчет для отладки
		logger = logging.getLogger("app.main")
		logger.debug(f"Расчет: ({crypto_price_usd} USD + {markup_percent}%) × {amount} {crypto_type} × {usd_to_currency_rate} {currency_symbol}/USD = {amount_currency} {currency_symbol}")
		
		# Сохраняем данные о сделке
		await state.update_data(
			amount=amount,
			amount_currency=amount_currency,
			crypto_type=crypto_type,
			crypto_symbol=crypto_symbol,
			crypto_price_usd=crypto_price_usd,
			crypto_price_with_markup=crypto_price_with_markup,
			markup_percent=markup_percent,
			selected_country=selected_country,
			currency_symbol=currency_symbol,
			usd_to_currency_rate=usd_to_currency_rate
		)
		
		# Формируем сообщение с расчетом
		# Форматируем сумму с правильным количеством знаков после запятой
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		confirmation_text = (
			f"Вам будет зачислено: {amount_str} {crypto_display}\n"
			f"Вам необходимо оплатить: {int(amount_currency)} {currency_symbol}"
		)
		
		# Показываем сообщение с кнопками подтверждения
		from app.keyboards import buy_confirmation_kb
		await state.set_state(BuyStates.waiting_confirmation)
		# Для inline-клавиатуры используем обычный answer
		bot = message.bot
		chat_id = message.chat.id
		
		# Получаем ID предыдущего сообщения из состояния
		previous_message_id = None
		if state:
			data = await state.get_data()
			previous_message_id = data.get("last_bot_message_id")
		
		# Удаляем предыдущее сообщение
		if previous_message_id:
			try:
				await bot.delete_message(chat_id=chat_id, message_id=previous_message_id)
			except:
				pass
		
		# Отправляем новое сообщение с inline-клавиатурой
		sent_message = await bot.send_message(
			chat_id=chat_id,
			text=confirmation_text,
			reply_markup=buy_confirmation_kb()
		)
		
		# Сохраняем ID нового сообщения
		if state:
			await state.update_data(last_bot_message_id=sent_message.message_id)

	@dp.message(BuyStates.waiting_confirmation, F.text == "✅ Согласен")
	async def on_buy_confirm_yes(message: Message, state: FSMContext):
		"""Обработчик подтверждения покупки"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Получаем данные о заказе
		data = await state.get_data()
		amount = data.get("amount", 0)
		amount_currency = data.get("amount_currency", 0)
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		currency_symbol = data.get("currency_symbol", "")
		selected_country = data.get("selected_country", "RUB")
		
		# Форматируем суммы
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Показываем уведомление о заказе
		order_notification = (
			f"Вам будет зачислено: {amount_str} {crypto_display}\n"
			f"Вам необходимо оплатить: {int(amount_currency)} {currency_symbol}"
		)
		
		# Сохраняем ID предыдущего сообщения для удаления
		last_bot_message_id = data.get("last_bot_message_id")
		
		# Переходим в состояние ожидания адреса кошелька
		await state.set_state(BuyStates.waiting_wallet_address)
		
		# Объединяем уведомление о заказе и запрос адреса в одно сообщение
		wallet_request = f"Введите адрес кошелька для {crypto_display}:"
		combined_message = f"{order_notification}\n\n{wallet_request}"
		await send_and_save_message(message, combined_message, state=state)
	
	@dp.message(BuyStates.waiting_confirmation, F.text == "❌ Не согласен")
	async def on_buy_confirm_no(message: Message, state: FSMContext):
		"""Обработчик отказа от покупки"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Возвращаемся в главное меню
		from app.keyboards import client_menu_kb
		await state.clear()
		await send_and_save_message(message, "Выберите действие:", reply_markup=client_menu_kb(), state=state)
	
	@dp.message(BuyStates.waiting_wallet_address)
	async def on_wallet_address_entered(message: Message, state: FSMContext):
		"""Обработчик ввода адреса кошелька"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Получаем данные о заказе
		data = await state.get_data()
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		amount = data.get("amount", 0)
		amount_currency = data.get("amount_currency", 0)
		currency_symbol = data.get("currency_symbol", "")
		selected_country = data.get("selected_country", "RUB")
		
		# Валидируем адрес кошелька
		wallet_address = message.text.strip()
		if not validate_wallet_address(wallet_address, crypto_type):
			await send_and_save_message(
				message,
				f"❌ Неверный формат адреса кошелька для {crypto_display}. Пожалуйста, введите корректный адрес:",
				state=state
			)
			return
		
		# Сохраняем адрес кошелька
		await state.update_data(wallet_address=wallet_address)
		
		# Форматируем суммы для отображения
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Формируем сообщение с информацией о заказе
		order_info = (
			f"Вам будет зачислено: {amount_str} {crypto_display}\n"
			f"Вам необходимо оплатить: {int(amount_currency)} {currency_symbol}\n\n"
			f"Выберите способ доставки:"
		)
		
		# Показываем клавиатуру выбора способа доставки
		is_byn = selected_country == "BYN"
		await state.set_state(BuyStates.waiting_delivery_method)
		await send_and_save_message(
			message,
			order_info,
			reply_markup=buy_delivery_method_kb(currency_symbol, is_byn),
			state=state
		)
	
	@dp.message(BuyStates.waiting_delivery_method)
	async def on_delivery_method_selected(message: Message, state: FSMContext):
		"""Обработчик выбора способа доставки"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Получаем данные о заказе
		data = await state.get_data()
		delivery_text = message.text
		
		# Определяем тип доставки по тексту кнопки
		if delivery_text == "⬅️ Назад":
			# Возвращаемся к вводу адреса кошелька
			crypto_display = data.get("crypto_display", "")
			await state.set_state(BuyStates.waiting_wallet_address)
			await send_and_save_message(message, f"Введите адрес кошелька для {crypto_display}:", state=state)
			return
		
		# Определяем тип доставки
		delivery_type = "normal"
		if "VIP" in delivery_text or "vip" in delivery_text.lower():
			delivery_type = "vip"
		
		# Сохраняем выбранный способ доставки
		await state.update_data(delivery_method=delivery_type)
		
		# Получаем данные о заказе
		amount = data.get("amount", 0)
		amount_currency = data.get("amount_currency", 0)
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		wallet_address = data.get("wallet_address", "")
		currency_symbol = data.get("currency_symbol", "")
		selected_country = data.get("selected_country", "RUB")
		
		# Рассчитываем сумму с учетом VIP
		final_amount = amount_currency
		if delivery_type == "vip":
			if selected_country == "BYN":
				final_amount += 4
			else:  # RUB
				final_amount += 1000
		
		# Получаем реквизиты пользователя
		user_cards = await db_local.get_cards_for_user_tg(message.from_user.id)
		requisites_text = ""
		
		if user_cards:
			# Берем первую карту пользователя
			card = user_cards[0]
			card_id = card["card_id"]
			
			# Получаем реквизиты из таблицы card_requisites
			requisites = await db_local.list_card_requisites(card_id)
			
			# Формируем текст реквизитов
			requisites_list = []
			for req in requisites:
				requisites_list.append(req["requisite_text"])
			
			# Добавляем user_message, если есть
			if card.get("user_message") and card["user_message"].strip():
				requisites_list.append(card["user_message"])
			
			if requisites_list:
				requisites_text = "\n".join(requisites_list)
		
		# Форматируем суммы для отображения
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Формируем финальное сообщение
		# Определяем сокращенное название криптовалюты
		crypto_short = ""
		if "BTC" in crypto_type or "Bitcoin" in crypto_display:
			crypto_short = "btc"
		elif "LTC" in crypto_type or "Litecoin" in crypto_display:
			crypto_short = "ltc"
		elif "USDT" in crypto_type:
			crypto_short = "usdt"
		elif "XMR" in crypto_type or "Monero" in crypto_display:
			crypto_short = "xmr"
		
		# Формируем сообщение
		order_message = (
			f"☑️Заявка успешно создана.\n"
			f"Вы получаете: {amount_str} {crypto_short}\n"
			f"{crypto_display} - {crypto_type}-адрес: {wallet_address}\n\n"
			f"💳Сумма к оплате: {int(final_amount)} {currency_symbol}\n"
			f"Реквизиты для оплаты:\n\n"
		)
		
		if requisites_text:
			order_message += requisites_text + "\n\n"
		else:
			order_message += "Реквизиты не найдены. Обратитесь к администратору.\n\n"
		
		# Сохраняем время создания заявки (15 минут)
		order_created_at = int(time.time())
		order_expires_at = order_created_at + 15 * 60  # 15 минут
		
		order_message += f"⏰Заявка действительна: 15 минут\n"
		order_message += f"✅После оплаты необходимо нажать на кнопку 'ОПЛАТА СОВЕРШЕНА'"
		
		# Сохраняем данные о заявке
		await state.update_data(
			final_amount=final_amount,
			order_created_at=order_created_at,
			order_expires_at=order_expires_at
		)
		
		# Отправляем финальное сообщение
		await state.set_state(BuyStates.waiting_payment_confirmation)
		final_message = await send_and_save_message(
			message,
			order_message,
			reply_markup=buy_payment_confirmed_kb(),
			state=state
		)
		# Сохраняем ID сообщения с заявкой в состоянии для последующего сохранения в БД
		await state.update_data(order_message_id=final_message.message_id)
	
	@dp.message(BuyStates.waiting_payment_confirmation, F.text == "ОПЛАТА СОВЕРШЕНА")
	async def on_payment_confirmed(message: Message, state: FSMContext):
		"""Обработчик подтверждения оплаты"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Получаем данные о заказе
		data = await state.get_data()
		order_expires_at = data.get("order_expires_at", 0)
		
		# Проверяем, не истекла ли заявка
		current_time = int(time.time())
		if current_time > order_expires_at:
			# Возвращаемся в главное меню
			from app.keyboards import client_menu_kb
			await state.clear()
			await send_and_save_message(
				message,
				"❌ Время действия заявки истекло. Пожалуйста, создайте новую заявку.\n\n"
				"🔒 Сервис не поддерживает подозрительные или незаконные транзакции.\n"
				"🔞 Только для пользователей старше 18 лет.\n\n"
				"✅Выберите нужную функцию в меню ниже, чтобы начать работу.",
				reply_markup=client_menu_kb(),
				state=state
			)
			return
		
		# Переходим в состояние ожидания скриншота/чека
		await state.set_state(BuyStates.waiting_payment_proof)
		
		# Запрашиваем скриншот/чек оплаты
		proof_request_message = await send_and_save_message(
			message,
			"Отправьте скрин перевода, либо чек оплаты.",
			state=state
		)
		# Сохраняем ID сообщения с запросом скриншота
		await state.update_data(proof_request_message_id=proof_request_message.message_id)
	
	@dp.message(BuyStates.waiting_payment_proof)
	async def on_payment_proof_received(message: Message, state: FSMContext):
		"""Обработчик получения скриншота/чека оплаты"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Проверяем, что пользователь отправил фото или документ
		has_photo = message.photo is not None and len(message.photo) > 0
		has_document = message.document is not None
		
		if not has_photo and not has_document:
			# Пользователь отправил текст вместо фото/документа
			await delete_user_message(message)
			await send_and_save_message(
				message,
				"❌ Пожалуйста, отправьте скриншот перевода или чек оплаты (фото или документ).",
				state=state
			)
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Получаем данные о заказе
		data = await state.get_data()
		amount = data.get("amount", 0)
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		wallet_address = data.get("wallet_address", "")
		amount_currency = data.get("final_amount", data.get("amount_currency", 0))
		currency_symbol = data.get("currency_symbol", "")
		delivery_method = data.get("delivery_method", "")
		
		# Получаем file_id для фото или документа
		proof_photo_file_id = None
		proof_document_file_id = None
		if has_photo:
			proof_photo_file_id = message.photo[-1].file_id  # Берем фото наибольшего размера
		elif has_document:
			proof_document_file_id = message.document.file_id
		
		# Получаем имя пользователя
		user_name = message.from_user.full_name or ""
		user_username = message.from_user.username or ""
		
		# Получаем ID сообщений из состояния
		order_message_id = data.get("order_message_id")
		proof_request_message_id = data.get("proof_request_message_id")
		
		# Отправляем сообщение об успешной отправке скриншота ПЕРЕД созданием заявки
		proof_confirmation_message = await message.bot.send_message(
			chat_id=message.chat.id,
			text="✅ Спасибо! Ваш скриншот/чек получен. Ожидайте зачисления средств на указанный адрес кошелька."
		)
		proof_confirmation_message_id = proof_confirmation_message.message_id
		
		# Создаем заявку в БД
		order_id = await db_local.create_order(
			user_tg_id=message.from_user.id,
			user_name=user_name,
			user_username=user_username,
			crypto_type=crypto_type,
			crypto_display=crypto_display,
			amount=amount,
			wallet_address=wallet_address,
			amount_currency=amount_currency,
			currency_symbol=currency_symbol,
			delivery_method=delivery_method,
			proof_photo_file_id=proof_photo_file_id,
			proof_document_file_id=proof_document_file_id,
			order_message_id=order_message_id,
			proof_request_message_id=proof_request_message_id,
			proof_confirmation_message_id=proof_confirmation_message_id,
		)
		
		# Получаем заявку для получения номера
		order = await db_local.get_order_by_id(order_id)
		order_number = order["order_number"] if order else order_id
		
		# Форматируем сумму для отображения
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Формируем сообщение для админа
		admin_message_text = (
			f"Номер заявки за сегодня: {order_number}\n"
			f"Имя пользователя: {user_name or 'Не указано'}\n"
			f"Username: @{user_username}\n\n"
			f"Количество монет: {amount_str} {crypto_display}\n"
			f"Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
			f"Адрес кошелька: <code>{wallet_address}</code>"
		)
		
		# Отправляем заявку всем админам
		admin_ids = get_admin_ids()
		for admin_id in admin_ids:
			try:
				# Отправляем текст заявки
				admin_msg = await message.bot.send_message(
					chat_id=admin_id,
					text=admin_message_text,
					parse_mode=ParseMode.HTML,
					reply_markup=order_action_kb(order_id)
				)
				
				# Отправляем скриншот/чек
				if proof_photo_file_id:
					await message.bot.send_photo(
						chat_id=admin_id,
						photo=proof_photo_file_id,
						reply_to_message_id=admin_msg.message_id
					)
				elif proof_document_file_id:
					await message.bot.send_document(
						chat_id=admin_id,
						document=proof_document_file_id,
						reply_to_message_id=admin_msg.message_id
					)
			except Exception as e:
				logging.getLogger("app.main").error(f"Ошибка отправки заявки админу {admin_id}: {e}")
		
		# Очищаем состояние
		await state.clear()
	
	@dp.callback_query(F.data.startswith("order:completed:"))
	async def on_order_completed(cb: CallbackQuery, state: FSMContext):
		"""Обработчик нажатия кнопки 'Выполнил'"""
		if not cb.from_user:
			return
		from app.di import get_db, get_admin_ids
		from app.admin import is_admin
		db_local = get_db()
		admin_ids = get_admin_ids()
		
		# Проверяем, что это админ
		if not is_admin(cb.from_user.id, cb.from_user.username, admin_ids, []):
			await cb.answer("❌ У вас нет прав для выполнения этого действия.", show_alert=True)
			return
		
		# Получаем ID заявки
		order_id = int(cb.data.split(":")[2])
		
		# Получаем данные заявки
		order = await db_local.get_order_by_id(order_id)
		if not order:
			await cb.answer("❌ Заявка не найдена.", show_alert=True)
			return
		
		# Отмечаем заявку как выполненную
		await db_local.complete_order(order_id)
		
		# Форматируем сумму для отображения
		amount = order["amount"]
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Формируем сообщение для пользователя
		user_message = (
			"✅ Ваша заявка успешно выполнена!\n"
			f"Вам зачислено: {amount_str} {order['crypto_display']}"
		)
		
		# Если это BTC, добавляем ссылку на mempool.space
		if order["crypto_type"] == "BTC":
			wallet_address = order["wallet_address"]
			mempool_link = f"https://mempool.space/address/{wallet_address}"
			user_message += f"\n\n🔗 Проверить транзакцию: {mempool_link}"
		
		# Удаляем все сообщения у пользователя, связанные с заявкой
		user_tg_id = order["user_tg_id"]
		messages_to_delete = []
		
		# Сообщение с заявкой
		order_message_id = order.get("order_message_id")
		if order_message_id:
			messages_to_delete.append(order_message_id)
		
		# Сообщение с запросом скриншота
		proof_request_message_id = order.get("proof_request_message_id")
		if proof_request_message_id:
			messages_to_delete.append(proof_request_message_id)
		
		# Сообщение с подтверждением получения скриншота
		proof_confirmation_message_id = order.get("proof_confirmation_message_id")
		if proof_confirmation_message_id:
			messages_to_delete.append(proof_confirmation_message_id)
		
		# Удаляем все сообщения
		for msg_id in messages_to_delete:
			try:
				await cb.bot.delete_message(
					chat_id=user_tg_id,
					message_id=msg_id
				)
			except Exception as e:
				logging.getLogger("app.main").debug(f"Не удалось удалить сообщение {msg_id} у пользователя {user_tg_id}: {e}")
		
		# Отправляем сообщение пользователю с кнопкой "Удалить"
		from app.keyboards import delete_message_kb
		try:
			await cb.bot.send_message(
				chat_id=order["user_tg_id"],
				text=user_message,
				reply_markup=delete_message_kb()
			)
		except Exception as e:
			logging.getLogger("app.main").error(f"Ошибка отправки сообщения пользователю {order['user_tg_id']}: {e}")
		
		# Обновляем сообщение админа
		await cb.answer("✅ Заявка отмечена как выполненная!")
		await cb.message.edit_text(
			f"{cb.message.text}\n\n✅ Выполнено",
			reply_markup=None
		)
	
	@dp.callback_query(F.data == "delete_message")
	async def on_delete_message(cb: CallbackQuery):
		"""Обработчик кнопки 'Удалить' для удаления сообщения"""
		if not cb.from_user or not cb.message:
			return
		
		try:
			# Проверяем, что пользователь пытается удалить свое сообщение
			if cb.message.chat.id == cb.from_user.id:
				# Удаляем сообщение
				await cb.message.delete()
				await cb.answer("✅ Сообщение удалено")
				
				# Отправляем главное меню с кнопками "Купить" и "Продать" без текста
				from app.keyboards import client_menu_kb
				from app.di import get_db
				db_local = get_db()
				if await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
					await cb.bot.send_message(
						chat_id=cb.from_user.id,
						text=" ",
						reply_markup=client_menu_kb()
					)
			else:
				await cb.answer("❌ Вы можете удалять только свои сообщения", show_alert=True)
		except Exception as e:
			logging.getLogger("app.main").error(f"Ошибка при удалении сообщения: {e}")
			await cb.answer("❌ Не удалось удалить сообщение", show_alert=True)

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
