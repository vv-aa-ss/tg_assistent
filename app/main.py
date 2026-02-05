import asyncio
import logging
import os
import re
import time
import glob
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from html import escape
from aiogram.types import Message, ReplyKeyboardRemove, CallbackQuery, ForceReply, FSInputFile, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
from app.keyboards import admin_menu_kb, client_menu_kb, buy_country_kb, buy_country_inline_kb, buy_crypto_kb, buy_crypto_inline_kb, buy_deal_confirm_kb, buy_deal_paid_kb, buy_deal_paid_reply_kb, buy_delivery_method_kb, buy_payment_confirmed_kb, order_action_kb, user_access_request_kb, sell_crypto_kb, sell_confirmation_kb, sell_order_user_reply_kb, question_user_reply_kb, question_reply_kb, order_user_reply_kb
from app.di import get_admin_ids, get_admin_usernames
from app.di import set_dependencies
from app.notifications import notification_ids


# Глобальный словарь для хранения message_id сообщений о крупных заявках и question_id для переписки
# Формат: {user_tg_id: {"message_ids": {admin_id: message_id}, "question_id": question_id}}
# Будет инициализирован в main()
large_order_alerts: dict[int, dict] = {}

# Глобальный словарь для хранения message_id алертов по сделкам без реквизитов
# Формат: {deal_id: {admin_id: message_id}}
buy_deal_alerts: dict[int, dict[int, int]] = {}

# Глобальный словарь для хранения message_id уведомлений о получении скриншота
# Формат: {deal_id: {admin_id: message_id}}
proof_notification_ids: dict[int, dict[int, int]] = {}

# Лимиты для глобальных словарей (защита от переполнения памяти)
MAX_LARGE_ORDER_ALERTS = 1000  # Максимум 1000 активных крупных заявок
MAX_BUY_DEAL_ALERTS = 5000  # Максимум 5000 активных сделок


def limit_dict_size(dictionary: dict, max_size: int, dict_name: str) -> None:
	"""Ограничивает размер словаря, удаляя старые записи"""
	if len(dictionary) > max_size:
		logger_main = logging.getLogger("app.main")
		logger_main.warning(f"⚠️ {dict_name} превысил лимит {max_size}, очищаем старые записи")
		# Удаляем 20% самых старых записей
		to_remove = int(max_size * 0.2)
		keys_to_remove = list(dictionary.keys())[:to_remove]
		for key in keys_to_remove:
			del dictionary[key]
		logger_main.info(f"✅ Удалено {to_remove} старых записей из {dict_name}")


async def cleanup_deal_alerts(deal_id: int) -> None:
	"""Удаляет записи о завершенной сделке из глобальных словарей и БД"""
	global buy_deal_alerts, large_order_alerts
	
	from app.di import get_db
	db = get_db()
	logger_main = logging.getLogger("app.main")
	
	# Удаляем из глобальных словарей
	if deal_id in buy_deal_alerts:
		# Удаляем из БД перед удалением из памяти
		for admin_id in buy_deal_alerts[deal_id].keys():
			try:
				await db.delete_deal_alert(deal_id, admin_id, "buy_deal")
			except Exception as e:
				logger_main.warning(f"⚠️ Ошибка при удалении deal alert из БД: {e}")
		del buy_deal_alerts[deal_id]
		logger_main.debug(f"🧹 Удалена запись deal_id={deal_id} из buy_deal_alerts")
	
	# Также удаляем из large_order_alerts, если есть
	deal = await db.get_buy_deal_by_id(deal_id)
	if deal:
		user_tg_id = deal.get("user_tg_id")
		if user_tg_id in large_order_alerts:
			# Проверяем, есть ли активные сделки у этого пользователя
			active_deal = await db.get_active_buy_deal_by_user(user_tg_id)
			if not active_deal or active_deal == deal_id:
				# Если нет активных сделок или это была последняя, удаляем
				del large_order_alerts[user_tg_id]
				logger_main.debug(f"🧹 Удалена запись user_tg_id={user_tg_id} из large_order_alerts")


async def save_deal_alert_to_db(deal_id: int, admin_id: int, message_id: int) -> None:
	"""Сохраняет deal alert в БД"""
	from app.di import get_db
	db = get_db()
	try:
		await db.save_deal_alert(deal_id, admin_id, message_id, "buy_deal")
	except Exception as e:
		logger_main = logging.getLogger("app.main")
		logger_main.warning(f"⚠️ Ошибка при сохранении deal alert в БД: {e}")


async def load_deal_alerts_from_db() -> None:
	"""Загружает deal alerts из БД при старте бота"""
	global buy_deal_alerts
	from app.di import get_db
	db = get_db()
	logger_main = logging.getLogger("app.main")
	
	try:
		alerts = await db.get_deal_alerts(alert_type="buy_deal")
		logger_main.info(f"📥 Загрузка {len(alerts)} deal alerts из БД")
		
		for alert in alerts:
			deal_id = alert["deal_id"]
			admin_id = alert["admin_id"]
			message_id = alert["message_id"]
			
			if deal_id not in buy_deal_alerts:
				buy_deal_alerts[deal_id] = {}
			buy_deal_alerts[deal_id][admin_id] = message_id
		
		logger_main.info(f"✅ Загружено {len(buy_deal_alerts)} активных deal alerts")
	except Exception as e:
		logger_main.error(f"❌ Ошибка при загрузке deal alerts из БД: {e}", exc_info=True)


async def periodic_cleanup_alerts():
	"""Периодически очищает старые записи из глобальных словарей"""
	from app.di import get_db
	logger_main = logging.getLogger("app.main")
	
	while True:
		await asyncio.sleep(3600)  # Каждый час
		try:
			# Ограничиваем размер словарей
			limit_dict_size(large_order_alerts, MAX_LARGE_ORDER_ALERTS, "large_order_alerts")
			limit_dict_size(buy_deal_alerts, MAX_BUY_DEAL_ALERTS, "buy_deal_alerts")
			
			# Удаляем записи для завершенных сделок
			db = get_db()
			# Получаем список активных deal_id (статус не "completed")
			active_deals = await db.get_active_buy_deals()
			active_deal_ids = {deal["id"] for deal in active_deals}
			inactive_deal_ids = set(buy_deal_alerts.keys()) - active_deal_ids
			for deal_id in inactive_deal_ids:
				await cleanup_deal_alerts(deal_id)
			
			logger_main.info(f"🧹 Периодическая очистка: удалено {len(inactive_deal_ids)} неактивных deal alerts")
			
			logger_main.debug("🧹 Периодическая очистка глобальных словарей завершена")
		except Exception as e:
			logger_main.error(f"❌ Ошибка при очистке глобальных словарей: {e}", exc_info=True)


def is_not_admin_message(message: Message) -> bool:
	"""Фильтр: пропускаем только сообщения от НЕ админов."""
	if not message.from_user:
		return False
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	return not is_admin(
		message.from_user.id,
		message.from_user.username,
		admin_ids,
		admin_usernames
	)


def get_user_stage_name(state: str) -> str:
	"""Определяет название этапа пользователя на основе его FSM состояния"""
	if not state:
		return "Неизвестно"
	
	if "waiting_confirmation" in state:
		return "Согласование цены"
	elif "waiting_amount" in state:
		return "Ввод суммы"
	elif "selecting_crypto" in state:
		return "Выбор монеты"
	elif "selecting_country" in state:
		return "Выбор страны"
	elif "waiting_wallet_address" in state:
		return "Ввод кошелька"
	elif "waiting_admin" in state:
		return "Согласование цены"
	elif "waiting_delivery_method" in state:
		return "Выбор реквизитов"
	elif "waiting_payment_confirmation" in state:
		return "Оплата"
	elif "waiting_payment_proof" in state:
		return "Оплата"
	else:
		return "Неизвестно"


async def update_large_order_alert(
	bot: Bot,
	user_tg_id: int,
	user_name: str,
	user_username: str,
	total_usd: float,
	crypto_display: str,
	amount: float,
	stage_name: str,
	admin_ids: list[int],
	state_amount_currency: float | None = None,
	state_currency_symbol: str | None = None,
	current_state: str | None = None,
	country_code: str | None = None
) -> None:
	"""Обновляет сообщение о крупной заявке с текущим этапом пользователя"""
	global large_order_alerts
	logger_main = logging.getLogger("app.main")
	
	logger_main.info(f"🔍 update_large_order_alert вызвана: user_tg_id={user_tg_id}, stage_name={stage_name}")
	logger_main.info(f"🔍 large_order_alerts содержит: {list(large_order_alerts.keys())}")
	
	if user_tg_id not in large_order_alerts:
		logger_main.warning(f"❌ Пользователь {user_tg_id} не найден в large_order_alerts. Доступные: {list(large_order_alerts.keys())}")
		return
	
	user_alerts_data = large_order_alerts[user_tg_id]
	# Поддерживаем обратную совместимость со старой структурой
	if isinstance(user_alerts_data, dict) and "message_ids" in user_alerts_data:
		user_alerts = user_alerts_data["message_ids"]
		question_id = user_alerts_data.get("question_id")
	else:
		# Старая структура: {admin_id: message_id}
		user_alerts = user_alerts_data
		question_id = None
	
	logger_main.info(f"🔍 Данные для пользователя {user_tg_id}: {user_alerts}")
	logger_main.info(f"🔍 Админы для обновления: {admin_ids}")
	
	# Пытаемся получить данные из БД, если заявка уже создана
	from app.di import get_db
	db = get_db()
	order_id = await db.get_active_order_by_user(user_tg_id)
	amount_currency = None
	currency_symbol = None

	pre_order_states = {
		"BuyStates:waiting_confirmation",
		"BuyStates:waiting_wallet_address",
		"BuyStates:waiting_delivery_method",
		"BuyStates:waiting_payment_confirmation",
		"BuyStates:waiting_payment_proof",
	}

	# Если пользователь на ранних этапах, берем сумму из FSM-состояния
	if current_state in pre_order_states and state_amount_currency is not None:
		amount_currency = state_amount_currency
		currency_symbol = state_currency_symbol or "₽"
	elif order_id:
		order = await db.get_order_by_id(order_id)
		if order:
			amount_currency = order.get("amount_currency", 0)
			currency_symbol = order.get("currency_symbol", "₽")
	
	# Получаем историю переписки, если есть question_id
	history_text = ""
	if question_id:
		messages = await db.get_question_messages(question_id)
		if messages:
			history_lines = []
			for msg in messages:
				if msg["sender_type"] == "admin":
					history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
				else:
					history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
			history_text = "\n\n".join(history_lines)
	if not history_text:
		try:
			deal_id = await db.get_active_buy_deal_by_user(user_tg_id)
			if deal_id:
				deal_messages = await db.get_buy_deal_messages(deal_id)
				if deal_messages:
					history_text = "\n".join(
						_build_deal_chat_lines(deal_messages, user_name or "Пользователь")
					)
		except Exception:
			pass
	
	# Формируем текст сообщения с этапом
	country_label = _deal_country_label(country_code or "BYN")
	if amount_currency is not None and currency_symbol:
		# Если заявка создана, показываем сумму в валюте заявки
		amount_str = f"{amount:.8f}".rstrip('0').rstrip('.') if amount < 1 else f"{amount:.2f}".rstrip('0').rstrip('.')
		alert_text = (
			f"🚨 <b>Крупная заявка</b>\n\n"
			f"Пользователь: {user_name or 'Не указано'} (@{user_username or 'нет'})\n"
			f"Страна: {country_label}\n"
			f"Сумма: {int(amount_currency)} {currency_symbol}\n"
			f"Крипта: {crypto_display}\n"
			f"Кол-во: {amount_str} {crypto_display}\n\n"
			f"📍 <b>Этап:</b> {stage_name}"
		)
	else:
		# Если заявка еще не создана, показываем сумму в USD
		alert_text = (
			f"🚨 <b>Крупная заявка</b>\n\n"
			f"Пользователь: {user_name or 'Не указано'} (@{user_username or 'нет'})\n"
			f"Страна: {country_label}\n"
			f"Сумма: {total_usd:.2f}$\n"
			f"Крипта: {crypto_display}\n"
			f"Кол-во: {amount}\n\n"
			f"📍 <b>Этап:</b> {stage_name}"
		)
	
	# Добавляем историю переписки, если есть
	if history_text:
		alert_text += f"\n\n💬 <b>Переписка:</b>\n\n{history_text}"
	
	logger_main.info(f"📝 Текст сообщения для обновления:\n{alert_text}")
	
	from aiogram.utils.keyboard import InlineKeyboardBuilder
	kb = InlineKeyboardBuilder()
	kb.button(text="💬 Написать", callback_data=f"alert:message:{user_tg_id}")
	kb.button(text="💳 Реквизиты", callback_data=f"alert:requisites:{user_tg_id}")
	kb.button(text="💰 Сумма", callback_data=f"alert:amount:{user_tg_id}")
	kb.button(text="🪙 Монеты", callback_data=f"alert:crypto:{user_tg_id}")
	kb.adjust(2, 2)
	
	# Обновляем сообщения для всех админов
	logger_main.info(f"🔄 Попытка обновить сообщение для пользователя {user_tg_id}, админов: {list(user_alerts.keys())}")
	
	updated_count = 0
	for admin_id, message_id in user_alerts.items():
		logger_main.info(f"🔍 Проверка админа {admin_id}: message_id={message_id}, admin_id в списке админов: {admin_id in admin_ids}")
		if admin_id in admin_ids:
			try:
				logger_main.info(f"📝 Обновление сообщения админа {admin_id}, message_id={message_id}, этап={stage_name}")
				result = await bot.edit_message_text(
					chat_id=admin_id,
					message_id=message_id,
					text=alert_text,
					parse_mode=ParseMode.HTML,
					reply_markup=kb.as_markup()
				)
				logger_main.info(f"✅ Сообщение успешно обновлено для админа {admin_id}, message_id={message_id}, результат: {result}")
				updated_count += 1
			except Exception as e:
				logger_main.error(
					f"❌ ОШИБКА при обновлении сообщения для админа {admin_id}, message_id={message_id}: {type(e).__name__}: {e}",
					exc_info=True
				)
		else:
			logger_main.warning(f"⚠️ Админ {admin_id} не в списке админов для обновления. Список админов: {admin_ids}")
	
	logger_main.info(f"📊 Итого обновлено сообщений: {updated_count} из {len(user_alerts)}")


async def try_update_large_order_alert(
	bot: Bot,
	state: FSMContext,
	user_tg_id: int,
	user_name: str,
	user_username: str
) -> None:
	"""Пытается обновить сообщение о крупной заявке, если она активна"""
	global large_order_alerts
	logger_main = logging.getLogger("app.main")
	
	logger_main.info(f"🔍 try_update_large_order_alert вызвана для user_tg_id={user_tg_id}")
	logger_main.info(f"🔍 large_order_alerts содержит ключи: {list(large_order_alerts.keys())}")
	
	if user_tg_id not in large_order_alerts:
		logger_main.warning(f"❌ Пользователь {user_tg_id} не найден в large_order_alerts. Доступные ключи: {list(large_order_alerts.keys())}")
		return
	
	logger_main.info(f"✅ Пользователь {user_tg_id} найден в large_order_alerts: {large_order_alerts[user_tg_id]}")
	
	# Получаем данные из состояния
	data = await state.get_data()
	logger_main.info(f"🔍 Данные из состояния: keys={list(data.keys())}")
	
	total_usd = data.get("total_usd", 0)
	alert_threshold = data.get("alert_threshold", 400.0)
	crypto_display = data.get("crypto_display", "")
	amount = data.get("amount", 0)
	state_amount_currency = data.get("final_amount", data.get("amount_currency"))
	state_currency_symbol = data.get("currency_symbol")
	country_code = data.get("selected_country", "BYN")
	
	logger_main.info(f"🔄 Обновление этапа для пользователя {user_tg_id}: total_usd={total_usd}, threshold={alert_threshold}")
	
	# Проверяем, является ли это крупной заявкой
	if total_usd < alert_threshold:
		logger_main.warning(f"⚠️ Заявка не является крупной: {total_usd} < {alert_threshold}")
		return
	
	# Получаем текущий этап
	current_state = await state.get_state()
	stage_name = get_user_stage_name(str(current_state) if current_state else "")
	
	logger_main.info(f"📍 Текущий этап пользователя {user_tg_id}: {stage_name} (состояние: {current_state})")
	
	# Обновляем сообщение
	admin_ids = get_admin_ids()
	logger_main.info(f"🔍 Список админов для обновления: {admin_ids}")
	
	await update_large_order_alert(
		bot=bot,
		user_tg_id=user_tg_id,
		user_name=user_name,
		user_username=user_username,
		total_usd=total_usd,
		crypto_display=crypto_display,
		amount=amount,
		stage_name=stage_name,
		admin_ids=admin_ids,
		state_amount_currency=state_amount_currency,
		state_currency_symbol=state_currency_symbol,
		current_state=str(current_state) if current_state else None,
		country_code=country_code
	)


class BuyStates(StatesGroup):
	"""Состояния для процесса покупки криптовалюты"""
	waiting_crypto_amount = State()  # Ожидание ввода суммы
	waiting_confirmation = State()  # Ожидание подтверждения сделки
	waiting_wallet_address = State()  # Ожидание ввода адреса кошелька
	waiting_delivery_method = State()  # Ожидание выбора способа доставки
	waiting_payment_confirmation = State()  # Ожидание подтверждения оплаты
	waiting_payment_proof = State()  # Ожидание скриншота/чека оплаты


class DealStates(StatesGroup):
	"""Состояния для нового окна сделки на покупку"""
	selecting_country = State()
	selecting_crypto = State()
	waiting_amount = State()
	waiting_confirmation = State()
	waiting_wallet_address = State()
	waiting_admin = State()
	waiting_payment = State()
	waiting_payment_proof = State()


class DealUserReplyStates(StatesGroup):
	"""Состояние для ответа пользователя в чате сделки"""
	waiting_reply = State()


class QuestionStates(StatesGroup):
	"""Состояния для вопроса пользователя"""
	waiting_question = State()  # Ожидание ввода вопроса


class SellStates(StatesGroup):
	"""Состояния для процесса продажи криптовалюты"""
	selecting_crypto = State()  # Выбор криптовалюты
	waiting_amount = State()  # Ожидание ввода суммы
	waiting_confirmation = State()  # Ожидание подтверждения сделки


class SellOrderUserReplyStates(StatesGroup):
	"""Состояния для ответа пользователя на сообщение админа по сделке"""
	waiting_reply = State()  # Ожидание ввода ответа пользователя

class OrderUserReplyStates(StatesGroup):
	"""Состояния для ответа пользователя на сообщение админа по обычной заявке"""
	waiting_reply = State()  # Ожидание ввода ответа пользователя

class QuestionUserReplyStates(StatesGroup):
	"""Состояния для ответа пользователя на вопрос админа"""
	waiting_reply = State()  # Ожидание ввода ответа пользователя


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


async def delete_message_after_delay(bot: Bot, chat_id: int, message_id: int, delay: float = 15.0):
	"""
	Удаляет сообщение через указанную задержку, игнорируя ошибки.
	"""
	async def delayed_delete():
		await asyncio.sleep(delay)
		try:
			await bot.delete_message(chat_id=chat_id, message_id=message_id)
		except Exception:
			pass
	asyncio.create_task(delayed_delete())


async def send_temporary_notification(bot: Bot, chat_id: int, text: str, duration: float = 2.0):
	"""
	Отправляет временное уведомление, которое автоматически удаляется через указанное время.
	Создает эффект всплывающего уведомления.
	
	Args:
		bot: Экземпляр бота
		chat_id: ID чата
		text: Текст уведомления
		duration: Время отображения в секундах (по умолчанию 2 секунды)
	"""
	try:
		notification = await bot.send_message(chat_id=chat_id, text=text)
		
		# Удаляем уведомление через указанное время
		async def delayed_delete():
			await asyncio.sleep(duration)
			try:
				await bot.delete_message(chat_id=chat_id, message_id=notification.message_id)
			except Exception:
				pass  # Игнорируем ошибки удаления
		
		asyncio.create_task(delayed_delete())
	except Exception:
		pass  # Игнорируем ошибки отправки


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


def _deal_country_label(country_code: str) -> str:
	if country_code == "BYN":
		return "🇧🇾Беларусь"
	return "🇷🇺Россия"


def _format_crypto_amount(amount: float) -> str:
	if amount < 1:
		return f"{amount:.8f}".rstrip('0').rstrip('.')
	return f"{amount:.2f}".rstrip('0').rstrip('.')


async def _build_deal_message(
	country_code: str | None,
	crypto_code: str | None,
	amount: float | None,
	amount_currency: float | None,
	currency_symbol: str | None,
	prompt: str | None,
	requisites_text: str | None = None,
	wallet_address: str | None = None,
	show_empty_amount: bool = False,
) -> str:
	header = "Я помогу😊...."
	if not country_code:
		return f"{header}\nВыбери страну ⬇️⬇️⬇️ :"
	lines = [
		header,
		"⬇️Сделка⬇️",
		"➖➖➖➖➖➖",
		_deal_country_label(country_code),
	]
	if crypto_code:
		lines.append(f"🤑{crypto_code}")
	if amount is not None:
		lines.append(f"💴{_format_crypto_amount(amount)}")
	if amount_currency is not None and currency_symbol:
		lines.append(f"❗️💵{int(amount_currency)} {currency_symbol}")
	elif show_empty_amount and currency_symbol:
		lines.append(f"💵согласовывается {currency_symbol}")
	if wallet_address:
		lines.append(f"👛<code>{escape(wallet_address)}</code>")
	# Добавляем разделитель перед курсом BTC, если есть сумма
	if amount_currency is not None:
		lines.append("➖➖➖➖➖➖➖➖➖➖➖")
	# Добавляем курс BTC
	btc_rate = await _get_btc_rate_text()
	lines.append(btc_rate)
	if requisites_text is not None:
		lines.append("➖➖➖➖➖➖➖➖➖➖➖")
		lines.append(
			requisites_text if requisites_text.strip() else "Реквизитов нет, ожидайте сообщение администратора"
		)
	if prompt:
		lines.append(prompt)
	return "\n".join(lines)


async def _build_deal_base_lines(
	country_code: str,
	crypto_code: str,
	amount: float,
	amount_currency: float | None,
	currency_symbol: str,
	wallet_address: str | None = None,
) -> list[str]:
	lines = [
		"⬇️Сделка⬇️",
		"➖➖➖➖➖➖",
		_deal_country_label(country_code),
		f"🤑{crypto_code}",
		f"💴{_format_crypto_amount(amount)}",
	]
	if amount_currency is not None:
		lines.append(f"💵{int(amount_currency)} {currency_symbol}")
	else:
		lines.append(f"💵согласовывается {currency_symbol}")
	if wallet_address:
		lines.append(f"👛<code>{escape(wallet_address)}</code>")
	# Добавляем курс BTC
	btc_rate = await _get_btc_rate_text()
	lines.append(btc_rate)
	lines.append("➖➖➖➖➖➖➖➖➖➖➖")
	return lines


async def _build_user_deal_admin_message_text(deal: dict, admin_text: str) -> str:
	lines = ["Я помогу😊...."]
	base_lines = await _build_deal_base_lines(
		deal.get("country_code", "BYN"),
		deal.get("crypto_type", ""),
		deal.get("amount", 0),
		deal.get("amount_currency", 0),
		deal.get("currency_symbol", "Br"),
		deal.get("wallet_address"),
	)
	lines.extend(base_lines)
	lines.append("💬Чат:")
	lines.append(f"<b>Администратор:</b> {escape(admin_text)}")
	return "\n".join(lines)


async def _build_user_deal_chat_text(deal: dict, chat_lines: list[str]) -> str:
	lines = await _build_deal_base_lines(
		deal.get("country_code", "BYN"),
		deal.get("crypto_type", ""),
		deal.get("amount", 0),
		deal.get("amount_currency", 0),
		deal.get("currency_symbol", "Br"),
		deal.get("wallet_address"),
	)
	lines.append("💬Чат:")
	lines.extend(chat_lines)
	return "\n".join(lines)


def _append_prompt(text: str, prompt: str | None) -> str:
	if not prompt:
		return text
	return f"{text}\n➖➖➖➖➖➖➖➖➖➖➖\n{prompt}"


async def _notify_user_new_message(bot: Bot, chat_id: int) -> None:
	try:
		notification = await bot.send_message(chat_id=chat_id, text="🔔 Новое сообщение от администратора")
		async def delayed_delete():
			await asyncio.sleep(2)
			try:
				await bot.delete_message(chat_id=chat_id, message_id=notification.message_id)
			except Exception:
				pass
		asyncio.create_task(delayed_delete())
	except Exception:
		pass


async def _notify_admins_deal_paid(bot: Bot, deal: dict) -> None:
	return


def _build_deal_chat_lines(messages: list[dict], user_name: str) -> list[str]:
	lines = []
	user_label = escape(user_name)
	for msg in messages:
		text = escape(msg["message_text"])
		if msg["sender_type"] == "admin":
			lines.append(f"<b>Администратор:</b> {text}")
		else:
			lines.append(f"<i>{user_label}:</i> {text}")
	return lines


def _build_deal_chat_blocks(messages: list[dict], user_name: str) -> list[str]:
	lines = []
	user_label = escape(user_name)
	for msg in messages:
		text = escape(msg["message_text"])
		if msg["sender_type"] == "admin":
			lines.append("💬💬 <b>Администратор:</b>")
			lines.append(text)
		else:
			lines.append(f"<i>{user_label}:</i>")
			lines.append(text)
	return lines


def _build_user_deal_chat_prompt_text(deal: dict, chat_blocks: list[str], prompt: str | None) -> str:
	lines = [
		"Я помогу😊....",
		"⬇️Сделка⬇️",
		"➖➖➖➖➖➖",
		_deal_country_label(deal.get("country_code", "BYN")),
		f"🤑{deal.get('crypto_type', '')}",
		f"💴{_format_crypto_amount(deal.get('amount', 0))}",
		f"💵{int(deal.get('amount_currency', 0))} {deal.get('currency_symbol', 'Br')}",
		f"👛<code>{escape(deal.get('wallet_address', ''))}</code>" if deal.get("wallet_address") else "",
		"➖➖➖➖➖➖",
		"💬Чат:",
	]
	lines = [line for line in lines if line]
	lines.extend(chat_blocks)
	if prompt:
		lines.append(prompt)
	return "\n".join(lines)


_NO_AMOUNT_OVERRIDE = object()


async def _build_user_deal_with_requisites_chat_text(
	deal: dict,
	requisites_text: str,
	chat_lines: list[str],
	prompt: str | None = None,
	amount_currency_override=_NO_AMOUNT_OVERRIDE,
	show_requisites: bool = True,
) -> str:
	amount_currency = (
		deal.get("amount_currency", 0)
		if amount_currency_override is _NO_AMOUNT_OVERRIDE
		else amount_currency_override
	)
	lines = await _build_deal_base_lines(
		deal.get("country_code", "BYN"),
		deal.get("crypto_type", ""),
		deal.get("amount", 0),
		amount_currency,
		deal.get("currency_symbol", "Br"),
		deal.get("wallet_address"),
	)
	if show_requisites:
		lines.append(requisites_text if requisites_text.strip() else "Реквизитов нет, ожидайте сообщение администратора")
	else:
		lines.append("Реквизиты будут после согласования суммы.")
	if chat_lines:
		lines.append("➖➖➖➖➖➖➖➖➖➖➖")
		lines.append("💬Чат:")
		lines.extend(chat_lines)
	if prompt:
		lines.append("➖➖➖➖➖➖➖➖➖➖➖")
		lines.append(prompt)
	return "\n".join(lines)


async def _build_user_deal_completed_text(deal: dict) -> str:
	lines = await _build_deal_base_lines(
		deal.get("country_code", "BYN"),
		deal.get("crypto_type", ""),
		deal.get("amount", 0),
		deal.get("amount_currency", 0),
		deal.get("currency_symbol", "Br"),
		deal.get("wallet_address"),
	)
	lines.append("💹Сделка завершена")
	return "\n".join(lines)


async def _get_admin_user_financial_lines(db_local, user_tg_id: int) -> list[str]:
	lines = []
	try:
		monthly_profit = await db_local.get_user_monthly_profit(user_tg_id)
		if monthly_profit is not None:
			try:
				monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
			except (ValueError, TypeError):
				monthly_profit_formatted = str(monthly_profit)
			lines.append(f"🧤 Профит за месяц: {monthly_profit_formatted}")
		else:
			lines.append("🧤 Профит за месяц: 0")
	except Exception:
		lines.append("🧤 Профит за месяц: 0")
	try:
		user_debts = await db_local.get_user_total_debt(user_tg_id)
		debt_lines = [f"{int(debt_sum)} {curr}" for curr, debt_sum in user_debts.items()] if user_debts else []
		lines.append(f"🧤 Долг:{', '.join(debt_lines) if debt_lines else '0'}")
	except Exception:
		lines.append("🧤 Долг:0")
	return lines


def _build_order_completion_message(order: dict) -> str:
	amount = order.get("amount", 0) or 0
	if amount < 1:
		amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
	else:
		amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
	crypto_display = order.get("crypto_display") or order.get("crypto_type") or ""
	user_message = (
		"✅ Ваша заявка успешно выполнена!\n"
		f"Вам зачислено: {amount_str} {crypto_display}"
	)
	crypto_type = order.get("crypto_type")
	wallet_address = order.get("wallet_address") or ""
	if crypto_type == "BTC" and wallet_address:
		user_message += f"\n\n🔗 Проверить транзакцию: https://mempool.space/address/{wallet_address}"
	elif crypto_type == "USDT" and wallet_address:
		user_message += f"\n\n🔗 Проверить транзакцию: https://tronscan.org/#/address/{wallet_address}"
	return user_message


def _deal_status_label(status: str | None) -> str:
	if status == "await_payment":
		return "⛑⛑⛑Статус: Оплата⛑⛑⛑"
	if status == "await_proof":
		return "⛑⛑⛑Статус: Оплачено⛑⛑⛑"
	if status == "completed":
		return "💹💹💹Статус: Завершено💹💹💹"
	return ""


async def _get_btc_rate_text() -> str:
	"""Получает текст с курсом BTC для отображения пользователю"""
	try:
		from app.google_sheets import get_btc_price_usd
		
		# Получаем курс BTC
		btc_price = await get_btc_price_usd()
		
		# Форматируем курс BTC
		btc_text = f"₿ BTC: ${btc_price:,.2f}" if btc_price else "₿ BTC: —"
		
		return btc_text
	except Exception as e:
		logger_main = logging.getLogger("app.main")
		logger_main.warning(f"⚠️ Ошибка при получении курса BTC: {e}")
		return "₿ BTC: —"


async def _get_rates_text(db) -> str:
	"""Получает текст с курсами валют для отображения админу"""
	try:
		from app.google_sheets import get_btc_price_usd
		from app.currency_rates import get_rate_with_fallback
		
		# Получаем курсы
		btc_price = await get_btc_price_usd()
		usd_to_byn = await get_rate_with_fallback("BYN", db)
		usd_to_rub = await get_rate_with_fallback("RUB", db)
		
		# Форматируем курс BTC
		btc_text = f"₿ BTC: ${btc_price:,.2f}" if btc_price else "₿ BTC: —"
		
		# Форматируем курсы валют
		byn_text = f"💱 USD→BYN: {usd_to_byn:.2f}" if usd_to_byn else "💱 USD→BYN: —"
		rub_text = f"💱 USD→RUB: {usd_to_rub:.2f}" if usd_to_rub else "💱 USD→RUB: —"
		
		rates_lines = [
			"➖➖➖➖➖➖➖➖➖➖➖",
			btc_text,
			byn_text,
			rub_text,
			"➖➖➖➖➖➖➖➖➖➖➖",
		]
		return "\n".join(rates_lines)
	except Exception as e:
		logger_main = logging.getLogger("app.main")
		logger_main.warning(f"⚠️ Ошибка при получении курсов: {e}")
		return "➖➖➖➖➖➖➖➖➖➖➖\n💱 Курсы: недоступны\n➖➖➖➖➖➖➖➖➖➖➖"


async def _build_admin_open_deal_text(
	deal: dict,
	requisites_label: str,
	chat_lines: list[str],
	financial_lines: list[str] | None = None,
	db=None,
) -> str:
	user_name = deal.get("user_name", "Не указано")
	user_username = deal.get("user_username", "нет")
	crypto_label = deal.get("crypto_display") or deal.get("crypto_type") or ""
	amount_currency = deal.get("amount_currency")
	currency_symbol = deal.get("currency_symbol", "Br")
	crypto_amount = _format_crypto_amount(deal.get("amount", 0))
	wallet_address = deal.get("wallet_address")
	status_label = _deal_status_label(deal.get("status"))
	
	# Получаем курсы валют
	rates_text = ""
	if db:
		rates_text = await _get_rates_text(db)
	
	# Рассчитываем профит, если есть все необходимые данные
	profit_text = ""
	if db and amount_currency and deal.get("crypto_type"):
		try:
			from app.currency_rates import get_rate_with_fallback
			from app.google_sheets import calculate_profit_from_deal_data
			from app.di import get_db as get_db_func
			from app.config import get_settings
			
			# Получаем курсы
			settings = get_settings()
			usd_to_byn = await get_rate_with_fallback("BYN", db, None)
			if not usd_to_byn:
				byn_rate_str = await db.get_setting("buy_usd_to_byn_rate", "3.3")
				usd_to_byn = float(byn_rate_str) if byn_rate_str else 3.3
			
			usd_to_rub = await get_rate_with_fallback("RUB", db, None)
			if not usd_to_rub:
				rub_rate_str = await db.get_setting("buy_usd_to_rub_rate", "95")
				usd_to_rub = float(rub_rate_str) if rub_rate_str else 95
			
			# Рассчитываем приблизительный профит
			profit = await calculate_profit_from_deal_data(deal, db, usd_to_byn, usd_to_rub)
			if profit is not None:
				profit_formatted = f"{int(profit):,}".replace(",", " ")
				profit_text = f"📈 Профит: {profit_formatted} USD"
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.warning(f"⚠️ Ошибка расчета профита для отображения: {e}")
	
	parts = [
		"⬇️Открыта Сделка⬇️",
		"〰️〰️〰️〰️〰️",
		f"👤 {user_name} (@{user_username})",
		f"🌍 Страна: {_deal_country_label(deal.get('country_code', 'BYN'))}",
		*(financial_lines or []),
		"➖➖➖➖➖➖➖➖➖➖➖",  # Разделитель после долга
		f"🪙Крипта: {crypto_label}",
		f"💴Сумма: {int(amount_currency)} {currency_symbol}" if amount_currency is not None else None,
		f"🤑{deal.get('crypto_type', '')}={crypto_amount}",
		f"👛<code>{escape(wallet_address)}</code>" if wallet_address else None,
		rates_text,  # Добавляем курсы
		profit_text if profit_text else None,  # Добавляем профит
		"➖➖➖➖➖➖➖➖➖➖➖",
		requisites_label,
		"➖➖➖➖➖➖➖➖➖➖➖",
	]
	parts = [part for part in parts if part]
	if chat_lines:
		parts.append("💬Чат:")
		parts.extend(chat_lines)
	if status_label:
		if chat_lines:
			parts.append("➖➖➖➖➖➖➖➖➖➖➖")
		parts.append(status_label)
	return "\n".join(parts)


async def _build_admin_deal_alert_text(
	deal: dict,
	chat_lines: list[str],
	financial_lines: list[str] | None = None,
	db=None,
) -> str:
	user_name = deal.get("user_name", "Не указано")
	user_username = deal.get("user_username", "нет")
	crypto_label = deal.get("crypto_display") or deal.get("crypto_type") or ""
	amount_currency = deal.get("amount_currency")
	currency_symbol = deal.get("currency_symbol", "Br")
	wallet_address = deal.get("wallet_address")
	status_label = _deal_status_label(deal.get("status"))
	
	# Получаем курсы валют
	rates_text = ""
	if db:
		rates_text = await _get_rates_text(db)
	
	parts = [
		"⚠️ У пользователя нет привязанной карты для оплаты.",
		"",
		f"👤 {user_name} (@{user_username})",
		f"🌍 Страна: {_deal_country_label(deal.get('country_code', 'BYN'))}",
		*(financial_lines or []),
		f"🆔 ID: {deal.get('user_tg_id')}",
		f"Крипта: {crypto_label}",
		f"Сумма: {int(amount_currency)} {currency_symbol}" if amount_currency is not None else None,
		f"👛<code>{escape(wallet_address)}</code>" if wallet_address else None,
		rates_text,  # Добавляем курсы
		"➖➖➖➖➖➖➖➖➖➖➖",
	]
	parts = [part for part in parts if part]
	if chat_lines:
		parts.append("💬Чат:")
		parts.extend(chat_lines)
	if status_label:
		if chat_lines:
			parts.append("➖➖➖➖➖➖➖➖➖➖➖")
		parts.append(status_label)
	return "\n".join(parts)


async def _get_global_card_id_for_country(db, country_code: str | None) -> int | None:
	if not country_code:
		return None
	value = await db.get_setting(f"one_card_for_all_{country_code}")
	if not value:
		return None
	try:
		return int(value)
	except (ValueError, TypeError):
		return None


async def _get_requisites_text_by_card_id(db, card_id: int) -> str:
	requisites = await db.list_card_requisites(card_id)
	requisites_list = [req["requisite_text"] for req in requisites]
	user_msg = await db.get_card_user_message(card_id)
	if user_msg and user_msg.strip():
		requisites_list.append(user_msg)
	return "\n".join(requisites_list) if requisites_list else ""


async def _get_requisites_label_by_card_id(db, card_id: int) -> str:
	card_info = await db.get_card_by_id(card_id)
	card_name = (card_info.get("name") if card_info else None) or ""
	group_name = ""
	if card_info and card_info.get("group_id"):
		group = await db.get_card_group_by_id(card_info["group_id"])
		group_name = group.get("name") if group else ""
	if group_name:
		return f"{group_name} ({card_name})"
	return card_name or "Реквизиты не привязаны"


async def _get_deal_requisites_text(db, user_tg_id: int, country_code: str | None = None) -> str:
	global_card_id = await _get_global_card_id_for_country(db, country_code)
	if global_card_id:
		return await _get_requisites_text_by_card_id(db, global_card_id)
	user_cards = await db.get_cards_for_user_tg(user_tg_id)
	if not user_cards:
		return ""
	card = user_cards[0]
	card_id = card["card_id"]
	return await _get_requisites_text_by_card_id(db, card_id)


async def _get_deal_requisites_label(db, user_tg_id: int, country_code: str | None = None) -> str:
	global_card_id = await _get_global_card_id_for_country(db, country_code)
	if global_card_id:
		return await _get_requisites_label_by_card_id(db, global_card_id)
	user_cards = await db.get_cards_for_user_tg(user_tg_id)
	if not user_cards:
		return "Реквизиты не привязаны"
	card = user_cards[0]
	card_id = card["card_id"]
	card_info = await db.get_card_by_id(card_id)
	card_name = (card_info.get("name") if card_info else None) or card.get("card_name") or card.get("name") or ""
	group_name = ""
	if card_info and card_info.get("group_id"):
		group = await db.get_card_group_by_id(card_info["group_id"])
		group_name = group.get("name") if group else ""
	if group_name:
		return f"{group_name} ({card_name})"
	return card_name or "Реквизиты не привязаны"




async def update_buy_deal_alert(bot: Bot, deal_id: int) -> None:
	from app.di import get_db
	db_local = get_db()
	deal = await db_local.get_buy_deal_by_id(deal_id)
	if not deal:
		return
	logger_main = logging.getLogger("app.main")
	logger_main.info(f"🧪 update_buy_deal_alert: deal_id={deal_id}, user_tg_id={deal.get('user_tg_id')}")
	try:
		alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
		alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
	except (ValueError, TypeError):
		alert_threshold = 400.0
	total_usd = deal.get("total_usd") or 0
	logger_main.info(f"🧪 update_buy_deal_alert: total_usd={total_usd}, alert_threshold={alert_threshold}")
	if total_usd >= alert_threshold:
		user_tg_id = deal.get("user_tg_id")
		logger_main.info(f"🧪 update_buy_deal_alert: large deal, deal_id={deal_id}, user_tg_id={user_tg_id}, large_order_alerts_keys={list(large_order_alerts.keys())}")
		# Всегда берем актуальные message_ids из buy_deal_alerts для текущей сделки
		from app.di import get_admin_ids
		admin_ids = get_admin_ids()
		message_ids = {}
		if deal_id in buy_deal_alerts and admin_ids:
			for admin_id in admin_ids:
				if admin_id in buy_deal_alerts[deal_id]:
					message_ids[admin_id] = buy_deal_alerts[deal_id][admin_id]
		logger_main.info(f"🧪 update_buy_deal_alert: message_ids from buy_deal_alerts[{deal_id}]={message_ids}")
		if not message_ids:
			logger_main.warning(f"⚠️ update_buy_deal_alert: message_ids пустые для deal_id={deal_id}")
			return
		# Инициализируем необходимые переменные
		financial_lines = await _get_admin_user_financial_lines(db_local, user_tg_id)
		requisites_label = await _get_deal_requisites_label(
			db_local,
			user_tg_id,
			deal.get("country_code")
		)
		# Получаем question_id из large_order_alerts, если есть
		user_data = large_order_alerts.get(user_tg_id)
		question_id = None
		if isinstance(user_data, dict):
			question_id = user_data.get("question_id")
		messages = await db_local.get_buy_deal_messages(deal_id)
		logger_main.info(f"🧪 update_buy_deal_alert: got {len(messages)} messages from DB for deal_id={deal_id}")
		if messages:
			logger_main.info(f"🧪 update_buy_deal_alert: last message: sender={messages[-1].get('sender_type')}, text={messages[-1].get('message_text', '')[:50]}")
		chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
		logger_main.info(f"🧪 update_buy_deal_alert: chat_lines count={len(chat_lines)}")
		if chat_lines:
			logger_main.info(f"🧪 update_buy_deal_alert: last chat_line={chat_lines[-1][:100]}")
		# Для крупных сделок проверяем question_messages, если есть question_id
		if question_id:
			try:
				q_messages = await db_local.get_question_messages(question_id)
				if q_messages:
					chat_lines = _build_deal_chat_lines(q_messages, deal.get("user_name", "Пользователь"))
					logger_main.info(f"🧪 update_buy_deal_alert: using question_messages, count={len(q_messages)}")
			except Exception as e:
				logger_main.warning(f"⚠️ update_buy_deal_alert: error getting question_messages: {e}")
		logger_main.info(f"🧪 update_buy_deal_alert: final chat_lines count={len(chat_lines)}, requisites_label={requisites_label}")
		alert_text = await _build_admin_open_deal_text(deal, requisites_label, chat_lines, financial_lines, db_local)
		logger_main.info(f"🧪 update_buy_deal_alert: alert_text length={len(alert_text)}, preview={alert_text[:200]}")
		logger_main.info(f"🧪 update_buy_deal_alert: alert_text_len={len(alert_text)}")
		from app.keyboards import deal_alert_admin_kb, deal_alert_admin_completed_kb
		reply_markup = (
			deal_alert_admin_completed_kb(deal_id)
			if deal.get("status") == "completed"
			else deal_alert_admin_kb(deal_id)
		)
		for admin_id, message_id in message_ids.items():
			try:
				logger_main.info(f"🧪 update_buy_deal_alert: editing message admin_id={admin_id}, message_id={message_id}")
				await bot.edit_message_text(
					chat_id=admin_id,
					message_id=message_id,
					text=alert_text,
					parse_mode=ParseMode.HTML,
					reply_markup=reply_markup
				)
				logger_main.info(f"✅ update_buy_deal_alert: message updated successfully")
			except Exception as e:
				logger_main.warning(f"⚠️ update_buy_deal_alert: error editing message: {e}")
		return
	financial_lines = await _get_admin_user_financial_lines(db_local, deal.get("user_tg_id"))
	requisites_label = await _get_deal_requisites_label(
		db_local,
		deal.get("user_tg_id"),
		deal.get("country_code")
	)
	messages = await db_local.get_buy_deal_messages(deal_id)
	chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
	user_data = large_order_alerts.get(deal.get("user_tg_id"))
	logger_main.info(f"🧪 update_buy_deal_alert: large_order_data={user_data}")
	if isinstance(user_data, dict):
		question_id = user_data.get("question_id")
		if question_id:
			try:
				q_messages = await db_local.get_question_messages(question_id)
				if q_messages:
					chat_lines = _build_deal_chat_lines(q_messages, deal.get("user_name", "Пользователь"))
			except Exception:
				pass
	if requisites_label and requisites_label != "Реквизиты не привязаны":
		alert_text = await _build_admin_open_deal_text(deal, requisites_label, chat_lines, financial_lines, db_local)
	else:
		alert_text = await _build_admin_deal_alert_text(deal, chat_lines, financial_lines, db_local)
	logger_main.info(f"🧪 update_buy_deal_alert: alert_text_len={len(alert_text)}")
	message_ids = buy_deal_alerts.get(deal_id, {})
	logger_main.info(f"🧪 update_buy_deal_alert: buy_deal_alerts_ids={message_ids}")
	if not message_ids:
		from app.di import get_admin_ids
		from app.keyboards import deal_alert_admin_kb, deal_alert_admin_completed_kb
		admin_ids = get_admin_ids()
		if not admin_ids:
			return
		# Защита от переполнения памяти
		limit_dict_size(buy_deal_alerts, MAX_BUY_DEAL_ALERTS, "buy_deal_alerts")
		buy_deal_alerts[deal_id] = {}
		for admin_id in admin_ids:
			reply_markup = (
				deal_alert_admin_completed_kb(deal_id)
				if deal.get("status") == "completed"
				else deal_alert_admin_kb(deal_id)
			)
			try:
				sent = await bot.send_message(
					chat_id=admin_id,
					text=alert_text,
					parse_mode="HTML",
					reply_markup=reply_markup
				)
				buy_deal_alerts[deal_id][admin_id] = sent.message_id
				# Сохраняем в БД для восстановления после перезапуска
				await save_deal_alert_to_db(deal_id, admin_id, sent.message_id)
			except Exception:
				pass
		return
	from app.keyboards import deal_alert_admin_kb, deal_alert_admin_completed_kb
	for admin_id, message_id in message_ids.items():
		reply_markup = (
			deal_alert_admin_completed_kb(deal_id)
			if deal.get("status") == "completed"
			else deal_alert_admin_kb(deal_id)
		)
		try:
			await bot.edit_message_text(
				chat_id=admin_id,
				message_id=message_id,
				text=alert_text,
				parse_mode="HTML",
				reply_markup=reply_markup
			)
		except Exception:
			try:
				await bot.edit_message_caption(
					chat_id=admin_id,
					message_id=message_id,
					caption=alert_text,
					parse_mode="HTML",
					reply_markup=reply_markup
				)
			except Exception:
				pass


async def build_admin_open_deal_text_with_chat(db_local, deal_id: int) -> str:
	deal = await db_local.get_buy_deal_by_id(deal_id)
	if not deal:
		return ""
	requisites_text = await _get_deal_requisites_label(
		db_local,
		deal.get("user_tg_id"),
		deal.get("country_code")
	)
	financial_lines = await _get_admin_user_financial_lines(db_local, deal.get("user_tg_id"))
	messages = await db_local.get_buy_deal_messages(deal_id)
	chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
	return await _build_admin_open_deal_text(deal, requisites_text, chat_lines, financial_lines, db_local)


async def _send_or_edit_deal_message(
	bot: Bot,
	chat_id: int,
	state: FSMContext,
	text: str,
	reply_markup=None,
) -> int:
	message_id = None
	if state:
		data = await state.get_data()
		message_id = data.get("deal_message_id")
	if message_id:
		try:
			await bot.edit_message_text(
				chat_id=chat_id,
				message_id=message_id,
				text=text,
				reply_markup=reply_markup,
				parse_mode="HTML"
			)
			return message_id
		except Exception:
			pass
	sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML")
	if state:
		await state.update_data(deal_message_id=sent.message_id, order_message_id=sent.message_id)
	return sent.message_id


def setup_logging(log_level: str = "INFO", max_log_size_mb: int = 10, backup_count: int = 10, keep_days: int = 30):
	"""
	Настраивает систему логирования с ротацией и очисткой старых файлов.
	
	Args:
		log_level: Уровень логирования (DEBUG/INFO/WARNING/ERROR)
		max_log_size_mb: Максимальный размер файла лога в MB перед ротацией
		backup_count: Количество резервных копий для ротации по размеру
		keep_days: Количество дней хранения логов (старые удаляются)
	"""
	os.makedirs("logs", exist_ok=True)
	
	log_level_name = log_level.upper()
	log_level_value = getattr(logging, log_level_name, logging.INFO)
	
	# Формат логов: дата, время, уровень, модуль, сообщение
	log_format = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
	date_format = "%Y-%m-%d %H:%M:%S"
	
	# Основной лог-файл с ротацией по размеру
	main_log_handler = RotatingFileHandler(
		"logs/bot.log",
		maxBytes=max_log_size_mb * 1024 * 1024,
		backupCount=backup_count,
		encoding="utf-8",
	)
	main_log_handler.setLevel(log_level_value)
	main_log_handler.setFormatter(logging.Formatter(log_format, date_format))
	
	# Лог-файл с ротацией по дням (ежедневная ротация)
	daily_log_handler = TimedRotatingFileHandler(
		"logs/bot_daily.log",
		when="midnight",
		interval=1,
		backupCount=keep_days,
		encoding="utf-8",
	)
	daily_log_handler.setLevel(log_level_value)
	daily_log_handler.setFormatter(logging.Formatter(log_format, date_format))
	
	# Отдельный файл для ошибок (только ERROR и CRITICAL)
	error_log_handler = RotatingFileHandler(
		"logs/errors.log",
		maxBytes=5 * 1024 * 1024,  # 5 MB для ошибок
		backupCount=5,
		encoding="utf-8",
	)
	error_log_handler.setLevel(logging.ERROR)
	error_log_handler.setFormatter(logging.Formatter(log_format, date_format))
	
	# Настройка корневого логгера
	root_logger = logging.getLogger()
	root_logger.setLevel(log_level_value)
	root_logger.handlers.clear()  # Очищаем существующие обработчики
	root_logger.addHandler(main_log_handler)
	root_logger.addHandler(daily_log_handler)
	root_logger.addHandler(error_log_handler)
	
	# Очистка старых логов
	cleanup_old_logs(keep_days)
	
	return root_logger


def cleanup_old_logs(keep_days: int = 30):
	"""
	Удаляет старые лог-файлы, которые старше указанного количества дней.
	
	Args:
		keep_days: Количество дней для хранения логов
	"""
	try:
		logs_dir = "logs"
		if not os.path.exists(logs_dir):
			return
		
		cutoff_date = datetime.now() - timedelta(days=keep_days)
		cutoff_timestamp = cutoff_date.timestamp()
		
		# Ищем все .log файлы и их резервные копии
		patterns = [
			os.path.join(logs_dir, "*.log"),
			os.path.join(logs_dir, "*.log.*"),
		]
		
		deleted_count = 0
		for pattern in patterns:
			for log_file in glob.glob(pattern):
				try:
					# Проверяем время модификации файла
					file_mtime = os.path.getmtime(log_file)
					if file_mtime < cutoff_timestamp:
						os.remove(log_file)
						deleted_count += 1
				except (OSError, Exception) as e:
					# Игнорируем ошибки при удалении (файл может быть занят)
					pass
		
		if deleted_count > 0:
			logging.getLogger("app.start").info(f"🧹 Очищено старых лог-файлов: {deleted_count}")
	except Exception as e:
		# Не падаем, если не удалось очистить логи
		pass


async def main() -> None:
	os.makedirs("logs", exist_ok=True)
	settings = get_settings()

	# Настройка логирования
	setup_logging(
		log_level=settings.log_level or "INFO",
		max_log_size_mb=10,  # 10 MB
		backup_count=10,  # Храним 10 резервных копий
		keep_days=30,  # Храним логи 30 дней
	)

	# Приглушаем сторонние библиотеки (они часто шумят на DEBUG)
	logging.getLogger("aiosqlite").setLevel(logging.WARNING)
	logging.getLogger("urllib3").setLevel(logging.WARNING)
	logging.getLogger("gspread").setLevel(logging.WARNING)

	logger = logging.getLogger("app.start")
	logger.info("=" * 80)
	logger.info("🚀 Запуск бота")
	logger.info(f"📊 Уровень логирования: {settings.log_level or 'INFO'}")
	logger.info("📁 Логи сохраняются в:")
	logger.info("   - logs/bot.log (ротация по размеру, до 10 файлов)")
	logger.info("   - logs/bot_daily.log (ротация по дням, 30 дней)")
	logger.info("   - logs/errors.log (только ошибки, до 5 файлов)")
	logger.info("=" * 80)
	logger.debug(f"Loaded settings: db={settings.database_path}, admins={settings.admin_ids}")
	if not settings.telegram_bot_token:
		raise RuntimeError("TELEGRAM_BOT_TOKEN не задан. Создайте .env с токеном.")

	db = Database(settings.database_path)
	await db.connect()
	set_dependencies(db, settings.admin_ids, settings.admin_usernames)
	logger.debug("Database connected and dependencies set")

	bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
	dp = Dispatcher(storage=MemoryStorage())
	
	# Инициализируем глобальные словари
	global large_order_alerts, buy_deal_alerts
	large_order_alerts = {}
	buy_deal_alerts = {}
	
	# Загружаем deal alerts из БД для восстановления после перезапуска
	await load_deal_alerts_from_db()
	logger.info("✅ Deal alerts загружены из БД")
	
	# Инициализируем rate limiters с параметрами из настроек
	from app.rate_limiter import init_rate_limiters, RateLimitMiddleware, CallbackRateLimitMiddleware, periodic_cleanup as rate_limiter_cleanup
	init_rate_limiters(settings)
	
	# Добавляем rate limiting middleware для защиты от flood атак
	dp.message.middleware(RateLimitMiddleware())
	dp.callback_query.middleware(CallbackRateLimitMiddleware())
	logger.info("✅ Rate limiting middleware добавлен")
	
	# Запускаем периодическую очистку rate limiters
	asyncio.create_task(rate_limiter_cleanup())
	logger.info("✅ Периодическая очистка rate limiters запущена")
	
	# Запускаем периодическую очистку глобальных словарей
	asyncio.create_task(periodic_cleanup_alerts())
	logger.info("✅ Периодическая очистка глобальных словарей запущена")
	
	# Глобальные словари уже инициализированы выше
	
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
		BotCommand(command="cons", description="Расходы"),
		BotCommand(command="start", description="Меню"),
	]

	# Команды для пользователей (чтобы появлялась кнопка "Меню" в чате)
	user_commands = [
		BotCommand(command="start", description="Перезапуск бота"),
		BotCommand(command="buy", description="Купить"),
		BotCommand(command="sell", description="Продать"),
		BotCommand(command="question", description="Задать вопрос"),
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
				# Получаем состояние FSM для логирования
				state: FSMContext = data.get("state")
				current_state = None
				if state:
					try:
						current_state = await state.get_state()
					except:
						pass
				
				text = event.text or event.caption or ""
				forward_origin = getattr(event, "forward_origin", None)
				forward_from = getattr(event, "forward_from", None)
				is_forward = bool(forward_origin or forward_from)
				is_command = text.startswith("/") if text else False
				
				# Логируем ВСЕ сообщения на INFO для отладки
				logger.info(f"🟢🟢🟢 MIDDLEWARE: message_id={event.message_id}, from_user={event.from_user.id if event.from_user else None}, text='{text[:100]}', state={current_state}, is_forward={is_forward}, is_command={is_command}, handler={handler.__name__ if hasattr(handler, '__name__') else 'unknown'}")
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
			else:
				# Пользователь не разрешен - отправляем уведомление админам
				logger.info(f"⚠️ Неразрешенный пользователь пытается получить доступ: tg_id={message.from_user.id}, username={message.from_user.username}")
				
				# Получаем user_id из БД
				user_id = await db_local.get_user_id_by_tg(message.from_user.id)
				if user_id:
					# Формируем сообщение для админов
					user_name = message.from_user.full_name or "Не указано"
					user_username = message.from_user.username or "Не указано"
					
					admin_message_text = (
						f"⚠️ <b>Новый запрос на доступ</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{message.from_user.id}</code>\n\n"
						f"Пользователь пытается получить доступ к боту."
					)
					
					# Отправляем уведомление всем админам (и по ID, и по username)
					admin_ids = get_admin_ids()
					admin_usernames = get_admin_usernames()
					logger_main = logging.getLogger("app.main")
					logger_main.info(f"📤 Отправка уведомления о запросе доступа админам. Админы по ID: {admin_ids}, по username: {admin_usernames}")
					
					# Собираем все chat_id админов для отправки
					admin_chat_ids = set()
					
					# Добавляем админов по ID
					admin_chat_ids.update(admin_ids)
					
					# Получаем chat_id для админов по username
					if admin_usernames:
						for username in admin_usernames:
							username_clean = username.lstrip("@")
							found_chat_id = None
							
							# Сначала пробуем найти в БД
							try:
								user_by_username = await db_local.get_user_by_username(username_clean)
								if user_by_username and user_by_username.get("tg_id"):
									found_chat_id = user_by_username["tg_id"]
									logger_main.info(f"✅ Найден админ @{username_clean} в БД, tg_id={found_chat_id}")
							except Exception as e:
								logger_main.debug(f"⚠️ Ошибка поиска админа @{username_clean} в БД: {e}")
							
							# Если не нашли в БД, пробуем через get_chat
							if not found_chat_id:
								try:
									chat = await message.bot.get_chat(f"@{username_clean}")
									found_chat_id = chat.id
									logger_main.info(f"✅ Получен chat_id={found_chat_id} для админа @{username_clean} через get_chat")
								except Exception as e:
									logger_main.warning(f"⚠️ Не удалось получить chat_id для админа @{username_clean}: {e}. Возможно, админ не писал боту. Попросите админа написать боту хотя бы раз (/start)")
							
							if found_chat_id:
								admin_chat_ids.add(found_chat_id)
					
					# Отправляем уведомления всем админам
					if admin_chat_ids:
						for admin_chat_id in admin_chat_ids:
							try:
								await message.bot.send_message(
									chat_id=admin_chat_id,
									text=admin_message_text,
									parse_mode=ParseMode.HTML,
									reply_markup=user_access_request_kb(user_id)
								)
								logger_main.info(f"✅ Уведомление о запросе доступа отправлено админу {admin_chat_id}")
							except Exception as e:
								logger_main.error(f"❌ Ошибка отправки уведомления админу {admin_chat_id}: {e}", exc_info=True)
					else:
						logger_main.warning("⚠️ Не удалось определить chat_id ни для одного админа, уведомление не отправлено")

		# Остальные: игнор (без ответа)

	@dp.message(F.text.in_({"🚀 Купить", "⚡ Продать", "❓ Задать вопрос"}))
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
			await state.clear()
			user_name = message.from_user.full_name or ""
			user_username = message.from_user.username or ""
			active_deal_id = await db_local.get_active_buy_deal_by_user(message.from_user.id)
			if active_deal_id:
				await db_local.update_buy_deal_fields(active_deal_id, status="cancelled")
			deal_id = await db_local.create_buy_deal(
				user_tg_id=message.from_user.id,
				user_name=user_name,
				user_username=user_username,
				status="draft"
			)
			await state.set_state(DealStates.selecting_country)
			message_text = await _build_deal_message(
				country_code=None,
				crypto_code=None,
				amount=None,
				amount_currency=None,
				currency_symbol=None,
				prompt=None
			)
			deal_message_id = await _send_or_edit_deal_message(
				bot=message.bot,
				chat_id=message.chat.id,
				state=state,
				text=message_text,
				reply_markup=buy_country_inline_kb()
			)
			await state.update_data(
				deal_id=deal_id,
				deal_message_id=deal_message_id,
				order_message_id=deal_message_id,
				last_bot_message_id=None
			)
			await db_local.update_buy_deal_user_message_id(deal_id, deal_message_id)
		elif message.text == "⚡ Продать":
			# Очищаем состояние при начале новой продажи
			await state.clear()
			await state.set_state(SellStates.selecting_crypto)
			from app.keyboards import sell_crypto_kb
			await send_and_save_message(message, "Выберите криптовалюту для продажи:", reply_markup=sell_crypto_kb(), state=state)
		elif message.text == "❓ Задать вопрос":
			# Переводим в состояние ожидания вопроса
			await state.set_state(QuestionStates.waiting_question)
			await send_and_save_message(
				message,
				"📝 Пожалуйста, введите ваш вопрос. Администратор получит ваше сообщение и свяжется с вами.",
				state=state
			)

	@dp.callback_query(F.data.startswith("deal:country:"))
	async def on_deal_country_selected(cb: CallbackQuery, state: FSMContext):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		await cb.answer()
		country_code = cb.data.split(":")[2]
		await state.update_data(selected_country=country_code)
		deal_id = (await state.get_data()).get("deal_id")
		if deal_id:
			await db_local.update_buy_deal_fields(deal_id, country_code=country_code)
		await state.set_state(DealStates.selecting_crypto)
		message_text = await _build_deal_message(
			country_code=country_code,
			crypto_code=None,
			amount=None,
			amount_currency=None,
			currency_symbol=None,
			prompt="Выбери монету⬇️⬇️⬇️ :"
		)
		await _send_or_edit_deal_message(
			bot=cb.bot,
			chat_id=cb.message.chat.id,
			state=state,
			text=message_text,
			reply_markup=buy_crypto_inline_kb()
		)

	@dp.callback_query(F.data.startswith("deal:crypto:"))
	async def on_deal_crypto_selected(cb: CallbackQuery, state: FSMContext):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		await cb.answer()
		crypto_type = cb.data.split(":")[2]
		if crypto_type == "BTC":
			crypto_display = "Bitcoin"
		elif crypto_type == "LTC":
			crypto_display = "Litecoin"
		elif crypto_type == "USDT":
			crypto_display = "USDT"
		else:
			crypto_display = "Monero"
		await state.update_data(crypto_type=crypto_type, crypto_display=crypto_display)
		deal_id = (await state.get_data()).get("deal_id")
		if deal_id:
			await db_local.update_buy_deal_fields(
				deal_id,
				crypto_type=crypto_type,
				crypto_display=crypto_display
			)
		await state.set_state(DealStates.waiting_amount)
		data = await state.get_data()
		message_text = await _build_deal_message(
			country_code=data.get("selected_country"),
			crypto_code=crypto_type,
			amount=None,
			amount_currency=None,
			currency_symbol=None,
			prompt="Введи количество монет⬇️⬇️⬇️ :"
		)
		await _send_or_edit_deal_message(
			bot=cb.bot,
			chat_id=cb.message.chat.id,
			state=state,
			text=message_text,
			reply_markup=None
		)

	@dp.message(DealStates.waiting_amount, ~F.text.startswith("/"))
	async def on_deal_amount_entered(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		await delete_user_message(message)
		data = await state.get_data()
		# Удаляем предыдущее сообщение об ошибке минимальной суммы, если оно есть
		min_amount_error_message_id = data.get("min_amount_error_message_id")
		if min_amount_error_message_id:
			await delete_previous_bot_message(message.bot, message.chat.id, min_amount_error_message_id)
			await state.update_data(min_amount_error_message_id=None)
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		selected_country = data.get("selected_country", "BYN")
		amount_str_raw = message.text.strip().replace(",", ".")
		try:
			amount = float(amount_str_raw)
		except ValueError:
			await message.answer("❌ Неверный формат суммы. Введите число (например: 0.008 или 100):")
			return
		
		# Валидация суммы с проверкой максимального значения
		from app.validators import validate_amount
		is_valid, error_msg = validate_amount(amount, min_value=0.0, max_value=1000000.0)
		if not is_valid:
			await message.answer(error_msg)
			return
		from app.google_sheets import get_btc_price_usd, get_ltc_price_usd, get_xmr_price_usd
		crypto_price_usd = None
		if crypto_type == "BTC":
			crypto_price_usd = await get_btc_price_usd()
			crypto_symbol = "₿"
		elif crypto_type == "LTC":
			crypto_price_usd = await get_ltc_price_usd()
			crypto_symbol = "Ł"
		elif crypto_type == "USDT":
			crypto_price_usd = 1.0
			crypto_symbol = "₮"
		else:
			crypto_price_usd = await get_xmr_price_usd()
			crypto_symbol = "ɱ"
		if crypto_price_usd is None:
			await message.answer("❌ Не удалось получить курс криптовалюты. Попробуйте позже.")
			return
		# Автоматическое получение курса валюты из интернета
		from app.currency_rates import get_rate_with_fallback
		if selected_country == "BYN":
			usd_to_currency_rate = await get_rate_with_fallback("BYN", db_local, message.bot)
			currency_symbol = "Br"
		else:  # RUB
			usd_to_currency_rate = await get_rate_with_fallback("RUB", db_local, message.bot)
			currency_symbol = "₽"
		amount_usd = amount * crypto_price_usd
		min_usd_str = await db_local.get_setting("buy_min_usd", "15")
		try:
			min_usd = float(min_usd_str) if min_usd_str else 15.0
		except (ValueError, TypeError):
			min_usd = 15.0
		if amount_usd < min_usd:
			error_message = await message.answer(
				f"❌ Минимальная сумма сделки {min_usd}$.\n"
				f"Введите сумму больше {min_usd}$:"
			)
			# Сохраняем ID сообщения об ошибке для последующего удаления
			await state.update_data(min_amount_error_message_id=error_message.message_id)
			return
		if amount_usd <= 100:
			markup_percent_key = "buy_markup_percent_small"
			default_markup = 15
		elif amount_usd <= 449:
			markup_percent_key = "buy_markup_percent_101_449"
			default_markup = 11
		elif amount_usd <= 699:
			markup_percent_key = "buy_markup_percent_450_699"
			default_markup = 9
		elif amount_usd <= 999:
			markup_percent_key = "buy_markup_percent_700_999"
			default_markup = 8
		elif amount_usd <= 1499:
			markup_percent_key = "buy_markup_percent_1000_1499"
			default_markup = 7
		elif amount_usd <= 1999:
			markup_percent_key = "buy_markup_percent_1500_1999"
			default_markup = 6
		else:
			markup_percent_key = "buy_markup_percent_2000_plus"
			default_markup = 5
		markup_percent_str = await db_local.get_setting(markup_percent_key, str(default_markup))
		try:
			markup_percent = float(markup_percent_str) if markup_percent_str else default_markup
		except (ValueError, TypeError):
			markup_percent = default_markup
		crypto_price_with_markup = crypto_price_usd * (1 + markup_percent / 100)
		total_usd = crypto_price_with_markup * amount
		try:
			alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
			alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
		except (ValueError, TypeError):
			alert_threshold = 400.0
		extra_fee_usd_low_str = await db_local.get_setting("buy_extra_fee_usd_low", "50")
		extra_fee_usd_mid_str = await db_local.get_setting("buy_extra_fee_usd_mid", "67")
		try:
			extra_fee_usd_low = float(extra_fee_usd_low_str) if extra_fee_usd_low_str else 50.0
		except (ValueError, TypeError):
			extra_fee_usd_low = 50.0
		try:
			extra_fee_usd_mid = float(extra_fee_usd_mid_str) if extra_fee_usd_mid_str else 67.0
		except (ValueError, TypeError):
			extra_fee_usd_mid = 67.0
		if selected_country == "BYN":
			fee_low_str = await db_local.get_setting("buy_extra_fee_low_byn", "10")
			fee_mid_str = await db_local.get_setting("buy_extra_fee_mid_byn", "5")
			try:
				fee_low = float(fee_low_str) if fee_low_str else 10.0
			except (ValueError, TypeError):
				fee_low = 10.0
			try:
				fee_mid = float(fee_mid_str) if fee_mid_str else 5.0
			except (ValueError, TypeError):
				fee_mid = 5.0
		else:
			fee_low_str = await db_local.get_setting("buy_extra_fee_low_rub", "10")
			fee_mid_str = await db_local.get_setting("buy_extra_fee_mid_rub", "5")
			try:
				fee_low = float(fee_low_str) if fee_low_str else 10.0
			except (ValueError, TypeError):
				fee_low = 10.0
			try:
				fee_mid = float(fee_mid_str) if fee_mid_str else 5.0
			except (ValueError, TypeError):
				fee_mid = 5.0
		extra_fee_currency = 0.0
		if total_usd < extra_fee_usd_low:
			extra_fee_currency = fee_low
		elif total_usd < extra_fee_usd_mid:
			extra_fee_currency = fee_mid
		amount_currency = (total_usd * usd_to_currency_rate) + extra_fee_currency
		await state.update_data(
			amount=amount,
			amount_currency=amount_currency,
			crypto_type=crypto_type,
			crypto_symbol=crypto_symbol,
			crypto_price_usd=crypto_price_usd,
			crypto_price_with_markup=crypto_price_with_markup,
			markup_percent=markup_percent,
			total_usd=total_usd,
			extra_fee_currency=extra_fee_currency,
			selected_country=selected_country,
			currency_symbol=currency_symbol,
			usd_to_currency_rate=usd_to_currency_rate,
			alert_threshold=alert_threshold
		)
		deal_id = data.get("deal_id")
		is_large_deal = total_usd >= alert_threshold
		if total_usd >= alert_threshold:
			if deal_id:
				await db_local.update_buy_deal_fields(
					deal_id,
					amount=amount,
					amount_currency=amount_currency,
					currency_symbol=currency_symbol,
					total_usd=total_usd,
					status="await_wallet"
				)
			await state.update_data(is_large_deal=True)
			await state.set_state(DealStates.waiting_wallet_address)
			message_text = await _build_deal_message(
				country_code=selected_country,
				crypto_code=crypto_type,
				amount=amount,
				amount_currency=None,
				currency_symbol=currency_symbol,
				prompt="Введи адрес кошелька⬇️⬇️⬇️ :",
				show_empty_amount=True
			)
			await _send_or_edit_deal_message(
				bot=message.bot,
				chat_id=message.chat.id,
				state=state,
				text=message_text,
				reply_markup=None
			)
			# Для крупных сделок отправляем предварительный алерт (не полное оповещение)
			# Полное оповещение будет отправлено в зависимости от настройки
			admin_ids = get_admin_ids()
			alert_text = (
				f"🚨 <b>Крупная заявка</b>\n\n"
				f"Пользователь: {message.from_user.full_name or 'Не указано'} (@{message.from_user.username or 'нет'})\n"
				f"Крипта: {crypto_display}\n"
				f"Кол-во: {amount} {crypto_display}\n\n"
				f"📍 Этап: Согласование цены"
			)
			from app.keyboards import deal_alert_admin_kb
			logger_main = logging.getLogger("app.main")
			if deal_id and deal_id not in buy_deal_alerts:
				# Защита от переполнения памяти
				limit_dict_size(buy_deal_alerts, MAX_BUY_DEAL_ALERTS, "buy_deal_alerts")
				buy_deal_alerts[deal_id] = {}
			for admin_id in admin_ids:
				try:
					sent_msg = await message.bot.send_message(
						chat_id=admin_id,
						text=alert_text,
						parse_mode=ParseMode.HTML,
						reply_markup=deal_alert_admin_kb(deal_id) if deal_id else None
					)
					if deal_id:
						buy_deal_alerts[deal_id][admin_id] = sent_msg.message_id
						# Сохраняем в БД для восстановления после перезапуска
						await save_deal_alert_to_db(deal_id, admin_id, sent_msg.message_id)
				except Exception as e:
					logger_main.error(
						f"❌ ОШИБКА при отправке алерта админу {admin_id}: {type(e).__name__}: {e}",
						exc_info=True
					)
			# Для крупных сделок не отправляем полное оповещение сразу
			# Оно будет отправлено в зависимости от настройки (после реквизитов или после скриншота)
			return
		if deal_id:
			await db_local.update_buy_deal_fields(
				deal_id,
				amount=amount,
				amount_currency=amount_currency,
				currency_symbol=currency_symbol,
				total_usd=total_usd,
				status="await_confirmation"
			)
		await state.set_state(DealStates.waiting_confirmation)
		message_text = await _build_deal_message(
			country_code=selected_country,
			crypto_code=crypto_type,
			amount=amount,
			amount_currency=amount_currency,
			currency_symbol=currency_symbol,
			prompt="Согласен ❔❔❔:"
		)
		await _send_or_edit_deal_message(
			bot=message.bot,
			chat_id=message.chat.id,
			state=state,
			text=message_text,
			reply_markup=buy_deal_confirm_kb()
		)
		await try_update_large_order_alert(
			bot=message.bot,
			state=state,
			user_tg_id=message.from_user.id,
			user_name=message.from_user.full_name or "",
			user_username=message.from_user.username or ""
		)

	@dp.callback_query(F.data == "deal:confirm:no")
	async def on_deal_confirm_no(cb: CallbackQuery, state: FSMContext):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		await cb.answer()
		data = await state.get_data()
		# Сохраняем данные о выбранной стране и криптовалюте
		selected_country = data.get("selected_country", "BYN")
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		deal_id = data.get("deal_id")
		
		# Не отменяем сделку, а возвращаем к вводу количества монет
		# Обновляем статус сделки, если она существует
		if deal_id:
			deal = await db_local.get_buy_deal_by_id(deal_id)
			if deal and deal.get("status") == "await_confirmation":
				await db_local.update_buy_deal_fields(deal_id, status="await_amount")
		
		# Возвращаем пользователя к вводу количества монет
		await state.set_state(DealStates.waiting_amount)
		# Очищаем amount и amount_currency из состояния, чтобы пользователь мог ввести новое значение
		await state.update_data(amount=None, amount_currency=None)
		
		# Показываем сообщение с запросом количества монет
		message_text = await _build_deal_message(
			country_code=selected_country,
			crypto_code=crypto_type,
			amount=None,
			amount_currency=None,
			currency_symbol=None,
			prompt="Введи количество монет⬇️⬇️⬇️ :"
		)
		await _send_or_edit_deal_message(
			bot=cb.bot,
			chat_id=cb.message.chat.id,
			state=state,
			text=message_text,
			reply_markup=None
		)

	@dp.callback_query(F.data == "deal:confirm:yes")
	async def on_deal_confirm_yes(cb: CallbackQuery, state: FSMContext):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		await cb.answer()
		data = await state.get_data()
		selected_country = data.get("selected_country", "BYN")
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		amount = data.get("amount", 0)
		amount_currency = data.get("amount_currency", 0)
		currency_symbol = data.get("currency_symbol", "Br")
		deal_id = data.get("deal_id")
		# После согласия спрашиваем адрес кошелька
		await state.set_state(DealStates.waiting_wallet_address)
		message_text = await _build_deal_message(
			country_code=selected_country,
			crypto_code=crypto_type,
			amount=amount,
			amount_currency=amount_currency,
			currency_symbol=currency_symbol,
			prompt="Введи адрес кошелька⬇️⬇️⬇️ :"
		)
		await _send_or_edit_deal_message(
			bot=cb.bot,
			chat_id=cb.message.chat.id,
			state=state,
			text=message_text,
			reply_markup=None
		)
		if deal_id:
			await db_local.update_buy_deal_fields(deal_id, status="await_wallet")

	@dp.callback_query(F.data == "deal:paid")
	async def on_deal_paid(cb: CallbackQuery, state: FSMContext):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		data = await state.get_data()
		deal_id = data.get("deal_id")
		amount_currency = data.get("amount_currency")
		# Если в состоянии нет данных (могло быть очищено при переписке), берем активную сделку из БД
		if not deal_id or amount_currency is None:
			active_deal_id = await db_local.get_active_buy_deal_by_user(cb.from_user.id)
			if not active_deal_id:
				await cb.answer("Нет активной сделки.", show_alert=True)
				return
			deal = await db_local.get_buy_deal_by_id(active_deal_id)
			if not deal:
				await cb.answer("Сделка не найдена.", show_alert=True)
				return
			await state.update_data(
				deal_id=deal["id"],
				selected_country=deal.get("country_code", "BYN"),
				crypto_type=deal.get("crypto_type", ""),
				crypto_display=deal.get("crypto_display", ""),
				amount=deal.get("amount", 0),
				amount_currency=deal.get("amount_currency", 0),
				currency_symbol=deal.get("currency_symbol", "Br"),
				wallet_address=deal.get("wallet_address"),
				deal_message_id=deal.get("user_message_id"),
				order_message_id=deal.get("user_message_id"),
			)
			data = await state.get_data()
			deal_id = data.get("deal_id")
			amount_currency = data.get("amount_currency")
		if amount_currency is None:
			await cb.answer("Сначала укажите сумму сделки.", show_alert=True)
			return
		selected_country = data.get("selected_country", "BYN")
		crypto_type = data.get("crypto_type", "")
		amount = data.get("amount", 0)
		amount_currency = data.get("amount_currency", 0)
		currency_symbol = data.get("currency_symbol", "Br")
		wallet_address = data.get("wallet_address")
		deal = await db_local.get_buy_deal_by_id(deal_id) if deal_id else None
		requisites_text = await _get_deal_requisites_text(
			db_local,
			cb.from_user.id,
			selected_country
		)
		messages = await db_local.get_buy_deal_messages(deal_id) if deal_id else []
		chat_lines = _build_deal_chat_lines(messages, cb.from_user.full_name or "Пользователь")
		message_text = await _build_user_deal_with_requisites_chat_text(
			deal=deal or {
				"country_code": selected_country,
				"crypto_type": crypto_type,
				"amount": amount,
				"amount_currency": amount_currency,
				"currency_symbol": currency_symbol,
				"wallet_address": wallet_address,
			},
			requisites_text=requisites_text,
			chat_lines=chat_lines,
			prompt="❗️➡️Пришли скрин чека или фото:",
		)
		# Удаляем уведомление о получении реквизитов
		if deal and deal.get("requisites_notice_message_id"):
			try:
				await cb.bot.delete_message(
					chat_id=cb.from_user.id,
					message_id=deal["requisites_notice_message_id"]
				)
			except Exception:
				pass
			await db_local.update_buy_deal_fields(
				deal_id,
				requisites_notice_message_id=None
			)
		if deal_id:
			await db_local.update_buy_deal_fields(deal_id, status="await_proof")
		await state.set_state(DealStates.waiting_payment_proof)
		message_id = await _send_or_edit_deal_message(
			bot=cb.bot,
			chat_id=cb.message.chat.id,
			state=state,
			text=message_text,
			reply_markup=None
		)
		await state.update_data(proof_request_message_id=message_id)
		await cb.answer()

	@dp.callback_query(F.data.startswith("deal:user:reply:"))
	async def on_deal_user_reply_start(cb: CallbackQuery, state: FSMContext):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		await cb.answer()
		try:
			deal_id = int(cb.data.split(":")[3])
		except (ValueError, IndexError):
			await cb.answer("Ошибка данных", show_alert=True)
			return
		deal = await db_local.get_buy_deal_by_id(deal_id)
		if not deal:
			await cb.answer("Сделка не найдена", show_alert=True)
			return
		try:
			from app.notifications import notification_ids
			notification_key = (cb.from_user.id, deal_id, "deal")
			if notification_key in notification_ids:
				try:
					await cb.bot.delete_message(chat_id=cb.from_user.id, message_id=notification_ids[notification_key])
				except Exception:
					pass
				del notification_ids[notification_key]
		except Exception:
			pass
		await state.set_state(DealUserReplyStates.waiting_reply)
		try:
			messages = await db_local.get_buy_deal_messages(deal_id)
			chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
			requisites_text = await _get_deal_requisites_text(
				db_local,
				deal.get("user_tg_id"),
				deal.get("country_code")
			)
			alert_threshold = 400.0
			try:
				alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
				alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
			except (ValueError, TypeError):
				alert_threshold = 400.0
			is_large_order = (deal.get("total_usd") or 0) >= alert_threshold
			admin_amount_set = bool(deal.get("admin_amount_set"))
			hide_requisites = is_large_order and not admin_amount_set
			prompt_text = "➡️Введи сообщение администратору:"
			if hide_requisites:
				user_text = await _build_user_deal_with_requisites_chat_text(
					deal=deal,
					requisites_text=requisites_text,
					chat_lines=chat_lines,
					prompt=prompt_text,
					amount_currency_override=None,
					show_requisites=False,
				)
			elif requisites_text:
				user_text = await _build_user_deal_with_requisites_chat_text(
					deal=deal,
					requisites_text=requisites_text,
					chat_lines=chat_lines,
					prompt=prompt_text,
				)
			else:
				user_text = _append_prompt(await _build_user_deal_chat_text(deal, chat_lines), prompt_text)
			if deal.get("user_message_id"):
				await cb.bot.edit_message_text(
					chat_id=cb.from_user.id,
					message_id=deal["user_message_id"],
					text=user_text,
					parse_mode="HTML",
					reply_markup=cb.message.reply_markup
				)
		except Exception:
			pass
		try:
			prompt = await cb.bot.send_message(
				chat_id=cb.from_user.id,
				text="✍️ Напишите сообщение для администратора:",
				reply_markup=ForceReply(selective=True)
			)
			await delete_message_after_delay(cb.bot, cb.from_user.id, prompt.message_id, 15.0)
		except TelegramNetworkError as e:
			logging.getLogger("app.main").warning(
				f"⚠️ Сеть недоступна при запросе сообщения пользователя: {e}. Повторить позже."
			)
			await state.clear()
			return
		await state.update_data(deal_id=deal_id, deal_reply_prompt_id=prompt.message_id)

	@dp.callback_query(F.data.startswith("deal:user:how_pay:") & ~F.data.startswith("deal:user:how_pay:delete:"))
	async def on_deal_user_how_pay(cb: CallbackQuery):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		try:
			deal_id = int(cb.data.split(":")[3])
		except (ValueError, IndexError):
			await cb.answer("Ошибка данных", show_alert=True)
			return
		deal = await db_local.get_buy_deal_by_id(deal_id)
		if not deal or deal.get("user_tg_id") != cb.from_user.id:
			await cb.answer("Сделка не найдена", show_alert=True)
			return
		instruction_text = (
			"Инстукция пополнения ЕРИП через аппарат Беларусбанка НАЛИЧНЫМИ!!:\n\n"
			"1. Выбираем «Платежи наличными»\n\n"
			"2. Нажимаем «Зарегестрироваться» (регестрируемся один раз, потом при следующих операциях "
			"нажимаем уже «ВОЙТИ»)\n\n"
			"3. Вводим номер телефона, на который придет смс с паролем (пароль сохраняем, так как он "
			"будет всегда тот же при последующих пополнениях)\n\n"
			"4. Вводим пароль из смс\n\n"
			"5. Нажимаем «Добавить платеж» после чего открывается дерево ЕРИП и дальше уже все просто!"
		)
		support_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "IMG", "support"))
		try:
			image_files = sorted(
				f for f in os.listdir(support_dir)
				if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
			)
		except Exception:
			image_files = []
		kb = InlineKeyboardBuilder()
		kb.button(text="🗑️ Удалить", callback_data=f"deal:user:how_pay:delete:{deal_id}")
		media_ids = []
		if not image_files:
			try:
				instruction_msg = await cb.bot.send_message(
					chat_id=cb.from_user.id,
					text=instruction_text,
					reply_markup=kb.as_markup()
				)
				media_ids.append(instruction_msg.message_id)
			except Exception:
				await cb.answer("Не удалось отправить инструкцию", show_alert=True)
				return
			await cb.answer()
			return
		media = []
		for idx, filename in enumerate(image_files[:6]):
			path = os.path.join(support_dir, filename)
			caption = instruction_text if idx == 0 else None
			media.append(InputMediaPhoto(media=FSInputFile(path), caption=caption))
		try:
			sent_media = await cb.bot.send_media_group(chat_id=cb.from_user.id, media=media)
			media_ids.extend([m.message_id for m in sent_media])
		except Exception:
			await cb.answer("Не удалось отправить инструкцию", show_alert=True)
			return
		try:
			delete_msg = await cb.bot.send_message(
				chat_id=cb.from_user.id,
				text="Нажмите кнопку, чтобы удалить инструкцию:",
				reply_markup=kb.as_markup()
			)
			media_ids.append(delete_msg.message_id)
		except Exception:
			pass
		try:
			from app.notifications import notification_ids
			notification_ids[(cb.from_user.id, deal_id, "how_pay")] = media_ids
		except Exception:
			pass
		await cb.answer()

	@dp.callback_query(F.data.startswith("deal:user:how_pay:delete:"))
	async def on_deal_user_how_pay_delete(cb: CallbackQuery):
		if not cb.from_user:
			return
		parts = cb.data.split(":")
		if len(parts) < 5:
			await cb.answer("Ошибка данных", show_alert=True)
			return
		try:
			deal_id = int(parts[-1])
		except (ValueError, IndexError):
			await cb.answer("Ошибка данных", show_alert=True)
			return
		try:
			from app.notifications import notification_ids
			key = (cb.from_user.id, deal_id, "how_pay")
			message_ids = notification_ids.get(key, [])
			for message_id in message_ids:
				try:
					await cb.bot.delete_message(chat_id=cb.from_user.id, message_id=message_id)
				except Exception:
					pass
			if key in notification_ids:
				del notification_ids[key]
		except Exception:
			pass
		await cb.answer("Удалено")

	@dp.message(DealStates.waiting_wallet_address, ~F.text.startswith("/"))
	async def on_deal_wallet_address_entered(message: Message, state: FSMContext):
		global buy_deal_alerts
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		wallet_address = (message.text or "").strip()
		if not wallet_address:
			await message.answer("❌ Введите корректный адрес кошелька.")
			return
		data = await state.get_data()
		crypto_type = data.get("crypto_type", "")
		
		# Валидация адреса кошелька
		from app.validators import validate_wallet_address
		is_valid, error_msg = validate_wallet_address(wallet_address, crypto_type)
		if not is_valid:
			await message.answer(error_msg)
			return
		await delete_user_message(message)
		await state.update_data(wallet_address=wallet_address)
		is_large_deal = data.get("is_large_deal")
		selected_country = data.get("selected_country", "BYN")
		crypto_display = data.get("crypto_display", "")
		amount = data.get("amount", 0)
		amount_currency = data.get("amount_currency", 0)
		currency_symbol = data.get("currency_symbol", "Br")
		deal_id = data.get("deal_id")
		if is_large_deal:
			if deal_id:
				await db_local.update_buy_deal_fields(
					deal_id,
					wallet_address=wallet_address,
					status="await_admin",
				)
			message_text = await _build_deal_message(
				country_code=selected_country,
				crypto_code=crypto_type,
				amount=amount,
				amount_currency=None,
				currency_symbol=currency_symbol,
				prompt="❗️Ожидай сообщение от администратора",
				requisites_text=None,
				wallet_address=wallet_address,
				show_empty_amount=True
			)
			await _send_or_edit_deal_message(
				bot=message.bot,
				chat_id=message.chat.id,
				state=state,
				text=message_text,
				reply_markup=None
			)
			if deal_id:
				await update_buy_deal_alert(message.bot, deal_id)
			return
		requisites_text = await _get_deal_requisites_text(
			db_local,
			message.from_user.id,
			selected_country
		)
		if deal_id:
			await db_local.update_buy_deal_fields(
				deal_id,
				wallet_address=wallet_address,
				status="await_payment" if requisites_text else "await_requisites",
				amount=amount,
				amount_currency=amount_currency,
				currency_symbol=currency_symbol
			)
		message_text = await _build_deal_message(
			country_code=selected_country,
			crypto_code=crypto_type,
			amount=amount,
			amount_currency=amount_currency,
			currency_symbol=currency_symbol,
			prompt=None,
			requisites_text=requisites_text,
			wallet_address=wallet_address
		)
		if requisites_text:
			await state.set_state(DealStates.waiting_payment)
			await _send_or_edit_deal_message(
				bot=message.bot,
				chat_id=message.chat.id,
				state=state,
				text=message_text,
				reply_markup=buy_deal_paid_reply_kb(deal_id, show_how_pay=True)
			)
			# Проверяем настройку оповещений и отправляем оповещение, если нужно
			if deal_id:
				notification_type = await db_local.get_setting("deal_notification_type", "after_proof")
				logger_main = logging.getLogger("app.main")
				logger_main.info(f"🔔 on_deal_wallet_address_entered: notification_type={notification_type}, deal_id={deal_id}, requisites_text exists")
				if notification_type == "after_requisites":
					# Отправляем оповещение админам после выдачи реквизитов
					from app.main import buy_deal_alerts, _build_admin_open_deal_text, _get_admin_user_financial_lines, _get_deal_requisites_label, _build_deal_chat_lines
					from app.keyboards import deal_alert_admin_kb
					from app.di import get_admin_ids
					
					# Проверяем, есть ли уже оповещение
					message_ids = buy_deal_alerts.get(deal_id, {})
					logger_main.info(f"🔔 on_deal_wallet_address_entered: buy_deal_alerts[{deal_id}]={message_ids}")
					
					if not message_ids:
						# Если оповещения еще нет, создаем его
						admin_ids = get_admin_ids()
						logger_main.info(f"🔔 on_deal_wallet_address_entered: создаем новое оповещение для deal_id={deal_id}, admin_ids={admin_ids}")
						if admin_ids:
							financial_lines = await _get_admin_user_financial_lines(db_local, message.from_user.id)
							requisites_label = await _get_deal_requisites_label(
								db_local,
								message.from_user.id,
								selected_country
							)
							deal_messages = await db_local.get_buy_deal_messages(deal_id)
							chat_lines = _build_deal_chat_lines(deal_messages, message.from_user.full_name or "Пользователь")
							deal = await db_local.get_buy_deal_by_id(deal_id)
							if deal:
								alert_text = await _build_admin_open_deal_text(deal, requisites_label, chat_lines, financial_lines, db_local)
								reply_markup = deal_alert_admin_kb(deal_id)
								
								# Защита от переполнения памяти
								from app.main import limit_dict_size, MAX_BUY_DEAL_ALERTS
								limit_dict_size(buy_deal_alerts, MAX_BUY_DEAL_ALERTS, "buy_deal_alerts")
								buy_deal_alerts[deal_id] = {}
								
								for admin_id in admin_ids:
									try:
										sent = await message.bot.send_message(
											chat_id=admin_id,
											text=alert_text,
											parse_mode="HTML",
											reply_markup=reply_markup
										)
										buy_deal_alerts[deal_id][admin_id] = sent.message_id
										# Сохраняем в БД для восстановления после перезапуска
										from app.main import save_deal_alert_to_db
										await save_deal_alert_to_db(deal_id, admin_id, sent.message_id)
										logger_main.info(f"✅ on_deal_wallet_address_entered: оповещение отправлено админу {admin_id}, message_id={sent.message_id}")
									except Exception as e:
										logger_main.warning(f"⚠️ Ошибка отправки оповещения админу {admin_id}: {e}")
					else:
						# Обновляем существующее оповещение
						logger_main.info(f"🔔 on_deal_wallet_address_entered: обновляем существующее оповещение для deal_id={deal_id}")
						await update_buy_deal_alert(message.bot, deal_id)
		else:
			# Реквизитов нет - отправляем оповещение админу
			await state.set_state(DealStates.waiting_payment)
			message_id = await _send_or_edit_deal_message(
				bot=message.bot,
				chat_id=message.chat.id,
				state=state,
				text=message_text,
				reply_markup=None
			)
			await db_local.save_pending_requisites(
				user_tg_id=message.from_user.id,
				message_id=message_id,
				crypto_type=crypto_type or "BTC",
				crypto_display=crypto_display or crypto_type,
				amount=amount,
				final_amount=amount_currency,
				currency_symbol=currency_symbol,
				wallet_address=wallet_address
			)
			
			# Отправляем оповещение админу о сделке без реквизитов
			if deal_id:
				from app.di import get_admin_ids
				from app.keyboards import deal_alert_admin_kb
				from app.main import _build_admin_open_deal_text, _get_admin_user_financial_lines, _get_deal_requisites_label, _build_deal_chat_lines, limit_dict_size, MAX_BUY_DEAL_ALERTS, save_deal_alert_to_db
				
				admin_ids = get_admin_ids()
				if admin_ids:
					deal = await db_local.get_buy_deal_by_id(deal_id)
					if deal:
						financial_lines = await _get_admin_user_financial_lines(db_local, message.from_user.id)
						requisites_text_check = await _get_deal_requisites_text(
							db_local,
							message.from_user.id,
							selected_country
						)
						# Проверяем, есть ли реквизиты
						if not requisites_text_check or not requisites_text_check.strip():
							# Реквизитов нет - добавляем заметную пометку
							requisites_label = "❗️❗️❗️❗️⬇️⬇️⬇️⬇️❗️❗️❗️❗️\n⚠️ У ПОЛЬЗОВАТЕЛЯ НЕТ РЕКВИЗИТОВ ⚠️\n❗️❗️❗️❗️⬆️⬆️⬆️⬆️❗️❗️❗️❗️"
						else:
							# Реквизиты есть - получаем обычный label
							requisites_label = await _get_deal_requisites_label(
								db_local,
								message.from_user.id,
								selected_country
							)
						
						messages = await db_local.get_buy_deal_messages(deal_id)
						chat_lines = _build_deal_chat_lines(messages, message.from_user.full_name or "Пользователь")
						
						alert_text = await _build_admin_open_deal_text(
							deal,
							requisites_label,
							chat_lines,
							financial_lines,
							db_local
						)
						
						# Защита от переполнения памяти
						limit_dict_size(buy_deal_alerts, MAX_BUY_DEAL_ALERTS, "buy_deal_alerts")
						
						if deal_id not in buy_deal_alerts:
							buy_deal_alerts[deal_id] = {}
						
						reply_markup = deal_alert_admin_kb(deal_id)
						
						for admin_id in admin_ids:
							try:
								sent = await message.bot.send_message(
									chat_id=admin_id,
									text=alert_text,
									parse_mode="HTML",
									reply_markup=reply_markup
								)
								buy_deal_alerts[deal_id][admin_id] = sent.message_id
								# Сохраняем в БД для восстановления после перезапуска
								await save_deal_alert_to_db(deal_id, admin_id, sent.message_id)
								logger_main = logging.getLogger("app.main")
								logger_main.info(f"✅ Оповещение о сделке без реквизитов отправлено админу {admin_id}, deal_id={deal_id}, message_id={sent.message_id}")
							except Exception as e:
								logger_main = logging.getLogger("app.main")
								logger_main.warning(f"⚠️ Ошибка отправки оповещения админу {admin_id}: {e}")

	@dp.callback_query(F.data.startswith("deal:user:delete:"))
	async def on_deal_user_delete(cb: CallbackQuery):
		if not cb.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			return
		await cb.answer()
		try:
			deal_id = int(cb.data.split(":")[3])
		except (ValueError, IndexError):
			await cb.answer("Ошибка данных", show_alert=True)
			return
		deal = await db_local.get_buy_deal_by_id(deal_id)
		if not deal:
			await cb.answer("Сделка не найдена", show_alert=True)
			return
		if deal.get("user_message_id"):
			try:
				await cb.bot.delete_message(
					chat_id=cb.from_user.id,
					message_id=deal["user_message_id"]
				)
			except Exception:
				pass
			await db_local.update_buy_deal_user_message_id(deal_id, None)

	@dp.message(DealUserReplyStates.waiting_reply)
	async def on_deal_user_reply_send(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		reply_text = message.text or message.caption or ""
		if not reply_text.strip():
			return
		
		# Валидация текста сообщения
		from app.validators import validate_text, sanitize_text
		is_valid, error_msg = validate_text(reply_text, max_length=4096, min_length=1)
		if not is_valid:
			await message.answer(error_msg)
			return
		
		# Очистка текста от опасных символов
		reply_text = sanitize_text(reply_text)
		
		await delete_user_message(message)
		data = await state.get_data()
		deal_id = data.get("deal_id")
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"🔵 on_deal_user_reply_send: deal_id={deal_id}, user_id={message.from_user.id}, text={reply_text[:50]}")
		if not deal_id:
			await state.clear()
			return
		await db_local.add_buy_deal_message(deal_id, "user", reply_text)
		logger_main.info(f"🔵 on_deal_user_reply_send: message saved to buy_deal_messages, deal_id={deal_id}")
		try:
			if message.from_user.id in large_order_alerts:
				user_data = large_order_alerts.get(message.from_user.id)
				question_id = None
				if isinstance(user_data, dict) and "question_id" in user_data:
					question_id = user_data.get("question_id")
				if question_id:
					await db_local.add_question_message(question_id, "user", reply_text)
		except Exception:
			pass
		deal = await db_local.get_buy_deal_by_id(deal_id)
		if not deal:
			await state.clear()
			return
		try:
			from app.notifications import notification_ids
			notification_key = (message.from_user.id, deal_id, "deal")
			if notification_key in notification_ids:
				try:
					await message.bot.delete_message(chat_id=message.from_user.id, message_id=notification_ids[notification_key])
				except Exception:
					pass
				del notification_ids[notification_key]
		except Exception:
			pass
		messages = await db_local.get_buy_deal_messages(deal_id)
		logger_main.info(f"🔵 on_deal_user_reply_send: got {len(messages)} messages from DB for deal_id={deal_id}")
		chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
		logger_main.info(f"🔵 on_deal_user_reply_send: chat_lines count={len(chat_lines)}")
		requisites_text = await _get_deal_requisites_text(
			db_local,
			deal.get("user_tg_id"),
			deal.get("country_code")
		)
		alert_threshold = 400.0
		try:
			alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
			alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
		except (ValueError, TypeError):
			alert_threshold = 400.0
		is_large_order = (deal.get("total_usd") or 0) >= alert_threshold
		admin_amount_set = bool(deal.get("admin_amount_set"))
		hide_requisites = is_large_order and not admin_amount_set
		logger_main.info(f"🔵 on_deal_user_reply_send: is_large_order={is_large_order}, admin_amount_set={admin_amount_set}, hide_requisites={hide_requisites}")
		if deal.get("status") == "completed":
			hide_requisites = True
		prompt_wallet = "➡️Введи адрес кошелька:" if deal.get("status") == "await_wallet" else None
		if hide_requisites:
			user_text = await _build_user_deal_with_requisites_chat_text(
				deal=deal,
				requisites_text=requisites_text,
				chat_lines=chat_lines,
				amount_currency_override=None,
				show_requisites=False,
				prompt=prompt_wallet,
			)
		elif requisites_text:
			user_text = await _build_user_deal_with_requisites_chat_text(
				deal=deal,
				requisites_text=requisites_text,
				chat_lines=chat_lines,
				prompt=prompt_wallet,
			)
		else:
			user_text = _append_prompt(await _build_user_deal_chat_text(deal, chat_lines), prompt_wallet)
		from app.keyboards import buy_deal_user_reply_kb, buy_deal_paid_reply_kb
		show_how_pay = bool(requisites_text) and not hide_requisites
		reply_markup = buy_deal_user_reply_kb(deal_id, show_how_pay=show_how_pay)
		if deal.get("status") == "await_payment":
			reply_markup = buy_deal_paid_reply_kb(deal_id, show_how_pay=show_how_pay)
		try:
			if deal.get("user_message_id"):
				await message.bot.edit_message_text(
					chat_id=message.from_user.id,
					message_id=deal["user_message_id"],
					text=user_text,
					parse_mode="HTML",
					reply_markup=reply_markup
				)
			else:
				sent = await message.bot.send_message(
					chat_id=message.from_user.id,
					text=user_text,
					parse_mode="HTML",
					reply_markup=reply_markup
				)
				await db_local.update_buy_deal_user_message_id(deal_id, sent.message_id)
		except Exception as e:
			logger_main.warning(f"⚠️ on_deal_user_reply_send: error updating user message: {e}")
		# Обновляем алерт сделки для админа
		# Для крупных сделок используем update_buy_deal_alert, которая берет актуальные message_ids из buy_deal_alerts[deal_id]
		logger_main.info(f"🔵 on_deal_user_reply_send: calling update_buy_deal_alert for deal_id={deal_id}")
		try:
			await update_buy_deal_alert(message.bot, deal_id)
			logger_main.info(f"✅ on_deal_user_reply_send: update_buy_deal_alert completed for deal_id={deal_id}")
		except Exception as e:
			logger_main.error(f"❌ on_deal_user_reply_send: error in update_buy_deal_alert: {type(e).__name__}: {e}", exc_info=True)
		prompt_id = data.get("deal_reply_prompt_id")
		if prompt_id:
			try:
				await message.bot.delete_message(chat_id=message.from_user.id, message_id=prompt_id)
			except Exception:
				pass
		if deal.get("status") == "await_wallet":
			await state.set_state(DealStates.waiting_wallet_address)
		else:
			await state.clear()

	@dp.message(DealStates.waiting_payment_proof)
	async def on_deal_payment_proof_received(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		has_photo = message.photo is not None and len(message.photo) > 0
		has_document = message.document is not None
		if not has_photo and not has_document:
			await delete_user_message(message)
			await message.answer("❌ Пожалуйста, отправьте скриншот перевода или чек оплаты (фото или документ).")
			return
		await delete_user_message(message)
		data = await state.get_data()
		amount = data.get("amount", 0)
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		amount_currency = data.get("amount_currency", 0)
		currency_symbol = data.get("currency_symbol", "")
		delivery_method = data.get("delivery_method", "normal")
		total_usd = data.get("total_usd", 0)
		alert_threshold = data.get("alert_threshold", 400.0)
		wallet_address = data.get("wallet_address", "не указан")
		proof_photo_file_id = None
		proof_document_file_id = None
		if has_photo:
			proof_photo_file_id = message.photo[-1].file_id
		elif has_document:
			proof_document_file_id = message.document.file_id
		user_name = message.from_user.full_name or ""
		user_username = message.from_user.username or ""
		order_message_id = data.get("order_message_id")
		proof_request_message_id = data.get("proof_request_message_id")
		amount_str = _format_crypto_amount(amount)
		proof_details = (
			f"\n\nКоличество монет: {amount_str} {crypto_display}\n"
			f"Сумма к оплате: {int(amount_currency)} {currency_symbol}"
		)
		proof_confirmation_message_id = None
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
		if order_message_id:
			await db_local.update_order_user_message_id(order_id, order_message_id)
		deal_id = data.get("deal_id")
		if deal_id:
			await db_local.update_buy_deal_fields(
				deal_id,
				status="await_proof",
				order_id=order_id,
				proof_photo_file_id=proof_photo_file_id,
				proof_document_file_id=proof_document_file_id
			)
			deal = await db_local.get_buy_deal_by_id(deal_id)
			if deal:
				requisites_text = await _get_deal_requisites_text(
					db_local,
					deal.get("user_tg_id"),
					deal.get("country_code")
				)
				messages = await db_local.get_buy_deal_messages(deal_id)
				chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
				user_text = await _build_user_deal_with_requisites_chat_text(
					deal=deal,
					requisites_text=requisites_text,
					chat_lines=chat_lines,
					prompt="🖼 Скриншот получен. ⏳Обработка..."
				)
				from app.keyboards import buy_deal_user_reply_kb
				show_how_pay = bool(requisites_text)
				try:
					if deal.get("user_message_id"):
						await message.bot.edit_message_text(
							chat_id=message.from_user.id,
							message_id=deal["user_message_id"],
							text=user_text,
							parse_mode="HTML",
							reply_markup=buy_deal_user_reply_kb(deal_id, show_how_pay=show_how_pay)
						)
				except Exception:
					pass
		order = await db_local.get_order_by_id(order_id)
		order_number = order["order_number"] if order else order_id
		user_tg_id = message.from_user.id
		user_debts = await db_local.get_user_total_debt(user_tg_id)
		total_debt_info = ""
		if user_debts:
			debt_lines = [f"{int(debt_sum)} {curr}" for curr, debt_sum in user_debts.items()]
			if debt_lines:
				total_debt_info = f"\n💳 Общий долг пользователя: {', '.join(debt_lines)}"
		last_order_info = ""
		try:
			user_id = await db_local.get_user_id_by_tg(user_tg_id)
			if user_id:
				user_data = await db_local.get_user_by_id(user_id)
				if user_data:
					last_order_id = user_data.get("last_order_id")
					last_order_profit = user_data.get("last_order_profit")
					if last_order_id:
						last_order = await db_local.get_order_by_id(last_order_id)
						if last_order:
							last_created_at = last_order.get("created_at")
							last_order_date = datetime.fromtimestamp(last_created_at).strftime("%d.%m.%Y %H:%M") if last_created_at else "неизвестно"
							last_order_info = f"\n📦 Последнее обращение: {last_order_date}"
							if last_order_profit is not None:
								try:
									profit_formatted = f"{int(round(last_order_profit)):,}".replace(",", " ")
									last_order_info += f"\n💰 Профит от последней сделки: {profit_formatted} USD"
								except (ValueError, TypeError):
									last_order_info += f"\n💰 Профит от последней сделки: {last_order_profit} USD"
					monthly_profit = await db_local.get_user_monthly_profit(user_tg_id)
					if monthly_profit is not None:
						try:
							monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit_formatted} USD"
						except (ValueError, TypeError):
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit} USD"
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.debug(f"Ошибка получения информации о последней сделке при создании заявки: {e}", exc_info=True)
		card_name = ""
		group_name = ""
		user_cards = await db_local.get_cards_for_user_tg(user_tg_id)
		if user_cards:
			card = user_cards[0]
			card_id = card["card_id"]
			card_info = await db_local.get_card_by_id(card_id)
			card_name = (card_info.get("name") if card_info else None) or card.get("card_name") or card.get("name") or ""
			if card_info and card_info.get("group_id"):
				group = await db_local.get_card_group_by_id(card_info["group_id"])
				group_name = group.get("name") if group else ""
		pay_card_info = f"\n💳 Карта для оплаты: {group_name} ({card_name})" if card_name and group_name else (f"\n💳 Карта для оплаты: {card_name}" if card_name else "")
		is_large_order = total_usd >= alert_threshold if total_usd > 0 else False
		large_order_info = f"\n🚨 <b>КРУПНАЯ СДЕЛКА</b> ({total_usd:.2f} USD)" if is_large_order and total_usd > 0 else (f"\n🚨 <b>КРУПНАЯ СДЕЛКА</b>" if is_large_order else "")
		delivery_info = "\n🚀 Доставка: <b>VIP</b>" if delivery_method == "vip" else ("\n📦 Доставка: Обычная" if delivery_method == "normal" else "")
		admin_message_text = (
			f"Номер заявки за сегодня: {order_number}\n"
			f"Имя пользователя: {user_name or 'Не указано'}\n"
			f"Username: @{user_username}\n"
			f"🆔 ID: <code>{user_tg_id}</code>{last_order_info}{large_order_info}\n\n"
			f"Количество монет: {amount_str} {crypto_display}\n"
			f"Сумма к оплате: {int(amount_currency)} {currency_symbol}{delivery_info}\n"
			f"Адрес кошелька: <code>{wallet_address}</code>{pay_card_info}{total_debt_info}"
		)
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		if not admin_ids:
			logger_main.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Список админов пустой! Заявка не будет отправлена.")
			await message.bot.send_message(
				chat_id=message.chat.id,
				text="⚠️ Произошла ошибка при отправке заявки администраторам. Пожалуйста, свяжитесь с поддержкой."
			)
		else:
			for admin_id in admin_ids:
				try:
					# Обновляем/создаем сообщение сделки у админа с добавлением скрина
					if deal_id:
						alert_text = await build_admin_open_deal_text_with_chat(
							db_local, deal_id
						)
						caption = alert_text
						from app.keyboards import deal_alert_admin_kb
						if proof_photo_file_id:
							sent_alert = await message.bot.send_photo(
								chat_id=admin_id,
								photo=proof_photo_file_id,
								caption=caption,
								parse_mode="HTML",
								reply_markup=deal_alert_admin_kb(deal_id)
							)
						elif proof_document_file_id:
							sent_alert = await message.bot.send_document(
								chat_id=admin_id,
								document=proof_document_file_id,
								caption=caption,
								parse_mode="HTML",
								reply_markup=deal_alert_admin_kb(deal_id)
							)
						else:
							sent_alert = None
						if sent_alert:
							# Если уже было сообщение сделки — удаляем старое
							if deal_id in buy_deal_alerts and admin_id in buy_deal_alerts[deal_id]:
								try:
									await message.bot.delete_message(
										chat_id=admin_id,
										message_id=buy_deal_alerts[deal_id][admin_id]
									)
								except Exception:
									pass
							buy_deal_alerts.setdefault(deal_id, {})[admin_id] = sent_alert.message_id

					if not deal_id:
						if proof_photo_file_id:
							proof_msg = await message.bot.send_photo(
								chat_id=admin_id,
								photo=proof_photo_file_id,
								caption=admin_message_text,
								parse_mode=ParseMode.HTML,
								reply_markup=order_action_kb(order_id)
							)
						elif proof_document_file_id:
							proof_msg = await message.bot.send_document(
								chat_id=admin_id,
								document=proof_document_file_id,
								caption=admin_message_text,
								parse_mode=ParseMode.HTML,
								reply_markup=order_action_kb(order_id)
							)
						else:
							proof_msg = await message.bot.send_message(
								chat_id=admin_id,
								text=admin_message_text,
								parse_mode=ParseMode.HTML,
								reply_markup=order_action_kb(order_id)
							)
						await db_local.update_order_admin_message_id(order_id, proof_msg.message_id)
				except Exception as e:
					logger_main.error(f"❌ Ошибка отправки заявки #{order_number} админу {admin_id}: {e}", exc_info=True)
		# Отправляем уведомление админу о получении скриншота (как ответ на сообщение сделки)
		logger_main = logging.getLogger("app.main")
		if deal_id:
			admin_ids = get_admin_ids()
			if admin_ids:
				# Получаем message_id сообщения сделки для каждого админа
				for admin_id in admin_ids:
					try:
						deal_alert_message_id = None
						if deal_id in buy_deal_alerts and admin_id in buy_deal_alerts[deal_id]:
							deal_alert_message_id = buy_deal_alerts[deal_id][admin_id]
						
						if deal_alert_message_id:
							# Отправляем уведомление как ответ на сообщение сделки
							notification_text = "🔔 Получен скриншот оплаты"
							notification_msg = await message.bot.send_message(
								chat_id=admin_id,
								text=notification_text,
								reply_to_message_id=deal_alert_message_id
							)
							# Сохраняем message_id уведомления для последующего удаления
							proof_notification_ids.setdefault(deal_id, {})[admin_id] = notification_msg.message_id
							logger_main.info(f"✅ Уведомление о скриншоте отправлено админу {admin_id} как ответ на сообщение сделки (message_id={deal_alert_message_id}, notification_id={notification_msg.message_id})")
						else:
							logger_main.warning(f"⚠️ Не найден message_id сообщения сделки для админа {admin_id}, deal_id={deal_id}")
					except Exception as e:
						logger_main.warning(f"⚠️ Ошибка отправки уведомления админу {admin_id}: {e}")
		
		# Проверяем настройку оповещений и отправляем оповещение, если нужно
		if deal_id:
			notification_type = await db_local.get_setting("deal_notification_type", "after_proof")
			logger_main.info(f"🔔 on_payment_proof_received: notification_type={notification_type}, deal_id={deal_id}")
			if notification_type == "after_proof":
				# Отправляем оповещение админам после отправки скриншота
				logger_main.info(f"🔔 on_payment_proof_received: отправляем оповещение после скриншота")
				await update_buy_deal_alert(message.bot, deal_id)
			else:
				# Если настройка "after_requisites", оповещение уже было отправлено после выдачи реквизитов
				# Просто обновляем существующее оповещение (если есть)
				logger_main.info(f"🔔 on_payment_proof_received: настройка не 'after_proof', обновляем существующее оповещение")
				await update_buy_deal_alert(message.bot, deal_id)
		await state.clear()

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
		
		if current_state == QuestionStates.waiting_question:
			# Если пользователь в процессе ввода вопроса, возвращаем в главное меню
			await state.clear()
			await send_and_save_message(message, "Выберите действие:", reply_markup=client_menu_kb(), state=state)
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
		current_state = await state.get_state()
		if current_state and "DealStates" in str(current_state):
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

	# Обработчики для продажи (должны быть ПЕРЕД обработчиками покупки)
	@dp.message(SellStates.selecting_crypto, F.text.in_({"Bitcoin - BTC", "Litecoin - LTC", "USDT - TRC20", "Monero - XMR"}))
	async def on_sell_crypto_selected(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		await delete_user_message(message)
		crypto_name = message.text
		if " - " in crypto_name:
			crypto_display = crypto_name.split(" - ")[0]
		else:
			crypto_display = crypto_name
		
		await state.update_data(selected_crypto=crypto_name, crypto_display=crypto_display)
		await state.set_state(SellStates.waiting_amount)
		await send_and_save_message(message, f"✅ Введите сумму в {crypto_display}, которую хотите продать:", state=state)

	@dp.message(F.text.in_({"Bitcoin - BTC", "Litecoin - LTC", "USDT - TRC20", "Monero - XMR"}))
	async def on_buy_crypto_selected(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		current_state = await state.get_state()
		if current_state and "DealStates" in str(current_state):
			return
		
		# Проверяем, что мы не в состоянии продажи
		current_state = await state.get_state()
		if current_state and "SellStates" in str(current_state):
			return  # Пропускаем, если в состоянии продажи
		
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
		
		# Проверяем лимит на создание сделок (защита от массового создания)
		from app.rate_limiter import check_deal_creation_limit
		is_allowed, wait_time = await check_deal_creation_limit(message.from_user.id)
		if not is_allowed:
			logger_main = logging.getLogger("app.main")
			logger_main.warning(f"⚠️ Deal creation limit exceeded: user_id={message.from_user.id}, wait={wait_time:.1f}s")
			await message.answer(
				f"⏳ Превышен лимит создания сделок. Подождите {int(wait_time)} секунд перед созданием новой сделки.",
				show_alert=False
			)
			return
		
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Проверяем, не является ли это командой - если да, пропускаем обработку
		# чтобы команда обработалась в своем обработчике
		if message.text and message.text.startswith("/"):
			return  # Пропускаем команды, они обработаются в своих обработчиках
		
		# Проверяем, что мы действительно в состоянии покупки, а не продажи
		current_state = await state.get_state()
		if current_state and "SellStates" in str(current_state):
			return  # Пропускаем, если в состоянии продажи
		
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
		# Удаляем предыдущее сообщение об ошибке минимальной суммы, если оно есть
		min_amount_error_message_id = data.get("min_amount_error_message_id")
		if min_amount_error_message_id:
			await delete_previous_bot_message(message.bot, message.chat.id, min_amount_error_message_id)
			await state.update_data(min_amount_error_message_id=None)
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
		if selected_country == "BYN":
			usd_to_currency_rate_str = await db_local.get_setting("buy_usd_to_byn_rate", "2.97")
			try:
				usd_to_currency_rate = float(usd_to_currency_rate_str) if usd_to_currency_rate_str else 2.97
			except (ValueError, TypeError):
				usd_to_currency_rate = 2.97
			currency_symbol = "Br"
		else:  # RUB
			usd_to_currency_rate_str = await db_local.get_setting("buy_usd_to_rub_rate", "95")
			try:
				usd_to_currency_rate = float(usd_to_currency_rate_str) if usd_to_currency_rate_str else 95.0
			except (ValueError, TypeError):
				usd_to_currency_rate = 95.0
			currency_symbol = "₽"
		
		# Рассчитываем сумму заказа в USD для определения процента наценки
		amount_usd = amount * crypto_price_usd
		
		# Проверяем минимальную сумму сделки
		min_usd_str = await db_local.get_setting("buy_min_usd", "15")
		try:
			min_usd = float(min_usd_str) if min_usd_str else 15.0
		except (ValueError, TypeError):
			min_usd = 15.0
		if amount_usd < min_usd:
			error_message = await send_and_save_message(
				message,
				f"❌ Минимальная сумма сделки {min_usd}$.\n"
				f"Введите сумму больше {min_usd}$:",
				state=state
			)
			# Сохраняем ID сообщения об ошибке для последующего удаления
			await state.update_data(min_amount_error_message_id=error_message.message_id)
			return
		
		# Определяем процент наценки в зависимости от суммы заказа
		if amount_usd <= 100:
			markup_percent_key = "buy_markup_percent_small"
			default_markup = 20
		elif amount_usd <= 449:
			markup_percent_key = "buy_markup_percent_101_449"
			default_markup = 15
		elif amount_usd <= 699:
			markup_percent_key = "buy_markup_percent_450_699"
			default_markup = 14
		elif amount_usd <= 999:
			markup_percent_key = "buy_markup_percent_700_999"
			default_markup = 13
		elif amount_usd <= 1499:
			markup_percent_key = "buy_markup_percent_1000_1499"
			default_markup = 12
		elif amount_usd <= 1999:
			markup_percent_key = "buy_markup_percent_1500_1999"
			default_markup = 11
		else:
			markup_percent_key = "buy_markup_percent_2000_plus"
			default_markup = 10
		
		# Получаем процент наценки из БД
		markup_percent_str = await db_local.get_setting(markup_percent_key, str(default_markup))
		try:
			markup_percent = float(markup_percent_str) if markup_percent_str else default_markup
		except (ValueError, TypeError):
			markup_percent = default_markup
		
		# Рассчитываем цену монеты с наценкой: цена_USD × (1 + процент/100)
		crypto_price_with_markup = crypto_price_usd * (1 + markup_percent / 100)
		
		# Рассчитываем итоговую сумму в USD после наценки
		total_usd = crypto_price_with_markup * amount
		
		# Сохраняем total_usd в состоянии для проверки крупной сделки
		await state.update_data(total_usd=total_usd)
		
		# Алерт админу при больших суммах (после ввода суммы)
		try:
			alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
			alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
		except (ValueError, TypeError):
			alert_threshold = 400.0
		
		# Сохраняем alert_threshold в состоянии
		await state.update_data(alert_threshold=alert_threshold)
		
		# Определяем, является ли это крупной заявкой
		is_large_order = total_usd >= alert_threshold
		
		if is_large_order:
			# Получаем текущий этап пользователя
			current_state = await state.get_state()
			stage_name = get_user_stage_name(str(current_state) if current_state else "")
			
			alert_text = (
				f"🚨 <b>Крупная заявка</b>\n\n"
				f"Пользователь: {message.from_user.full_name or 'Не указано'} (@{message.from_user.username or 'нет'})\n"
				f"Страна: {_deal_country_label(selected_country)}\n"
				f"Сумма: {total_usd:.2f}$\n"
				f"Крипта: {crypto_display}\n"
				f"Кол-во: {amount}\n\n"
				f"📍 <b>Этап:</b> {stage_name}"
			)
			from aiogram.utils.keyboard import InlineKeyboardBuilder
			kb = InlineKeyboardBuilder()
			kb.button(text="💬 Написать", callback_data=f"alert:message:{message.from_user.id}")
			kb.button(text="💳 Реквизиты", callback_data=f"alert:requisites:{message.from_user.id}")
			kb.button(text="💰 Сумма", callback_data=f"alert:amount:{message.from_user.id}")
			kb.button(text="🪙 Монеты", callback_data=f"alert:crypto:{message.from_user.id}")
			kb.adjust(2, 2)
			admin_ids = get_admin_ids()
			
			# Сохраняем message_id для обновления
			user_tg_id = message.from_user.id
			logger_main = logging.getLogger("app.main")
			logger_main.info(f"🔍 Создание записи для user_tg_id={user_tg_id} в large_order_alerts")
			logger_main.info(f"🔍 Текущее состояние large_order_alerts: {list(large_order_alerts.keys())}")
			
			# Защита от переполнения памяти
			limit_dict_size(large_order_alerts, MAX_LARGE_ORDER_ALERTS, "large_order_alerts")
			
			if user_tg_id not in large_order_alerts:
				large_order_alerts[user_tg_id] = {"message_ids": {}, "question_id": None}
				logger_main.info(f"✅ Создан новый словарь для user_tg_id={user_tg_id}")
			else:
				# Поддерживаем обратную совместимость
				if not isinstance(large_order_alerts[user_tg_id], dict) or "message_ids" not in large_order_alerts[user_tg_id]:
					# Старая структура, конвертируем в новую
					old_data = large_order_alerts[user_tg_id]
					large_order_alerts[user_tg_id] = {"message_ids": old_data, "question_id": None}
				logger_main.info(f"⚠️ Запись для user_tg_id={user_tg_id} уже существует: {large_order_alerts[user_tg_id]}")
			
			logger_main.info(f"🔍 Админы для отправки: {admin_ids}")
			
			for admin_id in admin_ids:
				try:
					logger_main.info(f"📤 Отправка сообщения админу {admin_id}")
					sent_msg = await message.bot.send_message(
						chat_id=admin_id,
						text=alert_text,
						parse_mode=ParseMode.HTML,
						reply_markup=kb.as_markup()
					)
					large_order_alerts[user_tg_id]["message_ids"][admin_id] = sent_msg.message_id
					# Сохраняем message_id в buy_deal_alerts, если deal_id уже есть
					deal_id_from_state = (await state.get_data()).get("deal_id")
					if deal_id_from_state:
						from app.main import buy_deal_alerts
						if deal_id_from_state not in buy_deal_alerts:
							buy_deal_alerts[deal_id_from_state] = {}
						buy_deal_alerts[deal_id_from_state][admin_id] = sent_msg.message_id
						logger_main.info(f"✅ Сохранено в buy_deal_alerts, deal_id={deal_id_from_state}, message_id={sent_msg.message_id}")
					logger_main.info(f"✅ Сообщение о крупной заявке отправлено админу {admin_id}, message_id={sent_msg.message_id}, user_tg_id={user_tg_id}")
					logger_main.info(f"✅ large_order_alerts[{user_tg_id}] = {large_order_alerts[user_tg_id]}")
				except Exception as e:
					logger_main.error(
						f"❌ ОШИБКА при отправке алерта админу {admin_id}: {type(e).__name__}: {e}",
						exc_info=True
					)
			
			logger_main.info(f"📊 Финальное состояние large_order_alerts для user_tg_id={user_tg_id}: {large_order_alerts.get(user_tg_id, 'НЕ НАЙДЕНО')}")
		
		# Дополнительные комиссии по порогам USD
		extra_fee_usd_low_str = await db_local.get_setting("buy_extra_fee_usd_low", "50")
		extra_fee_usd_mid_str = await db_local.get_setting("buy_extra_fee_usd_mid", "67")
		try:
			extra_fee_usd_low = float(extra_fee_usd_low_str) if extra_fee_usd_low_str else 50.0
		except (ValueError, TypeError):
			extra_fee_usd_low = 50.0
		try:
			extra_fee_usd_mid = float(extra_fee_usd_mid_str) if extra_fee_usd_mid_str else 67.0
		except (ValueError, TypeError):
			extra_fee_usd_mid = 67.0
		
		if selected_country == "BYN":
			fee_low_str = await db_local.get_setting("buy_extra_fee_low_byn", "10")
			fee_mid_str = await db_local.get_setting("buy_extra_fee_mid_byn", "5")
			try:
				fee_low = float(fee_low_str) if fee_low_str else 10.0
			except (ValueError, TypeError):
				fee_low = 10.0
			try:
				fee_mid = float(fee_mid_str) if fee_mid_str else 5.0
			except (ValueError, TypeError):
				fee_mid = 5.0
		else:
			fee_low_str = await db_local.get_setting("buy_extra_fee_low_rub", "10")
			fee_mid_str = await db_local.get_setting("buy_extra_fee_mid_rub", "5")
			try:
				fee_low = float(fee_low_str) if fee_low_str else 10.0
			except (ValueError, TypeError):
				fee_low = 10.0
			try:
				fee_mid = float(fee_mid_str) if fee_mid_str else 5.0
			except (ValueError, TypeError):
				fee_mid = 5.0
		
		extra_fee_currency = 0.0
		if selected_country == "RUB":
			# Для РФ: +300₽ к любому результату до 200$
			if total_usd < 200:
				extra_fee_currency = 300
		else:
			if total_usd < extra_fee_usd_low:
				extra_fee_currency = fee_low
			elif total_usd < extra_fee_usd_mid:
				extra_fee_currency = fee_mid
		
		# Рассчитываем итоговую сумму: (цена_с_наценкой) × количество × курс_валюты + доп. комиссия
		amount_currency = (total_usd * usd_to_currency_rate) + extra_fee_currency
		
		# Логируем расчет для отладки
		logger = logging.getLogger("app.main")
		logger.debug(
			f"Расчет: ({crypto_price_usd} USD + {markup_percent}%) × {amount} {crypto_type} = {total_usd} USD; "
			f"курс {usd_to_currency_rate} {currency_symbol}/USD, доп. комиссия {extra_fee_currency} {currency_symbol}; "
			f"итого {amount_currency} {currency_symbol}"
		)
		
		# Сохраняем данные о сделке
		await state.update_data(
			amount=amount,
			amount_currency=amount_currency,
			crypto_type=crypto_type,
			crypto_symbol=crypto_symbol,
			crypto_price_usd=crypto_price_usd,
			crypto_price_with_markup=crypto_price_with_markup,
			markup_percent=markup_percent,
			total_usd=total_usd,
			extra_fee_currency=extra_fee_currency,
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
		
		# Проверяем, является ли это крупной заявкой
		data = await state.get_data()
		alert_threshold = data.get("alert_threshold", 400.0)
		total_usd = data.get("total_usd", 0)
		is_large_order = total_usd >= alert_threshold
		
		# Для крупных заявок не показываем сумму оплаты
		if is_large_order:
			payment_text = "ожидайте сообщение администратора"
		else:
			payment_text = f"{int(amount_currency)} {currency_symbol}"
		
		confirmation_text = (
			f"Вам будет зачислено: {amount_str} {crypto_display}\n"
			f"Вам необходимо оплатить: {payment_text}"
		)
		
		# Показываем сообщение с кнопками подтверждения
		from app.keyboards import buy_confirmation_kb
		await state.set_state(BuyStates.waiting_confirmation)
		
		# Обновляем сообщение о крупной заявке, если она активна
		await try_update_large_order_alert(
			bot=message.bot,
			state=state,
			user_tg_id=message.from_user.id,
			user_name=message.from_user.full_name or "",
			user_username=message.from_user.username or ""
		)
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
		
		# Проверяем, является ли это крупной заявкой
		alert_threshold = data.get("alert_threshold", 400.0)
		total_usd = data.get("total_usd", 0)
		is_large_order = total_usd >= alert_threshold
		admin_amount_set = data.get("admin_amount_set", False)
		admin_amount_value = data.get("admin_amount_value")

		# Для крупных заявок не даем продолжить без суммы от админа
		if is_large_order and not admin_amount_set:
			await send_and_save_message(
				message,
				"⏳ Ожидайте сообщение администратора с суммой оплаты.",
				state=state
			)
			return
		
		# Для крупных заявок не показываем сумму оплаты
		if is_large_order:
			if admin_amount_set and admin_amount_value is not None:
				payment_text = f"{int(admin_amount_value)} {currency_symbol}"
			else:
				payment_text = "ожидайте сообщение администратора"
		else:
			payment_text = f"{int(amount_currency)} {currency_symbol}"
		
		# Показываем уведомление о заказе
		order_notification = (
			f"Вам будет зачислено: {amount_str} {crypto_display}\n"
			f"Вам необходимо оплатить: {payment_text}"
		)
		
		# Сохраняем ID предыдущего сообщения для удаления
		last_bot_message_id = data.get("last_bot_message_id")
		
		# Переходим в состояние ожидания адреса кошелька
		await state.set_state(BuyStates.waiting_wallet_address)
		
		# Небольшая задержка, чтобы состояние точно сохранилось
		await asyncio.sleep(0.1)
		
		# Обновляем сообщение о крупной заявке, если она активна
		total_usd = data.get("total_usd", 0)
		alert_threshold = data.get("alert_threshold", 400.0)
		if total_usd >= alert_threshold:
			await try_update_large_order_alert(
				bot=message.bot,
				state=state,
				user_tg_id=message.from_user.id,
				user_name=message.from_user.full_name or "",
				user_username=message.from_user.username or ""
			)
		
		# Убираем клавиатуру подтверждения
		await send_and_save_message(message, "✅ Принято.", reply_markup=ReplyKeyboardRemove(), state=state)
		
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
		
		# Убираем клавиатуру подтверждения
		await send_and_save_message(message, "❌ Отменено.", reply_markup=ReplyKeyboardRemove(), state=state)
		
		# Возвращаемся в главное меню
		from app.keyboards import client_menu_kb
		await state.clear()
		await send_and_save_message(message, "Выберите действие:", reply_markup=client_menu_kb(), state=state)

	@dp.message(BuyStates.waiting_confirmation)
	async def on_buy_confirm_other(message: Message, state: FSMContext):
		"""Обработчик прочих сообщений на шаге подтверждения"""
		data = await state.get_data()
		pending_question_id = data.get("pending_question_reply_id")
		pending_prompt_id = data.get("pending_question_reply_prompt_id")
		if pending_question_id and (
			message.reply_to_message and message.reply_to_message.message_id == pending_prompt_id
		):
			await _handle_question_user_reply(message, state, pending_question_id, keep_state=True)
			return
		# Если пользователь нажал "Ответить", но не ответил на prompt, все равно считаем это ответом админу
		if pending_question_id:
			await _handle_question_user_reply(message, state, pending_question_id, keep_state=True)
			return
	
	@dp.message(BuyStates.waiting_wallet_address)
	async def on_wallet_address_entered(message: Message, state: FSMContext):
		"""Обработчик ввода адреса кошелька"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Если это ответ на запрос сообщения админу, обрабатываем как чат
		data = await state.get_data()
		pending_question_id = data.get("pending_question_reply_id")
		pending_prompt_id = data.get("pending_question_reply_prompt_id")
		if pending_question_id and (
			message.reply_to_message and message.reply_to_message.message_id == pending_prompt_id
		):
			await _handle_question_user_reply(message, state, pending_question_id, keep_state=True)
			return
		# Если пользователь нажал "Ответить", но не ответил на prompt, все равно считаем это ответом админу
		if pending_question_id:
			await _handle_question_user_reply(message, state, pending_question_id, keep_state=True)
			return
		
		# Проверяем, не является ли это командой - если да, пропускаем обработку
		if message.text and message.text.startswith("/"):
			return  # Пропускаем команды, они обработаются в своих обработчиках
		
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
		
		# Проверяем, является ли сделка крупной
		total_usd = data.get("total_usd", 0)
		alert_threshold = data.get("alert_threshold", 400.0)
		is_large_order = total_usd >= alert_threshold
		admin_amount_set = data.get("admin_amount_set", False)
		admin_amount_value = data.get("admin_amount_value")
		
		# Обновляем сообщение о крупной заявке на этапе "Ввод кошелька" (состояние уже waiting_wallet_address)
		if is_large_order:
			await asyncio.sleep(0.1)  # Небольшая задержка для сохранения данных
			await try_update_large_order_alert(
				bot=message.bot,
				state=state,
				user_tg_id=message.from_user.id,
				user_name=message.from_user.full_name or "",
				user_username=message.from_user.username or ""
			)
		
		# Форматируем суммы для отображения
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Для XMR и USDT пропускаем выбор способа доставки, для BTC показываем выбор
		# Для крупных сделок автоматически устанавливаем VIP доставку
		if crypto_type == "XMR" or crypto_type == "USDT" or is_large_order:
			# Для XMR и USDT устанавливаем обычную доставку
			# Для крупных сделок устанавливаем VIP доставку
			if is_large_order:
				delivery_type = "vip"
			else:
				delivery_type = "normal"
			await state.update_data(delivery_method=delivery_type)
			
			# Рассчитываем сумму (без VIP для XMR и USDT, с VIP для крупных сделок)
			final_amount = amount_currency
			if is_large_order and delivery_type == "vip":
				# Добавляем VIP надбавку для крупных сделок
				if selected_country == "BYN":
					final_amount += 4
				else:  # RUB
					final_amount += 1000
			
			# Получаем реквизиты пользователя
			user_cards = await db_local.get_cards_for_user_tg(message.from_user.id)
			requisites_text = ""
			pay_card_info = ""
			
			if user_cards:
				# Берем первую карту пользователя
				card = user_cards[0]
				card_id = card["card_id"]
				card_info = await db_local.get_card_by_id(card_id)
				card_name = (card_info.get("name") if card_info else None) or card.get("card_name") or card.get("name") or ""
				group_name = ""
				if card_info and card_info.get("group_id"):
					group = await db_local.get_card_group_by_id(card_info["group_id"])
					group_name = group.get("name") if group else ""
				if card_name:
					label = f"{group_name} ({card_name})" if group_name else card_name
					pay_card_info = f"\n💳 Карта для оплаты: {label}"
				
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
			
			# Формируем сообщение
			# Определяем короткое название криптовалюты
			if crypto_type == "XMR":
				crypto_short = "xmr"
			elif crypto_type == "USDT":
				crypto_short = "usdt"
			else:
				crypto_short = crypto_type.lower()
			
			# Проверяем, является ли это крупной заявкой
			alert_threshold = data.get("alert_threshold", 400.0)
			total_usd = data.get("total_usd", 0)
			is_large_order = total_usd >= alert_threshold
			should_show_requisites = (not is_large_order) or admin_amount_set
			
			# Для крупных заявок не показываем сумму оплаты
			if is_large_order:
				if admin_amount_set and admin_amount_value is not None:
					payment_text = f"{int(admin_amount_value)} {currency_symbol}"
				else:
					payment_text = "ожидайте сообщение администратора"
			else:
				payment_text = f"{int(final_amount)} {currency_symbol}"
			
			order_message = (
				f"☑️Заявка успешно создана.\n"
				f"Вы получаете: {amount_str} {crypto_short}\n"
				f"{crypto_display} - {crypto_type}-адрес: {wallet_address}\n\n"
				f"💳Сумма к оплате: {payment_text}\n"
			)
			
			if should_show_requisites:
				order_message += f"Реквизиты для оплаты:\n{pay_card_info}\n\n"
				if requisites_text:
					order_message += requisites_text + "\n\n"
				else:
					order_message += "Реквизиты не найдены. Идет загрузка, ожидайте.\n\n"
			else:
				order_message += "Реквизиты будут после согласования суммы с администратором.\n\n"
			
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
			
			# Небольшая задержка, чтобы состояние точно сохранилось
			await asyncio.sleep(0.1)
			
			# Обновляем сообщение о крупной заявке, если она активна
			await try_update_large_order_alert(
				bot=message.bot,
				state=state,
				user_tg_id=message.from_user.id,
				user_name=message.from_user.full_name or "",
				user_username=message.from_user.username or ""
			)
			final_message = await send_and_save_message(
				message,
				order_message,
				reply_markup=buy_payment_confirmed_kb(),
				state=state
			)
			# Сохраняем ID сообщения с заявкой в состоянии для последующего сохранения в БД
			await state.update_data(order_message_id=final_message.message_id)
			
			# Если реквизитов нет, сохраняем ожидание и уведомляем админов
			if should_show_requisites and not requisites_text:
				await db_local.save_pending_requisites(
					user_tg_id=message.from_user.id,
					message_id=final_message.message_id,
					crypto_type=crypto_type,
					crypto_display=crypto_display,
					amount=amount,
					final_amount=final_amount,
					currency_symbol=currency_symbol,
					wallet_address=wallet_address
				)
				user_id = await db_local.get_or_create_user(
					message.from_user.id,
					message.from_user.username,
					message.from_user.full_name
				)
				admin_ids = get_admin_ids()
				if admin_ids and user_id != -1:
					kb = InlineKeyboardBuilder()
					kb.button(text="🔗 Привязать карту", callback_data=f"user:bind:{user_id}")
					kb.button(text="👤 Меню пользователя", callback_data=f"user:view:{user_id}")
					kb.adjust(1)
					alert_text = (
						"⚠️ У пользователя нет привязанной карты для оплаты.\n\n"
						f"👤 {message.from_user.full_name} (@{message.from_user.username or 'нет'})\n"
						f"🆔 ID: <code>{message.from_user.id}</code>\n"
						f"Крипта: {crypto_display}\n"
						f"Сумма: {int(final_amount)} {currency_symbol}"
					)
					for admin_id in admin_ids:
						try:
							await message.bot.send_message(
								chat_id=admin_id,
								text=alert_text,
								parse_mode="HTML",
								reply_markup=kb.as_markup()
							)
						except Exception:
							pass
		else:
			# Для BTC показываем выбор способа доставки (VIP или обычная)
			# Проверяем, является ли это крупной заявкой
			alert_threshold = data.get("alert_threshold", 400.0)
			total_usd = data.get("total_usd", 0)
			is_large_order = total_usd >= alert_threshold
			
			# Для крупных заявок не показываем сумму оплаты
			if is_large_order:
				payment_text = "ожидайте сообщение администратора"
			else:
				payment_text = f"{int(amount_currency)} {currency_symbol}"
			
			order_info = (
				f"Вам будет зачислено: {amount_str} {crypto_display}\n"
				f"Вам необходимо оплатить: {payment_text}\n\n"
				f"Выберите способ доставки:"
			)
			
			# Показываем клавиатуру выбора способа доставки
			is_byn = selected_country == "BYN"
			await state.set_state(BuyStates.waiting_delivery_method)
			
			# Небольшая задержка, чтобы состояние точно сохранилось
			await asyncio.sleep(0.1)
			
			# Обновляем сообщение о крупной заявке, если она активна
			await try_update_large_order_alert(
				bot=message.bot,
				state=state,
				user_tg_id=message.from_user.id,
				user_name=message.from_user.full_name or "",
				user_username=message.from_user.username or ""
			)
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
			await asyncio.sleep(0.1)
			# Обновляем сообщение о крупной заявке
			await try_update_large_order_alert(
				bot=message.bot,
				state=state,
				user_tg_id=message.from_user.id,
				user_name=message.from_user.full_name or "",
				user_username=message.from_user.username or ""
			)
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
		pay_card_info = ""
		
		if user_cards:
			# Берем первую карту пользователя
			card = user_cards[0]
			card_id = card["card_id"]
			card_info = await db_local.get_card_by_id(card_id)
			card_name = (card_info.get("name") if card_info else None) or card.get("card_name") or card.get("name") or ""
			group_name = ""
			if card_info and card_info.get("group_id"):
				group = await db_local.get_card_group_by_id(card_info["group_id"])
				group_name = group.get("name") if group else ""
			if card_name:
				label = f"{group_name} ({card_name})" if group_name else card_name
				pay_card_info = f"\n💳 Карта для оплаты: {label}"
			
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
			f"Реквизиты для оплаты:\n{pay_card_info}\n\n"
		)
		
		if requisites_text:
			order_message += requisites_text + "\n\n"
		else:
			order_message += "Реквизиты не найдены. Идет загрузка, ожидайте.\n\n"
		
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
		
		# Небольшая задержка, чтобы состояние точно сохранилось
		await asyncio.sleep(0.1)
		
		# Обновляем сообщение о крупной заявке, если она активна
		total_usd = data.get("total_usd", 0)
		alert_threshold = data.get("alert_threshold", 400.0)
		if total_usd >= alert_threshold:
			await try_update_large_order_alert(
				bot=message.bot,
				state=state,
				user_tg_id=message.from_user.id,
				user_name=message.from_user.full_name or "",
				user_username=message.from_user.username or ""
			)
		
		final_message = await send_and_save_message(
			message,
			order_message,
			reply_markup=buy_payment_confirmed_kb(),
			state=state
		)
		# Сохраняем ID сообщения с заявкой в состоянии для последующего сохранения в БД
		await state.update_data(order_message_id=final_message.message_id)
		
		# Если реквизитов нет, сохраняем ожидание и уведомляем админов
		if not requisites_text:
			await db_local.save_pending_requisites(
				user_tg_id=message.from_user.id,
				message_id=final_message.message_id,
				crypto_type=crypto_type,
				crypto_display=crypto_display,
				amount=amount,
				final_amount=final_amount,
				currency_symbol=currency_symbol,
				wallet_address=wallet_address
			)
			user_id = await db_local.get_or_create_user(
				message.from_user.id,
				message.from_user.username,
				message.from_user.full_name
			)
			admin_ids = get_admin_ids()
			if admin_ids and user_id != -1:
				kb = InlineKeyboardBuilder()
				kb.button(text="🔗 Привязать карту", callback_data=f"user:bind:{user_id}")
				kb.button(text="👤 Меню пользователя", callback_data=f"user:view:{user_id}")
				kb.adjust(1)
				alert_text = (
					"⚠️ У пользователя нет привязанной карты для оплаты.\n\n"
					f"👤 {message.from_user.full_name} (@{message.from_user.username or 'нет'})\n"
					f"🆔 ID: <code>{message.from_user.id}</code>\n"
					f"Крипта: {crypto_display}\n"
					f"Сумма: {int(final_amount)} {currency_symbol}"
				)
				for admin_id in admin_ids:
					try:
						await message.bot.send_message(
							chat_id=admin_id,
							text=alert_text,
							parse_mode="HTML",
							reply_markup=kb.as_markup()
						)
					except Exception:
						pass
	
	@dp.message(BuyStates.waiting_payment_confirmation, F.text == "ОПЛАТА СОВЕРШЕНА")
	async def on_payment_confirmed(message: Message, state: FSMContext):
		"""Обработчик подтверждения оплаты"""
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"🔵 on_payment_confirmed: Начало обработки для user_id={message.from_user.id if message.from_user else None}")
		
		if not message.from_user:
			logger_main.warning("❌ on_payment_confirmed: message.from_user is None")
			return
		
		try:
			from app.di import get_db
			db_local = get_db()
			logger_main.info(f"🔵 on_payment_confirmed: Проверка доступа для user_id={message.from_user.id}")
			
			if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
				logger_main.warning(f"❌ on_payment_confirmed: Пользователь {message.from_user.id} не имеет доступа")
				return
			
			logger_main.info(f"✅ on_payment_confirmed: Пользователь {message.from_user.id} имеет доступ")
			
			# Удаляем сообщение пользователя
			logger_main.info(f"🔵 on_payment_confirmed: Удаление сообщения пользователя")
			await delete_user_message(message)
			
			# Получаем данные о заказе
			logger_main.info(f"🔵 on_payment_confirmed: Получение данных из состояния")
			data = await state.get_data()
			logger_main.info(f"🔵 on_payment_confirmed: Данные получены, keys={list(data.keys())}")
			
			logger_main.info(f"🔵 on_payment_confirmed: Проверка pending_requisites")
			pending = await db_local.get_pending_requisites(message.from_user.id)
			if pending:
				logger_main.info(f"🔵 on_payment_confirmed: Найдены pending_requisites, message_id={pending.get('message_id')}")
				await state.update_data(order_message_id=pending["message_id"])
				await db_local.delete_pending_requisites(message.from_user.id)
			else:
				logger_main.info(f"🔵 on_payment_confirmed: pending_requisites не найдены")
			
			order_expires_at = data.get("order_expires_at", 0)
			logger_main.info(f"🔵 on_payment_confirmed: order_expires_at={order_expires_at}")
			
			# Проверяем, не истекла ли заявка
			current_time = int(time.time())
			logger_main.info(f"🔵 on_payment_confirmed: current_time={current_time}, order_expires_at={order_expires_at}")
			if current_time > order_expires_at:
				logger_main.warning(f"⚠️ on_payment_confirmed: Заявка истекла")
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
			
			logger_main.info(f"✅ on_payment_confirmed: Заявка не истекла, продолжаем")
			
			# Переходим в состояние ожидания скриншота/чека
			logger_main.info(f"🔵 on_payment_confirmed: Установка состояния waiting_payment_proof")
			await state.set_state(BuyStates.waiting_payment_proof)
			
			# Небольшая задержка, чтобы состояние точно сохранилось
			logger_main.info(f"🔵 on_payment_confirmed: Задержка 0.1 сек")
			await asyncio.sleep(0.1)
			
			# Обновляем сообщение о крупной заявке, если она активна
			logger_main.info(f"🔵 on_payment_confirmed: Обновление сообщения о крупной заявке")
			try:
				await try_update_large_order_alert(
					bot=message.bot,
					state=state,
					user_tg_id=message.from_user.id,
					user_name=message.from_user.full_name or "",
					user_username=message.from_user.username or ""
				)
				logger_main.info(f"✅ on_payment_confirmed: Сообщение о крупной заявке обновлено")
			except Exception as e:
				logger_main.error(f"❌ on_payment_confirmed: Ошибка при обновлении сообщения о крупной заявке: {e}", exc_info=True)
			
			# Запрашиваем скриншот/чек оплаты
			logger_main.info(f"🔵 on_payment_confirmed: Отправка запроса скриншота")
			try:
				proof_request_message = await send_and_save_message(
					message,
					"Отправьте скрин перевода, либо чек оплаты.",
					state=state
				)
				logger_main.info(f"✅ on_payment_confirmed: Запрос скриншота отправлен, message_id={proof_request_message.message_id}")
			except Exception as e:
				logger_main.error(f"❌ on_payment_confirmed: Ошибка при отправке запроса скриншота: {e}", exc_info=True)
				raise
			
			# Сохраняем ID сообщения с запросом скриншота
			logger_main.info(f"🔵 on_payment_confirmed: Сохранение proof_request_message_id")
			await state.update_data(proof_request_message_id=proof_request_message.message_id)
			logger_main.info(f"✅ on_payment_confirmed: Обработка завершена успешно")
			
		except Exception as e:
			logger_main.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА в on_payment_confirmed: {e}", exc_info=True)
			# Пытаемся отправить сообщение пользователю об ошибке
			try:
				await message.answer("❌ Произошла ошибка при обработке подтверждения оплаты. Попробуйте позже.")
			except:
				pass
			raise
	
	# Дополнительный обработчик для случая, когда пользователь в состоянии переписки,
	# но нажимает "ОПЛАТА СОВЕРШЕНА" (данные о заказе все еще в состоянии FSM)
	@dp.message(F.text == "ОПЛАТА СОВЕРШЕНА")
	async def on_payment_confirmed_any_state(message: Message, state: FSMContext):
		"""Обработчик подтверждения оплаты для любого состояния (если есть данные о заказе)"""
		logger_main = logging.getLogger("app.main")
		current_state = await state.get_state()
		logger_main.info(f"🔵 on_payment_confirmed_any_state: Получено сообщение 'ОПЛАТА СОВЕРШЕНА' для user_id={message.from_user.id if message.from_user else None}, state={current_state}")
		
		# Проверяем, есть ли данные о заказе в состоянии
		data = await state.get_data()
		has_order_data = any(key in data for key in ["total_usd", "crypto_display", "amount", "final_amount"])
		
		# Если состояние уже BuyStates.waiting_payment_confirmation, пропускаем (обработает основной обработчик)
		if current_state == BuyStates.waiting_payment_confirmation.state:
			logger_main.info(f"🔵 on_payment_confirmed_any_state: Состояние уже waiting_payment_confirmation, пропускаем")
			return
		
		# Если нет данных о заказе, пропускаем
		if not has_order_data:
			logger_main.info(f"🔵 on_payment_confirmed_any_state: Нет данных о заказе, пропускаем")
			return
		
		# Если есть данные о заказе, но состояние не waiting_payment_confirmation,
		# значит пользователь в переписке, но пытается подтвердить оплату
		logger_main.info(f"🔵 on_payment_confirmed_any_state: Найдены данные о заказе, но состояние {current_state} не waiting_payment_confirmation. Восстанавливаем состояние.")
		
		# Восстанавливаем состояние waiting_payment_confirmation
		await state.set_state(BuyStates.waiting_payment_confirmation)
		await asyncio.sleep(0.1)
		
		# Вызываем основной обработчик
		await on_payment_confirmed(message, state)
	
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
		total_usd = data.get("total_usd", 0)
		alert_threshold = data.get("alert_threshold", 400.0)
		
		# Если total_usd не сохранен, но delivery_method = "vip", считаем что это крупная сделка
		# Или проверяем по порогу
		if total_usd == 0 and delivery_method == "vip":
			# Если доставка VIP, но total_usd не сохранен, считаем что это крупная сделка
			is_large_order = True
		else:
			is_large_order = total_usd >= alert_threshold if total_usd > 0 else False
		
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
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		proof_details = (
			f"\n\nКоличество монет: {amount_str} {crypto_display}\n"
			f"Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
			f"Адрес кошелька: {wallet_address}"
		)
		proof_confirmation_message = await message.bot.send_message(
			chat_id=message.chat.id,
			text=(
				"✅ Спасибо! Ваш скриншот/чек получен. Ожидайте зачисления средств на указанный адрес кошелька."
				+ proof_details
			)
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
		# Сохраняем сообщение пользователя как user_message_id для обновлений
		if order_message_id:
			await db_local.update_order_user_message_id(order_id, order_message_id)
		
		# Получаем заявку для получения номера
		order = await db_local.get_order_by_id(order_id)
		order_number = order["order_number"] if order else order_id
		
		# Форматируем сумму для отображения
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Получаем общий долг пользователя
		user_tg_id = message.from_user.id
		user_debts = await db_local.get_user_total_debt(user_tg_id)
		total_debt_info = ""
		if user_debts:
			debt_lines = []
			for curr, debt_sum in user_debts.items():
				debt_lines.append(f"{int(debt_sum)} {curr}")
			if debt_lines:
				total_debt_info = f"\n💳 Общий долг пользователя: {', '.join(debt_lines)}"
		
		# Получаем информацию о последней сделке и профите пользователя
		last_order_info = ""
		try:
			user_id = await db_local.get_user_id_by_tg(user_tg_id)
			if user_id:
				user_data = await db_local.get_user_by_id(user_id)
				if user_data:
					last_order_id = user_data.get("last_order_id")
					last_order_profit = user_data.get("last_order_profit")
					
					if last_order_id:
						# Получаем информацию о последней сделке
						last_order = await db_local.get_order_by_id(last_order_id)
						if last_order:
							last_created_at = last_order.get("created_at")
							if last_created_at:
								last_order_date = datetime.fromtimestamp(last_created_at).strftime("%d.%m.%Y %H:%M")
							else:
								last_order_date = "неизвестно"
							last_order_info = f"\n📦 Последнее обращение: {last_order_date}"
							
							if last_order_profit is not None:
								try:
									profit_formatted = f"{int(round(last_order_profit)):,}".replace(",", " ")
									last_order_info += f"\n💰 Профит от последней сделки: {profit_formatted} USD"
								except (ValueError, TypeError):
									last_order_info += f"\n💰 Профит от последней сделки: {last_order_profit} USD"
					
					# Получаем профит за текущий месяц
					monthly_profit = await db_local.get_user_monthly_profit(user_tg_id)
					if monthly_profit is not None:
						try:
							monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit_formatted} USD"
						except (ValueError, TypeError):
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit} USD"
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.debug(f"Ошибка получения информации о последней сделке при создании заявки: {e}", exc_info=True)
		
		# Формируем сообщение для админа
		card_name = ""
		group_name = ""
		user_cards = await db_local.get_cards_for_user_tg(user_tg_id)
		if user_cards:
			card = user_cards[0]
			card_id = card["card_id"]
			card_info = await db_local.get_card_by_id(card_id)
			card_name = (card_info.get("name") if card_info else None) or card.get("card_name") or card.get("name") or ""
			if card_info and card_info.get("group_id"):
				group = await db_local.get_card_group_by_id(card_info["group_id"])
				group_name = group.get("name") if group else ""
		if card_name:
			label = f"{group_name} ({card_name})" if group_name else card_name
			pay_card_info = f"\n💳 Карта для оплаты: {label}"
		else:
			pay_card_info = ""
		# Формируем информацию о крупной сделке и способе доставки
		large_order_info = ""
		delivery_info = ""
		if is_large_order:
			if total_usd > 0:
				large_order_info = f"\n🚨 <b>КРУПНАЯ СДЕЛКА</b> ({total_usd:.2f} USD)"
			else:
				large_order_info = f"\n🚨 <b>КРУПНАЯ СДЕЛКА</b>"
		if delivery_method == "vip":
			delivery_info = "\n🚀 Доставка: <b>VIP</b>"
		elif delivery_method == "normal":
			delivery_info = "\n📦 Доставка: Обычная"
		
		admin_message_text = (
			f"Номер заявки за сегодня: {order_number}\n"
			f"Имя пользователя: {user_name or 'Не указано'}\n"
			f"Username: @{user_username}\n"
			f"🆔 ID: <code>{user_tg_id}</code>{last_order_info}{large_order_info}\n\n"
			f"Количество монет: {amount_str} {crypto_display}\n"
			f"Сумма к оплате: {int(amount_currency)} {currency_symbol}{delivery_info}\n"
			f"Адрес кошелька: <code>{wallet_address}</code>{pay_card_info}{total_debt_info}"
		)
		
		# Отправляем заявку всем админам
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"📤 Отправка заявки #{order_number} админам. Список админов: {admin_ids}")
		
		if not admin_ids:
			logger_main.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Список админов пустой! Заявка не будет отправлена.")
			# Отправляем сообщение пользователю об ошибке
			await message.bot.send_message(
				chat_id=message.chat.id,
				text="⚠️ Произошла ошибка при отправке заявки администраторам. Пожалуйста, свяжитесь с поддержкой."
			)
		else:
			success_count = 0
			for admin_id in admin_ids:
				try:
					logger_main.info(f"📤 Отправка заявки #{order_number} админу {admin_id}")
					# Отправляем скриншот/чек с информацией о заявке в caption и кнопками
					proof_msg = None
					if proof_photo_file_id:
						proof_msg = await message.bot.send_photo(
							chat_id=admin_id,
							photo=proof_photo_file_id,
							caption=admin_message_text,
							parse_mode=ParseMode.HTML,
							reply_markup=order_action_kb(order_id)
						)
						logger_main.info(f"✅ Фото отправлено админу {admin_id} с информацией и кнопками, message_id={proof_msg.message_id}")
					elif proof_document_file_id:
						proof_msg = await message.bot.send_document(
							chat_id=admin_id,
							document=proof_document_file_id,
							caption=admin_message_text,
							parse_mode=ParseMode.HTML,
							reply_markup=order_action_kb(order_id)
						)
						logger_main.info(f"✅ Документ отправлен админу {admin_id} с информацией и кнопками, message_id={proof_msg.message_id}")
					else:
						# Если нет фото и документа, отправляем текстовое сообщение с кнопками
						proof_msg = await message.bot.send_message(
							chat_id=admin_id,
							text=admin_message_text,
							parse_mode=ParseMode.HTML,
							reply_markup=order_action_kb(order_id)
						)
						logger_main.info(f"✅ Текст заявки отправлен админу {admin_id} с кнопками, message_id={proof_msg.message_id}")
					
					# Сохраняем admin_message_id в БД (ID фото/документа/текста для обновления при переписке)
					await db_local.update_order_admin_message_id(order_id, proof_msg.message_id)
					
					success_count += 1
					logger_main.info(f"✅ Заявка #{order_number} успешно отправлена админу {admin_id}")
				except Exception as e:
					logger_main.error(f"❌ Ошибка отправки заявки #{order_number} админу {admin_id}: {e}", exc_info=True)
			
			logger_main.info(f"📊 Итого: заявка #{order_number} отправлена {success_count} из {len(admin_ids)} админам")
		
		# Очищаем состояние
		await state.clear()
	
	@dp.message(QuestionStates.waiting_question)
	async def on_question_received(message: Message, state: FSMContext):
		"""Обработчик получения вопроса от пользователя"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Получаем текст вопроса
		question_text = message.text or message.caption or ""
		if not question_text.strip():
			await send_and_save_message(
				message,
				"❌ Пожалуйста, введите текст вопроса.",
				state=state
			)
			return
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Получаем информацию о пользователе
		user_name = message.from_user.full_name or "Не указано"
		user_username = message.from_user.username or "Не указано"
		user_tg_id = message.from_user.id
		
		# Создаем вопрос в БД (пока без admin_message_id)
		question_id = await db_local.create_question(
			user_tg_id=user_tg_id,
			user_name=user_name,
			user_username=user_username,
			question_text=question_text
		)
		
		# Получаем информацию о последней сделке и профите пользователя
		last_order_info = ""
		try:
			user_id = await db_local.get_user_id_by_tg(user_tg_id)
			if user_id:
				user_data = await db_local.get_user_by_id(user_id)
				if user_data:
					last_order_id = user_data.get("last_order_id")
					last_order_profit = user_data.get("last_order_profit")
					
					if last_order_id:
						# Получаем информацию о последней сделке
						last_order = await db_local.get_order_by_id(last_order_id)
						if last_order:
							last_created_at = last_order.get("created_at")
							if last_created_at:
								last_order_date = datetime.fromtimestamp(last_created_at).strftime("%d.%m.%Y %H:%M")
							else:
								last_order_date = "неизвестно"
							last_order_info = f"\n📦 Последнее обращение: {last_order_date}"
							
							if last_order_profit is not None:
								try:
									profit_formatted = f"{int(round(last_order_profit)):,}".replace(",", " ")
									last_order_info += f"\n💰 Профит от последней сделки: {profit_formatted} USD"
								except (ValueError, TypeError):
									last_order_info += f"\n💰 Профит от последней сделки: {last_order_profit} USD"
					
					# Получаем профит за текущий месяц
					monthly_profit = await db_local.get_user_monthly_profit(user_tg_id)
					if monthly_profit is not None:
						try:
							monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit_formatted} USD"
						except (ValueError, TypeError):
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit} USD"
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.debug(f"Ошибка получения информации о последней сделке: {e}", exc_info=True)
		
		# Формируем сообщение для админов
		admin_message_text = (
			f"❓ <b>Вопрос от пользователя</b>\n\n"
			f"👤 Имя: {user_name}\n"
			f"📱 Username: @{user_username}\n"
			f"🆔 ID: <code>{user_tg_id}</code>{last_order_info}\n\n"
			f"💬 <b>Вопрос:</b>\n{question_text}"
		)
		
		# Создаем клавиатуру с кнопкой "Ответить"
		from app.keyboards import question_reply_kb
		reply_keyboard = question_reply_kb(question_id)
		
		# Отправляем вопрос всем админам
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"📤 Отправка вопроса от пользователя {user_tg_id} админам. Список админов: {admin_ids}")
		
		if not admin_ids:
			logger_main.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Список админов пустой! Вопрос не будет отправлен.")
			await send_and_save_message(
				message,
				"❌ Произошла ошибка при отправке вопроса. Попробуйте позже.",
				reply_markup=client_menu_kb(),
				state=state
			)
			await state.clear()
			return
		
		# Отправляем вопрос первому админу и сохраняем admin_message_id
		admin_message_id = None
		for admin_id in admin_ids:
			try:
				sent_msg = await message.bot.send_message(
					chat_id=admin_id,
					text=admin_message_text,
					parse_mode=ParseMode.HTML,
					reply_markup=reply_keyboard
				)
				if admin_message_id is None:
					admin_message_id = sent_msg.message_id
					# Обновляем вопрос с admin_message_id
					await db_local.update_question_admin_message_id(question_id, admin_message_id)
				logger_main.info(f"✅ Вопрос отправлен админу {admin_id}")
			except Exception as e:
				logger_main.error(f"❌ Ошибка отправки вопроса админу {admin_id}: {e}", exc_info=True)
		
		if admin_message_id:
			# Формируем сообщение для пользователя с историей переписки
			question = await db_local.get_question_by_id(question_id)
			messages = await db_local.get_question_messages(question_id)
			
			# Формируем информацию о вопросе
			if question.get("initiated_by_admin"):
				question_info = "💬 <b>Сообщение администратора</b>\n\n"
			else:
				question_info = "❓ <b>Ваш вопрос</b>\n\n"
			
			# Добавляем историю переписки
			history_lines = []
			for msg in messages:
				if msg["sender_type"] == "admin":
					history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
				else:
					history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
			
			history_text = "\n\n".join(history_lines)
			user_message = question_info + history_text
			
			# Отправляем или обновляем сообщение пользователю
			from app.keyboards import question_user_reply_kb
			try:
				user_message_id = question.get("user_message_id")
				if user_message_id:
					# Обновляем существующее сообщение
					try:
						await message.bot.edit_message_text(
							chat_id=user_tg_id,
							message_id=user_message_id,
							text=user_message,
							parse_mode="HTML",
							reply_markup=question_user_reply_kb(question_id)
						)
					except Exception as e:
						# Если не удалось обновить, отправляем новое
						logger_main.warning(f"⚠️ Не удалось обновить сообщение {user_message_id}, отправляем новое: {e}")
						sent_msg = await message.bot.send_message(
							chat_id=user_tg_id,
							text=user_message,
							parse_mode="HTML",
							reply_markup=question_user_reply_kb(question_id)
						)
						await db_local.update_question_user_message_id(question_id, sent_msg.message_id)
				else:
					# Отправляем новое сообщение
					sent_msg = await message.bot.send_message(
						chat_id=user_tg_id,
						text=user_message,
						parse_mode="HTML",
						reply_markup=question_user_reply_kb(question_id)
					)
					await db_local.update_question_user_message_id(question_id, sent_msg.message_id)
			except Exception as e:
				logger_main.error(f"❌ Ошибка отправки сообщения пользователю: {e}", exc_info=True)
		else:
			await send_and_save_message(
				message,
				"❌ Произошла ошибка при отправке вопроса. Попробуйте позже.",
				reply_markup=client_menu_kb(),
				state=state
			)
		
		# Очищаем состояние
		await state.clear()
	
	@dp.callback_query(F.data.startswith("order:details:"))
	async def on_order_details(cb: CallbackQuery):
		"""Обработчик нажатия кнопки 'Дополнительно' для переключения состояния"""
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"🔵 ORDER_DETAILS: Получен callback: {cb.data}")
		
		if not cb.from_user:
			await cb.answer()
			return
		
		from app.di import get_db, get_admin_ids
		from app.admin import is_admin
		db_local = get_db()
		admin_ids = get_admin_ids()
		
		# Проверяем, что это админ
		if not is_admin(cb.from_user.id, cb.from_user.username, admin_ids, []):
			logger_main.warning(f"🔵 ORDER_DETAILS: Пользователь {cb.from_user.id} не является админом")
			await cb.answer("❌ У вас нет прав для выполнения этого действия.", show_alert=True)
			return
		
		# Парсим callback_data: order:details:{order_id} или order:details:{order_id}:expanded
		parts = cb.data.split(":")
		logger_main.info(f"🔵 ORDER_DETAILS: Парсинг callback_data: parts={parts}")
		
		if len(parts) < 3:
			await cb.answer("❌ Ошибка данных.", show_alert=True)
			return
		
		try:
			order_id = int(parts[2])
		except ValueError:
			await cb.answer("❌ Ошибка данных.", show_alert=True)
			return
		
		# Определяем текущее состояние:
		# Если в callback_data есть :expanded, значит кнопка была в обычном состоянии (expanded=False)
		# и мы переключаем на expanded (expanded=True)
		# Если нет :expanded, значит кнопка была в expanded состоянии (expanded=True)
		# и мы переключаем обратно на обычное (expanded=False)
		current_is_expanded = len(parts) <= 3 or parts[3] != "expanded"
		new_expanded = not current_is_expanded
		
		logger_main.info(f"🔵 ORDER_DETAILS: order_id={order_id}, current_is_expanded={current_is_expanded}, new_expanded={new_expanded}")
		
		# Получаем данные заявки
		order = await db_local.get_order_by_id(order_id)
		if not order:
			await cb.answer("❌ Заявка не найдена.", show_alert=True)
			return
		
		# Обновляем только клавиатуру, не меняя текст/caption
		# Кнопки теперь находятся на фото/документе, поэтому используем edit_message_reply_markup
		try:
			logger_main.info(f"🔵 ORDER_DETAILS: Обновление клавиатуры с expanded={new_expanded}")
			# Обновляем только клавиатуру, не трогая текст/caption
			await cb.message.edit_reply_markup(
				reply_markup=order_action_kb(order_id, expanded=new_expanded)
			)
			logger_main.info(f"🔵 ORDER_DETAILS: Сообщение успешно обновлено")
			await cb.answer()
		except Exception as e:
			# Если сообщение не изменилось, это нормально - просто отвечаем на callback
			if "message is not modified" in str(e):
				logger_main.debug(f"🔵 ORDER_DETAILS: Сообщение не изменилось (это нормально)")
				await cb.answer()
			# Сетевые ошибки - логируем, но не показываем пользователю
			elif "NetworkError" in str(type(e).__name__) or "ClientConnectorError" in str(e) or "ConnectionResetError" in str(e):
				logger_main.warning(f"⚠️ Сетевая ошибка при обновлении сообщения (временная): {e}")
				await cb.answer()  # Просто отвечаем на callback без ошибки
			else:
				logger_main.error(f"❌ Ошибка обновления сообщения: {e}", exc_info=True)
				await cb.answer("❌ Ошибка обновления сообщения.", show_alert=True)
	
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
		
		# Если это XMR, показываем выбор кошелька
		if order.get("crypto_type") == "XMR":
			from app.keyboards import xmr_wallet_select_kb
			try:
				# Обновляем сообщение с кнопками выбора кошелька
				if cb.message.photo:
					current_caption = cb.message.caption or ""
					await cb.message.edit_caption(
						caption=f"{current_caption}\n\n🪙 Выберите кошелек XMR:",
						reply_markup=xmr_wallet_select_kb(order_id)
					)
				elif cb.message.document:
					current_caption = cb.message.caption or ""
					await cb.message.edit_caption(
						caption=f"{current_caption}\n\n🪙 Выберите кошелек XMR:",
						reply_markup=xmr_wallet_select_kb(order_id)
					)
				else:
					await cb.message.edit_text(
						f"{cb.message.text}\n\n🪙 Выберите кошелек XMR:",
						reply_markup=xmr_wallet_select_kb(order_id)
					)
				await cb.answer()
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка обновления сообщения для выбора кошелька XMR: {e}")
				await cb.answer("❌ Ошибка обновления сообщения.", show_alert=True)
			return
		
		# Для остальных криптовалют выполняем завершение сразу
		await _complete_order_with_wallet(cb, order_id, order, db_local, None)
	
	async def _complete_order_with_wallet(cb: CallbackQuery, order_id: int, order: dict, db_local, xmr_number: int | None = None):
		"""Вспомогательная функция для завершения заявки с указанным номером кошелька XMR (если применимо)"""
		# Отмечаем заявку как выполненную (profit будет обновлен позже, если есть)
		# Пока отмечаем без профита, профит обновим после получения из Google Sheets
		await db_local.complete_order(order_id)
		
		user_message = _build_order_completion_message(order)
		
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
		
		# Сообщение с перепиской (нужно удалить)
		user_message_id = order.get("user_message_id")
		if user_message_id:
			messages_to_delete.append(user_message_id)
		
		# Удаляем все сообщения
		for msg_id in messages_to_delete:
			try:
				await cb.bot.delete_message(
					chat_id=user_tg_id,
					message_id=msg_id
				)
			except Exception as e:
				logging.getLogger("app.main").debug(f"Не удалось удалить сообщение {msg_id} у пользователя {user_tg_id}: {e}")
		
		# Удаляем зависшие уведомления у пользователя и админа
		from app.notifications import notification_ids
		from app.di import get_admin_ids
		admin_ids = get_admin_ids()
		
		# Удаляем уведомление пользователю
		user_notif_key = (user_tg_id, order_id, 'order')
		if user_notif_key in notification_ids:
			try:
				notif_message_id = notification_ids[user_notif_key]
				await cb.bot.delete_message(chat_id=user_tg_id, message_id=notif_message_id)
			except Exception as e:
				logging.getLogger("app.main").debug(f"Не удалось удалить уведомление пользователю: {e}")
			finally:
				del notification_ids[user_notif_key]
		
		# Удаляем уведомление админу
		if admin_ids:
			admin_notif_key = (admin_ids[0], order_id, 'order')
			if admin_notif_key in notification_ids:
				try:
					notif_message_id = notification_ids[admin_notif_key]
					await cb.bot.delete_message(chat_id=admin_ids[0], message_id=notif_message_id)
				except Exception as e:
					logging.getLogger("app.main").debug(f"Не удалось удалить уведомление админу: {e}")
				finally:
					del notification_ids[admin_notif_key]
		
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
		
		# Отправляем стикер пользователю
		try:
			await cb.bot.send_sticker(
				chat_id=order["user_tg_id"],
				sticker="CAACAgIAAxkBAAEVPMRpZ3yqu0lezCX6Gr6tMGiJnBBj7QACYAYAAvoLtgg_BZcxRs21uzgE"
			)
		except Exception as e:
			logging.getLogger("app.main").error(f"Ошибка отправки стикера пользователю {order['user_tg_id']}: {e}")
		
		# Показываем клавиатуру пользователю (как при /start)
		try:
			from app.keyboards import client_menu_kb
			# Отправляем сообщение с клавиатурой
			# Используем короткое сообщение, чтобы клавиатура точно отобразилась
			await cb.bot.send_message(
				chat_id=order["user_tg_id"],
				text="Выберите действие:",
				reply_markup=client_menu_kb()
			)
		except Exception as e:
			logging.getLogger("app.main").error(f"Ошибка отправки клавиатуры пользователю {order['user_tg_id']}: {e}")
		
		# Записываем данные в Google Sheets
		from app.config import get_settings
		from app.google_sheets import write_order_to_google_sheet, read_profit
		settings = get_settings()
		written_cells_info = []
		profit_value = None
		row_number = None
		
		if settings.google_sheet_id and settings.google_credentials_path:
			try:
				result = await write_order_to_google_sheet(
					sheet_id=settings.google_sheet_id,
					credentials_path=settings.google_credentials_path,
					order=order,
					db=db_local,
					sheet_name=settings.google_sheet_name,
					xmr_number=xmr_number
				)
				if result.get("success"):
					logger_main = logging.getLogger("app.main")
					logger_main.info(f"✅ Данные заявки {order_id} записаны в Google Sheets")
					
					# Получаем информацию о записанных ячейках
					written_cells = result.get("written_cells", [])
					row_number = result.get("row")
					
					# Формируем список записанных ячеек
					if written_cells:
						written_cells_info = written_cells
					
					# Читаем профит из ячейки BC
					if row_number:
						profit_column = await db_local.get_google_sheets_setting("profit_column", "BC")
						profit_value = await read_profit(
							sheet_id=settings.google_sheet_id,
							credentials_path=settings.google_credentials_path,
							row=row_number,
							profit_column=profit_column,
							sheet_name=settings.google_sheet_name
						)
				else:
					logger_main = logging.getLogger("app.main")
					logger_main.warning(f"⚠️ Не удалось записать данные заявки {order_id} в Google Sheets: {result.get('error', 'Unknown error')}")
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка записи данных заявки {order_id} в Google Sheets: {e}", exc_info=True)
		else:
			logger_main = logging.getLogger("app.main")
			logger_main.warning("⚠️ Google Sheets не настроен, пропускаем запись данных заявки")
		
		# Формируем дополнительную информацию для сообщения
		additional_info = "\n\n✅ Выполнено"
		
		written_entries_info = result.get("written_entries", [])
		if written_cells_info or profit_value is not None or written_entries_info:
			additional_info += "\n\n📊 Записано в Google Sheets:"
			
			# Добавляем информацию о записанных ячейках
			if written_cells_info:
				for cell_info in written_cells_info:
					additional_info += f"\n  • {cell_info}"
			
			# Добавляем профит
			if profit_value is not None:
				try:
					# Пытаемся форматировать как число
					profit_num = float(str(profit_value).replace(",", ".").replace(" ", ""))
					profit_formatted = f"{int(round(profit_num)):,}".replace(",", " ")
					additional_info += f"\n\n📈 Профит: {profit_formatted} USD"
				except (ValueError, AttributeError):
					# Если не число, используем как есть
					additional_info += f"\n\n📈 Профит: {profit_value} USD"
			if written_entries_info:
				additional_info += "\n\n➖➖➖➖➖➖➖➖➖➖➖"
				additional_info += "\nЗаписано в таблицу:"
				for entry in written_entries_info:
					entry_type = entry.get("type")
					cell = entry.get("cell", "")
					amount = entry.get("amount")
					currency = entry.get("currency", "")
					if entry_type == "card":
						group_name = entry.get("group", "Без группы")
						card_name = entry.get("card", "")
						label = f"{group_name}:{card_name}"
					elif entry_type == "crypto":
						label = entry.get("label", "")
					else:
						continue
					additional_info += f"\n{label}({cell}) = {amount} {currency}".rstrip()
			
			# Сохраняем информацию о последней сделке и профите пользователя
			try:
				profit_num = None
				if profit_value is not None:
					try:
						profit_num = float(str(profit_value).replace(",", ".").replace(" ", ""))
					except (ValueError, AttributeError):
						pass
				# Обновляем профит в таблице orders (если есть)
				if profit_num is not None:
					await db_local.complete_order(order_id, profit_num)
				# Всегда обновляем информацию о последней сделке пользователя (даже если профита нет)
				await db_local.update_user_last_order(order["user_tg_id"], order_id, profit_num)
			except Exception as e:
				logging.getLogger("app.main").warning(f"Ошибка сохранения информации о последней сделке: {e}")
		
		# Обновляем сообщение админа
		await cb.answer("✅ Заявка отмечена как выполненная!")
		# Проверяем тип сообщения (кнопки теперь на фото/документе)
		try:
			if cb.message.photo:
				# Это фото - используем edit_message_caption
				current_caption = cb.message.caption or ""
				await cb.message.edit_caption(
					caption=f"{current_caption}{additional_info}",
					reply_markup=None
				)
			elif cb.message.document:
				# Это документ - используем edit_message_caption
				current_caption = cb.message.caption or ""
				await cb.message.edit_caption(
					caption=f"{current_caption}{additional_info}",
					reply_markup=None
				)
			else:
				# Это текстовое сообщение - используем edit_text
				await cb.message.edit_text(
					f"{cb.message.text}{additional_info}",
					reply_markup=None
				)
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.error(f"❌ Ошибка обновления сообщения админа: {e}")
	
	@dp.callback_query(F.data.startswith("order:xmr:wallet:"))
	async def on_xmr_wallet_selected(cb: CallbackQuery, state: FSMContext):
		"""Обработчик выбора кошелька XMR при завершении заявки"""
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
		
		# Формат: order:xmr:wallet:{order_id}:{xmr_number}
		parts = cb.data.split(":")
		if len(parts) < 5:
			await cb.answer("❌ Ошибка данных.", show_alert=True)
			return
		
		order_id = int(parts[3])
		xmr_number = int(parts[4])
		
		# Получаем данные заявки
		order = await db_local.get_order_by_id(order_id)
		if not order:
			await cb.answer("❌ Заявка не найдена.", show_alert=True)
			return
		
		# Выполняем завершение заявки с выбранным номером кошелька
		await _complete_order_with_wallet(cb, order_id, order, db_local, xmr_number)
	
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
				
				# Отправляем главное меню с кнопками "Купить" и "Продать"
				from app.keyboards import client_menu_kb
				from app.di import get_db
				db_local = get_db()
				if await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
					# Используем невидимый символ вместо пробела (Telegram не принимает пустой текст)
					await cb.bot.send_message(
						chat_id=cb.from_user.id,
						text="\u200b",  # Невидимый символ (zero-width space)
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
		await state.clear()
		user_name = message.from_user.full_name or ""
		user_username = message.from_user.username or ""
		active_deal_id = await db_local.get_active_buy_deal_by_user(message.from_user.id)
		if active_deal_id:
			await db_local.update_buy_deal_fields(active_deal_id, status="cancelled")
		deal_id = await db_local.create_buy_deal(
			user_tg_id=message.from_user.id,
			user_name=user_name,
			user_username=user_username,
			status="draft"
		)
		await state.set_state(DealStates.selecting_country)
		message_text = await _build_deal_message(
			country_code=None,
			crypto_code=None,
			amount=None,
			amount_currency=None,
			currency_symbol=None,
			prompt=None
		)
		deal_message_id = await _send_or_edit_deal_message(
			bot=message.bot,
			chat_id=message.chat.id,
			state=state,
			text=message_text,
			reply_markup=buy_country_inline_kb()
		)
		await state.update_data(
			deal_id=deal_id,
			deal_message_id=deal_message_id,
			order_message_id=deal_message_id,
			last_bot_message_id=None
		)
		await db_local.update_buy_deal_user_message_id(deal_id, deal_message_id)

	@dp.message(Command("sell"))
	async def cmd_sell(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		# Очищаем состояние при начале новой продажи
		await state.clear()
		await state.set_state(SellStates.selecting_crypto)
		from app.keyboards import sell_crypto_kb
		await send_and_save_message(message, "Выберите криптовалюту для продажи:", reply_markup=sell_crypto_kb(), state=state)

	@dp.message(Command("question"))
	async def cmd_question(message: Message, state: FSMContext):
		"""Обработчик команды /question для вопроса пользователя"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Переводим в состояние ожидания вопроса
		await state.set_state(QuestionStates.waiting_question)
		await send_and_save_message(
			message,
			"📝 Пожалуйста, введите ваш вопрос. Администратор получит ваше сообщение и свяжется с вами.",
			state=state
		)

	@dp.message(SellStates.selecting_crypto, F.text == "⬅️ Назад")
	async def on_sell_back_to_menu(message: Message, state: FSMContext):
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		await delete_user_message(message)
		await state.clear()
		await send_and_save_message(message, "Главное меню", reply_markup=client_menu_kb(), state=state)

	@dp.message(SellStates.waiting_amount)
	async def on_sell_amount_entered(message: Message, state: FSMContext):
		"""Обработчик ввода суммы для продажи криптовалюты"""
		if not message.from_user:
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Проверяем, не является ли это командой - если да, пропускаем обработку
		# чтобы команда обработалась в своем обработчике
		if message.text and message.text.startswith("/"):
			return  # Пропускаем команды, они обработаются в своих обработчиках
		
		if message.text == "⬅️ Назад":
			await delete_user_message(message)
			data = await state.get_data()
			last_bot_message_id = data.get("last_bot_message_id")
			await state.clear()
			await state.set_state(SellStates.selecting_crypto)
			if last_bot_message_id:
				await state.update_data(last_bot_message_id=last_bot_message_id)
			await send_and_save_message(message, "Выберите криптовалюту для продажи:", reply_markup=sell_crypto_kb(), state=state)
			return
		
		await delete_user_message(message)
		
		data = await state.get_data()
		crypto_name = data.get("selected_crypto", "")
		crypto_display = data.get("crypto_display", "")
		
		amount_str = message.text.strip().replace(",", ".")
		
		try:
			amount = float(amount_str)
			if amount <= 0:
				await send_and_save_message(message, "❌ Сумма должна быть больше нуля. Введите корректную сумму:", state=state)
				return
		except ValueError:
			await send_and_save_message(message, "❌ Неверный формат суммы. Введите число (например: 0.008 или 100):", state=state)
			return
		
		# Определяем тип криптовалюты
		crypto_type = None
		if "BTC" in crypto_name or "Bitcoin" in crypto_name:
			crypto_type = "BTC"
		elif "LTC" in crypto_name or "Litecoin" in crypto_name:
			crypto_type = "LTC"
		elif "USDT" in crypto_name:
			crypto_type = "USDT"
		elif "XMR" in crypto_name or "Monero" in crypto_name:
			crypto_type = "XMR"
		
		# Получаем курс криптовалюты в USD
		from app.google_sheets import get_btc_price_usd, get_ltc_price_usd, get_xmr_price_usd
		
		crypto_price_usd = None
		if crypto_type == "BTC":
			crypto_price_usd = await get_btc_price_usd()
		elif crypto_type == "LTC":
			crypto_price_usd = await get_ltc_price_usd()
		elif crypto_type == "USDT":
			crypto_price_usd = 1.0
		elif crypto_type == "XMR":
			crypto_price_usd = await get_xmr_price_usd()
		
		if crypto_price_usd is None:
			await send_and_save_message(message, "❌ Не удалось получить курс криптовалюты. Попробуйте позже.", state=state)
			return
		
		# Используем RUB по умолчанию
		usd_to_currency_rate = 95.0
		currency_symbol = "₽"
		
		# Рассчитываем сумму в валюте (без наценки для продажи)
		amount_currency = crypto_price_usd * amount * usd_to_currency_rate
		
		# Сохраняем данные
		await state.update_data(
			amount=amount,
			amount_currency=amount_currency,
			crypto_type=crypto_type,
			crypto_price_usd=crypto_price_usd,
			currency_symbol=currency_symbol,
			usd_to_currency_rate=usd_to_currency_rate
		)
		
		# Формируем сообщение с информацией
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		confirmation_text = (
			f"💰 Криптовалюта: {crypto_display}\n"
			f"💵 Сумма: {amount_str} {crypto_display}"
		)
		
		from app.keyboards import sell_confirmation_kb
		await state.set_state(SellStates.waiting_confirmation)
		
		bot = message.bot
		chat_id = message.chat.id
		
		previous_message_id = None
		if state:
			data = await state.get_data()
			previous_message_id = data.get("last_bot_message_id")
		
		if previous_message_id:
			try:
				await bot.delete_message(chat_id=chat_id, message_id=previous_message_id)
			except:
				pass
		
		sent_message = await bot.send_message(
			chat_id=chat_id,
			text=confirmation_text,
			reply_markup=sell_confirmation_kb(),
			parse_mode="HTML"
		)
		
		if state:
			await state.update_data(last_bot_message_id=sent_message.message_id)

	@dp.callback_query(F.data == "sell:confirm:yes", SellStates.waiting_confirmation)
	async def on_sell_confirm_yes(cb: CallbackQuery, state: FSMContext):
		"""Обработчик подтверждения продажи"""
		if not cb.from_user:
			await cb.answer()
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			await cb.answer()
			return
		
		await cb.answer()
		
		# Получаем данные о продаже
		data = await state.get_data()
		amount = data.get("amount", 0)
		amount_currency = data.get("amount_currency", 0)
		crypto_type = data.get("crypto_type", "")
		crypto_display = data.get("crypto_display", "")
		currency_symbol = data.get("currency_symbol", "₽")
		
		# Форматируем суммы
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Получаем информацию о пользователе
		user_tg_id = cb.from_user.id
		user_name = cb.from_user.full_name or "Не указано"
		user_username = cb.from_user.username or "Не указано"
		
		# Создаем заявку на продажу в БД
		from app.keyboards import sell_order_admin_kb
		bot = cb.bot
		
		# Формируем сообщение для админа
		admin_message_text = (
			f"💰 <b>Заявка на продажу</b>\n\n"
			f"📊 Номер заявки: #{{order_number}}\n"
			f"👤 Имя: {user_name}\n"
			f"📱 Username: @{user_username}\n"
			f"🆔 ID: <code>{user_tg_id}</code>\n\n"
			f"💵 Криптовалюта: {crypto_display}\n"
			f"💸 Сумма: {amount_str} {crypto_display}\n"
			f"💰 К получению: {int(amount_currency)} {currency_symbol}"
		)
		
		# Отправляем заявку всем админам
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"📤 Отправка заявки на продажу от пользователя {user_tg_id} админам. Список админов: {admin_ids}")
		
		if not admin_ids:
			logger_main.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Список админов пустой! Заявка не будет отправлена.")
			await cb.message.edit_text("❌ Произошла ошибка при отправке заявки. Попробуйте позже.")
			await state.clear()
			return
		
		sent_to_admins = False
		admin_message_id = None
		for admin_id in admin_ids:
			try:
				# Сначала отправляем сообщение без номера заявки
				sent_msg = await bot.send_message(
					chat_id=admin_id,
					text=admin_message_text.format(order_number="..."),
					parse_mode="HTML",
					reply_markup=sell_order_admin_kb(0)  # Временно 0, обновим после создания заявки
				)
				admin_message_id = sent_msg.message_id
				sent_to_admins = True
				break  # Отправляем только первому админу
			except Exception as e:
				logger_main.error(f"❌ Ошибка отправки заявки админу {admin_id}: {e}")
		
		if not sent_to_admins:
			await cb.message.edit_text("❌ Произошла ошибка при отправке заявки. Попробуйте позже.")
			await state.clear()
			return
		
		# Создаем заявку в БД
		order_id = await db_local.create_sell_order(
			user_tg_id=user_tg_id,
			user_name=user_name,
			user_username=user_username,
			crypto_type=crypto_type,
			crypto_display=crypto_display,
			amount=amount,
			amount_currency=amount_currency,
			currency_symbol=currency_symbol,
			admin_message_id=admin_message_id
		)
		
		# Получаем номер заявки
		order = await db_local.get_sell_order_by_id(order_id)
		order_number = order["order_number"] if order else order_id
		
		# Обновляем сообщение админу с правильным номером заявки и клавиатурой
		try:
			await bot.edit_message_text(
				chat_id=admin_ids[0],
				message_id=admin_message_id,
				text=admin_message_text.format(order_number=order_number),
				parse_mode="HTML",
				reply_markup=sell_order_admin_kb(order_id)
			)
		except Exception as e:
			logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}")
		
		# Уведомляем пользователя
		await cb.message.edit_text(
			f"✅ Ваша заявка на продажу принята!\n\n"
			f"💵 Криптовалюта: {crypto_display}\n"
			f"💸 Сумма: {amount_str} {crypto_display}\n\n"
			f"Администратор свяжется с вами в ближайшее время.",
			parse_mode="HTML"
		)
		
		await state.clear()

	@dp.callback_query(F.data == "sell:confirm:no", SellStates.waiting_confirmation)
	async def on_sell_confirm_no(cb: CallbackQuery, state: FSMContext):
		"""Обработчик отказа от продажи"""
		if not cb.from_user:
			await cb.answer()
			return
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			await cb.answer()
			return
		
		await cb.answer()
		
		# Возвращаемся в главное меню
		await cb.message.edit_text("❌ Заявка отменена.")
		await state.clear()
		await cb.message.bot.send_message(
			chat_id=cb.message.chat.id,
			text="Главное меню",
			reply_markup=client_menu_kb()
		)

	# ВАЖНО: Сначала включаем admin_router, чтобы команды из него обрабатывались первыми
	dp.include_router(admin_router)

	# Обработчик кнопки "Ответить" для пользователя по обычной заявке
	# ВАЖНО: Должен быть ПЕРЕД обработчиком без состояния, чтобы иметь приоритет
	@dp.callback_query(F.data.startswith("order:user:reply:"))
	async def on_order_user_reply_start(cb: CallbackQuery, state: FSMContext):
		"""Обработчик начала ответа пользователя на сообщение админа по обычной заявке"""
		if not cb.from_user:
			await cb.answer()
			return
		
		# Формат: order:user:reply:{order_id}
		parts = cb.data.split(":")
		if len(parts) < 4:
			await cb.answer("Ошибка данных", show_alert=True)
			return
		
		try:
			order_id = int(parts[3])
		except ValueError:
			await cb.answer("Ошибка данных", show_alert=True)
			return
		
		from app.di import get_db
		db_local = get_db()
		
		# Проверяем, что заявка принадлежит пользователю
		order = await db_local.get_order_by_id(order_id)
		if not order or order["user_tg_id"] != cb.from_user.id:
			await cb.answer("Заявка не найдена", show_alert=True)
			return
		
		# Проверяем, не завершена ли заявка
		if order.get("completed_at"):
			await cb.answer("Заявка уже завершена", show_alert=True)
			return
		
		# Сохраняем order_id в состоянии
		await state.update_data(order_id=order_id)
		
		# Переводим в состояние ожидания ответа
		await state.set_state(OrderUserReplyStates.waiting_reply)
		
		# Удаляем уведомление о новом сообщении (если есть)
		notification_key = (cb.from_user.id, order_id, 'order')
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"🔵 Удаление уведомления: key={notification_key}, exists={notification_key in notification_ids}, all_keys={list(notification_ids.keys())}")
		if notification_key in notification_ids:
			try:
				notif_message_id = notification_ids[notification_key]
				logger_main.info(f"🔵 Удаление уведомления: message_id={notif_message_id}, chat_id={cb.from_user.id}")
				await cb.bot.delete_message(chat_id=cb.from_user.id, message_id=notif_message_id)
				del notification_ids[notification_key]
				logger_main.info(f"✅ Уведомление успешно удалено")
			except Exception as e:
				# Если не удалось удалить уведомление, продолжаем работу
				logger_main.warning(f"⚠️ Не удалось удалить уведомление: {e}")
		
		# Уведомляем пользователя
		await cb.message.edit_text(
			cb.message.text + "\n\n📝 Введите ваш ответ:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
		await cb.answer()
	
	@dp.message(OrderUserReplyStates.waiting_reply)
	async def on_order_user_reply_send(message: Message, state: FSMContext):
		"""Обработчик отправки ответа пользователя на сообщение админа по обычной заявке"""
		if not message.from_user:
			return
		
		from app.di import get_db
		db_local = get_db()
		
		# Получаем данные из состояния
		data = await state.get_data()
		order_id = data.get("order_id")
		
		if not order_id:
			await message.answer("❌ Ошибка: не найден ID заявки")
			await state.clear()
			return
		
		# Получаем текст ответа
		reply_text = message.text or message.caption or ""
		if not reply_text.strip():
			await message.answer("❌ Пожалуйста, введите текст ответа.")
			return
		
		# Получаем информацию о заявке
		order = await db_local.get_order_by_id(order_id)
		if not order:
			await message.answer("❌ Заявка не найдена")
			await state.clear()
			return
		
		# Проверяем, что заявка принадлежит пользователю
		if order["user_tg_id"] != message.from_user.id:
			await message.answer("❌ Это не ваша заявка")
			await state.clear()
			return
		
		# Проверяем, не завершена ли заявка
		if order.get("completed_at"):
			await message.answer("❌ Заявка уже завершена. Вы не можете отправлять сообщения по завершенной заявке.")
			await state.clear()
			return
		
		# Сохраняем сообщение в истории переписки
		await db_local.add_buy_order_message(order_id, "user", reply_text)
		
		# Получаем всю историю переписки
		messages = await db_local.get_buy_order_messages(order_id)
		
		# Формируем информацию о заявке
		order_number = order["order_number"]
		crypto_display = order["crypto_display"]
		amount = order["amount"]
		amount_currency = order.get("amount_currency", 0)
		currency_symbol = order.get("currency_symbol", "₽")
		
		# Форматируем сумму
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Формируем полное сообщение для пользователя: информация о заявке + история (без номера заявки)
		order_info = (
			f"💵 Криптовалюта: {crypto_display}\n"
			f"💸 Сумма: {amount_str} {crypto_display}\n"
			f"💰 К оплате: {int(amount_currency)} {currency_symbol}\n"
		)
		
		# Добавляем историю переписки
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
		
		history_text = "\n\n".join(history_lines)
		user_message = order_info + "\n" + history_text
		
		# Обновляем сообщение пользователя
		from app.keyboards import order_user_reply_kb
		try:
			user_message_id = order.get("user_message_id")
			if user_message_id:
				await message.bot.edit_message_text(
					chat_id=message.from_user.id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=order_user_reply_kb(order_id)
				)
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)
		
		# Обновляем сообщение админа
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"🔵 USER_REPLY: Проверка обновления сообщения админа: admin_ids={admin_ids}, admin_message_id={order.get('admin_message_id')}")
		if admin_ids and order.get("admin_message_id"):
			user_name = order.get("user_name", "Не указано")
			user_username = order.get("user_username", "Не указано")
			user_tg_id = order["user_tg_id"]
			wallet_address = order.get("wallet_address", "")
			
			admin_order_info = (
				f"Имя пользователя: {user_name or 'Не указано'}\n"
				f"Username: @{user_username}\n\n"
				f"Количество монет: {amount_str} {crypto_display}\n"
				f"Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
				f"Адрес кошелька: <code>{wallet_address}</code>"
			)
		
		# Алерт по крупной заявке (с кнопкой "Написать", когда есть order_id)
		try:
			alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
			alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
		except (ValueError, TypeError):
			alert_threshold = 400.0
		
		total_usd = data.get("total_usd")
		if total_usd is None:
			crypto_price_with_markup = data.get("crypto_price_with_markup")
			if crypto_price_with_markup:
				total_usd = crypto_price_with_markup * amount
			else:
				crypto_price_usd = data.get("crypto_price_usd", 0)
				markup_percent = data.get("markup_percent", 0)
				amount_usd = amount * crypto_price_usd
				total_usd = amount_usd * (1 + (markup_percent / 100))
		
		if total_usd and total_usd >= alert_threshold:
			alert_text = (
				f"🚨 <b>Крупная заявка</b>\n\n"
				f"Номер заявки: {order_number}\n"
				f"Пользователь: {user_name or 'Не указано'} (@{user_username})\n"
				f"Сумма: {total_usd:.2f}$\n"
				f"Крипта: {crypto_display}\n"
				f"Кол-во: {amount}"
			)
			admin_ids = get_admin_ids()
			for admin_id in admin_ids:
				try:
					await message.bot.send_message(
						chat_id=admin_id,
						text=alert_text,
						parse_mode=ParseMode.HTML,
						reply_markup=order_action_kb(order_id, expanded=True)
					)
				except Exception as e:
					logging.getLogger("app.main").warning(
						f"⚠️ Не удалось отправить алерт админу {admin_id}: {e}"
					)
			
			# Алерт, если сумма в USD превышает порог
			try:
				alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
				alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
			except (ValueError, TypeError):
				alert_threshold = 400.0
			
			total_usd = data.get("total_usd")
			if total_usd is None:
				crypto_price_with_markup = data.get("crypto_price_with_markup")
				if crypto_price_with_markup:
					total_usd = crypto_price_with_markup * amount
				else:
					crypto_price_usd = data.get("crypto_price_usd", 0)
					markup_percent = data.get("markup_percent", 0)
					amount_usd = amount * crypto_price_usd
					total_usd = amount_usd * (1 + (markup_percent / 100))
			
			if total_usd and total_usd >= alert_threshold:
				alert_text = (
					f"🚨 <b>Крупная заявка</b>\n\n"
					f"Номер заявки: {order_number}\n"
					f"Пользователь: {user_name or 'Не указано'} (@{user_username})\n"
					f"Сумма: {total_usd:.2f}$\n"
					f"Крипта: {crypto_display}\n"
					f"Кол-во: {amount}"
				)
				for admin_id in admin_ids:
					try:
						await message.bot.send_message(
							chat_id=admin_id,
							text=alert_text,
							parse_mode=ParseMode.HTML
						)
					except Exception as e:
						logging.getLogger("app.main").warning(
							f"⚠️ Не удалось отправить алерт админу {admin_id}: {e}"
						)
			
			try:
				admin_history_lines = []
				for msg in messages:
					if msg["sender_type"] == "admin":
						admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
					else:
						admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
				
				admin_history_text = "\n\n".join(admin_history_lines)
				admin_message = admin_order_info + "\n\n" + admin_history_text
				
				from app.keyboards import order_action_kb
				# Используем расширенную клавиатуру, если есть переписка
				is_expanded = len(messages) > 0
				logger_main.info(f"🔵 USER_REPLY: Обновление сообщения админа: chat_id={admin_ids[0]}, message_id={order['admin_message_id']}, messages_count={len(messages)}")
				# Отправляем уведомление перед обновлением
				try:
					notif_msg = await message.bot.send_message(
						chat_id=admin_ids[0],
						text="💬 <b>Новое сообщение от пользователя</b>",
						parse_mode="HTML"
					)
					# Сохраняем ID уведомления
					notification_ids[(admin_ids[0], order_id, 'order')] = notif_msg.message_id
				except Exception as e:
					# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
					logger_main.warning(f"⚠️ Не удалось отправить уведомление админу {admin_ids[0]}: {e}")
				# Пытаемся обновить как caption (для фото/документа), если не получится - как текст
				try:
					await message.bot.edit_message_caption(
						chat_id=admin_ids[0],
						message_id=order["admin_message_id"],
						caption=admin_message,
						parse_mode="HTML",
						reply_markup=order_action_kb(order_id, expanded=is_expanded)
					)
				except Exception as e:
					# Если не получилось (это текстовое сообщение), используем edit_text
					logger_main.debug(f"Не удалось обновить caption, пробуем edit_text: {e}")
					await message.bot.edit_message_text(
						chat_id=admin_ids[0],
						message_id=order["admin_message_id"],
						text=admin_message,
						parse_mode="HTML",
						reply_markup=order_action_kb(order_id, expanded=is_expanded)
					)
				logger_main.info(f"✅ Сообщение админа обновлено после ответа пользователя по заявке {order_id}")
				
				# Отправляем временное уведомление пользователю
				notif_msg = await message.bot.send_message(
					chat_id=message.from_user.id,
					text="✅ Сообщение отправлено администратору"
				)
				await asyncio.sleep(2)
				try:
					await message.bot.delete_message(chat_id=message.from_user.id, message_id=notif_msg.message_id)
				except:
					pass
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}", exc_info=True)
		else:
			logger_main = logging.getLogger("app.main")
			logger_main.warning(f"⚠️ USER_REPLY: Не удалось обновить сообщение админа: admin_ids={admin_ids}, admin_message_id={order.get('admin_message_id')}")
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Очищаем состояние
		await state.clear()

	# Обработчик ответов пользователя на вопросы админа (должен быть ПЕРВЫМ, чтобы перехватывать сообщения с активными вопросами)
	# УБРАЛИ StateFilter(None) - обработчик должен работать в любом состоянии, если есть активный вопрос
	@dp.message(
		is_not_admin_message,
		~(F.forward_origin.as_(bool) | F.forward_from.as_(bool)),
		~(F.text.startswith("/") if F.text else False)
	)
	async def on_user_reply_to_question(message: Message, state: FSMContext):
		"""Обработчик ответов пользователя на вопросы админа"""
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"🔵🔵🔵 on_user_reply_to_question: НАЧАЛО ОБРАБОТКИ")
		logger_main.info(f"🔵🔵🔵 on_user_reply_to_question: message_id={message.message_id}, from_user={message.from_user.id if message.from_user else None}, text='{message.text or message.caption or ''}'")
		
		current_state = await state.get_state()
		logger_main.info(f"🔵🔵🔵 on_user_reply_to_question: current_state={current_state}")
		logger_main.info(f"🔵🔵🔵 on_user_reply_to_question: forward_origin={getattr(message, 'forward_origin', None)}, forward_from={getattr(message, 'forward_from', None)}")
		
		if not message.from_user:
			logger_main.info(f"❌ on_user_reply_to_question: нет from_user")
			return
		
		# Проверяем, не является ли отправитель админом - если да, пропускаем обработку
		from app.admin import is_admin
		from app.di import get_admin_ids, get_admin_usernames
		admin_ids = get_admin_ids()
		admin_usernames = get_admin_usernames()
		user_id = message.from_user.id
		username = message.from_user.username
		if is_admin(user_id, username, admin_ids, admin_usernames):
			logger_main.info(f"🔵🔵🔵 on_user_reply_to_question: сообщение от админа, пропускаем обработку")
			return
		
		from app.di import get_db
		db_local = get_db()
		
		# Проверяем, есть ли у пользователя активный вопрос
		user_tg_id = message.from_user.id
		
		# Получаем последний активный вопрос пользователя
		question_id = await db_local.get_active_question_by_user(user_tg_id)
		logger_main.info(f"🔍 on_user_reply_to_question: question_id={question_id} для user_tg_id={user_tg_id}")
		
		if not question_id:
			# Нет активного вопроса, пропускаем обработку
			logger_main.info(f"❌ on_user_reply_to_question: нет активного вопроса для user_tg_id={user_tg_id}")
			return
		
		# Получаем информацию о вопросе
		question = await db_local.get_question_by_id(question_id)
		if not question:
			return
		
		# Проверяем, не закрыт ли вопрос
		if question.get("completed_at"):
			# Вопрос закрыт, не обрабатываем ответ
			return
		
		# Получаем текст сообщения
		message_text = message.text or message.caption or ""
		if not message_text.strip():
			return
		
		# Сохраняем сообщение в истории переписки
		await db_local.add_question_message(question_id, "user", message_text)
		
		# Получаем всю историю переписки
		messages = await db_local.get_question_messages(question_id)
		
		# Формируем сообщение для админа с историей
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
		
		admin_message = "\n\n".join(history_lines)
		history_text = "\n\n".join(history_lines) if history_lines else ""
		
		# Проверяем, является ли это вопросом для крупной заявки
		from app.main import large_order_alerts
		logger_main.info(f"🔍 on_user_reply_to_question: user_tg_id={user_tg_id}, question_id={question_id}")
		logger_main.info(f"🔍 on_user_reply_to_question: large_order_alerts содержит: {list(large_order_alerts.keys())}")
		
		# Проверяем, есть ли активная крупная заявка для этого пользователя
		order_id = await db_local.get_active_order_by_user(user_tg_id)
		is_large_order = False
		if order_id:
			order = await db_local.get_order_by_id(order_id)
			if order:
				# Проверяем, является ли это крупной заявкой
				alert_threshold_str = await db_local.get_setting("buy_alert_usd_threshold", "400")
				try:
					alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
				except (ValueError, TypeError):
					alert_threshold = 400.0
				total_usd = order.get("total_usd", 0)
				is_large_order = total_usd >= alert_threshold
				logger_main.info(f"🔍 on_user_reply_to_question: order_id={order_id}, total_usd={total_usd}, alert_threshold={alert_threshold}, is_large_order={is_large_order}")
		
		if user_tg_id in large_order_alerts or is_large_order:
			# Если записи нет, но есть крупная заявка, создаем запись
			if user_tg_id not in large_order_alerts:
				logger_main.info(f"⚠️ on_user_reply_to_question: создаем запись в large_order_alerts для user_tg_id={user_tg_id}")
				large_order_alerts[user_tg_id] = {"message_ids": {}, "question_id": question_id}
			
			user_data = large_order_alerts[user_tg_id]
			logger_main.info(f"🔍 on_user_reply_to_question: user_data={user_data}")
			
			# Поддерживаем обратную совместимость
			if not isinstance(user_data, dict):
				# Старая структура
				old_data = user_data
				large_order_alerts[user_tg_id] = {"message_ids": old_data, "question_id": question_id}
				user_data = large_order_alerts[user_tg_id]
			elif "message_ids" not in user_data:
				# Старая структура dict, но без message_ids
				old_data = user_data.copy()
				large_order_alerts[user_tg_id] = {"message_ids": old_data, "question_id": question_id}
				user_data = large_order_alerts[user_tg_id]
			
			stored_question_id = user_data.get("question_id")
			logger_main.info(f"🔍 on_user_reply_to_question: stored_question_id={stored_question_id}, question_id={question_id}")
			
			# Если question_id не сохранен, но есть активная крупная заявка, сохраняем его
			if stored_question_id is None and is_large_order:
				logger_main.info(f"⚠️ on_user_reply_to_question: question_id не сохранен, но есть крупная заявка, сохраняем его")
				large_order_alerts[user_tg_id]["question_id"] = question_id
				stored_question_id = question_id
			
			# Если question_id совпадает или это крупная заявка, обновляем сообщение
			should_update = stored_question_id == question_id or (is_large_order and (stored_question_id is None or stored_question_id == question_id))
			logger_main.info(f"🔍 on_user_reply_to_question: should_update={should_update}, stored_question_id={stored_question_id}, question_id={question_id}, is_large_order={is_large_order}")
			
			if should_update:
					# Это вопрос для крупной заявки, обновляем сообщение о крупной заявке
					if isinstance(user_data, dict) and "message_ids" in user_data:
						message_ids = user_data["message_ids"]
					else:
						message_ids = user_data
					
					# Получаем данные о заявке
					order_id = await db_local.get_active_order_by_user(user_tg_id)
					from app.main import get_user_stage_name
					from aiogram.fsm.storage.base import StorageKey
					
					storage = message.bot.session.storage if hasattr(message.bot, 'session') else None
					stage_name = "Неизвестно"
					state_str = None
					state_data_payload = {}
					if storage:
						try:
							bot_id = message.bot.id
							key = StorageKey(
								bot_id=bot_id,
								chat_id=user_tg_id,
								user_id=user_tg_id
							)
							state_str = await storage.get_state(key)
							if state_str:
								stage_name = get_user_stage_name(str(state_str))
							state_data_payload = await storage.get_data(key)
						except:
							pass
					
					# Формируем текст сообщения
					user_name = question.get("user_name", "Не указано")
					user_username = question.get("user_username", "нет")
					pre_order_states = {
						"BuyStates:waiting_confirmation",
						"BuyStates:waiting_wallet_address",
						"BuyStates:waiting_delivery_method",
						"BuyStates:waiting_payment_confirmation",
						"BuyStates:waiting_payment_proof",
					}
					state_amount_currency = state_data_payload.get("final_amount", state_data_payload.get("amount_currency"))
					state_currency_symbol = state_data_payload.get("currency_symbol")

					if order_id:
						order = await db_local.get_order_by_id(order_id)
						if order:
							amount_currency = order.get("amount_currency", 0)
							currency_symbol = order.get("currency_symbol", "₽")
							if state_str in pre_order_states and state_amount_currency is not None:
								amount_currency = state_amount_currency
								if state_currency_symbol:
									currency_symbol = state_currency_symbol
							amount = order.get("amount", 0)
							crypto_display = order.get("crypto_display", "")
							amount_str = f"{amount:.8f}".rstrip('0').rstrip('.') if amount < 1 else f"{amount:.2f}".rstrip('0').rstrip('.')
							alert_text = (
								f"🚨 <b>Крупная заявка</b>\n\n"
								f"Пользователь: {user_name} (@{user_username})\n"
								f"Сумма: {int(amount_currency)} {currency_symbol}\n"
								f"Крипта: {crypto_display}\n"
								f"Кол-во: {amount_str} {crypto_display}\n\n"
								f"📍 <b>Этап:</b> {stage_name}"
							)
						else:
							alert_text = (
								f"🚨 <b>Крупная заявка</b>\n\n"
								f"Пользователь: {user_name} (@{user_username})\n\n"
								f"📍 <b>Этап:</b> {stage_name}"
							)
					else:
						if state_amount_currency is not None:
							currency_symbol = state_currency_symbol or "₽"
							amount_str = f"{amount:.8f}".rstrip('0').rstrip('.') if amount < 1 else f"{amount:.2f}".rstrip('0').rstrip('.')
							alert_text = (
								f"🚨 <b>Крупная заявка</b>\n\n"
								f"Пользователь: {user_name} (@{user_username})\n"
								f"Сумма: {int(state_amount_currency)} {currency_symbol}\n"
								f"Крипта: {crypto_display}\n"
								f"Кол-во: {amount_str} {crypto_display}\n\n"
								f"📍 <b>Этап:</b> {stage_name}"
							)
						else:
							alert_text = (
								f"🚨 <b>Крупная заявка</b>\n\n"
								f"Пользователь: {user_name} (@{user_username})\n\n"
								f"📍 <b>Этап:</b> {stage_name}"
							)
					
					# Добавляем историю переписки
					if history_text:
						alert_text += f"\n\n💬 <b>Переписка:</b>\n\n{history_text}"
					
					# Обновляем сообщения для всех админов
					from aiogram.utils.keyboard import InlineKeyboardBuilder
					kb = InlineKeyboardBuilder()
					kb.button(text="💬 Написать", callback_data=f"alert:message:{user_tg_id}")
					kb.button(text="💳 Реквизиты", callback_data=f"alert:requisites:{user_tg_id}")
					kb.button(text="💰 Сумма", callback_data=f"alert:amount:{user_tg_id}")
					kb.button(text="🪙 Монеты", callback_data=f"alert:crypto:{user_tg_id}")
					kb.adjust(2, 2)
					
					if not message_ids:
						logger_main.warning(f"⚠️ on_user_reply_to_question: message_ids пуст, не можем обновить сообщение")
					else:
						for admin_id, msg_id in message_ids.items():
							try:
								logger_main.info(f"🔄 on_user_reply_to_question: обновляем сообщение для админа {admin_id}, message_id={msg_id}")
								await message.bot.edit_message_text(
									chat_id=admin_id,
									message_id=msg_id,
									text=alert_text,
									parse_mode="HTML",
									reply_markup=kb.as_markup()
								)
								logger_main.info(f"✅ on_user_reply_to_question: сообщение успешно обновлено для админа {admin_id}")
							except Exception as e:
								logger_main.error(f"❌ Не удалось обновить сообщение о крупной заявке для админа {admin_id}: {e}", exc_info=True)
					
					# Удаляем сообщение пользователя
					await delete_user_message(message)
					logger_main.info(f"✅ on_user_reply_to_question: обработка завершена, возвращаемся")
					return
			else:
				logger_main.info(f"⚠️ on_user_reply_to_question: условие не выполнено, продолжаем обычную обработку")
		
		# Отправляем сообщение админу
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		
		if admin_ids and question.get("admin_message_id"):
			try:
				# Формируем полное сообщение для админа
				user_name = question.get("user_name", "Не указано")
				user_username = question.get("user_username", "Не указано")
				question_text = question["question_text"]
				initiated_by_admin = bool(question.get("initiated_by_admin"))
				initiated_by_admin = bool(question.get("initiated_by_admin"))
				
				# Формируем исходное сообщение о вопросе
				if initiated_by_admin:
					question_info = (
						f"💬 <b>Диалог (инициировано администратором)</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>"
					)
				else:
					question_info = (
						f"❓ <b>Вопрос от пользователя</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>\n\n"
						f"💬 <b>Вопрос:</b>\n{question_text}"
					)
				
				# Обновляем сообщение админа с историей переписки
				from app.keyboards import question_reply_kb
				await message.bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=question["admin_message_id"],
					text=question_info + "\n\n" + admin_message,
					parse_mode="HTML",
					reply_markup=question_reply_kb(question_id)
				)
				logger_main.info(f"✅ Ответ пользователя {user_tg_id} по вопросу {question_id} отправлен админу")
				
				# Отправляем временное уведомление пользователю
				notif_msg = await message.bot.send_message(
					chat_id=user_tg_id,
					text="✅ Сообщение отправлено администратору"
				)
				await asyncio.sleep(2)
				try:
					await message.bot.delete_message(chat_id=user_tg_id, message_id=notif_msg.message_id)
				except:
					pass
			except Exception as e:
				logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}", exc_info=True)
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Если у пользователя было сообщение с историей, обновляем его
		user_message_id = question.get("user_message_id")
		if user_message_id:
			try:
				# Формируем полное сообщение для пользователя
				if question.get("initiated_by_admin"):
					question_info = "💬 <b>Сообщение администратора</b>\n\n"
				else:
					question_info = "❓ <b>Ваш вопрос</b>\n\n"
				
				# Получаем обновленную историю переписки
				updated_messages = await db_local.get_question_messages(question_id)
				history_lines = []
				for msg in updated_messages:
					if msg["sender_type"] == "admin":
						history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
					else:
						history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
				
				history_text = "\n\n".join(history_lines)
				user_message = question_info + history_text
				
				# Обновляем сообщение пользователя
				from app.keyboards import question_user_reply_kb
				await message.bot.edit_message_text(
					chat_id=user_tg_id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=question_user_reply_kb(question_id)
				)
			except Exception as e:
				logger_main.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)

	# Обработчик ответов пользователя на сообщения админа по обычной заявке (должен быть ПЕРЕД обработчиком для продажи)
	# УБРАЛИ StateFilter(None) - обработчик должен работать в любом состоянии, если есть активная заявка
	@dp.message(
		is_not_admin_message,
		~(F.forward_origin.as_(bool) | F.forward_from.as_(bool)),
		~(F.text.startswith("/") if F.text else False)
	)
	async def on_user_reply_to_order(message: Message, state: FSMContext):
		"""Обработчик ответов пользователя на сообщения админа по обычной заявке"""
		if not message.from_user:
			return
		
		# Проверяем, не является ли отправитель админом - если да, пропускаем обработку
		admin_ids = get_admin_ids()
		admin_usernames = get_admin_usernames()
		user_id = message.from_user.id
		username = message.from_user.username
		if is_admin(user_id, username, admin_ids, admin_usernames):
			logger_main = logging.getLogger("app.main")
			logger_main.info(f"🟡🟡🟡 on_user_reply_to_order: сообщение от админа, пропускаем обработку")
			return
		
		from app.di import get_db
		db_local = get_db()
		
		user_tg_id = message.from_user.id
		
		# СНАЧАЛА проверяем, есть ли активный вопрос - если есть, пропускаем обработку
		# чтобы сообщение обработал on_user_reply_to_question
		question_id = await db_local.get_active_question_by_user(user_tg_id)
		if question_id:
			question = await db_local.get_question_by_id(question_id)
			if question and not question.get("completed_at"):
				# Есть активный вопрос, пропускаем обработку
				logger_main = logging.getLogger("app.main")
				logger_main.info(f"🔍 on_user_reply_to_order: пропускаем обработку, есть активный вопрос question_id={question_id}")
				return
		
		# Проверяем, есть ли у пользователя активная заявка
		# Получаем последнюю активную заявку пользователя
		order_id = await db_local.get_active_order_by_user(user_tg_id)
		
		if not order_id:
			# Нет активной заявки, пропускаем обработку
			return
		
		# Получаем информацию о заявке
		order = await db_local.get_order_by_id(order_id)
		if not order:
			return
		
		# Проверяем, не завершена ли заявка
		if order.get("completed_at"):
			# Заявка завершена, не обрабатываем ответ
			return
		
		# Получаем текст сообщения
		message_text = message.text or message.caption or ""
		if not message_text.strip():
			return
		
		# Сохраняем сообщение в истории переписки
		await db_local.add_buy_order_message(order_id, "user", message_text)
		
		# Получаем всю историю переписки
		messages = await db_local.get_buy_order_messages(order_id)
		
		# Формируем сообщение для админа с историей
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
		
		admin_message = "\n\n".join(history_lines)
		
		# Отправляем сообщение админу
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		
		if admin_ids and order.get("admin_message_id"):
			try:
				# Формируем полное сообщение для админа
				order_number = order["order_number"]
				user_name = order.get("user_name", "Не указано")
				user_username = order.get("user_username", "Не указано")
				amount = order["amount"]
				amount_currency = order.get("amount_currency", 0)
				currency_symbol = order.get("currency_symbol", "₽")
				wallet_address = order.get("wallet_address", "")
				crypto_display = order["crypto_display"]
				
				# Форматируем сумму
				if amount < 1:
					amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
				else:
					amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
				
				# Формируем исходное сообщение о заявке (без номера заявки)
				order_info = (
					f"Имя пользователя: {user_name or 'Не указано'}\n"
					f"Username: @{user_username}\n\n"
					f"Количество монет: {amount_str} {crypto_display}\n"
					f"Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
					f"Адрес кошелька: <code>{wallet_address}</code>"
				)
				
				# Обновляем сообщение админа с историей переписки
				from app.keyboards import order_action_kb
				# Используем расширенную клавиатуру, если есть переписка
				is_expanded = len(messages) > 0
				
				# Отправляем уведомление перед обновлением
				try:
					notif_msg = await message.bot.send_message(
						chat_id=admin_ids[0],
						text="💬 <b>Новое сообщение от пользователя</b>",
						parse_mode="HTML"
					)
					# Сохраняем ID уведомления
					notification_ids[(admin_ids[0], order_id, 'order')] = notif_msg.message_id
				except Exception as e:
					# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
					logger_main.warning(f"⚠️ Не удалось отправить уведомление админу {admin_ids[0]}: {e}")
				
				# Пытаемся обновить как caption (для фото/документа), если не получится - как текст
				try:
					await message.bot.edit_message_caption(
						chat_id=admin_ids[0],
						message_id=order["admin_message_id"],
						caption=order_info + "\n\n" + admin_message,
						parse_mode="HTML",
						reply_markup=order_action_kb(order_id, expanded=is_expanded)
					)
				except Exception as e:
					# Если не получилось (это текстовое сообщение), используем edit_text
					logger_main.debug(f"Не удалось обновить caption, пробуем edit_text: {e}")
					await message.bot.edit_message_text(
						chat_id=admin_ids[0],
						message_id=order["admin_message_id"],
						text=order_info + "\n\n" + admin_message,
						parse_mode="HTML",
						reply_markup=order_action_kb(order_id, expanded=is_expanded)
					)
				logger_main.info(f"✅ Ответ пользователя {user_tg_id} по заявке {order_id} отправлен админу")
				
				# Отправляем временное уведомление пользователю
				notif_msg = await message.bot.send_message(
					chat_id=user_tg_id,
					text="✅ Сообщение отправлено администратору"
				)
				await asyncio.sleep(2)
				try:
					await message.bot.delete_message(chat_id=user_tg_id, message_id=notif_msg.message_id)
				except:
					pass
			except Exception as e:
				logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}", exc_info=True)
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Если у пользователя было сообщение с историей, обновляем его
		user_message_id = order.get("user_message_id")
		if user_message_id:
			try:
				# Формируем полное сообщение для пользователя
				order_number = order["order_number"]
				crypto_display = order["crypto_display"]
				amount = order["amount"]
				amount_currency = order.get("amount_currency", 0)
				currency_symbol = order.get("currency_symbol", "₽")
				
				# Форматируем сумму
				if amount < 1:
					amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
				else:
					amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
				
				order_info = (
					f"💵 Криптовалюта: {crypto_display}\n"
					f"💸 Сумма: {amount_str} {crypto_display}\n"
					f"💰 К оплате: {int(amount_currency)} {currency_symbol}\n"
				)
				
				# Получаем обновленную историю переписки
				updated_messages = await db_local.get_buy_order_messages(order_id)
				history_lines = []
				for msg in updated_messages:
					if msg["sender_type"] == "admin":
						history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
					else:
						history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
				
				history_text = "\n\n".join(history_lines)
				user_message = order_info + "\n" + history_text
				
				# Обновляем сообщение пользователя
				from app.keyboards import order_user_reply_kb
				await message.bot.edit_message_text(
					chat_id=user_tg_id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=order_user_reply_kb(order_id)
				)
			except Exception as e:
				logger_main.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)
	
	# Обработчик ответов пользователя на сообщения админа по сделке на продажу
	# УБРАЛИ StateFilter(None) - обработчик должен работать в любом состоянии, если есть активная заявка на продажу
	@dp.message(
		is_not_admin_message,
		~(F.forward_origin.as_(bool) | F.forward_from.as_(bool)),
		~(F.text.startswith("/") if F.text else False)
	)
	async def on_user_reply_to_sell_order(message: Message, state: FSMContext):
		logger_main = logging.getLogger("app.main")
		logger_main.info(f"🟠🟠🟠 on_user_reply_to_sell_order: НАЧАЛО ОБРАБОТКИ")
		logger_main.info(f"🟠🟠🟠 on_user_reply_to_sell_order: message_id={message.message_id}, from_user={message.from_user.id if message.from_user else None}, text='{message.text or message.caption or ''}'")
		current_state = await state.get_state()
		logger_main.info(f"🟠🟠🟠 on_user_reply_to_sell_order: current_state={current_state}")
		
		if not message.from_user:
			return
		
		# Проверяем, не является ли отправитель админом - если да, пропускаем обработку
		from app.admin import is_admin
		from app.di import get_admin_ids, get_admin_usernames
		admin_ids = get_admin_ids()
		admin_usernames = get_admin_usernames()
		user_id = message.from_user.id
		username = message.from_user.username
		if is_admin(user_id, username, admin_ids, admin_usernames):
			logger_main.info(f"🟠🟠🟠 on_user_reply_to_sell_order: сообщение от админа, пропускаем обработку")
			return
		"""Обработчик ответов пользователя на сообщения админа по сделке"""
		if not message.from_user:
			return
		
		from app.di import get_db
		db_local = get_db()
		
		# Проверяем, есть ли у пользователя активная сделка на продажу
		user_tg_id = message.from_user.id
		
		# Получаем последнюю активную сделку пользователя
		order_id = await db_local.get_active_sell_order_by_user(user_tg_id)
		
		if not order_id:
			# Нет активной сделки, пропускаем обработку
			return
		
		# Получаем текст сообщения
		message_text = message.text or message.caption or ""
		if not message_text.strip():
			return
		
		# Сохраняем сообщение в истории переписки
		await db_local.add_order_message(order_id, "user", message_text)
		
		# Получаем информацию о сделке
		order = await db_local.get_sell_order_by_id(order_id)
		if not order:
			return
		
		# Получаем всю историю переписки
		messages = await db_local.get_order_messages(order_id)
		
		# Формируем сообщение для админа с историей
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
		
		admin_message = "\n\n".join(history_lines)
		
		# Отправляем сообщение админу
		admin_ids = get_admin_ids()
		logger_main = logging.getLogger("app.main")
		
		if admin_ids and order.get("admin_message_id"):
			try:
				# Формируем полное сообщение для админа
				order_number = order["order_number"]
				user_name = order.get("user_name", "Не указано")
				user_username = order.get("user_username", "Не указано")
				crypto_display = order["crypto_display"]
				amount = order["amount"]
				amount_currency = order["amount_currency"]
				currency_symbol = order["currency_symbol"]
				
				# Форматируем сумму
				if amount < 1:
					amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
				else:
					amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
				
				# Формируем исходное сообщение о сделке
				order_info = (
					f"💰 <b>Заявка на продажу</b>\n\n"
					f"📊 Номер заявки: #{order_number}\n"
					f"👤 Имя: {user_name}\n"
					f"📱 Username: @{user_username}\n"
					f"🆔 ID: <code>{user_tg_id}</code>\n\n"
					f"💵 Криптовалюта: {crypto_display}\n"
					f"💸 Сумма: {amount_str} {crypto_display}\n"
					f"💰 К получению: {int(amount_currency)} {currency_symbol}"
				)
				
				# Обновляем сообщение админа с историей переписки
				from app.keyboards import sell_order_admin_kb
				# Отправляем уведомление перед обновлением
				try:
					notif_msg = await message.bot.send_message(
						chat_id=admin_ids[0],
						text="💬 <b>Новое сообщение от пользователя</b>",
						parse_mode="HTML"
					)
					# Сохраняем ID уведомления
					notification_ids[(admin_ids[0], order_id, 'sell_order')] = notif_msg.message_id
				except Exception as e:
					# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
					logger_main.warning(f"⚠️ Не удалось отправить уведомление админу {admin_ids[0]}: {e}")
				await message.bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=order["admin_message_id"],
					text=order_info + "\n\n" + admin_message,
					parse_mode="HTML",
					reply_markup=sell_order_admin_kb(order_id)
				)
				logger_main.info(f"✅ Ответ пользователя {user_tg_id} по сделке {order_id} отправлен админу")
				
				# Отправляем временное уведомление пользователю
				notif_msg = await message.bot.send_message(
					chat_id=user_tg_id,
					text="✅ Сообщение отправлено администратору"
				)
				await asyncio.sleep(2)
				try:
					await message.bot.delete_message(chat_id=user_tg_id, message_id=notif_msg.message_id)
				except:
					pass
			except Exception as e:
				logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}", exc_info=True)
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Если у пользователя было сообщение с историей, обновляем его
		user_message_id = order.get("user_message_id")
		if user_message_id:
			try:
				# Формируем полное сообщение для пользователя
				order_number = order["order_number"]
				crypto_display = order["crypto_display"]
				amount = order["amount"]
				
				# Форматируем сумму
				if amount < 1:
					amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
				else:
					amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
				
				order_info = (
					f"💰 <b>Заявка на продажу #{order_number}</b>\n\n"
					f"💵 Криптовалюта: {crypto_display}\n"
					f"💸 Сумма: {amount_str} {crypto_display}\n"
				)
				
				# Получаем обновленную историю переписки
				updated_messages = await db_local.get_order_messages(order_id)
				history_lines = []
				for msg in updated_messages:
					if msg["sender_type"] == "admin":
						history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
					else:
						history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
				
				history_text = "\n\n".join(history_lines)
				user_message = order_info + "\n" + history_text
				
				# Обновляем сообщение пользователя
				from app.keyboards import sell_order_user_reply_kb
				await message.bot.edit_message_text(
					chat_id=user_tg_id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=sell_order_user_reply_kb(order_id)
				)
			except Exception as e:
				logger_main.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)
	
	# Обработчик кнопки "Ответить" для пользователя по вопросу
	@dp.callback_query(F.data.startswith("question:user:reply:"))
	async def on_question_user_reply_start(cb: CallbackQuery, state: FSMContext):
		"""Обработчик начала ответа пользователя на вопрос админа"""
		if not cb.from_user:
			await cb.answer()
			return
		
		# Формат: question:user:reply:{question_id}
		parts = cb.data.split(":")
		if len(parts) < 4:
			await cb.answer("Ошибка данных", show_alert=True)
			return
		
		try:
			question_id = int(parts[3])
		except ValueError:
			await cb.answer("Ошибка данных", show_alert=True)
			return
		
		from app.di import get_db
		db_local = get_db()
		
		# Проверяем, что вопрос принадлежит пользователю
		question = await db_local.get_question_by_id(question_id)
		if not question or question["user_tg_id"] != cb.from_user.id:
			await cb.answer("Вопрос не найден", show_alert=True)
			return
		
		# Проверяем, не завершен ли вопрос
		if question.get("completed_at"):
			await cb.answer("Вопрос уже завершен", show_alert=True)
			return
		
		# Если пользователь в процессе покупки, не меняем состояние покупки
		current_state = await state.get_state()
		if current_state in (
			BuyStates.waiting_confirmation.state, 
			BuyStates.waiting_wallet_address.state,
			BuyStates.waiting_delivery_method.state,
			BuyStates.waiting_payment_confirmation.state,
			BuyStates.waiting_payment_proof.state
		):
			try:
				prompt_msg = await cb.message.answer(
					"📝 Напишите сообщение администратору:",
					reply_markup=ForceReply(selective=True)
				)
				await delete_message_after_delay(cb.bot, cb.from_user.id, prompt_msg.message_id, 15.0)
				await state.update_data(
					pending_question_reply_id=question_id,
					pending_question_reply_prompt_id=prompt_msg.message_id
				)
				await cb.answer()
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка при отправке запроса на ответ в on_question_user_reply_start: {e}", exc_info=True)
				await cb.answer("❌ Произошла ошибка. Попробуйте позже.", show_alert=True)
			return
		
		# Сохраняем question_id в состоянии
		await state.update_data(question_id=question_id)
		
		# Переводим в состояние ожидания ответа
		await state.set_state(QuestionUserReplyStates.waiting_reply)
		
		# Удаляем уведомление о новом сообщении (если есть)
		notification_key = (cb.from_user.id, question_id, 'question')
		if notification_key in notification_ids:
			try:
				notif_message_id = notification_ids[notification_key]
				await cb.bot.delete_message(chat_id=cb.from_user.id, message_id=notif_message_id)
				del notification_ids[notification_key]
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.debug(f"Не удалось удалить уведомление: {e}")
		
		# Уведомляем пользователя
		await cb.message.edit_text(
			cb.message.text + "\n\n📝 Введите ваш ответ:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
		await cb.answer()
	
	async def _handle_question_user_reply(message: Message, state: FSMContext, question_id: int, keep_state: bool) -> None:
		"""Отправка ответа пользователя по вопросу без смены состояния покупки"""
		if not message.from_user:
			return
		
		# Проверяем, не является ли это командой - если да, пропускаем обработку
		if message.text and message.text.startswith("/"):
			return
		
		from app.di import get_db
		db_local = get_db()
		
		# Получаем текст ответа
		reply_text = message.text or message.caption or ""
		if not reply_text.strip():
			await message.answer("❌ Пожалуйста, введите текст ответа.")
			return
		
		# Получаем информацию о вопросе
		question = await db_local.get_question_by_id(question_id)
		if not question:
			await message.answer("❌ Вопрос не найден")
			if keep_state:
				await state.update_data(pending_question_reply_id=None, pending_question_reply_prompt_id=None)
			else:
				await state.clear()
			return
		
		# Проверяем, что вопрос принадлежит пользователю
		if question["user_tg_id"] != message.from_user.id:
			await message.answer("❌ Ошибка доступа")
			if keep_state:
				await state.update_data(pending_question_reply_id=None, pending_question_reply_prompt_id=None)
			else:
				await state.clear()
			return
		
		# Проверяем, не закрыт ли вопрос
		if question.get("completed_at"):
			await message.answer("❌ Вопрос уже закрыт. Вы не можете отправлять сообщения по закрытому вопросу.")
			if keep_state:
				await state.update_data(pending_question_reply_id=None, pending_question_reply_prompt_id=None)
			else:
				await state.clear()
			return
		
		# Сохраняем сообщение в истории переписки
		await db_local.add_question_message(question_id, "user", reply_text)

		# Если это крупная заявка, обновляем админский алерт с перепиской
		try:
			await try_update_large_order_alert(
				bot=message.bot,
				state=state,
				user_tg_id=question["user_tg_id"],
				user_name=question.get("user_name", "") or "",
				user_username=question.get("user_username", "") or ""
			)
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.warning(f"⚠️ Не удалось обновить алерт крупной заявки: {e}")
		
		# Получаем всю историю переписки
		messages = await db_local.get_question_messages(question_id)
		
		# Формируем сообщение для пользователя: информация + история
		if question.get("initiated_by_admin"):
			question_info = "💬 <b>Сообщение администратора</b>\n\n"
		else:
			question_info = "❓ <b>Ваш вопрос</b>\n\n"
		
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
		
		history_text = "\n\n".join(history_lines)
		user_message = question_info + history_text
		
		# Обновляем сообщение пользователя
		from app.keyboards import question_user_reply_kb
		try:
			user_message_id = question.get("user_message_id")
			if user_message_id:
				await message.bot.edit_message_text(
					chat_id=message.from_user.id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=question_user_reply_kb(question_id)
				)
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)
		
		# Обновляем сообщение админа
		admin_ids = get_admin_ids()
		if admin_ids and question.get("admin_message_id"):
			try:
				user_name = question.get("user_name", "Не указано")
				user_username = question.get("user_username", "Не указано")
				user_tg_id = question["user_tg_id"]
				question_text = question["question_text"]
				initiated_by_admin = bool(question.get("initiated_by_admin"))
				
				if initiated_by_admin:
					admin_question_info = (
						f"💬 <b>Диалог (инициировано администратором)</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>"
					)
				else:
					admin_question_info = (
						f"❓ <b>Вопрос от пользователя</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>\n\n"
						f"💬 <b>Вопрос:</b>\n{question_text}"
					)
				
				admin_history_lines = []
				for msg in messages:
					if msg["sender_type"] == "admin":
						admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
					else:
						admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
				
				admin_history_text = "\n\n".join(admin_history_lines)
				admin_message = admin_question_info + "\n\n" + admin_history_text
				
				from app.keyboards import question_reply_kb
				# Отправляем уведомление перед обновлением
				try:
					notif_msg = await message.bot.send_message(
						chat_id=admin_ids[0],
						text="💬 <b>Новое сообщение от пользователя</b>",
						parse_mode="HTML"
					)
					notification_ids[(admin_ids[0], question_id, 'question')] = notif_msg.message_id
				except Exception as e:
					logger_main = logging.getLogger("app.main")
					logger_main.warning(f"⚠️ Не удалось отправить уведомление админу {admin_ids[0]}: {e}")
				
				await message.bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=question["admin_message_id"],
					text=admin_message,
					parse_mode="HTML",
					reply_markup=question_reply_kb(question_id)
				)
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}", exc_info=True)
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		if keep_state:
			current_state = await state.get_state()
			await state.update_data(pending_question_reply_id=None, pending_question_reply_prompt_id=None)
			if current_state == BuyStates.waiting_confirmation.state:
				data = await state.get_data()
				amount = data.get("amount", 0)
				amount_currency = data.get("amount_currency", 0)
				crypto_display = data.get("crypto_display", "")
				currency_symbol = data.get("currency_symbol", "")
				if amount < 1:
					amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
				else:
					amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
				# Проверяем, является ли это крупной заявкой
				alert_threshold = data.get("alert_threshold", 400.0)
				total_usd = data.get("total_usd", 0)
				is_large_order = total_usd >= alert_threshold
				
				# Для крупных заявок не показываем сумму оплаты
				if is_large_order:
					payment_text = "ожидайте сообщение администратора"
				else:
					payment_text = f"{int(amount_currency)} {currency_symbol}"
				
				confirmation_text = (
					f"Вам будет зачислено: {amount_str} {crypto_display}\n"
					f"Вам необходимо оплатить: {payment_text}"
				)
				from app.keyboards import buy_confirmation_kb
				await message.answer(confirmation_text, reply_markup=buy_confirmation_kb())
		else:
			await state.clear()

	@dp.message(QuestionUserReplyStates.waiting_reply)
	async def on_question_user_reply_send(message: Message, state: FSMContext):
		"""Обработчик отправки ответа пользователя на вопрос админа"""
		if not message.from_user:
			return
		
		# Проверяем, не является ли это командой - если да, пропускаем обработку
		if message.text and message.text.startswith("/"):
			return  # Пропускаем команды, они обработаются в своих обработчиках
		
		from app.di import get_db
		db_local = get_db()
		
		# Получаем данные из состояния
		data = await state.get_data()
		question_id = data.get("question_id")
		
		if not question_id:
			await message.answer("❌ Ошибка: не найден ID вопроса")
			await state.clear()
			return
		
		# Получаем текст ответа
		reply_text = message.text or message.caption or ""
		if not reply_text.strip():
			await message.answer("❌ Пожалуйста, введите текст ответа.")
			return
		
		# Получаем информацию о вопросе
		question = await db_local.get_question_by_id(question_id)
		if not question:
			await message.answer("❌ Вопрос не найден")
			await state.clear()
			return
		
		# Проверяем, что вопрос принадлежит пользователю
		if question["user_tg_id"] != message.from_user.id:
			await message.answer("❌ Ошибка доступа")
			await state.clear()
			return
		
		# Проверяем, не закрыт ли вопрос
		if question.get("completed_at"):
			await message.answer("❌ Вопрос уже закрыт. Вы не можете отправлять сообщения по закрытому вопросу.")
			await state.clear()
			return
		
		# Сохраняем сообщение в истории переписки
		await db_local.add_question_message(question_id, "user", reply_text)
		
		# Получаем всю историю переписки
		messages = await db_local.get_question_messages(question_id)
		
		# Формируем полное сообщение для пользователя: информация о вопросе + история
		if question.get("initiated_by_admin"):
			question_info = "💬 <b>Сообщение администратора</b>\n\n"
		else:
			question_info = "❓ <b>Ваш вопрос</b>\n\n"
		
		# Добавляем историю переписки
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
		
		history_text = "\n\n".join(history_lines)
		user_message = question_info + history_text
		
		# Обновляем сообщение пользователя
		from app.keyboards import question_user_reply_kb
		try:
			user_message_id = question.get("user_message_id")
			if user_message_id:
				await message.bot.edit_message_text(
					chat_id=message.from_user.id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=question_user_reply_kb(question_id)
				)
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)
		
		# Обновляем сообщение админа
		admin_ids = get_admin_ids()
		if admin_ids and question.get("admin_message_id"):
			try:
				user_name = question.get("user_name", "Не указано")
				user_username = question.get("user_username", "Не указано")
				user_tg_id = question["user_tg_id"]
				question_text = question["question_text"]
				initiated_by_admin = bool(question.get("initiated_by_admin"))
				
				if initiated_by_admin:
					admin_question_info = (
						f"💬 <b>Диалог (инициировано администратором)</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>"
					)
				else:
					admin_question_info = (
						f"❓ <b>Вопрос от пользователя</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>\n\n"
						f"💬 <b>Вопрос:</b>\n{question_text}"
					)
				
				admin_history_lines = []
				for msg in messages:
					if msg["sender_type"] == "admin":
						admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
					else:
						admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
				
				admin_history_text = "\n\n".join(admin_history_lines)
				admin_message = admin_question_info + "\n\n" + admin_history_text
				
				from app.keyboards import question_reply_kb
				# Отправляем уведомление перед обновлением
				try:
					notif_msg = await message.bot.send_message(
						chat_id=admin_ids[0],
						text="💬 <b>Новое сообщение от пользователя</b>",
						parse_mode="HTML"
					)
					# Сохраняем ID уведомления
					notification_ids[(admin_ids[0], question_id, 'question')] = notif_msg.message_id
				except Exception as e:
					# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
					logger_main.warning(f"⚠️ Не удалось отправить уведомление админу {admin_ids[0]}: {e}")
				await message.bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=question["admin_message_id"],
					text=admin_message,
					parse_mode="HTML",
					reply_markup=question_reply_kb(question_id)
				)
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}", exc_info=True)
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Очищаем состояние
		await state.clear()

	# Обработчик кнопки "Ответить" для пользователя по сделке
	@dp.callback_query(F.data.startswith("sell:order:user:reply:"))
	async def on_sell_order_user_reply_start(cb: CallbackQuery, state: FSMContext):
		"""Обработчик начала ответа пользователя на сообщение админа по сделке"""
		if not cb.from_user:
			await cb.answer()
			return
		
		# Формат: sell:order:user:reply:{order_id}
		parts = cb.data.split(":")
		if len(parts) < 5:
			await cb.answer("Ошибка данных", show_alert=True)
			return
		
		try:
			order_id = int(parts[4])
		except ValueError:
			await cb.answer("Ошибка данных", show_alert=True)
			return
		
		# Проверяем, что это пользователь
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(cb.from_user.id, cb.from_user.username):
			await cb.answer()
			return
		
		# Получаем информацию о сделке
		order = await db_local.get_sell_order_by_id(order_id)
		if not order:
			await cb.answer("Сделка не найдена", show_alert=True)
			return
		
		# Проверяем, что сделка принадлежит этому пользователю
		if order["user_tg_id"] != cb.from_user.id:
			await cb.answer("Это не ваша сделка", show_alert=True)
			return
		
		# Проверяем, не завершена ли сделка
		if order.get("completed_at"):
			await cb.answer("Сделка уже завершена", show_alert=True)
			return
		
		# Сохраняем order_id в FSM
		await state.update_data(sell_order_id=order_id)
		
		# Переводим в состояние ожидания ответа
		await state.set_state(SellOrderUserReplyStates.waiting_reply)
		
		# Удаляем уведомление о новом сообщении (если есть)
		notification_key = (cb.from_user.id, order_id, 'sell_order')
		if notification_key in notification_ids:
			try:
				notif_message_id = notification_ids[notification_key]
				await cb.bot.delete_message(chat_id=cb.from_user.id, message_id=notif_message_id)
				del notification_ids[notification_key]
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.debug(f"Не удалось удалить уведомление: {e}")
		
		try:
			await cb.message.edit_text(
				(cb.message.text or "") + "\n\n📝 Введите ваш ответ:",
				parse_mode="HTML",
				reply_markup=cb.message.reply_markup
			)
		except Exception as e:
			logger_main = logging.getLogger("app.main")
			logger_main.error(f"Ошибка редактирования сообщения: {e}")
			await cb.message.answer("📝 Введите ваш ответ:")
		
		await cb.answer()

	@dp.message(SellOrderUserReplyStates.waiting_reply)
	async def on_sell_order_user_reply_send(message: Message, state: FSMContext):
		"""Обработчик отправки ответа пользователя на сообщение админа"""
		if not message.from_user:
			return
		
		from app.di import get_db
		db_local = get_db()
		if not await db_local.is_allowed_user(message.from_user.id, message.from_user.username):
			return
		
		# Получаем данные из FSM
		data = await state.get_data()
		order_id = data.get("sell_order_id")
		
		if not order_id:
			await message.answer("❌ Ошибка: не найдена сделка")
			await state.clear()
			return
		
		# Получаем текст сообщения
		message_text = message.text or message.caption or ""
		if not message_text.strip():
			await message.answer("❌ Пожалуйста, введите текст ответа.")
			return
		
		# Получаем информацию о сделке
		order = await db_local.get_sell_order_by_id(order_id)
		if not order:
			await message.answer("❌ Сделка не найдена")
			await state.clear()
			return
		
		# Проверяем, что сделка принадлежит этому пользователю
		if order["user_tg_id"] != message.from_user.id:
			await message.answer("❌ Это не ваша сделка")
			await state.clear()
			return
		
		# Сохраняем сообщение в БД
		await db_local.add_order_message(order_id, "user", message_text)
		
		# Удаляем сообщение пользователя
		await delete_user_message(message)
		
		# Получаем всю историю переписки
		messages = await db_local.get_order_messages(order_id)
		
		# Формируем полное сообщение для пользователя
		order_number = order["order_number"]
		crypto_display = order["crypto_display"]
		amount = order["amount"]
		
		# Форматируем сумму
		if amount < 1:
			amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
		else:
			amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
		
		order_info = (
			f"💰 <b>Заявка на продажу #{order_number}</b>\n\n"
			f"💵 Криптовалюта: {crypto_display}\n"
			f"💸 Сумма: {amount_str} {crypto_display}\n"
		)
		
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
		
		history_text = "\n\n".join(history_lines)
		user_message = order_info + "\n" + history_text
		
		# Обновляем сообщение пользователя
		user_message_id = order.get("user_message_id")
		if user_message_id:
			try:
				from app.keyboards import sell_order_user_reply_kb
				await message.bot.edit_message_text(
					chat_id=message.from_user.id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=sell_order_user_reply_kb(order_id)
				)
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)
		
		# Обновляем сообщение админа
		admin_ids = get_admin_ids()
		if admin_ids and order.get("admin_message_id"):
			try:
				user_name = order.get("user_name", "Не указано")
				user_username = order.get("user_username", "Не указано")
				user_tg_id = order["user_tg_id"]
				amount_currency = order["amount_currency"]
				currency_symbol = order["currency_symbol"]
				
				admin_order_info = (
					f"💰 <b>Заявка на продажу</b>\n\n"
					f"📊 Номер заявки: #{order_number}\n"
					f"👤 Имя: {user_name}\n"
					f"📱 Username: @{user_username}\n"
					f"🆔 ID: <code>{user_tg_id}</code>\n\n"
					f"💵 Криптовалюта: {crypto_display}\n"
					f"💸 Сумма: {amount_str} {crypto_display}\n"
					f"💰 К получению: {int(amount_currency)} {currency_symbol}"
				)
				
				admin_history_lines = []
				for msg in messages:
					if msg["sender_type"] == "admin":
						admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
					else:
						admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
				
				admin_history_text = "\n\n".join(admin_history_lines)
				admin_message = admin_order_info + "\n\n" + admin_history_text
				
				from app.keyboards import sell_order_admin_kb
				# Отправляем уведомление перед обновлением
				try:
					notif_msg = await message.bot.send_message(
						chat_id=admin_ids[0],
						text="💬 <b>Новое сообщение от пользователя</b>",
						parse_mode="HTML"
					)
					# Сохраняем ID уведомления
					notification_ids[(admin_ids[0], order_id, 'sell_order')] = notif_msg.message_id
				except Exception as e:
					# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
					logger_main.warning(f"⚠️ Не удалось отправить уведомление админу {admin_ids[0]}: {e}")
				await message.bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=order["admin_message_id"],
					text=admin_message,
					parse_mode="HTML",
					reply_markup=sell_order_admin_kb(order_id)
				)
			except Exception as e:
				logger_main = logging.getLogger("app.main")
				logger_main.error(f"❌ Ошибка обновления сообщения админу: {e}", exc_info=True)
		
		# Очищаем состояние
		await state.clear()

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
	
	# Периодическая очистка старых логов (раз в день)
	async def periodic_log_cleanup():
		"""Периодическая очистка старых логов"""
		while True:
			await asyncio.sleep(24 * 60 * 60)  # Ждем 24 часа
			try:
				cleanup_old_logs(keep_days=30)
				logger.debug("🧹 Периодическая очистка старых логов выполнена")
			except Exception as e:
				logger.warning(f"⚠️ Ошибка при периодической очистке логов: {e}")
	
	# Запускаем задачу очистки логов в фоне
	asyncio.create_task(periodic_log_cleanup())
	
	# Фоновая задача для периодического обновления курсов криптовалют
	async def periodic_crypto_rates_update():
		"""Периодически обновляет курсы криптовалют в фоне"""
		from app.google_sheets import update_all_crypto_rates, _get_crypto_rate_update_interval
		
		# Начальная задержка 10 секунд, чтобы бот успел запуститься
		await asyncio.sleep(10)
		
		# Первое обновление при запуске
		logger.info("🔄 Первоначальное обновление курсов криптовалют...")
		await update_all_crypto_rates()
		
		while True:
			try:
				# Получаем интервал обновления из настроек (в минутах)
				interval_minutes = await _get_crypto_rate_update_interval()
				await asyncio.sleep(interval_minutes * 60)
				
				logger.debug(f"🔄 Обновление курсов криптовалют (интервал: {interval_minutes} мин)")
				await update_all_crypto_rates()
			except asyncio.CancelledError:
				break
			except Exception as e:
				logger.warning(f"⚠️ Ошибка при обновлении курсов криптовалют: {e}")
				await asyncio.sleep(60)  # Ждём минуту перед повторной попыткой
	
	# Запускаем задачу обновления курсов в фоне
	asyncio.create_task(periodic_crypto_rates_update())
	
	logger.debug("Starting polling...")
	try:
		await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
	finally:
		logger.debug("Shutting down, closing DB")
		await db.close()


if __name__ == "__main__":
	asyncio.run(main())
