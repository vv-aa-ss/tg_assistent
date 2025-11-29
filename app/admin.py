from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter, Command
from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from typing import Any, Awaitable, Callable, Dict, List, Tuple
from datetime import datetime, timedelta
import logging
import re
from html import escape
import asyncio
from app.keyboards import (
	admin_menu_kb,
	cards_list_kb,
	cards_groups_kb,
	users_list_kb,
	simple_back_kb,
	cards_select_kb,
	user_card_select_kb,
	crypto_list_kb,
	crypto_delete_kb,
	user_action_kb,
	card_action_kb,
	user_cards_reply_kb,
	similar_users_select_kb,
	card_groups_list_kb,
	card_groups_select_kb,
)
from app.di import get_db, get_admin_ids, get_admin_usernames

admin_router = Router(name="admin")
logger = logging.getLogger("app.admin")

USERS_PER_PAGE = 6

# Блокировка для синхронизации обработки множественных пересылок
# Ключ: (user_id, session_key), значение: asyncio.Lock
_multi_forward_locks: Dict[Tuple[int, str], asyncio.Lock] = {}
_locks_lock = asyncio.Lock()  # Блокировка для доступа к словарю блокировок


class AdminOnlyMiddleware(BaseMiddleware):
	async def __call__(
		self,
		handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
		event: TelegramObject,
		data: Dict[str, Any],
	) -> Any:
		admin_ids = get_admin_ids()
		admin_usernames = get_admin_usernames()
		from_user = getattr(event, "from_user", None)
		
		# Логируем ВСЕ сообщения, которые проходят через middleware
		if isinstance(event, Message):
			text = event.text or event.caption or ""
			is_forward = bool(getattr(event, "forward_origin", None) or getattr(event, "forward_from", None))
			logger.info(f"🔵 MIDDLEWARE: message_id={event.message_id}, is_forward={is_forward}, text='{text[:100]}', from_user={from_user.id if from_user else None}, handler={handler.__name__ if hasattr(handler, '__name__') else 'unknown'}")
		
		if from_user:
			user_id = getattr(from_user, "id", None)
			username = getattr(from_user, "username", None)
			is_admin_user = is_admin(user_id, username, admin_ids, admin_usernames)
			logger.info(f"🔵 MIDDLEWARE: Проверка админа: user_id={user_id}, username={username}, is_admin={is_admin_user}")
			if not is_admin_user:
				if isinstance(event, Message):
					logger.info(f"🔵 MIDDLEWARE: Сообщение {event.message_id} от не-админа, блокируем")
				return
		logger.info(f"🔵 MIDDLEWARE: Пропускаем сообщение дальше к handler")
		result = await handler(event, data)
		logger.info(f"🔵 MIDDLEWARE: Handler вернул результат")
		return result


admin_router.message.middleware(AdminOnlyMiddleware())
admin_router.callback_query.middleware(AdminOnlyMiddleware())


class AddCardStates(StatesGroup):
	waiting_name = State()


class CardUserMessageStates(StatesGroup):
	waiting_message = State()


class CardColumnBindStates(StatesGroup):
	selecting_card = State()
	waiting_column = State()


class ForwardBindStates(StatesGroup):
	waiting_select_card = State()
	waiting_select_existing_card = State()
	collecting_multi_forward = State()
	editing_crypto_amount = State()  # Состояние для редактирования количества криптовалюты
	editing_cash_amount = State()  # Состояние для редактирования количества наличных
	selecting_card_for_cash = State()  # Состояние для выбора карты при вводе наличных


class CryptoColumnEditStates(StatesGroup):
	waiting_column = State()
	waiting_crypto_name = State()
	waiting_crypto_column = State()


class CardGroupStates(StatesGroup):
	waiting_group_name = State()


def is_admin(user_id: int | None, username: str | None, admin_ids: list[int], admin_usernames: list[str] = None) -> bool:
	"""Проверяет, является ли пользователь администратором по ID или username"""
	if admin_usernames is None:
		admin_usernames = []
	if user_id is not None and user_id in admin_ids:
		return True
	if username:
		username_clean = username.lstrip("@").lower()
		admin_usernames_clean = [u.lstrip("@").lower() for u in admin_usernames]
		if username_clean in admin_usernames_clean:
			return True
	return False


def format_ts(ts: int | None) -> str:
	if not ts:
		return "нет данных"
	try:
		return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
	except (OSError, OverflowError, ValueError):
		return "недоступно"


def format_relative(ts: int | None) -> str:
	if not ts:
		return "нет данных"
	try:
		dt = datetime.fromtimestamp(ts)
	except (OSError, OverflowError, ValueError):
		return "недоступно"
	now = datetime.now()
	delta = now - dt
	if delta.total_seconds() < 0:
		# Если дата в будущем, показываем как 0 дней назад
		return "0 д назад"
	if delta <= timedelta(minutes=1):
		return "только что"
	if delta < timedelta(hours=1):
		minutes = int(delta.total_seconds() // 60)
		return f"{minutes} мин назад"
	if delta < timedelta(days=1):
		hours = int(delta.total_seconds() // 3600)
		return f"{hours} ч назад"
	# Для всех случаев больше дня показываем только дни назад
	days = delta.days
	return f"{days} д назад"


def detect_crypto_type(amount: float) -> str:
	"""
	Определяет тип криптовалюты по сумме.
	BTC: очень маленькие суммы (< 0.01)
	LTC/XMR: средние суммы (0.1 - 10)
	"""
	if amount < 0.01:
		return "BTC"
	elif 0.1 <= amount <= 10:
		return "LTC"  # Можно изменить на XMR если нужно
	else:
		return "BTC"  # По умолчанию


def detect_cash_type(amount: int) -> str:
	"""
	Определяет тип наличных по сумме.
	BYN: до 1000
	RUB: 1000 и больше
	"""
	if amount < 1000:
		return "BYN"
	else:
		return "RUB"


def parse_forwarded_message(text: str) -> dict:
	"""
	Парсит пересланное сообщение и определяет его тип.
	Возвращает dict с полями: type, value, currency, card_name, user_name, display
	
	Обрабатывает многострочные сообщения и различные форматы форматирования.
	"""
	logger.debug(f"🔍 parse_forwarded_message: входной текст='{text}'")
	
	if not text:
		logger.debug(f"❌ parse_forwarded_message: текст пустой")
		return {"type": "unknown"}
	
	# Нормализуем текст: заменяем множественные пробелы и переносы строк на одинарные пробелы
	# Это позволяет надежно парсить многострочные сообщения
	normalized_text = re.sub(r'\s+', ' ', text.strip())
	logger.debug(f"🔍 parse_forwarded_message: после нормализации='{normalized_text}'")
	
	# ПРИОРИТЕТ 1: Проверяем на карту (если есть ключевые слова карты)
	# Это должно быть первым, так как сообщения с картами могут содержать числа и другие данные
	card_name = None
	text_upper = normalized_text.upper()
	
	# Проверяем на ключевые слова карт (более надежная проверка)
	if "ТИНЕК" in text_upper or "ТИНЬКОФ" in text_upper or "ТИНЬКОФФ" in text_upper:
		card_name = "ТИНЕК"
	elif "СБЕР" in text_upper or "СБЕРБАНК" in text_upper:
		card_name = "СБЕР"
	
	# ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Если в тексте есть 4 числа подряд (номер карты) И имя в скобках,
	# то это скорее всего карта, даже если нет ключевого слова
	if not card_name:
		# Ищем паттерн: 4 числа через пробел (номер карты) + имя в скобках
		card_number_pattern = r'\d{4}\s+\d{4}\s+\d{4}\s+\d{4}'
		name_in_brackets = r'\([А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z]\.?\)'
		
		if re.search(card_number_pattern, normalized_text) and re.search(name_in_brackets, normalized_text):
			# Это похоже на карту с номером и именем, но без названия карты
			# Пытаемся найти название карты в тексте (заглавные буквы, короткое слово)
			card_name_match = re.search(r'\b([А-ЯЁA-Z]{2,10})\b', normalized_text)
			if card_name_match:
				card_name = card_name_match.group(1)
			else:
				# Если название не найдено, используем "КАРТА" как заглушку
				card_name = "КАРТА"
	
	if card_name:
		# Пытаемся найти имя в скобках (работает даже с переносами строк благодаря нормализации)
		# Ищем паттерн: (Имя И.)
		name_match = re.search(r'\(([А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z]\.?)\)', normalized_text)
		user_name = name_match.group(1) if name_match else None
		
		# Пытаемся извлечь номер карты (4 числа через пробел)
		card_number_match = re.search(r'(\d{4}\s+\d{4}\s+\d{4}\s+\d{4})', normalized_text)
		card_number = card_number_match.group(1) if card_number_match else None
		
		# Формируем display: если есть имя - показываем карту и имя, иначе только карту
		if user_name:
			display = f"{card_name} ({user_name})"
		else:
			display = card_name
		
		result = {
			"type": "card",
			"card_name": card_name,
			"user_name": user_name,
			"card_number": card_number,  # Сохраняем номер карты для возможного использования
			"display": display
		}
		logger.info(f"✅ parse_forwarded_message: определена карта: {result}")
		return result
	
	# ПРИОРИТЕТ 2: Проверяем на криптовалюту (число с точкой)
	# Ищем число с точкой в начале строки или как отдельное слово
	crypto_match = re.search(r'(?:^|\s)(\d+\.\d+)(?:\s|$)', normalized_text)
	logger.debug(f"🔍 parse_forwarded_message: crypto_match={bool(crypto_match)}, паттерн='(?:^|\\s)(\\d+\\.\\d+)(?:\\s|$)'")
	if crypto_match:
		amount = float(crypto_match.group(1))
		currency = detect_crypto_type(amount)
		result = {
			"type": "crypto",
			"value": amount,
			"currency": currency,
			"display": f"{amount} {currency}"
		}
		logger.info(f"✅ parse_forwarded_message: определена криптовалюта: {result}")
		return result
	
	# ПРИОРИТЕТ 3: Проверяем на наличные (целое число)
	# Ищем первое целое число в тексте (может быть несколько чисел через пробел)
	# Также проверяем наличие текста "без долга"
	# ВАЖНО: НЕ определяем как наличные, если это похоже на номер карты (4 числа через пробел)
	# или если есть имя в скобках (это скорее всего карта)
	card_number_pattern = r'\d{4}\s+\d{4}\s+\d{4}\s+\d{4}'
	name_in_brackets = r'\([А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z]\.?\)'
	is_likely_card = bool(re.search(card_number_pattern, normalized_text) or re.search(name_in_brackets, normalized_text))
	
	if not is_likely_card:
		cash_match = re.search(r'(?:^|\s)(\d+)(?:\s+\d+)*(?:\s+без\s+долга)?(?:\s|$)', normalized_text, re.IGNORECASE)
		logger.debug(f"🔍 parse_forwarded_message: cash_match={bool(cash_match)}, паттерн='(?:^|\\s)(\\d+)(?:\\s+\\d+)*(?:\\s+без\\s+долга)?(?:\\s|$)'")
		if cash_match:
			amount = int(cash_match.group(1))
			currency = detect_cash_type(amount)
			result = {
				"type": "cash",
				"value": amount,
				"currency": currency,
				"display": f"{amount}"
			}
			logger.info(f"✅ parse_forwarded_message: определены наличные: {result}")
			return result
	else:
		logger.debug(f"🔍 parse_forwarded_message: пропускаем проверку наличных, т.к. это похоже на карту")
	
	# ПРИОРИТЕТ 4: Проверяем просто на название карты (только заглавные буквы, короткое слово)
	if re.match(r'^[А-ЯЁA-Z]{2,10}$', normalized_text):
		result = {
			"type": "card",
			"card_name": normalized_text,
			"user_name": None,
			"display": normalized_text
		}
		logger.info(f"✅ parse_forwarded_message: определена карта (только название): {result}")
		return result
	
	# ПРИОРИТЕТ 5: Проверяем на имя пользователя в скобках (Артем В.)
	name_match = re.match(r'^\(([А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z]\.?)\)$', normalized_text)
	if name_match:
		result = {
			"type": "user_name",
			"user_name": name_match.group(1),
			"display": name_match.group(1)
		}
		logger.info(f"✅ parse_forwarded_message: определено имя пользователя: {result}")
		return result
	
	logger.warning(f"⚠️ parse_forwarded_message: не удалось определить тип сообщения, текст='{normalized_text}'")
	return {"type": "unknown", "text": normalized_text}


def render_bar(value: int, max_value: int, width: int = 10) -> str:
	if max_value <= 0:
		max_value = 1
	value = max(0, value)
	ratio = value / max_value if max_value else 0
	filled = int(round(ratio * width))
	if value > 0 and filled == 0:
		filled = 1
	filled = min(width, filled)
	empty = width - filled
	return "█" * filled + "·" * empty

def extract_forward_profile(message: Message) -> tuple[int | None, str | None, str | None]:
	"""
	Извлекает информацию о пересланном пользователе.
	Возвращает (tg_id, username, full_name).
	Примечание: из-за настроек приватности Telegram ID может быть недоступен,
	но username и full_name могут быть доступны.
	"""
	try:
		# Пытаемся получить через новый API (forward_origin)
		if getattr(message, "forward_origin", None):
			origin = message.forward_origin
			origin_type = type(origin).__name__
			logger.info(f"🔍 forward_origin найден: {origin_type}")
			
			# Проверяем тип origin
			if origin_type == "MessageOriginHiddenUser":
				# Пользователь полностью скрыл информацию - есть только имя
				sender_user_name = getattr(origin, "sender_user_name", None)
				if sender_user_name:
					logger.warning(f"⚠️ MessageOriginHiddenUser: ID недоступен, но есть sender_user_name='{sender_user_name}' (полная приватность)")
					# Возвращаем имя как full_name, username будет None
					return None, None, sender_user_name
				else:
					logger.warning(f"⚠️ MessageOriginHiddenUser: даже sender_user_name недоступен")
			
			# Для MessageOriginUser пытаемся получить sender_user
			user = getattr(origin, "sender_user", None)
			if user:
				user_id = getattr(user, "id", None)
				username = getattr(user, "username", None)
				full_name = " ".join([x for x in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if x]) or None
				if user_id:
					logger.info(f"✅ forward_origin: user_id={user_id}, username={username}, full_name={full_name}")
					return user_id, username, full_name
				elif username or full_name:
					# ID недоступен из-за настроек приватности, но есть другие данные
					logger.warning(f"⚠️ forward_origin: user_id недоступен (приватность), но есть username={username}, full_name={full_name}")
					return None, username, full_name
			else:
				logger.warning(f"⚠️ forward_origin найден ({origin_type}), но sender_user отсутствует")
		
		# Пытаемся получить через старый API (forward_from)
		ex = getattr(message, "forward_from", None)
		if ex:
			user_id = getattr(ex, "id", None)
			username = getattr(ex, "username", None)
			full_name = " ".join([x for x in [getattr(ex, "first_name", None), getattr(ex, "last_name", None)] if x]) or None
			if user_id:
				logger.info(f"✅ forward_from: user_id={user_id}, username={username}, full_name={full_name}")
				return user_id, username, full_name
			elif username or full_name:
				# ID недоступен из-за настроек приватности
				logger.warning(f"⚠️ forward_from: user_id недоступен (приватность), но есть username={username}, full_name={full_name}")
				return None, username, full_name
		
		logger.warning("❌ Нет информации о пересылке в сообщении (возможно, пользователь скрыл данные через настройки приватности или сообщение не переслано)")
		return None, None, None
	except Exception as e:
		logger.exception(f"❌ extract_forward_profile error: {e}")
		return None, None, None


async def format_multi_forward_message_text(rows_data: List[Dict] | None = None) -> str:
	"""
	Формирует текст сообщения "Проверка данных" с суммами в USD для всех строк.
	
	Args:
		rows_data: Список словарей, каждый содержит crypto_data, cash_data, card_data и row_index
	
	Returns:
		Текст сообщения с суммами в USD для всех строк
	"""
	text = "📋 Проверка данных:"
	
	if rows_data is None:
		rows_data = []
	
	# Обрабатываем каждую строку
	for i, row in enumerate(rows_data):
		crypto_data = row.get("crypto_data")
		cash_data = row.get("cash_data")
		card_data = row.get("card_data")
		
		# Если есть данные в строке, показываем их
		row_parts = []
		
		# Если есть криптовалюта, показываем USD с названием валюты
		if crypto_data:
			usd_amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
			currency = crypto_data.get("currency", "")
			if usd_amount > 0:
				usd_amount_rounded = int(round(usd_amount))
				if currency:
					row_parts.append(f"🪙 {usd_amount_rounded} USD ({currency})")
				else:
					row_parts.append(f"🪙 {usd_amount_rounded} USD")
		
		# Если есть наличные
		if cash_data:
			value = cash_data.get("value", 0)
			currency = cash_data.get("currency", "")
			if value > 0:
				row_parts.append(f"💵 {value} {currency}")
		
		# Если есть карта
		if card_data:
			display = card_data.get("display", "Карта")
			row_parts.append(f"💳 {display}")
		
		# Если есть данные в строке, добавляем их в текст
		if row_parts:
			text += "\n" + "\n".join(row_parts)
	
	return text


@admin_router.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		logger.debug(f"/admin ignored: user {message.from_user.id} is not admin")
		return
	await message.answer("Админ-панель:", reply_markup=admin_menu_kb())


@admin_router.message(F.text == "/add")
async def cmd_add(message: Message, state: FSMContext, bot: Bot):
	"""Команда для вызова меню добавления данных в таблицу"""
	logger.info(f"🔴🔴🔴 ОБРАБОТЧИК cmd_add ВЫЗВАН! message_id={message.message_id}, user_id={message.from_user.id if message.from_user else None}, text='{message.text}'")
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	logger.info(f"🔴 Проверка админа: user_id={message.from_user.id if message.from_user else None}, admin_ids={admin_ids}, admin_usernames={admin_usernames}")
	is_admin_user = is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames)
	logger.info(f"🔴 Результат проверки админа: {is_admin_user}")
	if not is_admin_user:
		logger.warning(f"🔴 /add ignored: user {message.from_user.id} is not admin")
		return
	logger.info(f"✅ /add обрабатывается для админа {message.from_user.id}")
	
	# Устанавливаем состояние collecting_multi_forward с пустым списком сообщений
	await state.set_state(ForwardBindStates.collecting_multi_forward)
	session_key = f"multi_{message.from_user.id}_{message.message_id}"
	
	# Инициализируем структуру данных с одной пустой строкой
	rows_data = [{"crypto_data": None, "cash_data": None, "card_data": None, "row_index": 0}]
	
	await state.update_data(
		multi_forward_messages=[],
		multi_forward_session_key=session_key,
		multi_forward_ready=False,
		multi_forward_rows=rows_data,
		selected_xmr_numbers={},  # Словарь {row_index: xmr_number}
		mode="add"  # Флаг режима add (по умолчанию)
	)
	
	# Создаем пустое меню (все данные None - будут показаны как "Не указано")
	from app.keyboards import multi_forward_select_kb
	message_text = await format_multi_forward_message_text(rows_data)
	
	sent_message = await message.answer(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr={})
	)
	
	# Сохраняем ID сообщения с кнопками
	await state.update_data(
		multi_forward_buttons_msg_id=sent_message.message_id,
		multi_forward_ready=True
	)
	
	logger.info(f"✅ Создано меню добавления данных через команду /add для пользователя {message.from_user.id}")


@admin_router.message(Command("rate"))
async def cmd_rate(message: Message, state: FSMContext, bot: Bot):
	"""Команда для вызова меню добавления данных в таблицу в режиме rate (запись в отдельные ячейки)"""
	logger.info(f"🔴🔴🔴 ОБРАБОТЧИК cmd_rate ВЫЗВАН! message_id={message.message_id}, user_id={message.from_user.id if message.from_user else None}, text='{message.text}'")
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	logger.info(f"🔴 Проверка админа: user_id={message.from_user.id if message.from_user else None}, admin_ids={admin_ids}, admin_usernames={admin_usernames}")
	is_admin_user = is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames)
	logger.info(f"🔴 Результат проверки админа: {is_admin_user}")
	if not is_admin_user:
		logger.warning(f"🔴 /rate ignored: user {message.from_user.id} is not admin")
		return
	logger.info(f"✅ /rate обрабатывается для админа {message.from_user.id}")
	
	# Устанавливаем состояние collecting_multi_forward с пустым списком сообщений
	await state.set_state(ForwardBindStates.collecting_multi_forward)
	session_key = f"multi_{message.from_user.id}_{message.message_id}"
	
	# Инициализируем структуру данных с одной пустой строкой
	rows_data = [{"crypto_data": None, "cash_data": None, "card_data": None, "row_index": 0}]
	
	await state.update_data(
		multi_forward_messages=[],
		multi_forward_session_key=session_key,
		multi_forward_ready=False,
		multi_forward_rows=rows_data,
		selected_xmr_numbers={},  # Словарь {row_index: xmr_number}
		mode="rate"  # Флаг режима rate
	)
	
	# Создаем пустое меню (все данные None - будут показаны как "Не указано")
	from app.keyboards import multi_forward_select_kb
	message_text = await format_multi_forward_message_text(rows_data)
	
	sent_message = await message.answer(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr={})
	)
	
	# Сохраняем ID сообщения с кнопками
	await state.update_data(
		multi_forward_buttons_msg_id=sent_message.message_id,
		multi_forward_ready=True
	)
	
	logger.info(f"✅ Создано меню добавления данных через команду /rate для пользователя {message.from_user.id}")


@admin_router.message(F.text == "/del")
async def cmd_del(message: Message, state: FSMContext):
	"""Команда для удаления последней добавленной строки из Google Sheets"""
	logger.info(f"🔴 ОБРАБОТЧИК cmd_del ВЫЗВАН! message_id={message.message_id}, user_id={message.from_user.id if message.from_user else None}")
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	is_admin_user = is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames)
	
	if not is_admin_user:
		logger.warning(f"🔴 /del ignored: user {message.from_user.id} is not admin")
		return
	
	logger.info(f"✅ /del обрабатывается для админа {message.from_user.id}")
	
	# Получаем настройки Google Sheets
	from app.config import get_settings
	settings = get_settings()
	
	if not settings.google_sheet_id or not settings.google_credentials_path:
		await message.answer("⚠️ Google Sheets не настроен (отсутствует GOOGLE_SHEET_ID или GOOGLE_CREDENTIALS_PATH)")
		return
	
	# Удаляем последнюю строку
	from app.google_sheets import delete_last_row_from_google_sheet
	
	try:
		result = await delete_last_row_from_google_sheet(
			settings.google_sheet_id,
			settings.google_credentials_path
		)
		
		if result.get("success"):
			deleted_row = result.get("deleted_row")
			await message.answer(f"✅ Успешно удалена строка {deleted_row}")
		else:
			error_message = result.get("message", "Неизвестная ошибка")
			await message.answer(f"❌ Ошибка удаления: {error_message}")
	except Exception as e:
		logger.exception(f"Ошибка при удалении строки: {e}")
		await message.answer(f"❌ Произошла ошибка при удалении: {str(e)}")


@admin_router.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery, state: FSMContext):
	await state.clear()
	await cb.message.edit_text("Админ-панель:", reply_markup=admin_menu_kb())
	await cb.answer()


@admin_router.callback_query(F.data == "admin:cards")
async def admin_cards(cb: CallbackQuery):
	"""Показывает список групп карт"""
	db = get_db()
	groups = await db.list_card_groups()
	logger.debug(f"Show card groups: count={len(groups)}")
	
	text = "Выберите группу карт:" if groups else "Групп пока нет."
	await cb.message.edit_text(text, reply_markup=cards_groups_kb(groups))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("cards:group:"))
async def cards_group_view(cb: CallbackQuery):
	"""Показывает карты выбранной группы или карты вне групп"""
	db = get_db()
	group_id_str = cb.data.split(":")[-1]
	group_id = int(group_id_str) if group_id_str != "0" else None
	
	if group_id:
		# Получаем карты из группы
		cards = await db.get_cards_by_group(group_id)
		group = await db.get_card_group(group_id)
		group_name = group.get("name", "Группа") if group else "Группа"
		text = f"Карты группы '{group_name}':" if cards else f"В группе '{group_name}' нет карт."
		
		# Преобразуем формат карт из (id, name, details) в (id, name)
		cards_list = [(c[0], c[1]) for c in cards]
		
		logger.debug(f"Show cards for group_id={group_id}, count={len(cards_list)}")
		
		await cb.message.edit_text(text, reply_markup=cards_list_kb(cards_list, back_to="admin:cards", group_id=group_id))
	else:
		# Получаем карты без группы
		cards = await db.get_cards_without_group()
		text = "Карты вне групп:" if cards else "Нет карт вне групп."
		
		# Преобразуем формат карт из (id, name, details) в (id, name)
		cards_list = [(c[0], c[1]) for c in cards]
		
		logger.debug(f"Show cards without group, count={len(cards_list)}")
		
		await cb.message.edit_text(text, reply_markup=cards_list_kb(cards_list, back_to="admin:cards"))
	
	await cb.answer()


@admin_router.callback_query(F.data.startswith("cards:delete_group:"))
async def cards_delete_group(cb: CallbackQuery):
	"""Удаляет группу карт и отвязывает все карты от группы"""
	db = get_db()
	group_id = int(cb.data.split(":")[-1])
	
	# Получаем информацию о группе перед удалением
	group = await db.get_card_group(group_id)
	if not group:
		await cb.answer("Группа не найдена", show_alert=True)
		return
	
	group_name = group.get("name", "Группа")
	
	# Удаляем группу (карты автоматически отвязываются)
	try:
		await db.delete_card_group(group_id)
		logger.info(f"Группа '{group_name}' (id={group_id}) удалена, карты отвязаны")
		
		# Возвращаемся к списку групп
		groups = await db.list_card_groups()
		text = "Выберите группу карт:" if groups else "Групп пока нет."
		await cb.message.edit_text(text, reply_markup=cards_groups_kb(groups))
		await cb.answer(f"✅ Группа '{group_name}' удалена, карты отвязаны")
	except Exception as e:
		logger.exception(f"Ошибка удаления группы {group_id}: {e}")
		await cb.answer("❌ Ошибка при удалении группы", show_alert=True)


@admin_router.callback_query(F.data == "admin:crypto")
async def admin_crypto(cb: CallbackQuery):
	"""Показывает список криптовалют с их адресами столбцов"""
	db = get_db()
	crypto_columns = await db.list_crypto_columns()
	logger.debug(f"Show crypto columns: count={len(crypto_columns)}")
	
	if not crypto_columns:
		text = "Список криптовалют пуст."
	else:
		text = "Список криптовалют и их адресов столбцов:\n\n"
		for crypto in crypto_columns:
			crypto_type = crypto.get("crypto_type", "")
			column = crypto.get("column", "")
			text += f"{crypto_type} → {column}\n"
	
	await cb.message.edit_text(text, reply_markup=crypto_list_kb(crypto_columns))
	await cb.answer()


@admin_router.callback_query(F.data == "crypto:new")
async def crypto_new(cb: CallbackQuery, state: FSMContext):
	"""Начинает создание новой криптовалюты"""
	await state.set_state(CryptoColumnEditStates.waiting_crypto_name)
	await cb.message.edit_text(
		"Введите название криптовалюты:\n\nНапример: BTC, LTC, XMR-1, USDT",
		reply_markup=simple_back_kb("admin:crypto")
	)
	await cb.answer()


@admin_router.message(CryptoColumnEditStates.waiting_crypto_name)
async def crypto_name_input(message: Message, state: FSMContext):
	"""Обрабатывает ввод названия криптовалюты"""
	crypto_name = message.text.strip().upper()
	
	if not crypto_name:
		await message.answer("❌ Название криптовалюты не может быть пустым. Попробуйте еще раз:")
		return
	
	# Сохраняем название в state
	await state.update_data(crypto_type=crypto_name)
	await state.set_state(CryptoColumnEditStates.waiting_crypto_column)
	
	await message.answer(
		"✅ Название сохранено.\n\n"
		"Теперь введите адрес столбца (только латинские буквы):\n"
		"Например: A, B, AS, AY",
		reply_markup=simple_back_kb("admin:crypto")
	)


@admin_router.message(CryptoColumnEditStates.waiting_crypto_column)
async def crypto_column_input(message: Message, state: FSMContext):
	"""Обрабатывает ввод адреса столбца для новой криптовалюты"""
	db = get_db()
	column_input = message.text.strip().upper()
	
	if not column_input:
		await message.answer("❌ Адрес столбца не может быть пустым. Попробуйте еще раз:")
		return
	
	# Проверка на русские символы
	import re
	if re.search(r'[А-ЯЁа-яё]', column_input):
		await message.answer("❌ Адрес столбца должен содержать только латинские буквы. Русские символы не допускаются. Попробуйте еще раз:")
		return
	
	# Проверка на допустимые символы (только латинские буквы)
	if not re.match(r'^[A-Z]+$', column_input):
		await message.answer("❌ Адрес столбца должен содержать только латинские буквы (A-Z). Попробуйте еще раз:")
		return
	
	# Получаем данные из state
	data = await state.get_data()
	crypto_type = data.get("crypto_type")
	
	if not crypto_type:
		await message.answer("❌ Ошибка: название криптовалюты не найдено. Попробуйте начать заново.")
		await state.clear()
		return
	
	# Сохраняем криптовалюту
	try:
		await db.set_crypto_column(crypto_type, column_input)
		
		await message.answer(
			f"✅ Криптовалюта успешно добавлена!\n\n"
			f"Название: {crypto_type}\n"
			f"Адрес столбца: {column_input}",
			reply_markup=simple_back_kb("admin:crypto")
		)
		await state.clear()
	except Exception as e:
		logger.exception(f"Ошибка при сохранении криптовалюты: {e}")
		if "UNIQUE constraint failed" in str(e):
			await message.answer("❌ Криптовалюта с таким названием уже существует. Попробуйте другое название:")
		else:
			await message.answer("❌ Произошла ошибка при сохранении. Попробуйте еще раз.")


@admin_router.callback_query(F.data == "crypto:delete_list")
async def crypto_delete_list(cb: CallbackQuery):
	"""Показывает список криптовалют для удаления"""
	db = get_db()
	crypto_columns = await db.list_crypto_columns()
	
	if not crypto_columns:
		await cb.answer("Нет криптовалют для удаления", show_alert=True)
		return
	
	text = "Выберите криптовалюту для удаления:"
	await cb.message.edit_text(text, reply_markup=crypto_delete_kb(crypto_columns))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("crypto:delete:"))
async def crypto_delete(cb: CallbackQuery):
	"""Удаляет криптовалюту из базы данных"""
	db = get_db()
	crypto_type = cb.data.split(":")[-1]
	
	try:
		await db.delete_crypto_column(crypto_type)
		await cb.answer(f"✅ Криптовалюта '{crypto_type}' удалена", show_alert=True)
		
		# Обновляем список
		crypto_columns = await db.list_crypto_columns()
		if not crypto_columns:
			text = "Список криптовалют пуст."
		else:
			text = "Список криптовалют и их адресов столбцов:\n\n"
			for crypto in crypto_columns:
				crypto_type_item = crypto.get("crypto_type", "")
				column = crypto.get("column", "")
				text += f"{crypto_type_item} → {column}\n"
		
		await cb.message.edit_text(text, reply_markup=crypto_list_kb(crypto_columns))
	except Exception as e:
		logger.exception(f"Ошибка при удалении криптовалюты: {e}")
		await cb.answer("❌ Произошла ошибка при удалении", show_alert=True)


@admin_router.callback_query(F.data.startswith("crypto:edit:"))
async def crypto_edit(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование адреса столбца для криптовалюты"""
	db = get_db()
	crypto_type = cb.data.split(":")[-1]
	
	# Получаем текущий адрес столбца
	current_column = await db.get_crypto_column(crypto_type)
	
	# Сохраняем тип криптовалюты в state
	await state.update_data(crypto_type=crypto_type)
	await state.set_state(CryptoColumnEditStates.waiting_column)
	
	current_text = f" (текущий: {current_column})" if current_column else ""
	await cb.message.edit_text(
		f"Редактирование адреса столбца для {crypto_type}{current_text}\n\n"
		"Введите новый адрес столбца (только латинские буквы):\n"
		"Например: A, B, C, D, E, AS, AY",
		reply_markup=simple_back_kb("admin:crypto")
	)
	await cb.answer()


@admin_router.message(CryptoColumnEditStates.waiting_column)
async def crypto_column_waiting_column(message: Message, state: FSMContext):
	"""Обрабатывает ввод адреса столбца для криптовалюты"""
	db = get_db()
	column_input = message.text.strip().upper()  # Приводим к верхнему регистру
	
	if not column_input:
		await message.answer("❌ Адрес столбца не может быть пустым. Попробуйте еще раз:")
		return
	
	# Проверка на русские символы
	if re.search(r'[А-ЯЁа-яё]', column_input):
		await message.answer("❌ Адрес столбца должен содержать только латинские буквы. Русские символы не допускаются. Попробуйте еще раз:")
		return
	
	# Проверка на допустимые символы (только латинские буквы)
	if not re.match(r'^[A-Z]+$', column_input):
		await message.answer("❌ Адрес столбца должен содержать только латинские буквы (A-Z). Попробуйте еще раз:")
		return
	
	# Получаем данные из state
	data = await state.get_data()
	crypto_type = data.get("crypto_type")
	
	if not crypto_type:
		await message.answer("❌ Ошибка: тип криптовалюты не найден. Попробуйте начать заново.")
		await state.clear()
		return
	
	# Сохраняем адрес столбца
	try:
		await db.set_crypto_column(crypto_type, column_input)
		
		await message.answer(
			f"✅ Адрес столбца успешно обновлен!\n\n"
			f"Криптовалюта: {crypto_type}\n"
			f"Адрес столбца: {column_input}",
			reply_markup=simple_back_kb("admin:crypto")
		)
		await state.clear()
	except Exception as e:
		logger.exception(f"Ошибка при сохранении адреса столбца криптовалюты: {e}")
		await message.answer("❌ Произошла ошибка при сохранении. Попробуйте еще раз.")


@admin_router.callback_query(F.data.startswith("card:view:"))
async def card_view(cb: CallbackQuery):
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	# Формируем информацию о карте
	text = f"💳 {card['name']}"
	
	# Получаем привязанные ячейки для этой карты
	card_columns = await db.list_card_columns(card_id=card_id)
	if card_columns:
		# Формируем список ячеек
		columns_text = ", ".join([col['column'] for col in card_columns])
		text += f"\n\nЯчейка: {columns_text}"
	else:
		text += "\n\nЯчейка: не привязана"
	
	# Получаем информацию о группе
	if card.get("group_id"):
		group = await db.get_card_group(card["group_id"])
		if group:
			text += f"\n\nГруппа: {group['name']}"
	else:
		text += "\n\nГруппа: не привязана"
	
	if card['user_message']:
		text += f"\n\nТекущее сообщение:\n{card['user_message']}"
	else:
		text += "\n\nСообщение не задано"
	
	text += "\n\nЧто хотите сделать?"
	
	await cb.message.edit_text(text, reply_markup=card_action_kb(card_id, "admin:cards"), parse_mode="HTML")
	await cb.answer()


@admin_router.callback_query(F.data.startswith("card:groups:"))
async def card_groups(cb: CallbackQuery):
	"""Показывает список групп для привязки карты"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	
	# Получаем все группы
	groups = await db.list_card_groups()
	
	text = "Выберите группу для карты:"
	if not groups:
		text = "Групп пока нет. Создайте новую группу:"
	
	await cb.message.edit_text(
		text,
		reply_markup=card_groups_list_kb(groups, card_id)
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("card:new_group:"))
async def card_new_group(cb: CallbackQuery, state: FSMContext):
	"""Начинает создание новой группы"""
	card_id = int(cb.data.split(":")[-1])
	
	# Сохраняем ID карты в state
	await state.update_data(card_id=card_id)
	await state.set_state(CardGroupStates.waiting_group_name)
	
	await cb.message.edit_text(
		"Введите название новой группы:",
		reply_markup=simple_back_kb(f"card:groups:{card_id}")
	)
	await cb.answer()


@admin_router.message(CardGroupStates.waiting_group_name)
async def card_group_name_input(message: Message, state: FSMContext):
	"""Обрабатывает ввод названия группы"""
	db = get_db()
	group_name = message.text.strip()
	
	if not group_name:
		await message.answer("❌ Название группы не может быть пустым. Попробуйте еще раз:")
		return
	
	data = await state.get_data()
	card_id = data.get("card_id")
	
	if not card_id:
		await message.answer("❌ Ошибка: ID карты не найден. Попробуйте начать заново.")
		await state.clear()
		return
	
	try:
		# Создаем новую группу
		group_id = await db.add_card_group(group_name)
		
		# Привязываем карту к группе
		await db.set_card_group(card_id, group_id)
		
		await message.answer(
			f"✅ Группа '{group_name}' создана и карта привязана к ней!",
			reply_markup=simple_back_kb(f"card:view:{card_id}")
		)
		await state.clear()
	except Exception as e:
		logger.exception(f"Ошибка при создании группы: {e}")
		if "UNIQUE constraint failed" in str(e):
			await message.answer("❌ Группа с таким названием уже существует. Попробуйте другое название:")
		else:
			await message.answer("❌ Произошла ошибка при создании группы. Попробуйте еще раз.")


@admin_router.callback_query(F.data.startswith("card:select_group:"))
async def card_select_group(cb: CallbackQuery):
	"""Привязывает карту к выбранной группе"""
	db = get_db()
	parts = cb.data.split(":")
	card_id = int(parts[2])
	group_id = int(parts[3])
	
	try:
		# Привязываем карту к группе
		await db.set_card_group(card_id, group_id)
		
		# Получаем информацию о группе
		group = await db.get_card_group(group_id)
		group_name = group.get("name", "Группа") if group else "Группа"
		
		await cb.answer(f"✅ Карта привязана к группе '{group_name}'", show_alert=True)
		
		# Возвращаемся к просмотру карты
		card = await db.get_card_by_id(card_id)
		if not card:
			await cb.answer("Карта не найдена", show_alert=True)
			return
		
		# Формируем информацию о карте
		text = f"💳 {card['name']}"
		if card['user_message']:
			text += f"\n\nТекущее сообщение:\n{card['user_message']}"
		else:
			text += "\n\nСообщение не задано"
		
		# Получаем привязанные ячейки для этой карты
		card_columns = await db.list_card_columns(card_id=card_id)
		if card_columns:
			# Формируем список ячеек
			columns_text = ", ".join([col['column'] for col in card_columns])
			text += f"\n\nЯчейка: {columns_text}"
		else:
			text += "\n\nЯчейка: не привязана"
		
		# Получаем информацию о группе
		if card.get("group_id"):
			group = await db.get_card_group(card["group_id"])
			if group:
				text += f"\n\nГруппа: {group['name']}"
		
		text += "\n\nЧто хотите сделать?"
		
		await cb.message.edit_text(text, reply_markup=card_action_kb(card_id, "admin:cards"), parse_mode="HTML")
	except Exception as e:
		logger.exception(f"Ошибка при привязке карты к группе: {e}")
		await cb.answer("❌ Произошла ошибка при привязке карты к группе", show_alert=True)


@admin_router.callback_query(F.data.startswith("card:edit:"))
async def card_edit(cb: CallbackQuery, state: FSMContext):
	"""Показывает форму для изменения сообщения карты"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	current = card['user_message']
	logger.debug(f"Edit user_message for card_id={card_id}")
	await state.set_state(CardUserMessageStates.waiting_message)
	await state.update_data(card_id=card_id)
	if current:
		pref = f"Текущее сообщение карты:\n\n{current}\n\n"
		await cb.message.edit_text(
			pref + "Отправьте новое сообщение этой карты.\nДля очистки отправьте: СБРОС",
			reply_markup=simple_back_kb(f"card:view:{card_id}"),
			parse_mode="HTML",
		)
	else:
		await cb.message.edit_text(
			"Отправьте новое сообщение этой карты.\nДля очистки отправьте: СБРОС",
			reply_markup=simple_back_kb(f"card:view:{card_id}"),
		)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("card:bind_column:"))
async def card_bind_column_start(cb: CallbackQuery, state: FSMContext):
	"""Начинает процесс привязки ячейки к карте"""
	db = get_db()
	source_card_id = int(cb.data.split(":")[-1])
	
	# Сохраняем ID исходной карты в state
	await state.update_data(source_card_id=source_card_id, selected_card_id=source_card_id)
	
	# Получаем информацию о карте
	selected_card = await db.get_card_by_id(source_card_id)
	if not selected_card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	card_name = selected_card['name']
	
	# Сохраняем выбранную карту в state
	await state.update_data(selected_card_id=source_card_id)
	
	# Сразу запрашиваем адрес столбца
	await state.set_state(CardColumnBindStates.waiting_column)
	await cb.message.edit_text(
		f"Выбрана карта: {card_name}\n\n"
		"Введите адрес столбца (только латинские буквы):\n"
		"Например: A, B, C, D, E, G, AS, AY",
		reply_markup=simple_back_kb(f"card:view:{source_card_id}")
	)
	await cb.answer()


@admin_router.callback_query(
	F.data.startswith("card:select_for_column:"),
	StateFilter(CardColumnBindStates.selecting_card)
)
async def card_select_for_column(cb: CallbackQuery, state: FSMContext):
	"""Обрабатывает выбор карты для привязки ячейки"""
	db = get_db()
	data = await state.get_data()
	source_card_id = data.get("source_card_id")
	selected_card_id = int(cb.data.split(":")[-1])
	
	# Получаем информацию о выбранной карте
	selected_card = await db.get_card_by_id(selected_card_id)
	if not selected_card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	card_name = selected_card['name']
	
	# Сохраняем выбранную карту в state
	await state.update_data(selected_card_id=selected_card_id)
	
	# Сразу запрашиваем адрес столбца
	await state.set_state(CardColumnBindStates.waiting_column)
	await cb.message.edit_text(
		f"Выбрана карта: {card_name}\n\n"
		"Введите адрес столбца (только латинские буквы):\n"
		"Например: A, B, C, D, E, G, AS, AY",
		reply_markup=simple_back_kb(f"card:view:{source_card_id}")
	)
	await cb.answer()


@admin_router.message(CardColumnBindStates.waiting_column)
async def card_column_waiting_column(message: Message, state: FSMContext):
	"""Обрабатывает ввод адреса столбца"""
	db = get_db()
	column_input = message.text.strip().upper()  # Приводим к верхнему регистру
	
	if not column_input:
		await message.answer("❌ Адрес столбца не может быть пустым. Попробуйте еще раз:")
		return
	
	# Проверка на русские символы
	import re
	if re.search(r'[А-ЯЁа-яё]', column_input):
		await message.answer("❌ Адрес столбца должен содержать только латинские буквы. Русские символы не допускаются. Попробуйте еще раз:")
		return
	
	# Проверка на допустимые символы (только латинские буквы)
	if not re.match(r'^[A-Z]+$', column_input):
		await message.answer("❌ Адрес столбца должен содержать только латинские буквы (A-Z). Попробуйте еще раз:")
		return
	
	# Получаем данные из state
	data = await state.get_data()
	source_card_id = data.get("source_card_id")
	selected_card_id = data.get("selected_card_id")
	
	if not selected_card_id:
		await message.answer("❌ Ошибка: ID карты не найден. Попробуйте начать заново.")
		await state.clear()
		return
	
	# Сохраняем привязку
	try:
		await db.set_card_column(selected_card_id, column_input)
		selected_card = await db.get_card_by_id(selected_card_id)
		
		await message.answer(
			f"✅ Ячейка успешно привязана!\n\n"
			f"Карта: {selected_card['name']}\n"
			f"Адрес столбца: {column_input}",
			reply_markup=simple_back_kb(f"card:view:{source_card_id}")
		)
		await state.clear()
	except Exception as e:
		logger.exception(f"Ошибка при сохранении привязки ячейки: {e}")
		await message.answer("❌ Произошла ошибка при сохранении. Попробуйте еще раз.")


@admin_router.callback_query(F.data.startswith("card:delete:"))
async def card_delete(cb: CallbackQuery):
	"""Удаляет карту из базы данных"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	# Удаляем карту (связи удалятся автоматически благодаря CASCADE)
	await db.delete_card(card_id)
	logger.debug(f"Deleted card_id={card_id}")
	
	text = f"💳 {card['name']}\n\n✅ Карта удалена из базы данных"
	
	await cb.message.edit_text(text, reply_markup=simple_back_kb("admin:cards"))
	await cb.answer("Карта удалена ✅")


@admin_router.message(CardUserMessageStates.waiting_message)
async def card_set_user_message(message: Message, state: FSMContext):
	db = get_db()
	data = await state.get_data()
	card_id = int(data.get("card_id"))
	# Используем html_text для сохранения форматирования, но проверяем "СБРОС" по чистому тексту
	plain_text = (message.text or message.caption or "").strip()
	logger.debug(f"Set user_message for card_id={card_id}, reset={(plain_text.upper()=='СБРОС')}")
	if plain_text.upper() == "СБРОС":
		await db.set_card_user_message(card_id, None)
		await state.clear()
		# Получаем информацию о карте для возврата
		card = await db.get_card_by_id(card_id)
		if card:
			await message.answer("Сообщение карты очищено ✅", reply_markup=simple_back_kb(f"card:view:{card_id}"))
		else:
			await message.answer("Сообщение карты очищено ✅", reply_markup=admin_menu_kb())
		return
	# Сохраняем текст с HTML форматированием (html_text автоматически конвертирует entities в HTML)
	html_text = message.html_text or message.html_caption or plain_text
	await db.set_card_user_message(card_id, html_text)
	await state.clear()
	# Получаем информацию о карте для возврата
	card = await db.get_card_by_id(card_id)
	if card:
		await message.answer("Сообщение карты сохранено ✅", reply_markup=simple_back_kb(f"card:view:{card_id}"))
	else:
		await message.answer("Сообщение карты сохранено ✅", reply_markup=admin_menu_kb())


@admin_router.callback_query(F.data == "card:add")
async def add_card_start(cb: CallbackQuery, state: FSMContext):
	await state.set_state(AddCardStates.waiting_name)
	await cb.message.edit_text("Введите название карты:", reply_markup=simple_back_kb("admin:cards"))
	await cb.answer()


@admin_router.message(AddCardStates.waiting_name)
async def add_card_name(message: Message, state: FSMContext):
	db = get_db()
	name = (message.text or "").strip()
	if not name:
		await message.answer("Название не должно быть пустым")
		return
	logger.debug(f"Add card with name={name!r}")
	card_id = await db.add_card(name, details="")
	# сразу предложим задать сообщение карты
	await state.set_state(CardUserMessageStates.waiting_message)
	await state.update_data(card_id=card_id)
	await message.answer("Карта создана. Отправьте сообщение карты (или 'СБРОС' для очистки).", reply_markup=simple_back_kb("admin:cards"))


async def render_users_page(cb: CallbackQuery, page: int = 0) -> None:
	db = get_db()
	rows = await db.list_users_with_binding()
	items: List[Tuple[int, str]] = []
	for r in rows:
		if r["full_name"]:
			label = r["full_name"]
		elif r["username"]:
			label = f"@{r['username']}"
		elif r["tg_id"]:
			label = f"tg_id: {r['tg_id']}"
		else:
			label = f"ID {r['user_id']}"
		if r["cards"]:
			card_names = ", ".join(card["card_name"] for card in r["cards"])
			label += f" → {card_names}"
		items.append((r["user_id"], label))
	total = len(items)
	logger.debug(f"Show users: total={total} page={page}")
	if total == 0:
		text = "Пользователи не найдены."
		reply_markup = users_list_kb([], back_to="admin:back")
	else:
		total_pages = (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE
		page = max(0, min(page, total_pages - 1))
		start = page * USERS_PER_PAGE
		end = start + USERS_PER_PAGE
		page_items = items[start:end]
		text = f"Пользователи (стр. {page+1}/{total_pages}, всего: {total}):"
		reply_markup = users_list_kb(
			page_items,
			back_to="admin:back",
			page=page,
			per_page=USERS_PER_PAGE,
			total=total,
		)
	await cb.message.edit_text(text, reply_markup=reply_markup)


@admin_router.callback_query(F.data == "admin:users")
async def admin_users(cb: CallbackQuery):
	await render_users_page(cb, page=0)
	await cb.answer()


@admin_router.callback_query((F.data.startswith("admin:users:")) & (F.data != "admin:users:noop"))
async def admin_users_page(cb: CallbackQuery):
	part = cb.data.split(":")
	try:
		page = int(part[2])
	except (IndexError, ValueError):
		page = 0
	await render_users_page(cb, page=page)
	await cb.answer()


@admin_router.callback_query(F.data == "admin:users:noop")
async def admin_users_noop(cb: CallbackQuery):
	await cb.answer()


async def _update_crypto_values_in_stats(
	bot: Bot,
	chat_id: int,
	message_id: int,
	sheet_id: str,
	credentials_path: str,
	crypto_columns: List[Dict[str, str]],
	base_lines: List[str]
):
	"""
	Обновляет значения криптовалют в сообщении статистики после их загрузки.
	"""
	from app.google_sheets import get_crypto_values_from_row_4
	
	try:
		logger.info(f"Начинаем загрузку значений криптовалют из строки 4. Криптовалют: {len(crypto_columns)}")
		crypto_values = await get_crypto_values_from_row_4(
			sheet_id,
			credentials_path,
			crypto_columns
		)
		
		logger.info(f"Получены значения криптовалют: {crypto_values}")
		
		# Формируем строки для раздела "Крипта"
		crypto_lines = ["", "<b>₿ Крипта</b>"]
		
		for crypto in crypto_columns:
			crypto_type = crypto.get("crypto_type", "")
			column = crypto.get("column", "")
			value = crypto_values.get(crypto_type)
			
			logger.debug(f"Обработка {crypto_type}: column={column}, value={value}, type={type(value)}")
			
			# Проверяем, что значение не None и не пустая строка
			if value is not None and str(value).strip():
				# Пытаемся форматировать как число, если возможно
				try:
					# Если это число, форматируем его
					num_value = float(str(value).replace(",", ".").replace(" ", ""))
					# Форматируем с разделителями тысяч, только целая часть
					formatted_value = f"{int(round(num_value)):,}".replace(",", " ")
					logger.debug(f"Отформатировано значение для {crypto_type}: {formatted_value}")
				except (ValueError, AttributeError):
					# Если не число, используем как есть
					formatted_value = str(value).strip()
					logger.debug(f"Использовано исходное значение для {crypto_type}: {formatted_value}")
				
				crypto_lines.append(f"<code>{crypto_type} = {formatted_value} USD</code>")
			else:
				logger.warning(f"Значение для {crypto_type} пустое или None (column={column})")
				crypto_lines.append(f"<code>{crypto_type} = —</code>")
		
		# Объединяем базовые строки и строки с криптовалютами
		all_lines = base_lines + crypto_lines
		text = "\n".join(all_lines)
		
		# Обновляем сообщение
		try:
			await bot.edit_message_text(
				chat_id=chat_id,
				message_id=message_id,
				text=text,
				reply_markup=simple_back_kb("admin:back"),
				parse_mode="HTML"
			)
			logger.info("Сообщение статистики успешно обновлено с значениями криптовалют")
		except Exception as e:
			logger.exception(f"Ошибка обновления сообщения статистики: {e}")
		
	except Exception as e:
		logger.exception(f"Ошибка загрузки значений криптовалют: {e}")
		# Пытаемся обновить сообщение с сообщением об ошибке
		try:
			crypto_lines = ["", "<b>₿ Крипта</b>", "<i>Ошибка загрузки данных</i>"]
			all_lines = base_lines + crypto_lines
			text = "\n".join(all_lines)
			
			await bot.edit_message_text(
				chat_id=chat_id,
				message_id=message_id,
				text=text,
				reply_markup=simple_back_kb("admin:back"),
				parse_mode="HTML"
			)
		except Exception as update_error:
			logger.exception(f"Ошибка обновления сообщения с ошибкой: {update_error}")


@admin_router.callback_query(F.data == "admin:stats")
async def admin_stats(cb: CallbackQuery):
	db = get_db()
	stats = await db.get_stats_summary()
	lines = [
		"<b>📊 Статистика</b>",
		f"<code>👥 Пользователи: {stats['total_users']:>4}</code>",
		f"<code>📤 Выдачи:      {stats['total_deliveries']:>4}</code>",
	]
	top_recent = stats.get("top_recent") or []
	top_inactive = stats.get("top_inactive") or []
	if top_recent:
		lines.append("")
		lines.append("<b>🔥 Топ-5 по активности</b>")
		max_delivery = max((entry["delivery_count"] for entry in top_recent), default=1)
		for entry in top_recent:
			if entry["full_name"]:
				label = entry["full_name"]
			elif entry["username"]:
				label = f"@{entry['username']}"
			elif entry["tg_id"]:
				label = f"tg_id: {entry['tg_id']}"
			else:
				label = f"ID {entry['user_id']}"
			count = entry["delivery_count"]
			last_relative = format_relative(entry.get("last_interaction_at"))
			bar = render_bar(count, max_delivery)
			lines.append(
				f"<code>{bar} {count:>3}</code> {escape(label)} <i>({last_relative})</i>"
			)
	if top_inactive:
		lines.append("")
		lines.append("<b>🕒 Топ-7 по давности активности</b>")
		now_ts = int(datetime.now().timestamp())
		inactivity_values = []
		for entry in top_inactive:
			ts = entry.get("last_interaction_at")
			if ts:
				inactivity_values.append(max(0, now_ts - ts))
			else:
				inactivity_values.append(0)
		max_inactivity = max(inactivity_values or [1])
		for idx, entry in enumerate(top_inactive):
			inactivity = inactivity_values[idx] if idx < len(inactivity_values) else 0
			if entry["full_name"]:
				label = entry["full_name"]
			elif entry["username"]:
				label = f"@{entry['username']}"
			elif entry["tg_id"]:
				label = f"tg_id: {entry['tg_id']}"
			else:
				label = f"ID {entry['user_id']}"
			last_relative = format_relative(entry.get("last_interaction_at"))
			bar = render_bar(inactivity, max_inactivity)
			count = entry["delivery_count"]
			lines.append(
				f"<code>{bar} {count:>3}</code> {escape(label)} <i>({last_relative})</i>"
			)
	if not top_recent and not top_inactive:
		lines.append("")
		lines.append("Нет данных по пользователям.")
	
	# Добавляем раздел "Крипта" с заглушками "Загрузка..."
	from app.config import get_settings
	from app.google_sheets import get_crypto_values_from_row_4
	
	settings = get_settings()
	crypto_columns = await db.list_crypto_columns()
	
	if crypto_columns and settings.google_sheet_id and settings.google_credentials_path:
		lines.append("")
		lines.append("<b>₿ Крипта</b>")
		
		# Добавляем заглушки "Загрузка..." для каждой криптовалюты
		for crypto in crypto_columns:
			crypto_type = crypto.get("crypto_type", "")
			lines.append(f"<code>{crypto_type} = Загрузка...</code>")
	
	# Отправляем сообщение сразу со статистикой активности
	text = "\n".join(lines)
	await cb.message.edit_text(text, reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")
	await cb.answer()
	
	# Асинхронно загружаем значения криптовалют и обновляем сообщение
	if crypto_columns and settings.google_sheet_id and settings.google_credentials_path:
		# Сохраняем базовые строки (без раздела "Крипта")
		base_lines = lines[:-len(crypto_columns)-2]  # Все строки кроме раздела "Крипта"
		
		# Запускаем задачу обновления в фоне
		asyncio.create_task(_update_crypto_values_in_stats(
			cb.bot,
			cb.message.chat.id,
			cb.message.message_id,
			settings.google_sheet_id,
			settings.google_credentials_path,
			crypto_columns,
			base_lines
		))


@admin_router.callback_query(F.data.startswith("user:view:"))
async def user_view(cb: CallbackQuery):
	db = get_db()
	user_id = int(cb.data.split(":")[-1])
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	
	# Формируем информацию о пользователе для заголовка
	parts = []
	if user["full_name"]:
		parts.append(user["full_name"])
	if user["username"]:
		parts.append(f"@{user['username']}")
	if user["tg_id"]:
		parts.append(f"(tg_id: {user['tg_id']})")
	
	if not parts:
		text = f"ID: {user['user_id']}"
	else:
		text = " ".join(parts)
	
	if user["cards"]:
		text += "\n\nТекущие привязки:"
		for card in user["cards"]:
			text += f"\n• {card['card_name']}"
	else:
		text += "\n\nНе привязан к карте"
	
	text += "\n\nЧто хотите сделать?"
	
	await cb.message.edit_text(text, reply_markup=user_action_kb(user_id, "admin:users"))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("user:bind:") & ~F.data.startswith("user:bind:card:"))
async def user_bind(cb: CallbackQuery):
	"""Показывает список карт для привязки к пользователю"""
	db = get_db()
	# Формат: user:bind:{user_id}
	user_id = int(cb.data.split(":")[-1])
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	
	# Получаем список всех карт
	rows = await db.list_cards()
	cards = [(r[0], r[1]) for r in rows]
	
	# Формируем информацию о пользователе для заголовка
	parts = []
	if user["full_name"]:
		parts.append(user["full_name"])
	if user["username"]:
		parts.append(f"@{user['username']}")
	if user["tg_id"]:
		parts.append(f"(tg_id: {user['tg_id']})")
	
	if not parts:
		text = f"ID: {user['user_id']}"
	else:
		text = " ".join(parts)
	
	if user["cards"]:
		text += "\n\nТекущие привязки:"
		for card in user["cards"]:
			text += f"\n• {card['card_name']}"
	else:
		text += "\n\nНе привязан к карте"
	
	if not cards:
		text += "\n\n⚠️ Нет доступных карт для привязки"
		await cb.message.edit_text(text, reply_markup=simple_back_kb(f"user:view:{user_id}"))
	else:
		text += "\n\nВыберите карту для изменения привязки:"
		selected_ids = [card["card_id"] for card in user["cards"]]
		# Используем специальную клавиатуру для выбора карты с указанием user_id
		await cb.message.edit_text(
			text,
			reply_markup=user_card_select_kb(cards, user_id, f"user:view:{user_id}", selected_ids),
		)
	
	await cb.answer()


@admin_router.callback_query(F.data.startswith("user:delete:"))
async def user_delete(cb: CallbackQuery):
	"""Удаляет пользователя из базы данных"""
	db = get_db()
	user_id = int(cb.data.split(":")[-1])
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	
	# Удаляем пользователя (связи удалятся автоматически благодаря CASCADE)
	await db.delete_user(user_id)
	logger.debug(f"Deleted user_id={user_id}")
	
	# Формируем информацию для подтверждения
	parts = []
	if user["full_name"]:
		parts.append(user["full_name"])
	if user["username"]:
		parts.append(f"@{user['username']}")
	if user["tg_id"]:
		parts.append(f"(tg_id: {user['tg_id']})")
	
	if not parts:
		text = f"ID: {user['user_id']}"
	else:
		text = " ".join(parts)
	
	text += "\n\n✅ Пользователь удален из базы данных"
	
	await cb.message.edit_text(text, reply_markup=simple_back_kb("admin:users"))
	await cb.answer("Пользователь удален ✅")


@admin_router.callback_query(F.data.startswith("user:bind:card:"))
async def user_bind_card(cb: CallbackQuery):
	db = get_db()
	# Формат: user:bind:card:{user_id}:{card_id}
	parts = cb.data.split(":")
	user_id = int(parts[3])
	card_id = int(parts[4])

	# Получаем информацию о пользователе до изменения
	user_before = await db.get_user_by_id(user_id)
	if not user_before:
		await cb.answer("Ошибка: пользователь не найден", show_alert=True)
		return
	bound_ids_before = {card["card_id"] for card in user_before.get("cards", [])}
	
	# Получаем информацию о карте и список всех карт
	rows = await db.list_cards()
	cards = [(r[0], r[1]) for r in rows]
	card_name = next((name for cid, name in cards if cid == card_id), None)

	# Привязываем или отвязываем карту
	if card_id in bound_ids_before:
		await db.unbind_user_from_card(user_id, card_id)
		action_text = f"❎ Карта {card_name if card_name else card_id} отвязана"
		alert_text = "Карта отвязана ❎"
		logger.debug(f"Unbound user_id={user_id} from card_id={card_id}")
	else:
		await db.bind_user_to_card(user_id, card_id)
		action_text = f"✅ Карта {card_name if card_name else card_id} привязана"
		alert_text = "Карта привязана ✅"
		logger.debug(f"Bound user_id={user_id} to card_id={card_id}")
	
	# Получаем обновленную информацию о пользователе
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Ошибка: пользователь не найден", show_alert=True)
		return
	
	# Формируем текст подтверждения
	parts_user = []
	if user["full_name"]:
		parts_user.append(user["full_name"])
	if user["username"]:
		parts_user.append(f"@{user['username']}")
	if user["tg_id"]:
		parts_user.append(f"(tg_id: {user['tg_id']})")
	
	if not parts_user:
		text = f"ID: {user['user_id']}"
	else:
		text = " ".join(parts_user)
	
	if user["cards"]:
		text += "\n\nТекущие привязки:"
		for card in user["cards"]:
			text += f"\n• {card['card_name']}"
	else:
		text += "\n\nНе привязан к карте"
	
	text += f"\n\n{action_text}"
	
	selected_ids = [card["card_id"] for card in user.get("cards", [])]
	text += "\n\nВыберите карту для изменения привязки:"
	await cb.message.edit_text(
		text,
		reply_markup=user_card_select_kb(cards, user_id, f"user:view:{user_id}", selected_ids),
	)
	await cb.answer(alert_text)


# Обработчик ввода количества криптовалюты - должен быть ПЕРЕД handle_forwarded_from_admin
@admin_router.message(ForwardBindStates.editing_crypto_amount)
async def crypto_change_amount_process(message: Message, state: FSMContext):
	"""Обработка ввода нового количества криптовалюты"""
	current_state = await state.get_state()
	is_forward = bool(getattr(message, "forward_origin", None) or getattr(message, "forward_from", None))
	logger.info(f"🔔🔔🔔 ОБРАБОТЧИК editing_crypto_amount: message_id={message.message_id}, text='{message.text[:100] if message.text else None}', state={current_state}, is_forward={is_forward}")
	# Если это пересылка, не обрабатываем здесь - пусть обрабатывается в handle_forwarded_from_admin
	if is_forward:
		logger.warning(f"⚠️ Пересылка попала в editing_crypto_amount, пропускаем: message_id={message.message_id}")
		return
	logger.info(f"📝 Получен ввод количества USD: {message.text}, состояние: {current_state}")
	try:
		usd_amount = float(message.text.replace(",", "."))
		if usd_amount <= 0:
			await message.answer("❌ Количество USD должно быть больше нуля. Попробуйте еще раз:")
			return
		
		logger.info(f"✅ Парсинг USD успешен: {usd_amount}")
		data = await state.get_data()
		row_index = data.get("current_row_index", 0)
		rows_data = data.get("multi_forward_rows", [])
		
		# Убеждаемся, что строка существует
		while len(rows_data) <= row_index:
			rows_data.append({"crypto_data": None, "cash_data": None, "card_data": None, "row_index": len(rows_data)})
		
		row = rows_data[row_index]
		crypto_data = row.get("crypto_data")
		
		# Обновляем или создаем данные криптовалюты
		if crypto_data:
			currency = crypto_data.get("currency", "BTC")
			crypto_data["usd_amount"] = usd_amount
			crypto_data["value"] = usd_amount  # Для обратной совместимости
			crypto_data["display"] = f"${int(round(usd_amount))} ({currency})"
			logger.info(f"✅ Обновлена криптовалюта: USD={usd_amount}, currency={currency}")
		else:
			# Создаем новую запись криптовалюты
			logger.info("⚠️ Криптовалюта не найдена в строке, создаем новую запись")
			currency = "BTC"  # Валюта по умолчанию
			crypto_data = {
				"type": "crypto",
				"usd_amount": usd_amount,
				"value": usd_amount,  # Для обратной совместимости
				"currency": currency,
				"display": f"${int(round(usd_amount))} ({currency})"
			}
			logger.info(f"✅ Создана криптовалюта: USD={usd_amount}, currency={currency}")
		
		row["crypto_data"] = crypto_data
		rows_data[row_index] = row
		
		# Если валюта XMR, проверяем выбранный номер XMR
		selected_xmr_numbers = data.get("selected_xmr_numbers", {})
		selected_xmr = selected_xmr_numbers.get(row_index)
		
		if crypto_data.get("currency") == "XMR" and not selected_xmr:
			# Если номер XMR не выбран, показываем кнопки выбора XMR
			await state.update_data(multi_forward_rows=rows_data)
			await state.set_state(ForwardBindStates.collecting_multi_forward)
			
			from app.keyboards import multi_forward_select_kb
			
			# Показываем клавиатуру с кнопками XMR-1, XMR-2, XMR-3
			message_text = await format_multi_forward_message_text(rows_data)
			await message.answer(
				message_text,
				reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
			)
			return
		
		# Обновляем данные в state
		await state.update_data(multi_forward_rows=rows_data)
		await state.set_state(ForwardBindStates.collecting_multi_forward)
		logger.info(f"✅ Состояние обновлено на: {await state.get_state()}")
		
		# Обновляем кнопки в основном сообщении
		from app.keyboards import multi_forward_select_kb
		
		# Обновляем сообщение с кнопками
		buttons_message_id = data.get("multi_forward_buttons_msg_id")
		if buttons_message_id:
			try:
				message_text = await format_multi_forward_message_text(rows_data)
				await message.bot.edit_message_text(
					chat_id=message.chat.id,
					message_id=buttons_message_id,
					text=message_text,
					reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
				)
			except Exception as e:
				logger.exception(f"Ошибка обновления сообщения с кнопками: {e}")
		
		# Возвращаемся к основному меню с двумя кнопками
		message_text = await format_multi_forward_message_text(rows_data)
		await message.answer(
			f"✅ Количество обновлено\n\n{message_text}",
			reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
		)
		
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число, например: 100")
	except Exception as e:
		logger.exception(f"Ошибка обработки количества криптовалюты: {e}")
		await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")


@admin_router.message(ForwardBindStates.editing_cash_amount)
async def cash_change_amount_process(message: Message, state: FSMContext):
	"""Обработка ввода нового количества наличных"""
	current_state = await state.get_state()
	is_forward = bool(getattr(message, "forward_origin", None) or getattr(message, "forward_from", None))
	logger.info(f"🔔🔔🔔 ОБРАБОТЧИК editing_cash_amount: message_id={message.message_id}, text='{message.text[:100] if message.text else None}', state={current_state}, is_forward={is_forward}")
	# Если это пересылка, не обрабатываем здесь - пусть обрабатывается в handle_forwarded_from_admin
	if is_forward:
		logger.warning(f"⚠️ Пересылка попала в editing_cash_amount, пропускаем: message_id={message.message_id}")
		return
	logger.info(f"📝 Получен ввод количества наличных: {message.text}, состояние: {current_state}")
	try:
		amount = int(float(message.text.replace(",", ".")))
		if amount <= 0:
			await message.answer("❌ Количество должно быть больше нуля. Попробуйте еще раз:")
			return
		
		logger.info(f"✅ Парсинг количества успешен: {amount}")
		data = await state.get_data()
		row_index = data.get("current_row_index", 0)
		rows_data = data.get("multi_forward_rows", [])
		selected_card_for_cash = data.get("selected_card_for_cash")
		
		# Убеждаемся, что строка существует
		while len(rows_data) <= row_index:
			rows_data.append({"crypto_data": None, "cash_data": None, "card_data": None, "row_index": len(rows_data)})
		
		row = rows_data[row_index]
		cash_data = row.get("cash_data")
		
		# Если была выбрана карта для наличных, сохраняем её
		if selected_card_for_cash:
			row["card_data"] = selected_card_for_cash
			logger.info(f"✅ Сохранена выбранная карта для наличных: {selected_card_for_cash.get('display')}")
		
		# Обновляем или создаем данные наличных
		if cash_data:
			currency = cash_data.get("currency", "RUB")
			cash_data["value"] = amount
			cash_data["display"] = f"{amount} {currency}"
			logger.info(f"✅ Обновлены наличные: {cash_data.get('display')}")
		else:
			# Создаем новую запись наличных
			logger.info("⚠️ Наличные не найдены в строке, создаем новую запись")
			currency = "RUB"  # Валюта по умолчанию
			cash_data = {
				"type": "cash",
				"value": amount,
				"currency": currency,
				"display": f"{amount} {currency}"
			}
			logger.info(f"✅ Созданы наличные: {cash_data.get('display')}")
		
		row["cash_data"] = cash_data
		rows_data[row_index] = row
		
		# Очищаем выбранную карту из state
		await state.update_data(selected_card_for_cash=None)
		
		# Обновляем данные в state
		await state.update_data(multi_forward_rows=rows_data)
		await state.set_state(ForwardBindStates.collecting_multi_forward)
		logger.info(f"✅ Состояние обновлено на: {await state.get_state()}")
		
		# Обновляем кнопки в основном сообщении
		from app.keyboards import multi_forward_select_kb
		
		selected_xmr_numbers = data.get("selected_xmr_numbers", {})
		
		# Обновляем сообщение с кнопками
		buttons_message_id = data.get("multi_forward_buttons_msg_id")
		if buttons_message_id:
			try:
				message_text = await format_multi_forward_message_text(rows_data)
				await message.bot.edit_message_text(
					chat_id=message.chat.id,
					message_id=buttons_message_id,
					text=message_text,
					reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
				)
			except Exception as e:
				logger.exception(f"Ошибка обновления сообщения с кнопками: {e}")
		
		# Возвращаемся к основному меню с двумя кнопками
		message_text = await format_multi_forward_message_text(rows_data)
		await message.answer(
			f"✅ Количество обновлено\n\n{message_text}",
			reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
		)
		
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число, например: 5020")
	except Exception as e:
		logger.exception(f"Ошибка обработки количества наличных: {e}")
		await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")


# Handle any message and process forwarding logic for admins
# Важно: этот обработчик должен быть ПОСЛЕ обработчика editing_crypto_amount
# чтобы не перехватывать сообщения в состоянии редактирования
@admin_router.message()
async def handle_forwarded_from_admin(message: Message, bot: Bot, state: FSMContext):
	# Пропускаем команды - они обрабатываются отдельными обработчиками
	if message.text and message.text.startswith("/"):
		return
	
	# Логируем ВСЕ сообщения, которые попадают в обработчик (ДАЖЕ ДО ПРОВЕРКИ АДМИНА)
	text = message.text or message.caption or ""
	is_forward = bool(getattr(message, "forward_origin", None) or getattr(message, "forward_from", None))
	current_state_before_check = await state.get_state()
	
	# ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ для отладки третьего сообщения
	logger.info(f"🔔🔔🔔 ВХОДЯЩЕЕ СООБЩЕНИЕ (ДО ПРОВЕРКИ): message_id={message.message_id}, is_forward={is_forward}, text='{text[:200]}', from_user={message.from_user.id if message.from_user else None}, state={current_state_before_check}")
	logger.info(f"📋 ДЕТАЛИ СООБЩЕНИЯ: message_id={message.message_id}, chat_id={message.chat.id if message.chat else None}, date={message.date if hasattr(message, 'date') else None}")
	logger.info(f"📋 ПОЛНЫЙ ТЕКСТ: '{text}'")
	logger.info(f"📋 СОСТОЯНИЕ FSM: {current_state_before_check}")
	
	# Получаем данные состояния для отладки
	try:
		state_data = await state.get_data()
		logger.info(f"📋 ДАННЫЕ СОСТОЯНИЯ: multi_forward_messages={len(state_data.get('multi_forward_messages', []))}, multi_forward_ready={state_data.get('multi_forward_ready', False)}, buttons_msg_id={state_data.get('multi_forward_buttons_msg_id', None)}")
		if state_data.get('multi_forward_messages'):
			logger.info(f"📋 СУЩЕСТВУЮЩИЕ СООБЩЕНИЯ: {[msg.get('message_id') for msg in state_data.get('multi_forward_messages', [])]}")
	except Exception as e:
		logger.warning(f"⚠️ Ошибка получения данных состояния: {e}")
	
	# Проверяем админа ПЕРЕД обработкой, чтобы третье сообщение тоже обрабатывалось
	db = get_db()
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not message.from_user or not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		logger.info(f"❌ Сообщение {message.message_id} от не-админа или нет from_user, пропускаем")
		return
	
	# ВАЖНО: Если это пересылка и мы в состоянии collecting_multi_forward, это может быть второе или третье сообщение
	if is_forward and current_state_before_check == ForwardBindStates.collecting_multi_forward:
		logger.info(f"🔔 Сообщение в состоянии collecting_multi_forward: message_id={message.message_id}, text='{text[:200]}'")
	
	# Проверяем, есть ли активное состояние сбора множественных пересылок
	current_state = await state.get_state()
	logger.info(f"📨 handle_forwarded_from_admin: состояние={current_state}, текст={text[:50] if text else 'None'}")
	
	data = await state.get_data()
	multi_forward_ready = data.get("multi_forward_ready", False)
	
	logger.info(f"🔍 Проверка множественной пересылки: состояние={current_state}, multi_forward_ready={multi_forward_ready}, текст='{(message.text or message.caption or '')[:50]}'")
	
	# УПРОЩЕННАЯ ЛОГИКА: Если мы в состоянии сбора множественных пересылок, просто добавляем сообщение
	# Собираем до 3 сообщений, затем парсим все сразу
	if current_state == ForwardBindStates.collecting_multi_forward:
		logger.info(f"🚨🚨🚨 ПОПАЛИ В БЛОК ОБРАБОТКИ МНОЖЕСТВЕННОЙ ПЕРЕСЫЛКИ! message_id={message.message_id}, state={current_state}")
		# Собираем множественные пересылки
		messages_list = data.get("multi_forward_messages", [])
		buttons_message_id = data.get("multi_forward_buttons_msg_id")  # ID сообщения с кнопками
		session_key = data.get("multi_forward_session_key")  # Уникальный ключ сеанса
		
		text = message.text or message.caption or ""
		logger.info(f"📝 ОБРАБОТКА СООБЩЕНИЯ: message_id={message.message_id}, text='{text[:200]}'")
		parsed = parse_forwarded_message(text)
		logger.info(f"📝 Результат парсинга: parsed={parsed}, message_id={message.message_id}")
		
		# ВАЖНО: Для криптовалюты проверяем даже если parsed уже определен
		# parse_forwarded_message может не распознать "0.8" правильно
		normalized_text = re.sub(r'\s+', ' ', text.strip())
		
		# ПРИОРИТЕТ: Если текст - это просто число с точкой (например "0.8"), это точно криптовалюта
		# Проверяем это ПЕРВЫМ, до проверки других типов
		if re.match(r'^\d+\.\d+$', normalized_text):
			amount = float(normalized_text)
			currency = detect_crypto_type(amount)
			parsed = {
				"type": "crypto",
				"value": amount,
				"currency": currency,
				"display": f"{amount} {currency}"
			}
			logger.info(f"✅ Текст является числом с точкой - переопределено как криптовалюта: {parsed}")
		# Если тип не определен ИЛИ это может быть криптовалюта (число с точкой), пытаемся определить заново
		elif parsed.get("type") == "unknown" or (parsed.get("type") != "crypto" and re.search(r'^\d+\.\d+$', normalized_text)):
			logger.warning(f"⚠️ Тип не определен или требует переопределения: parsed={parsed}, text='{text[:200]}'")
			
			# Проверяем, есть ли в тексте число с точкой (криптовалюта)
			crypto_match = re.search(r'(?:^|\s)(\d+\.\d+)(?:\s|$)', normalized_text)
			logger.debug(f"🔍 Поиск криптовалюты в тексте: crypto_match={crypto_match}")
			if crypto_match:
				amount = float(crypto_match.group(1))
				currency = detect_crypto_type(amount)
				parsed = {
					"type": "crypto",
					"value": amount,
					"currency": currency,
					"display": f"{amount} {currency}"
				}
				logger.info(f"✅ Переопределено как криптовалюта: {parsed}")
			# Проверяем, есть ли в тексте целое число (наличные)
			else:
				cash_match = re.search(r'(?:^|\s)(\d+)(?:\s+\d+)*(?:\s+без\s+долга)?(?:\s|$)', normalized_text, re.IGNORECASE)
				logger.debug(f"🔍 Поиск наличных в тексте: cash_match={cash_match}")
				if cash_match:
					amount = int(cash_match.group(1))
					currency = detect_cash_type(amount)
					parsed = {
						"type": "cash",
						"value": amount,
						"currency": currency,
						"display": f"{amount}"
					}
					logger.info(f"✅ Переопределено как наличные: {parsed}")
				else:
					logger.warning(f"⚠️ Не найдено число в тексте")
		
		# Создаем уникальный ключ сеанса на основе первого сообщения, если его еще нет
		# Определяем первое сообщение по дате (самое раннее)
		# Это важно, т.к. при пересылке из избранного порядок может быть другим
		if not session_key and messages_list:
			# Сортируем сообщения по дате, чтобы найти самое первое
			sorted_messages = sorted(messages_list, key=lambda x: x.get("date", 0))
			first_msg = sorted_messages[0] if sorted_messages else messages_list[0]
			first_msg_id = first_msg.get("message_id")
			session_key = f"multi_{message.from_user.id}_{first_msg_id}"
			logger.info(f"🔑 Создан session_key на основе первого сообщения по дате: message_id={first_msg_id}, date={first_msg.get('date')}")
		elif not session_key:
			# Если это первое сообщение в сеансе, создаем ключ
			session_key = f"multi_{message.from_user.id}_{message.message_id}"
			logger.info(f"🔑 Создан session_key для первого сообщения: message_id={message.message_id}")
		
		# Получаем блокировку для этого сеанса
		lock_key = (message.from_user.id, session_key)
		async with _locks_lock:
			if lock_key not in _multi_forward_locks:
				_multi_forward_locks[lock_key] = asyncio.Lock()
			lock = _multi_forward_locks[lock_key]
		
		# Используем блокировку для синхронизации обработки
		async with lock:
			# Перечитываем состояние внутри блокировки
			data = await state.get_data()
			messages_list = data.get("multi_forward_messages", [])
			buttons_message_id = data.get("multi_forward_buttons_msg_id")
			
			# Проверяем, не добавлено ли уже это сообщение (по message_id)
			message_already_added = any(msg.get("message_id") == message.message_id for msg in messages_list)
			if message_already_added:
				logger.warning(f"⚠️ Сообщение {message.message_id} уже добавлено в список, пропускаем")
				return
			
			# ОГРАНИЧЕНИЕ: Максимум 3 сообщения в списке (криптовалюта, наличные, карта)
			# Удаляем дубликаты по типу ПЕРЕД добавлением нового сообщения
			# Это предотвращает добавление дубликатов при повторной пересылке тех же сообщений
			seen_types = set()
			unique_messages = []
			for msg in messages_list:
				msg_type = msg.get("parsed", {}).get("type")
				if msg_type not in seen_types:
					seen_types.add(msg_type)
					unique_messages.append(msg)
				else:
					logger.warning(f"⚠️ Удален дубликат сообщения: message_id={msg.get('message_id')}, type={msg_type}")
			messages_list = unique_messages
			
			# Проверяем, есть ли уже сообщение с таким же типом
			parsed_type = parsed.get("type")
			if parsed_type in seen_types:
				logger.warning(f"⚠️ Сообщение {message.message_id} с типом {parsed_type} уже есть в списке, пропускаем")
				return
			
			# ОГРАНИЧЕНИЕ: Максимум 3 сообщения
			if len(messages_list) >= 3:
				logger.warning(f"⚠️ Уже есть 3 сообщения в списке, пропускаем новое сообщение {message.message_id}")
				return
			
			# Добавляем новое сообщение с сохранением даты для правильного определения порядка
			messages_list.append({
				"text": text,
				"parsed": parsed,
				"message_id": message.message_id,
				"date": message.date.timestamp() if hasattr(message.date, 'timestamp') else (message.date if message.date else 0)
			})
			logger.info(f"✅ Добавлено сообщение в список. Всего сообщений: {len(messages_list)}")
			
			await state.update_data(
				multi_forward_messages=messages_list,
				multi_forward_session_key=session_key
			)
			
			# Объединяем имена пользователей с картами
			user_name = None
			for msg in messages_list:
				if msg["parsed"].get("type") == "user_name":
					user_name = msg["parsed"].get("user_name")
					break
			
			# Если нашли имя, добавляем его к картам
			if user_name:
				for msg in messages_list:
					if msg["parsed"].get("type") == "card" and not msg["parsed"].get("user_name"):
						# Сохраняем исходные данные
						original_card_name = msg["parsed"].get("card_name")
						original_display = msg["parsed"].get("display", "")
						
						# Добавляем имя пользователя
						msg["parsed"]["user_name"] = user_name
						
						# Формируем display: всегда используем card_name, если он есть
						card_name = msg["parsed"].get("card_name")
						if card_name and card_name.strip():
							# card_name есть - используем его
							msg["parsed"]["display"] = f"{card_name} ({user_name})"
						elif original_display and original_display.strip() and " (" not in original_display and " - " not in original_display:
							# card_name нет, но есть display без имени - используем display как card_name
							msg["parsed"]["display"] = f"{original_display} ({user_name})"
							# Также обновляем card_name, если он был пустым
							if not card_name:
								msg["parsed"]["card_name"] = original_display
						else:
							# Ничего не нашли - используем только имя
							msg["parsed"]["display"] = f"- {user_name}"
						
						logger.info(f"🔗 Объединено имя с картой: original_card_name={original_card_name}, card_name={msg['parsed'].get('card_name')}, user_name={user_name}, original_display={original_display}, final_display={msg['parsed'].get('display')}")
			
			# Извлекаем данные из всех сообщений ПОСЛЕ объединения имени с картой
			# Это важно, чтобы card_data содержал обновленный display
			crypto_data = None
			cash_data = None
			card_data = None
			
			# Логируем все сообщения для отладки
			logger.info(f"🔍 ИЗВЛЕЧЕНИЕ ДАННЫХ из {len(messages_list)} сообщений:")
			for i, msg in enumerate(messages_list):
				logger.info(f"  📨 Сообщение {i+1}: message_id={msg.get('message_id')}, text='{msg.get('text', '')[:100]}', parsed={msg.get('parsed', {})}")
			
			for i, msg in enumerate(messages_list):
				parsed_msg = msg["parsed"]
				msg_type = parsed_msg.get("type")
				logger.info(f"  🔍 Обработка сообщения {i+1}: type={msg_type}, parsed={parsed_msg}")
				
				if msg_type == "crypto" and not crypto_data:
					crypto_data = parsed_msg
					logger.info(f"  ✅ НАЙДЕНА КРИПТОВАЛЮТА: {crypto_data}")
				elif msg_type == "cash" and not cash_data:
					cash_data = parsed_msg
					logger.info(f"  ✅ НАЙДЕНЫ НАЛИЧНЫЕ: {cash_data}")
				elif msg_type == "card" and not card_data:
					# Используем обновленный parsed_msg (после объединения имени)
					card_data = parsed_msg.copy()  # Делаем копию, чтобы не изменять оригинал
					logger.info(f"  ✅ НАЙДЕНА КАРТА: card_name={card_data.get('card_name')}, user_name={card_data.get('user_name')}, display={card_data.get('display')}")
				else:
					logger.warning(f"  ⚠️ Сообщение {i+1} пропущено: type={msg_type}, crypto_data={bool(crypto_data)}, cash_data={bool(cash_data)}, card_data={bool(card_data)}")
			
			logger.info(f"📊 ИТОГОВЫЕ ДАННЫЕ: crypto={bool(crypto_data)} ({crypto_data if crypto_data else 'None'}), cash={bool(cash_data)} ({cash_data if cash_data else 'None'}), card={bool(card_data)} ({card_data if card_data else 'None'})")
			
			# ВАЖНО: Обновляем кнопки только после обработки всех сообщений
			# Если это второе сообщение или больше, показываем/обновляем кнопки
			# Но обновляем кнопки ТОЛЬКО если это второе сообщение (len == 2)
			# Если это третье сообщение (len == 3), кнопки уже должны быть созданы, просто обновляем их
			if len(messages_list) >= 2:
				from app.keyboards import multi_forward_select_kb
				
				# Перечитываем buttons_message_id внутри блокировки (может быть обновлен другим обработчиком)
				current_data = await state.get_data()
				buttons_message_id = current_data.get("multi_forward_buttons_msg_id")
				selected_xmr = current_data.get("selected_xmr_number")
				
				# ВАЖНО: Обновляем кнопки с актуальными данными из всех сообщений
				# Это гарантирует, что криптовалюта будет отображена даже если она пришла третьим сообщением
				if buttons_message_id:
					try:
						message_text = await format_multi_forward_message_text(crypto_data)
						await bot.edit_message_text(
							chat_id=message.chat.id,
							message_id=buttons_message_id,
							text=message_text,
							reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data, selected_xmr=selected_xmr)
						)
						# Сообщение успешно отредактировано
						await state.set_state(ForwardBindStates.collecting_multi_forward)
						await state.update_data(
							multi_forward_ready=True,
							multi_forward_buttons_msg_id=buttons_message_id,
							multi_forward_messages=messages_list,
							multi_forward_session_key=session_key
						)
						logger.info(f"✅ Сообщение с кнопками обновлено с актуальными данными. Всего сообщений: {len(messages_list)}, crypto={bool(crypto_data)}")
					except Exception as e:
						# Не удалось отредактировать - возможно сообщение было удалено
						logger.warning(f"Не удалось отредактировать сообщение с кнопками (ID: {buttons_message_id}): {e}")
						# Пытаемся удалить старое сообщение
						try:
							await bot.delete_message(chat_id=message.chat.id, message_id=buttons_message_id)
						except:
							pass
						# Сбрасываем buttons_message_id, чтобы создать новое
						buttons_message_id = None
				
				# Создаем новое сообщение с кнопками только если его нет
				# Еще раз проверяем состояние перед созданием (на случай, если другой обработчик уже создал)
				if not buttons_message_id:
					final_check = await state.get_data()
					final_buttons_id = final_check.get("multi_forward_buttons_msg_id")
					
					if final_buttons_id:
						# Другой обработчик уже создал сообщение - редактируем его
						try:
							final_selected_xmr = final_check.get("selected_xmr_number")
							message_text = await format_multi_forward_message_text(crypto_data)
							await bot.edit_message_text(
								chat_id=message.chat.id,
								message_id=final_buttons_id,
								text=message_text,
								reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data, selected_xmr=final_selected_xmr)
							)
							await state.set_state(ForwardBindStates.collecting_multi_forward)
							await state.update_data(
								multi_forward_ready=True,
								multi_forward_buttons_msg_id=final_buttons_id,
								multi_forward_messages=messages_list,
								multi_forward_session_key=session_key
							)
							logger.info(f"✅ Сообщение с кнопками обновлено после финальной проверки. Всего сообщений: {len(messages_list)}, crypto={bool(crypto_data)}")
						except Exception as e:
							logger.warning(f"Не удалось отредактировать сообщение после финальной проверки: {e}")
					else:
						# Создаем новое сообщение только если его еще нет
						# Определяем первое сообщение по дате (самое раннее)
						# Это важно, т.к. при пересылке из избранного порядок может быть другим
						sorted_messages = sorted(messages_list, key=lambda x: x.get("date", 0))
						first_msg = sorted_messages[0] if sorted_messages else (messages_list[0] if messages_list else None)
						first_message_id = first_msg.get("message_id") if first_msg else None
						logger.info(f"📌 Определено первое сообщение для reply_to_message_id: message_id={first_message_id}, date={first_msg.get('date') if first_msg else None}")
						
						message_text = await format_multi_forward_message_text(crypto_data)
						initial_selected_xmr = final_check.get("selected_xmr_number")
						
						sent_message = await message.answer(
							message_text,
							reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data, selected_xmr=initial_selected_xmr),
							reply_to_message_id=first_message_id if first_message_id else None
						)
						
						await state.set_state(ForwardBindStates.collecting_multi_forward)
						await state.update_data(
							multi_forward_ready=True,
							multi_forward_buttons_msg_id=sent_message.message_id,
							multi_forward_messages=messages_list,
							multi_forward_session_key=session_key
						)
						logger.info(f"✅ Создано новое сообщение с кнопками. Всего сообщений: {len(messages_list)}, crypto={bool(crypto_data)}")
		# ВАЖНО: Делаем return после обработки сообщения в блоке множественной пересылки
		# Третье сообщение будет обработано в отдельном вызове этой функции в финальном блоке
		logger.info(f"✅ Сообщение обработано в блоке множественной пересылки, выходим. Следующее сообщение обработается в отдельном вызове.")
		return
	
	# Обработка обычной пересылки (не множественной)
	# ВАЖНО: Если мы дошли сюда, значит сообщение не обработано в блоке выше
	# Это может быть третье сообщение, которое не попало в блок collecting_multi_forward
	# Проверяем состояние еще раз
	current_state_final = await state.get_state()
	text_final = message.text or message.caption or ""
	is_forward_final = bool(getattr(message, "forward_origin", None) or getattr(message, "forward_from", None))
	logger.info(f"🔄 ФИНАЛЬНАЯ ПРОВЕРКА СОСТОЯНИЯ: current_state_final={current_state_final}, message_id={message.message_id}, is_forward={is_forward_final}, text='{text_final[:200]}'")
	
	# ВАЖНО: Проверяем, является ли это пересылкой в состоянии collecting_multi_forward
	# Это может быть третье сообщение, которое не попало в основной блок
	# НО: если это пересылка и состояние collecting_multi_forward, то оно ДОЛЖНО было попасть в основной блок
	# Если не попало, значит что-то не так с логикой
	if current_state_final == ForwardBindStates.collecting_multi_forward and is_forward_final:
		logger.warning(f"⚠️⚠️⚠️ ВНИМАНИЕ: Сообщение {message.message_id} в состоянии collecting_multi_forward, но не попало в основной блок! Это может быть третье сообщение.")
		# Это сообщение в состоянии collecting_multi_forward, которое не попало в блок выше - обрабатываем его здесь
		# Это может быть второе, третье или последующее сообщение
		logger.info(f"🚨🚨🚨 СООБЩЕНИЕ В СОСТОЯНИИ collecting_multi_forward ОБНАРУЖЕНО В ФИНАЛЬНОМ БЛОКЕ! message_id={message.message_id}, text='{text[:200]}'")
		text = message.text or message.caption or ""
		parsed = parse_forwarded_message(text)
		logger.info(f"🔄 Продолжаем сбор множественных пересылок: состояние={current_state_final}, parsed={parsed}")
		# Получаем существующий список сообщений
		data_final = await state.get_data()
		existing_messages = data_final.get("multi_forward_messages", [])
		logger.info(f"🔄 Существующий список сообщений: {len(existing_messages)} сообщений")
		session_key = data_final.get("multi_forward_session_key")
		
		# Проверяем, не добавлено ли уже это сообщение
		message_already_added = any(msg.get("message_id") == message.message_id for msg in existing_messages)
		if message_already_added:
			logger.warning(f"⚠️ Сообщение {message.message_id} уже добавлено в список, пропускаем")
			return
		
		# ОГРАНИЧЕНИЕ: Максимум 3 сообщения в списке (криптовалюта, наличные, карта)
		# Удаляем дубликаты по типу ПЕРЕД добавлением нового сообщения
		seen_types = set()
		unique_messages = []
		for msg in existing_messages:
			msg_type = msg.get("parsed", {}).get("type")
			if msg_type not in seen_types:
				seen_types.add(msg_type)
				unique_messages.append(msg)
			else:
				logger.warning(f"⚠️ Удален дубликат сообщения: message_id={msg.get('message_id')}, type={msg_type}")
		existing_messages = unique_messages
		
		# Проверяем, есть ли уже сообщение с таким же типом
		parsed_type = parsed.get("type")
		if parsed_type in seen_types:
			logger.warning(f"⚠️ Сообщение {message.message_id} с типом {parsed_type} уже есть в списке, пропускаем")
			return
		
		# ОГРАНИЧЕНИЕ: Максимум 3 сообщения
		if len(existing_messages) >= 3:
			logger.warning(f"⚠️ Уже есть 3 сообщения в списке, пропускаем новое сообщение {message.message_id}")
			return
		
		# Если тип не определен или это криптовалюта, пытаемся переопределить
		normalized_text = re.sub(r'\s+', ' ', text.strip())
		if parsed.get("type") == "unknown" or (parsed.get("type") != "crypto" and re.search(r'^\d+\.\d+$', normalized_text)):
			logger.warning(f"⚠️ Тип не определен или требует переопределения: parsed={parsed}, text='{text[:200]}'")
			crypto_match = re.search(r'(?:^|\s)(\d+\.\d+)(?:\s|$)', normalized_text)
			if crypto_match:
				amount = float(crypto_match.group(1))
				currency = detect_crypto_type(amount)
				parsed = {
					"type": "crypto",
					"value": amount,
					"currency": currency,
					"display": f"{amount} {currency}"
				}
				logger.info(f"✅ Переопределено как криптовалюта: {parsed}")
			else:
				cash_match = re.search(r'(?:^|\s)(\d+)(?:\s+\d+)*(?:\s+без\s+долга)?(?:\s|$)', normalized_text, re.IGNORECASE)
				if cash_match:
					amount = int(cash_match.group(1))
					currency = detect_cash_type(amount)
					parsed = {
						"type": "cash",
						"value": amount,
						"currency": currency,
						"display": f"{amount}"
					}
					logger.info(f"✅ Переопределено как наличные: {parsed}")
		
		# Добавляем сообщение в существующий список с сохранением даты
		existing_messages.append({
			"text": text,
			"parsed": parsed,
			"message_id": message.message_id,
			"date": message.date.timestamp() if hasattr(message.date, 'timestamp') else (message.date if message.date else 0)
		})
		logger.info(f"✅ Добавлено сообщение в существующий список. Всего сообщений: {len(existing_messages)}")
		
		# Обновляем состояние
		await state.update_data(
			multi_forward_messages=existing_messages,
			multi_forward_session_key=session_key
		)
		
		# Извлекаем данные из всех сообщений
		crypto_data = None
		cash_data = None
		card_data = None
		
		for msg in existing_messages:
			parsed_msg = msg["parsed"]
			msg_type = parsed_msg.get("type")
			
			if msg_type == "crypto" and not crypto_data:
				crypto_data = parsed_msg
			elif msg_type == "cash" and not cash_data:
				cash_data = parsed_msg
			elif msg_type == "card" and not card_data:
				card_data = parsed_msg.copy()
		
		# Обновляем сообщение с кнопками
		buttons_message_id = data_final.get("multi_forward_buttons_msg_id")
		if buttons_message_id:
			try:
				from app.keyboards import multi_forward_select_kb
				message_text = await format_multi_forward_message_text(crypto_data)
				selected_xmr = data_final.get("selected_xmr_number")
				await bot.edit_message_text(
					chat_id=message.chat.id,
					message_id=buttons_message_id,
					text=message_text,
					reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data, selected_xmr=selected_xmr)
				)
				logger.info(f"✅ Обновлено сообщение с кнопками после добавления третьего сообщения")
			except Exception as e:
				logger.exception(f"❌ Ошибка обновления сообщения с кнопками: {e}")
		
		return
	
	# Обычная обработка пересылки
	orig_tg_id, orig_username, orig_full_name = extract_forward_profile(message)
	text = message.text or message.caption or ""
	logger.info(f"📨 Пересылка от админа {message.from_user.id}: tg_id={orig_tg_id}, username={orig_username}, full_name={orig_full_name}, text={text[:50] if text else 'нет'}")
	
	# ВАЖНО: Сначала парсим сообщение, чтобы parsed была доступна везде
	parsed = parse_forwarded_message(text)
	
	if parsed.get("type") in ["crypto", "cash", "card", "user_name"] or re.search(r'[🏦💳🆘]', text):
		# Это может быть множественная пересылка - начинаем сбор
		logger.info(f"🔍 Обнаружена возможная множественная пересылка: {parsed}")
		# Создаем уникальный ключ сеанса для этого множественного пересылки
		session_key = f"multi_{message.from_user.id}_{message.message_id}"
		
		await state.set_state(ForwardBindStates.collecting_multi_forward)
		await state.update_data(
			multi_forward_messages=[{
				"text": text,
				"parsed": parsed,
				"message_id": message.message_id,
				"date": message.date.timestamp() if hasattr(message.date, 'timestamp') else (message.date if message.date else 0)
			}],
			multi_forward_session_key=session_key
		)
		# Ждем следующее сообщение
		return
	
	# Если ID недоступен, но есть username, пытаемся найти пользователя в БД по username
	if orig_tg_id is None and orig_username:
		logger.info(f"⚠️ ID недоступен, но есть username={orig_username}, ищем пользователя в БД")
		user_by_username = await db.get_user_by_username(orig_username)
		logger.info(f"🔍 Результат поиска по username '{orig_username}': {user_by_username}")
		if user_by_username and user_by_username.get("tg_id"):
			orig_tg_id = user_by_username["tg_id"]
			logger.info(f"✅ Найден пользователь в БД по username={orig_username}, tg_id={orig_tg_id} (проблема с приватностью обойдена)")
			# Обновляем данные пользователя (get_or_create_user обновит пустые поля)
			await db.get_or_create_user(orig_tg_id, orig_username, orig_full_name)
		else:
			logger.warning(f"❌ Пользователь с username={orig_username} не найден в БД")
	
	# Если ID и username недоступны, но есть full_name (MessageOriginHiddenUser), ищем по full_name
	if orig_tg_id is None and not orig_username and orig_full_name:
		logger.info(f"⚠️ ID и username недоступны, но есть full_name='{orig_full_name}', ищем пользователя в БД по имени")
		user_by_full_name = await db.get_user_by_full_name(orig_full_name)
		logger.info(f"🔍 Результат поиска по full_name '{orig_full_name}': {user_by_full_name}")
		if user_by_full_name:
			# Запись найдена (может быть с tg_id=None для скрытых пользователей)
			user_id = user_by_full_name.get("user_id")
			orig_tg_id = user_by_full_name.get("tg_id")  # Может быть None
			orig_username = user_by_full_name.get("username")  # Может быть None
			
			if orig_tg_id:
				# Есть реальный tg_id - используем его
				logger.info(f"✅ Найден пользователь в БД по full_name='{orig_full_name}', tg_id={orig_tg_id} (MessageOriginHiddenUser обойден)")
				await db.get_or_create_user(orig_tg_id, orig_username, orig_full_name)
			else:
				# Найдена запись с NULL tg_id - это запись, созданная ранее для скрытого пользователя
				logger.info(f"✅ Найдена запись для скрытого пользователя '{orig_full_name}' (user_id={user_id}, tg_id=None). Проверяем карты...")
				# Получаем карты для этого пользователя через user_id
				cards_for_user = await db.list_cards_for_user(user_id)
				if cards_for_user:
					# У пользователя уже есть привязанные карты - продолжаем как обычно
					if len(cards_for_user) == 1:
						card = cards_for_user[0]
						user_msg = card.get("user_message")
						admin_text = "Сообщение карты отсутствует" if not user_msg else user_msg
						if user_msg:
							await message.answer(admin_text, parse_mode="HTML")
						else:
							await message.answer(admin_text)
						logger.info(f"✅ Отправлено сообщение карты для скрытого пользователя '{orig_full_name}' (user_id={user_id})")
						return
					else:
						# Несколько карт - показываем выбор
						buttons = [(card["card_id"], card["card_name"]) for card in cards_for_user]
						await state.set_state(ForwardBindStates.waiting_select_existing_card)
						await state.update_data(original_tg_id=None, user_id_for_hidden=user_id, hidden_user_name=orig_full_name)
						await message.answer(
							f"✅ Найден пользователь '{orig_full_name}' с привязанными картами.\n\nУ пользователя привязано несколько карт. Выберите нужную:",
							reply_markup=user_cards_reply_kb(buttons, 0, back_to="admin:back"),  # Используем 0, так как tg_id нет
						)
						return
				else:
					# Карт нет - показываем выбор карты для привязки
					logger.info(f"⚠️ У пользователя '{orig_full_name}' нет привязанных карт, предлагаем выбрать")
					rows = await db.list_cards()
					cards = [(r[0], r[1]) for r in rows]
					await state.set_state(ForwardBindStates.waiting_select_card)
					await state.update_data(hidden_user_name=orig_full_name, reply_only=False, existing_user_id=user_id)
					await message.answer(f"✅ Пользователь '{orig_full_name}' найден в БД, но не привязан к карте.\n\nВыберите карту для привязки:", reply_markup=cards_select_kb(cards, back_to="admin:back"))
					return
		else:
			logger.warning(f"❌ Пользователь с full_name='{orig_full_name}' не найден в БД")
	
	# Try resolve @username from text when no forward info
	if orig_tg_id is None and text:
		m = re.search(r"@([A-Za-z0-9_]{5,})", text)
		if m:
			uname = m.group(1)
			try:
				chat = await bot.get_chat(uname)
				orig_tg_id = chat.id
				orig_username = getattr(chat, "username", orig_username)
				if getattr(chat, "first_name", None) or getattr(chat, "last_name", None):
					orig_full_name = " ".join([x for x in [getattr(chat, "first_name", None), getattr(chat, "last_name", None)] if x])
				logger.debug(f"Resolved username @{uname} to id={orig_tg_id}")
			except Exception as e:
				logger.debug(f"Failed resolve username @{uname}: {e}")
				# Если не удалось резолвить через API, попробуем найти в БД
				if not orig_tg_id:
					user_by_username = await db.get_user_by_username(uname)
					if user_by_username and user_by_username.get("tg_id"):
						orig_tg_id = user_by_username["tg_id"]
						logger.info(f"Найден пользователь в БД по username из текста @{uname}, tg_id={orig_tg_id}")
	
	if orig_tg_id is not None:
		# Ensure user is saved/upserted before any binding/lookup
		user_id = await db.get_or_create_user(orig_tg_id, orig_username, orig_full_name)
		logger.info(f"💾 Пользователь сохранен/обновлен: tg_id={orig_tg_id}, user_id={user_id}, username={orig_username}")
		await db.touch_user_by_tg(orig_tg_id)
		cards_for_user = await db.get_cards_for_user_tg(orig_tg_id)
		logger.info(f"🎴 У пользователя {orig_tg_id} найдено карт: {len(cards_for_user)}")
		if cards_for_user:
			if len(cards_for_user) == 1:
				card = cards_for_user[0]
				user_msg = card.get("user_message")
				admin_text = "Сообщение карты отсутствует" if not user_msg else user_msg
				if user_msg:
					await message.answer(admin_text, parse_mode="HTML")
				else:
					await message.answer(admin_text)
				await db.log_card_delivery_by_tg(
					orig_tg_id,
					card["card_id"],
					admin_id=message.from_user.id if message.from_user else None,
				)
				if user_msg:
					try:
						await bot.send_message(chat_id=orig_tg_id, text=user_msg, parse_mode="HTML")
						logger.debug("Sent user_message to user")
					except Exception as e:
						logger.exception(f"Failed to send user_message: {e}")
				return
			buttons = [(card["card_id"], card["card_name"]) for card in cards_for_user]
			await state.set_state(ForwardBindStates.waiting_select_existing_card)
			await state.update_data(original_tg_id=orig_tg_id)
			await message.answer(
				"У пользователя привязано несколько карт. Выберите нужную:",
				reply_markup=user_cards_reply_kb(buttons, orig_tg_id, back_to="admin:back"),
			)
			return
		logger.info(f"⚠️ Пользователь {orig_tg_id} не привязан к карте, предлагаем выбрать карту")
		rows = await db.list_cards()
		cards = [(r[0], r[1]) for r in rows]
		await state.set_state(ForwardBindStates.waiting_select_card)
		await state.update_data(original_tg_id=orig_tg_id)
		await message.answer("Пользователь не привязан. Выберите карту для привязки:", reply_markup=cards_select_kb(cards, back_to="admin:back"))
		return
	# Если не удалось найти пользователя, но есть username или full_name - возможно пользователь еще не в БД или все скрыто
	if orig_tg_id is None:
		# Проверяем, есть ли хотя бы username
		if orig_username:
			# Пользователь не найден в БД, но есть username - возможно первый раз
			logger.warning(f"Не удалось определить ID пользователя, но есть username={orig_username}. Возможные причины: пользователь скрыл данные в настройках приватности Telegram или еще не взаимодействовал с ботом.")
			rows = await db.list_cards()
			cards = [(r[0], r[1]) for r in rows]
			await state.set_state(ForwardBindStates.waiting_select_card)
			await state.update_data(reply_only=True)
			warning_msg = f"⚠️ Не удалось получить ID пользователя @{orig_username}.\n\nВозможные причины:\n• Пользователь скрыл данные в настройках приватности Telegram\n• Пользователь еще не взаимодействовал с ботом\n\nВыберите карту для ответа администратору:"
			await message.answer(warning_msg, reply_markup=cards_select_kb(cards, back_to="admin:back"))
			return
		# Проверяем, есть ли full_name (MessageOriginHiddenUser)
		elif orig_full_name:
			logger.warning(f"Не удалось определить ID пользователя, но есть full_name='{orig_full_name}'. Это MessageOriginHiddenUser - пользователь полностью скрыл информацию. Пытаемся найти похожих пользователей...")
			
			# Пытаемся найти похожих пользователей по имени (частичное совпадение)
			similar_users = await db.find_similar_users_by_name(orig_full_name, limit=5)
			logger.info(f"🔍 Найдено {len(similar_users)} похожих пользователей для '{orig_full_name}'")
			
			if len(similar_users) == 1:
				# Найден один похожий - используем его автоматически
				user = similar_users[0]
				orig_tg_id = user["tg_id"]
				orig_username = user.get("username")
				logger.info(f"✅ Автоматически выбран единственный похожий пользователь: tg_id={orig_tg_id}, full_name={user['full_name']}")
				# Продолжаем обработку с найденным пользователем
				await db.get_or_create_user(orig_tg_id, orig_username, orig_full_name)
				# Не делаем return, продолжаем выполнение кода ниже
			elif len(similar_users) > 1:
				# Найдено несколько похожих - предлагаем выбрать
				await state.set_state(ForwardBindStates.waiting_select_card)
				await state.update_data(hidden_user_name=orig_full_name, similar_users=[u["tg_id"] for u in similar_users])
				
				similar_text = f"🔍 Найдено {len(similar_users)} похожих пользователей для '{orig_full_name}':\n\n"
				similar_text += "Выберите нужного пользователя:"
				await message.answer(similar_text, reply_markup=similar_users_select_kb(similar_users, orig_full_name))
				return
			else:
				# Не найдено похожих - сохраняем имя для последующей привязки
				rows = await db.list_cards()
				cards = [(r[0], r[1]) for r in rows]
				await state.set_state(ForwardBindStates.waiting_select_card)
				# Сохраняем имя скрытого пользователя в state, чтобы при выборе карты создать запись
				await state.update_data(hidden_user_name=orig_full_name, reply_only=False)
				warning_msg = f"⚠️ Пользователь '{orig_full_name}' полностью скрыл информацию (MessageOriginHiddenUser).\n\nID и username недоступны. Похожие пользователи в БД не найдены.\n\n💡 Система запомнит выбор карты для этого имени.\nКогда пользователь '{orig_full_name}' напишет боту, карта будет автоматически привязана.\n\nВыберите карту для привязки:"
				await message.answer(warning_msg, reply_markup=cards_select_kb(cards, back_to="admin:back"))
		return
	# fallback by text when no origin available
	if text:
		card = await db.find_card_by_text(text)
		logger.debug(f"Pattern search result: {bool(card)}")
		if card:
			user_msg = await db.get_card_user_message(card[0])
			if user_msg:
				await message.answer(user_msg, parse_mode="HTML")
			else:
				await message.answer("Сообщение карты отсутствует")
			return
	# as last resort: show cards to reply-only
	rows = await db.list_cards()
	cards = [(r[0], r[1]) for r in rows]
	await state.set_state(ForwardBindStates.waiting_select_card)
	await state.update_data(reply_only=True)
	warning_msg = "⚠️ Не удалось определить пользователя из пересылки.\n\nВозможные причины:\n• Пользователь скрыл все данные в настройках приватности Telegram\n• Сообщение не переслано\n\nВыберите карту для ответа администратору:"
	await message.answer(warning_msg, reply_markup=cards_select_kb(cards, back_to="admin:back"))


@admin_router.callback_query(
	F.data.startswith("multi:select:") & ~F.data.startswith("multi:select:xmr:") & ~F.data.startswith("multi:select:group:"),
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def multi_forward_select(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик выбора из множественных пересылок"""
	db = get_db()
	# Формат: multi:select:{type}:{row_index} - type может быть crypto, cash, card
	parts = cb.data.split(":")
	selected_type = parts[2]  # crypto, cash или card
	row_index = int(parts[3]) if len(parts) > 3 else 0  # row_index из callback_data
	
	data = await state.get_data()
	messages_list = data.get("multi_forward_messages", [])
	rows_data = data.get("multi_forward_rows", [])
	current_state = await state.get_state()
	logger.info(f"🔘 Нажата кнопка multi:select:{selected_type}:{row_index}, состояние: {current_state}, сообщений: {len(messages_list)}")
	
	# Получаем данные для текущей строки
	if row_index < len(rows_data):
		row = rows_data[row_index]
		crypto_data = row.get("crypto_data")
		cash_data = row.get("cash_data")
		card_data = row.get("card_data")
	else:
		# Если строка не существует, создаем новую
		while len(rows_data) <= row_index:
			rows_data.append({"crypto_data": None, "cash_data": None, "card_data": None, "row_index": len(rows_data)})
		row = rows_data[row_index]
		crypto_data = None
		cash_data = None
		card_data = None
	
	logger.debug(f"📊 Извлечено для строки {row_index}: crypto={bool(crypto_data)}, cash={bool(cash_data)}, card={bool(card_data)}")
	
	# Сохраняем текущий row_index в state для последующего использования
	await state.update_data(current_row_index=row_index)
	
	# Обрабатываем выбор в зависимости от типа
	if selected_type == "crypto":
		# Всегда показываем клавиатуру выбора валюты (BTC, LTC, XMR, Подтвердить, Назад)
		logger.info("📝 Показываем выбор валюты")
		from app.keyboards import crypto_select_kb
		
		# Формируем текст сообщения
		if crypto_data:
			current_currency = crypto_data.get("currency", "BTC")
			amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
			display = crypto_data.get("display", "Криптовалюта")
			message_text = f"📝 Выбор криптовалюты\n\nТекущая: {display}\n\nВыберите тип монеты:"
		else:
			message_text = "📝 Выбор криптовалюты\n\nВыберите тип монеты:"
		
		try:
			await cb.message.edit_text(
				message_text,
				reply_markup=crypto_select_kb(back_to="multi:back_to_main", show_confirm=True)
			)
			await cb.answer()
		except Exception as e:
			logger.exception(f"❌ Ошибка при открытии выбора валюты: {e}")
			await cb.answer("Ошибка при открытии выбора валюты", show_alert=True)
		return
	
	elif selected_type == "cash":
		# Сохраняем row_index для последующего использования
		await state.update_data(current_row_index=row_index)
		
		# Всегда сначала показываем выбор группы карт
		groups = await db.list_card_groups()
		logger.debug(f"Показываем список групп для выбора карты при вводе наличных: count={len(groups)}")
		
		from app.keyboards import card_groups_select_kb
		
		# Переходим в состояние выбора карты для наличных
		await state.set_state(ForwardBindStates.selecting_card_for_cash)
		
		text = "💵 Выберите группу карт для наличных:"
		if not groups:
			text = "💵 Групп пока нет. Выберите карты без группы:"
		
		await cb.message.edit_text(text, reply_markup=card_groups_select_kb(groups, back_to=f"multi:select:cash:{row_index}"))
		await cb.answer()
		return
	
	elif selected_type == "card":
		# Показываем список групп для выбора
		groups = await db.list_card_groups()
		logger.debug(f"Показываем список групп для выбора: count={len(groups)}")
		
		from app.keyboards import card_groups_select_kb
		
		text = "Выберите группу карт:"
		if not groups:
			text = "Групп пока нет. Выберите карты без группы:"
		
		await cb.message.edit_text(text, reply_markup=card_groups_select_kb(groups))
		await cb.answer()
		return
	
	else:
		await cb.answer("Неизвестный тип", show_alert=True)
		return


@admin_router.callback_query(
	F.data == "multi:add_row",
	StateFilter(ForwardBindStates.collecting_multi_forward)
)
async def multi_add_row(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик добавления новой строки для множественных пересылок"""
	logger.info(f"🔘 Нажата кнопка Добавить строку")
	
	data = await state.get_data()
	rows_data = data.get("multi_forward_rows", [])
	
	# Проверяем, не превышен ли лимит в 5 строк
	if len(rows_data) >= 5:
		await cb.answer("Максимум 5 строк", show_alert=True)
		return
	
	# Добавляем новую пустую строку
	new_row_index = len(rows_data)
	rows_data.append({"crypto_data": None, "cash_data": None, "card_data": None, "row_index": new_row_index})
	
	await state.update_data(multi_forward_rows=rows_data)
	
	# Обновляем клавиатуру
	from app.keyboards import multi_forward_select_kb
	selected_xmr = data.get("selected_xmr_numbers", {})
	
	message_text = await format_multi_forward_message_text(rows_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr)
	)
	await cb.answer(f"Добавлена строка {new_row_index + 1}")


@admin_router.callback_query(
	F.data == "multi:confirm",
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def multi_forward_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик подтверждения множественных пересылок - обрабатывает все строки"""
	logger.info(f"🔘 Нажата кнопка Подтвердить, состояние: {await state.get_state()}")
	db = get_db()
	data = await state.get_data()
	rows_data = data.get("multi_forward_rows", [])
	selected_xmr_numbers = data.get("selected_xmr_numbers", {})
	logger.debug(f"📋 Найдено строк: {len(rows_data)}")
	
	# Фильтруем строки, которые имеют хотя бы одно заполненное поле
	valid_rows = []
	for row in rows_data:
		crypto_data = row.get("crypto_data")
		cash_data = row.get("cash_data")
		card_data = row.get("card_data")
		
		# Если есть хотя бы одно заполненное поле, строка валидна
		if crypto_data or cash_data or card_data:
			valid_rows.append(row)
	
	if not valid_rows:
		await cb.answer("Нет данных для обработки", show_alert=True)
		return
	
	# Проверяем XMR для всех строк с криптовалютой XMR
	for row in valid_rows:
		crypto_data = row.get("crypto_data")
		if crypto_data and crypto_data.get("currency") == "XMR":
			row_index = row.get("row_index", 0)
			if row_index not in selected_xmr_numbers:
				await cb.answer(f"Выберите номер XMR для строки {row_index + 1} (XMR-1, XMR-2 или XMR-3)", show_alert=True)
				return
	
	# Обрабатываем каждую строку
	from app.config import get_settings
	from app.google_sheets import write_to_google_sheet, write_xmr_to_google_sheet
	
	settings = get_settings()
	result_parts = []
	processed_count = 0
	
	# Обрабатываем карты для всех строк (отправляем сообщения пользователям)
	for row in valid_rows:
		card_data = row.get("card_data")
		if card_data:
			card_name = card_data.get("card_name")
			user_name = card_data.get("user_name")
			
			if card_name:
				# Ищем карту в БД
				rows = await db.list_cards()
				card = None
				for db_row in rows:
					if card_name.upper() in db_row[1].upper() or db_row[1].upper() in card_name.upper():
						card = await db.get_card_by_id(db_row[0])
						break
				
				if card:
					# Если есть имя пользователя, пытаемся найти его
					orig_tg_id = None
					if user_name:
						user_by_name = await db.get_user_by_full_name(user_name)
						if user_by_name:
							orig_tg_id = user_by_name.get("tg_id")
					
					user_msg = card.get("user_message")
					
					# Если нашли пользователя, логируем и отправляем сообщение
					if orig_tg_id:
						await db.log_card_delivery_by_tg(
							orig_tg_id,
							card["card_id"],
							admin_id=cb.from_user.id if cb.from_user else None,
						)
						if user_msg:
							try:
								await bot.send_message(chat_id=orig_tg_id, text=user_msg, parse_mode="HTML")
								logger.info(f"Отправлено сообщение карты пользователю {orig_tg_id}")
							except Exception as e:
								logger.exception(f"Ошибка отправки сообщения пользователю {orig_tg_id}: {e}")
	
	# Определяем режим работы (add или rate)
	mode = data.get("mode", "add")  # По умолчанию режим add
	
	if mode == "rate":
		# Режим rate: каждая строка записывается отдельно в свою ячейку
		# Обрабатываем каждую строку отдельно
		all_results = []
		for row in valid_rows:
			crypto_data = row.get("crypto_data")
			cash_data = row.get("cash_data")
			card_data = row.get("card_data")
			row_index = row.get("row_index", 0)
			selected_xmr = selected_xmr_numbers.get(row_index)
			
			# Формируем данные для одной строки
			crypto_list = []
			xmr_list = []
			cash_list = []
			card_cash_pairs = []
			
			# Обрабатываем криптовалюту
			if crypto_data:
				currency = crypto_data.get("currency")
				usd_amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
				
				if currency == "XMR" and selected_xmr:
					if usd_amount > 0:
						xmr_list.append({
							"xmr_number": selected_xmr,
							"usd_amount": usd_amount
						})
				else:
					if usd_amount > 0:
						crypto_list.append({
							"currency": currency,
							"usd_amount": usd_amount
						})
			
			# Обрабатываем карту и наличные
			if card_data and cash_data:
				card_cash_pairs.append({
					"card": card_data.copy(),
					"cash": cash_data.copy()
				})
			elif card_data:
				card_cash_pairs.append({
					"card": card_data.copy(),
					"cash": None
				})
			elif cash_data:
				# Наличные без карты
				cash_list.append({
					"currency": cash_data.get("currency", "RUB"),
					"value": cash_data.get("value", 0)
				})
			
			# Записываем данные этой строки
			if (crypto_list or xmr_list or cash_list or card_cash_pairs) and settings.google_sheet_id and settings.google_credentials_path:
				from app.google_sheets import write_to_google_sheet_rate_mode
				result = await write_to_google_sheet_rate_mode(
					settings.google_sheet_id,
					settings.google_credentials_path,
					crypto_list,
					xmr_list,
					cash_list,
					card_cash_pairs
				)
				all_results.append(result)
		
		# Объединяем результаты всех записей
		all_written_cells = []
		all_failed_writes = []
		for res in all_results:
			all_written_cells.extend(res.get("written_cells", []))
			all_failed_writes.extend(res.get("failed_writes", []))
		
		result = {
			"success": len(all_written_cells) > 0,
			"written_cells": all_written_cells,
			"failed_writes": all_failed_writes
		}
	else:
		# Режим add: объединяем все данные из всех строк в одну запись
		# Собираем все криптовалюты, наличные и карты
		all_crypto_data = {}  # {currency: total_usd_amount}
		all_cash_data = {}  # {currency: total_amount}
		card_cash_pairs = []  # Список пар (карта, наличные) - сохраняем связь между картой и наличными из той же строки
		xmr_data = {}  # {xmr_number: usd_amount}
		
		for row in valid_rows:
			crypto_data = row.get("crypto_data")
			cash_data = row.get("cash_data")
			card_data = row.get("card_data")
			row_index = row.get("row_index", 0)
			selected_xmr = selected_xmr_numbers.get(row_index)
			
			# Обрабатываем криптовалюту
			if crypto_data:
				currency = crypto_data.get("currency")
				usd_amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
				
				if currency == "XMR" and selected_xmr:
					# Для XMR сохраняем отдельно по номерам
					if selected_xmr in xmr_data:
						xmr_data[selected_xmr] += usd_amount
					else:
						xmr_data[selected_xmr] = usd_amount
				else:
					# Для других криптовалют суммируем USD
					if currency in all_crypto_data:
						all_crypto_data[currency] += usd_amount
					else:
						all_crypto_data[currency] = usd_amount
			
			# Сохраняем связь между картой и наличными из той же строки
			if card_data and cash_data:
				# Создаем копию данных карты и наличных для сохранения связи
				card_copy = card_data.copy()
				cash_copy = cash_data.copy()
				card_cash_pairs.append({
					"card": card_copy,
					"cash": cash_copy
				})
			elif card_data:
				# Если есть карта, но нет наличных
				card_copy = card_data.copy()
				card_cash_pairs.append({
					"card": card_copy,
					"cash": None
				})
			elif cash_data:
				# Если есть наличные, но нет карты - суммируем как раньше
				currency = cash_data.get("currency", "RUB")
				amount = cash_data.get("value", 0)
				if currency in all_cash_data:
					all_cash_data[currency] += amount
				else:
					all_cash_data[currency] = amount
		
		# Преобразуем XMR данные в формат списка
		xmr_list = []
		for xmr_number, usd_amount in xmr_data.items():
			if usd_amount > 0:
				xmr_list.append({
					"xmr_number": xmr_number,
					"usd_amount": usd_amount
				})
		
		# Преобразуем криптовалюты в список
		crypto_list = []
		for currency, usd_amount in all_crypto_data.items():
			if usd_amount > 0:
				crypto_list.append({
					"currency": currency,
					"usd_amount": usd_amount
				})
		
		# Преобразуем наличные в список (для случаев, когда нет карты)
		cash_list = []
		for currency, amount in all_cash_data.items():
			if amount > 0:
				cash_list.append({
					"currency": currency,
					"value": amount
				})
		
		# Записываем все данные в одну строку Google Sheets
		if settings.google_sheet_id and settings.google_credentials_path:
			try:
				from app.google_sheets import write_all_to_google_sheet_one_row
				
				result = await write_all_to_google_sheet_one_row(
					settings.google_sheet_id,
					settings.google_credentials_path,
					crypto_list,
					xmr_list,
					cash_list,
					card_cash_pairs  # Передаем пары карта-наличные вместо просто карт
				)
			except Exception as e:
				logger.exception(f"Ошибка записи в Google Sheet (режим add): {e}")
				result = {"success": False}
	
	# Обрабатываем результат записи
	if settings.google_sheet_id and settings.google_credentials_path:
		try:
			# result уже получен выше в зависимости от режима
			if result.get("success") or result.get("written_cells") or result.get("failed_writes"):
				if mode == "rate":
					# Формируем отчет для режима rate (показываем записанные ячейки и неудачные записи)
					written_cells = result.get("written_cells", [])
					failed_writes = result.get("failed_writes", [])
					
					if written_cells:
						result_parts.append("✅ Обработано и записано в ячейки:")
						result_parts.append("")
						for cell_info in written_cells:
							result_parts.append(f"📝 {cell_info}")
					
					if failed_writes:
						if written_cells:
							result_parts.append("")
						result_parts.append("❌ Не записано (нет места):")
						result_parts.append("")
						for failed_info in failed_writes:
							result_parts.append(f"⚠️ {failed_info}")
					
					if not written_cells and not failed_writes:
						result_parts.append("✅ Обработано (нет данных для записи)")
				else:
					# Формируем детальный отчет для режима add
					result_parts.append("✅ Обработано и записано в одну строку:")
					result_parts.append("")
					
					# Криптовалюты (BTC, LTC, USDT)
					if crypto_list:
						for crypto in crypto_list:
							currency = crypto.get("currency")
							usd_amount = crypto.get("usd_amount", 0.0)
							if usd_amount > 0:
								usd_amount_rounded = int(round(usd_amount))
								result_parts.append(f"🪙 {currency}: {usd_amount_rounded} USD")
					
					# XMR
					if xmr_list:
						for xmr in xmr_list:
							xmr_number = xmr.get("xmr_number")
							usd_amount = xmr.get("usd_amount", 0.0)
							if usd_amount > 0:
								usd_amount_rounded = int(round(usd_amount))
								result_parts.append(f"🪙 XMR-{xmr_number}: {usd_amount_rounded} USD")
					
					# Наличные
					if cash_list:
						for cash in cash_list:
							currency = cash.get("currency", "RUB")
							amount = cash.get("value", 0)
							if amount > 0:
								result_parts.append(f"💵 {amount} {currency}")
					
					# Карты с наличными
					if card_cash_pairs:
						for pair in card_cash_pairs:
							card_data = pair.get("card")
							cash_data = pair.get("cash")
							card_name = card_data.get("card_name", "")
							user_name = card_data.get("user_name", "")
							
							if cash_data:
								cash_currency = cash_data.get("currency", "RUB")
								cash_amount = cash_data.get("value", 0)
								if user_name:
									result_parts.append(f"💳 {card_name} ({user_name}): {cash_amount} {cash_currency}")
								else:
									result_parts.append(f"💳 {card_name}: {cash_amount} {cash_currency}")
							else:
								if user_name:
									result_parts.append(f"💳 {card_name} ({user_name})")
								else:
									result_parts.append(f"💳 {card_name}")
			else:
				result_parts.append("⚠️ Ошибка записи в Google Sheet")
		except Exception as e:
			logger.exception(f"Ошибка при записи в Google Sheet: {e}")
			result_parts.append("⚠️ Ошибка записи в Google Sheet")
	else:
		logger.warning("Google Sheets не настроен (отсутствует GOOGLE_SHEET_ID или GOOGLE_CREDENTIALS_PATH)")
		result_parts.append("⚠️ Google Sheets не настроен")
	
	result_text = "\n".join(result_parts) if result_parts else "✅ Обработано"
	await cb.message.edit_text(result_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
	await state.clear()
	await cb.answer("✅ Обработано")


@admin_router.callback_query(
	F.data.startswith("multi:select:xmr:"),
	StateFilter(ForwardBindStates.collecting_multi_forward)
)
async def multi_select_xmr(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора XMR-1, XMR-2 или XMR-3 - сохраняет выбор и обновляет клавиатуру"""
	logger.info(f"🔘 Выбран XMR вариант: {cb.data}")
	
	# Извлекаем row_index и номер XMR из callback_data (multi:select:xmr:{row_index}:{xmr_number})
	try:
		parts = cb.data.split(":")
		if len(parts) == 4:
			# Старый формат: multi:select:xmr:{xmr_number}
			row_index = 0
			xmr_number = int(parts[3])
		else:
			# Новый формат: multi:select:xmr:{row_index}:{xmr_number}
			row_index = int(parts[3])
			xmr_number = int(parts[4])
		
		if xmr_number not in [1, 2, 3]:
			await cb.answer("Неверный номер XMR", show_alert=True)
			return
	except (ValueError, IndexError):
		await cb.answer("Ошибка обработки номера XMR", show_alert=True)
		return
	
	# Получаем данные для обновления клавиатуры
	data = await state.get_data()
	rows_data = data.get("multi_forward_rows", [])
	selected_xmr_numbers = data.get("selected_xmr_numbers", {})
	
	# Сохраняем выбранный номер XMR для текущей строки
	selected_xmr_numbers[row_index] = xmr_number
	await state.update_data(selected_xmr_numbers=selected_xmr_numbers)
	
	# Обновляем клавиатуру с выбранным номером XMR
	from app.keyboards import multi_forward_select_kb
	
	message_text = await format_multi_forward_message_text(rows_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
	)
	await cb.answer(f"Выбрано XMR-{xmr_number} для строки {row_index + 1}")


@admin_router.callback_query(
	F.data.startswith("crypto:change_type:"),
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def crypto_change_type(cb: CallbackQuery, state: FSMContext):
	"""Обработчик изменения типа криптовалюты"""
	# Формат: crypto:change_type:{currency}
	parts = cb.data.split(":")
	new_currency = parts[2]  # BTC, LTC или XMR
	
	data = await state.get_data()
	messages_list = data.get("multi_forward_messages", [])
	
	# Находим сообщение с криптовалютой и обновляем тип
	for msg in messages_list:
		if msg["parsed"].get("type") == "crypto":
			msg["parsed"]["currency"] = new_currency
			usd_amount = msg["parsed"].get("usd_amount", msg["parsed"].get("value", 0.0))
			msg["parsed"]["usd_amount"] = usd_amount
			msg["parsed"]["value"] = usd_amount  # Для обратной совместимости
			msg["parsed"]["display"] = f"${int(round(usd_amount))} ({new_currency})"
			break
	
	# Если изменили валюту на не-XMR, сбрасываем выбранный номер XMR
	selected_xmr = data.get("selected_xmr_number")
	if new_currency != "XMR":
		selected_xmr = None
		await state.update_data(selected_xmr_number=None)
	
	# Обновляем данные в state
	await state.update_data(multi_forward_messages=messages_list)
	
	# Обновляем кнопки в основном сообщении
	from app.keyboards import multi_forward_select_kb
	
	crypto_data = None
	cash_data = None
	card_data = None
	
	for msg in messages_list:
		parsed_msg = msg["parsed"]
		msg_type = parsed_msg.get("type")
		
		if msg_type == "crypto" and not crypto_data:
			crypto_data = parsed_msg
		elif msg_type == "cash" and not cash_data:
			cash_data = parsed_msg
		elif msg_type == "card" and not card_data:
			card_data = parsed_msg
	
	# Обновляем сообщение с кнопками
	buttons_message_id = data.get("multi_forward_buttons_msg_id")
	if buttons_message_id:
		try:
			message_text = await format_multi_forward_message_text(crypto_data)
			await cb.bot.edit_message_text(
				chat_id=cb.message.chat.id,
				message_id=buttons_message_id,
				text=message_text,
				reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data, selected_xmr=selected_xmr)
			)
		except Exception as e:
			logger.exception(f"Ошибка обновления сообщения с кнопками: {e}")
	
	# Возвращаемся к основному меню с тремя кнопками
	message_text = await format_multi_forward_message_text(crypto_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data, selected_xmr=selected_xmr)
	)
	await cb.answer(f"✅ Изменено на {new_currency}")


@admin_router.callback_query(
	F.data.startswith("crypto:select:"),
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def crypto_select_currency(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора валюты, если криптовалюта не распознана"""
	# Формат: crypto:select:{currency} или crypto:select:amount
	parts = cb.data.split(":")
	
	if len(parts) < 3:
		await cb.answer("Ошибка обработки выбора", show_alert=True)
		return
	
	action = parts[2]  # BTC, LTC, XMR или amount
	
	if action == "amount":
		# Переход к вводу количества
		logger.info(f"🔘 Нажата кнопка 'Количество', текущее состояние: {await state.get_state()}")
		
		# Получаем данные из state
		data = await state.get_data()
		messages_list = data.get("multi_forward_messages", [])
		
		# Проверяем, есть ли уже криптовалюта
		crypto_msg = None
		for msg in messages_list:
			if msg["parsed"].get("type") == "crypto":
				crypto_msg = msg
				break
		
		# Если криптовалюты нет, создаем с валютой по умолчанию (BTC)
		if not crypto_msg:
			# Создаем новое сообщение с криптовалютой (валюта по умолчанию - BTC)
			# USD будет введен пользователем
			crypto_msg = {
				"text": "",
				"parsed": {
					"type": "crypto",
					"usd_amount": 0.0,
					"value": 0.0,  # Для обратной совместимости
					"currency": "BTC",  # Валюта по умолчанию
					"display": "$0 (BTC)"
				},
				"message_id": None  # Это виртуальное сообщение
			}
			messages_list.append(crypto_msg)
			await state.update_data(multi_forward_messages=messages_list)
		
		await state.set_state(ForwardBindStates.editing_crypto_amount)
		logger.info(f"✅ Состояние установлено на: {await state.get_state()}")
		await cb.message.edit_text(
			"📝 Введите количество USD:\n\nНапример: 100",
			reply_markup=None
		)
		await cb.answer()
		return
	
	# Выбор валюты (BTC, LTC, XMR)
	currency = action
	if currency not in ["BTC", "LTC", "XMR", "USDT"]:
		await cb.answer("Неверная валюта", show_alert=True)
		return
	
	logger.info(f"🔘 Выбрана валюта: {currency}")
	
	# Получаем данные из state
	data = await state.get_data()
	row_index = data.get("current_row_index", 0)
	rows_data = data.get("multi_forward_rows", [])
	
	# Убеждаемся, что строка существует
	while len(rows_data) <= row_index:
		rows_data.append({"crypto_data": None, "cash_data": None, "card_data": None, "row_index": len(rows_data)})
	
	row = rows_data[row_index]
	crypto_data = row.get("crypto_data")
	
	# Обновляем или создаем данные криптовалюты
	if crypto_data:
		usd_amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
		crypto_data["currency"] = currency
		crypto_data["usd_amount"] = usd_amount
		crypto_data["value"] = usd_amount  # Для обратной совместимости
		crypto_data["display"] = f"${int(round(usd_amount))} ({currency})"
	else:
		# Создаем новые данные криптовалюты
		crypto_data = {
			"type": "crypto",
			"usd_amount": 0.0,
			"value": 0.0,  # Для обратной совместимости
			"currency": currency,
			"display": f"$0 ({currency})"
		}
	
	row["crypto_data"] = crypto_data
	rows_data[row_index] = row
	
	# Обновляем данные в state
	await state.update_data(multi_forward_rows=rows_data)
	
	# Проверяем, введен ли USD
	usd_amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
	
	# Если USD не введен (равен 0), предлагаем ввести
	if usd_amount == 0.0 or usd_amount is None:
		logger.info(f"⚠️ USD не введен для валюты {currency}, предлагаем ввести")
		await state.set_state(ForwardBindStates.editing_crypto_amount)
		await cb.message.edit_text(
			f"📝 Выбрана валюта: {currency}\n\nВведите количество USD:\n\nНапример: 100",
			reply_markup=None
		)
		await cb.answer(f"✅ Выбрана валюта: {currency}. Введите USD")
		return
	
	# USD введен - возвращаемся к основному меню
	# Обновляем сообщение с кнопками
	from app.keyboards import multi_forward_select_kb
	
	selected_xmr_numbers = data.get("selected_xmr_numbers", {})
	buttons_message_id = data.get("multi_forward_buttons_msg_id")
	if buttons_message_id:
		try:
			message_text = await format_multi_forward_message_text(rows_data)
			await cb.bot.edit_message_text(
				chat_id=cb.message.chat.id,
				message_id=buttons_message_id,
				text=message_text,
				reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
			)
		except Exception as e:
			logger.exception(f"Ошибка обновления сообщения с кнопками: {e}")
	
	# Возвращаемся к основному меню
	message_text = await format_multi_forward_message_text(rows_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
	)
	await cb.answer(f"✅ Выбрана валюта: {currency}")


@admin_router.callback_query(
	F.data == "crypto:change_amount",
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def crypto_change_amount_start(cb: CallbackQuery, state: FSMContext):
	"""Начало редактирования количества криптовалюты"""
	logger.info(f"🔘 Нажата кнопка изменения количества, текущее состояние: {await state.get_state()}")
	await state.set_state(ForwardBindStates.editing_crypto_amount)
	logger.info(f"✅ Состояние установлено на: {await state.get_state()}")
	await cb.message.edit_text(
		"📝 Введите количество USD:\n\nНапример: 100",
		reply_markup=None
	)
	await cb.answer()


@admin_router.callback_query(
	F.data == "crypto:back",
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def crypto_edit_back(cb: CallbackQuery, state: FSMContext):
	"""Возврат к основному меню множественных пересылок"""
	from app.keyboards import multi_forward_select_kb
	
	data = await state.get_data()
	messages_list = data.get("multi_forward_messages", [])
	
	# Извлекаем данные из всех сообщений
	crypto_data = None
	cash_data = None
	card_data = None
	
	for msg in messages_list:
		parsed_msg = msg["parsed"]
		msg_type = parsed_msg.get("type")
		
		if msg_type == "crypto" and not crypto_data:
			crypto_data = parsed_msg
		elif msg_type == "cash" and not cash_data:
			cash_data = parsed_msg
		elif msg_type == "card" and not card_data:
			card_data = parsed_msg
	
	message_text = await format_multi_forward_message_text(crypto_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data)
	)
	await cb.answer()


@admin_router.callback_query(
	F.data.startswith("cash:change_currency:"),
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def cash_change_currency(cb: CallbackQuery, state: FSMContext):
	"""Обработчик изменения валюты наличных"""
	# Формат: cash:change_currency:{currency}
	parts = cb.data.split(":")
	new_currency = parts[2]  # BYN или RUB
	
	data = await state.get_data()
	row_index = data.get("current_row_index", 0)
	rows_data = data.get("multi_forward_rows", [])
	
	# Убеждаемся, что строка существует
	while len(rows_data) <= row_index:
		rows_data.append({"crypto_data": None, "cash_data": None, "card_data": None, "row_index": len(rows_data)})
	
	row = rows_data[row_index]
	cash_data = row.get("cash_data")
	
	# Обновляем валюту наличных
	if cash_data:
		amount = cash_data.get("value", 0)
		cash_data["currency"] = new_currency
		cash_data["display"] = f"{amount} {new_currency}"
		row["cash_data"] = cash_data
		rows_data[row_index] = row
	
	# Обновляем данные в state
	await state.update_data(multi_forward_rows=rows_data)
	
	# Обновляем кнопки в основном сообщении
	from app.keyboards import multi_forward_select_kb
	
	selected_xmr_numbers = data.get("selected_xmr_numbers", {})
	
	# Обновляем сообщение с кнопками
	buttons_message_id = data.get("multi_forward_buttons_msg_id")
	if buttons_message_id:
		try:
			message_text = await format_multi_forward_message_text(rows_data)
			await cb.bot.edit_message_text(
				chat_id=cb.message.chat.id,
				message_id=buttons_message_id,
				text=message_text,
				reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
			)
		except Exception as e:
			logger.exception(f"Ошибка обновления сообщения с кнопками: {e}")
	
	# Возвращаемся к основному меню с тремя кнопками
	message_text = await format_multi_forward_message_text(rows_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
	)
	await cb.answer(f"✅ Изменено на {new_currency}")


@admin_router.callback_query(
	F.data == "cash:change_amount",
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def cash_change_amount_start(cb: CallbackQuery, state: FSMContext):
	"""Начало редактирования количества наличных"""
	logger.info(f"🔘 Нажата кнопка изменения количества наличных, текущее состояние: {await state.get_state()}")
	await state.set_state(ForwardBindStates.editing_cash_amount)
	logger.info(f"✅ Состояние установлено на: {await state.get_state()}")
	await cb.message.edit_text(
		"📝 Введите новое количество наличных:\n\nНапример: 5020",
		reply_markup=None
	)
	await cb.answer()


@admin_router.callback_query(
	F.data == "cash:back",
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def cash_edit_back(cb: CallbackQuery, state: FSMContext):
	"""Возврат к основному меню множественных пересылок"""
	from app.keyboards import multi_forward_select_kb
	
	data = await state.get_data()
	messages_list = data.get("multi_forward_messages", [])
	
	# Извлекаем данные из всех сообщений
	crypto_data = None
	cash_data = None
	card_data = None
	
	for msg in messages_list:
		parsed_msg = msg["parsed"]
		msg_type = parsed_msg.get("type")
		
		if msg_type == "crypto" and not crypto_data:
			crypto_data = parsed_msg
		elif msg_type == "cash" and not cash_data:
			cash_data = parsed_msg
		elif msg_type == "card" and not card_data:
			card_data = parsed_msg
	
	message_text = await format_multi_forward_message_text(crypto_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(crypto_data, cash_data, card_data)
	)
	await cb.answer()


@admin_router.callback_query(
	F.data.startswith("multi:select:group:"),
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward, ForwardBindStates.selecting_card_for_cash)
)
async def multi_select_group(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора группы карт - показывает карты из выбранной группы"""
	db = get_db()
	current_state = await state.get_state()
	is_cash_mode = current_state == ForwardBindStates.selecting_card_for_cash
	
	# Формат: multi:select:group:{group_id} или multi:select:group:0 для карт без группы
	group_id_str = cb.data.split(":")[-1]
	group_id = int(group_id_str) if group_id_str != "0" else None
	
	if group_id:
		# Получаем карты из группы
		cards = await db.get_cards_by_group(group_id)
		group = await db.get_card_group(group_id)
		group_name = group.get("name", "Группа") if group else "Группа"
		text = f"💵 Карты из группы '{group_name}':" if is_cash_mode else f"Карты из группы '{group_name}':"
	else:
		# Получаем карты без группы
		cards = await db.get_cards_without_group()
		text = "💵 Карты без группы:" if is_cash_mode else "Карты без группы:"
	
	if not cards:
		await cb.answer("В этой группе нет карт" if group_id else "Нет карт без группы", show_alert=True)
		return
	
	# Создаем клавиатуру с картами
	from aiogram.utils.keyboard import InlineKeyboardBuilder
	kb = InlineKeyboardBuilder()
	for card_id, card_name, _ in cards:
		kb.button(text=f"💳 {card_name}", callback_data=f"multi:select_card:{card_id}")
	
	# Определяем callback для кнопки "Назад" в зависимости от режима
	if is_cash_mode:
		data = await state.get_data()
		row_index = data.get("current_row_index", 0)
		kb.button(text="⬅️ Назад", callback_data=f"multi:select:cash:{row_index}")
	else:
		kb.button(text="⬅️ Назад", callback_data="multi:select:card")
	kb.adjust(1)
	
	await cb.message.edit_text(text, reply_markup=kb.as_markup())
	await cb.answer()


@admin_router.callback_query(
	F.data.startswith("multi:select_card:"),
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward, ForwardBindStates.selecting_card_for_cash)
)
async def multi_select_card(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора карты в множественных пересылках"""
	db = get_db()
	current_state = await state.get_state()
	is_cash_mode = current_state == ForwardBindStates.selecting_card_for_cash
	
	# Формат: multi:select_card:{card_id}
	card_id = int(cb.data.split(":")[-1])
	
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	card_name = card["name"]
	
	data = await state.get_data()
	row_index = data.get("current_row_index", 0)
	rows_data = data.get("multi_forward_rows", [])
	messages_list = data.get("multi_forward_messages", [])
	
	# Убеждаемся, что строка существует
	while len(rows_data) <= row_index:
		rows_data.append({"crypto_data": None, "cash_data": None, "card_data": None, "row_index": len(rows_data)})
	
	row = rows_data[row_index]
	
	# Проверяем, содержит ли название карты уже информацию о пользователе в скобках
	card_has_user_name = bool(re.search(r'\(([А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z]\.?)\)', card_name))
	
	# Создаем или обновляем данные карты
	if card_has_user_name:
		# Карта уже содержит информацию о пользователе
		card_data = {
			"type": "card",
			"card_name": card_name,
			"user_name": None,
			"display": card_name
		}
	else:
		# Ищем имя пользователя в других сообщениях
		user_name = None
		for msg in messages_list:
			if msg["parsed"].get("type") == "user_name":
				user_name = msg["parsed"].get("user_name")
				break
		
		card_data = {
			"type": "card",
			"card_name": card_name,
			"user_name": user_name,
			"display": f"{card_name} ({user_name})" if user_name else card_name
		}
	
	row["card_data"] = card_data
	rows_data[row_index] = row
	
	# Если это режим выбора карты для наличных, переходим к вводу количества
	if is_cash_mode:
		# Сохраняем выбранную карту
		await state.update_data(multi_forward_rows=rows_data, selected_card_for_cash=card_data)
		
		# Переходим к вводу количества наличных
		await state.set_state(ForwardBindStates.editing_cash_amount)
		await cb.message.edit_text(
			f"💵 Выбрана карта: {card_data['display']}\n\n📝 Введите количество наличных:\n\nНапример: 100",
			reply_markup=None
		)
		await cb.answer(f"✅ Выбрана карта: {card_name}")
		return
	
	# Обновляем данные в state
	await state.update_data(multi_forward_rows=rows_data)
	
	# Обновляем кнопки в основном сообщении
	from app.keyboards import multi_forward_select_kb
	
	selected_xmr_numbers = data.get("selected_xmr_numbers", {})
	
	# Обновляем сообщение с кнопками
	buttons_message_id = data.get("multi_forward_buttons_msg_id")
	if buttons_message_id:
		try:
			message_text = await format_multi_forward_message_text(rows_data)
			await cb.bot.edit_message_text(
				chat_id=cb.message.chat.id,
				message_id=buttons_message_id,
				text=message_text,
				reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
			)
		except Exception as e:
			logger.exception(f"Ошибка обновления сообщения с кнопками: {e}")
	
	# Возвращаемся к основному меню с двумя кнопками
	message_text = await format_multi_forward_message_text(rows_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
	)
	await cb.answer(f"✅ Выбрана карта: {card_name}")


@admin_router.callback_query(
	F.data == "multi:back_to_main",
	StateFilter(ForwardBindStates.waiting_select_card, ForwardBindStates.collecting_multi_forward)
)
async def multi_back_to_main(cb: CallbackQuery, state: FSMContext):
	"""Возврат к основному меню множественных пересылок"""
	from app.keyboards import multi_forward_select_kb
	
	data = await state.get_data()
	rows_data = data.get("multi_forward_rows", [])
	selected_xmr_numbers = data.get("selected_xmr_numbers", {})
	
	# Если нет строк, создаем одну пустую
	if not rows_data:
		rows_data = [{"crypto_data": None, "cash_data": None, "card_data": None, "row_index": 0}]
	
	message_text = await format_multi_forward_message_text(rows_data)
	await cb.message.edit_text(
		message_text,
		reply_markup=multi_forward_select_kb(rows_data, selected_xmr=selected_xmr_numbers)
	)
	await cb.answer()


@admin_router.callback_query(ForwardBindStates.waiting_select_card, F.data.startswith("select:card:"))
async def forward_bind_select_card(cb: CallbackQuery, state: FSMContext, bot: Bot):
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	data = await state.get_data()
	reply_only = bool(data.get("reply_only", False))
	hidden_user_name = data.get("hidden_user_name")  # Имя скрытого пользователя
	
	if reply_only:
		# reply to admin only without binding
		user_msg = await db.get_card_user_message(card_id)
		await state.clear()
		if user_msg:
			await cb.message.edit_text(user_msg, reply_markup=admin_menu_kb(), parse_mode="HTML")
		else:
			await cb.message.edit_text("Сообщение карты отсутствует", reply_markup=admin_menu_kb())
		await cb.answer()
		return
	
	orig_tg_id = data.get("original_tg_id")
	existing_user_id = data.get("existing_user_id")  # ID существующего пользователя с NULL tg_id
	
	# Если есть имя скрытого пользователя, но нет ID
	if hidden_user_name and not orig_tg_id:
		if existing_user_id:
			# Пользователь уже существует - просто привязываем карту
			user_id = existing_user_id
			logger.info(f"💾 Привязываю карту {card_id} к существующему пользователю '{hidden_user_name}' (user_id={user_id})")
		else:
			# Создаем нового пользователя с NULL tg_id
			logger.info(f"💾 Создаю запись для скрытого пользователя '{hidden_user_name}' с card_id={card_id}")
			user_id = await db.create_user_by_name_only(hidden_user_name)
		
		if user_id:
			await db.bind_user_to_card(user_id, card_id)
			logger.info(f"✅ Привязана карта {card_id} к пользователю '{hidden_user_name}' (user_id={user_id})")
			
			card = await db.get_card_by_id(card_id)
			await state.clear()
			admin_text = f"✅ Запись создана для пользователя '{hidden_user_name}'.\n\n"
			if card:
				user_msg = card.get("user_message")
				if user_msg:
					admin_text += f"Сообщение карты:\n{user_msg}"
				else:
					admin_text += "Сообщение карты отсутствует"
			else:
				admin_text += "Карта не найдена"
			
			await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
			await cb.answer(f"✅ Записано для '{hidden_user_name}'")
			return
	
	if not orig_tg_id:
		await cb.answer("Ошибка: не удалось определить пользователя", show_alert=True)
		return
	
	orig_tg_id = int(orig_tg_id)
	logger.debug(f"Bind forwarded user {orig_tg_id} to card_id={card_id}")
	user_id = await db.get_or_create_user(orig_tg_id, None, None)
	await db.touch_user_by_tg(orig_tg_id)
	await db.bind_user_to_card(user_id, card_id)
	card = await db.get_card_by_id(card_id)
	await state.clear()
	if card:
		user_msg = card.get("user_message")
		admin_text = user_msg or "Сообщение карты отсутствует"
		if user_msg:
			await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
		else:
			await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb())
		await db.log_card_delivery_by_tg(
			orig_tg_id,
			card_id,
			admin_id=cb.from_user.id if cb.from_user else None,
		)
		if user_msg:
			try:
				await bot.send_message(chat_id=orig_tg_id, text=user_msg, parse_mode="HTML")
				logger.debug("Sent user_message after binding")
			except Exception as e:
				logger.exception(f"Failed to send user_message after binding: {e}")
	await cb.answer()


@admin_router.callback_query(ForwardBindStates.waiting_select_card, F.data.startswith("hidden:select:"))
async def hidden_user_select(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик выбора пользователя из списка похожих (для MessageOriginHiddenUser)"""
	db = get_db()
	# Формат: hidden:select:{tg_id}
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка формата", show_alert=True)
		return
	
	tg_id = int(parts[2])
	data = await state.get_data()
	hidden_name = data.get("hidden_user_name", "Неизвестный")
	
	logger.info(f"✅ Выбран пользователь из похожих: tg_id={tg_id}, hidden_name='{hidden_name}'")
	
	# Получаем данные пользователя
	user_id = await db.get_user_id_by_tg(tg_id)
	if not user_id:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Ошибка получения данных пользователя", show_alert=True)
		return
	
	# Обновляем состояние с найденным tg_id
	await state.update_data(original_tg_id=tg_id, reply_only=False)
	
	# Проверяем, привязан ли пользователь к картам
	cards_for_user = await db.get_cards_for_user_tg(tg_id)
	
	if cards_for_user:
		if len(cards_for_user) == 1:
			# Одна карта - используем её
			card = cards_for_user[0]
			user_msg = card.get("user_message")
			admin_text = "Сообщение карты отсутствует" if not user_msg else user_msg
			
			await state.clear()
			if user_msg:
				await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
			else:
				await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb())
			
			await db.log_card_delivery_by_tg(tg_id, card["card_id"], admin_id=cb.from_user.id if cb.from_user else None)
			
			if user_msg:
				try:
					await bot.send_message(chat_id=tg_id, text=user_msg, parse_mode="HTML")
					logger.info(f"Отправлено сообщение карты пользователю {tg_id}")
				except Exception as e:
					logger.exception(f"Ошибка отправки сообщения пользователю {tg_id}: {e}")
		else:
			# Несколько карт - выбираем
			buttons = [(card["card_id"], card["card_name"]) for card in cards_for_user]
			await state.set_state(ForwardBindStates.waiting_select_existing_card)
			text = f"✅ Выбран: {user.get('full_name', 'Без имени')}\n\nУ пользователя привязано несколько карт. Выберите нужную:"
			await cb.message.edit_text(text, reply_markup=user_cards_reply_kb(buttons, tg_id, back_to="admin:back"))
	else:
		# Не привязан - выбираем карту для привязки
		rows = await db.list_cards()
		cards = [(r[0], r[1]) for r in rows]
		text = f"✅ Выбран: {user.get('full_name', 'Без имени')}\n\nПользователь не привязан. Выберите карту для привязки:"
		await cb.message.edit_text(text, reply_markup=cards_select_kb(cards, back_to="admin:back"))
	
	await cb.answer()


@admin_router.callback_query(ForwardBindStates.waiting_select_card, F.data == "hidden:no_match")
async def hidden_user_no_match(cb: CallbackQuery, state: FSMContext):
	"""Обработчик когда пользователь не найден в списке похожих"""
	data = await state.get_data()
	hidden_name = data.get("hidden_user_name", "Неизвестный")
	
	logger.info(f"❌ Пользователь '{hidden_name}' не найден в списке похожих")
	
	# Показываем список карт для ответа администратору
	db = get_db()
	rows = await db.list_cards()
	cards = [(r[0], r[1]) for r in rows]
	await state.update_data(reply_only=True)
	
	text = f"⚠️ Пользователь '{hidden_name}' не найден в базе данных.\n\nДля работы с этим пользователем попросите его написать боту хотя бы один раз.\n\nВыберите карту для ответа администратору:"
	await cb.message.edit_text(text, reply_markup=cards_select_kb(cards, back_to="admin:back"))
	await cb.answer()


@admin_router.callback_query(ForwardBindStates.waiting_select_existing_card, F.data.startswith("user:reply:card:"))
async def forward_existing_card_reply(cb: CallbackQuery, state: FSMContext, bot: Bot):
	db = get_db()
	parts = cb.data.split(":")
	user_tg_id_val = parts[3]
	card_id = int(parts[4])
	
	data = await state.get_data()
	user_id_for_hidden = data.get("user_id_for_hidden")
	hidden_user_name = data.get("hidden_user_name")
	
	# Обрабатываем случай, когда tg_id = 0 (для скрытых пользователей)
	user_tg_id = int(user_tg_id_val) if user_tg_id_val != "0" else None
	
	if user_tg_id:
		# Обычный пользователь с tg_id
		await db.touch_user_by_tg(user_tg_id)
	else:
		# Скрытый пользователь - обновляем last_interaction_at через user_id
		if user_id_for_hidden:
			await db.touch_user(user_id_for_hidden)
			logger.info(f"Обновлен last_interaction_at для скрытого пользователя '{hidden_user_name}' (user_id={user_id_for_hidden})")
	
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	await state.clear()
	user_msg = card.get("user_message")
	admin_text = user_msg or "Сообщение карты отсутствует"
	if user_msg:
		await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
	else:
		await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb())
	
	# Логируем доставку
	if user_tg_id:
		await db.log_card_delivery_by_tg(
		user_tg_id,
		card_id,
		admin_id=cb.from_user.id if cb.from_user else None,
	)
	if user_msg:
		try:
			await bot.send_message(chat_id=user_tg_id, text=user_msg, parse_mode="HTML")
			logger.info(f"Sent user_message for existing binding card_id={card_id} to user {user_tg_id}")
		except Exception as e:
			logger.exception(f"Failed to send user_message for existing card: {e}")
	elif user_id_for_hidden:
		# Логируем для скрытого пользователя через user_id
		await db.log_card_delivery(
			user_id_for_hidden,
			card_id,
			admin_id=cb.from_user.id if cb.from_user else None,
		)
		logger.info(f"✅ Логирование доставки для скрытого пользователя '{hidden_user_name}' (user_id={user_id_for_hidden}, card_id={card_id}). Сообщение не отправлено (нет tg_id)")
	
	await cb.answer()
