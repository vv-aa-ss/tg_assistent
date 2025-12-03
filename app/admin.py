from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter, Command
from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
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
	cash_list_kb,
	cash_delete_kb,
	user_action_kb,
	card_action_kb,
	user_cards_reply_kb,
	similar_users_select_kb,
	card_groups_list_kb,
	card_groups_select_kb,
	requisites_list_kb,
	requisite_action_kb,
	delete_confirmation_kb,
	stat_u_menu_kb,
)
from app.di import get_db, get_admin_ids, get_admin_usernames

admin_router = Router(name="admin")
logger = logging.getLogger("app.admin")

USERS_PER_PAGE = 6



async def send_card_requisites_to_admin(bot: Bot, admin_chat_id: int, card_id: int, db) -> int:
	"""
	Отправляет все реквизиты карты админу отдельными сообщениями.
	Отправляет и реквизиты из таблицы card_requisites, и user_message (если есть) для обратной совместимости.
	
	Args:
		bot: Экземпляр бота
		admin_chat_id: ID чата админа
		card_id: ID карты
		db: Экземпляр базы данных
	
	Returns:
		Количество успешно отправленных реквизитов
	"""
	logger.info(f"📤 send_card_requisites_to_admin: card_id={card_id}, admin_chat_id={admin_chat_id}")
	requisites = await db.list_card_requisites(card_id)
	logger.info(f"📋 Найдено реквизитов в таблице: {len(requisites)} для card_id={card_id}")
	
	sent_count = 0
	
	# Отправляем все реквизиты из таблицы card_requisites
	if requisites:
		for idx, requisite in enumerate(requisites, 1):
			try:
				logger.info(f"📨 Отправка реквизита {idx}/{len(requisites)} (id={requisite['id']}) админу {admin_chat_id}")
				await bot.send_message(
					chat_id=admin_chat_id,
					text=requisite["requisite_text"],
					parse_mode="HTML"
				)
				sent_count += 1
				logger.info(f"✅ Реквизит {requisite['id']} успешно отправлен админу {admin_chat_id}")
			except Exception as e:
				logger.exception(f"❌ Ошибка отправки реквизита {requisite['id']} админу {admin_chat_id}: {e}")
	
	# Также отправляем user_message (для обратной совместимости со старыми данными)
	user_msg = await db.get_card_user_message(card_id)
	logger.info(f"🔍 Проверка user_message для card_id={card_id}: value={user_msg[:100] if user_msg else None}..., is_empty={not (user_msg and user_msg.strip())}")
	if user_msg and user_msg.strip():
		try:
			logger.info(f"📨 Отправка user_message админу {admin_chat_id}")
			await bot.send_message(chat_id=admin_chat_id, text=user_msg, parse_mode="HTML")
			sent_count += 1
			logger.info(f"✅ user_message отправлен админу {admin_chat_id}")
		except Exception as e:
			logger.exception(f"❌ Ошибка отправки user_message админу {admin_chat_id}: {e}")
	else:
		logger.info(f"⚠️ user_message для card_id={card_id} пустой или отсутствует, пропускаем")
	
	if sent_count == 0:
		logger.warning(f"⚠️ Нет ни реквизитов, ни user_message для card_id={card_id}")
	
	return sent_count


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


class CardRequisiteStates(StatesGroup):
	waiting_requisite = State()
	waiting_edit_requisite = State()


class CardColumnBindStates(StatesGroup):
	selecting_card = State()
	waiting_column = State()


class ForwardBindStates(StatesGroup):
	waiting_select_group = State()  # Состояние для выбора группы карт при пересылке
	waiting_select_card = State()
	waiting_select_existing_card = State()
	editing_crypto_amount = State()  # Состояние для редактирования количества криптовалюты
	editing_cash_amount = State()  # Состояние для редактирования количества наличных
	selecting_card_for_cash = State()  # Состояние для выбора карты при вводе наличных


class AddDataStates(StatesGroup):
	"""Состояния для команд /add и /rate"""
	selecting_type = State()  # Выбор типа данных (криптовалюта, наличные, карта)
	entering_crypto = State()  # Ввод суммы криптовалюты в USD
	selecting_cash_name = State()  # Выбор названия наличных
	entering_cash = State()  # Ввод суммы наличных (без карты)
	entering_card_cash = State()  # Ввод суммы наличных для карты
	selecting_card = State()  # Выбор карты
	selecting_xmr = State()  # Выбор номера XMR (1, 2, 3)


class CryptoColumnEditStates(StatesGroup):
	waiting_column = State()
	waiting_crypto_name = State()
	waiting_crypto_column = State()


class CardGroupStates(StatesGroup):
	waiting_group_name = State()


class CashColumnEditStates(StatesGroup):
	waiting_column = State()
	waiting_cash_name = State()
	waiting_cash_column = State()


class DeleteRowStates(StatesGroup):
	first_confirmation = State()
	second_confirmation = State()


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




@admin_router.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		logger.debug(f"/admin ignored: user {message.from_user.id} is not admin")
		return
	await message.answer("Админ-панель:", reply_markup=admin_menu_kb())




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
	
	# Спрашиваем первое подтверждение
	await state.set_state(DeleteRowStates.first_confirmation)
	await message.answer("⚠️ Вы действительно хотите удалить последнюю строку?", reply_markup=delete_confirmation_kb())


@admin_router.callback_query(DeleteRowStates.first_confirmation, F.data == "delete:confirm:yes")
async def delete_first_confirmation_yes(cb: CallbackQuery, state: FSMContext):
	"""Обработчик первого подтверждения удаления - пользователь нажал 'Да'"""
	# Переходим ко второму подтверждению
	await state.set_state(DeleteRowStates.second_confirmation)
	await cb.message.edit_text("⚠️ Вы уверены? Это действие нельзя отменить.", reply_markup=delete_confirmation_kb())
	await cb.answer()


@admin_router.callback_query(DeleteRowStates.first_confirmation, F.data == "delete:confirm:no")
async def delete_first_confirmation_no(cb: CallbackQuery, state: FSMContext):
	"""Обработчик первого подтверждения удаления - пользователь нажал 'Нет'"""
	await state.clear()
	await cb.message.edit_text("❌ Операция удаления отменена.")
	await cb.answer()


@admin_router.callback_query(DeleteRowStates.second_confirmation, F.data == "delete:confirm:yes")
async def delete_second_confirmation_yes(cb: CallbackQuery, state: FSMContext):
	"""Обработчик второго подтверждения удаления - выполняет удаление"""
	# Удаляем последнюю строку
	from app.google_sheets import delete_last_row_from_google_sheet
	from app.config import get_settings
	
	settings = get_settings()
	
	try:
		result = await delete_last_row_from_google_sheet(
			settings.google_sheet_id,
			settings.google_credentials_path,
			settings.google_sheet_name
		)
		
		if result.get("success"):
			deleted_row = result.get("deleted_row")
			await cb.message.edit_text(f"✅ Успешно удалена строка {deleted_row}")
		else:
			error_message = result.get("message", "Неизвестная ошибка")
			await cb.message.edit_text(f"❌ Ошибка удаления: {error_message}")
	except Exception as e:
		logger.exception(f"Ошибка при удалении строки: {e}")
		await cb.message.edit_text(f"❌ Произошла ошибка при удалении: {str(e)}")
	finally:
		await state.clear()
		await cb.answer()


@admin_router.callback_query(DeleteRowStates.second_confirmation, F.data == "delete:confirm:no")
async def delete_second_confirmation_no(cb: CallbackQuery, state: FSMContext):
	"""Обработчик второго подтверждения удаления - пользователь нажал 'Нет'"""
	await state.clear()
	await cb.message.edit_text("❌ Операция удаления отменена.")
	await cb.answer()


@admin_router.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery, state: FSMContext):
	await state.clear()
	await cb.message.edit_text("Админ-панель:", reply_markup=admin_menu_kb())
	await cb.answer()


def format_add_data_text(data: dict) -> str:
	"""Форматирует текст с выбранными данными для меню /add"""
	text = "📋 Добавление данных\n\n"
	
	# Показываем все сохраненные блоки данных
	selected_items = []
	
	# Показываем сохраненные блоки
	saved_blocks = data.get("saved_blocks", [])
	for block_idx, block in enumerate(saved_blocks, 1):
		block_items = []
		block_crypto = block.get("crypto_data")
		if block_crypto:
			currency = block_crypto.get("currency", "")
			usd_amount = block_crypto.get("usd_amount", 0)
			xmr_number = block_crypto.get("xmr_number")
			if xmr_number:
				block_items.append(f"🪙 XMR-{xmr_number}: ${int(usd_amount)}")
			else:
				block_items.append(f"🪙 {currency}: ${int(usd_amount)}")
		
		block_card = block.get("card_data")
		block_card_cash = block.get("card_cash_data")
		if block_card:
			card_name = block_card.get("card_name", "")
			if block_card_cash:
				amount = block_card_cash.get("value", 0)
				block_items.append(f"💳{card_name}: {amount} р.")
			else:
				block_items.append(f"💳{card_name}")
		
		block_cash = block.get("cash_data")
		if block_cash:
			amount = block_cash.get("value", 0)
			cash_name = block_cash.get("cash_name", "Наличные")
			block_items.append(f"💵 {cash_name}: {amount}")
		
		if block_items:
			selected_items.append(f"{block_idx}: " + ", ".join(block_items))
	
	# Показываем текущий блок (если есть)
	current_block_items = []
	crypto_data = data.get("crypto_data")
	if crypto_data:
		currency = crypto_data.get("currency", "")
		usd_amount = crypto_data.get("usd_amount", 0)
		xmr_number = crypto_data.get("xmr_number")
		if xmr_number:
			current_block_items.append(f"🪙 XMR-{xmr_number}: ${int(usd_amount)}")
		else:
			current_block_items.append(f"🪙 {currency}: ${int(usd_amount)}")
	
	card_data = data.get("card_data")
	cash_data = data.get("cash_data")
	card_cash_data = data.get("card_cash_data")  # Наличные для карты
	
	# Обрабатываем карту
	if card_data:
		card_name = card_data.get("card_name", "")
		if card_cash_data:
			# Карта с наличными
			amount = card_cash_data.get("value", 0)
			current_block_items.append(f"💳{card_name}: {amount} р.")
		else:
			# Только карта без наличных
			current_block_items.append(f"💳{card_name}")
	
	# Обрабатываем наличные без карты
	if cash_data:
		amount = cash_data.get("value", 0)
		cash_name = cash_data.get("cash_name", "Наличные")
		current_block_items.append(f"💵 {cash_name}: {amount}")
	
	if current_block_items:
		current_block_num = len(saved_blocks) + 1
		selected_items.append(f"{current_block_num}: " + ", ".join(current_block_items))
	
	if selected_items:
		text += "Выбранные данные:\n" + "\n".join(selected_items) + "\n\n"
	
	text += "Выберите тип данных для добавления:"
	return text


@admin_router.message(F.text == "/add")
async def cmd_add(message: Message, state: FSMContext):
	"""Команда для вызова меню добавления данных в таблицу (режим add)"""
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	await state.set_state(AddDataStates.selecting_type)
	await state.update_data(
		mode="add",
		crypto_data=None,
		cash_data=None,
		card_data=None,
		card_cash_data=None,
		xmr_number=None,
		saved_blocks=[],
		crypto_list=[],
		xmr_list=[],
		cash_list=[],
		card_cash_pairs=[]
	)
	
	from app.keyboards import add_data_type_kb
	data = await state.get_data()
	text = format_add_data_text(data)
	await message.answer(text, reply_markup=add_data_type_kb(mode="add", data=data))


@admin_router.message(Command("rate"))
async def cmd_rate(message: Message, state: FSMContext):
	"""Команда для вызова меню добавления данных в таблицу (режим rate)"""
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	await state.set_state(AddDataStates.selecting_type)
	await state.update_data(
		mode="rate",
		crypto_data=None,
		cash_data=None,
		card_data=None,
		card_cash_data=None,
		xmr_number=None,
		saved_blocks=[],
		crypto_list=[],
		xmr_list=[],
		cash_list=[],
		card_cash_pairs=[]
	)
	
	from app.keyboards import add_data_type_kb
	data = await state.get_data()
	text = format_add_data_text(data)
	await message.answer(text, reply_markup=add_data_type_kb(mode="rate", data=data))


@admin_router.callback_query(F.data == "admin:cash")
async def admin_cash(cb: CallbackQuery):
	"""Показывает список наличных с их адресами столбцов"""
	db = get_db()
	cash_columns = await db.list_cash_columns()
	logger.debug(f"Show cash columns: count={len(cash_columns)}")
	
	if not cash_columns:
		text = "Список наличных пуст."
	else:
		text = "Список наличных и их адресов столбцов:\n\n"
		for cash in cash_columns:
			cash_name = cash.get("cash_name", "")
			column = cash.get("column", "")
			text += f"{cash_name} → {column}\n"
	
	await cb.message.edit_text(text, reply_markup=cash_list_kb(cash_columns))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:type:"))
async def add_data_select_type(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора типа данных в командах /add и /rate"""
	parts = cb.data.split(":")
	data_type = parts[2]  # crypto, cash, card
	
	# Определяем, редактируется ли сохраненный блок или текущий
	# Формат для сохраненного блока: add_data:type:crypto:block:{block_idx}:{mode}
	# Формат для текущего блока: add_data:type:crypto:current:{mode}
	# Старый формат (для обратной совместимости): add_data:type:crypto:{mode}
	block_idx = None
	if len(parts) >= 5 and parts[3] == "block":
		# Редактирование сохраненного блока
		block_idx = int(parts[4])
		mode = parts[5]
		# Загружаем данные блока в текущий блок для редактирования
		data = await state.get_data()
		
		# Сохраняем текущий блок перед загрузкой данных для редактирования (если есть данные)
		current_crypto_data = data.get("crypto_data")
		current_cash_data = data.get("cash_data")
		current_card_data = data.get("card_data")
		current_card_cash_data = data.get("card_cash_data")
		has_current_data = current_crypto_data or current_cash_data or current_card_data
		
		if has_current_data:
			# Сохраняем текущий блок в saved_blocks (только в saved_blocks, не в списки)
			saved_blocks = data.get("saved_blocks", [])
			
			# Добавляем текущий блок в saved_blocks
			saved_blocks.append({
				"crypto_data": current_crypto_data.copy() if current_crypto_data else None,
				"cash_data": current_cash_data.copy() if current_cash_data else None,
				"card_data": current_card_data.copy() if current_card_data else None,
				"card_cash_data": current_card_cash_data.copy() if current_card_cash_data else None
			})
			
			# Обновляем только saved_blocks (списки будут заполнены при подтверждении из saved_blocks)
			await state.update_data(saved_blocks=saved_blocks)
			# Получаем обновленные данные после сохранения текущего блока
			data = await state.get_data()
		
		# Теперь загружаем данные сохраненного блока для редактирования
		saved_blocks = data.get("saved_blocks", [])
		if 0 <= block_idx < len(saved_blocks):
			block = saved_blocks[block_idx]
			# Загружаем данные блока в текущий блок для редактирования
			await state.update_data(
				crypto_data=block.get("crypto_data").copy() if block.get("crypto_data") else None,
				cash_data=block.get("cash_data").copy() if block.get("cash_data") else None,
				card_data=block.get("card_data").copy() if block.get("card_data") else None,
				card_cash_data=block.get("card_cash_data").copy() if block.get("card_cash_data") else None,
				xmr_number=block.get("crypto_data", {}).get("xmr_number") if block.get("crypto_data") else None,
				crypto_currency=block.get("crypto_data", {}).get("currency") if block.get("crypto_data") else None,
				cash_name=block.get("cash_data", {}).get("cash_name") if block.get("cash_data") else None,
				editing_block_idx=block_idx  # Сохраняем индекс редактируемого блока
			)
	elif len(parts) >= 5 and parts[3] == "current":
		# Редактирование текущего блока
		mode = parts[4]
		await state.update_data(editing_block_idx=None)  # Очищаем индекс, если редактируем текущий блок
	else:
		# Старый формат (обратная совместимость)
		mode = parts[3]
		await state.update_data(editing_block_idx=None)
	
	data = await state.get_data()
	
	if data_type == "crypto":
		# Показываем выбор криптовалюты
		from app.keyboards import crypto_select_kb
		# Получаем список криптовалют из БД
		db = get_db()
		crypto_columns = await db.list_crypto_columns()
		await state.set_state(AddDataStates.selecting_type)
		await cb.message.edit_text(
			"🪙 Выберите криптовалюту:",
			reply_markup=crypto_select_kb(back_to=f"add_data:back:{mode}", show_confirm=False, crypto_columns=crypto_columns)
		)
		await cb.answer()
	elif data_type == "cash":
		# Показываем выбор названия наличных
		db = get_db()
		cash_columns = await db.list_cash_columns()
		if not cash_columns:
			await cb.answer("❌ Нет доступных наличных. Добавьте наличные в меню 'Наличные'.", show_alert=True)
			return
		
		from app.keyboards import cash_select_kb
		await state.set_state(AddDataStates.selecting_cash_name)
		await cb.message.edit_text(
			"💵 Выберите название наличных:",
			reply_markup=cash_select_kb(cash_columns, mode=mode, back_to=f"add_data:back")
		)
		await cb.answer()
	elif data_type == "card":
		# Показываем выбор карты
		db = get_db()
		groups = await db.list_card_groups()
		# Получаем последние используемые карты для текущего админа
		admin_id = cb.from_user.id
		recent_cards = await db.get_recent_cards_by_admin(admin_id, limit=4)
		from app.keyboards import card_groups_select_kb
		await state.set_state(AddDataStates.selecting_card)
		text = "💳 Выберите группу карт:" if groups else "💳 Групп пока нет. Выберите карты без группы:"
		await cb.message.edit_text(text, reply_markup=card_groups_select_kb(groups, back_to=f"add_data:back:{mode}", recent_cards=recent_cards))
		await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:back:") & ~F.data.contains(":group:"))
async def add_data_back(cb: CallbackQuery, state: FSMContext):
	"""Возврат к меню выбора типа данных"""
	parts = cb.data.split(":")
	mode = parts[2]
	
	await state.set_state(AddDataStates.selecting_type)
	from app.keyboards import add_data_type_kb
	data = await state.get_data()
	text = format_add_data_text(data)
	await cb.message.edit_text(text, reply_markup=add_data_type_kb(mode=mode, data=data))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:back:") & F.data.contains(":group:"))
async def add_data_select_group(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора группы карт в командах /add и /rate"""
	# Формат: add_data:back:{mode}:group:{group_id}
	parts = cb.data.split(":")
	mode = parts[2]
	group_id_str = parts[4]
	group_id = int(group_id_str) if group_id_str != "0" else None
	
	db = get_db()
	if group_id:
		cards = await db.get_cards_by_group(group_id)
		group = await db.get_card_group(group_id)
		group_name = group.get("name", "Группа") if group else "Группа"
		text = f"💳 Карты группы '{group_name}':"
	else:
		cards = await db.get_cards_without_group()
		text = "💳 Карты вне групп:"
	
	if not cards:
		await cb.answer("В этой группе нет карт", show_alert=True)
		return
	
	cards_list = [(c[0], c[1]) for c in cards]
	from app.keyboards import cards_list_kb
	await state.set_state(AddDataStates.selecting_card)
	await cb.message.edit_text(text, reply_markup=cards_list_kb(cards_list, with_add=False, back_to=f"add_data:back:{mode}"))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("crypto:select:"))
async def add_data_select_crypto(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора криптовалюты в командах /add и /rate"""
	currency = cb.data.split(":")[-1]
	data = await state.get_data()
	mode = data.get("mode", "add")
	
	if currency == "XMR":
		# Для XMR нужно выбрать номер
		await state.set_state(AddDataStates.selecting_xmr)
		from app.keyboards import add_data_xmr_select_kb
		await cb.message.edit_text(
			"🪙 Выберите номер XMR:",
			reply_markup=add_data_xmr_select_kb(mode=mode, back_to=f"add_data:back:{mode}")
		)
		await cb.answer()
	else:
		# Для других криптовалют запрашиваем сумму в USD
		await state.set_state(AddDataStates.entering_crypto)
		await state.update_data(crypto_currency=currency)
		await cb.message.edit_text(
			f"🪙 Введите сумму в USD для {currency}:",
			reply_markup=simple_back_kb(f"add_data:back:{mode}")
		)
		await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:xmr:"))
async def add_data_select_xmr(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора номера XMR"""
	parts = cb.data.split(":")
	xmr_number = int(parts[2])
	mode = parts[3]
	
	await state.update_data(xmr_number=xmr_number, crypto_currency="XMR")
	await state.set_state(AddDataStates.entering_crypto)
	await cb.message.edit_text(
		f"🪙 Введите сумму в USD для XMR-{xmr_number}:",
		reply_markup=simple_back_kb(f"add_data:back:{mode}")
	)
	await cb.answer()


@admin_router.message(AddDataStates.entering_crypto)
async def add_data_enter_crypto(message: Message, state: FSMContext):
	"""Обработчик ввода суммы криптовалюты"""
	try:
		usd_amount = float(message.text.replace(",", "."))
		
		data = await state.get_data()
		currency = data.get("crypto_currency", "BTC")
		xmr_number = data.get("xmr_number")
		editing_block_idx = data.get("editing_block_idx")
		
		crypto_data = {
			"currency": currency,
			"usd_amount": usd_amount,
			"value": usd_amount
		}
		if xmr_number:
			crypto_data["xmr_number"] = xmr_number
		
		# Если редактируется сохраненный блок, обновляем его
		if editing_block_idx is not None:
			saved_blocks = data.get("saved_blocks", [])
			if 0 <= editing_block_idx < len(saved_blocks):
				saved_blocks[editing_block_idx]["crypto_data"] = crypto_data.copy()
				
				# Очищаем весь текущий блок после обновления сохраненного блока
				# Списки будут пересобраны из saved_blocks в add_data_confirm
				await state.update_data(
					saved_blocks=saved_blocks,
					crypto_data=None,  # Очищаем текущий блок
					card_data=None,  # Очищаем текущий блок
					card_cash_data=None,  # Очищаем текущий блок
					cash_data=None,  # Очищаем текущий блок
					xmr_number=None,  # Очищаем текущий блок
					crypto_currency=None,  # Очищаем текущий блок
					cash_name=None,  # Очищаем текущий блок
					editing_block_idx=None  # Сбрасываем индекс после обновления
				)
			else:
				await state.update_data(crypto_data=crypto_data, editing_block_idx=None)
		else:
			await state.update_data(crypto_data=crypto_data)
		
		await state.set_state(AddDataStates.selecting_type)
		
		mode = data.get("mode", "add")
		from app.keyboards import add_data_type_kb
		data = await state.get_data()
		text = format_add_data_text(data)
		await message.answer(text, reply_markup=add_data_type_kb(mode=mode, data=data))
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число, например: 100 или -100")
	except Exception as e:
		logger.exception(f"Ошибка обработки криптовалюты: {e}")
		await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")


@admin_router.callback_query(AddDataStates.selecting_cash_name, F.data.startswith("add_data:cash_select:"))
async def add_data_select_cash_name(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора названия наличных"""
	parts = cb.data.split(":")
	cash_name = parts[2]
	mode = parts[3]
	
	# Сохраняем название наличных
	await state.update_data(cash_name=cash_name)
	await state.set_state(AddDataStates.entering_cash)
	
	await cb.message.edit_text(
		f"💵 Введите сумму наличных для '{cash_name}' (число):",
		reply_markup=simple_back_kb(f"add_data:back:{mode}")
	)
	await cb.answer()


@admin_router.message(AddDataStates.entering_card_cash)
async def add_data_enter_card_cash(message: Message, state: FSMContext):
	"""Обработчик ввода суммы наличных для карты"""
	try:
		amount = int(float(message.text.replace(",", ".")))
		
		data = await state.get_data()
		editing_block_idx = data.get("editing_block_idx")
		
		# Сохраняем наличные для карты
		card_cash_data = {
			"currency": "RUB",
			"value": amount,
			"display": f"{amount} RUB"
		}
		
		# Если редактируется сохраненный блок, обновляем его
		if editing_block_idx is not None:
			saved_blocks = data.get("saved_blocks", [])
			if 0 <= editing_block_idx < len(saved_blocks):
				# Обновляем card_cash_data в сохраненном блоке
				saved_blocks[editing_block_idx]["card_cash_data"] = card_cash_data.copy()
				# Также обновляем card_data, если он был установлен на предыдущем шаге
				card_data = data.get("card_data")
				if card_data:
					saved_blocks[editing_block_idx]["card_data"] = card_data.copy()
				
				# Очищаем весь текущий блок после обновления сохраненного блока
				await state.update_data(
					saved_blocks=saved_blocks,
					crypto_data=None,  # Очищаем текущий блок
					card_data=None,  # Очищаем текущий блок
					card_cash_data=None,  # Очищаем текущий блок
					cash_data=None,  # Очищаем текущий блок
					xmr_number=None,  # Очищаем текущий блок
					crypto_currency=None,  # Очищаем текущий блок
					cash_name=None,  # Очищаем текущий блок
					editing_block_idx=None  # Сбрасываем индекс после завершения редактирования
				)
			else:
				await state.update_data(card_cash_data=card_cash_data, editing_block_idx=None)
		else:
			await state.update_data(card_cash_data=card_cash_data)
		
		await state.set_state(AddDataStates.selecting_type)

		mode = data.get("mode", "add")
		from app.keyboards import add_data_type_kb
		data = await state.get_data()
		text = format_add_data_text(data)
		await message.answer(text, reply_markup=add_data_type_kb(mode=mode, data=data))
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число, например: 200 или -200")
	except Exception as e:
		logger.exception(f"Ошибка обработки наличных для карты: {e}")
		await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")


@admin_router.message(AddDataStates.entering_cash)
async def add_data_enter_cash(message: Message, state: FSMContext):
	"""Обработчик ввода суммы наличных (без карты)"""
	try:
		amount = int(float(message.text.replace(",", ".")))
		
		data = await state.get_data()
		cash_name = data.get("cash_name", "Наличные")
		editing_block_idx = data.get("editing_block_idx")
		
		cash_data = {
			"currency": "RUB",
			"value": amount,
			"display": f"{amount} RUB",
			"cash_name": cash_name  # Сохраняем название для режима rate
		}
		
		# Если редактируется сохраненный блок, обновляем его
		if editing_block_idx is not None:
			saved_blocks = data.get("saved_blocks", [])
			if 0 <= editing_block_idx < len(saved_blocks):
				saved_blocks[editing_block_idx]["cash_data"] = cash_data.copy()
				
				# Очищаем весь текущий блок после обновления сохраненного блока
				# Списки будут пересобраны из saved_blocks в add_data_confirm
				await state.update_data(
					saved_blocks=saved_blocks,
					crypto_data=None,  # Очищаем текущий блок
					card_data=None,  # Очищаем текущий блок
					card_cash_data=None,  # Очищаем текущий блок
					cash_data=None,  # Очищаем текущий блок
					xmr_number=None,  # Очищаем текущий блок
					crypto_currency=None,  # Очищаем текущий блок
					cash_name=None,  # Очищаем текущий блок
					editing_block_idx=None  # Сбрасываем индекс после обновления
				)
			else:
				await state.update_data(cash_data=cash_data, editing_block_idx=None)
		else:
			await state.update_data(cash_data=cash_data)
		
		await state.set_state(AddDataStates.selecting_type)

		mode = data.get("mode", "add")
		from app.keyboards import add_data_type_kb
		data = await state.get_data()
		text = format_add_data_text(data)
		await message.answer(text, reply_markup=add_data_type_kb(mode=mode, data=data))
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число, например: 5000 или -5000")
	except Exception as e:
		logger.exception(f"Ошибка обработки наличных: {e}")
		await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")


@admin_router.message(AddDataStates.selecting_type)
async def add_data_selecting_type_message(message: Message, state: FSMContext):
	"""Обработчик текстовых сообщений в состоянии selecting_type - игнорируем, показываем подсказку"""
	data = await state.get_data()
	mode = data.get("mode", "add")
	from app.keyboards import add_data_type_kb
	text = format_add_data_text(data)
	await message.answer(text, reply_markup=add_data_type_kb(mode=mode, data=data))


@admin_router.callback_query(AddDataStates.selecting_card, F.data.startswith("card:view:"))
async def add_data_select_card(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора карты в командах /add и /rate"""
	card_id = int(cb.data.split(":")[-1])
	db = get_db()
	card = await db.get_card_by_id(card_id)
	
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	# Логируем выбор карты для отслеживания последних используемых карт
	admin_id = cb.from_user.id
	await db.log_card_selection(card_id, admin_id)
	
	data = await state.get_data()
	mode = data.get("mode", "add")
	editing_block_idx = data.get("editing_block_idx")
	
	# Получаем адрес столбца для карты
	column = await db.get_card_column(card_id)
	
	card_data = {
		"card_id": card_id,
		"card_name": card.get("name", ""),
		"user_name": None,
		"column": column
	}
	
	# Если редактируется сохраненный блок, обновляем его
	# НО НЕ сбрасываем editing_block_idx - он нужен для следующего шага (ввода суммы)
	if editing_block_idx is not None:
		saved_blocks = data.get("saved_blocks", [])
		if 0 <= editing_block_idx < len(saved_blocks):
			saved_blocks[editing_block_idx]["card_data"] = card_data.copy()
			# Сохраняем card_data во временное хранилище для следующего шага
			# НЕ очищаем editing_block_idx - он нужен для ввода суммы
			await state.update_data(
				saved_blocks=saved_blocks,
				card_data=card_data,  # Сохраняем для следующего шага
				crypto_data=None,  # Очищаем другие данные текущего блока
				cash_data=None,
				xmr_number=None,
				crypto_currency=None,
				cash_name=None
				# НЕ сбрасываем editing_block_idx и card_cash_data
			)
		else:
			await state.update_data(card_data=card_data, editing_block_idx=None)
	else:
		await state.update_data(card_data=card_data)
	# После выбора карты запрашиваем ввод суммы наличных для карты
	await state.set_state(AddDataStates.entering_card_cash)
	
	from app.keyboards import simple_back_kb
	text = f"✅ Карта выбрана: {card.get('name', '')}\n\n💵 Введите сумму наличных для карты (число):"
	await cb.message.edit_text(text, reply_markup=simple_back_kb(f"add_data:back:{mode}"))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:add_block:"))
async def add_data_add_block(cb: CallbackQuery, state: FSMContext):
	"""Обработчик добавления нового блока данных"""
	mode = cb.data.split(":")[-1]
	data = await state.get_data()
	
	# Получаем текущие данные
	crypto_data = data.get("crypto_data")
	cash_data = data.get("cash_data")
	card_data = data.get("card_data")
	card_cash_data = data.get("card_cash_data")
	
	# Проверяем, есть ли данные для сохранения
	has_data = crypto_data or cash_data or card_data
	
	if not has_data:
		await cb.answer("⚠️ Нет данных для сохранения. Добавьте данные перед нажатием '+'.", show_alert=True)
		return
	
	# Проверяем, не редактируется ли сохраненный блок
	editing_block_idx = data.get("editing_block_idx")
	if editing_block_idx is not None:
		await cb.answer("⚠️ Завершите редактирование текущего блока перед добавлением нового.", show_alert=True)
		return
	
	# Получаем список сохраненных блоков
	saved_blocks = data.get("saved_blocks", [])
	
	# Сохраняем текущий блок как новый сохраненный блок
	saved_blocks.append({
		"crypto_data": crypto_data.copy() if crypto_data else None,
		"cash_data": cash_data.copy() if cash_data else None,
		"card_data": card_data.copy() if card_data else None,
		"card_cash_data": card_cash_data.copy() if card_cash_data else None
	})
	
	# Очищаем текущие данные для нового блока
	# Списки будут пересобраны из saved_blocks в add_data_confirm
	await state.update_data(
		crypto_data=None,
		cash_data=None,
		card_data=None,
		card_cash_data=None,
		xmr_number=None,
		crypto_currency=None,
		cash_name=None,
		saved_blocks=saved_blocks
	)
	
	# Обновляем сообщение
	from app.keyboards import add_data_type_kb
	data = await state.get_data()
	text = format_add_data_text(data)
	try:
		await cb.message.edit_text(text, reply_markup=add_data_type_kb(mode=mode, data=data))
	except Exception as e:
		# Игнорируем ошибку, если сообщение не изменилось
		if "message is not modified" not in str(e):
			raise
	await cb.answer("✅ Блок данных сохранен. Добавьте новый блок.")


@admin_router.callback_query(F.data.startswith("add_data:confirm:"))
async def add_data_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик подтверждения и записи данных в Google Sheets"""
	# Отвечаем на callback сразу, чтобы избежать таймаута
	try:
		await cb.answer("⏳ Запись данных...")
	except Exception:
		# Если callback уже устарел, продолжаем выполнение
		pass
	
	# Сразу обновляем сообщение, показывая процесс записи
	try:
		await cb.message.edit_text("⏳ Запись данных, подождите...", reply_markup=None)
	except Exception:
		# Если не удалось обновить сообщение, продолжаем выполнение
		pass
	
	mode = cb.data.split(":")[-1]
	data = await state.get_data()
	
	# Получаем текущие данные
	crypto_data = data.get("crypto_data")
	cash_data = data.get("cash_data")
	card_data = data.get("card_data")
	card_cash_data = data.get("card_cash_data")  # Наличные для карты
	
	# Получаем сохраненные блоки
	saved_blocks = data.get("saved_blocks", [])
	
	# Пересобираем списки из saved_blocks, чтобы гарантировать правильный порядок и актуальность данных
	# Это важно, особенно после редактирования блоков
	crypto_list = []
	xmr_list = []
	cash_list = []
	card_cash_pairs = []
	
	# Сначала добавляем данные из всех сохраненных блоков
	for block in saved_blocks:
		block_crypto = block.get("crypto_data")
		if block_crypto:
			currency = block_crypto.get("currency")
			usd_amount = block_crypto.get("usd_amount", 0)
			xmr_number = block_crypto.get("xmr_number")
			
			if currency == "XMR" and xmr_number:
				xmr_list.append({
					"xmr_number": xmr_number,
					"usd_amount": usd_amount
				})
			else:
				crypto_list.append({
					"currency": currency,
					"usd_amount": usd_amount
				})
		
		block_card = block.get("card_data")
		block_card_cash = block.get("card_cash_data")
		if block_card:
			if block_card_cash:
				card_cash_pairs.append({
					"card": block_card.copy(),
					"cash": block_card_cash.copy()
				})
			else:
				card_cash_pairs.append({
					"card": block_card.copy(),
					"cash": None
				})
		
		block_cash = block.get("cash_data")
		if block_cash:
			cash_list.append({
				"currency": block_cash.get("currency", "RUB"),
				"value": block_cash.get("value", 0),
				"cash_name": block_cash.get("cash_name")
			})
	
	# Затем добавляем текущие данные (если есть) - это новый блок, который еще не был сохранен
	if crypto_data:
		currency = crypto_data.get("currency")
		usd_amount = crypto_data.get("usd_amount", 0)
		xmr_number = crypto_data.get("xmr_number")
		
		if currency == "XMR" and xmr_number:
			xmr_list.append({
				"xmr_number": xmr_number,
				"usd_amount": usd_amount
			})
		else:
			crypto_list.append({
				"currency": currency,
				"usd_amount": usd_amount
			})
	
	if card_data:
		if card_cash_data:
			card_cash_pairs.append({
				"card": card_data.copy(),
				"cash": card_cash_data.copy()
			})
		else:
			card_cash_pairs.append({
				"card": card_data.copy(),
				"cash": None
			})
	
	if cash_data:
		cash_list.append({
			"currency": cash_data.get("currency", "RUB"),
			"value": cash_data.get("value", 0),
			"cash_name": cash_data.get("cash_name")
		})
	
	# Проверяем, что есть хотя бы какие-то данные
	if not crypto_list and not xmr_list and not cash_list and not card_cash_pairs:
		try:
			await cb.answer("❌ Нет данных для записи. Добавьте хотя бы один тип данных.", show_alert=True)
		except Exception:
			pass
		return
	
	from app.config import get_settings
	from app.google_sheets import write_all_to_google_sheet_one_row, write_to_google_sheet_rate_mode
	
	settings = get_settings()
	if not settings.google_sheet_id or not settings.google_credentials_path:
		try:
			await cb.answer("❌ Google Sheets не настроен", show_alert=True)
		except Exception:
			pass
		return
	
	# Данные уже собраны в списки выше
	logger.info(f"🔍 Формирование cash_list: cash_list={cash_list}")
	
	# Записываем в Google Sheets
	logger.info(f"🔍 Данные для записи (mode={mode}): crypto_list={crypto_list}, xmr_list={xmr_list}, cash_list={cash_list}, card_cash_pairs={card_cash_pairs}")
	try:
		if mode == "rate":
			result = await write_to_google_sheet_rate_mode(
				settings.google_sheet_id,
				settings.google_credentials_path,
				crypto_list,
				xmr_list,
				cash_list,
				card_cash_pairs,
				settings.google_sheet_name
			)
		else:
			result = await write_all_to_google_sheet_one_row(
				settings.google_sheet_id,
				settings.google_credentials_path,
				crypto_list,
				xmr_list,
				cash_list,
				card_cash_pairs,
				settings.google_sheet_name
			)
		
		if result.get("success"):
			# Формируем отчет о записи
			from datetime import datetime
			from app.google_sheets import read_card_balance, read_profit
			current_date = datetime.now().strftime("%d.%m.%Y")
			
			written_cells = result.get("written_cells", [])
			row = result.get("row")
			column_rows = result.get("column_rows", {})  # Для режима rate: {column: row}
			
			report_lines = [f"📊 Отчет о записи данных ({current_date}):\n"]
			
			if mode == "add" and row:
				report_lines.append(f"📍 Строка: {row}\n")
			
			if written_cells:
				report_lines.append("✅ Записано:")
				for cell_info in written_cells:
					report_lines.append(f"  • {cell_info}")
			else:
				report_lines.append("⚠️ Нет записанных данных")
			
			# Читаем балансы карт и профиты
			# Получаем настройки из БД
			db = get_db()
			balance_row_str = await db.get_google_sheets_setting("balance_row", "4")
			profit_column_str = await db.get_google_sheets_setting("profit_column", "BC")
			balance_row = int(balance_row_str) if balance_row_str else 4
			profit_column = profit_column_str if profit_column_str else "BC"
			
			# Читаем балансы для всех карт из card_cash_pairs (batch чтение)
			from app.google_sheets import read_card_balances_batch, read_profits_batch
			
			card_balances = {}
			balance_cell_addresses = []
			card_mapping = {}  # {cell_address: (card_name, column)}
			
			for pair in card_cash_pairs:
				card_data = pair.get("card")
				if card_data:
					card_name = card_data.get("card_name", "")
					column = card_data.get("column")
					if column:
						cell_address = f"{column}{balance_row}"
						balance_cell_addresses.append(cell_address)
						card_mapping[cell_address] = (card_name, column)
			
			# Читаем все балансы одним batch запросом
			if balance_cell_addresses:
				balances = await read_card_balances_batch(
					settings.google_sheet_id,
					settings.google_credentials_path,
					balance_cell_addresses,
					settings.google_sheet_name
				)
				for cell_address, (card_name, column) in card_mapping.items():
					balance = balances.get(cell_address)
					if balance:
						card_balances[card_name] = {"balance": balance, "column": column}
			
			# Читаем профиты (batch чтение)
			profits = {}
			profit_cell_addresses = []
			
			if mode == "add" and row:
				# В режиме /add все данные в одной строке
				cell_address = f"{profit_column}{row}"
				profit_cell_addresses.append(cell_address)
			elif mode == "rate" and column_rows:
				# В режиме /rate может быть несколько строк для разных столбцов
				for column, written_row in column_rows.items():
					cell_address = f"{profit_column}{written_row}"
					profit_cell_addresses.append(cell_address)
			
			# Читаем все профиты одним batch запросом
			if profit_cell_addresses:
				profits_dict = await read_profits_batch(
					settings.google_sheet_id,
					settings.google_credentials_path,
					profit_cell_addresses,
					settings.google_sheet_name
				)
				profits = profits_dict
			
			# Добавляем информацию о балансах и профите в отчет
			# Профит отображаем только в режиме /add
			if card_balances or (profits and mode == "add"):
				report_lines.append("\n💰 Дополнительная информация:")
				
				if card_balances:
					for card_name, data in card_balances.items():
						report_lines.append(f"  💳 {card_name}: Баланс ({data['column']}{balance_row}) = {data['balance']}")
				
				if profits and mode == "add":
					for cell_address, profit_value in profits.items():
						report_lines.append(f"  📈 Профит сделки ({cell_address}) = {profit_value}")
			
			# Проверяем наличие ошибок
			failed_writes = result.get("failed_writes", [])
			if failed_writes:
				report_lines.append("\n❌ Не записано:")
				for failed in failed_writes:
					report_lines.append(f"  • {failed}")
			
			report_text = "\n".join(report_lines)
			
			# Callback уже был обработан в начале функции
			await state.clear()
			await cb.message.edit_text(report_text, reply_markup=admin_menu_kb())
		else:
			try:
				await cb.answer("❌ Ошибка записи в Google Sheets", show_alert=True)
			except Exception:
				# Если callback устарел, просто обновляем сообщение
				await cb.message.edit_text("❌ Ошибка записи в Google Sheets", reply_markup=admin_menu_kb())
	except Exception as e:
		logger.exception(f"Ошибка записи в Google Sheets: {e}")
		try:
			await cb.answer("❌ Произошла ошибка при записи", show_alert=True)
		except Exception:
			# Если callback устарел, просто обновляем сообщение
			try:
				await cb.message.edit_text("❌ Произошла ошибка при записи", reply_markup=admin_menu_kb())
			except Exception:
				pass


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


@admin_router.callback_query(F.data == "cash:new")
async def cash_new(cb: CallbackQuery, state: FSMContext):
	"""Начинает создание новых наличных"""
	await state.set_state(CashColumnEditStates.waiting_cash_name)
	await cb.message.edit_text(
		"Введите название наличных:\n\nНапример: Рубли, Доллары, Евро",
		reply_markup=simple_back_kb("admin:cash")
	)
	await cb.answer()


@admin_router.message(CashColumnEditStates.waiting_cash_name)
async def cash_name_input(message: Message, state: FSMContext):
	"""Обрабатывает ввод названия наличных"""
	cash_name = message.text.strip()
	
	if not cash_name:
		await message.answer("❌ Название наличных не может быть пустым. Попробуйте еще раз:")
		return
	
	# Сохраняем название в state
	await state.update_data(cash_name=cash_name)
	await state.set_state(CashColumnEditStates.waiting_cash_column)
	
	await message.answer(
		"✅ Название сохранено.\n\n"
		"Теперь введите адрес столбца (только латинские буквы):\n"
		"Например: A, B, AS, AY",
		reply_markup=simple_back_kb("admin:cash")
	)


@admin_router.message(CashColumnEditStates.waiting_cash_column)
async def cash_column_input(message: Message, state: FSMContext):
	"""Обрабатывает ввод адреса столбца для новых наличных"""
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
	cash_name = data.get("cash_name")
	
	if not cash_name:
		await message.answer("❌ Ошибка: название наличных не найдено. Попробуйте начать заново.")
		await state.clear()
		return
	
	# Сохраняем наличные
	try:
		await db.set_cash_column(cash_name, column_input)
		
		await message.answer(
			f"✅ Наличные успешно добавлены!\n\n"
			f"Название: {cash_name}\n"
			f"Адрес столбца: {column_input}",
			reply_markup=simple_back_kb("admin:cash")
		)
		await state.clear()
	except Exception as e:
		logger.exception(f"Ошибка при сохранении наличных: {e}")
		if "UNIQUE constraint failed" in str(e):
			await message.answer("❌ Наличные с таким названием уже существуют. Попробуйте другое название:")
		else:
			await message.answer("❌ Произошла ошибка при сохранении. Попробуйте еще раз.")


@admin_router.callback_query(F.data == "cash:delete_list")
async def cash_delete_list(cb: CallbackQuery):
	"""Показывает список наличных для удаления"""
	db = get_db()
	cash_columns = await db.list_cash_columns()
	
	if not cash_columns:
		await cb.answer("Нет наличных для удаления", show_alert=True)
		return
	
	text = "Выберите наличные для удаления:"
	await cb.message.edit_text(text, reply_markup=cash_delete_kb(cash_columns))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("cash:delete:"))
async def cash_delete(cb: CallbackQuery):
	"""Удаляет наличные из базы данных"""
	db = get_db()
	cash_name = cb.data.split(":")[-1]
	
	try:
		await db.delete_cash_column(cash_name)
		await cb.answer(f"✅ Наличные '{cash_name}' удалены", show_alert=True)
		
		# Обновляем список
		cash_columns = await db.list_cash_columns()
		if not cash_columns:
			text = "Список наличных пуст."
		else:
			text = "Список наличных и их адресов столбцов:\n\n"
			for cash in cash_columns:
				cash_name_item = cash.get("cash_name", "")
				column = cash.get("column", "")
				text += f"{cash_name_item} → {column}\n"
		
		await cb.message.edit_text(text, reply_markup=cash_list_kb(cash_columns))
	except Exception as e:
		logger.exception(f"Ошибка при удалении наличных: {e}")
		await cb.answer("❌ Произошла ошибка при удалении", show_alert=True)


@admin_router.callback_query(F.data.startswith("cash:edit:"))
async def cash_edit(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование адреса столбца для наличных"""
	db = get_db()
	cash_name = cb.data.split(":")[-1]
	
	# Получаем текущий адрес столбца
	current_column = await db.get_cash_column(cash_name)
	
	# Сохраняем название наличных в state
	await state.update_data(cash_name=cash_name)
	await state.set_state(CashColumnEditStates.waiting_column)
	
	current_text = f" (текущий: {current_column})" if current_column else ""
	await cb.message.edit_text(
		f"Редактирование адреса столбца для {cash_name}{current_text}\n\n"
		"Введите новый адрес столбца (только латинские буквы):\n"
		"Например: A, B, C, D, E, AS, AY",
		reply_markup=simple_back_kb("admin:cash")
	)
	await cb.answer()


@admin_router.message(CashColumnEditStates.waiting_column)
async def cash_column_waiting_column(message: Message, state: FSMContext):
	"""Обрабатывает ввод адреса столбца для наличных"""
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
	cash_name = data.get("cash_name")
	
	if not cash_name:
		await message.answer("❌ Ошибка: название наличных не найдено. Попробуйте начать заново.")
		await state.clear()
		return
	
	# Сохраняем адрес столбца
	try:
		await db.set_cash_column(cash_name, column_input)
		await state.clear()
		
		# Обновляем список
		cash_columns = await db.list_cash_columns()
		if not cash_columns:
			text = "Список наличных пуст."
		else:
			text = "Список наличных и их адресов столбцов:\n\n"
			for cash in cash_columns:
				cash_name_item = cash.get("cash_name", "")
				column = cash.get("column", "")
				text += f"{cash_name_item} → {column}\n"
		
		await message.answer(
			f"✅ Адрес столбца для '{cash_name}' обновлен на '{column_input}'",
			reply_markup=cash_list_kb(cash_columns)
		)
	except Exception as e:
		logger.exception(f"Ошибка при сохранении адреса столбца для наличных: {e}")
		await message.answer("❌ Произошла ошибка при сохранении. Попробуйте еще раз.")


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
async def card_view(cb: CallbackQuery, state: FSMContext):
	# Проверяем, не находимся ли мы в состоянии выбора карты для /add или /rate
	current_state = await state.get_state()
	if current_state == AddDataStates.selecting_card.state:
		# Это выбор карты для /add или /rate, пропускаем обработку
		return
	
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
	"""Показывает список всех реквизитов карты для редактирования"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	# Получаем все реквизиты
	requisites = await db.list_card_requisites(card_id)
	user_msg = card.get("user_message")
	has_user_message = bool(user_msg and user_msg.strip())
	
	# Формируем текст
	text = f"💳 {card['name']}\n\n"
	if has_user_message or requisites:
		text += "Выберите реквизит для редактирования:"
	else:
		text += "Реквизитов пока нет."
	
	await cb.message.edit_text(
		text,
		reply_markup=requisites_list_kb(requisites, card_id, has_user_message, f"card:view:{card_id}"),
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("card:add_requisite:"))
async def card_add_requisite(cb: CallbackQuery, state: FSMContext):
	"""Показывает форму для добавления реквизита карты"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	logger.debug(f"Add requisite for card_id={card_id}")
	await state.set_state(CardRequisiteStates.waiting_requisite)
	await state.update_data(card_id=card_id)
	await cb.message.edit_text(
		"Отправьте текст реквизита:",
		reply_markup=simple_back_kb(f"card:view:{card_id}"),
	)
	await cb.answer()


@admin_router.message(CardRequisiteStates.waiting_requisite)
async def card_set_requisite(message: Message, state: FSMContext):
	db = get_db()
	data = await state.get_data()
	card_id = int(data.get("card_id"))
	# Получаем текст реквизита с сохранением HTML форматирования
	html_text = message.html_text or message.html_caption or (message.text or message.caption or "").strip()
	
	if not html_text.strip():
		await message.answer("Текст реквизита не может быть пустым")
		return
	
	logger.debug(f"Add requisite for card_id={card_id}")
	await db.add_card_requisite(card_id, html_text)
	await state.clear()
	
	# Получаем информацию о карте для возврата
	card = await db.get_card_by_id(card_id)
	if card:
		await message.answer("Реквизит добавлен ✅", reply_markup=simple_back_kb(f"card:view:{card_id}"))
	else:
		await message.answer("Реквизит добавлен ✅", reply_markup=admin_menu_kb())


@admin_router.callback_query(F.data.startswith("req:select:"))
async def requisite_select(cb: CallbackQuery):
	"""Показывает меню действий для выбранного реквизита"""
	db = get_db()
	requisite_id = int(cb.data.split(":")[-1])
	
	# Получаем информацию о реквизите напрямую из базы
	cur = await db._db.execute("SELECT card_id, requisite_text FROM card_requisites WHERE id = ?", (requisite_id,))
	row = await cur.fetchone()
	if not row:
		await cb.answer("Реквизит не найден", show_alert=True)
		return
	
	card_id = row[0]
	requisite_text = row[1]
	requisite = {
		'id': requisite_id,
		'card_id': card_id,
		'requisite_text': requisite_text
	}
	
	# Обрезаем текст для отображения и экранируем HTML-теги
	text_preview = escape(requisite['requisite_text'][:200])
	if len(requisite['requisite_text']) > 200:
		text_preview += "..."
	
	text = f"📄 Реквизит:\n\n{text_preview}\n\nВыберите действие:"
	
	await cb.message.edit_text(
		text,
		reply_markup=requisite_action_kb(requisite_id=requisite_id, card_id=card_id, back_to=f"card:edit:{card_id}"),
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("req:edit_main:"))
async def requisite_edit_main(cb: CallbackQuery):
	"""Показывает меню действий для основного реквизита (user_message)"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	user_msg = card.get("user_message")
	if not user_msg or not user_msg.strip():
		await cb.answer("Основной реквизит отсутствует", show_alert=True)
		return
	
	# Обрезаем текст для отображения и экранируем HTML-теги
	text_preview = escape(user_msg[:200])
	if len(user_msg) > 200:
		text_preview += "..."
	
	text = f"📝 Основной реквизит:\n\n{text_preview}\n\nВыберите действие:"
	
	await cb.message.edit_text(
		text,
		reply_markup=requisite_action_kb(card_id=card_id, is_main=True, back_to=f"card:edit:{card_id}"),
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("req:edit:"))
async def requisite_edit_start(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование реквизита"""
	db = get_db()
	parts = cb.data.split(":")
	
	if len(parts) >= 4 and parts[2] == "main":
		# Редактирование основного реквизита (формат: req:edit:main:card_id)
		card_id = int(parts[3])
		card = await db.get_card_by_id(card_id)
		if not card:
			await cb.answer("Карта не найдена", show_alert=True)
			return
		
		current = card.get("user_message", "")
		await state.set_state(CardUserMessageStates.waiting_message)
		await state.update_data(card_id=card_id, is_main_requisite=True)
		
		if current:
			# Экранируем HTML для безопасного отображения
			current_escaped = escape(current)
			text = f"Текущий основной реквизит:\n\n{current_escaped}\n\nОтправьте новый текст реквизита.\nДля очистки отправьте: СБРОС"
		else:
			text = "Отправьте текст основного реквизита.\nДля очистки отправьте: СБРОС"
		
		await cb.message.edit_text(
			text,
			reply_markup=simple_back_kb(f"req:edit_main:{card_id}"),
		)
	else:
		# Редактирование дополнительного реквизита (формат: req:edit:requisite_id)
		requisite_id = int(parts[-1])
		
		# Получаем информацию о реквизите
		cur = await db._db.execute("SELECT card_id, requisite_text FROM card_requisites WHERE id = ?", (requisite_id,))
		row = await cur.fetchone()
		if not row:
			await cb.answer("Реквизит не найден", show_alert=True)
			return
		
		card_id = row[0]
		current = row[1]
		
		await state.set_state(CardRequisiteStates.waiting_edit_requisite)
		await state.update_data(requisite_id=requisite_id, card_id=card_id)
		
		# Экранируем HTML для безопасного отображения
		current_escaped = escape(current)
		text = f"Текущий реквизит:\n\n{current_escaped}\n\nОтправьте новый текст реквизита."
		
		await cb.message.edit_text(
			text,
			reply_markup=simple_back_kb(f"req:select:{requisite_id}"),
		)
	
	await cb.answer()


@admin_router.message(CardRequisiteStates.waiting_edit_requisite)
async def requisite_edit_save(message: Message, state: FSMContext):
	"""Сохраняет отредактированный реквизит"""
	db = get_db()
	data = await state.get_data()
	requisite_id = int(data.get("requisite_id"))
	card_id = int(data.get("card_id"))
	
	# Получаем текст реквизита с сохранением HTML форматирования
	html_text = message.html_text or message.html_caption or (message.text or message.caption or "").strip()
	
	if not html_text.strip():
		await message.answer("Текст реквизита не может быть пустым")
		return
	
	logger.debug(f"Update requisite id={requisite_id} for card_id={card_id}")
	await db.update_card_requisite(requisite_id, html_text)
	await state.clear()
	
	await message.answer("Реквизит обновлен ✅", reply_markup=simple_back_kb(f"card:edit:{card_id}"))


@admin_router.callback_query(F.data.startswith("req:delete:"))
async def requisite_delete(cb: CallbackQuery):
	"""Удаляет реквизит"""
	db = get_db()
	parts = cb.data.split(":")
	
	if len(parts) >= 4 and parts[2] == "main":
		# Удаление основного реквизита (user_message) (формат: req:delete:main:card_id)
		card_id = int(parts[3])
		card = await db.get_card_by_id(card_id)
		if not card:
			await cb.answer("Карта не найдена", show_alert=True)
			return
		
		# Очищаем user_message
		await db._db.execute("UPDATE cards SET user_message = NULL WHERE id = ?", (card_id,))
		await db._db.commit()
		
		await cb.answer("Основной реквизит удален ✅", show_alert=True)
		# Возвращаемся к списку реквизитов
		card = await db.get_card_by_id(card_id)
		requisites = await db.list_card_requisites(card_id)
		user_msg = card.get("user_message")
		has_user_message = bool(user_msg and user_msg.strip())
		
		text = f"💳 {card['name']}\n\nВыберите реквизит для редактирования:"
		await cb.message.edit_text(
			text,
			reply_markup=requisites_list_kb(requisites, card_id, has_user_message, f"card:view:{card_id}"),
			parse_mode="HTML",
		)
	else:
		# Удаление дополнительного реквизита (формат: req:delete:requisite_id)
		requisite_id = int(parts[-1])
		
		# Получаем card_id перед удалением
		cur = await db._db.execute("SELECT card_id FROM card_requisites WHERE id = ?", (requisite_id,))
		row = await cur.fetchone()
		if not row:
			await cb.answer("Реквизит не найден", show_alert=True)
			return
		
		card_id = row[0]
		
		await db.delete_card_requisite(requisite_id)
		await cb.answer("Реквизит удален ✅", show_alert=True)
		
		# Возвращаемся к списку реквизитов
		card = await db.get_card_by_id(card_id)
		requisites = await db.list_card_requisites(card_id)
		user_msg = card.get("user_message")
		has_user_message = bool(user_msg and user_msg.strip())
		
		text = f"💳 {card['name']}\n\nВыберите реквизит для редактирования:"
		await cb.message.edit_text(
			text,
			reply_markup=requisites_list_kb(requisites, card_id, has_user_message, f"card:view:{card_id}"),
			parse_mode="HTML",
		)


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
	is_main_requisite = data.get("is_main_requisite", False)
	
	# Используем html_text для сохранения форматирования, но проверяем "СБРОС" по чистому тексту
	plain_text = (message.text or message.caption or "").strip()
	logger.debug(f"Set user_message for card_id={card_id}, reset={(plain_text.upper()=='СБРОС')}, is_main_requisite={is_main_requisite}")
	
	if plain_text.upper() == "СБРОС":
		await db.set_card_user_message(card_id, None)
		await state.clear()
		if is_main_requisite:
			# Возвращаемся к списку реквизитов
			card = await db.get_card_by_id(card_id)
			requisites = await db.list_card_requisites(card_id)
			user_msg = card.get("user_message") if card else None
			has_user_message = bool(user_msg and user_msg.strip())
			text = f"💳 {card['name']}\n\nВыберите реквизит для редактирования:" if card else "Реквизит очищен ✅"
			await message.answer(
				"Основной реквизит очищен ✅",
				reply_markup=requisites_list_kb(requisites, card_id, has_user_message, f"card:view:{card_id}") if card else simple_back_kb(f"card:view:{card_id}"),
			)
		else:
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
	
	if is_main_requisite:
		# Возвращаемся к списку реквизитов
		card = await db.get_card_by_id(card_id)
		requisites = await db.list_card_requisites(card_id)
		user_msg = card.get("user_message") if card else None
		has_user_message = bool(user_msg and user_msg.strip())
		text = f"💳 {card['name']}\n\nВыберите реквизит для редактирования:" if card else "Реквизит обновлен ✅"
		await message.answer(
			"Основной реквизит обновлен ✅",
			reply_markup=requisites_list_kb(requisites, card_id, has_user_message, f"card:view:{card_id}") if card else simple_back_kb(f"card:view:{card_id}"),
		)
	else:
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
	base_lines: List[str],
	sheet_name: Optional[str] = None
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
			crypto_columns,
			sheet_name
		)
		
		logger.info(f"Получены значения криптовалют: {crypto_values}")
		
		# Формируем строки для раздела "Крипта"
		# Не добавляем заголовок, так как он уже есть в base_lines
		crypto_lines = []
		
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


async def _build_activity_stats(db):
	"""Строит сообщение со статистикой по активности (все пользователи)"""
	stats = await db.get_stats_summary()
	lines = [
		"<b>📊 Статистика пользователей</b>",
		f"<code>👥 Пользователи: {stats['total_users']:>4}</code>",
		f"<code>📤 Выдачи:      {stats['total_deliveries']:>4}</code>",
		"",
		"<b>🔥 По активности</b>",
	]
	
	# Получаем всех пользователей, исключая системного пользователя (tg_id = -1)
	per_user = stats.get("per_user") or []
	all_users = [u for u in per_user if u.get("tg_id") != -1]
	
	# Сортируем по активности (количество выдач, затем по последнему взаимодействию)
	all_users_sorted = sorted(
		all_users,
		key=lambda x: (
			-(x.get("delivery_count") or 0),
			-(x.get("last_interaction_at") or 0),
		),
	)
	
	if all_users_sorted:
		max_delivery = max((entry.get("delivery_count") or 0 for entry in all_users_sorted), default=1)
		for entry in all_users_sorted:
			if entry.get("full_name"):
				label = entry["full_name"]
			elif entry.get("username"):
				label = f"@{entry['username']}"
			elif entry.get("tg_id"):
				label = f"tg_id: {entry['tg_id']}"
			else:
				label = f"ID {entry.get('user_id', '?')}"
			count = entry.get("delivery_count") or 0
			last_relative = format_relative(entry.get("last_interaction_at"))
			bar = render_bar(count, max_delivery)
			lines.append(
				f"<code>{bar} {count:>3}</code> {escape(label)} <i>({last_relative})</i>"
			)
	else:
		lines.append("Нет данных по активности.")
	
	return "\n".join(lines)


async def _build_inactivity_stats(db):
	"""Строит сообщение со статистикой по давности (все пользователи)"""
	stats = await db.get_stats_summary()
	lines = [
		"<b>📊 Статистика пользователей</b>",
		f"<code>👥 Пользователи: {stats['total_users']:>4}</code>",
		f"<code>📤 Выдачи:      {stats['total_deliveries']:>4}</code>",
		"",
		"<b>🕒 По давности активности</b>",
	]
	
	# Получаем всех пользователей, исключая системного пользователя (tg_id = -1)
	per_user = stats.get("per_user") or []
	all_users = [u for u in per_user if u.get("tg_id") != -1]
	
	# Сортируем по давности (сначала те, у кого нет last_interaction_at, затем по возрастанию)
	all_users_sorted = sorted(
		all_users,
		key=lambda x: (x.get("last_interaction_at") or 0),
	)
	
	if all_users_sorted:
		now_ts = int(datetime.now().timestamp())
		inactivity_values = []
		for entry in all_users_sorted:
			ts = entry.get("last_interaction_at")
			if ts:
				inactivity_values.append(max(0, now_ts - ts))
			else:
				inactivity_values.append(0)
		max_inactivity = max(inactivity_values or [1])
		for idx, entry in enumerate(all_users_sorted):
			inactivity = inactivity_values[idx] if idx < len(inactivity_values) else 0
			if entry.get("full_name"):
				label = entry["full_name"]
			elif entry.get("username"):
				label = f"@{entry['username']}"
			elif entry.get("tg_id"):
				label = f"tg_id: {entry['tg_id']}"
			else:
				label = f"ID {entry.get('user_id', '?')}"
			last_relative = format_relative(entry.get("last_interaction_at"))
			bar = render_bar(inactivity, max_inactivity)
			count = entry.get("delivery_count") or 0
			lines.append(
				f"<code>{bar} {count:>3}</code> {escape(label)} <i>({last_relative})</i>"
			)
	else:
		lines.append("Нет данных по давности.")
	
	return "\n".join(lines)


@admin_router.message(Command("stat_u"))
async def admin_stats_command(msg: Message):
	"""Обработчик команды /stat_u - показывает меню выбора типа статистики"""
	db = get_db()
	stats = await db.get_stats_summary()
	text = (
		"<b>📊 Статистика пользователей</b>\n"
		f"<code>👥 Пользователи: {stats['total_users']:>4}</code>\n"
		f"<code>📤 Выдачи:      {stats['total_deliveries']:>4}</code>\n\n"
		"Выберите тип статистики:"
	)
	await msg.answer(text, reply_markup=stat_u_menu_kb(back_to="admin:back"), parse_mode="HTML")


@admin_router.callback_query(F.data == "stat_u:activity")
async def stat_u_activity(cb: CallbackQuery):
	"""Обработчик кнопки 'По активности'"""
	db = get_db()
	text = await _build_activity_stats(db)
	await cb.message.edit_text(text, reply_markup=stat_u_menu_kb(back_to="admin:back"), parse_mode="HTML")
	await cb.answer()


@admin_router.callback_query(F.data == "stat_u:inactivity")
async def stat_u_inactivity(cb: CallbackQuery):
	"""Обработчик кнопки 'По давности'"""
	db = get_db()
	text = await _build_inactivity_stats(db)
	await cb.message.edit_text(text, reply_markup=stat_u_menu_kb(back_to="admin:back"), parse_mode="HTML")
	await cb.answer()


@admin_router.callback_query(F.data == "stat_u:menu")
async def stat_u_menu(cb: CallbackQuery):
	"""Обработчик возврата в меню выбора типа статистики"""
	db = get_db()
	stats = await db.get_stats_summary()
	text = (
		"<b>📊 Статистика пользователей</b>\n"
		f"<code>👥 Пользователи: {stats['total_users']:>4}</code>\n"
		f"<code>📤 Выдачи:      {stats['total_deliveries']:>4}</code>\n\n"
		"Выберите тип статистики:"
	)
	await cb.message.edit_text(text, reply_markup=stat_u_menu_kb(back_to="admin:back"), parse_mode="HTML")
	await cb.answer()


@admin_router.message(Command("stat_bk"))
async def admin_stat_bk_command(msg: Message, bot: Bot):
	"""Обработчик команды /stat_bk для отображения балансов всех карт"""
	db = get_db()
	from app.config import get_settings
	from app.google_sheets import read_card_balances_batch
	
	settings = get_settings()
	
	if not settings.google_sheet_id or not settings.google_credentials_path:
		await msg.answer("❌ Google Sheets не настроен", reply_markup=simple_back_kb("admin:back"))
		return
	
	# Получаем balance_row из настроек
	balance_row_str = await db.get_google_sheets_setting("balance_row", "4")
	balance_row = int(balance_row_str) if balance_row_str else 4
	
	# Получаем все карты
	all_cards = await db.list_cards()
	
	if not all_cards:
		await msg.answer("❌ Карты не найдены", reply_markup=simple_back_kb("admin:back"))
		return
	
	# Сразу отправляем сообщение о загрузке
	loading_msg = await msg.answer("⏳ Загрузка балансов карт...", reply_markup=simple_back_kb("admin:back"))
	
	# Собираем информацию о картах и их столбцах
	cards_info = []  # [(card_id, card_name, column, cell_address)]
	cards_without_column = []
	cell_addresses = []
	
	for card_id, card_name, card_details in all_cards:
		# Получаем столбец для карты
		column = await db.get_card_column(card_id)
		
		if column:
			cell_address = f"{column}{balance_row}"
			cards_info.append((card_id, card_name, column, cell_address))
			cell_addresses.append(cell_address)
		else:
			cards_without_column.append(card_name)
	
	# Читаем все балансы одним batch запросом
	balances = {}
	if cell_addresses:
		try:
			balances = await read_card_balances_batch(
				settings.google_sheet_id,
				settings.google_credentials_path,
				cell_addresses,
				settings.google_sheet_name
			)
		except Exception as e:
			logger.exception(f"Ошибка batch чтения балансов: {e}")
	
	# Формируем результат
	lines = ["<b>💳 Балансы карт</b>"]
	cards_with_balance = []
	
	for card_id, card_name, column, cell_address in cards_info:
		balance = balances.get(cell_address)
		if balance:
			cards_with_balance.append((card_name, column, balance))
		else:
			cards_with_balance.append((card_name, column, "—"))
	
	# Добавляем карты с балансами
	if cards_with_balance:
		for card_name, column, balance in cards_with_balance:
			lines.append(f"<code>💳 {card_name} ({column}{balance_row}) = {balance}</code>")
	
	# Добавляем карты без привязки
	if cards_without_column:
		lines.append("")
		lines.append("<b>⚠️ Карты без привязки к столбцу:</b>")
		for card_name in cards_without_column:
			lines.append(f"<code>💳 {card_name}</code>")
	
	if not cards_with_balance and not cards_without_column:
		lines.append("Нет данных о картах.")
	
	text = "\n".join(lines)
	logger.info(f"📊 Отправка балансов карт: карт с балансом={len(cards_with_balance)}, без столбца={len(cards_without_column)}")
	try:
		await loading_msg.edit_text(text, reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")
		logger.info("✅ Сообщение с балансами карт успешно отправлено")
	except Exception as e:
		logger.exception(f"❌ Ошибка отправки сообщения с балансами карт: {e}")
		# Если не удалось обновить, отправляем новое сообщение
		try:
			await msg.answer(text, reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")
		except Exception as e2:
			logger.exception(f"❌ Ошибка отправки нового сообщения с балансами карт: {e2}")


@admin_router.message(Command("stat_k"))
async def admin_stat_k_command(msg: Message, bot: Bot):
	"""Обработчик команды /stat_k для отображения балансов крипты"""
	db = get_db()
	from app.config import get_settings
	from app.google_sheets import get_crypto_values_from_row_4
	
	settings = get_settings()
	crypto_columns = await db.list_crypto_columns()
	
	if not crypto_columns:
		await msg.answer("❌ Криптовалюты не настроены", reply_markup=simple_back_kb("admin:back"))
		return
	
	lines = ["<b>₿ Балансы криптовалют</b>"]
	
	if settings.google_sheet_id and settings.google_credentials_path:
		# Добавляем заглушки "Загрузка..." для каждой криптовалюты
		for crypto in crypto_columns:
			crypto_type = crypto.get("crypto_type", "")
			lines.append(f"<code>{crypto_type} = Загрузка...</code>")
		
		text = "\n".join(lines)
		sent_message = await msg.answer(text, reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")
		
		# Асинхронно загружаем значения криптовалют и обновляем сообщение
		# Передаем только заголовок в base_lines, без строк "Загрузка..."
		base_lines = ["<b>₿ Балансы криптовалют</b>"]
		asyncio.create_task(_update_crypto_values_in_stats(
			bot,
			sent_message.chat.id,
			sent_message.message_id,
			settings.google_sheet_id,
			settings.google_credentials_path,
			crypto_columns,
			base_lines,
			settings.google_sheet_name
		))
	else:
		lines.append("❌ Google Sheets не настроен")
		await msg.answer("\n".join(lines), reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")


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

# Handle any message and process forwarding logic for admins
# Важно: этот обработчик должен быть ПОСЛЕ обработчика editing_crypto_amount
# чтобы не перехватывать сообщения в состоянии редактирования
@admin_router.message()
async def handle_forwarded_from_admin(message: Message, bot: Bot, state: FSMContext):
	# Пропускаем команды - они обрабатываются отдельными обработчиками
	if message.text and message.text.startswith("/"):
		return
	
	# Проверяем текущее состояние FSM - если пользователь находится в процессе /add или других операциях,
	# не обрабатываем пересылки (они должны обрабатываться соответствующими обработчиками состояний)
	current_state = await state.get_state()
	if current_state:
		# Если есть активное состояние, проверяем, не является ли это состоянием для пересылок
		# Состояния ForwardBindStates - это состояния для обработки пересылок
		if current_state not in [ForwardBindStates.waiting_select_card.state, 
		                          ForwardBindStates.waiting_select_existing_card.state]:
			# Пользователь находится в другом состоянии (например, /add), пропускаем обработку пересылки
			logger.debug(f"⚠️ Пропуск обработки пересылки: пользователь находится в состоянии {current_state}")
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
	
	# Проверяем админа
	db = get_db()
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not message.from_user or not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		logger.info(f"❌ Сообщение {message.message_id} от не-админа или нет from_user, пропускаем")
		return
	
	# Обрабатываем только пересылки от админа
	if not is_forward:
		return
	
	# Обычная обработка пересылки
	orig_tg_id, orig_username, orig_full_name = extract_forward_profile(message)
	text = message.text or message.caption or ""
	logger.info(f"📨 Пересылка от админа {message.from_user.id}: tg_id={orig_tg_id}, username={orig_username}, full_name={orig_full_name}, text={text[:50] if text else 'нет'}")
	
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
					# Карт нет - показываем выбор группы карт для привязки
					logger.info(f"⚠️ У пользователя '{orig_full_name}' нет привязанных карт, предлагаем выбрать группу")
					groups = await db.list_card_groups()
					if groups:
						await state.set_state(ForwardBindStates.waiting_select_group)
						await state.update_data(hidden_user_name=orig_full_name, reply_only=False, existing_user_id=user_id)
						await message.answer(f"✅ Пользователь '{orig_full_name}' найден в БД, но не привязан к карте.\n\nВыберите группу карт:", reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
					else:
						rows = await db.list_cards()
						cards = [(r[0], r[1]) for r in rows]
						await state.set_state(ForwardBindStates.waiting_select_card)
						await state.update_data(hidden_user_name=orig_full_name, reply_only=False, existing_user_id=user_id)
						await message.answer(f"✅ Пользователь '{orig_full_name}' найден в БД, но не привязан к карте.\n\nГрупп пока нет. Выберите карту:", reply_markup=cards_select_kb(cards, back_to="admin:back"))
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
				card_id = card["card_id"]
				logger.info(f"🔍 Получение реквизитов для card_id={card_id}")
				requisites = await db.list_card_requisites(card_id)
				logger.info(f"📊 Получено реквизитов из БД: {len(requisites)} для card_id={card_id}")
				if requisites:
					for idx, req in enumerate(requisites, 1):
						logger.info(f"  Реквизит {idx}: id={req['id']}, text_preview={req['requisite_text'][:50]}...")
				
				# Проверяем наличие user_message
				user_msg = card.get("user_message")
				has_user_message = bool(user_msg and user_msg.strip())
				logger.info(f"📋 user_message для card_id={card_id}: has={has_user_message}, value={user_msg[:100] if user_msg else None}...")
				
				# Подсчитываем общее количество реквизитов (из таблицы + user_message если есть)
				total_requisites_count = len(requisites) + (1 if has_user_message else 0)
				logger.info(f"📊 Общее количество реквизитов для card_id={card_id}: {total_requisites_count} (из таблицы: {len(requisites)}, user_message: {1 if has_user_message else 0})")
				
				await db.log_card_delivery_by_tg(
					orig_tg_id,
					card_id,
					admin_id=message.from_user.id if message.from_user else None,
				)
				
				# Отправляем все реквизиты админу (из таблицы + user_message если есть)
				logger.info(f"🚀 Вызов send_card_requisites_to_admin для card_id={card_id}, admin_chat_id={message.chat.id}")
				try:
					admin_chat_id = message.chat.id
					sent_count = await send_card_requisites_to_admin(bot, admin_chat_id, card_id, db)
					logger.info(f"✅ send_card_requisites_to_admin завершена для card_id={card_id}, отправлено: {sent_count}")
				except Exception as e:
					logger.exception(f"❌ КРИТИЧЕСКАЯ ОШИБКА в send_card_requisites_to_admin: {e}")
				return
			buttons = [(card["card_id"], card["card_name"]) for card in cards_for_user]
			await state.set_state(ForwardBindStates.waiting_select_existing_card)
			await state.update_data(original_tg_id=orig_tg_id)
			await message.answer(
				"У пользователя привязано несколько карт. Выберите нужную:",
				reply_markup=user_cards_reply_kb(buttons, orig_tg_id, back_to="admin:back"),
			)
			return
		logger.info(f"⚠️ Пользователь {orig_tg_id} не привязан к карте, предлагаем выбрать группу карт")
		groups = await db.list_card_groups()
		if groups:
			await state.set_state(ForwardBindStates.waiting_select_group)
			await state.update_data(original_tg_id=orig_tg_id)
			await message.answer("Пользователь не привязан. Выберите группу карт:", reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
		else:
			# Если групп нет, показываем все карты
			rows = await db.list_cards()
			cards = [(r[0], r[1]) for r in rows]
			await state.set_state(ForwardBindStates.waiting_select_card)
			await state.update_data(original_tg_id=orig_tg_id)
			await message.answer("Пользователь не привязан. Групп пока нет. Выберите карту:", reply_markup=cards_select_kb(cards, back_to="admin:back"))
		return
	# Если не удалось найти пользователя, но есть username или full_name - возможно пользователь еще не в БД или все скрыто
	if orig_tg_id is None:
		# Проверяем, есть ли хотя бы username
		if orig_username:
			# Пользователь не найден в БД, но есть username - возможно первый раз
			logger.warning(f"Не удалось определить ID пользователя, но есть username={orig_username}. Возможные причины: пользователь скрыл данные в настройках приватности Telegram или еще не взаимодействовал с ботом.")
			groups = await db.list_card_groups()
			if groups:
				await state.set_state(ForwardBindStates.waiting_select_group)
				await state.update_data(reply_only=True)
				warning_msg = f"⚠️ Не удалось получить ID пользователя @{orig_username}.\n\nВозможные причины:\n• Пользователь скрыл данные в настройках приватности Telegram\n• Пользователь еще не взаимодействовал с ботом\n\nВыберите группу карт:"
				await message.answer(warning_msg, reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
			else:
				rows = await db.list_cards()
				cards = [(r[0], r[1]) for r in rows]
				await state.set_state(ForwardBindStates.waiting_select_card)
				await state.update_data(reply_only=True)
				warning_msg = f"⚠️ Не удалось получить ID пользователя @{orig_username}.\n\nВозможные причины:\n• Пользователь скрыл данные в настройках приватности Telegram\n• Пользователь еще не взаимодействовал с ботом\n\nГрупп пока нет. Выберите карту:"
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
				groups = await db.list_card_groups()
				if groups:
					await state.set_state(ForwardBindStates.waiting_select_group)
					# Сохраняем имя скрытого пользователя в state, чтобы при выборе карты создать запись
					await state.update_data(hidden_user_name=orig_full_name, reply_only=False)
					warning_msg = f"⚠️ Пользователь '{orig_full_name}' полностью скрыл информацию (MessageOriginHiddenUser).\n\nID и username недоступны. Похожие пользователи в БД не найдены.\n\n💡 Система запомнит выбор карты для этого имени.\nКогда пользователь '{orig_full_name}' напишет боту, карта будет автоматически привязана.\n\nВыберите группу карт:"
					await message.answer(warning_msg, reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
				else:
					rows = await db.list_cards()
					cards = [(r[0], r[1]) for r in rows]
					await state.set_state(ForwardBindStates.waiting_select_card)
					await state.update_data(hidden_user_name=orig_full_name, reply_only=False)
					warning_msg = f"⚠️ Пользователь '{orig_full_name}' полностью скрыл информацию (MessageOriginHiddenUser).\n\nID и username недоступны. Похожие пользователи в БД не найдены.\n\n💡 Система запомнит выбор карты для этого имени.\nКогда пользователь '{orig_full_name}' напишет боту, карта будет автоматически привязана.\n\nГрупп пока нет. Выберите карту:"
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
	# as last resort: show groups to reply-only
	groups = await db.list_card_groups()
	if groups:
		await state.set_state(ForwardBindStates.waiting_select_group)
		await state.update_data(reply_only=True)
		warning_msg = "⚠️ Не удалось определить пользователя из пересылки.\n\nВозможные причины:\n• Пользователь скрыл все данные в настройках приватности Telegram\n• Сообщение не переслано\n\nВыберите группу карт:"
		await message.answer(warning_msg, reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
	else:
		rows = await db.list_cards()
		cards = [(r[0], r[1]) for r in rows]
		await state.set_state(ForwardBindStates.waiting_select_card)
		await state.update_data(reply_only=True)
		warning_msg = "⚠️ Не удалось определить пользователя из пересылки.\n\nВозможные причины:\n• Пользователь скрыл все данные в настройках приватности Telegram\n• Сообщение не переслано\n\nГрупп пока нет. Выберите карту:"
		await message.answer(warning_msg, reply_markup=cards_select_kb(cards, back_to="admin:back"))


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
			card_id = card["card_id"]
			requisites = await db.list_card_requisites(card_id)
			user_msg = card.get("user_message")
			has_user_message = bool(user_msg)
			
			# Подсчитываем общее количество реквизитов (из таблицы + user_message если есть)
			total_requisites_count = len(requisites) + (1 if has_user_message else 0)
			
			await state.clear()
			await db.log_card_delivery_by_tg(tg_id, card_id, admin_id=cb.from_user.id if cb.from_user else None)
			
			# Отправляем все реквизиты админу (из таблицы + user_message если есть)
			sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db)
		else:
			# Несколько карт - выбираем
			buttons = [(card["card_id"], card["card_name"]) for card in cards_for_user]
			await state.set_state(ForwardBindStates.waiting_select_existing_card)
			text = f"✅ Выбран: {user.get('full_name', 'Без имени')}\n\nУ пользователя привязано несколько карт. Выберите нужную:"
			await cb.message.edit_text(text, reply_markup=user_cards_reply_kb(buttons, tg_id, back_to="admin:back"))
	else:
		# Не привязан - выбираем группу карт для привязки
		groups = await db.list_card_groups()
		if groups:
			await state.set_state(ForwardBindStates.waiting_select_group)
			text = f"✅ Выбран: {user.get('full_name', 'Без имени')}\n\nПользователь не привязан. Выберите группу карт:"
			await cb.message.edit_text(text, reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
		else:
			rows = await db.list_cards()
			cards = [(r[0], r[1]) for r in rows]
			await state.set_state(ForwardBindStates.waiting_select_card)
			text = f"✅ Выбран: {user.get('full_name', 'Без имени')}\n\nПользователь не привязан. Групп пока нет. Выберите карту:"
			await cb.message.edit_text(text, reply_markup=cards_select_kb(cards, back_to="admin:back"))
	
	await cb.answer()


@admin_router.callback_query(ForwardBindStates.waiting_select_card, F.data == "hidden:no_match")
async def hidden_user_no_match(cb: CallbackQuery, state: FSMContext):
	"""Обработчик когда пользователь не найден в списке похожих"""
	data = await state.get_data()
	hidden_name = data.get("hidden_user_name", "Неизвестный")
	
	logger.info(f"❌ Пользователь '{hidden_name}' не найден в списке похожих")
	
	# Показываем список групп карт для ответа администратору
	db = get_db()
	groups = await db.list_card_groups()
	if groups:
		await state.set_state(ForwardBindStates.waiting_select_group)
		await state.update_data(reply_only=True)
		text = f"⚠️ Пользователь '{hidden_name}' не найден в базе данных.\n\nДля работы с этим пользователем попросите его написать боту хотя бы один раз.\n\nВыберите группу карт:"
		await cb.message.edit_text(text, reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
	else:
		rows = await db.list_cards()
		cards = [(r[0], r[1]) for r in rows]
		await state.set_state(ForwardBindStates.waiting_select_card)
		await state.update_data(reply_only=True)
		text = f"⚠️ Пользователь '{hidden_name}' не найден в базе данных.\n\nДля работы с этим пользователем попросите его написать боту хотя бы один раз.\n\nГрупп пока нет. Выберите карту:"
		await cb.message.edit_text(text, reply_markup=cards_select_kb(cards, back_to="admin:back"))
	await cb.answer()


@admin_router.callback_query(ForwardBindStates.waiting_select_group, F.data.startswith("forward:group:"))
async def forward_select_group(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора группы карт при пересылке"""
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
		
		if cards_list:
			await state.set_state(ForwardBindStates.waiting_select_card)
			await state.update_data(selected_group_id=group_id)
			await cb.message.edit_text(text, reply_markup=cards_select_kb(cards_list, back_to="admin:back"))
		else:
			await cb.answer(f"В группе '{group_name}' нет карт", show_alert=True)
	else:
		# Получаем карты без группы
		cards = await db.get_cards_without_group()
		text = "Карты вне групп:" if cards else "Нет карт вне групп."
		
		# Преобразуем формат карт из (id, name, details) в (id, name)
		cards_list = [(c[0], c[1]) for c in cards]
		
		if cards_list:
			await state.set_state(ForwardBindStates.waiting_select_card)
			await state.update_data(selected_group_id=None)
			await cb.message.edit_text(text, reply_markup=cards_select_kb(cards_list, back_to="admin:back"))
		else:
			await cb.answer("Нет карт вне групп", show_alert=True)
	
	await cb.answer()


@admin_router.callback_query(ForwardBindStates.waiting_select_card, F.data.startswith("select:card:"))
async def forward_select_card(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик выбора карты при пересылке"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	
	data = await state.get_data()
	original_tg_id = data.get("original_tg_id")
	hidden_user_name = data.get("hidden_user_name")
	reply_only = data.get("reply_only", False)
	existing_user_id = data.get("existing_user_id")
	
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	# Если это только ответ администратору (reply_only), отправляем реквизиты и завершаем
	if reply_only:
		requisites = await db.list_card_requisites(card_id)
		user_msg = card.get("user_message")
		has_user_message = bool(user_msg)
		
		await state.clear()
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db)
		await cb.answer()
		return
	
	# Обрабатываем привязку карты к пользователю
	if original_tg_id:
		# Обычный пользователь с tg_id
		user_id = await db.get_user_id_by_tg(original_tg_id)
		if not user_id:
			await cb.answer("Пользователь не найден", show_alert=True)
			return
		
		# Привязываем карту к пользователю
		await db.bind_user_to_card(user_id, card_id)
		await db.touch_user_by_tg(original_tg_id)
		logger.info(f"✅ Карта {card_id} привязана к пользователю tg_id={original_tg_id}")
		
		# Получаем реквизиты карты
		requisites = await db.list_card_requisites(card_id)
		user_msg = card.get("user_message")
		has_user_message = bool(user_msg)
		
		# Логируем доставку
		await db.log_card_delivery_by_tg(
			original_tg_id,
			card_id,
			admin_id=cb.from_user.id if cb.from_user else None,
		)
		
		# Отправляем все реквизиты админу
		await state.clear()
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db)
		
	elif hidden_user_name:
		# Скрытый пользователь (MessageOriginHiddenUser)
		if existing_user_id:
			# Пользователь уже существует в БД
			user_id = existing_user_id
			await db.bind_user_to_card(user_id, card_id)
			await db.touch_user(user_id)
			logger.info(f"✅ Карта {card_id} привязана к скрытому пользователю '{hidden_user_name}' (user_id={user_id})")
		else:
			# Ищем существующего скрытого пользователя или создаем нового
			# Используем find_similar_users_by_name для поиска
			similar_users = await db.find_similar_users_by_name(hidden_user_name, limit=1)
			if similar_users and similar_users[0].get("tg_id") is None:
				# Найден скрытый пользователь
				user_id = similar_users[0]["id"]
				await db.bind_user_to_card(user_id, card_id)
				await db.touch_user(user_id)
				logger.info(f"✅ Карта {card_id} привязана к существующему скрытому пользователю '{hidden_user_name}' (user_id={user_id})")
			else:
				# Создаем нового скрытого пользователя напрямую через SQL
				import time
				cur = await db._db.execute(
					"INSERT INTO users(tg_id, username, full_name, last_interaction_at) VALUES(?, ?, ?, ?)",
					(None, None, hidden_user_name, int(time.time())),
				)
				await db._db.commit()
				user_id = cur.lastrowid
				await db.bind_user_to_card(user_id, card_id)
				logger.info(f"✅ Создан новый скрытый пользователь '{hidden_user_name}' (user_id={user_id}) и привязана карта {card_id}")
		
		# Логируем доставку для скрытого пользователя
		await db.log_card_delivery(
			user_id,
			card_id,
			admin_id=cb.from_user.id if cb.from_user else None,
		)
		logger.info(f"✅ Логирование доставки для скрытого пользователя '{hidden_user_name}' (user_id={user_id}, card_id={card_id})")
		
		# Отправляем все реквизиты админу (даже для скрытого пользователя)
		await state.clear()
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db)
		logger.info(f"✅ Отправлено {sent_count} сообщений с реквизитами админу для скрытого пользователя '{hidden_user_name}'")
	
	await cb.answer()


@admin_router.callback_query(F.data.startswith("user:reply:card:"))
async def forward_existing_card_reply(cb: CallbackQuery, state: FSMContext, bot: Bot):
	logger.info(f"🔔 Обработчик forward_existing_card_reply вызван: callback_data={cb.data}")
	current_state = await state.get_state()
	logger.info(f"🔔 Текущее состояние: {current_state}")
	
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) < 5:
		logger.error(f"❌ Неверный формат callback_data: {cb.data}")
		await cb.answer("Ошибка: неверный формат данных", show_alert=True)
		return
	
	user_tg_id_val = parts[3]
	card_id = int(parts[4])
	logger.info(f"🔔 Парсинг callback: user_tg_id_val={user_tg_id_val}, card_id={card_id}")
	
	data = await state.get_data()
	user_id_for_hidden = data.get("user_id_for_hidden")
	hidden_user_name = data.get("hidden_user_name")
	logger.info(f"🔔 Данные состояния: user_id_for_hidden={user_id_for_hidden}, hidden_user_name={hidden_user_name}")
	
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
	
	# Получаем реквизиты карты
	requisites = await db.list_card_requisites(card_id)
	user_msg = card.get("user_message")
	has_user_message = bool(user_msg)
	
	# Подсчитываем общее количество реквизитов (из таблицы + user_message если есть)
	total_requisites_count = len(requisites) + (1 if has_user_message else 0)
	
	# Логируем доставку
	if user_tg_id:
		await db.log_card_delivery_by_tg(
		user_tg_id,
		card_id,
		admin_id=cb.from_user.id if cb.from_user else None,
	)
		# Отправляем все реквизиты админу (из таблицы + user_message если есть)
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db)
	elif user_id_for_hidden:
		# Логируем для скрытого пользователя через user_id
		await db.log_card_delivery(
			user_id_for_hidden,
			card_id,
			admin_id=cb.from_user.id if cb.from_user else None,
		)
		logger.info(f"✅ Логирование доставки для скрытого пользователя '{hidden_user_name}' (user_id={user_id_for_hidden}, card_id={card_id})")
		# Отправляем все реквизиты админу (из таблицы + user_message если есть)
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db)
		logger.info(f"✅ Отправлено {sent_count} реквизитов админу для скрытого пользователя")
	
	await cb.answer()
