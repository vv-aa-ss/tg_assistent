from aiogram import Router, F
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import Message, CallbackQuery, TelegramObject, FSInputFile, InlineKeyboardMarkup
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter, Command
from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
import re
from html import escape
import asyncio
import json
import matplotlib
matplotlib.use('Agg')  # Используем неинтерактивный backend
import matplotlib.pyplot as plt
import numpy as np
import os
import tempfile
from app.keyboards import (
	admin_menu_kb,
	admin_settings_kb,
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
	user_menu_button_kb,
	multipliers_settings_kb,
	markup_percents_settings_kb,
	buy_calc_settings_kb,
	buy_payment_confirmed_kb,
	buy_deal_paid_kb,
	buy_deal_user_reply_kb,
	buy_deal_confirm_kb,
	buy_deal_paid_reply_kb,
)
from app.di import get_db, get_admin_ids, get_admin_usernames

admin_router = Router(name="admin")
logger = logging.getLogger("app.admin")


async def _build_user_deal_text_for_admin_update(db, deal: dict) -> tuple[str, object]:
	from app.main import (
		_build_user_deal_chat_text,
		_build_user_deal_admin_message_text,
		_build_user_deal_with_requisites_chat_text,
		_build_deal_chat_lines,
		_get_deal_requisites_text,
		_build_deal_message,
	)
	deal_id = deal["id"]
	messages = await db.get_buy_deal_messages(deal_id)
	requisites_text = await _get_deal_requisites_text(
		db,
		deal["user_tg_id"],
		deal.get("country_code")
	)
	chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
	alert_threshold = 400.0
	try:
		alert_threshold_str = await db.get_setting("buy_alert_usd_threshold", "400")
		alert_threshold = float(alert_threshold_str) if alert_threshold_str else 400.0
	except (ValueError, TypeError):
		alert_threshold = 400.0
	total_usd = deal.get("total_usd") or 0
	is_large_order = total_usd >= alert_threshold
	prompt = None
	if deal.get("status") == "await_proof":
		prompt = "🖼 Скриншот получен. ⏳Обработка..."
	elif deal.get("status") == "completed":
		prompt = "🖼 Скриншот получен. ⏳Обработка..."
	elif deal.get("status") == "await_admin":
		if deal.get("amount_currency") is None:
			prompt = "❗️Ожидай сообщение от администратора"
	elif deal.get("status") == "await_wallet":
		prompt = "Введи адрес кошелька⬇️⬇️⬇️ :"
	if deal.get("status") in ("await_proof", "completed"):
		user_text = _build_user_deal_with_requisites_chat_text(
			deal=deal,
			requisites_text=requisites_text,
			chat_lines=chat_lines,
			prompt=prompt,
		)
	elif deal.get("status") == "await_admin":
		amount_currency_for_user = None if is_large_order else deal.get("amount_currency")
		user_text = _build_deal_message(
			country_code=deal.get("country_code", "BYN"),
			crypto_code=deal.get("crypto_type", ""),
			amount=deal.get("amount", 0),
			amount_currency=amount_currency_for_user,
			currency_symbol=deal.get("currency_symbol", "Br"),
			prompt=prompt,
			requisites_text=requisites_text if deal.get("amount_currency") is not None else None,
			wallet_address=deal.get("wallet_address"),
			show_empty_amount=is_large_order,
		)
	elif deal.get("status") == "await_wallet":
		amount_currency_for_user = None if is_large_order else deal.get("amount_currency")
		user_text = _build_deal_message(
			country_code=deal.get("country_code", "BYN"),
			crypto_code=deal.get("crypto_type", ""),
			amount=deal.get("amount", 0),
			amount_currency=amount_currency_for_user,
			currency_symbol=deal.get("currency_symbol", "Br"),
			prompt=prompt,
			requisites_text=None,
			wallet_address=deal.get("wallet_address"),
			show_empty_amount=is_large_order,
		)
	elif messages:
		if requisites_text:
			user_text = _build_user_deal_with_requisites_chat_text(
				deal=deal,
				requisites_text=requisites_text,
				chat_lines=chat_lines,
			)
		else:
			has_user_reply = any(msg["sender_type"] == "user" for msg in messages)
			if has_user_reply or len(messages) > 1:
				user_text = _build_user_deal_chat_text(deal, chat_lines)
			else:
				user_text = _build_user_deal_admin_message_text(deal, messages[-1]["message_text"])
	else:
		user_text = _build_deal_message(
			country_code=deal.get("country_code", "BYN"),
			crypto_code=deal.get("crypto_type", ""),
			amount=deal.get("amount", 0),
			amount_currency=deal.get("amount_currency", 0),
			currency_symbol=deal.get("currency_symbol", "Br"),
			prompt=None,
			requisites_text=requisites_text,
		)
	reply_markup = None
	if deal.get("status") in ("await_proof", "completed"):
		reply_markup = buy_deal_user_reply_kb(deal_id)
	elif deal.get("status") == "await_payment":
		reply_markup = buy_deal_paid_reply_kb(deal_id)
	elif messages:
		reply_markup = buy_deal_user_reply_kb(deal_id)
	return user_text, reply_markup


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


async def get_add_data_type_kb_with_recent(admin_id: int, mode: str, data: Optional[Dict[str, Any]] = None, back_to: str = "admin:back"):
	"""
	Получает клавиатуру add_data_type_kb с последними используемыми криптовалютами и картами.
	Оптимизированная версия с batch запросами к БД.
	
	Args:
		admin_id: ID администратора
		mode: Режим работы ("add", "rate" или "move")
		data: Словарь с выбранными данными
		back_to: Callback data для кнопки "Назад"
	
	Returns:
		InlineKeyboardMarkup с последними используемыми элементами
	"""
	from app.keyboards import add_data_type_kb
	db = get_db()
	
	# Получаем последние используемые элементы всех типов
	recent_items = await db.get_recent_items_by_admin(admin_id, limit=9)
	
	# Разделяем по типам и собираем ID для batch запросов
	recent_cryptos = []
	card_ids_to_fetch = []
	cash_names_to_fetch = []
	item_id_to_type = {}  # {item_id: item_type} для маппинга
	
	for item in recent_items:
		item_type = item["item_type"]
		item_id = item["item_id"]
		
		if item_type == "crypto":
			recent_cryptos.append(item_id)
		elif item_type == "card":
			# Извлекаем card_id из формата "card_id_{card_id}"
			if item_id.startswith("card_id_"):
				card_id = int(item_id.replace("card_id_", ""))
				card_ids_to_fetch.append(card_id)
				item_id_to_type[card_id] = "card"
		elif item_type == "cash":
			cash_names_to_fetch.append(item_id)
			item_id_to_type[item_id] = "cash"
	
	# Batch запросы для карт
	recent_cards = []
	if card_ids_to_fetch:
		# Получаем все карты и группы одним batch запросом
		cards_dict = await db.get_cards_by_ids_batch(card_ids_to_fetch)
		card_groups_info = await db.get_cards_groups_batch(card_ids_to_fetch)
		# Формируем список карт
		for card_id in card_ids_to_fetch:
			card_info = cards_dict.get(card_id)
			if card_info:
				group_name = card_groups_info.get(card_id)
				recent_cards.append((card_id, card_info["name"], group_name))
	
	# Batch запрос для наличных
	recent_cash = []
	if cash_names_to_fetch:
		cash_columns_dict = await db.get_cash_columns_batch(cash_names_to_fetch)
		for cash_name in cash_names_to_fetch:
			cash_info = cash_columns_dict.get(cash_name)
			if cash_info:
				display_name = cash_info.get("display_name", "") or cash_name
				recent_cash.append((cash_name, display_name))
			else:
				recent_cash.append((cash_name, cash_name))
	
	# Если элементов меньше 9, дополняем картами из старого метода
	if len(recent_cryptos) + len(recent_cards) + len(recent_cash) < 9:
		recent_cards_raw = await db.get_recent_cards_by_admin(admin_id, limit=9)
		existing_card_ids = {card[0] for card in recent_cards}
		additional_card_ids = []
		for card_id, card_name in recent_cards_raw:
			if card_id not in existing_card_ids and len(recent_cards) + len(recent_cryptos) + len(recent_cash) < 9:
				additional_card_ids.append(card_id)
		
		# Batch запрос для дополнительных карт
		if additional_card_ids:
			additional_cards = await db.get_cards_by_ids_batch(additional_card_ids)
			additional_groups = await db.get_cards_groups_batch(additional_card_ids)
			for card_id in additional_card_ids:
				card_info = additional_cards.get(card_id)
				if card_info:
					group_name = additional_groups.get(card_id)
					recent_cards.append((card_id, card_info["name"], group_name))
	
	return add_data_type_kb(
		mode=mode,
		back_to=back_to,
		data=data,
		recent_cryptos=recent_cryptos,
		recent_cards=recent_cards,
		recent_cash=recent_cash
	)


async def check_and_send_btc_address_links(bot: Bot, chat_id: int, text: str, user_id: Optional[int] = None) -> None:
	"""
	Проверяет наличие BTC адресов в тексте и отправляет ссылки на mempool.space.
	
	Args:
		bot: Экземпляр бота
		chat_id: ID чата для отправки
		text: Текст для проверки
		user_id: ID пользователя для отправки клавиатуры "Меню пользователя" (опционально)
	"""
	if not text:
		logger.debug(f"🔍 check_and_send_btc_address_links: text пустой, пропускаем")
		return
	
	btc_addresses = find_btc_addresses(text)
	if not btc_addresses:
		logger.debug(f"🔍 check_and_send_btc_address_links: BTC адреса не найдены в тексте '{text[:50]}...'")
		return
	
	logger.info(f"🔍 check_and_send_btc_address_links: найдено {len(btc_addresses)} BTC адресов, chat_id={chat_id}, user_id={user_id}")
	
	# Отправляем ссылку для каждого найденного адреса
	last_message = None
	for idx, address in enumerate(btc_addresses):
		link = f"https://mempool.space/address/{address}"
		try:
			logger.debug(f"🔍 Отправка ссылки на BTC адрес {idx+1}/{len(btc_addresses)}: {address}, chat_id={chat_id}")
			# Если это последний адрес и передан user_id, добавляем клавиатуру сразу
			if idx == len(btc_addresses) - 1 and user_id is not None:
				last_message = await bot.send_message(
					chat_id=chat_id,
					text=link,
					reply_markup=user_menu_button_kb(user_id)
				)
				logger.info(f"✅ Отправлена ссылка на BTC адрес: {address} (с клавиатурой) в chat_id={chat_id}, message_id={last_message.message_id if last_message else None}")
			else:
				last_message = await bot.send_message(chat_id=chat_id, text=link)
				logger.info(f"✅ Отправлена ссылка на BTC адрес: {address} в chat_id={chat_id}, message_id={last_message.message_id if last_message else None}")
		except Exception as e:
			logger.exception(f"❌ Ошибка отправки ссылки на BTC адрес {address} в chat_id={chat_id}: {e}")


async def send_card_requisites_to_admin(bot: Bot, admin_chat_id: int, card_id: int, db, user_id: Optional[int] = None, admin_id: Optional[int] = None) -> int:
	"""
	Отправляет все реквизиты карты админу отдельными сообщениями.
	Отправляет и реквизиты из таблицы card_requisites, и user_message (если есть) для обратной совместимости.
	
	Args:
		bot: Экземпляр бота
		admin_chat_id: ID чата админа
		card_id: ID карты
		db: Экземпляр базы данных
		user_id: ID пользователя для отправки клавиатуры "Меню пользователя" (опционально)
		admin_id: ID администратора для логирования использования карты (опционально)
	
	Returns:
		Количество успешно отправленных реквизитов
	"""
	logger.info(f"📤 send_card_requisites_to_admin: card_id={card_id}, admin_chat_id={admin_chat_id}, user_id={user_id}, admin_id={admin_id}")
	
	# Логируем использование карты, если передан admin_id
	if admin_id is not None:
		await db.log_card_selection(card_id, admin_id)
		logger.info(f"📝 Логирование использования карты card_id={card_id} для admin_id={admin_id}")
	
	requisites = await db.list_card_requisites(card_id)
	logger.info(f"📋 Найдено реквизитов в таблице: {len(requisites)} для card_id={card_id}")
	
	sent_count = 0
	last_message = None
	
	# Отправляем статистику пользователя отдельным сообщением, если передан user_id
	if user_id is not None:
		try:
			user_stats = await db.get_user_stats(user_id)
			if user_stats:
				delivery_count = user_stats.get("delivery_count", 0)
				last_interaction = user_stats.get("last_interaction_at")
				# Если это первая доставка (доставок 1), показываем "первая доставка"
				if delivery_count == 1:
					last_activity = "первая доставка"
				else:
					last_activity = format_relative(last_interaction)
				user_stats_text = f"📊 Всего сделок: {delivery_count}\n🕒 Последняя активность: {last_activity}"
				
				try:
					await bot.send_message(
						chat_id=admin_chat_id,
						text=user_stats_text,
						parse_mode="HTML"
					)
					logger.info(f"✅ Статистика пользователя отправлена админу {admin_chat_id}")
					
					# Отправляем разделитель из точек
					try:
						await bot.send_message(
							chat_id=admin_chat_id,
							text="....................."
						)
						logger.info(f"✅ Разделитель отправлен админу {admin_chat_id}")
					except Exception as e:
						logger.exception(f"❌ Ошибка отправки разделителя админу {admin_chat_id}: {e}")
				except Exception as e:
					logger.exception(f"❌ Ошибка отправки статистики пользователя админу {admin_chat_id}: {e}")
		except Exception as e:
			logger.warning(f"⚠️ Не удалось получить статистику пользователя user_id={user_id}: {e}")
	
	# Проверяем наличие user_message для определения последнего сообщения
	user_msg = await db.get_card_user_message(card_id)
	has_user_message = bool(user_msg and user_msg.strip())
	total_messages = len(requisites) + (1 if has_user_message else 0)
	
	# Отправляем все реквизиты из таблицы card_requisites
	if requisites:
		for idx, requisite in enumerate(requisites, 1):
			try:
				logger.info(f"📨 Отправка реквизита {idx}/{len(requisites)} (id={requisite['id']}) админу {admin_chat_id}")
				# Если это последнее сообщение и передан user_id, добавляем клавиатуру сразу
				is_last = (idx == len(requisites) and not has_user_message)
				if is_last and user_id is not None:
					last_message = await bot.send_message(
						chat_id=admin_chat_id,
						text=requisite["requisite_text"],
						parse_mode="HTML",
						reply_markup=user_menu_button_kb(user_id, card_id)
					)
				else:
					last_message = await bot.send_message(
						chat_id=admin_chat_id,
						text=requisite["requisite_text"],
						parse_mode="HTML"
					)
				sent_count += 1
				logger.info(f"✅ Реквизит {requisite['id']} успешно отправлен админу {admin_chat_id}")
			except Exception as e:
				logger.exception(f"❌ Ошибка отправки реквизита {requisite['id']} админу {admin_chat_id}: {e}")
	
	# Также отправляем user_message (для обратной совместимости со старыми данными)
	logger.info(f"🔍 Проверка user_message для card_id={card_id}: value={user_msg[:100] if user_msg else None}..., is_empty={not has_user_message}")
	if has_user_message:
		try:
			logger.info(f"📨 Отправка user_message админу {admin_chat_id}")
			# user_message всегда последний, если есть - добавляем клавиатуру, если передан user_id
			if user_id is not None:
				last_message = await bot.send_message(
					chat_id=admin_chat_id,
					text=user_msg,
					parse_mode="HTML",
					reply_markup=user_menu_button_kb(user_id, card_id)
				)
			else:
				last_message = await bot.send_message(chat_id=admin_chat_id, text=user_msg, parse_mode="HTML")
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


class CardNameEditStates(StatesGroup):
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
	entering_note = State()  # Ввод примечания для /rate


class QuestionReplyStates(StatesGroup):
	"""Состояния для ответа админа на вопрос пользователя"""
	waiting_reply = State()  # Ожидание ввода ответа


class SellOrderMessageStates(StatesGroup):
	"""Состояния для переписки по сделке на продажу"""
	waiting_message = State()  # Ожидание ввода сообщения админом

class OrderMessageStates(StatesGroup):
	"""Состояния для переписки по обычной заявке"""
	waiting_message = State()  # Ожидание ввода сообщения админом

class OrderEditStates(StatesGroup):
	"""Состояния для редактирования заявки"""
	waiting_amount = State()  # Ожидание ввода новой суммы сделки
	waiting_crypto_amount = State()  # Ожидание ввода нового количества крипты
	waiting_debt_amount = State()  # Ожидание ввода суммы долга
	waiting_debt_currency = State()  # Ожидание выбора валюты долга


class DebtorsStates(StatesGroup):
	"""Состояния для управления должниками"""
	waiting_currency = State()
	waiting_amount = State()


class CryptoColumnEditStates(StatesGroup):
	waiting_column = State()
	waiting_crypto_name = State()
	waiting_crypto_column = State()
	waiting_rename = State()


class MultiplierEditStates(StatesGroup):
	waiting_multiplier = State()


class MarkupPercentEditStates(StatesGroup):
	waiting_percent = State()


class BuyCalcEditStates(StatesGroup):
	waiting_value = State()


class AlertMessageStates(StatesGroup):
	"""Состояние для отправки сообщения пользователю из раннего алерта"""
	waiting_message = State()

class AlertRequisitesStates(StatesGroup):
	"""Состояние для выбора реквизитов для крупной заявки"""
	waiting_card = State()

class AlertAmountStates(StatesGroup):
	"""Состояние для установки суммы для крупной заявки"""
	waiting_amount = State()

class AlertCryptoStates(StatesGroup):
	"""Состояние для установки количества монет для крупной заявки"""
	waiting_crypto = State()


class DealAlertMessageStates(StatesGroup):
	"""Состояние для отправки сообщения пользователю по сделке"""
	waiting_message = State()


class DealAlertRequisitesStates(StatesGroup):
	"""Состояние для выбора реквизитов в алерте сделки"""
	waiting_card = State()


class DealAlertAmountStates(StatesGroup):
	"""Состояние для изменения суммы сделки"""
	waiting_amount = State()


class DealAlertCryptoStates(StatesGroup):
	"""Состояние для изменения количества монет сделки"""
	waiting_crypto = State()


class DealAlertDebtStates(StatesGroup):
	"""Состояние для добавления долга по сделке"""
	waiting_amount = State()


class CardGroupStates(StatesGroup):
	waiting_group_name = State()


class CashColumnEditStates(StatesGroup):
	waiting_column = State()
	waiting_cash_name = State()
	waiting_cash_column = State()
	waiting_cash_display_name = State()


class DeleteRowStates(StatesGroup):
	first_confirmation = State()
	second_confirmation = State()

class DeleteRateStates(StatesGroup):
	first_confirmation = State()
	second_confirmation = State()

class DeleteMoveStates(StatesGroup):
	first_confirmation = State()
	second_confirmation = State()


def _one_card_country_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="🇧🇾 Беларусь", callback_data="settings:one_card_for_all:country:BYN")
	kb.button(text="🇷🇺 Россия", callback_data="settings:one_card_for_all:country:RUB")
	kb.button(text="⬅️ Назад", callback_data="admin:settings")
	kb.adjust(1)
	return kb.as_markup()


def _one_card_groups_kb(
	groups: List[Dict[str, Any]],
	country_code: str,
	include_ungrouped: bool = True,
	selected_group_id: Optional[int] = None,
) -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	for group in groups:
		group_name = group.get("name", "")
		group_id = group.get("id")
		is_selected = selected_group_id is not None and group_id == selected_group_id
		prefix = "🔜 " if is_selected else ""
		kb.button(
			text=f"{prefix}📁 {group_name}",
			callback_data=f"settings:one_card_for_all:group:{country_code}:{group_id}"
		)
	if include_ungrouped:
		is_selected_ungrouped = selected_group_id == 0
		prefix = "🔜 " if is_selected_ungrouped else ""
		kb.button(
			text=f"{prefix}📋 Вне групп",
			callback_data=f"settings:one_card_for_all:group:{country_code}:0"
		)
	kb.button(
		text="⛔ Отключить функцию",
		callback_data=f"settings:one_card_for_all:disable:{country_code}"
	)
	kb.button(text="⬅️ Назад", callback_data="settings:one_card_for_all")
	kb.adjust(1)
	return kb


def _one_card_cards_kb(cards: List[Tuple[int, str]], country_code: str, back_to_group: int) -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	for card_id, card_name in cards:
		kb.button(
			text=f"💳 {card_name}",
			callback_data=f"settings:one_card_for_all:card:{country_code}:{card_id}"
		)
	kb.button(
		text="⬅️ Назад",
		callback_data=f"settings:one_card_for_all:country:{country_code}"
	)
	kb.adjust(1)
	return kb


async def _one_card_for_all_enabled(db) -> bool:
	byn = await db.get_setting("one_card_for_all_BYN")
	rub = await db.get_setting("one_card_for_all_RUB")
	return bool(byn) or bool(rub)


async def _one_card_for_all_status_text(db) -> str:
	entries = []
	for country_code, label in (("BYN", "🇧🇾 BYN"), ("RUB", "🇷🇺 RUB")):
		card_id_raw = await db.get_setting(f"one_card_for_all_{country_code}")
		if not card_id_raw:
			entries.append(f"❌ {label}: не настроено")
			continue
		try:
			card_id = int(card_id_raw)
		except (TypeError, ValueError):
			entries.append(f"❌ {label}: неверный ID карты")
			continue
		card = await db.get_card_by_id(card_id)
		if not card:
			entries.append(f"✅ {label}: карта id {card_id} не найдена")
			continue
		group_id = card.get("group_id")
		if group_id:
			group = await db.get_card_group(group_id)
			group_name = group.get("name", "Группа") if group else "Группа"
		else:
			group_name = "Без группы"
		card_name = card.get("name") or f"id {card_id}"
		entries.append(f"✅ {label}: {card_name} ({group_name})")
	return "Одна карта для всех:\n" + "\n".join(entries)


async def safe_edit_text(message, text: str, reply_markup=None, parse_mode=None):
	"""
	Безопасно редактирует текст сообщения, игнорируя ошибку "message is not modified".
	Если сообщение не содержит текста (например, это фото), отправляет новое сообщение.
	
	Args:
		message: Объект сообщения (Message или CallbackQuery.message)
		text: Текст для установки
		reply_markup: Клавиатура (опционально)
		parse_mode: Режим парсинга (опционально)
	"""
	try:
		await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
	except TelegramNetworkError as e:
		logger.warning(f"⚠️ Сеть недоступна при редактировании сообщения: {e}. Повторить позже.")
		return
	except Exception as e:
		error_str = str(e).lower()
		# Игнорируем ошибку "message is not modified", если содержимое не изменилось
		if "message is not modified" in error_str:
			return
		# Если сообщение не содержит текста, отправляем новое сообщение
		if "there is no text in the message to edit" in error_str or "no text" in error_str:
			await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
		else:
			raise


async def safe_edit_text_or_caption(message, text: str, reply_markup=None, parse_mode=None):
	"""
	Редактирует текст, а если это медиа-сообщение — редактирует caption.
	"""
	try:
		await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
	except TelegramNetworkError as e:
		logger.warning(f"⚠️ Сеть недоступна при редактировании сообщения: {e}. Повторить позже.")
		return
	except Exception as e:
		error_str = str(e).lower()
		if "message is not modified" in error_str:
			return
		if "there is no text in the message to edit" in error_str or "no text" in error_str:
			try:
				await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
			except Exception:
				await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
		else:
			raise


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
	BYN: до 1500
	RUB: 1500 и больше
	"""
	if amount < 1500:
		return "BYN"
	else:
		return "RUB"


def find_btc_addresses(text: str) -> list[str]:
	"""
	Находит все BTC адреса в тексте.
	
	Поддерживает форматы:
	- Bech32 (bc1...): bc1qq3e8wsy3u979ghmc0xht257zlm70gpha522n6y
	- Legacy (1... или 3...): 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa
	
	Args:
		text: Текст для поиска
	
	Returns:
		Список найденных BTC адресов
	"""
	if not text:
		return []
	
	import re
	addresses = []
	
	# Паттерн для Bech32 адресов (bc1...)
	bech32_pattern = r'\bbc1[a-z0-9]{25,62}\b'
	bech32_matches = re.findall(bech32_pattern, text, re.IGNORECASE)
	addresses.extend(bech32_matches)
	
	# Паттерн для Legacy адресов (начинаются с 1 или 3)
	legacy_pattern = r'\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b'
	legacy_matches = re.findall(legacy_pattern, text)
	addresses.extend(legacy_matches)
	
	# Удаляем дубликаты, сохраняя порядок
	seen = set()
	unique_addresses = []
	for addr in addresses:
		if addr.lower() not in seen:
			seen.add(addr.lower())
			unique_addresses.append(addr)
	
	return unique_addresses


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
	# Очищаем предыдущее состояние перед показом меню
	await state.clear()
	await message.answer("Админ-панель:", reply_markup=admin_menu_kb())


@admin_router.message(Command("user_add"))
async def cmd_user_add(message: Message):
	"""Добавить доступ пользователю по tg_id или @username."""
	db = get_db()
	args = (message.text or "").split(maxsplit=1)
	if len(args) < 2 or not args[1].strip():
		await message.answer("Использование: /user_add <tg_id|@username>")
		return
	value = args[1].strip()
	tg_id: Optional[int] = None
	username: Optional[str] = None
	if value.lstrip("@").isdigit():
		# tg_id
		tg_id = int(value.lstrip("@"))
	else:
		username = value
	await db.grant_user_access(tg_id=tg_id, username=username)
	await message.answer("✅ Доступ выдан")


@admin_router.message(Command("user_del"))
async def cmd_user_del(message: Message):
	"""Забрать доступ у пользователя по tg_id или @username."""
	db = get_db()
	args = (message.text or "").split(maxsplit=1)
	if len(args) < 2 or not args[1].strip():
		await message.answer("Использование: /user_del <tg_id|@username>")
		return
	value = args[1].strip()
	tg_id: Optional[int] = None
	username: Optional[str] = None
	if value.lstrip("@").isdigit():
		tg_id = int(value.lstrip("@"))
	else:
		username = value
	await db.revoke_user_access(tg_id=tg_id, username=username)
	await message.answer("✅ Доступ забран")


@admin_router.message(Command("user_list"))
async def cmd_user_list(message: Message):
	"""Показать список пользователей, у которых есть доступ."""
	db = get_db()
	rows = await db.list_allowed_users()
	if not rows:
		await message.answer("Список доступа пуст.")
		return
	lines = ["<b>Пользователи с доступом:</b>"]
	for r in rows[:100]:
		label_parts = []
		if r.get("tg_id") is not None:
			label_parts.append(f"<code>{r['tg_id']}</code>")
		if r.get("username"):
			label_parts.append(f"@{escape(r['username'])}")
		lines.append(" • " + " ".join(label_parts) if label_parts else " • (пусто)")
	if len(rows) > 100:
		lines.append(f"\n…и ещё {len(rows) - 100}")
	await message.answer("\n".join(lines))




@admin_router.message(Command("del"))
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
	
	# Очищаем предыдущее состояние перед началом новой операции
	await state.clear()
	
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


@admin_router.message(Command("del_move"))
async def cmd_del_move(message: Message, state: FSMContext):
	"""Команда для удаления последнего передвижения из Google Sheets"""
	logger.info(f"🔴 ОБРАБОТЧИК cmd_del_move ВЫЗВАН! message_id={message.message_id}, user_id={message.from_user.id if message.from_user else None}")
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	is_admin_user = is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames)
	
	if not is_admin_user:
		logger.warning(f"🔴 /del_move ignored: user {message.from_user.id} is not admin")
		return
	
	logger.info(f"✅ /del_move обрабатывается для админа {message.from_user.id}")
	
	# Очищаем предыдущее состояние перед началом новой операции
	await state.clear()
	
	# Получаем настройки Google Sheets
	from app.config import get_settings
	settings = get_settings()
	
	if not settings.google_sheet_id or not settings.google_credentials_path:
		await message.answer("⚠️ Google Sheets не настроен (отсутствует GOOGLE_SHEET_ID или GOOGLE_CREDENTIALS_PATH)")
		return
	
	# Спрашиваем первое подтверждение
	await state.set_state(DeleteMoveStates.first_confirmation)
	await message.answer("⚠️ Вы действительно хотите удалить последнее передвижение?", reply_markup=delete_confirmation_kb())


@admin_router.callback_query(DeleteMoveStates.first_confirmation, F.data == "delete:confirm:yes")
async def delete_move_first_confirmation_yes(cb: CallbackQuery, state: FSMContext):
	"""Обработчик первого подтверждения удаления move - пользователь нажал 'Да'"""
	# Переходим ко второму подтверждению
	await state.set_state(DeleteMoveStates.second_confirmation)
	await cb.message.edit_text("⚠️ Вы уверены? Это действие нельзя отменить.", reply_markup=delete_confirmation_kb())
	await cb.answer()


@admin_router.callback_query(DeleteMoveStates.first_confirmation, F.data == "delete:confirm:no")
async def delete_move_first_confirmation_no(cb: CallbackQuery, state: FSMContext):
	"""Обработчик первого подтверждения удаления move - пользователь нажал 'Нет'"""
	await state.clear()
	await cb.message.edit_text("❌ Операция удаления отменена.")
	await cb.answer()


@admin_router.callback_query(DeleteMoveStates.second_confirmation, F.data == "delete:confirm:yes")
async def delete_move_second_confirmation_yes(cb: CallbackQuery, state: FSMContext):
	"""Обработчик второго подтверждения удаления move - выполняет удаление"""
	# Удаляем последнюю строку в диапазоне move
	from app.google_sheets import delete_last_move_row_from_google_sheet
	from app.config import get_settings
	
	settings = get_settings()
	
	try:
		result = await delete_last_move_row_from_google_sheet(
			settings.google_sheet_id,
			settings.google_credentials_path,
			settings.google_sheet_name
		)
		
		if result.get("success"):
			deleted_row = result.get("deleted_row")
			await cb.message.edit_text(f"✅ Успешно удалено последнее передвижение (строка {deleted_row})")
		else:
			error_message = result.get("message", "Неизвестная ошибка")
			await cb.message.edit_text(f"❌ Ошибка удаления: {error_message}")
	except Exception as e:
		logger.exception(f"Ошибка при удалении передвижения: {e}")
		await cb.message.edit_text(f"❌ Произошла ошибка при удалении: {str(e)}")
	finally:
		await state.clear()
		await cb.answer()


@admin_router.callback_query(DeleteMoveStates.second_confirmation, F.data == "delete:confirm:no")
async def delete_move_second_confirmation_no(cb: CallbackQuery, state: FSMContext):
	"""Обработчик второго подтверждения удаления move - пользователь нажал 'Нет'"""
	await state.clear()
	await cb.message.edit_text("❌ Операция удаления отменена.")
	await cb.answer()


@admin_router.message(Command("del_rate"))
async def cmd_del_rate(message: Message, state: FSMContext):
	"""Команда для удаления последней операции /rate из Google Sheets"""
	logger.info(f"🔴 ОБРАБОТЧИК cmd_del_rate ВЫЗВАН! message_id={message.message_id}, user_id={message.from_user.id if message.from_user else None}")
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	is_admin_user = is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames)
	
	if not is_admin_user:
		logger.warning(f"🔴 /del_rate ignored: user {message.from_user.id} is not admin")
		return
	
	logger.info(f"✅ /del_rate обрабатывается для админа {message.from_user.id}")
	
	# Очищаем предыдущее состояние перед началом новой операции
	await state.clear()
	
	# Получаем настройки Google Sheets
	from app.config import get_settings
	settings = get_settings()
	
	if not settings.google_sheet_id or not settings.google_credentials_path:
		await message.answer("⚠️ Google Sheets не настроен (отсутствует GOOGLE_SHEET_ID или GOOGLE_CREDENTIALS_PATH)")
		return
	
	# Проверяем, есть ли история операций
	db = get_db()
	last_history = await db.get_last_rate_history()
	
	if not last_history:
		await message.answer("⚠️ Нет истории операций /rate для удаления")
		return
	
	# Сохраняем ID истории в состояние для использования при подтверждении
	await state.update_data(history_id=last_history["id"], operations_history=last_history["operations"])
	
	# Спрашиваем первое подтверждение
	await state.set_state(DeleteRateStates.first_confirmation)
	await message.answer("⚠️ Вы действительно хотите удалить последнюю операцию расхода?", reply_markup=delete_confirmation_kb())


@admin_router.callback_query(DeleteRateStates.first_confirmation, F.data == "delete:confirm:yes")
async def delete_rate_first_confirmation_yes(cb: CallbackQuery, state: FSMContext):
	"""Обработчик первого подтверждения удаления /rate - пользователь нажал 'Да'"""
	# Переходим ко второму подтверждению
	await state.set_state(DeleteRateStates.second_confirmation)
	await cb.message.edit_text("⚠️ Вы уверены? Это действие нельзя отменить.", reply_markup=delete_confirmation_kb())
	await cb.answer()


@admin_router.callback_query(DeleteRateStates.first_confirmation, F.data == "delete:confirm:no")
async def delete_rate_first_confirmation_no(cb: CallbackQuery, state: FSMContext):
	"""Обработчик первого подтверждения удаления /rate - пользователь нажал 'Нет'"""
	await state.clear()
	await cb.message.edit_text("❌ Операция удаления отменена.")
	await cb.answer()


@admin_router.callback_query(DeleteRateStates.second_confirmation, F.data == "delete:confirm:yes")
async def delete_rate_second_confirmation_yes(cb: CallbackQuery, state: FSMContext):
	"""Обработчик второго подтверждения удаления /rate - выполняет удаление"""
	from app.google_sheets import delete_last_rate_operation
	from app.config import get_settings
	
	settings = get_settings()
	data = await state.get_data()
	history_id = data.get("history_id")
	operations_history_json = data.get("operations_history")
	
	if not history_id or not operations_history_json:
		await cb.message.edit_text("❌ Ошибка: не найдена история операции")
		await state.clear()
		await cb.answer()
		return
	
	try:
		# Парсим JSON с историей операций
		operations_history = json.loads(operations_history_json)
		
		# Получаем информацию о последней истории для отображения даты (до удаления из БД)
		db = get_db()
		last_history = await db.get_last_rate_history()
		
		# Удаляем ячейки из Google Sheets
		result = await delete_last_rate_operation(
			settings.google_sheet_id,
			settings.google_credentials_path,
			operations_history,
			settings.google_sheet_name
		)
		
		if result.get("success"):
			deleted_cells_info = result.get("deleted_cells_info", [])
			# Удаляем запись из БД
			await db.delete_rate_history(history_id)
			
			# Формируем подробный отчет
			from datetime import datetime
			created_at = last_history.get("created_at") if last_history else None
			if created_at:
				date_str = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
			else:
				date_str = "неизвестно"
			
			# Получаем примечание из истории
			note = last_history.get("note") if last_history else None
			if note and note.strip():
				note_text = note.strip()
			else:
				note_text = None
			
			report_lines = [f"✅ Успешно удалена последняя операция расхода", f"От: {date_str}"]
			
			# Добавляем примечание, если оно есть
			if note_text:
				report_lines.append(f"📝 Примечание: {note_text}")
			
			report_lines.append("")
			report_lines.append("Удаленные ячейки:")
			
			for cell_info in deleted_cells_info:
				cell_address = cell_info.get("cell", "")
				value = cell_info.get("value")
				cell_type = cell_info.get("type", "")
				
				# Форматируем значение
				if value is not None:
					try:
						value_float = float(str(value).replace(",", ".").replace(" ", ""))
						value_str = f"{int(round(value_float)):,}".replace(",", " ")
					except (ValueError, TypeError):
						value_str = str(value)
				else:
					value_str = "—"
				
				# Формируем описание в зависимости от типа
				if cell_type == "crypto":
					crypto_type = cell_info.get("crypto_type", "")
					report_lines.append(f"  • {cell_address} ({crypto_type}: {value_str} USD)")
				elif cell_type == "xmr":
					xmr_number = cell_info.get("xmr_number")
					report_lines.append(f"  • {cell_address} (XMR-{xmr_number}: {value_str} USD)")
				elif cell_type == "card":
					card_name = cell_info.get("card_name", "")
					currency = cell_info.get("currency", "RUB")
					report_lines.append(f"  • {cell_address} (Карта {card_name}: {value_str} {currency})")
				elif cell_type == "cash":
					cash_name = cell_info.get("cash_name", "")
					currency = cell_info.get("currency", "RUB")
					report_lines.append(f"  • {cell_address} (Наличные {cash_name}: {value_str} {currency})")
				else:
					report_lines.append(f"  • {cell_address} ({value_str})")
			
			# Получаем новый баланс после удаления
			from app.google_sheets import get_crypto_values_from_row_4, read_card_balances_batch
			
			# Получаем balance_row из настроек
			balance_row_str = await db.get_google_sheets_setting("balance_row", "4")
			balance_row = int(balance_row_str) if balance_row_str else 4
			
			# Собираем уникальные карты, криптовалюты и наличку из удаленных ячеек
			cards_to_check = set()  # {card_name}
			crypto_to_check = set()  # {crypto_type}
			cash_to_check = set()  # {cash_name}
			
			for cell_info in deleted_cells_info:
				cell_type = cell_info.get("type", "")
				if cell_type == "card":
					card_name = cell_info.get("card_name", "")
					if card_name:
						cards_to_check.add(card_name)
				elif cell_type == "crypto":
					crypto_type = cell_info.get("crypto_type", "")
					if crypto_type:
						crypto_to_check.add(crypto_type)
				elif cell_type == "cash":
					cash_name = cell_info.get("cash_name", "")
					if cash_name:
						cash_to_check.add(cash_name)
			
			# Получаем балансы карт
			card_balances = {}
			if cards_to_check:
				card_balance_cell_addresses = []
				card_mapping = {}  # {cell_address: card_name}
				
				# Получаем все карты с их столбцами
				all_cards_data = await db.get_all_cards_with_columns_and_groups()
				
				for card_name in cards_to_check:
					# Ищем карту по имени
					card_info = None
					for card_data in all_cards_data:
						if card_data.get("name") == card_name:
							card_info = card_data
							break
					
					if card_info and card_info.get("column"):
						column = card_info.get("column")
						cell_address = f"{column}{balance_row}"
						card_balance_cell_addresses.append(cell_address)
						card_mapping[cell_address] = card_name
				
				# Читаем все балансы карт одним batch запросом
				if card_balance_cell_addresses:
					try:
						card_balances_dict = await read_card_balances_batch(
							settings.google_sheet_id,
							settings.google_credentials_path,
							card_balance_cell_addresses,
							settings.google_sheet_name
						)
						for cell_address, card_name in card_mapping.items():
							balance = card_balances_dict.get(cell_address)
							if balance:
								card_balances[card_name] = balance
					except Exception as e:
						logger.warning(f"Ошибка чтения балансов карт: {e}")
			
			# Получаем балансы криптовалют
			crypto_balances = {}
			if crypto_to_check:
				try:
					# Получаем все криптовалюты из БД
					all_crypto_columns = await db.list_crypto_columns()
					# Фильтруем только те, которые были в удаленных ячейках
					crypto_columns_to_read = [
						crypto for crypto in all_crypto_columns
						if crypto.get("crypto_type") in crypto_to_check
					]
					
					if crypto_columns_to_read:
						crypto_values = await get_crypto_values_from_row_4(
							settings.google_sheet_id,
							settings.google_credentials_path,
							crypto_columns_to_read,
							settings.google_sheet_name
						)
						for crypto_type in crypto_to_check:
							value = crypto_values.get(crypto_type)
							if value:
								crypto_balances[crypto_type] = value
				except Exception as e:
					logger.warning(f"Ошибка чтения балансов криптовалют: {e}")
			
			# Получаем балансы налички
			cash_balances = {}
			if cash_to_check:
				cash_balance_cell_addresses = []
				cash_mapping = {}  # {cell_address: cash_name}
				
				for cash_name in cash_to_check:
					# Получаем столбец налички из БД
					cash_column_info = await db.get_cash_column(cash_name)
					if cash_column_info and cash_column_info.get("column"):
						column = cash_column_info.get("column")
						cell_address = f"{column}{balance_row}"
						cash_balance_cell_addresses.append(cell_address)
						cash_mapping[cell_address] = cash_name
				
				# Читаем все балансы налички одним batch запросом
				if cash_balance_cell_addresses:
					try:
						cash_balances_dict = await read_card_balances_batch(
							settings.google_sheet_id,
							settings.google_credentials_path,
							cash_balance_cell_addresses,
							settings.google_sheet_name
						)
						for cell_address, cash_name in cash_mapping.items():
							balance = cash_balances_dict.get(cell_address)
							if balance:
								cash_balances[cash_name] = balance
					except Exception as e:
						logger.warning(f"Ошибка чтения балансов налички: {e}")
			
			# Добавляем новый баланс в отчет
			if card_balances or crypto_balances or cash_balances:
				report_lines.append("")
				report_lines.append("💰 Новый баланс после удаления:")
				
				# Добавляем балансы карт
				if card_balances:
					for card_name, balance in sorted(card_balances.items()):
						report_lines.append(f"  💳 Карта {card_name} = {balance}")
				
				# Добавляем балансы криптовалют
				if crypto_balances:
					for crypto_type, balance in sorted(crypto_balances.items()):
						# Форматируем значение
						try:
							balance_float = float(str(balance).replace(",", ".").replace(" ", ""))
							formatted_balance = f"{int(round(balance_float)):,}".replace(",", " ")
							report_lines.append(f"  ₿ {crypto_type} = {formatted_balance} USD")
						except (ValueError, TypeError):
							report_lines.append(f"  ₿ {crypto_type} = {balance} USD")
				
				# Добавляем балансы налички
				if cash_balances:
					for cash_name, balance in sorted(cash_balances.items()):
						report_lines.append(f"  💵 Наличные {cash_name} = {balance}")
			
			report_text = "\n".join(report_lines)
			await cb.message.edit_text(report_text)
		else:
			error_message = result.get("message", "Неизвестная ошибка")
			await cb.message.edit_text(f"❌ Ошибка удаления: {error_message}")
	except Exception as e:
		logger.exception(f"Ошибка при удалении операции /rate: {e}")
		await cb.message.edit_text(f"❌ Произошла ошибка при удалении: {str(e)}")
	finally:
		await state.clear()
		await cb.answer()


@admin_router.callback_query(DeleteRateStates.second_confirmation, F.data == "delete:confirm:no")
async def delete_rate_second_confirmation_no(cb: CallbackQuery, state: FSMContext):
	"""Обработчик второго подтверждения удаления /rate - пользователь нажал 'Нет'"""
	await state.clear()
	await cb.message.edit_text("❌ Операция удаления отменена.")
	await cb.answer()


@admin_router.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery, state: FSMContext):
	await state.clear()
	await safe_edit_text(cb.message, "Админ-панель:", reply_markup=admin_menu_kb())
	await cb.answer()


@admin_router.callback_query(F.data == "admin:settings")
async def admin_settings(cb: CallbackQuery, state: FSMContext):
	await state.clear()
	db = get_db()
	enabled = await _one_card_for_all_enabled(db)
	await safe_edit_text(cb.message, "⚙️ Настройки:", reply_markup=admin_settings_kb(enabled))
	await cb.answer()


@admin_router.callback_query(F.data == "settings:debtors")
async def settings_debtors(cb: CallbackQuery, state: FSMContext):
	await state.clear()
	text, kb = await _get_debtors_list_text_kb()
	await safe_edit_text(cb.message, text, reply_markup=kb)
	await cb.answer()


@admin_router.callback_query(F.data == "settings:one_card_for_all")
async def settings_one_card_for_all(cb: CallbackQuery, state: FSMContext):
	await state.clear()
	db = get_db()
	status_text = await _one_card_for_all_status_text(db)
	await safe_edit_text(
		cb.message,
		f"{status_text}\n\nВыберите страну для установки одной карты для всех:",
		reply_markup=_one_card_country_kb()
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:one_card_for_all:country:"))
async def settings_one_card_for_all_country(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	try:
		country_code = cb.data.split(":")[3]
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	groups = await db.list_card_groups()
	selected_group_id = None
	selected_card_id = await db.get_setting(f"one_card_for_all_{country_code}")
	if selected_card_id:
		try:
			card = await db.get_card_by_id(int(selected_card_id))
		except (TypeError, ValueError):
			card = None
		if card:
			group_id = card.get("group_id")
			selected_group_id = group_id if group_id else 0
	await safe_edit_text(
		cb.message,
		"📁 Выберите группу карт:",
		reply_markup=_one_card_groups_kb(groups, country_code, selected_group_id=selected_group_id).as_markup()
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:one_card_for_all:disable:"))
async def settings_one_card_for_all_disable(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	country_code = parts[3]
	await db.set_setting(f"one_card_for_all_{country_code}", "")
	groups = await db.list_card_groups()
	country_label = "Беларусь" if country_code == "BYN" else "Россия" if country_code == "RUB" else country_code
	await safe_edit_text(
		cb.message,
		f"✅ Одна карта для всех отключена для страны: {country_label}.\n\n📁 Выберите группу карт:",
		reply_markup=_one_card_groups_kb(groups, country_code, selected_group_id=None).as_markup()
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:one_card_for_all:group:"))
async def settings_one_card_for_all_group(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) < 5:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	country_code = parts[3]
	try:
		group_id = int(parts[4])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	if group_id == 0:
		cards = await db.get_cards_without_group()
		group_name = "Без группы"
	else:
		cards = await db.get_cards_by_group(group_id)
		group = await db.get_card_group(group_id)
		group_name = group.get("name", "Группа") if group else "Группа"
	cards_list = [(c[0], c[1]) for c in cards]
	if not cards_list:
		await cb.answer("Нет карт в группе", show_alert=True)
		return
	await safe_edit_text(
		cb.message,
		f"Карты группы '{group_name}':",
		reply_markup=_one_card_cards_kb(cards_list, country_code, group_id).as_markup()
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:one_card_for_all:card:"))
async def settings_one_card_for_all_card(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) < 5:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	country_code = parts[3]
	try:
		card_id = int(parts[4])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	await db.set_setting(f"one_card_for_all_{country_code}", str(card_id))
	enabled = await _one_card_for_all_enabled(db)
	await safe_edit_text(
		cb.message,
		"✅ Карта установлена для всех пользователей выбранной страны.",
		reply_markup=admin_settings_kb(enabled)
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("debtors:view:"))
async def debtors_view(cb: CallbackQuery, state: FSMContext):
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		user_tg_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	db = get_db()
	totals = await db.get_user_total_debt(user_tg_id)
	totals = {k: v for k, v in totals.items() if v and v > 0}
	user = await db.get_user_by_tg(user_tg_id)
	name = user.get("full_name") if user else None
	username = user.get("username") if user else None
	name_label = name or (f"@{username}" if username else str(user_tg_id))
	
	text = f"👤 Имя: {name_label}\n"
	if username:
		text += f"📱 Username: @{username}\n"
	text += f"🆔 ID: <code>{user_tg_id}</code>\n"
	text += f"💳 Долг: {_format_debt_totals(totals) if totals else '0'}\n\n"
	text += "Выберите действие:"
	
	kb = InlineKeyboardBuilder()
	kb.button(text="➕ Добавить долг", callback_data=f"debtors:add:{user_tg_id}")
	kb.button(text="➖ Списать долг", callback_data=f"debtors:writeoff:{user_tg_id}")
	kb.button(text="⬅️ Назад", callback_data="settings:debtors")
	kb.adjust(1)
	
	await safe_edit_text(cb.message, text, reply_markup=kb.as_markup(), parse_mode="HTML")
	await cb.answer()


@admin_router.callback_query(F.data.startswith("debtors:add:") | F.data.startswith("debtors:writeoff:"))
async def debtors_action_start(cb: CallbackQuery, state: FSMContext):
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	action = parts[1]
	try:
		user_tg_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	await state.update_data(debt_user_tg_id=user_tg_id, debt_action=action)
	await state.set_state(DebtorsStates.waiting_currency)
	
	kb = InlineKeyboardBuilder()
	kb.button(text="BYN", callback_data="debtors:currency:BYN")
	kb.button(text="RUB", callback_data="debtors:currency:RUB")
	kb.button(text="⬅️ Назад", callback_data=f"debtors:view:{user_tg_id}")
	kb.adjust(1)
	
	await safe_edit_text(cb.message, "Выберите валюту долга:", reply_markup=kb.as_markup())
	await cb.answer()


@admin_router.callback_query(F.data.startswith("debtors:currency:"))
async def debtors_currency_selected(cb: CallbackQuery, state: FSMContext):
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	currency = parts[2]
	
	await state.update_data(debt_currency=currency)
	await state.set_state(DebtorsStates.waiting_amount)
	
	await safe_edit_text(cb.message, f"Введите сумму ({currency}):", reply_markup=cb.message.reply_markup)
	await cb.answer()


@admin_router.message(DebtorsStates.waiting_amount)
async def debtors_amount_save(message: Message, state: FSMContext, bot: Bot):
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	if message.text and message.text.startswith("/"):
		return
	
	data = await state.get_data()
	user_tg_id = data.get("debt_user_tg_id")
	action = data.get("debt_action")
	currency = data.get("debt_currency")
	if not user_tg_id or not action or not currency:
		await message.answer("❌ Ошибка: данные не найдены.")
		await state.clear()
		return
	
	try:
		amount_str = message.text.strip().replace(",", ".")
		amount = float(amount_str)
		if amount <= 0:
			await message.answer("❌ Сумма должна быть больше нуля.")
			return
	except ValueError:
		await message.answer("❌ Неверный формат суммы.")
		return
	
	db = get_db()
	totals = await db.get_user_total_debt(user_tg_id)
	current_total = float(totals.get(currency, 0) or 0)
	
	if action == "writeoff":
		if current_total <= 0:
			await message.answer("❌ У пользователя нет долга в этой валюте.")
			return
		if amount > current_total:
			await message.answer("❌ Сумма списания больше текущего долга.")
			return
		amount_to_save = -amount
	else:
		amount_to_save = amount
	
	await db.add_user_debt(user_tg_id, amount_to_save, currency)
	await state.clear()
	
	# Удаляем сообщение админа
	from app.main import delete_user_message
	await delete_user_message(message)
	
	text, kb = await _get_debtors_list_text_kb()
	await message.answer(text, reply_markup=kb)


def _format_debt_totals(totals: Dict[str, float]) -> str:
	parts = []
	for curr, amount in totals.items():
		try:
			amount_val = int(amount)
		except (ValueError, TypeError):
			amount_val = amount
		parts.append(f"{amount_val} {curr}")
	return ", ".join(parts)


def _build_payment_order_message(
	crypto_type: str,
	crypto_display: str,
	amount: float,
	final_amount: float,
	currency_symbol: str,
	wallet_address: str,
	requisites_text: str,
) -> str:
	if amount < 1:
		amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
	else:
		amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
	country_label = "🇧🇾Беларусь" if currency_symbol == "Br" else "🇷🇺Россия"
	requisites_block = requisites_text.strip() if requisites_text else ""
	if not requisites_block:
		requisites_block = "Реквизитов нет, ожидайте сообщение администратора"
	order_message = (
		"Я помогу😊....\n"
		"⬇️Сделка⬇️\n"
		"➖➖➖➖➖➖\n"
		f"{country_label}\n"
		f"🤑{crypto_type}\n"
		f"💴{amount_str}\n"
		f"💵{int(final_amount)} {currency_symbol}\n"
		"➖➖➖➖➖➖➖➖➖➖➖\n"
		f"{requisites_block}"
	)
	return order_message


def _deal_requisites_kb(cards: List[Tuple[int, str]], deal_id: int) -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	for card_id, card_name in cards:
		kb.button(text=f"💳 {card_name}", callback_data=f"dealalert:requisites:select:{deal_id}:{card_id}")
	kb.button(text="⬅️ Назад", callback_data=f"dealalert:requisites:back:{deal_id}")
	kb.adjust(1)
	return kb


def _deal_groups_kb(groups: List[Dict[str, Any]], deal_id: int, include_ungrouped: bool = True) -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	for group in groups:
		group_name = group.get("name", "")
		group_id = group.get("id")
		kb.button(text=f"📁 {group_name}", callback_data=f"dealalert:group:{deal_id}:{group_id}")
	if include_ungrouped:
		kb.button(text="📋 Без группы", callback_data=f"dealalert:group:{deal_id}:0")
	kb.button(text="⬅️ Назад", callback_data=f"dealalert:requisites:back:{deal_id}")
	kb.adjust(1)
	return kb


def _deal_cards_kb(cards: List[Tuple[int, str]], deal_id: int, back_to_group: int) -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	for card_id, card_name in cards:
		kb.button(text=f"💳 {card_name}", callback_data=f"dealalert:card:{deal_id}:{card_id}")
	kb.button(text="⬅️ Назад", callback_data=f"dealalert:group:{deal_id}:{back_to_group}")
	kb.adjust(1)
	return kb


async def _get_debtors_list_text_kb():
	db = get_db()
	debtors = await db.get_debtors_totals()
	
	# Формируем список с суммами
	items = []
	for row in debtors:
		user_tg_id = row["user_tg_id"]
		totals = {k: v for k, v in row["totals"].items() if v and v > 0}
		if not totals:
			continue
		user = await db.get_user_by_tg(user_tg_id)
		if user:
			name = user.get("full_name") or f"@{user.get('username')}" or str(user_tg_id)
		else:
			name = str(user_tg_id)
		items.append((user_tg_id, name, _format_debt_totals(totals)))
	
	kb = InlineKeyboardBuilder()
	for user_tg_id, name, totals_str in items:
		kb.button(text=f"{name} — {totals_str}", callback_data=f"debtors:view:{user_tg_id}")
	kb.button(text="⬅️ Назад", callback_data="admin:back")
	kb.adjust(1)
	
	text = "💳 Должники:\n" if items else "💳 Должников нет."
	return text, kb.as_markup()


def _parse_float(value: str, default: float) -> float:
	try:
		return float(value) if value is not None else default
	except (ValueError, TypeError):
		return default


async def _get_buy_calc_settings(db) -> dict:
	return {
		"buy_markup_percent_small": _parse_float(await db.get_setting("buy_markup_percent_small", "15"), 15),
		"buy_markup_percent_101_449": _parse_float(await db.get_setting("buy_markup_percent_101_449", "11"), 11),
		"buy_markup_percent_450_699": _parse_float(await db.get_setting("buy_markup_percent_450_699", "9"), 9),
		"buy_markup_percent_700_999": _parse_float(await db.get_setting("buy_markup_percent_700_999", "8"), 8),
		"buy_markup_percent_1000_1499": _parse_float(await db.get_setting("buy_markup_percent_1000_1499", "7"), 7),
		"buy_markup_percent_1500_1999": _parse_float(await db.get_setting("buy_markup_percent_1500_1999", "6"), 6),
		"buy_markup_percent_2000_plus": _parse_float(await db.get_setting("buy_markup_percent_2000_plus", "5"), 5),
		"buy_min_usd": _parse_float(await db.get_setting("buy_min_usd", "15"), 15),
		"buy_extra_fee_usd_low": _parse_float(await db.get_setting("buy_extra_fee_usd_low", "50"), 50),
		"buy_extra_fee_usd_mid": _parse_float(await db.get_setting("buy_extra_fee_usd_mid", "67"), 67),
		"buy_extra_fee_low_byn": _parse_float(await db.get_setting("buy_extra_fee_low_byn", "10"), 10),
		"buy_extra_fee_mid_byn": _parse_float(await db.get_setting("buy_extra_fee_mid_byn", "5"), 5),
		"buy_extra_fee_low_rub": _parse_float(await db.get_setting("buy_extra_fee_low_rub", "10"), 10),
		"buy_extra_fee_mid_rub": _parse_float(await db.get_setting("buy_extra_fee_mid_rub", "5"), 5),
	"buy_alert_usd_threshold": _parse_float(await db.get_setting("buy_alert_usd_threshold", "400"), 400),
		"buy_usd_to_byn_rate": _parse_float(await db.get_setting("buy_usd_to_byn_rate", "2.97"), 2.97),
		"buy_usd_to_rub_rate": _parse_float(await db.get_setting("buy_usd_to_rub_rate", "95"), 95),
	}


@admin_router.callback_query(F.data == "settings:buy_calc")
async def settings_buy_calc(cb: CallbackQuery):
	"""Показывает настройки расчета покупки"""
	db = get_db()
	settings = await _get_buy_calc_settings(db)
	await safe_edit_text(
		cb.message,
		"🧮 Настройки расчета покупки:\n\n"
		f"📉 $0-100: {settings['buy_markup_percent_small']}%\n"
		f"📈 $101-449: {settings['buy_markup_percent_101_449']}%\n"
		f"📈 $450-699: {settings['buy_markup_percent_450_699']}%\n"
		f"📈 $700-999: {settings['buy_markup_percent_700_999']}%\n"
		f"📈 $1000-1499: {settings['buy_markup_percent_1000_1499']}%\n"
		f"📈 $1500-1999: {settings['buy_markup_percent_1500_1999']}%\n"
		f"📈 $2000+: {settings['buy_markup_percent_2000_plus']}%\n"
		f"✅ Мин. сумма сделки: {settings['buy_min_usd']}$\n"
		f"💵 Порог 1: < {settings['buy_extra_fee_usd_low']}$\n"
		f"💵 Порог 2: < {settings['buy_extra_fee_usd_mid']}$\n"
		f"➕ BYN: +{settings['buy_extra_fee_low_byn']} / +{settings['buy_extra_fee_mid_byn']}\n"
		f"➕ RUB: +{settings['buy_extra_fee_low_rub']} / +{settings['buy_extra_fee_mid_rub']}\n"
		f"🚨 Алерт от $: {settings['buy_alert_usd_threshold']}\n"
		f"💱 USD→BYN: {settings['buy_usd_to_byn_rate']}\n"
		f"💱 USD→RUB: {settings['buy_usd_to_rub_rate']}\n\n"
		"Выберите параметр для редактирования:",
		reply_markup=buy_calc_settings_kb(settings),
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:buy_calc:edit:"))
async def settings_buy_calc_edit(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование параметра расчета покупки"""
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	key = parts[3]
	
	db = get_db()
	current_value = await db.get_setting(key, "")
	await state.update_data(buy_calc_key=key)
	await state.set_state(BuyCalcEditStates.waiting_value)
	
	await safe_edit_text(
		cb.message,
		f"🧮 Введите новое значение для '{key}':\n\n"
		f"Текущее значение: {current_value}\n\n"
		"Введите число (например: 2.97 или 15):",
		reply_markup=simple_back_kb("admin:settings")
	)
	await cb.answer()


@admin_router.message(BuyCalcEditStates.waiting_value)
async def settings_buy_calc_save(message: Message, state: FSMContext):
	"""Сохраняет параметр расчета покупки"""
	data = await state.get_data()
	key = data.get("buy_calc_key")
	if not key:
		await state.clear()
		await message.answer("❌ Ошибка: не найден ключ настройки.")
		return
	
	value_str = message.text.strip().replace(",", ".")
	try:
		value = float(value_str)
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число.")
		return
	
	db = get_db()
	await db.set_setting(key, str(value))
	await state.clear()
	await message.answer(f"✅ Настройка {key} обновлена: {value}")
	
	settings = await _get_buy_calc_settings(db)
	await message.answer(
		"🧮 Настройки расчета покупки:\n\n"
		f"📉 $0-100: {settings['buy_markup_percent_small']}%\n"
		f"📈 $101-449: {settings['buy_markup_percent_101_449']}%\n"
		f"📈 $450-699: {settings['buy_markup_percent_450_699']}%\n"
		f"📈 $700-999: {settings['buy_markup_percent_700_999']}%\n"
		f"📈 $1000-1499: {settings['buy_markup_percent_1000_1499']}%\n"
		f"📈 $1500-1999: {settings['buy_markup_percent_1500_1999']}%\n"
		f"📈 $2000+: {settings['buy_markup_percent_2000_plus']}%\n"
		f"✅ Мин. сумма сделки: {settings['buy_min_usd']}$\n"
		f"💵 Порог 1: < {settings['buy_extra_fee_usd_low']}$\n"
		f"💵 Порог 2: < {settings['buy_extra_fee_usd_mid']}$\n"
		f"➕ BYN: +{settings['buy_extra_fee_low_byn']} / +{settings['buy_extra_fee_mid_byn']}\n"
		f"➕ RUB: +{settings['buy_extra_fee_low_rub']} / +{settings['buy_extra_fee_mid_rub']}\n"
		f"🚨 Алерт от $: {settings['buy_alert_usd_threshold']}\n"
		f"💱 USD→BYN: {settings['buy_usd_to_byn_rate']}\n"
		f"💱 USD→RUB: {settings['buy_usd_to_rub_rate']}\n\n"
		"Выберите параметр для редактирования:",
		reply_markup=buy_calc_settings_kb(settings),
	)


@admin_router.callback_query(F.data == "settings:multipliers")
async def settings_multipliers(cb: CallbackQuery):
	"""Показывает настройки коэффициентов"""
	db = get_db()
	multiplier_byn_str = await db.get_google_sheets_setting("multiplier_byn", "1.15")
	multiplier_rub_str = await db.get_google_sheets_setting("multiplier_rub", "1.15")
	
	try:
		multiplier_byn = float(multiplier_byn_str) if multiplier_byn_str else 1.15
	except (ValueError, TypeError):
		multiplier_byn = 1.15
	
	try:
		multiplier_rub = float(multiplier_rub_str) if multiplier_rub_str else 1.15
	except (ValueError, TypeError):
		multiplier_rub = 1.15
	
	await safe_edit_text(
		cb.message,
		"💰 Настройки коэффициентов:\n\n"
		f"🇧🇾 Коэффициент для BYN: {multiplier_byn}\n"
		f"🇷🇺 Коэффициент для RUB: {multiplier_rub}\n\n"
		"Выберите коэффициент для редактирования:",
		reply_markup=multipliers_settings_kb(multiplier_byn, multiplier_rub)
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:multiplier:"))
async def settings_multiplier_edit(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование коэффициента"""
	parts = cb.data.split(":")
	currency = parts[2]  # "byn" или "rub"
	
	db = get_db()
	if currency == "byn":
		current_value = await db.get_google_sheets_setting("multiplier_byn", "1.15")
		currency_name = "BYN"
		multiplier_key = "multiplier_byn"
	else:  # rub
		current_value = await db.get_google_sheets_setting("multiplier_rub", "1.15")
		currency_name = "RUB"
		multiplier_key = "multiplier_rub"
	
	await state.update_data(multiplier_key=multiplier_key, currency_name=currency_name)
	await state.set_state(MultiplierEditStates.waiting_multiplier)
	
	await safe_edit_text(
		cb.message,
		f"💰 Введите новый коэффициент для {currency_name}:\n\n"
		f"Текущее значение: {current_value}\n\n"
		"Введите число (например: 1.15):",
		reply_markup=simple_back_kb("admin:settings")
	)
	await cb.answer()


@admin_router.message(MultiplierEditStates.waiting_multiplier)
async def settings_multiplier_save(message: Message, state: FSMContext):
	"""Сохраняет новый коэффициент"""
	data = await state.get_data()
	multiplier_key = data.get("multiplier_key")
	currency_name = data.get("currency_name")
	
	# Валидация ввода
	multiplier_str = message.text.strip().replace(",", ".")
	try:
		multiplier = float(multiplier_str)
		if multiplier <= 0:
			await message.answer("❌ Коэффициент должен быть больше нуля. Введите корректное значение:")
			return
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число (например: 1.15):")
		return
	
	# Сохраняем в БД
	db = get_db()
	await db.set_google_sheets_setting(multiplier_key, str(multiplier))
	
	await state.clear()
	await message.answer(f"✅ Коэффициент для {currency_name} установлен: {multiplier}")
	
	# Возвращаемся к списку коэффициентов
	multiplier_byn_str = await db.get_google_sheets_setting("multiplier_byn", "1.15")
	multiplier_rub_str = await db.get_google_sheets_setting("multiplier_rub", "1.15")
	
	try:
		multiplier_byn = float(multiplier_byn_str) if multiplier_byn_str else 1.15
	except (ValueError, TypeError):
		multiplier_byn = 1.15
	
	try:
		multiplier_rub = float(multiplier_rub_str) if multiplier_rub_str else 1.15
	except (ValueError, TypeError):
		multiplier_rub = 1.15
	
	await message.answer(
		"💰 Настройки коэффициентов:\n\n"
		f"🇧🇾 Коэффициент для BYN: {multiplier_byn}\n"
		f"🇷🇺 Коэффициент для RUB: {multiplier_rub}\n\n"
		"Выберите коэффициент для редактирования:",
		reply_markup=multipliers_settings_kb(multiplier_byn, multiplier_rub)
	)


@admin_router.callback_query(F.data == "settings:markup_percents")
async def settings_markup_percents(cb: CallbackQuery):
	"""Показывает настройки процентов наценки"""
	db = get_db()
	percent_small_str = await db.get_google_sheets_setting("markup_percent_small", "20")
	percent_large_str = await db.get_google_sheets_setting("markup_percent_large", "15")
	
	try:
		percent_small = float(percent_small_str) if percent_small_str else 20.0
	except (ValueError, TypeError):
		percent_small = 20.0
	
	try:
		percent_large = float(percent_large_str) if percent_large_str else 15.0
	except (ValueError, TypeError):
		percent_large = 15.0
	
	await safe_edit_text(
		cb.message,
		"📊 Настройки процентов наценки:\n\n"
		f"📉 Для заказов < 100$: {percent_small}%\n"
		f"📈 Для заказов >= 100$: {percent_large}%\n\n"
		"Формула: (цена_монеты_в_USD + процент) × количество_монет × курс_валюты\n\n"
		"Выберите процент для редактирования:",
		reply_markup=markup_percents_settings_kb(percent_small, percent_large)
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:markup_percent:"))
async def settings_markup_percent_edit(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование процента наценки"""
	parts = cb.data.split(":")
	percent_type = parts[2]  # "small" или "large"
	
	db = get_db()
	if percent_type == "small":
		current_value = await db.get_google_sheets_setting("markup_percent_small", "20")
		percent_name = "для заказов < 100$"
		percent_key = "markup_percent_small"
		default_value = 20.0
	else:  # large
		current_value = await db.get_google_sheets_setting("markup_percent_large", "15")
		percent_name = "для заказов >= 100$"
		percent_key = "markup_percent_large"
		default_value = 15.0
	
	await state.update_data(percent_key=percent_key, percent_name=percent_name, default_value=default_value)
	await state.set_state(MarkupPercentEditStates.waiting_percent)
	
	await safe_edit_text(
		cb.message,
		f"📊 Введите новый процент наценки {percent_name}:\n\n"
		f"Текущее значение: {current_value}%\n\n"
		"Введите число (например: 20):",
		reply_markup=simple_back_kb("admin:settings")
	)
	await cb.answer()


@admin_router.message(MarkupPercentEditStates.waiting_percent)
async def settings_markup_percent_save(message: Message, state: FSMContext):
	"""Сохраняет новый процент наценки"""
	data = await state.get_data()
	percent_key = data.get("percent_key")
	percent_name = data.get("percent_name")
	default_value = data.get("default_value", 20.0)
	
	# Валидация ввода
	percent_str = message.text.strip().replace(",", ".")
	try:
		percent = float(percent_str)
		if percent < 0 or percent > 100:
			await message.answer("❌ Процент должен быть от 0 до 100. Введите корректное значение:")
			return
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число (например: 20):")
		return
	
	# Сохраняем в БД
	db = get_db()
	await db.set_google_sheets_setting(percent_key, str(percent))
	
	await state.clear()
	await message.answer(f"✅ Процент наценки {percent_name} установлен: {percent}%")
	
	# Возвращаемся к списку процентов
	percent_small_str = await db.get_google_sheets_setting("markup_percent_small", "20")
	percent_large_str = await db.get_google_sheets_setting("markup_percent_large", "15")
	
	try:
		percent_small = float(percent_small_str) if percent_small_str else 20.0
	except (ValueError, TypeError):
		percent_small = 20.0
	
	try:
		percent_large = float(percent_large_str) if percent_large_str else 15.0
	except (ValueError, TypeError):
		percent_large = 15.0
	
	await message.answer(
		"📊 Настройки процентов наценки:\n\n"
		f"📉 Для заказов < 100$: {percent_small}%\n"
		f"📈 Для заказов >= 100$: {percent_large}%\n\n"
		"Формула: (цена_монеты_в_USD + процент) × количество_монет × курс_валюты\n\n"
		"Выберите процент для редактирования:",
		reply_markup=markup_percents_settings_kb(percent_small, percent_large)
	)


@admin_router.callback_query(F.data.startswith("alert:message:"))
async def alert_message_start(cb: CallbackQuery, state: FSMContext):
	"""Начало отправки сообщения пользователю из раннего алерта"""
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		user_tg_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	await state.update_data(alert_user_tg_id=user_tg_id)
	await state.set_state(AlertMessageStates.waiting_message)
	
	# Получаем текущее состояние пользователя для определения этапа
	from app.main import get_user_stage_name
	from aiogram.fsm.storage.base import StorageKey
	
	stage_name = "Неизвестно"
	try:
		# Получаем storage из dispatcher
		storage = state.storage
		if storage:
			# Формируем key для получения состояния пользователя
			bot_id = cb.message.bot.id
			key = StorageKey(
				bot_id=bot_id,
				chat_id=user_tg_id,
				user_id=user_tg_id
			)
			state_data = await storage.get_state(key)
			if state_data:
				stage_name = get_user_stage_name(str(state_data))
	except Exception as e:
		logger.debug(f"Не удалось получить этап пользователя: {e}")
	
	# Обновляем сообщение, добавляя/обновляя информацию об этапе
	message_text = cb.message.text or ""
	
	# Если в сообщении уже есть этап, обновляем его
	if "📍" in message_text:
		lines = message_text.split("\n")
		new_lines = []
		for line in lines:
			if "📍" in line:
				new_lines.append(f"📍 <b>Этап:</b> {stage_name}")
			else:
				new_lines.append(line)
		message_text = "\n".join(new_lines)
	else:
		# Добавляем этап в конец сообщения перед добавлением текста о вводе
		message_text = message_text.rstrip()
		if message_text:
			message_text += f"\n\n📍 <b>Этап:</b> {stage_name}"
	
	await safe_edit_text(
		cb.message,
		message_text + "\n\n📝 Введите ваше сообщение пользователю:",
		parse_mode="HTML",
		reply_markup=cb.message.reply_markup
	)
	await cb.answer()


@admin_router.message(AlertMessageStates.waiting_message)
async def alert_message_send(message: Message, state: FSMContext, bot: Bot):
	"""Отправка сообщения пользователю из раннего алерта"""
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	data = await state.get_data()
	user_tg_id = data.get("alert_user_tg_id")
	if not user_tg_id:
		await message.answer("❌ Ошибка: не найден ID пользователя.")
		await state.clear()
		return
	
	text = message.text or message.caption or ""
	if not text.strip():
		await message.answer("❌ Пожалуйста, введите текст сообщения.")
		return
	
	db = get_db()
	try:
		deal_id = await db.get_active_buy_deal_by_user(user_tg_id)
		if deal_id:
			deal = await db.get_buy_deal_by_id(deal_id)
			if deal:
				await db.add_buy_deal_message(deal_id, "admin", text.strip())
				messages = await db.get_buy_deal_messages(deal_id)
				from app.main import (
					_build_deal_chat_blocks,
					_build_user_deal_chat_prompt_text,
					_notify_user_new_message,
				)
				chat_blocks = _build_deal_chat_blocks(messages, deal.get("user_name", "Пользователь"))
				prompt = "Согласен ❔❔❔:" if deal.get("status") == "await_confirmation" else None
				user_text = _build_user_deal_chat_prompt_text(deal, chat_blocks, prompt)
				reply_markup = None
				from app.keyboards import buy_deal_confirm_kb, buy_deal_paid_kb, buy_deal_user_reply_kb
				if deal.get("status") == "await_confirmation":
					reply_markup = buy_deal_confirm_kb()
				elif deal.get("status") == "await_payment":
					from app.keyboards import buy_deal_paid_reply_kb
					reply_markup = buy_deal_paid_reply_kb(deal_id)
				elif deal.get("status") in ("await_requisites", "await_proof"):
					reply_markup = None
				else:
					reply_markup = buy_deal_user_reply_kb(deal_id)
				try:
					if deal.get("user_message_id"):
						await bot.edit_message_text(
							chat_id=user_tg_id,
							message_id=deal["user_message_id"],
							text=user_text,
							parse_mode="HTML",
							reply_markup=reply_markup
						)
					else:
						sent = await bot.send_message(
							chat_id=user_tg_id,
							text=user_text,
							parse_mode="HTML",
							reply_markup=reply_markup
						)
						await db.update_buy_deal_user_message_id(deal_id, sent.message_id)
				except Exception as e:
					logger.warning(f"⚠️ Не удалось обновить сообщение сделки пользователю: {e}")
				await _notify_user_new_message(bot, user_tg_id)
				from app.main import update_buy_deal_alert
				await update_buy_deal_alert(bot, deal_id)
				await state.clear()
				from app.main import delete_user_message
				await delete_user_message(message)
				return

		# Получаем информацию о пользователе
		user_id = await db.get_user_id_by_tg(user_tg_id)
		user = await db.get_user_by_id(user_id) if user_id else None
		user_name = (user or {}).get("full_name") or "Не указано"
		user_username = (user or {}).get("username") or "Не указано"
		
		# Проверяем, есть ли уже вопрос для этого пользователя
		from app.main import large_order_alerts
		question_id = None
		if user_tg_id in large_order_alerts:
			user_data = large_order_alerts[user_tg_id]
			logger.info(f"🔍 alert_message_send: user_data={user_data}")
			if isinstance(user_data, dict) and "question_id" in user_data:
				question_id = user_data.get("question_id")
				logger.info(f"🔍 alert_message_send: найден существующий question_id={question_id}")
		
		# Если вопроса нет, создаем новый
		if not question_id:
			logger.info(f"🔍 alert_message_send: создаем новый вопрос для user_tg_id={user_tg_id}")
			question_id = await db.create_question(
				user_tg_id=user_tg_id,
				user_name=user_name,
				user_username=user_username,
				question_text="Сообщение администратора",
				initiated_by_admin=1
			)
			# Сохраняем question_id в large_order_alerts
			logger.info(f"🔍 alert_message_send: сохраняем question_id={question_id} для user_tg_id={user_tg_id}")
			if user_tg_id not in large_order_alerts:
				large_order_alerts[user_tg_id] = {"message_ids": {}, "question_id": question_id}
				logger.info(f"✅ alert_message_send: создана новая запись в large_order_alerts: {large_order_alerts[user_tg_id]}")
			else:
				if isinstance(large_order_alerts[user_tg_id], dict):
					if "message_ids" in large_order_alerts[user_tg_id]:
						# Новая структура
						large_order_alerts[user_tg_id]["question_id"] = question_id
						logger.info(f"✅ alert_message_send: обновлен question_id в новой структуре: {large_order_alerts[user_tg_id]}")
					else:
						# Старая структура, конвертируем
						old_data = large_order_alerts[user_tg_id]
						large_order_alerts[user_tg_id] = {"message_ids": old_data, "question_id": question_id}
						logger.info(f"✅ alert_message_send: конвертирована старая структура: {large_order_alerts[user_tg_id]}")
				else:
					# Старая структура (dict, но не с message_ids)
					old_data = large_order_alerts[user_tg_id]
					large_order_alerts[user_tg_id] = {"message_ids": old_data, "question_id": question_id}
					logger.info(f"✅ alert_message_send: конвертирована старая структура (не dict): {large_order_alerts[user_tg_id]}")
		
		# Добавляем сообщение в историю переписки
		await db.add_question_message(question_id, "admin", text)
		
		from app.keyboards import question_user_reply_kb
		
		# Сообщение пользователю с кнопкой "Ответить" (обновляем существующее, если есть)
		messages = await db.get_question_messages(question_id)
		history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				history_lines.append(f"💬 <b>Администратор:</b>\n{msg['message_text']}")
			else:
				history_lines.append(f"👤 <b>Вы:</b>\n{msg['message_text']}")
		history_text = "\n\n".join(history_lines) if history_lines else text
		user_message = "💬 <b>Сообщение администратора</b>\n\n" + history_text

		user_message_id = None
		question = await db.get_question_by_id(question_id)
		if question:
			user_message_id = question.get("user_message_id")

		if user_message_id:
			await bot.edit_message_text(
				chat_id=user_tg_id,
				message_id=user_message_id,
				text=user_message,
				parse_mode="HTML",
				reply_markup=question_user_reply_kb(question_id)
			)
		else:
			user_msg = await bot.send_message(
				chat_id=user_tg_id,
				text=user_message,
				parse_mode="HTML",
				reply_markup=question_user_reply_kb(question_id)
			)
			await db.update_question_user_message_id(question_id, user_msg.message_id)
		
		# Обновляем сообщение о крупной заявке, добавляя переписку
		if user_tg_id in large_order_alerts:
			user_data = large_order_alerts[user_tg_id]
			# Поддерживаем обратную совместимость
			if isinstance(user_data, dict) and "message_ids" in user_data:
				message_ids = user_data["message_ids"]
			else:
				message_ids = user_data
			
			# Получаем историю переписки
			messages = await db.get_question_messages(question_id)
			history_lines = []
			for msg in messages:
				if msg["sender_type"] == "admin":
					history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
				else:
					history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
			history_text = "\n\n".join(history_lines) if history_lines else ""
			
			# Получаем данные о заявке для формирования сообщения
			order_id = await db.get_active_order_by_user(user_tg_id)
			from app.main import get_user_stage_name
			from aiogram.fsm.storage.base import StorageKey
			
			storage = state.storage
			stage_name = "Неизвестно"
			if storage:
				try:
					bot_id = bot.id
					key = StorageKey(
						bot_id=bot_id,
						chat_id=user_tg_id,
						user_id=user_tg_id
					)
					state_data = await storage.get_state(key)
					if state_data:
						stage_name = get_user_stage_name(str(state_data))
				except:
					pass
			
			# Формируем текст сообщения
			if order_id:
				order = await db.get_order_by_id(order_id)
				if order:
					amount_currency = order.get("amount_currency", 0)
					currency_symbol = order.get("currency_symbol", "₽")
					amount = order.get("amount", 0)
					crypto_display = order.get("crypto_display", "")
					amount_str = f"{amount:.8f}".rstrip('0').rstrip('.') if amount < 1 else f"{amount:.2f}".rstrip('0').rstrip('.')
					alert_text = (
						f"🚨 <b>Крупная заявка</b>\n\n"
						f"Пользователь: {user_name} (@{user_username or 'нет'})\n"
						f"Сумма: {int(amount_currency)} {currency_symbol}\n"
						f"Крипта: {crypto_display}\n"
						f"Кол-во: {amount_str} {crypto_display}\n\n"
						f"📍 <b>Этап:</b> {stage_name}"
					)
				else:
					alert_text = (
						f"🚨 <b>Крупная заявка</b>\n\n"
						f"Пользователь: {user_name} (@{user_username or 'нет'})\n\n"
						f"📍 <b>Этап:</b> {stage_name}"
					)
			else:
				alert_text = (
					f"🚨 <b>Крупная заявка</b>\n\n"
					f"Пользователь: {user_name} (@{user_username or 'нет'})\n\n"
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
			
			for admin_id, msg_id in message_ids.items():
				try:
					await bot.edit_message_text(
						chat_id=admin_id,
						message_id=msg_id,
						text=alert_text,
						parse_mode="HTML",
						reply_markup=kb.as_markup()
					)
				except Exception as e:
					logger.warning(f"⚠️ Не удалось обновить сообщение о крупной заявке для админа {admin_id}: {e}")
		
		# Удаляем сообщение админа
		from app.main import delete_user_message
		await delete_user_message(message)
		
		await message.answer("✅ Сообщение отправлено пользователю.")
	except Exception as e:
		logger.error(f"❌ Ошибка отправки сообщения пользователю {user_tg_id}: {e}", exc_info=True)
		await message.answer("❌ Не удалось отправить сообщение пользователю.")
	
	await state.clear()


@admin_router.callback_query(F.data.startswith("alert:requisites:"))
async def alert_requisites_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик выбора реквизитов для крупной заявки"""
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		user_tg_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем активную заявку пользователя
	db = get_db()
	order_id = await db.get_active_order_by_user(user_tg_id)
	if not order_id:
		await cb.answer("Активная заявка не найдена", show_alert=True)
		return
	
	# Получаем информацию о пользователе
	user_id = await db.get_user_id_by_tg(user_tg_id)
	if not user_id:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	
	# Получаем список всех карт
	rows = await db.list_cards()
	cards = [(r[0], r[1]) for r in rows]
	
	if not cards:
		await cb.answer("Нет доступных карт", show_alert=True)
		return
	
	# Сохраняем данные в FSM
	await state.update_data(
		alert_user_tg_id=user_tg_id,
		alert_order_id=order_id
	)
	await state.set_state(AlertRequisitesStates.waiting_card)
	
	# Показываем список карт для выбора
	from app.keyboards import user_cards_reply_kb
	buttons = [(card_id, card_name) for card_id, card_name in cards]
	await safe_edit_text(
		cb.message,
		"💳 Выберите карту с реквизитами для пользователя:",
		reply_markup=user_cards_reply_kb(buttons, user_tg_id, back_to="admin:back")
	)
	await cb.answer()


@admin_router.callback_query(AlertRequisitesStates.waiting_card, F.data.startswith("select:card:"))
async def alert_requisites_select_card(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик выбора карты для реквизитов крупной заявки"""
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		card_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	user_tg_id = data.get("alert_user_tg_id")
	order_id = data.get("alert_order_id")
	
	if not user_tg_id or not order_id:
		await cb.answer("Ошибка: не найдены данные заявки", show_alert=True)
		await state.clear()
		return
	
	# Получаем информацию о карте
	db = get_db()
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	# Привязываем карту к пользователю (если еще не привязана)
	user_id = await db.get_user_id_by_tg(user_tg_id)
	if user_id:
		user_cards = await db.list_cards_for_user(user_id)
		card_ids = [c["card_id"] for c in user_cards]
		if card_id not in card_ids:
			await db.bind_user_to_card(user_id, card_id)
			await db.log_card_delivery_by_tg(
				user_tg_id,
				card_id,
				admin_id=cb.from_user.id if cb.from_user else None
			)
	
	# Обновляем сообщение пользователя с новыми реквизитами
	await _update_user_order_message(bot, order_id, db)
	
	await cb.answer("✅ Реквизиты обновлены")
	await state.clear()
	
	# Возвращаемся к сообщению о крупной заявке
	from app.main import large_order_alerts
	if user_tg_id in large_order_alerts:
		admin_id = cb.from_user.id if cb.from_user else None
		user_data = large_order_alerts[user_tg_id]
		# Поддерживаем обратную совместимость
		if isinstance(user_data, dict) and "message_ids" in user_data:
			message_ids = user_data["message_ids"]
		else:
			message_ids = user_data
		if admin_id and admin_id in message_ids:
			message_id = message_ids[admin_id]
			try:
				order = await db.get_order_by_id(order_id)
				if order:
					from app.main import get_user_stage_name
					from aiogram.fsm.storage.base import StorageKey
					storage = state.storage
					stage_name = "Неизвестно"
					state_data = {}
					if storage:
						try:
							bot_id = bot.id
							key = StorageKey(
								bot_id=bot_id,
								chat_id=user_tg_id,
								user_id=user_tg_id
							)
							state_str = await storage.get_state(key)
							if state_str:
								stage_name = get_user_stage_name(str(state_str))
							state_data = await storage.get_data(key)
						except:
							pass
					
					# Получаем историю переписки, если есть
					question_id = user_data.get("question_id") if isinstance(user_data, dict) else None
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
					
					alert_text = (
						f"🚨 <b>Крупная заявка</b>\n\n"
						f"Пользователь: {order.get('user_name', 'Не указано')} (@{order.get('user_username', 'нет')})\n"
						f"Сумма: {order.get('amount_currency', 0)} {order.get('currency_symbol', '₽')}\n"
						f"Крипта: {order.get('crypto_display', '')}\n"
						f"Кол-во: {order.get('amount', 0)}\n\n"
						f"📍 <b>Этап:</b> {stage_name}"
					)
					
					# Добавляем историю переписки, если есть
					if history_text:
						alert_text += f"\n\n💬 <b>Переписка:</b>\n\n{history_text}"
					
					from aiogram.utils.keyboard import InlineKeyboardBuilder
					kb = InlineKeyboardBuilder()
					kb.button(text="💬 Написать", callback_data=f"alert:message:{user_tg_id}")
					kb.button(text="💳 Реквизиты", callback_data=f"alert:requisites:{user_tg_id}")
					kb.button(text="💰 Сумма", callback_data=f"alert:amount:{user_tg_id}")
					kb.button(text="🪙 Монеты", callback_data=f"alert:crypto:{user_tg_id}")
					kb.adjust(2, 2)
					
					await bot.edit_message_text(
						chat_id=admin_id,
						message_id=message_id,
						text=alert_text,
						parse_mode="HTML",
						reply_markup=kb.as_markup()
					)
			except Exception as e:
				logger.warning(f"⚠️ Не удалось обновить сообщение о крупной заявке: {e}")


@admin_router.callback_query(F.data.startswith("alert:amount:"))
async def alert_amount_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик установки суммы для крупной заявки"""
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		user_tg_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем активную заявку пользователя (может отсутствовать до этапа оплаты)
	db = get_db()
	order_id = await db.get_active_order_by_user(user_tg_id)
	current_amount = 0
	currency_symbol = "₽"
	alert_from_state = False
	if order_id:
		order = await db.get_order_by_id(order_id)
		if not order:
			await cb.answer("Заявка не найдена", show_alert=True)
			return
		current_amount = order.get("amount_currency", 0)
		currency_symbol = order.get("currency_symbol", "₽")
	else:
		from aiogram.fsm.storage.base import StorageKey
		storage = bot.session.storage if hasattr(bot, "session") else None
		if storage:
			try:
				key = StorageKey(bot_id=bot.id, chat_id=user_tg_id, user_id=user_tg_id)
				state_data = await storage.get_data(key)
				current_amount = state_data.get("final_amount", state_data.get("amount_currency", 0))
				currency_symbol = state_data.get("currency_symbol", "₽")
				alert_from_state = True
			except Exception:
				pass
	
	# Сохраняем данные в FSM
	await state.update_data(
		alert_user_tg_id=user_tg_id,
		alert_order_id=order_id,
		current_amount=current_amount,
		currency_symbol=currency_symbol,
		alert_from_state=alert_from_state
	)
	await state.set_state(AlertAmountStates.waiting_amount)
	
	try:
		await safe_edit_text(
			cb.message,
			f"💰 Текущая сумма: {int(current_amount)} {currency_symbol}\n\nВведите новую сумму:",
			reply_markup=cb.message.reply_markup
		)
	except Exception as e:
		logger.warning(f"⚠️ Не удалось отредактировать сообщение для ввода суммы: {e}")
	await cb.answer()


@admin_router.message(AlertAmountStates.waiting_amount)
async def alert_amount_save(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик сохранения суммы для крупной заявки"""
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Проверяем, не является ли это командой
	if message.text and message.text.startswith("/"):
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	order_id = data.get("alert_order_id")
	user_tg_id = data.get("alert_user_tg_id")
	current_amount = data.get("current_amount", 0)
	currency_symbol = data.get("currency_symbol", "₽")
	alert_from_state = data.get("alert_from_state", False)
	
	if not order_id and not user_tg_id:
		await message.answer("❌ Ошибка: не найден ID пользователя")
		await state.clear()
		return
	
	# Валидируем введенную сумму
	try:
		new_amount_str = message.text.strip().replace(",", ".")
		new_amount = float(new_amount_str)
		if new_amount <= 0:
			await message.answer(f"❌ Сумма должна быть больше нуля. Текущая сумма: {int(current_amount)} {currency_symbol}\nВведите новую сумму:")
			return
	except ValueError:
		await message.answer(f"❌ Неверный формат суммы. Текущая сумма: {int(current_amount)} {currency_symbol}\nВведите число (например: 5000):")
		return
	
	db = get_db()
	if order_id:
		# Обновляем сумму в БД
		await db._db.execute(
			"UPDATE orders SET amount_currency = ? WHERE id = ?",
			(new_amount, order_id)
		)
		await db._db.commit()
		
		logger.info(f"✅ Сумма крупной заявки {order_id} обновлена: {int(current_amount)} {currency_symbol} -> {int(new_amount)} {currency_symbol}")
		
		# Обновляем сообщение пользователя с новыми данными
		await _update_user_order_message(bot, order_id, db)
	else:
		logger.info(f"✅ Сумма крупной заявки обновлена через FSM: {int(current_amount)} {currency_symbol} -> {int(new_amount)} {currency_symbol}")
	
	# Обновляем состояние пользователя, чтобы разрешить продолжение
	try:
		from aiogram.fsm.storage.base import StorageKey
		from app.main import BuyStates
		storage = bot.session.storage if hasattr(bot, "session") else None
		if storage and user_tg_id:
			key = StorageKey(bot_id=bot.id, chat_id=user_tg_id, user_id=user_tg_id)
			state_data = await storage.get_data(key)
			state_data["admin_amount_set"] = True
			state_data["admin_amount_value"] = new_amount
			state_data["amount_currency"] = new_amount
			state_data["final_amount"] = new_amount
			await storage.set_data(key, state_data)
			current_state = await storage.get_state(key)
			
			# Если пользователь сейчас на этапах до оплаты, обновим сообщение
			if current_state in (
				BuyStates.waiting_confirmation.state,
				BuyStates.waiting_wallet_address.state,
				BuyStates.waiting_delivery_method.state,
			):
				amount = state_data.get("amount", 0)
				crypto_display = state_data.get("crypto_display", "")
				selected_country = state_data.get("selected_country", "RUB")
				amount_str = f"{amount:.8f}".rstrip("0").rstrip(".") if amount < 1 else f"{amount:.2f}".rstrip("0").rstrip(".")
				payment_text = f"{int(new_amount)} {currency_symbol}"
				
				from app.keyboards import buy_confirmation_kb, buy_delivery_method_kb
				message_id = state_data.get("last_bot_message_id")
				if message_id:
					if current_state == BuyStates.waiting_confirmation.state:
						text = (
							f"Вам будет зачислено: {amount_str} {crypto_display}\n"
							f"Вам необходимо оплатить: {payment_text}"
						)
						await bot.edit_message_text(
							chat_id=user_tg_id,
							message_id=message_id,
							text=text,
							reply_markup=buy_confirmation_kb()
						)
					elif current_state == BuyStates.waiting_wallet_address.state:
						wallet_request = f"Введите адрес кошелька для {crypto_display}:"
						text = (
							f"Вам будет зачислено: {amount_str} {crypto_display}\n"
							f"Вам необходимо оплатить: {payment_text}\n\n"
							f"{wallet_request}"
						)
						await bot.edit_message_text(
							chat_id=user_tg_id,
							message_id=message_id,
							text=text
						)
					elif current_state == BuyStates.waiting_delivery_method.state:
						text = (
							f"Вам будет зачислено: {amount_str} {crypto_display}\n"
							f"Вам необходимо оплатить: {payment_text}\n\n"
							f"Выберите способ доставки:"
						)
						is_byn = selected_country == "BYN"
						await bot.edit_message_text(
							chat_id=user_tg_id,
							message_id=message_id,
							text=text,
							reply_markup=buy_delivery_method_kb(currency_symbol, is_byn)
						)
	except Exception as e:
		logger.warning(f"⚠️ Не удалось обновить состояние/сообщение пользователя: {e}")
	
	# Обновляем сообщение о крупной заявке
	order = await db.get_order_by_id(order_id) if order_id else None
	if order or user_tg_id:
		from app.main import large_order_alerts, get_user_stage_name
		from aiogram.fsm.storage.base import StorageKey
		if user_tg_id in large_order_alerts:
			admin_id = message.from_user.id if message.from_user else None
			user_data = large_order_alerts[user_tg_id]
			# Поддерживаем обратную совместимость
			if isinstance(user_data, dict) and "message_ids" in user_data:
				message_ids = user_data["message_ids"]
				question_id = user_data.get("question_id")
			else:
				message_ids = user_data
				question_id = None
			if admin_id and admin_id in message_ids:
				message_id = message_ids[admin_id]
				try:
					storage = state.storage
					stage_name = "Неизвестно"
					if storage:
						try:
							bot_id = bot.id
							key = StorageKey(
								bot_id=bot_id,
								chat_id=user_tg_id,
								user_id=user_tg_id
							)
							state_data = await storage.get_state(key)
							if state_data:
								stage_name = get_user_stage_name(str(state_data))
						except:
							pass
					
					# Получаем историю переписки, если есть
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
					
					if order:
						user_name = order.get("user_name", "Не указано")
						user_username = order.get("user_username", "нет")
						crypto_display = order.get("crypto_display", "")
						amount = order.get("amount", 0)
					else:
						user_id = await db.get_user_id_by_tg(user_tg_id)
						user = await db.get_user_by_id(user_id) if user_id else None
						user_name = (user or {}).get("full_name", "Не указано")
						user_username = (user or {}).get("username", "нет")
						crypto_display = state_data.get("crypto_display", "")
						amount = state_data.get("amount", 0)
					
					amount_str = f"{amount:.8f}".rstrip("0").rstrip(".") if amount < 1 else f"{amount:.2f}".rstrip("0").rstrip(".")
					alert_text = (
						f"🚨 <b>Крупная заявка</b>\n\n"
						f"Пользователь: {user_name} (@{user_username})\n"
						f"Сумма: {int(new_amount)} {currency_symbol}\n"
						f"Крипта: {crypto_display}\n"
						f"Кол-во: {amount_str} {crypto_display}\n\n"
						f"📍 <b>Этап:</b> {stage_name}"
					)
					
					# Добавляем историю переписки, если есть
					if history_text:
						alert_text += f"\n\n💬 <b>Переписка:</b>\n\n{history_text}"
					
					from aiogram.utils.keyboard import InlineKeyboardBuilder
					kb = InlineKeyboardBuilder()
					kb.button(text="💬 Написать", callback_data=f"alert:message:{user_tg_id}")
					kb.button(text="💳 Реквизиты", callback_data=f"alert:requisites:{user_tg_id}")
					kb.button(text="💰 Сумма", callback_data=f"alert:amount:{user_tg_id}")
					kb.button(text="🪙 Монеты", callback_data=f"alert:crypto:{user_tg_id}")
					kb.adjust(2, 2)
					
					await bot.edit_message_text(
						chat_id=admin_id,
						message_id=message_id,
						text=alert_text,
						parse_mode="HTML",
						reply_markup=kb.as_markup()
					)
				except Exception as e:
					logger.warning(f"⚠️ Не удалось обновить сообщение о крупной заявке: {e}")
	
	# Очищаем состояние
	await state.clear()
	
	# Удаляем сообщение админа
	from app.main import delete_user_message
	await delete_user_message(message)
	
	await message.answer(f"✅ Сумма обновлена: {int(new_amount)} {currency_symbol}")


@admin_router.callback_query(F.data.startswith("alert:crypto:"))
async def alert_crypto_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик установки количества монет для крупной заявки"""
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		user_tg_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем активную заявку пользователя
	db = get_db()
	order_id = await db.get_active_order_by_user(user_tg_id)
	if not order_id:
		await cb.answer("Активная заявка не найдена", show_alert=True)
		return
	
	order = await db.get_order_by_id(order_id)
	if not order:
		await cb.answer("Заявка не найдена", show_alert=True)
		return
	
	# Сохраняем данные в FSM
	current_crypto_amount = order.get("amount", 0)
	crypto_display = order.get("crypto_display", "")
	
	await state.update_data(
		alert_user_tg_id=user_tg_id,
		alert_order_id=order_id,
		current_crypto_amount=current_crypto_amount,
		crypto_display=crypto_display
	)
	await state.set_state(AlertCryptoStates.waiting_crypto)
	
	current_str = f"{current_crypto_amount:.8f}".rstrip('0').rstrip('.') if current_crypto_amount < 1 else f"{current_crypto_amount:.2f}".rstrip('0').rstrip('.')
	
	await safe_edit_text(
		cb.message,
		f"🪙 Текущее количество: {current_str} {crypto_display}\n\nВведите новое количество:",
		reply_markup=cb.message.reply_markup
	)
	await cb.answer()


@admin_router.message(AlertCryptoStates.waiting_crypto)
async def alert_crypto_save(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик сохранения количества монет для крупной заявки"""
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Проверяем, не является ли это командой
	if message.text and message.text.startswith("/"):
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	order_id = data.get("alert_order_id")
	current_crypto_amount = data.get("current_crypto_amount", 0)
	crypto_display = data.get("crypto_display", "")
	
	if not order_id:
		await message.answer("❌ Ошибка: не найден ID заявки")
		await state.clear()
		return
	
	# Валидируем введенное количество
	try:
		new_amount_str = message.text.strip().replace(",", ".")
		new_crypto_amount = float(new_amount_str)
		if new_crypto_amount <= 0:
			current_str = f"{current_crypto_amount:.8f}".rstrip('0').rstrip('.') if current_crypto_amount < 1 else f"{current_crypto_amount:.2f}".rstrip('0').rstrip('.')
			await message.answer(f"❌ Количество должно быть больше нуля. Текущее количество: {current_str} {crypto_display}\nВведите новое количество:")
			return
	except ValueError:
		current_str = f"{current_crypto_amount:.8f}".rstrip('0').rstrip('.') if current_crypto_amount < 1 else f"{current_crypto_amount:.2f}".rstrip('0').rstrip('.')
		await message.answer(f"❌ Неверный формат количества. Текущее количество: {current_str} {crypto_display}\nВведите число (например: 0.008 или 100):")
		return
	
	# Обновляем количество крипты в БД
	db = get_db()
	await db._db.execute(
		"UPDATE orders SET amount = ? WHERE id = ?",
		(new_crypto_amount, order_id)
	)
	await db._db.commit()
	
	current_str = f"{current_crypto_amount:.8f}".rstrip('0').rstrip('.') if current_crypto_amount < 1 else f"{current_crypto_amount:.2f}".rstrip('0').rstrip('.')
	new_str = f"{new_crypto_amount:.8f}".rstrip('0').rstrip('.') if new_crypto_amount < 1 else f"{new_crypto_amount:.2f}".rstrip('0').rstrip('.')
	logger.info(f"✅ Количество крипты крупной заявки {order_id} обновлено: {current_str} {crypto_display} -> {new_str} {crypto_display}")
	
	# Обновляем сообщение пользователя с новыми данными
	await _update_user_order_message(bot, order_id, db)
	
	# Обновляем сообщение о крупной заявке
	order = await db.get_order_by_id(order_id)
	if order:
		user_tg_id = order.get("user_tg_id")
		from app.main import large_order_alerts, get_user_stage_name
		from aiogram.fsm.storage.base import StorageKey
		if user_tg_id in large_order_alerts:
			admin_id = message.from_user.id if message.from_user else None
			user_data = large_order_alerts[user_tg_id]
			# Поддерживаем обратную совместимость
			if isinstance(user_data, dict) and "message_ids" in user_data:
				message_ids = user_data["message_ids"]
			else:
				message_ids = user_data
			if admin_id and admin_id in message_ids:
				message_id = message_ids[admin_id]
				try:
					storage = state.storage
					stage_name = "Неизвестно"
					if storage:
						try:
							bot_id = bot.id
							key = StorageKey(
								bot_id=bot_id,
								chat_id=user_tg_id,
								user_id=user_tg_id
							)
							state_data = await storage.get_state(key)
							if state_data:
								stage_name = get_user_stage_name(str(state_data))
						except:
							pass
					
					# Получаем историю переписки, если есть
					question_id = user_data.get("question_id") if isinstance(user_data, dict) else None
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
					
					alert_text = (
						f"🚨 <b>Крупная заявка</b>\n\n"
						f"Пользователь: {order.get('user_name', 'Не указано')} (@{order.get('user_username', 'нет')})\n"
						f"Сумма: {order.get('amount_currency', 0)} {order.get('currency_symbol', '₽')}\n"
						f"Крипта: {order.get('crypto_display', '')}\n"
						f"Кол-во: {new_str} {crypto_display}\n\n"
						f"📍 <b>Этап:</b> {stage_name}"
					)
					
					# Добавляем историю переписки, если есть
					if history_text:
						alert_text += f"\n\n💬 <b>Переписка:</b>\n\n{history_text}"
					
					from aiogram.utils.keyboard import InlineKeyboardBuilder
					kb = InlineKeyboardBuilder()
					kb.button(text="💬 Написать", callback_data=f"alert:message:{user_tg_id}")
					kb.button(text="💳 Реквизиты", callback_data=f"alert:requisites:{user_tg_id}")
					kb.button(text="💰 Сумма", callback_data=f"alert:amount:{user_tg_id}")
					kb.button(text="🪙 Монеты", callback_data=f"alert:crypto:{user_tg_id}")
					kb.adjust(2, 2)
					
					await bot.edit_message_text(
						chat_id=admin_id,
						message_id=message_id,
						text=alert_text,
						parse_mode="HTML",
						reply_markup=kb.as_markup()
					)
				except Exception as e:
					logger.warning(f"⚠️ Не удалось обновить сообщение о крупной заявке: {e}")
	
	# Очищаем состояние
	await state.clear()
	
	# Удаляем сообщение админа
	from app.main import delete_user_message
	await delete_user_message(message)
	
	await message.answer(f"✅ Количество обновлено: {new_str} {crypto_display}")


@admin_router.callback_query(F.data.startswith("dealalert:message:"))
async def deal_alert_message_start(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	try:
		deal_id = int(cb.data.split(":")[2])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await cb.answer("Сделка не найдена", show_alert=True)
		return
	await state.set_state(DealAlertMessageStates.waiting_message)
	await state.update_data(deal_id=deal_id, user_tg_id=deal["user_tg_id"])
	try:
		prompt = await cb.message.answer("✍️ Введите сообщение для пользователя:")
		await state.update_data(deal_prompt_message_id=prompt.message_id)
	except TelegramNetworkError as e:
		logger.warning(f"⚠️ Сеть недоступна при запросе сообщения админа: {e}. Повторить позже.")
		await state.clear()
		return
	await cb.answer()


@admin_router.message(DealAlertMessageStates.waiting_message)
async def deal_alert_message_send(message: Message, state: FSMContext, bot: Bot):
	# Проверяем админа
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	data = await state.get_data()
	deal_id = data.get("deal_id")
	user_tg_id = data.get("user_tg_id")
	if not deal_id or not user_tg_id:
		await state.clear()
		return
	reply_text = message.text or message.caption or ""
	if not reply_text.strip():
		await message.answer("❌ Пожалуйста, введите текст сообщения.")
		return
	db = get_db()
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await state.clear()
		return
	await db.add_buy_deal_message(deal_id, "admin", reply_text)
	messages = await db.get_buy_deal_messages(deal_id)
	user_name = deal.get("user_name", "Пользователь")
	from app.main import _build_user_deal_admin_message_text, _build_user_deal_chat_text, _build_user_deal_with_requisites_chat_text, _build_deal_chat_lines, _get_deal_requisites_text, update_buy_deal_alert, _notify_user_new_message
	user_text = ""
	has_user_reply = any(msg["sender_type"] == "user" for msg in messages)
	if not has_user_reply and len(messages) == 1:
		user_text = _build_user_deal_admin_message_text(deal, reply_text)
	else:
		chat_lines = _build_deal_chat_lines(messages, user_name)
		requisites_text = await _get_deal_requisites_text(
			db,
			deal.get("user_tg_id"),
			deal.get("country_code")
		)
		if requisites_text:
			user_text = _build_user_deal_with_requisites_chat_text(
				deal=deal,
				requisites_text=requisites_text,
				chat_lines=chat_lines
			)
		else:
			user_text = _build_user_deal_chat_text(deal, chat_lines)
	try:
		reply_markup = buy_deal_user_reply_kb(deal_id)
		if deal.get("status") == "await_payment":
			from app.keyboards import buy_deal_paid_reply_kb
			reply_markup = buy_deal_paid_reply_kb(deal_id)
		if deal.get("user_message_id"):
			await bot.edit_message_text(
				chat_id=user_tg_id,
				message_id=deal["user_message_id"],
				text=user_text,
				parse_mode="HTML",
				reply_markup=reply_markup
			)
		else:
			sent = await bot.send_message(
				chat_id=user_tg_id,
				text=user_text,
				parse_mode="HTML",
				reply_markup=reply_markup
			)
			await db.update_buy_deal_user_message_id(deal_id, sent.message_id)
	except Exception:
		pass
	await _notify_user_new_message(bot, user_tg_id)
	await update_buy_deal_alert(bot, deal_id)
	prompt_id = data.get("deal_prompt_message_id")
	if prompt_id:
		try:
			await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
		except Exception:
			pass
	from app.main import delete_user_message
	await delete_user_message(message)
	await state.clear()


@admin_router.callback_query(F.data.startswith("dealalert:requisites:back:"))
async def deal_alert_requisites_back(cb: CallbackQuery):
	try:
		deal_id = int(cb.data.split(":")[3])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	from app.main import update_buy_deal_alert
	await update_buy_deal_alert(cb.bot, deal_id)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("dealalert:requisites:"))
async def deal_alert_requisites_start(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) != 3:
		return
	try:
		deal_id = int(parts[2])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await cb.answer("Сделка не найдена", show_alert=True)
		return
	groups = await db.list_card_groups()
	await state.set_state(DealAlertRequisitesStates.waiting_card)
	await state.update_data(deal_id=deal_id, user_tg_id=deal["user_tg_id"])
	await safe_edit_text_or_caption(
		cb.message,
		"📁 Выберите группу карт:",
		reply_markup=_deal_groups_kb(groups, deal_id).as_markup()
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("dealalert:group:"))
async def deal_alert_group_select(cb: CallbackQuery):
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) != 4:
		return
	try:
		deal_id = int(parts[2])
		group_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	if group_id == 0:
		cards = await db.get_cards_without_group()
		group_name = "Без группы"
	else:
		cards = await db.get_cards_by_group(group_id)
		group = await db.get_card_group(group_id)
		group_name = group.get("name", "Группа") if group else "Группа"
	cards_list = [(c[0], c[1]) for c in cards]
	if not cards_list:
		await cb.answer("Нет карт в группе", show_alert=True)
		return
	await safe_edit_text_or_caption(
		cb.message,
		f"Карты группы '{group_name}':",
		reply_markup=_deal_cards_kb(cards_list, deal_id, group_id).as_markup()
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("dealalert:card:"))
async def deal_alert_requisites_select(cb: CallbackQuery, state: FSMContext, bot: Bot):
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		deal_id = int(parts[2])
		card_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await cb.answer("Сделка не найдена", show_alert=True)
		return
	user_tg_id = deal["user_tg_id"]
	user_id = await db.get_user_id_by_tg(user_tg_id)
	if not user_id:
		user_id = await db.get_or_create_user(user_tg_id, deal.get("user_username"), deal.get("user_name"))
	await db.bind_user_to_card(user_id, card_id)
	await db.delete_pending_requisites(user_tg_id)
	requisites = await db.list_card_requisites(card_id)
	requisites_list = [req["requisite_text"] for req in requisites]
	user_msg = await db.get_card_user_message(card_id)
	if user_msg and user_msg.strip():
		requisites_list.append(user_msg)
	requisites_text = "\n".join(requisites_list)
	messages = await db.get_buy_deal_messages(deal_id)
	from app.main import _build_deal_chat_lines, _build_user_deal_with_requisites_chat_text
	chat_lines = _build_deal_chat_lines(messages, deal.get("user_name", "Пользователь"))
	user_text = _build_user_deal_with_requisites_chat_text(
		deal=deal,
		requisites_text=requisites_text,
		chat_lines=chat_lines,
		prompt=None
	)
	try:
		if deal.get("user_message_id"):
			await bot.edit_message_text(
				chat_id=user_tg_id,
				message_id=deal["user_message_id"],
				text=user_text,
				parse_mode="HTML",
				reply_markup=buy_deal_paid_reply_kb(deal_id)
			)
		else:
			sent = await bot.send_message(
				chat_id=user_tg_id,
				text=user_text,
				parse_mode="HTML",
				reply_markup=buy_deal_paid_reply_kb(deal_id)
			)
			await db.update_buy_deal_user_message_id(deal_id, sent.message_id)
	except Exception:
		pass
	await db.update_buy_deal_fields(deal_id, status="await_payment")
	from app.main import update_buy_deal_alert
	try:
		# Удаляем старое уведомление о реквизитах, если было
		if deal.get("requisites_notice_message_id"):
			try:
				await bot.delete_message(
					chat_id=user_tg_id,
					message_id=deal["requisites_notice_message_id"]
				)
			except Exception:
				pass
		notice = await bot.send_message(
			chat_id=user_tg_id,
			text="✅ Реквизиты получены"
		)
		await delete_message_after_delay(bot, user_tg_id, notice.message_id, 15.0)
		await db.update_buy_deal_fields(
			deal_id,
			requisites_notice_message_id=notice.message_id
		)
	except Exception:
		pass
	await update_buy_deal_alert(bot, deal_id)
	await cb.answer("Реквизиты отправлены пользователю ✅")


@admin_router.callback_query(F.data.startswith("dealalert:amount:"))
async def deal_alert_amount_start(cb: CallbackQuery, state: FSMContext):
	try:
		deal_id = int(cb.data.split(":")[2])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	await state.set_state(DealAlertAmountStates.waiting_amount)
	await state.update_data(deal_id=deal_id)
	prompt = await cb.message.answer("Введите новую сумму в валюте сделки:")
	await state.update_data(deal_prompt_message_id=prompt.message_id)
	await cb.answer()


@admin_router.message(DealAlertAmountStates.waiting_amount)
async def deal_alert_amount_save(message: Message, state: FSMContext, bot: Bot):
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	data = await state.get_data()
	deal_id = data.get("deal_id")
	if not deal_id:
		await state.clear()
		return
	text = (message.text or "").replace(",", ".").strip()
	try:
		new_amount = float(text)
		if new_amount <= 0:
			raise ValueError
	except ValueError:
		await message.answer("❌ Введите корректную сумму.")
		return
	db = get_db()
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await state.clear()
		return
	await db.update_buy_deal_fields(deal_id, amount_currency=new_amount)
	deal["amount_currency"] = new_amount
	order_id = deal.get("order_id")
	if order_id:
		try:
			await db._db.execute(
				"UPDATE orders SET amount_currency = ? WHERE id = ?",
				(new_amount, order_id)
			)
			await db._db.commit()
		except Exception as e:
			logger.warning(
				f"⚠️ Не удалось синхронизировать сумму по order_id={order_id} для deal_id={deal_id}: {e}"
			)
	else:
		logger.warning(f"⚠️ Синхронизация суммы пропущена: отсутствует order_id для deal_id={deal_id}")
	try:
		from aiogram.fsm.storage.base import StorageKey
		storage = bot.session.storage if hasattr(bot, "session") else None
		if storage:
			key = StorageKey(bot_id=bot.id, chat_id=deal["user_tg_id"], user_id=deal["user_tg_id"])
			current_data = await storage.get_data(key)
			current_data = current_data or {}
			current_data["amount_currency"] = new_amount
			await storage.set_data(key, current_data)
	except Exception:
		pass
	if deal.get("status") == "await_admin" and not deal.get("wallet_address"):
		await db.update_buy_deal_fields(deal_id, status="await_wallet")
		deal["status"] = "await_wallet"
		try:
			from aiogram.fsm.storage.base import StorageKey
			from app.main import DealStates
			storage = bot.session.storage if hasattr(bot, "session") else None
			if storage:
				key = StorageKey(bot_id=bot.id, chat_id=deal["user_tg_id"], user_id=deal["user_tg_id"])
				await storage.set_state(key, DealStates.waiting_wallet_address.state)
				await storage.set_data(
					key,
					{
						"deal_id": deal_id,
						"selected_country": deal.get("country_code", "BYN"),
						"crypto_type": deal.get("crypto_type", ""),
						"crypto_display": deal.get("crypto_display", ""),
						"amount": deal.get("amount", 0),
						"amount_currency": new_amount,
						"currency_symbol": deal.get("currency_symbol", "Br"),
						"deal_message_id": deal.get("user_message_id"),
						"order_message_id": deal.get("user_message_id"),
					}
				)
		except Exception:
			pass
	from app.main import _get_deal_requisites_text
	requisites_text = await _get_deal_requisites_text(
		db,
		deal.get("user_tg_id"),
		deal.get("country_code")
	)
	if deal.get("status") == "await_admin" and deal.get("wallet_address") and requisites_text:
		await db.update_buy_deal_fields(deal_id, status="await_payment")
		deal["status"] = "await_payment"
	from app.main import update_buy_deal_alert
	user_text, reply_markup = await _build_user_deal_text_for_admin_update(db, deal)
	try:
		if deal.get("user_message_id"):
			await bot.edit_message_text(
				chat_id=deal["user_tg_id"],
				message_id=deal["user_message_id"],
				text=user_text,
				parse_mode="HTML",
				reply_markup=reply_markup
			)
	except Exception:
		pass
	await update_buy_deal_alert(bot, deal_id)
	from app.main import delete_user_message
	await delete_user_message(message)
	prompt_id = data.get("deal_prompt_message_id")
	if prompt_id:
		try:
			await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
		except Exception:
			pass
	await state.clear()


@admin_router.callback_query(F.data.startswith("dealalert:crypto:"))
async def deal_alert_crypto_start(cb: CallbackQuery, state: FSMContext):
	try:
		deal_id = int(cb.data.split(":")[2])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	await state.set_state(DealAlertCryptoStates.waiting_crypto)
	await state.update_data(deal_id=deal_id)
	prompt = await cb.message.answer("Введите новое количество монет:")
	await state.update_data(deal_prompt_message_id=prompt.message_id)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("dealalert:debt:"))
async def deal_alert_debt_start(cb: CallbackQuery, state: FSMContext):
	try:
		deal_id = int(cb.data.split(":")[2])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	await state.set_state(DealAlertDebtStates.waiting_amount)
	await state.update_data(deal_id=deal_id)
	prompt = await cb.message.answer("Введите сумму долга в валюте сделки:")
	await state.update_data(deal_prompt_message_id=prompt.message_id)
	await cb.answer()


@admin_router.message(DealAlertCryptoStates.waiting_crypto)
async def deal_alert_crypto_save(message: Message, state: FSMContext, bot: Bot):
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	data = await state.get_data()
	deal_id = data.get("deal_id")
	if not deal_id:
		await state.clear()
		return
	text = (message.text or "").replace(",", ".").strip()
	try:
		new_amount = float(text)
		if new_amount <= 0:
			raise ValueError
	except ValueError:
		await message.answer("❌ Введите корректное количество.")
		return
	db = get_db()
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await state.clear()
		return
	await db.update_buy_deal_fields(deal_id, amount=new_amount)
	deal["amount"] = new_amount
	order_id = deal.get("order_id")
	if order_id:
		try:
			await db._db.execute(
				"UPDATE orders SET amount = ? WHERE id = ?",
				(new_amount, order_id)
			)
			await db._db.commit()
		except Exception as e:
			logger.warning(
				f"⚠️ Не удалось синхронизировать количество по order_id={order_id} для deal_id={deal_id}: {e}"
			)
	else:
		logger.warning(f"⚠️ Синхронизация количества пропущена: отсутствует order_id для deal_id={deal_id}")
	try:
		from aiogram.fsm.storage.base import StorageKey
		storage = bot.session.storage if hasattr(bot, "session") else None
		if storage:
			key = StorageKey(bot_id=bot.id, chat_id=deal["user_tg_id"], user_id=deal["user_tg_id"])
			current_data = await storage.get_data(key)
			current_data = current_data or {}
			current_data["amount"] = new_amount
			await storage.set_data(key, current_data)
	except Exception:
		pass
	from app.main import update_buy_deal_alert
	user_text, reply_markup = await _build_user_deal_text_for_admin_update(db, deal)
	try:
		if deal.get("user_message_id"):
			await bot.edit_message_text(
				chat_id=deal["user_tg_id"],
				message_id=deal["user_message_id"],
				text=user_text,
				parse_mode="HTML",
				reply_markup=reply_markup
			)
	except Exception:
		pass
	await update_buy_deal_alert(bot, deal_id)
	from app.main import delete_user_message
	await delete_user_message(message)
	prompt_id = data.get("deal_prompt_message_id")
	if prompt_id:
		try:
			await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
		except Exception:
			pass
	await state.clear()


@admin_router.message(DealAlertDebtStates.waiting_amount)
async def deal_alert_debt_save(message: Message, state: FSMContext, bot: Bot):
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	data = await state.get_data()
	deal_id = data.get("deal_id")
	if not deal_id:
		await state.clear()
		return
	text = (message.text or "").replace(",", ".").strip()
	try:
		debt_amount = float(text)
		if debt_amount <= 0:
			raise ValueError
	except ValueError:
		await message.answer("❌ Введите корректную сумму долга.")
		return
	db = get_db()
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await state.clear()
		return
	currency_symbol = deal.get("currency_symbol", "Br")
	base_amount_currency = float(deal.get("amount_currency", 0))
	if debt_amount > base_amount_currency:
		await message.answer("❌ Долг не может быть больше суммы сделки.")
		return
	new_amount_currency = base_amount_currency - debt_amount
	await db.add_user_debt(deal["user_tg_id"], debt_amount, currency_symbol)
	await db.update_buy_deal_fields(deal_id, amount_currency=new_amount_currency)
	deal["amount_currency"] = new_amount_currency
	from app.main import update_buy_deal_alert
	user_text, reply_markup = await _build_user_deal_text_for_admin_update(db, deal)
	try:
		if deal.get("user_message_id"):
			await bot.edit_message_text(
				chat_id=deal["user_tg_id"],
				message_id=deal["user_message_id"],
				text=user_text,
				parse_mode="HTML",
				reply_markup=reply_markup
			)
	except Exception:
		pass
	await update_buy_deal_alert(bot, deal_id)
	from app.main import delete_user_message
	await delete_user_message(message)
	prompt_id = data.get("deal_prompt_message_id")
	if prompt_id:
		try:
			await bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
		except Exception:
			pass
	await state.clear()


@admin_router.callback_query(F.data.startswith("dealalert:cancel:"))
async def deal_alert_cancel(cb: CallbackQuery, bot: Bot):
	db = get_db()
	try:
		deal_id = int(cb.data.split(":")[2])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await cb.answer("Сделка не найдена", show_alert=True)
		return
	await db.update_buy_deal_fields(deal_id, status="cancelled")
	# Обновляем сообщение пользователя
	try:
		if deal.get("user_message_id"):
			await bot.edit_message_text(
				chat_id=deal["user_tg_id"],
				message_id=deal["user_message_id"],
				text="❌ Сделка отменена администратором."
			)
	except Exception:
		pass
	# Обновляем алерт админа
	from app.main import buy_deal_alerts
	message_ids = buy_deal_alerts.get(deal_id, {})
	for admin_id, message_id in message_ids.items():
		try:
			try:
				await bot.edit_message_text(
					chat_id=admin_id,
					message_id=message_id,
					text="❌ Сделка отменена администратором.",
					reply_markup=None
				)
			except Exception:
				await bot.delete_message(chat_id=admin_id, message_id=message_id)
				await bot.send_message(
					chat_id=admin_id,
					text="❌ Сделка отменена администратором."
				)
		except Exception:
			pass
	buy_deal_alerts.pop(deal_id, None)
	await cb.answer("Сделка отменена")


@admin_router.callback_query(F.data.startswith("dealalert:complete:"))
async def deal_alert_complete(cb: CallbackQuery, bot: Bot):
	db = get_db()
	try:
		deal_id = int(cb.data.split(":")[2])
	except (ValueError, IndexError):
		await cb.answer("Ошибка данных", show_alert=True)
		return
	deal = await db.get_buy_deal_by_id(deal_id)
	if not deal:
		await cb.answer("Сделка не найдена", show_alert=True)
		return
	await db.update_buy_deal_fields(deal_id, status="completed")
	deal["status"] = "completed"
	from app.main import _build_user_deal_completed_text, _build_order_completion_message
	from app.keyboards import buy_deal_completed_delete_kb
	user_text = _build_user_deal_completed_text(deal)
	reply_markup = buy_deal_completed_delete_kb(deal_id)
	try:
		if deal.get("user_message_id"):
			await bot.edit_message_text(
				chat_id=deal["user_tg_id"],
				message_id=deal["user_message_id"],
				text=user_text,
				parse_mode="HTML",
				reply_markup=reply_markup
			)
	except Exception:
		pass
	from app.main import update_buy_deal_alert
	await update_buy_deal_alert(bot, deal_id)
	profit_line = ""
	try:
		from app.config import get_settings
		from app.google_sheets import write_order_to_google_sheet, read_profit
		settings = get_settings()
		order = None
		order_id = deal.get("order_id")
		if order_id:
			order = await db.get_order_by_id(order_id)
		if not order:
			active_order_id = await db.get_active_order_by_user(deal["user_tg_id"])
			if active_order_id:
				order = await db.get_order_by_id(active_order_id)
				order_id = active_order_id
		if not order:
			user_row = await db.get_user_by_tg(deal["user_tg_id"])
			last_order_id = user_row.get("last_order_id") if user_row else None
			if last_order_id:
				order = await db.get_order_by_id(last_order_id)
				order_id = last_order_id
		profit_value = None
		if order and settings.google_sheet_id and settings.google_credentials_path:
			result = await write_order_to_google_sheet(
				sheet_id=settings.google_sheet_id,
				credentials_path=settings.google_credentials_path,
				order=order,
				db=db,
				sheet_name=settings.google_sheet_name,
				xmr_number=None
			)
			if result.get("success"):
				row_number = result.get("row")
				if row_number:
					profit_column = await db.get_google_sheets_setting("profit_column", "BC")
					profit_value = await read_profit(
						sheet_id=settings.google_sheet_id,
						credentials_path=settings.google_credentials_path,
						row=row_number,
						profit_column=profit_column,
						sheet_name=settings.google_sheet_name
					)
		profit_num = None
		if profit_value is not None:
			try:
				profit_num = float(str(profit_value).replace(",", ".").replace(" ", ""))
			except (ValueError, AttributeError):
				profit_num = None
		if order_id:
			if profit_num is not None:
				await db.complete_order(order_id, profit_num)
			else:
				await db.complete_order(order_id)
			await db.update_user_last_order(order["user_tg_id"], order_id, profit_num)
		if profit_value is not None:
			try:
				profit_formatted = f"{int(round(float(str(profit_value).replace(',', '.').replace(' ', '')))):,}".replace(",", " ")
				profit_line = f"📈 Профит: {profit_formatted} USD"
			except (ValueError, AttributeError):
				profit_line = f"📈 Профит: {profit_value} USD"
	except Exception:
		profit_line = ""
	# Отправляем сообщение со ссылкой на блокчейн и стикером (как раньше)
	try:
		order = None
		order_id = deal.get("order_id")
		if order_id:
			order = await db.get_order_by_id(order_id)
		if not order:
			active_order_id = await db.get_active_order_by_user(deal["user_tg_id"])
			if active_order_id:
				order = await db.get_order_by_id(active_order_id)
		if not order:
			user_row = await db.get_user_by_tg(deal["user_tg_id"])
			last_order_id = user_row.get("last_order_id") if user_row else None
			if last_order_id:
				order = await db.get_order_by_id(last_order_id)
		if order:
			await bot.send_message(
				chat_id=deal["user_tg_id"],
				text=_build_order_completion_message(order)
			)
			await bot.send_sticker(
				chat_id=deal["user_tg_id"],
				sticker="CAACAgIAAxkBAAEVPMRpZ3yqu0lezCX6Gr6tMGiJnBBj7QACYAYAAvoLtgg_BZcxRs21uzgE"
			)
			try:
				from app.keyboards import client_menu_kb
				await bot.send_message(
					chat_id=deal["user_tg_id"],
					text="Выберите действие:",
					reply_markup=client_menu_kb()
				)
			except Exception:
				pass
		else:
			logger.warning(f"⚠️ Не найден order для завершенной сделки deal_id={deal_id}, user_tg_id={deal['user_tg_id']}")
	except Exception:
		pass
	# Убираем кнопки у админа после завершения
	try:
		from app.main import buy_deal_alerts, build_admin_open_deal_text_with_chat
		from app.keyboards import deal_alert_admin_completed_kb
		message_ids = buy_deal_alerts.get(deal_id, {})
		alert_text = await build_admin_open_deal_text_with_chat(db, deal_id)
		if profit_line:
			alert_text = f"{alert_text}\n{profit_line}"
		for admin_id, message_id in message_ids.items():
			try:
				await bot.edit_message_text(
					chat_id=admin_id,
					message_id=message_id,
					text=alert_text,
					parse_mode="HTML",
					reply_markup=deal_alert_admin_completed_kb(deal_id)
				)
			except Exception:
				try:
					await bot.edit_message_caption(
						chat_id=admin_id,
						message_id=message_id,
						caption=alert_text,
						parse_mode="HTML",
						reply_markup=deal_alert_admin_completed_kb(deal_id)
					)
				except Exception:
					pass
	except Exception:
		pass
	await cb.answer("Сделка завершена")


@admin_router.callback_query(F.data == "settings:users")
async def settings_users(cb: CallbackQuery):
	"""Показывает последнюю заявку (последнего написавшего), у кого доступа ещё нет."""
	db = get_db()
	admin_ids = get_admin_ids()
	pending = await db.get_latest_pending_user(exclude_tg_ids=admin_ids)
	if not pending:
		await safe_edit_text(cb.message, "Заявок нет.", reply_markup=simple_back_kb("admin:settings"))
		await cb.answer()
		return

	parts = []
	if pending.get("full_name"):
		parts.append(pending["full_name"])
	if pending.get("username"):
		parts.append(f"@{pending['username']}")
	if pending.get("tg_id"):
		parts.append(f"(tg_id: {pending['tg_id']})")
	label = " ".join(parts) if parts else f"ID {pending.get('user_id')}"

	kb = InlineKeyboardBuilder()
	kb.button(text=f"🆕 {label}", callback_data=f"settings:users:view:{pending['user_id']}")
	kb.button(text="⬅️ Назад", callback_data="admin:settings")
	kb.adjust(1)
	await safe_edit_text(cb.message, "Новая заявка:", reply_markup=kb.as_markup())
	await cb.answer()


def _allow_deny_kb(user_id: int, allowed: bool) -> InlineKeyboardBuilder:
	kb = InlineKeyboardBuilder()
	allow_text = "✅ Разрешить" if allowed else "Разрешить"
	deny_text = "✅ Запретить" if not allowed else "Запретить"
	kb.button(text=allow_text, callback_data=f"settings:users:set:{user_id}:allow")
	kb.button(text=deny_text, callback_data=f"settings:users:set:{user_id}:deny")
	kb.button(text="⬅️ Назад", callback_data="settings:users")
	kb.adjust(2, 1)
	return kb


@admin_router.callback_query(F.data.startswith("settings:users:view:"))
async def settings_users_view(cb: CallbackQuery):
	db = get_db()
	try:
		user_id = int(cb.data.split(":")[-1])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	allowed = await db.is_allowed_user(user.get("tg_id"), user.get("username"))

	parts = []
	if user.get("full_name"):
		parts.append(user["full_name"])
	if user.get("username"):
		parts.append(f"@{user['username']}")
	if user.get("tg_id"):
		parts.append(f"(tg_id: {user['tg_id']})")
	title = " ".join(parts) if parts else f"ID {user_id}"

	text = f"Заявка:\n{title}\n\nСтатус: {'✅ Разрешено' if allowed else '❌ Запрещено'}"
	await safe_edit_text(cb.message, text, reply_markup=_allow_deny_kb(user_id, allowed).as_markup())
	await cb.answer()


@admin_router.callback_query(F.data.startswith("settings:users:set:"))
async def settings_users_set(cb: CallbackQuery, bot: Bot):
	db = get_db()
	parts = cb.data.split(":")
	# Формат: settings:users:set:{user_id}:{allow|deny}
	if len(parts) < 5:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		user_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	action = parts[4]
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	tg_id = user.get("tg_id")
	username = user.get("username")
	if tg_id is None and not username:
		await cb.answer("Нет tg_id/username", show_alert=True)
		return

	was_allowed = await db.is_allowed_user(tg_id, username)
	if action == "allow":
		await db.grant_user_access(tg_id=tg_id, username=username)
		allowed = True
		if not was_allowed and tg_id is not None:
			# Пользователь уже писал боту, значит можно отправить приветствие и меню
			from app.keyboards import client_menu_kb
			try:
				# Выставляем команды пользователю, чтобы появилась кнопка "Меню"
				from aiogram.types import BotCommand, BotCommandScopeChat
				await bot.set_my_commands(
					commands=[
						BotCommand(command="start", description="Меню"),
						BotCommand(command="buy", description="Купить"),
						BotCommand(command="sell", description="Продать"),
						BotCommand(command="question", description="Задать вопрос"),
					],
					scope=BotCommandScopeChat(chat_id=tg_id),
				)
				await bot.send_message(
					chat_id=tg_id,
					text="🔒 Сервис не поддерживает подозрительные или незаконные транзакции.\n"
					     "🔞 Только для пользователей старше 18 лет.\n\n"
					     "✅Выберите нужную функцию в меню ниже, чтобы начать работу.",
					reply_markup=client_menu_kb()
				)
			except Exception as e:
				logger.warning(f"Не удалось отправить сообщение пользователю tg_id={tg_id}: {e}")
		alert = "Разрешено ✅"
	elif action == "deny":
		await db.revoke_user_access(tg_id=tg_id, username=username)
		allowed = False
		alert = "Запрещено ✅"
	else:
		await cb.answer("Неизвестное действие", show_alert=True)
		return

	# Обновляем интерфейс с галочкой
	parts_title = []
	if user.get("full_name"):
		parts_title.append(user["full_name"])
	if user.get("username"):
		parts_title.append(f"@{user['username']}")
	if user.get("tg_id"):
		parts_title.append(f"(tg_id: {user['tg_id']})")
	title = " ".join(parts_title) if parts_title else f"ID {user_id}"
	
	# Проверяем, является ли это уведомлением о запросе доступа
	is_access_request = "Новый запрос на доступ" in (cb.message.text or cb.message.caption or "")
	
	if is_access_request and action == "allow":
		# Для уведомления о запросе доступа используем более понятный текст
		text = (
			f"✅ <b>Доступ разрешен</b>\n\n"
			f"👤 Имя: {user.get('full_name') or 'Не указано'}\n"
			f"📱 Username: @{user.get('username') or 'Не указано'}\n"
			f"🆔 ID: <code>{user.get('tg_id') or 'Не указано'}</code>\n\n"
			f"Пользователю отправлено приветственное сообщение."
		)
		from app.keyboards import user_access_request_kb
		# Обновляем сообщение, добавляя кнопку "Меню пользователя"
		kb = InlineKeyboardBuilder()
		kb.button(text="✅ Разрешено", callback_data=f"settings:users:set:{user_id}:allow")
		kb.button(text="👤 Меню пользователя", callback_data=f"user:view:{user_id}")
		kb.adjust(1, 1)
		await safe_edit_text(cb.message, text, reply_markup=kb.as_markup(), parse_mode="HTML")
	else:
		# Обычное обновление для настроек пользователей
		text = f"Заявка:\n{title}\n\nСтатус: {'✅ Разрешено' if allowed else '❌ Запрещено'}"
		await safe_edit_text(cb.message, text, reply_markup=_allow_deny_kb(user_id, allowed).as_markup())
	
	await cb.answer(alert)


def format_add_data_text(data: dict) -> str:
	"""Форматирует текст с выбранными данными для меню /add, /rate, /move"""
	mode = data.get("mode", "add")
	
	# Выбираем заголовок в зависимости от режима
	if mode == "move":
		text = "📋 Перемещение средств\n\n"
	elif mode == "rate":
		text = "📋 Расход\n\n"
	else:
		text = "📋 Добавление операции\n\n"
	
	# Показываем все сохраненные блоки данных
	selected_items = []
	
	# Показываем сохраненные блоки
	saved_blocks = data.get("saved_blocks", [])
	for block_idx, block in enumerate(saved_blocks, 1):
		block_lines = []
		block_crypto = block.get("crypto_data")
		if block_crypto:
			currency = block_crypto.get("currency", "")
			usd_amount = block_crypto.get("usd_amount", 0)
			xmr_number = block_crypto.get("xmr_number")
			if xmr_number:
				block_lines.append(f"🪙 XMR-{xmr_number}: ${int(usd_amount)},")
			else:
				block_lines.append(f"🪙 {currency}: ${int(usd_amount)},")
		
		block_card = block.get("card_data")
		block_card_cash = block.get("card_cash_data")
		if block_card:
			card_name = block_card.get("card_name", "")
			group_name = block_card.get("group_name")
			# Формируем строку с именем группы, если есть
			if group_name:
				card_display = f"💳 ({group_name}){card_name}"
			else:
				card_display = f"💳{card_name}"
			
			if block_card_cash:
				amount = block_card_cash.get("value", 0)
				block_lines.append(f"{card_display}: {amount} р.")
			else:
				block_lines.append(card_display)
		
		block_cash = block.get("cash_data")
		if block_cash:
			amount = block_cash.get("value", 0)
			cash_name = block_cash.get("cash_name", "Наличные")
			currency = block_cash.get("currency", "")
			# Убираем дублирующийся эмодзи 💵, так как cash_name уже содержит эмодзи
			# Если cash_name начинается с эмодзи, используем его, иначе добавляем 💵
			if cash_name and cash_name[0] in ["💵", "💴", "💶", "💷", "💰", "🐿", "💸"]:
				# Добавляем валюту, если она есть
				if currency:
					block_lines.append(f"{cash_name}: {amount} {currency}")
				else:
					block_lines.append(f"{cash_name}: {amount}")
			else:
				# Добавляем валюту, если она есть
				if currency:
					block_lines.append(f"💵 {cash_name}: {amount} {currency}")
				else:
					block_lines.append(f"💵 {cash_name}: {amount}")
		
		if block_lines:
			selected_items.append(f"{block_idx}:\n" + "\n".join(block_lines))
	
	# Показываем текущий блок (если есть)
	current_block_lines = []
	crypto_data = data.get("crypto_data")
	if crypto_data:
		currency = crypto_data.get("currency", "")
		usd_amount = crypto_data.get("usd_amount", 0)
		xmr_number = crypto_data.get("xmr_number")
		if xmr_number:
			current_block_lines.append(f"🪙 XMR-{xmr_number}: ${int(usd_amount)},")
		else:
			current_block_lines.append(f"🪙 {currency}: ${int(usd_amount)},")
	
	card_data = data.get("card_data")
	cash_data = data.get("cash_data")
	card_cash_data = data.get("card_cash_data")  # Наличные для карты
	
	# Обрабатываем карту
	if card_data:
		card_name = card_data.get("card_name", "")
		group_name = card_data.get("group_name")
		# Формируем строку с именем группы, если есть
		if group_name:
			card_display = f"💳 ({group_name}){card_name}"
		else:
			card_display = f"💳{card_name}"
		
		if card_cash_data:
			# Карта с наличными
			amount = card_cash_data.get("value", 0)
			current_block_lines.append(f"{card_display}: {amount} р.")
		else:
			# Только карта без наличных
			current_block_lines.append(card_display)
	
	# Обрабатываем наличные без карты
	if cash_data:
		amount = cash_data.get("value", 0)
		cash_name = cash_data.get("cash_name", "Наличные")
		currency = cash_data.get("currency", "")
		# Убираем дублирующийся эмодзи 💵, так как cash_name уже содержит эмодзи
		# Если cash_name начинается с эмодзи, используем его, иначе добавляем 💵
		if cash_name and cash_name[0] in ["💵", "💴", "💶", "💷", "💰", "🐿", "💸"]:
			# Добавляем валюту, если она есть
			if currency:
				current_block_lines.append(f"{cash_name}: {amount} {currency}")
			else:
				current_block_lines.append(f"{cash_name}: {amount}")
		else:
			# Добавляем валюту, если она есть
			if currency:
				current_block_lines.append(f"💵 {cash_name}: {amount} {currency}")
			else:
				current_block_lines.append(f"💵 {cash_name}: {amount}")
	
	if current_block_lines:
		current_block_num = len(saved_blocks) + 1
		selected_items.append(f"{current_block_num}:\n" + "\n".join(current_block_lines))
	
	if selected_items:
		text += "Выбранные данные:\n" + "\n".join(selected_items) + "\n\n"
	
	# Показываем примечание, если оно есть (только для режима rate)
	if mode == "rate":
		note = data.get("note")
		if note and note.strip():
			text += f"📝 Примечание: {note}\n\n"
	
	text += "Выберите тип данных для добавления:"
	return text


@admin_router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
	"""Команда для вызова меню добавления данных в таблицу (режим add)"""
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Очищаем предыдущее состояние перед началом новой операции
	await state.clear()
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
	
	data = await state.get_data()
	text = format_add_data_text(data)
	kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode="add", data=data)
	await message.answer(text, reply_markup=kb)


@admin_router.message(Command("rate"))
async def cmd_rate(message: Message, state: FSMContext):
	"""Команда для вызова меню добавления данных в таблицу (режим rate)"""
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Очищаем предыдущее состояние перед началом новой операции
	await state.clear()
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
	
	data = await state.get_data()
	text = format_add_data_text(data)
	kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode="rate", data=data)
	await message.answer(text, reply_markup=kb)


@admin_router.message(Command("move"))
async def cmd_move(message: Message, state: FSMContext):
	"""Команда для вызова меню перемещения средств (режим move)"""
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	await state.set_state(AddDataStates.selecting_type)
	await state.update_data(
		mode="move",
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
	
	data = await state.get_data()
	text = format_add_data_text(data)
	kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode="move", data=data)
	await message.answer(text, reply_markup=kb)


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
			display_name = cash.get("display_name", "")
			currency = cash.get("currency", "RUB")
			display = display_name if display_name else cash_name
			text += f"{display} → {column} ({currency})\n"
	
	from app.keyboards import cash_list_kb
	await cb.message.edit_text(text, reply_markup=cash_list_kb(cash_columns))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:type:"))
async def add_data_select_type(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора типа данных в командах /add, /rate и /move"""
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
		await safe_edit_text(
			cb.message,
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
		recent_cards_raw = await db.get_recent_cards_by_admin(admin_id, limit=4)
		# Преобразуем в старый формат для совместимости с card_groups_select_kb
		recent_cards = []
		recent_cards_groups = {}
		for card_id, card_name in recent_cards_raw:
			recent_cards.append((card_id, card_name))
			# Получаем информацию о группе карты
			card_info = await db.get_card_by_id(card_id)
			if card_info and card_info.get("group_id"):
				group = await db.get_card_group_by_id(card_info["group_id"])
				if group:
					recent_cards_groups[card_id] = group["name"]
		
		from app.keyboards import card_groups_select_kb
		await state.set_state(AddDataStates.selecting_card)
		text = "💳 Выберите группу карт:" if groups else "💳 Групп пока нет. Выберите карты без группы:"
		await cb.message.edit_text(text, reply_markup=card_groups_select_kb(groups, back_to=f"add_data:back:{mode}", recent_cards=recent_cards, recent_cards_groups=recent_cards_groups))
		await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:back:") & ~F.data.contains(":group:"))
async def add_data_back(cb: CallbackQuery, state: FSMContext):
	"""Возврат к меню выбора типа данных"""
	parts = cb.data.split(":")
	mode = parts[2]
	
	data = await state.get_data()
	editing_block_idx = data.get("editing_block_idx")
	
	# Если редактировался сохраненный блок, но пользователь нажал "Назад" без сохранения изменений,
	# нужно очистить текущий блок и сбросить editing_block_idx, чтобы не создавать дубликат
	if editing_block_idx is not None:
		# Очищаем текущий блок, так как изменения не были сохранены
		await state.update_data(
			crypto_data=None,
			cash_data=None,
			card_data=None,
			card_cash_data=None,
			xmr_number=None,
			crypto_currency=None,
			cash_name=None,
			editing_block_idx=None
		)
	
	await state.set_state(AddDataStates.selecting_type)
	data = await state.get_data()
	text = format_add_data_text(data)
	kb = await get_add_data_type_kb_with_recent(cb.from_user.id, mode=mode, data=data)
	await cb.message.edit_text(text, reply_markup=kb)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:quick:"))
async def add_data_quick_select(cb: CallbackQuery, state: FSMContext):
	"""Обработчик быстрого выбора криптовалюты, карты или наличных"""
	# Формат: add_data:quick:{type}:{id}:{mode}
	# type: crypto, card, cash
	# id: crypto_id (например, 'BTC', 'XMR-1'), card_id, или currency ('BYN', 'USD')
	parts = cb.data.split(":")
	item_type = parts[2]  # crypto, card, cash
	item_id = parts[3]
	mode = parts[4]
	
	db = get_db()
	admin_id = cb.from_user.id
	
	# Логируем использование
	# Для карт используем формат 'card_id_{card_id}', для остальных - как есть
	if item_type == "card":
		log_item_id = f"card_id_{item_id}"
	else:
		log_item_id = item_id
	# Логируем использование элемента
	if item_type == "card" and item_id.isdigit():
		# log_card_selection уже логирует в item_usage_log
		await db.log_card_selection(int(item_id), admin_id)
	elif item_type == "crypto":
		await db.log_item_usage(admin_id, "crypto", item_id)
	elif item_type == "cash":
		await db.log_item_usage(admin_id, "cash", item_id)
	
	if item_type == "crypto":
		# Быстрый выбор криптовалюты
		crypto_id = item_id
		
		# Определяем, это XMR с номером или обычная крипта
		if crypto_id.startswith("XMR-"):
			# XMR с номером кошелька
			xmr_number = int(crypto_id.split("-")[1])
			await state.update_data(
				crypto_currency="XMR",
				xmr_number=xmr_number,
				editing_block_idx=None
			)
		else:
			# Обычная криптовалюта
			await state.update_data(
				crypto_currency=crypto_id,
				xmr_number=None,
				editing_block_idx=None
			)
		
		# Запрашиваем сумму
		await state.set_state(AddDataStates.entering_crypto)
		await cb.message.edit_text(
			f"💰 Введите сумму в USD для {crypto_id}:",
			reply_markup=None
		)
		await cb.answer()
		
	elif item_type == "card":
		# Быстрый выбор карты
		card_id = int(item_id)
		card = await db.get_card_by_id(card_id)
		
		if not card:
			await cb.answer("Карта не найдена", show_alert=True)
			return
		
		# Получаем адрес столбца для карты
		column = await db.get_card_column(card_id)
		
		# Получаем имя группы, если есть group_id
		group_name = None
		if card.get("group_id"):
			group = await db.get_card_group_by_id(card["group_id"])
			if group:
				group_name = group["name"]
		
		card_data = {
			"card_id": card_id,
			"card_name": card.get("name", ""),
			"user_name": None,
			"column": column,
			"group_id": card.get("group_id"),
			"group_name": group_name
		}
		
		await state.update_data(
			card_data=card_data,
			editing_block_idx=None
		)
		
		# Запрашиваем сумму для карты
		await state.set_state(AddDataStates.entering_card_cash)
		await cb.message.edit_text(
			f"💰 Введите сумму в рублях для карты {card_data['card_name']}:",
			reply_markup=None
		)
		await cb.answer()
		
	elif item_type == "cash":
		# Быстрый выбор наличных
		cash_name = item_id  # Название наличных из БД
		
		# Проверяем, что наличные существуют в БД
		cash_info = await db.get_cash_column(cash_name)
		if not cash_info:
			await cb.answer(f"Наличные {cash_name} не найдены в базе", show_alert=True)
			return
		
		currency = cash_info.get("currency", "RUB")
		display_name = cash_info.get("display_name", "") or cash_name
		
		await state.update_data(
			cash_name=cash_name,
			editing_block_idx=None
		)
		
		# Запрашиваем сумму
		await state.set_state(AddDataStates.entering_cash)
		await cb.message.edit_text(
			f"💰 Введите сумму в {currency} для {display_name}:",
			reply_markup=None
		)
		await cb.answer()


@admin_router.callback_query(F.data.startswith("add_data:back:") & F.data.contains(":group:"))
async def add_data_select_group(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора группы карт в командах /add, /rate и /move"""
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
		# Создаем словарь с группами для всех карт (все в одной группе)
		card_groups = {c[0]: group_name for c in cards}
	else:
		cards = await db.get_cards_without_group()
		text = "💳 Карты вне групп:"
		# Для карт без группы не добавляем информацию о группе
		card_groups = {}
	
	if not cards:
		await cb.answer("В этой группе нет карт", show_alert=True)
		return
	
	cards_list = [(c[0], c[1]) for c in cards]
	from app.keyboards import cards_list_kb
	await state.set_state(AddDataStates.selecting_card)
	await cb.message.edit_text(text, reply_markup=cards_list_kb(cards_list, with_add=False, back_to=f"add_data:back:{mode}", card_groups=card_groups))
	await cb.answer()


@admin_router.callback_query(F.data.startswith("crypto:select:"))
async def add_data_select_crypto(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора криптовалюты в командах /add, /rate и /move"""
	currency = cb.data.split(":")[-1]
	data = await state.get_data()
	mode = data.get("mode", "add")
	
	# Логируем выбор криптовалюты
	db = get_db()
	admin_id = cb.from_user.id
	
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
		await db.log_item_usage(admin_id, "crypto", currency)
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
	
	# Логируем выбор XMR с номером
	db = get_db()
	admin_id = cb.from_user.id
	await db.log_item_usage(admin_id, "crypto", f"XMR-{xmr_number}")
	
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
	# Пропускаем команды - они должны обрабатываться отдельными обработчиками
	if message.text and message.text.startswith("/"):
		return
	
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
		# Логируем использование криптовалюты
		db = get_db()
		crypto_id = f"XMR-{xmr_number}" if xmr_number else currency
		await db.log_item_usage(message.from_user.id, "crypto", crypto_id)
		
		data = await state.get_data()
		text = format_add_data_text(data)
		kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode=mode, data=data)
		await message.answer(text, reply_markup=kb)
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
	
	# Получаем информацию о валюте из БД
	db = get_db()
	cash_info = await db.get_cash_column(cash_name)
	display_name = cash_info.get("display_name", "") if cash_info else ""
	display = display_name if display_name else cash_name
	currency = cash_info.get("currency", "RUB") if cash_info else "RUB"
	
	# Логирование использования наличных не требуется
	
	# Логируем использование наличных
	admin_id = cb.from_user.id
	await db.log_item_usage(admin_id, "cash", cash_name)
	
	# Сохраняем название наличных
	await state.update_data(cash_name=cash_name)
	await state.set_state(AddDataStates.entering_cash)
	
	await cb.message.edit_text(
		f"💵 Введите сумму наличных для '{display}' (число):",
		reply_markup=simple_back_kb(f"add_data:back:{mode}")
	)
	await cb.answer()


@admin_router.message(AddDataStates.entering_card_cash)
async def add_data_enter_card_cash(message: Message, state: FSMContext):
	"""Обработчик ввода суммы наличных для карты"""
	# Пропускаем команды - они должны обрабатываться отдельными обработчиками
	if message.text and message.text.startswith("/"):
		return
	
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
		# Логируем использование карты
		db = get_db()
		card_data = data.get("card_data")
		if card_data:
			card_id = card_data.get("card_id")
			if card_id:
				await db.log_card_selection(card_id, message.from_user.id)
		
		data = await state.get_data()
		text = format_add_data_text(data)
		kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode=mode, data=data)
		await message.answer(text, reply_markup=kb)
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число, например: 200 или -200")
	except Exception as e:
		logger.exception(f"Ошибка обработки наличных для карты: {e}")
		await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")


@admin_router.message(AddDataStates.entering_cash)
async def add_data_enter_cash(message: Message, state: FSMContext):
	"""Обработчик ввода суммы наличных (без карты)"""
	# Пропускаем команды - они должны обрабатываться отдельными обработчиками
	if message.text and message.text.startswith("/"):
		return
	
	try:
		amount = int(float(message.text.replace(",", ".")))
		
		data = await state.get_data()
		cash_name = data.get("cash_name", "Наличные")
		editing_block_idx = data.get("editing_block_idx")
		
		# Получаем валюту из БД
		db = get_db()
		cash_info = await db.get_cash_column(cash_name)
		currency = cash_info.get("currency", "RUB") if cash_info else "RUB"
		
		cash_data = {
			"currency": currency,
			"value": amount,
			"display": f"{amount} {currency}",
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
		# Логируем использование наличных
		db = get_db()
		cash_data = data.get("cash_data")
		if cash_data:
			cash_name = cash_data.get("cash_name")
			if cash_name:
				await db.log_item_usage(message.from_user.id, "cash", cash_name)
		
		data = await state.get_data()
		text = format_add_data_text(data)
		kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode=mode, data=data)
		await message.answer(text, reply_markup=kb)
	except ValueError:
		await message.answer("❌ Неверный формат. Введите число, например: 5000 или -5000")
	except Exception as e:
		logger.exception(f"Ошибка обработки наличных: {e}")
		await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")


@admin_router.message(AddDataStates.selecting_type, ~F.text.startswith("/"), ~(F.forward_origin | F.forward_from))
async def add_data_selecting_type_message(message: Message, state: FSMContext):
	"""Обработчик текстовых сообщений в состоянии selecting_type - игнорируем, показываем подсказку"""
	# Пропускаем команды - они должны обрабатываться отдельными обработчиками
	if message.text and message.text.startswith("/"):
		return
	
	data = await state.get_data()
	mode = data.get("mode", "add")
	text = format_add_data_text(data)
	kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode=mode, data=data)
	await message.answer(text, reply_markup=kb)


@admin_router.callback_query(AddDataStates.selecting_card, F.data.startswith("card:view:"))
async def add_data_select_card(cb: CallbackQuery, state: FSMContext):
	"""Обработчик выбора карты в командах /add, /rate и /move"""
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
	
	# Получаем имя группы, если есть group_id
	group_name = None
	if card.get("group_id"):
		group = await db.get_card_group_by_id(card["group_id"])
		if group:
			group_name = group["name"]
	
	card_data = {
		"card_id": card_id,
		"card_name": card.get("name", ""),
		"user_name": None,
		"column": column,
		"group_id": card.get("group_id"),
		"group_name": group_name
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
	data = await state.get_data()
	text = format_add_data_text(data)
	kb = await get_add_data_type_kb_with_recent(cb.from_user.id, mode=mode, data=data)
	try:
		await cb.message.edit_text(text, reply_markup=kb)
	except Exception as e:
		# Игнорируем ошибку, если сообщение не изменилось
		if "message is not modified" not in str(e):
			raise
	await cb.answer("✅ Блок данных сохранен. Добавьте новый блок.")


@admin_router.callback_query(F.data.startswith("add_data:note:"))
async def add_data_note(cb: CallbackQuery, state: FSMContext):
	"""Обработчик кнопки 'Примечание' для /rate"""
	mode = cb.data.split(":")[-1]
	
	if mode != "rate":
		await cb.answer("⚠️ Примечание доступно только для операции /rate", show_alert=True)
		return
	
	# Переходим в состояние ввода примечания
	await state.set_state(AddDataStates.entering_note)
	from app.keyboards import simple_back_kb
	await cb.message.edit_text("📝 Введите примечание:", reply_markup=simple_back_kb(f"add_data:back:{mode}"))
	await cb.answer()


@admin_router.message(AddDataStates.entering_note, ~F.text.startswith("/"))
async def add_data_note_entered(message: Message, state: FSMContext):
	"""Обработчик ввода примечания для /rate"""
	note_text = message.text.strip()
	
	# Сохраняем примечание в state
	await state.update_data(note=note_text)
	await state.set_state(AddDataStates.selecting_type)
	
	# Получаем текущие данные для отображения
	data = await state.get_data()
	mode = data.get("mode", "add")
	
	# Формируем текст (примечание уже будет включено в format_add_data_text)
	text = format_add_data_text(data)
	
	# Обновляем сообщение с клавиатурой
	kb = await get_add_data_type_kb_with_recent(message.from_user.id, mode=mode, data=data)
	await message.answer(text, reply_markup=kb)
	
	# Удаляем сообщение с вводом примечания
	try:
		await message.delete()
	except Exception:
		pass


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
	
	# В режиме rate суммируем значения для одинаковых карт
	if mode == "rate" and card_cash_pairs:
		# Группируем пары по карте (card_id или card_name + user_name)
		card_sums = {}  # {(card_id или f"{card_name}_{user_name}"): {"card": {...}, "cash": {"value": сумма, "currency": ...}}}
		
		for pair in card_cash_pairs:
			card_data = pair.get("card", {})
			cash_data = pair.get("cash")
			
			# Создаем уникальный ключ для карты
			card_id = card_data.get("card_id")
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			
			if card_id:
				key = f"card_id_{card_id}"
			else:
				key = f"{card_name}_{user_name}"
			
			if key not in card_sums:
				# Первая запись для этой карты
				card_sums[key] = {
					"card": card_data.copy(),
					"cash": cash_data.copy() if cash_data else None
				}
			else:
				# Суммируем значения
				if cash_data and card_sums[key]["cash"]:
					# Суммируем значения, если валюта совпадает
					existing_currency = card_sums[key]["cash"].get("currency", "RUB")
					new_currency = cash_data.get("currency", "RUB")
					
					if existing_currency == new_currency:
						existing_value = card_sums[key]["cash"].get("value", 0)
						new_value = cash_data.get("value", 0)
						card_sums[key]["cash"]["value"] = existing_value + new_value
						logger.info(f"🔍 Суммирование для карты {key}: {existing_value} + {new_value} = {card_sums[key]['cash']['value']}")
					else:
						# Если валюта не совпадает, оставляем как есть (не суммируем)
						logger.warning(f"⚠️ Разные валюты для одной карты {key}: {existing_currency} и {new_currency}")
				elif cash_data and not card_sums[key]["cash"]:
					# Если раньше не было cash, добавляем
					card_sums[key]["cash"] = cash_data.copy()
				# Если cash_data нет, ничего не делаем
		
		# Преобразуем обратно в список
		card_cash_pairs = list(card_sums.values())
		logger.info(f"🔍 После суммирования card_cash_pairs: {len(card_cash_pairs)} записей")
	
	# В режиме rate суммируем значения для одинаковых криптовалют
	if mode == "rate" and crypto_list:
		crypto_sums = {}  # {currency: usd_amount}
		
		for crypto in crypto_list:
			currency = crypto.get("currency")
			usd_amount = crypto.get("usd_amount", 0)
			
			if currency:
				if currency in crypto_sums:
					crypto_sums[currency] += usd_amount
					logger.info(f"🔍 Суммирование криптовалюты {currency}: {crypto_sums[currency] - usd_amount} + {usd_amount} = {crypto_sums[currency]}")
				else:
					crypto_sums[currency] = usd_amount
		
		# Преобразуем обратно в список
		crypto_list = [{"currency": currency, "usd_amount": amount} for currency, amount in crypto_sums.items()]
		logger.info(f"🔍 После суммирования crypto_list: {len(crypto_list)} записей")
	
	# В режиме rate суммируем значения для одинаковых наличных без карты
	if mode == "rate" and cash_list:
		cash_sums = {}  # {cash_name: {"value": сумма, "currency": валюта}}
		
		for cash in cash_list:
			cash_name = cash.get("cash_name", "")
			currency = cash.get("currency", "RUB")
			value = cash.get("value", 0)
			
			if cash_name:
				key = f"{cash_name}_{currency}"
				if key in cash_sums:
					cash_sums[key]["value"] += value
					logger.info(f"🔍 Суммирование наличных {cash_name} ({currency}): {cash_sums[key]['value'] - value} + {value} = {cash_sums[key]['value']}")
				else:
					cash_sums[key] = {
						"cash_name": cash_name,
						"currency": currency,
						"value": value
					}
		
		# Преобразуем обратно в список
		cash_list = list(cash_sums.values())
		logger.info(f"🔍 После суммирования cash_list: {len(cash_list)} записей")
	
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
			# Получаем примечание из state (если было введено)
			note = data.get("note", None)
			if note:
				note = note.strip() if note.strip() else None
			
			result = await write_to_google_sheet_rate_mode(
				settings.google_sheet_id,
				settings.google_credentials_path,
				crypto_list,
				xmr_list,
				cash_list,
				card_cash_pairs,
				settings.google_sheet_name,
				note=note,
				bot=bot,
				chat_id=cb.message.chat.id
			)
		elif mode == "move":
			# Для режима move получаем настройки из БД
			db = get_db()
			move_start_row_str = await db.get_google_sheets_setting("move_start_row", "375")
			move_max_row_str = await db.get_google_sheets_setting("move_max_row", "406")
			move_start_row = int(move_start_row_str) if move_start_row_str else 375
			move_max_row = int(move_max_row_str) if move_max_row_str else 406
			
			result = await write_all_to_google_sheet_one_row(
				settings.google_sheet_id,
				settings.google_credentials_path,
				crypto_list,
				xmr_list,
				cash_list,
				card_cash_pairs,
				mode=mode,
				sheet_name=settings.google_sheet_name,
				bot=bot,
				chat_id=cb.message.chat.id
			)
		else:
			# Для режима add определяем диапазон строк по дню недели из БД
			current_date = datetime.now()
			weekday = current_date.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
			
			# Названия дней недели
			day_names = {
				0: "Понедельник",
				1: "Вторник",
				2: "Среда",
				3: "Четверг",
				4: "Пятница",
				5: "Суббота",
				6: "Воскресенье"
			}
			
			# Ключи настроек для каждого дня недели
			day_setting_keys = {
				0: ("add_monday_start", "add_monday_max"),    # Понедельник
				1: ("add_tuesday_start", "add_tuesday_max"),  # Вторник
				2: ("add_wednesday_start", "add_wednesday_max"), # Среда
				3: ("add_thursday_start", "add_thursday_max"), # Четверг
				4: ("add_friday_start", "add_friday_max"),    # Пятница
				5: ("add_saturday_start", "add_saturday_max"), # Суббота
				6: ("add_sunday_start", "add_sunday_max")     # Воскресенье
			}
			
			# Значения по умолчанию (на случай, если настройки не найдены)
			default_ranges = {
				0: (5, 54),    # Понедельник
				1: (55, 104),  # Вторник
				2: (105, 154), # Среда
				3: (155, 204), # Четверг
				4: (205, 254), # Пятница
				5: (255, 304), # Суббота
				6: (305, 364)  # Воскресенье
			}
			
			# Получаем настройки из БД
			db = get_db()
			start_key, max_key = day_setting_keys.get(weekday, ("add_monday_start", "add_monday_max"))
			default_start, default_max = default_ranges.get(weekday, (5, 54))
			
			start_row_str = await db.get_google_sheets_setting(start_key, str(default_start))
			max_row_str = await db.get_google_sheets_setting(max_key, str(default_max))
			
			try:
				add_start_row = int(start_row_str) if start_row_str else default_start
				add_max_row = int(max_row_str) if max_row_str else default_max
			except (ValueError, TypeError):
				add_start_row, add_max_row = default_start, default_max
				logger.warning(f"Неверные значения для дня недели {weekday}, используем значения по умолчанию")
			
			day_name = day_names.get(weekday, "Понедельник")
			
			logger.info(f"📍 Режим /add: {day_name}, диапазон строк {add_start_row}-{add_max_row}")
			
			result = await write_all_to_google_sheet_one_row(
				settings.google_sheet_id,
				settings.google_credentials_path,
				crypto_list,
				xmr_list,
				cash_list,
				card_cash_pairs,
				mode="add",
				sheet_name=settings.google_sheet_name,
				bot=bot,
				chat_id=cb.message.chat.id
			)
			
			# Проверяем, есть ли свободная строка в диапазоне
			if not result.get("success"):
				error_message = result.get("message", "Неизвестная ошибка")
				if "Нет свободных строк" in error_message or "свободных строк" in error_message.lower():
					# Нет места в диапазоне для текущего дня недели
					try:
						await cb.message.edit_text(
							f"⚠️ Нет свободных строк в диапазоне для {day_name} (строки {add_start_row}-{add_max_row}).\n\n"
							f"Пожалуйста, освободите место в таблице или попробуйте позже.",
							reply_markup=admin_menu_kb()
						)
					except Exception:
						# Если не удалось отредактировать сообщение, отправляем новое
						await cb.message.answer(
							f"⚠️ Нет свободных строк в диапазоне для {day_name} (строки {add_start_row}-{add_max_row}).\n\n"
							f"Пожалуйста, освободите место в таблице или попробуйте позже.",
							reply_markup=admin_menu_kb()
						)
					await state.clear()
					try:
						await cb.answer()
					except Exception:
						pass
					return
		
		if result.get("success"):
			# Сохраняем пополнения карт в БД (только для mode == "add" и только положительные суммы)
			db = get_db()
			if mode == "add":
				for pair in card_cash_pairs:
					card_data = pair.get("card")
					cash_data = pair.get("cash")
					if card_data and cash_data:
						card_id = card_data.get("card_id")
						cash_value = cash_data.get("value", 0)
						# Сохраняем только положительные суммы (пополнения)
						if card_id and cash_value > 0:
							try:
								await db.log_card_replenishment(card_id, float(cash_value))
								logger.info(f"✅ Пополнение сохранено: card_id={card_id}, amount={cash_value}")
							except Exception as e:
								logger.warning(f"⚠️ Ошибка сохранения пополнения card_id={card_id}, amount={cash_value}: {e}")
			
			# Отправляем промежуточное уведомление о формировании отчета
			try:
				await cb.message.edit_text("⏳ Формирование отчета...", reply_markup=None)
			except Exception:
				pass
			
			# Формируем отчет о записи
			from app.google_sheets import read_card_balance, read_profit
			current_date = datetime.now().strftime("%d.%m.%Y")
			
			written_cells = result.get("written_cells", [])
			row = result.get("row")
			column_rows = result.get("column_rows", {})  # Для режима rate: {column: row}
			
			report_lines = []
			
			if mode == "add" and row:
				report_lines.append(f"<code>📍 Строка: {row}</code>")
			
			if written_cells:
				for cell_info in written_cells:
					report_lines.append(f"<code> • {cell_info}</code>")
			else:
				report_lines.append("⚠️ Нет записанных данных")
			
			# Читаем балансы карт и профиты
			# Получаем настройки из БД
			balance_row_str = await db.get_google_sheets_setting("balance_row", "4")
			profit_column_str = await db.get_google_sheets_setting("profit_column", "BC")
			balance_row = int(balance_row_str) if balance_row_str else 4
			profit_column = profit_column_str if profit_column_str else "BC"
			
			# Читаем балансы для всех карт из card_cash_pairs (batch чтение)
			from app.google_sheets import read_card_balances_batch, read_profits_batch
			
			card_balances = {}
			balance_cell_addresses = []
			card_mapping = {}  # {cell_address: (card_name, column, card_id)}
			
			for pair in card_cash_pairs:
				card_data = pair.get("card")
				if card_data:
					card_name = card_data.get("card_name", "")
					card_id = card_data.get("card_id")
					column = card_data.get("column")
					if column:
						cell_address = f"{column}{balance_row}"
						balance_cell_addresses.append(cell_address)
						card_mapping[cell_address] = (card_name, column, card_id)
			
			# Получаем информацию о группах для всех карт (оптимизированно - одним запросом)
			card_groups_info = {}  # {card_id: group_name}
			# Собираем все уникальные card_id
			card_ids = []
			for pair in card_cash_pairs:
				card_data = pair.get("card")
				if card_data:
					card_id = card_data.get("card_id")
					if card_id and card_id not in card_ids:
						card_ids.append(card_id)
			
			# Получаем все карты с группами одним batch запросом
			if card_ids:
				card_groups_info = await db.get_cards_groups_batch(card_ids)
			
			# Читаем все балансы одним batch запросом
			if balance_cell_addresses:
				balances = await read_card_balances_batch(
					settings.google_sheet_id,
					settings.google_credentials_path,
					balance_cell_addresses,
					settings.google_sheet_name
				)
				for cell_address, (card_name, column, card_id) in card_mapping.items():
					balance = balances.get(cell_address)
					if balance:
						group_name = card_groups_info.get(card_id, "") if card_id else ""
						card_balances[card_name] = {
							"balance": balance,
							"column": column,
							"group_name": group_name
						}
			
			# Читаем профиты (batch чтение)
			profits = {}
			profit_cell_addresses = []
			
			if mode in ["add", "move"] and row:
				# В режимах /add и /move все данные в одной строке
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
			
			# Читаем балансы для наличных (оптимизированно - batch запрос)
			cash_balances = {}
			cash_balance_cell_addresses = []
			cash_mapping = {}  # {cell_address: (cash_name, column)}
			
			# Собираем все cash_name и получаем столбцы одним запросом
			cash_names = [cash.get("cash_name", "") for cash in cash_list if cash.get("cash_name")]
			cash_columns_dict = {}
			if cash_names:
				cash_columns_dict = await db.get_cash_columns_batch(cash_names)
			
			for cash in cash_list:
				cash_name = cash.get("cash_name", "")
				if cash_name:
					# Получаем столбец из batch результата
					cash_column_info = cash_columns_dict.get(cash_name)
				if cash_column_info:
					column = cash_column_info.get("column")
					if column:
						cell_address = f"{column}{balance_row}"
						cash_balance_cell_addresses.append(cell_address)
						cash_mapping[cell_address] = (cash_name, column)
			
			# Читаем все балансы наличных одним batch запросом
			if cash_balance_cell_addresses:
				cash_balances_dict = await read_card_balances_batch(
					settings.google_sheet_id,
					settings.google_credentials_path,
					cash_balance_cell_addresses
				)
				for cell_address, (cash_name, column) in cash_mapping.items():
					balance = cash_balances_dict.get(cell_address)
					if balance:
						cash_balances[cash_name] = balance
			
			# Читаем балансы для криптовалют (оптимизированно - batch запрос)
			crypto_balances = {}
			crypto_balance_cell_addresses = []
			crypto_mapping = {}  # {cell_address: (crypto_type, column)}
			
			# Собираем все crypto_type и получаем столбцы одним запросом
			crypto_types = []
			# Обрабатываем обычные криптовалюты (BTC, LTC и т.д.)
			for crypto in crypto_list:
				crypto_type = crypto.get("currency", "")
				if crypto_type:
					crypto_types.append(crypto_type)
			
			# Обрабатываем XMR (формат XMR-1, XMR-2, XMR-3)
			for xmr in xmr_list:
				xmr_number = xmr.get("xmr_number")
				if xmr_number:
					crypto_types.append(f"XMR-{xmr_number}")
			
			# Получаем все столбцы одним batch запросом
			crypto_columns_dict = {}
			if crypto_types:
				crypto_columns_dict = await db.get_crypto_columns_batch(crypto_types)
			
			# Формируем cell_addresses на основе полученных столбцов
			for crypto in crypto_list:
				crypto_type = crypto.get("currency", "")
				if crypto_type:
					column = crypto_columns_dict.get(crypto_type)
					if column:
						cell_address = f"{column}{balance_row}"
						crypto_balance_cell_addresses.append(cell_address)
						crypto_mapping[cell_address] = (crypto_type, column)
			
			for xmr in xmr_list:
				xmr_number = xmr.get("xmr_number")
				if xmr_number:
					crypto_type = f"XMR-{xmr_number}"
					column = crypto_columns_dict.get(crypto_type)
					if column:
						cell_address = f"{column}{balance_row}"
						crypto_balance_cell_addresses.append(cell_address)
						crypto_mapping[cell_address] = (crypto_type, column)
			
			# Читаем все балансы криптовалют одним batch запросом
			if crypto_balance_cell_addresses:
				crypto_balances_dict = await read_card_balances_batch(
					settings.google_sheet_id,
					settings.google_credentials_path,
					crypto_balance_cell_addresses
				)
				for cell_address, (crypto_type, column) in crypto_mapping.items():
					balance = crypto_balances_dict.get(cell_address)
					if balance:
						crypto_balances[crypto_type] = balance
			
			# Добавляем информацию о балансах в отчет
			if card_balances or cash_balances or crypto_balances:
				report_lines.append("")
				
				if card_balances:
					for card_name, data in card_balances.items():
						group_name = data.get("group_name", "")
						# Формируем строку с балансом
						if group_name:
							report_lines.append(f"  💳 Баланс <code>{card_name} ({group_name}) = {data['balance']}</code>")
						else:
							report_lines.append(f"  💳 Баланс <code>{card_name} = {data['balance']}</code>")
				
				if cash_balances:
					for cash_name, balance in cash_balances.items():
						report_lines.append(f"  💳 Баланс <code>{cash_name} = {balance}</code>")
				
				if crypto_balances:
					for crypto_type, balance in crypto_balances.items():
						report_lines.append(f"  💳 Баланс <code>{crypto_type} = {balance}</code>")
				
				# Добавляем статистику пополнений после всех балансов (только для mode == "add")
				if mode == "add" and card_balances:
					replenishment_lines = []
					# Собираем все card_id для batch запроса
					card_ids_for_stats = []
					card_name_to_id = {}  # {card_name: card_id}
					for card_name, data in card_balances.items():
						# Находим card_id для этой карты из card_mapping
						card_id = None
						for cell_address, (mapped_card_name, column, mapped_card_id) in card_mapping.items():
							if mapped_card_name == card_name:
								card_id = mapped_card_id
								break
						
						if card_id:
							card_ids_for_stats.append(card_id)
							card_name_to_id[card_name] = card_id
					
					# Получаем статистику пополнений одним batch запросом
					replenishment_stats_dict = {}
					if card_ids_for_stats:
						try:
							replenishment_stats_dict = await db.get_cards_replenishment_stats_batch(card_ids_for_stats)
						except Exception as e:
							logger.warning(f"⚠️ Ошибка batch получения статистики пополнений: {e}")
					
					# Формируем строки статистики
					for card_name, data in card_balances.items():
						card_id = card_name_to_id.get(card_name)
						if card_id:
							replenishment_stats = replenishment_stats_dict.get(card_id)
							if replenishment_stats:
								month_total = replenishment_stats.get("month_total", 0.0)
								all_time_total = replenishment_stats.get("all_time_total", 0.0)
								# Форматируем числа (убираем лишние нули после запятой)
								month_str = f"{month_total:.2f}".rstrip('0').rstrip('.') if month_total != int(month_total) else str(int(month_total))
								all_time_str = f"{all_time_total:.2f}".rstrip('0').rstrip('.') if all_time_total != int(all_time_total) else str(int(all_time_total))
								
								group_name = data.get("group_name", "")
								if group_name:
									replenishment_lines.append(f"  💳 {card_name} ({group_name}):")
								else:
									replenishment_lines.append(f"  💳 {card_name}:")
								replenishment_lines.append(f"    💳❇️ Пополнение за месяц: <code>{month_str}</code>")
								replenishment_lines.append(f"    💳✳️ Общее пополнение: <code>{all_time_str}</code>")
					
					# Добавляем статистику пополнений в отчет, если есть данные
					if replenishment_lines:
						report_lines.append("")
						report_lines.extend(replenishment_lines)
			
			# Добавляем раздел с профитом
			profit_section_lines = []
			
			# Профит сделки (для режимов /add и /move)
			if profits and mode in ["add", "move"]:
				for cell_address, profit_value in profits.items():
					profit_section_lines.append(f"  💹 <b>Профит сделки ({cell_address}) = {profit_value} USD </b>💹\n")
			
			# Профит за сегодня и средний профит (только для режима /add)
			if mode == "add":
					try:
						# Определяем текущий день недели
						today = datetime.now()
						weekday = today.weekday()  # 0 = Monday, 6 = Sunday
						
						day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
						day_name = day_names[weekday]
						
						# Собираем все адреса ячеек для профитов для batch чтения
						profit_cells_to_read = {}  # {cell_address: day_name}
						
						# Получаем ячейку профита за текущий день
						profit_cell_key = f"profit_{day_name}"
						profit_cell = await db.get_google_sheets_setting(profit_cell_key)
						if profit_cell:
							profit_cells_to_read[profit_cell] = day_name
						
						# Собираем адреса ячеек для среднего профита (если не понедельник)
						if weekday != 0:  # 0 = понедельник
							profit_days_all = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
							# Берем только дни с понедельника до текущего дня включительно
							profit_days = profit_days_all[:weekday + 1]
							
							for day in profit_days:
								profit_cell_key = f"profit_{day}"
								profit_cell = await db.get_google_sheets_setting(profit_cell_key)
								if profit_cell and profit_cell not in profit_cells_to_read:
									profit_cells_to_read[profit_cell] = day
						
						# Читаем все профиты одним batch запросом
						if profit_cells_to_read:
							from app.google_sheets import read_profits_batch
							cell_addresses = list(profit_cells_to_read.keys())
							profits_data = await read_profits_batch(
								settings.google_sheet_id,
								settings.google_credentials_path,
								cell_addresses,
								settings.google_sheet_name
							)
							
							# Обрабатываем профит за сегодня
							if day_name in profit_cells_to_read.values():
								# Находим ячейку для сегодняшнего дня
								today_cell = None
								for cell, day in profit_cells_to_read.items():
									if day == day_name:
										today_cell = cell
										break
								
								if today_cell and today_cell in profits_data:
									profit_today = profits_data[today_cell]
									if profit_today:
										try:
											profit_value = float(str(profit_today).replace(",", ".").replace(" ", ""))
											formatted_profit = f"{int(round(profit_value)):,}".replace(",", " ")
											profit_section_lines.append(f"  📈 Профит за сегодня: <code>{formatted_profit} USD</code>")
										except (ValueError, AttributeError):
											profit_section_lines.append(f"  📈 Профит за сегодня: <code>{profit_today} USD</code>")
							
							# Обрабатываем средний профит (если не понедельник)
							if weekday != 0:
								profit_values = []
								for cell_address, day in profit_cells_to_read.items():
									if cell_address in profits_data:
										profit_value = profits_data[cell_address]
										if profit_value:
											try:
												value = float(str(profit_value).replace(",", ".").replace(" ", ""))
												profit_values.append(value)
											except (ValueError, AttributeError):
												pass
								
								if profit_values:
									avg_profit = sum(profit_values) / len(profit_values)
									formatted_avg = f"{int(round(avg_profit)):,}".replace(",", " ")
									profit_section_lines.append(f"  📊 Средний профит в день: <code>{formatted_avg} USD</code>")
					except Exception as e:
						logger.warning(f"Ошибка получения профита за сегодня и среднего профита: {e}")
			
			# Добавляем раздел с профитом в отчет, если есть данные
			if profit_section_lines:
				report_lines.append("")
				report_lines.extend(profit_section_lines)
			
			# Проверяем наличие ошибок
			failed_writes = result.get("failed_writes", [])
			error_message = result.get("message")
			if error_message:
				report_lines.append(f"\n❌ Ошибка: {error_message}")
			if failed_writes:
				report_lines.append("\n❌ Не записано:")
				for failed in failed_writes:
					report_lines.append(f"  • {failed}")
			
			report_text = "\n".join(report_lines)
			
			# Callback уже был обработан в начале функции
			await state.clear()
			try:
				await cb.message.edit_text(report_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
			except Exception as edit_error:
				# Обрабатываем ошибки сети при отправке сообщения
				logger.warning(f"Ошибка отправки сообщения с отчетом: {edit_error}")
				# Пытаемся отправить новое сообщение вместо редактирования
				try:
					await cb.message.answer(report_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
				except Exception as answer_error:
					logger.error(f"Не удалось отправить отчет: {answer_error}")
		else:
			await state.clear()
			try:
				await cb.answer("❌ Ошибка записи в Google Sheets", show_alert=True)
			except Exception:
				# Если callback устарел, просто обновляем сообщение
				await cb.message.edit_text("❌ Ошибка записи в Google Sheets", reply_markup=admin_menu_kb())
	except Exception as e:
		logger.exception(f"Ошибка записи в Google Sheets: {e}")
		await state.clear()
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
	await safe_edit_text(cb.message, text, reply_markup=cards_groups_kb(groups))
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


@admin_router.callback_query(F.data == "admin:expenses")
async def admin_expenses(cb: CallbackQuery):
	"""Показывает расходы из ячейки BD420"""
	await cb.answer()
	
	db = get_db()
	from app.config import get_settings
	from app.google_sheets import read_cell_value
	
	settings = get_settings()
	
	if not settings.google_sheet_id or not settings.google_credentials_path:
		await safe_edit_text(cb.message, "❌ Google Sheets не настроен", reply_markup=simple_back_kb("admin:back"))
		return
	
	# Получаем адрес ячейки расходов из настроек
	expenses_cell = await db.get_google_sheets_setting("expenses_cell", "BD420")
	
	# Отправляем сообщение о загрузке
	loading_msg = await cb.message.edit_text("⏳ Загрузка расходов...", reply_markup=simple_back_kb("admin:back"))
	
	try:
		# Читаем значение ячейки
		value = await read_cell_value(
			settings.google_sheet_id,
			settings.google_credentials_path,
			expenses_cell,
			settings.google_sheet_name
		)
		
		if value is None:
			text = f"❌ Не удалось прочитать значение ячейки {expenses_cell}"
		else:
			# Форматируем значение (если это число, форматируем его)
			try:
				num_value = float(value)
				formatted_value = f"{num_value:,.2f}".replace(",", " ").replace(".", ",")
				text = f"💰 <b>Расходы</b>\n\nЯчейка: {expenses_cell}\nЗначение: {formatted_value}"
			except ValueError:
				text = f"💰 <b>Расходы</b>\n\nЯчейка: {expenses_cell}\nЗначение: {value}"
		
		await safe_edit_text(loading_msg, text, reply_markup=simple_back_kb("admin:back"))
	except Exception as e:
		logger.exception(f"Ошибка получения расходов: {e}")
		await safe_edit_text(loading_msg, f"❌ Ошибка получения расходов: {str(e)}", reply_markup=simple_back_kb("admin:back"))


@admin_router.message(Command("cons"))
async def admin_cons_command(msg: Message, bot: Bot, state: FSMContext):
	"""Обработчик команды /cons для отображения расходов"""
	await state.clear()
	
	db = get_db()
	from app.config import get_settings
	from app.google_sheets import read_cell_value
	
	settings = get_settings()
	
	if not settings.google_sheet_id or not settings.google_credentials_path:
		await msg.answer("❌ Google Sheets не настроен", reply_markup=simple_back_kb("admin:back"))
		return
	
	# Получаем адрес ячейки расходов из настроек
	expenses_cell = await db.get_google_sheets_setting("expenses_cell", "BD420")
	
	# Отправляем сообщение о загрузке
	loading_msg = await msg.answer("⏳ Загрузка расходов...", reply_markup=simple_back_kb("admin:back"))
	
	try:
		# Читаем значение ячейки
		value = await read_cell_value(
			settings.google_sheet_id,
			settings.google_credentials_path,
			expenses_cell,
			settings.google_sheet_name
		)
		
		if value is None:
			text = f"❌ Не удалось прочитать значение ячейки {expenses_cell}"
		else:
			# Форматируем значение (если это число, форматируем его)
			try:
				num_value = float(value)
				formatted_value = f"{num_value:,.2f}".replace(",", " ").replace(".", ",")
				text = f"💰 <b>Расходы</b>\n\nЯчейка: {expenses_cell}\nЗначение: {formatted_value}"
			except ValueError:
				text = f"💰 <b>Расходы</b>\n\nЯчейка: {expenses_cell}\nЗначение: {value}"
		
		await safe_edit_text(loading_msg, text, reply_markup=simple_back_kb("admin:back"))
	except Exception as e:
		logger.exception(f"Ошибка получения расходов: {e}")
		await safe_edit_text(loading_msg, f"❌ Ошибка получения расходов: {str(e)}", reply_markup=simple_back_kb("admin:back"))


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
				display_name = cash.get("display_name", "")
				currency = cash.get("currency", "RUB")
				display = display_name if display_name else cash_name_item
				text += f"{display} → {column} ({currency})\n"
		
		await cb.message.edit_text(text, reply_markup=cash_list_kb(cash_columns))
	except Exception as e:
		logger.exception(f"Ошибка при удалении наличных: {e}")
		await cb.answer("❌ Произошла ошибка при удалении", show_alert=True)


@admin_router.callback_query(F.data.startswith("cash:edit:"))
async def cash_edit(cb: CallbackQuery, state: FSMContext):
	"""Показывает меню редактирования валюты"""
	db = get_db()
	cash_name = cb.data.split(":")[-1]
	
	# Получаем текущую информацию о валюте
	cash_info = await db.get_cash_column(cash_name)
	
	if not cash_info:
		await cb.answer("❌ Валюта не найдена", show_alert=True)
		return
	
	# Сохраняем название наличных в state
	await state.update_data(cash_name=cash_name)
	
	from app.keyboards import cash_edit_menu_kb
	current_column = cash_info.get("column", "")
	current_currency = cash_info.get("currency", "RUB")
	current_display_name = cash_info.get("display_name", "")
	# Если display_name пустое, используем cash_name как имя валюты
	display_name_for_show = current_display_name if current_display_name else cash_name
	
	text = f"Что вы хотите изменить для '{cash_name}'?\n\n"
	text += f"Текущие значения:\n"
	text += f"📍 Ячейка: {current_column or 'не указана'}\n"
	text += f"💵 Имя валюты: {display_name_for_show}\n"
	text += f"💰 Номинал валюты: {current_currency}"
	
	await cb.message.edit_text(
		text,
		reply_markup=cash_edit_menu_kb(cash_name)
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("cash:edit_column:"))
async def cash_edit_column(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование адреса столбца для наличных"""
	db = get_db()
	cash_name = cb.data.split(":")[-1]
	
	# Получаем текущую информацию
	cash_info = await db.get_cash_column(cash_name)
	
	# Сохраняем название наличных в state
	await state.update_data(cash_name=cash_name)
	await state.set_state(CashColumnEditStates.waiting_cash_column)
	
	current_column = cash_info.get("column", "") if cash_info else ""
	current_text = f" (текущий: {current_column})" if current_column else ""
	await cb.message.edit_text(
		f"Редактирование адреса столбца для {cash_name}{current_text}\n\n"
		"Введите новый адрес столбца (только латинские буквы):\n"
		"Например: A, B, C, D, E, AS, AY",
		reply_markup=simple_back_kb(f"cash:edit:{cash_name}")
	)
	await cb.answer()


@admin_router.message(CashColumnEditStates.waiting_cash_column)
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
				display_name = cash.get("display_name", "")
				currency = cash.get("currency", "RUB")
				display = display_name if display_name else cash_name_item
				text += f"{display} → {column} ({currency})\n"
		
		from app.keyboards import cash_list_kb
		await message.answer(
			f"✅ Адрес столбца для '{cash_name}' обновлен на '{column_input}'",
			reply_markup=cash_list_kb(cash_columns)
		)
	except Exception as e:
		logger.exception(f"Ошибка при сохранении адреса столбца для наличных: {e}")
		await message.answer("❌ Произошла ошибка при сохранении. Попробуйте еще раз.")


@admin_router.callback_query(F.data.startswith("cash:edit_display_name:"))
async def cash_edit_display_name(cb: CallbackQuery, state: FSMContext):
	"""Начинает редактирование имени валюты (emoji) для наличных"""
	db = get_db()
	cash_name = cb.data.split(":")[-1]
	
	# Получаем текущую информацию
	cash_info = await db.get_cash_column(cash_name)
	
	# Сохраняем название наличных в state
	await state.update_data(cash_name=cash_name)
	await state.set_state(CashColumnEditStates.waiting_cash_display_name)
	
	current_display_name = cash_info.get("display_name", "") if cash_info else ""
	current_text = f" (текущий: {current_display_name})" if current_display_name else ""
	await cb.message.edit_text(
		f"Редактирование имени валюты для {cash_name}{current_text}\n\n"
		"Введите новое имя валюты (например, 🐿, 💵):",
		reply_markup=simple_back_kb(f"cash:edit:{cash_name}")
	)
	await cb.answer()


@admin_router.message(CashColumnEditStates.waiting_cash_display_name)
async def cash_display_name_waiting(message: Message, state: FSMContext):
	"""Обрабатывает ввод имени валюты для наличных"""
	db = get_db()
	display_name_input = message.text.strip()
	
	# Получаем данные из state
	data = await state.get_data()
	cash_name = data.get("cash_name")
	
	if not cash_name:
		await message.answer("❌ Ошибка: название наличных не найдено. Попробуйте начать заново.")
		await state.clear()
		return
	
	# Сохраняем имя валюты
	try:
		await db.update_cash_display_name(cash_name, display_name_input)
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
				display_name = cash.get("display_name", "")
				currency = cash.get("currency", "RUB")
				display = display_name if display_name else cash_name_item
				text += f"{display} → {column} ({currency})\n"
		
		from app.keyboards import cash_list_kb
		await message.answer(
			f"✅ Имя валюты для '{cash_name}' обновлено на '{display_name_input}'",
			reply_markup=cash_list_kb(cash_columns)
		)
	except Exception as e:
		logger.exception(f"Ошибка при сохранении имени валюты для наличных: {e}")
		await message.answer("❌ Произошла ошибка при сохранении. Попробуйте еще раз.")


@admin_router.callback_query(F.data.startswith("cash:edit_currency:"))
async def cash_edit_currency(cb: CallbackQuery, state: FSMContext):
	"""Показывает выбор номинала валюты для наличных"""
	db = get_db()
	cash_name = cb.data.split(":")[-1]
	
	# Получаем текущую информацию
	cash_info = await db.get_cash_column(cash_name)
	
	# Сохраняем название наличных в state
	await state.update_data(cash_name=cash_name)
	
	current_currency = cash_info.get("currency", "RUB") if cash_info else "RUB"
	
	from app.keyboards import cash_currency_select_kb
	await cb.message.edit_text(
		f"Редактирование номинала валюты для {cash_name}\n\n"
		f"Текущий номинал: {current_currency}\n\n"
		"Выберите новый номинал валюты:",
		reply_markup=cash_currency_select_kb(cash_name, current_currency)
	)
	await cb.answer()


@admin_router.callback_query(F.data.startswith("cash:set_currency:"))
async def cash_set_currency(cb: CallbackQuery, state: FSMContext):
	"""Обрабатывает выбор номинала валюты для наличных"""
	db = get_db()
	parts = cb.data.split(":")
	cash_name = parts[2]
	currency = parts[3]
	
	# Сохраняем номинал валюты
	try:
		await db.update_cash_currency(cash_name, currency)
		
		# Обновляем список
		cash_columns = await db.list_cash_columns()
		if not cash_columns:
			text = "Список наличных пуст."
		else:
			text = "Список наличных и их адресов столбцов:\n\n"
			for cash in cash_columns:
				cash_name_item = cash.get("cash_name", "")
				column = cash.get("column", "")
				display_name = cash.get("display_name", "")
				currency_item = cash.get("currency", "RUB")
				display = display_name if display_name else cash_name_item
				text += f"{display} → {column} ({currency_item})\n"
		
		from app.keyboards import cash_list_kb
		await cb.message.edit_text(
			f"✅ Номинал валюты для '{cash_name}' обновлен на '{currency}'",
			reply_markup=cash_list_kb(cash_columns)
		)
		await cb.answer()
	except Exception as e:
		logger.exception(f"Ошибка при сохранении номинала валюты для наличных: {e}")
		await cb.answer("❌ Произошла ошибка при сохранении", show_alert=True)


@admin_router.callback_query(F.data.startswith("crypto:edit:"))
async def crypto_edit(cb: CallbackQuery, state: FSMContext):
	"""Показывает меню выбора: редактировать название или столбец"""
	db = get_db()
	crypto_type = cb.data.split(":")[-1]
	
	# Получаем текущий адрес столбца
	current_column = await db.get_crypto_column(crypto_type)
	
	# Сохраняем тип криптовалюты в state
	await state.update_data(crypto_type=crypto_type)
	
	text = f"Криптовалюта: <b>{crypto_type}</b>\n"
	text += f"Адрес столбца: <b>{current_column}</b>\n\n"
	text += "Что вы хотите отредактировать?"
	
	kb = InlineKeyboardBuilder()
	kb.button(text="✏️ Редактировать название", callback_data=f"crypto:rename:{crypto_type}")
	kb.button(text="📊 Редактировать столбец", callback_data=f"crypto:edit_column:{crypto_type}")
	kb.button(text="⬅️ Назад", callback_data="admin:crypto")
	kb.adjust(1)
	
	await cb.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
	await cb.answer()


@admin_router.callback_query(F.data.startswith("crypto:rename:"))
async def crypto_rename_start(cb: CallbackQuery, state: FSMContext):
	"""Начинает переименование криптовалюты"""
	crypto_type = cb.data.split(":")[-1]
	
	# Сохраняем тип криптовалюты в state
	await state.update_data(crypto_type=crypto_type)
	await state.set_state(CryptoColumnEditStates.waiting_rename)
	
	await cb.message.edit_text(
		f"Переименование криптовалюты\n\n"
		f"Текущее название: <b>{crypto_type}</b>\n\n"
		"Введите новое название:\n"
		"Например: ТРАСТ, BTC, LTC",
		reply_markup=simple_back_kb("admin:crypto"),
		parse_mode="HTML"
	)
	await cb.answer()


@admin_router.message(CryptoColumnEditStates.waiting_rename)
async def crypto_rename_input(message: Message, state: FSMContext):
	"""Обрабатывает ввод нового названия криптовалюты"""
	db = get_db()
	new_crypto_type = message.text.strip().upper()
	
	if not new_crypto_type:
		await message.answer("❌ Название криптовалюты не может быть пустым. Попробуйте еще раз:")
		return
	
	# Получаем данные из state
	data = await state.get_data()
	old_crypto_type = data.get("crypto_type")
	
	if not old_crypto_type:
		await message.answer("❌ Ошибка: тип криптовалюты не найден. Попробуйте начать заново.")
		await state.clear()
		return
	
	if old_crypto_type == new_crypto_type:
		await message.answer("❌ Новое название совпадает со старым. Попробуйте другое название:")
		return
	
	# Переименовываем криптовалюту
	try:
		await db.rename_crypto_type(old_crypto_type, new_crypto_type)
		
		await message.answer(
			f"✅ Криптовалюта успешно переименована!\n\n"
			f"Старое название: {old_crypto_type}\n"
			f"Новое название: {new_crypto_type}",
			reply_markup=simple_back_kb("admin:crypto")
		)
		await state.clear()
	except ValueError as e:
		await message.answer(f"❌ {str(e)}. Попробуйте другое название:")
	except Exception as e:
		logger.exception(f"Ошибка при переименовании криптовалюты: {e}")
		await message.answer("❌ Произошла ошибка при переименовании. Попробуйте еще раз.")


@admin_router.callback_query(F.data.startswith("crypto:edit_column:"))
async def crypto_edit_column_start(cb: CallbackQuery, state: FSMContext):
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
	
	# Проверяем наличие реквизитов (user_message и/или реквизиты из card_requisites)
	requisites = await db.list_card_requisites(card_id)
	has_user_message = bool(card.get('user_message') and card['user_message'].strip())
	has_requisites = len(requisites) > 0
	
	if has_user_message:
		text += f"\n\nТекущее сообщение:\n{card['user_message']}"
	elif has_requisites:
		text += f"\n\nРеквизитов: {len(requisites)}"
	else:
		text += "\n\nСообщение не задано"
	
	text += "\n\nЧто хотите сделать?"
	
	# Определяем, куда должна вести кнопка "Назад"
	# Если карта в группе, возвращаемся к списку карт группы, иначе к списку групп
	if card.get("group_id"):
		back_to = f"cards:group:{card['group_id']}"
	else:
		back_to = "admin:cards"
	
	await cb.message.edit_text(text, reply_markup=card_action_kb(card_id, back_to), parse_mode="HTML")
	await cb.answer()


@admin_router.callback_query(F.data.startswith("card:edit_name:"))
async def card_edit_name_start(cb: CallbackQuery, state: FSMContext):
	"""Начинает процесс редактирования названия карты"""
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	card = await db.get_card_by_id(card_id)
	if not card:
		await cb.answer("Карта не найдена", show_alert=True)
		return
	
	# Очищаем старое состояние перед установкой нового
	await state.clear()
	await state.set_state(CardNameEditStates.waiting_name)
	await state.update_data(card_id=card_id)
	
	from app.keyboards import simple_back_kb
	await cb.message.edit_text(
		f"💳 Текущее название: {card['name']}\n\nВведите новое название карты:",
		reply_markup=simple_back_kb(f"card:view:{card_id}")
	)
	await cb.answer()


@admin_router.message(CardNameEditStates.waiting_name)
async def card_edit_name_set(message: Message, state: FSMContext):
	"""Обрабатывает ввод нового названия карты"""
	db = get_db()
	data = await state.get_data()
	card_id = data.get("card_id")
	
	if not card_id:
		await message.answer("❌ Ошибка: не найден ID карты")
		await state.clear()
		return
	
	new_name = message.text.strip()
	if not new_name:
		await message.answer("❌ Название карты не может быть пустым. Попробуйте еще раз:")
		return
	
	# Обновляем название карты
	await db.set_card_name(card_id, new_name)
	await state.clear()
	
	# Получаем обновленную информацию о карте
	card = await db.get_card_by_id(card_id)
	if not card:
		await message.answer("❌ Карта не найдена", reply_markup=admin_menu_kb())
		return
	
	# Формируем информацию о карте для возврата
	text = f"💳 {card['name']}"
	
	# Получаем привязанные ячейки для этой карты
	card_columns = await db.list_card_columns(card_id=card_id)
	if card_columns:
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
	
	text += f"\n\n✅ Название карты изменено на: {new_name}\n\nЧто хотите сделать?"
	
	# Определяем, куда должна вести кнопка "Назад"
	# Если карта в группе, возвращаемся к списку карт группы, иначе к списку групп
	if card.get("group_id"):
		back_to = f"cards:group:{card['group_id']}"
	else:
		back_to = "admin:cards"
	
	from app.keyboards import card_action_kb
	await message.answer(text, reply_markup=card_action_kb(card_id, back_to), parse_mode="HTML")


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
		
		# Определяем, куда должна вести кнопка "Назад"
		# Если карта в группе, возвращаемся к списку карт группы, иначе к списку групп
		if card.get("group_id"):
			back_to = f"cards:group:{card['group_id']}"
		else:
			back_to = "admin:cards"
		
		await cb.message.edit_text(text, reply_markup=card_action_kb(card_id, back_to), parse_mode="HTML")
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
	# Получаем количество пользователей на странице из БД (по умолчанию 10)
	users_per_page_str = await db.get_google_sheets_setting("users_per_page", "10")
	try:
		users_per_page = int(users_per_page_str) if users_per_page_str else 10
	except (ValueError, TypeError):
		users_per_page = 10
	
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
	logger.debug(f"Show users: total={total} page={page} users_per_page={users_per_page}")
	if total == 0:
		text = "Пользователи не найдены."
		reply_markup = users_list_kb([], back_to="admin:back")
	else:
		total_pages = (total + users_per_page - 1) // users_per_page
		page = max(0, min(page, total_pages - 1))
		start = page * users_per_page
		end = start + users_per_page
		page_items = items[start:end]
		text = f"Пользователи (стр. {page+1}/{total_pages}, всего: {total}):"
		reply_markup = users_list_kb(
			page_items,
			back_to="admin:back",
			page=page,
			per_page=users_per_page,
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
	from app.google_sheets import get_crypto_values_from_row_4, read_card_balance
	from app.di import get_db
	
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
		
		# Читаем профит за день и средний профит со вторника
		profit_lines = []
		db = get_db()
		
		# Определяем текущий день недели
		from datetime import datetime
		today = datetime.now()
		weekday = today.weekday()  # 0 = Monday, 6 = Sunday
		
		day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
		day_name = day_names[weekday]
		day_name_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][weekday]
		
		# Собираем все адреса ячеек для профитов для batch чтения
		profit_cells_to_read = {}  # {cell_address: day_name}
		import re
		
		# Получаем ячейку профита за текущий день
		profit_cell_key = f"profit_{day_name}"
		profit_cell = await db.get_google_sheets_setting(profit_cell_key)
		if profit_cell:
			profit_cells_to_read[profit_cell] = day_name
		
		# Собираем адреса ячеек для среднего профита (если не понедельник)
		if weekday != 0:  # 0 = понедельник
			profit_days_all = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
			# Берем только дни с понедельника до текущего дня включительно
			profit_days = profit_days_all[:weekday + 1]
			
			for day in profit_days:
				profit_cell_key = f"profit_{day}"
				profit_cell = await db.get_google_sheets_setting(profit_cell_key)
				if profit_cell and profit_cell not in profit_cells_to_read:
					profit_cells_to_read[profit_cell] = day
		
		# Читаем все профиты одним batch запросом
		if profit_cells_to_read:
			try:
				from app.google_sheets import read_profits_batch
				cell_addresses = list(profit_cells_to_read.keys())
				profits_data = await read_profits_batch(
					sheet_id,
					credentials_path,
					cell_addresses,
					sheet_name
				)
				
				# Обрабатываем профит за сегодня
				if day_name in profit_cells_to_read.values():
					# Находим ячейку для сегодняшнего дня
					today_cell = None
					for cell, day in profit_cells_to_read.items():
						if day == day_name:
							today_cell = cell
							break
					
					if today_cell and today_cell in profits_data:
						profit_today = profits_data[today_cell]
						if profit_today:
							try:
								profit_value = float(str(profit_today).replace(",", ".").replace(" ", ""))
								formatted_profit = f"{int(round(profit_value)):,}".replace(",", " ")
								profit_lines.append(f"<code>📈 Профит за сегодня: {formatted_profit} USD</code>")
							except (ValueError, AttributeError):
								profit_lines.append(f"<code>📈 Профит за сегодня: {profit_today} USD</code>")
				
				# Обрабатываем средний профит (если не понедельник)
				if weekday != 0:
					profit_values = []
					for cell_address, day in profit_cells_to_read.items():
						if cell_address in profits_data:
							profit_value = profits_data[cell_address]
							if profit_value:
								try:
									value = float(str(profit_value).replace(",", ".").replace(" ", ""))
									profit_values.append(value)
								except (ValueError, AttributeError):
									pass
					
					if profit_values:
						avg_profit = sum(profit_values) / len(profit_values)
						formatted_avg = f"{int(round(avg_profit)):,}".replace(",", " ")
						profit_lines.append(f"<code>📊 Средний: {formatted_avg} USD</code>")
			except Exception as e:
				logger.warning(f"Ошибка batch чтения профитов: {e}")
		
		# Читаем балансы наличных БЕЛКИ и БАКСЫ
		cash_lines = []
		try:
			from app.google_sheets import read_card_balances_batch
			
			# Получаем balance_row из настроек
			balance_row_str = await db.get_google_sheets_setting("balance_row", "4")
			balance_row = int(balance_row_str) if balance_row_str else 4
			
			# Получаем информацию о наличных из базы
			belki_info = await db.get_cash_column("БЕЛКИ")
			baksy_info = await db.get_cash_column("БАКСЫ")
			
			cash_cell_addresses = []
			cash_mapping = {}  # {cell_address: (cash_name, currency, emoji)}
			
			# БЕЛКИ: если не найдено в базе, используем хардкод AP (BYN)
			if belki_info:
				column = belki_info.get("column")
				currency = belki_info.get("currency", "BYN")
			else:
				column = "AP"
				currency = "BYN"
				logger.debug("БЕЛКИ не найдено в базе, используем хардкод: AP (BYN)")
			
			if column:
				cell_address = f"{column}{balance_row}"
				cash_cell_addresses.append(cell_address)
				cash_mapping[cell_address] = ("БЕЛКИ", currency, "🐿")
			
			# БАКСЫ: если не найдено в базе, используем хардкод AQ (USD)
			if baksy_info:
				column = baksy_info.get("column")
				currency = baksy_info.get("currency", "USD")
			else:
				column = "AQ"
				currency = "USD"
				logger.debug("БАКСЫ не найдено в базе, используем хардкод: AQ (USD)")
			
			if column:
				cell_address = f"{column}{balance_row}"
				cash_cell_addresses.append(cell_address)
				cash_mapping[cell_address] = ("БАКСЫ", currency, "💵")
			
			# Читаем балансы наличных одним batch запросом
			if cash_cell_addresses:
				cash_balances = await read_card_balances_batch(
					sheet_id,
					credentials_path,
					cash_cell_addresses,
					sheet_name
				)
				
				for cell_address, (cash_name, currency, emoji) in cash_mapping.items():
					balance = cash_balances.get(cell_address)
					if balance:
						try:
							# Пытаемся форматировать как число
							num_value = float(str(balance).replace(",", ".").replace(" ", ""))
							formatted_value = f"{int(round(num_value)):,}".replace(",", " ")
							cash_lines.append(f"<code>{emoji} {cash_name} ({currency}) = {formatted_value}</code>")
						except (ValueError, AttributeError):
							cash_lines.append(f"<code>{emoji} {cash_name} ({currency}) = {balance}</code>")
					else:
						cash_lines.append(f"<code>{emoji} {cash_name} ({currency}) = —</code>")
		except Exception as e:
			logger.warning(f"Ошибка чтения балансов наличных: {e}")
		
		# Объединяем базовые строки, строки с криптовалютами, наличными и профитом
		all_lines = base_lines + crypto_lines
		if cash_lines:
			all_lines.append("")  # Пустая строка перед наличными
			all_lines.extend(cash_lines)
		if profit_lines:
			all_lines.append("")  # Пустая строка перед профитом
			all_lines.extend(profit_lines)
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
async def admin_stats_command(msg: Message, state: FSMContext):
	"""Обработчик команды /stat_u - показывает меню выбора типа статистики"""
	# Очищаем состояние FSM, чтобы не мешало другим командам
	await state.clear()
	
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


async def _generate_cards_chart(graph_data: Dict[str, Dict[str, Dict[str, Any]]]) -> Optional[str]:
	"""
	Генерирует график балансов и оборотов за месяц по группам и банкам.
	Исключает группу "РАШКА".
	
	Args:
		graph_data: Словарь {group_name: {card_name: {"balance": float, "month": float, "bank": str}}}
	
	Returns:
		Путь к временному файлу с графиком или None при ошибке
	"""
	try:
		# Собираем уникальные группы (люди) и банки
		people = sorted([p for p in graph_data.keys() if p.upper() != "РАШКА"])
		if not people:
			return None
		
		# Собираем все уникальные банки из всех карт
		all_banks = set()
		for group_data in graph_data.values():
			for card_data in group_data.values():
				bank = card_data.get("bank", "")
				if bank:
					all_banks.add(bank)
		banks = sorted(list(all_banks))
		
		if not banks:
			return None
		
		# Инициализируем структуры данных
		balance = {p: {b: 0.0 for b in banks} for p in people}
		month = {p: {b: 0.0 for b in banks} for p in people}
		# Храним карты для каждого сегмента столбца
		cards_by_segment_bal = {p: {b: [] for b in banks} for p in people}
		cards_by_segment_mon = {p: {b: [] for b in banks} for p in people}
		
		# Заполняем данные из graph_data
		for person in people:
			if person not in graph_data:
				continue
			for card_name, card_data in graph_data[person].items():
				bank = card_data.get("bank", "")
				if bank in banks:
					bal_val = card_data.get("balance", 0.0)
					mon_val = card_data.get("month", 0.0)
					balance[person][bank] += bal_val
					month[person][bank] += mon_val
					if bal_val > 0:
						cards_by_segment_bal[person][bank].append((card_name, bal_val))
					if mon_val > 0:
						cards_by_segment_mon[person][bank].append((card_name, mon_val))
		
		# Создаем график
		x = np.arange(len(people))
		w = 0.35
		
		fig = plt.figure(figsize=(7.2, 12.8), dpi=150)  # ~1080x1920
		ax = plt.gca()
		
		bottom_bal = np.zeros(len(people))
		bottom_mon = np.zeros(len(people))
		
		# Вычисляем общую высоту каждого столбца (сумма всех банков для каждого человека)
		total_heights_bal = np.array([sum(balance[p][b] for b in banks) for p in people])
		total_heights_mon = np.array([sum(month[p][b] for b in banks) for p in people])
		max_total_bal = max(total_heights_bal) if len(total_heights_bal) > 0 and max(total_heights_bal) > 0 else 1
		max_total_mon = max(total_heights_mon) if len(total_heights_mon) > 0 and max(total_heights_mon) > 0 else 1
		
		# Цвета для банков (используем цветовую палитру matplotlib)
		colors = plt.cm.tab20(np.linspace(0, 1, len(banks)))
		
		for i, b in enumerate(banks):
			yb = np.array([balance[p][b] for p in people])
			ym = np.array([month[p][b] for p in people])
			
			ax.bar(x - w/2, yb, w, bottom=bottom_bal, label=b, color=colors[i])
			ax.bar(x + w/2, ym, w, bottom=bottom_mon, color=colors[i], alpha=0.7)
			
			# Добавляем подписи карт на столбцы балансов
			for j, person in enumerate(people):
				if yb[j] > 0:
					# Проверяем несколько условий:
					# 1. Сегмент должен быть не менее 10% от высоты всего столбца этого человека
					# 2. Сегмент должен быть не менее 1.5% от максимального столбца
					segment_height_ratio = yb[j] / total_heights_bal[j] if total_heights_bal[j] > 0 else 0
					segment_to_max_ratio = yb[j] / max_total_bal if max_total_bal > 0 else 0
					
					if segment_height_ratio >= 0.10 and segment_to_max_ratio >= 0.015:
						cards = cards_by_segment_bal[person][b]
						if cards:
							# Вычисляем позицию для подписи (середина сегмента)
							label_y = bottom_bal[j] + yb[j] / 2
							# Формируем текст из названий карт
							card_labels = [cn for cn, _ in cards]
							label_text = "\n".join(card_labels) if len(card_labels) <= 2 else f"{len(card_labels)} карт"
							ax.text(x[j] - w/2, label_y, label_text, 
									ha='center', va='center', fontsize=8, 
									color='white' if colors[i][:3].mean() < 0.5 else 'black',
									weight='bold', rotation=0)
			
			# Добавляем подписи карт на столбцы оборотов за месяц
			for j, person in enumerate(people):
				if ym[j] > 0:
					segment_height_ratio = ym[j] / total_heights_mon[j] if total_heights_mon[j] > 0 else 0
					segment_to_max_ratio = ym[j] / max_total_mon if max_total_mon > 0 else 0
					
					if segment_height_ratio >= 0.10 and segment_to_max_ratio >= 0.015:
						cards = cards_by_segment_mon[person][b]
						if cards:
							label_y = bottom_mon[j] + ym[j] / 2
							card_labels = [cn for cn, _ in cards]
							label_text = "\n".join(card_labels) if len(card_labels) <= 2 else f"{len(card_labels)} карт"
							ax.text(x[j] + w/2, label_y, label_text,
									ha='center', va='center', fontsize=6,
									color='white' if colors[i][:3].mean() < 0.5 else 'black',
									weight='bold', rotation=0)
			
			bottom_bal += yb
			bottom_mon += ym
		
		ax.set_title("Балансы и оборот за месяц", fontsize=18)
		ax.set_xticks(x)
		ax.set_xticklabels(people, rotation=0)
		# Убираем легенду справа, так как карты подписаны на графике
		# ax.legend(ncols=2, fontsize=10, loc="upper left", bbox_to_anchor=(1.02, 1))
		ax.grid(axis="y", alpha=0.3)
		
		plt.tight_layout()
		
		# Сохраняем во временный файл
		fd, temp_path = tempfile.mkstemp(suffix='.png', prefix='cards_chart_')
		os.close(fd)
		plt.savefig(temp_path, bbox_inches="tight")
		plt.close(fig)
		
		return temp_path
	except Exception as e:
		logger.exception(f"❌ Ошибка генерации графика: {e}")
		return None


@admin_router.message(Command("cons"))
async def admin_cons_command(msg: Message, state: FSMContext):
	"""Обработчик команды /cons для отображения статистики расходов"""
	# Очищаем состояние FSM, чтобы не мешало другим командам
	await state.clear()
	
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(msg.from_user.id, msg.from_user.username, admin_ids, admin_usernames):
		return
	
	db = get_db()
	from app.config import get_settings
	from app.google_sheets import read_card_balances_batch
	
	settings = get_settings()
	
	if not settings.google_sheet_id or not settings.google_credentials_path:
		await msg.answer("❌ Google Sheets не настроен", reply_markup=simple_back_kb("admin:back"))
		return
	
	# Получаем адрес ячейки расходов из БД
	expenses_cell = await db.get_google_sheets_setting("expenses_cell", "BD420")
	
	if not expenses_cell:
		expenses_cell = "BD420"
	
	# Отправляем сообщение о загрузке
	loading_msg = await msg.answer("⏳ Загрузка статистики расходов...", reply_markup=simple_back_kb("admin:back"))
	
	try:
		# Читаем значение из Google Sheets
		expenses_dict = await read_card_balances_batch(
			settings.google_sheet_id,
			settings.google_credentials_path,
			[expenses_cell],
			settings.google_sheet_name
		)
		
		expenses_value = expenses_dict.get(expenses_cell)
		
		if expenses_value is None or expenses_value == "":
			expenses_value = "0"
		
		# Форматируем значение (убираем лишние нули после запятой, если есть)
		try:
			expenses_float = float(expenses_value.replace(",", "."))
			if expenses_float == int(expenses_float):
				expenses_display = str(int(expenses_float))
			else:
				expenses_display = f"{expenses_float:.2f}".rstrip('0').rstrip('.')
		except (ValueError, AttributeError):
			expenses_display = expenses_value
		
		# Формируем текст ответа
		text = (
			"📊 <b>Статистика расходов</b>\n\n"
			f"💰 <b>Сумма расходов:</b> <code>{expenses_display}</code>\n\n"
			f"📍 <i>Ячейка: {expenses_cell}</i>"
		)
		
		await loading_msg.edit_text(text, reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")
		
	except Exception as e:
		logger.exception(f"❌ Ошибка получения статистики расходов: {e}")
		await loading_msg.edit_text(
			f"❌ Ошибка получения статистики расходов: {e}",
			reply_markup=simple_back_kb("admin:back")
		)


@admin_router.message(Command("stat_bk"))
async def admin_stat_bk_command(msg: Message, bot: Bot, state: FSMContext):
	"""Обработчик команды /stat_bk для отображения балансов всех карт"""
	# Очищаем состояние FSM, чтобы не мешало другим командам
	await state.clear()
	
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
	
	# Получаем все карты с их столбцами и группами одним запросом (оптимизация)
	all_cards_data = await db.get_all_cards_with_columns_and_groups()
	
	if not all_cards_data:
		await msg.answer("❌ Карты не найдены", reply_markup=simple_back_kb("admin:back"))
		return
	
	# Сразу отправляем сообщение о загрузке
	loading_msg = await msg.answer("⏳ Загрузка балансов карт...", reply_markup=simple_back_kb("admin:back"))
	
	# Собираем информацию о картах и их столбцах, группируя по группам
	cards_by_group = {}  # {group_id: [(card_id, card_name, column, cell_address)]}
	cards_without_group = []  # [(card_id, card_name, column, cell_address)]
	cards_without_column = []
	cell_addresses = []
	
	# Получаем все группы
	all_groups = await db.list_card_groups()
	group_names = {group["id"]: group["name"] for group in all_groups}
	
	# Обрабатываем карты (данные уже получены одним запросом)
	for card_data in all_cards_data:
		card_id = card_data["card_id"]
		card_name = card_data["name"]
		column = card_data["column"]
		group_id = card_data["group_id"]
		
		if column:
			cell_address = f"{column}{balance_row}"
			
			if group_id:
				if group_id not in cards_by_group:
					cards_by_group[group_id] = []
				cards_by_group[group_id].append((card_id, card_name, column, cell_address))
			else:
				cards_without_group.append((card_id, card_name, column, cell_address))
			
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
				cell_addresses
			)
		except Exception as e:
			logger.exception(f"Ошибка batch чтения балансов: {e}")

	# Получаем статистику пополнений для всех карт одним batch запросом (ускорение)
	replenishment_stats_dict = {}
	try:
		card_ids_for_stats = []
		for group_cards in cards_by_group.values():
			for card_id, _card_name, _column, _cell_address in group_cards:
				card_ids_for_stats.append(card_id)
		for card_id, _card_name, _column, _cell_address in cards_without_group:
			card_ids_for_stats.append(card_id)
		# уникализируем, сохраняя порядок
		seen_ids = set()
		card_ids_for_stats_unique = []
		for cid in card_ids_for_stats:
			if cid not in seen_ids:
				seen_ids.add(cid)
				card_ids_for_stats_unique.append(cid)

		if card_ids_for_stats_unique:
			replenishment_stats_dict = await db.get_cards_replenishment_stats_batch(card_ids_for_stats_unique)
	except Exception as e:
		logger.warning(f"⚠️ Ошибка batch получения статистики пополнений для /stat_bk: {e}")
	
	# Формируем результат с группировкой (новый короткий формат)
	lines = ["💳 Балансы карт"]
	
	# Собираем данные для графика (исключая группу "РАШКА")
	graph_data = {}  # {group_name: {card_name: {"balance": float, "month": float, "bank": str}}}
	
	# Добавляем карты по группам (сортируем по названию группы)
	sorted_groups = sorted(cards_by_group.keys(), key=lambda gid: group_names.get(gid, f"Группа {gid}"))
	for group_id in sorted_groups:
		group_name = group_names.get(group_id, f"Группа {group_id}")
		lines.append("")
		lines.append(f"❇️{group_name}:")
		
		# Инициализируем данные группы для графика (если не "РАШКА")
		if group_name.upper() != "РАШКА":
			if group_name not in graph_data:
				graph_data[group_name] = {}
		
		for card_id, card_name, column, cell_address in cards_by_group[group_id]:
			balance = balances.get(cell_address)
			balance_value = float(balance) if balance and balance != "—" else 0.0
			balance_str = balance if balance else "—"
			
			# Статистика пополнений (из batch)
			stats = replenishment_stats_dict.get(card_id, {}) if replenishment_stats_dict else {}
			month_total = stats.get("month_total", 0.0) if stats else 0.0
			all_time_total = stats.get("all_time_total", 0.0) if stats else 0.0
			
			# Форматируем числа (убираем лишние нули после запятой)
			month_str = f"{month_total:.2f}".rstrip('0').rstrip('.') if month_total != int(month_total) else str(int(month_total))
			all_time_str = f"{all_time_total:.2f}".rstrip('0').rstrip('.') if all_time_total != int(all_time_total) else str(int(all_time_total))
			
			# Для группы "РАШКА" показываем только первую букву названия карты и первую букву имени владельца
			if group_name.upper() == "РАШКА" and card_name:
				display_name = card_name[0]
				# Извлекаем имя владельца из скобок (например, "ТИНЕК (ВАЩИК)" -> "В")
				owner_initial = ""
				match = re.search(r'\(([^)]+)\)', card_name)
				if match:
					owner_name = match.group(1).strip()
					if owner_name:
						# Берем первую букву имени (убираем пробелы)
						owner_initial = owner_name.replace(" ", "")[0].upper()
				# Формируем строку с инициалом владельца в скобках
				if owner_initial:
					display_name = f"{display_name} ({owner_initial})"
			else:
				display_name = card_name
			
			# Новый короткий формат: баланс(месяц;общее)
			# Для группы "РАШКА" убираем символ ➖ перед скобками
			if group_name.upper() == "РАШКА":
				lines.append(f" ▶️ {display_name} ({column}{balance_row}) = <i>{balance_str}</i>({month_str};{all_time_str})")
			else:
				lines.append(f" ▶️ {display_name} ({column}{balance_row}) = <i>{balance_str}</i>➖({month_str};{all_time_str})")
			
			# Сохраняем данные для графика (исключая группу "РАШКА")
			if group_name.upper() != "РАШКА" and balance_str != "—":
				# Извлекаем банк из названия карты (первое слово или часть до первого пробела)
				bank = card_name.split()[0].upper() if card_name.split() else card_name.upper()
				graph_data[group_name][card_name] = {
					"balance": balance_value,
					"month": month_total,
					"bank": bank
				}
	
	# Добавляем карты без группы
	if cards_without_group:
		lines.append("")
		lines.append("❇️БЕЗ ГРУППЫ:")
		for card_id, card_name, column, cell_address in cards_without_group:
			balance = balances.get(cell_address)
			balance_value = float(balance) if balance and balance != "—" else 0.0
			balance_str = balance if balance else "—"
			
			# Статистика пополнений (из batch)
			stats = replenishment_stats_dict.get(card_id, {}) if replenishment_stats_dict else {}
			month_total = stats.get("month_total", 0.0) if stats else 0.0
			all_time_total = stats.get("all_time_total", 0.0) if stats else 0.0
			
			# Форматируем числа
			month_str = f"{month_total:.2f}".rstrip('0').rstrip('.') if month_total != int(month_total) else str(int(month_total))
			all_time_str = f"{all_time_total:.2f}".rstrip('0').rstrip('.') if all_time_total != int(all_time_total) else str(int(all_time_total))
			
			lines.append(f" ▶️ {card_name} ({column}{balance_row}) = {balance_str}({month_str};{all_time_str})")
	
	# Добавляем карты без привязки к столбцу
	if cards_without_column:
		lines.append("")
		lines.append("⚠️ Карты без привязки к столбцу:")
		for card_name in cards_without_column:
			lines.append(f"💳 {card_name}")
	
	if not cards_by_group and not cards_without_group and not cards_without_column:
		lines.append("Нет данных о картах.")
	
	text = "\n".join(lines)
	total_cards_with_balance = sum(len(cards) for cards in cards_by_group.values()) + len(cards_without_group)
	logger.info(f"📊 Отправка балансов карт: групп={len(cards_by_group)}, карт с балансом={total_cards_with_balance}, без столбца={len(cards_without_column)}")
	try:
		await loading_msg.edit_text(text, reply_markup=simple_back_kb("admin:back"))
		logger.info("✅ Сообщение с балансами карт успешно отправлено")
	except Exception as e:
		logger.exception(f"❌ Ошибка отправки сообщения с балансами карт: {e}")
		# Если не удалось обновить, отправляем новое сообщение
		try:
			await msg.answer(text, reply_markup=simple_back_kb("admin:back"))
		except Exception as e2:
			logger.exception(f"❌ Ошибка отправки нового сообщения с балансами карт: {e2}")
	
	# Генерируем и отправляем график (исключая группу "РАШКА")
	if graph_data:
		try:
			chart_path = await _generate_cards_chart(graph_data)
			if chart_path:
				photo = FSInputFile(chart_path)
				await bot.send_photo(msg.chat.id, photo, reply_markup=simple_back_kb("admin:back"))
				# Удаляем временный файл
				import os
				try:
					os.remove(chart_path)
				except Exception:
					pass
		except Exception as e:
			logger.exception(f"❌ Ошибка генерации/отправки графика: {e}")


@admin_router.message(Command("stat_k"))
async def admin_stat_k_command(msg: Message, bot: Bot, state: FSMContext):
	"""Обработчик команды /stat_k для отображения балансов крипты"""
	# Очищаем состояние FSM, чтобы не мешало другим командам
	await state.clear()
	
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
		
		# Добавляем заглушки для наличных (всегда показываем БЕЛКИ и БАКСЫ)
		lines.append("")
		lines.append("<code>🐿 БЕЛКИ (BYN) = Загрузка...</code>")
		lines.append("<code>💵 БАКСЫ (USD) = Загрузка...</code>")
		
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
			base_lines
		))
	else:
		lines.append("❌ Google Sheets не настроен")
		await msg.answer("\n".join(lines), reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")


@admin_router.callback_query(F.data.startswith("user:view:"))
@admin_router.callback_query(F.data.startswith("user:view:"))
async def user_view(cb: CallbackQuery, bot: Bot):
	db = get_db()
	parts = cb.data.split(":")
	user_id = int(parts[2])
	
	# Проверяем, есть ли card_id в callback_data (формат: user:view:{user_id}:card:{card_id})
	card_id = None
	if len(parts) > 4 and parts[3] == "card":
		card_id = int(parts[4])
	
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return

	has_access = await db.is_allowed_user(user.get("tg_id"), user.get("username"))
	
	# Формируем информацию о пользователе для заголовка
	parts_text = []
	if user["full_name"]:
		parts_text.append(user["full_name"])
	if user["username"]:
		parts_text.append(f"@{user['username']}")
	if user["tg_id"]:
		parts_text.append(f"(tg_id: {user['tg_id']})")
	
	if not parts_text:
		text = f"ID: {user['user_id']}"
	else:
		text = " ".join(parts_text)
	
	if user["cards"]:
		text += "\n\nТекущие привязки:"
		for card in user["cards"]:
			# Получаем полную информацию о карте для получения группы
			card_info = await db.get_card_by_id(card["card_id"])
			group_name = ""
			if card_info and card_info.get("group_id"):
				group = await db.get_card_group(card_info["group_id"])
				if group:
					group_name = f" ({group['name']})"
			text += f"\n• {card['card_name']}{group_name}"
	else:
		text += "\n\nНе привязан к карте"

	text += f"\n\nДоступ к боту: {'✅ есть' if has_access else '❌ нет'}"
	
	text += "\n\nЧто хотите сделать?"
	
	# Формат callback_data для возврата: user:back_to_requisites:{user_id}:{card_id}
	if card_id is not None:
		# Если есть card_id - это из реквизитов, отправляем новое сообщение, чтобы не потерять реквизиты
		back_to = f"user:back_to_requisites:{user_id}:{card_id}"
		await bot.send_message(
			chat_id=cb.message.chat.id,
			text=text,
			reply_markup=user_action_kb(user_id, back_to, has_access=has_access)
		)
	else:
		# Если нет card_id - это возврат из списка карт, редактируем существующее сообщение
		back_to = "admin:back"
		await cb.message.edit_text(
			text,
			reply_markup=user_action_kb(user_id, back_to, has_access=has_access)
		)


@admin_router.callback_query(F.data.startswith("user:deal:message:"))
async def user_deal_message_start(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	try:
		user_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	user = await db.get_user_by_id(user_id)
	if not user or not user.get("tg_id"):
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	deal_id = await db.get_active_buy_deal_by_user(user["tg_id"])
	if not deal_id:
		await cb.answer("У пользователя нет активной сделки", show_alert=True)
		return
	await state.set_state(DealAlertMessageStates.waiting_message)
	await state.update_data(deal_id=deal_id, user_tg_id=user["tg_id"])
	await cb.message.answer("✍️ Введите сообщение для пользователя:")
	await cb.answer()
	await cb.answer()


@admin_router.callback_query(F.data.startswith("user:access:toggle:"))
async def user_access_toggle(cb: CallbackQuery):
	db = get_db()
	try:
		user_id = int(cb.data.split(":")[-1])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	tg_id = user.get("tg_id")
	username = user.get("username")
	if tg_id is None and not username:
		await cb.answer("Нельзя выдать доступ: нет tg_id/username", show_alert=True)
		return
	has_access = await db.is_allowed_user(tg_id, username)
	was_allowed = has_access
	if has_access:
		await db.revoke_user_access(tg_id=tg_id, username=username)
		has_access = False
		alert = "Доступ забран ✅"
	else:
		await db.grant_user_access(tg_id=tg_id, username=username)
		has_access = True
		alert = "Доступ выдан ✅"
		
		# Отправляем сообщение пользователю при выдаче доступа
		if tg_id is not None:
			from app.keyboards import client_menu_kb
			try:
				# Выставляем команды пользователю
				from aiogram.types import BotCommand, BotCommandScopeChat
				await cb.bot.set_my_commands(
					commands=[
						BotCommand(command="start", description="Меню"),
						BotCommand(command="buy", description="Купить"),
						BotCommand(command="sell", description="Продать"),
					],
					scope=BotCommandScopeChat(chat_id=tg_id),
				)
				# Отправляем сообщение с кнопками
				await cb.bot.send_message(
					chat_id=tg_id,
					text="🔒 Сервис не поддерживает подозрительные или незаконные транзакции.\n"
					     "🔞 Только для пользователей старше 18 лет.\n\n"
					     "✅Выберите нужную функцию в меню ниже, чтобы начать работу.",
					reply_markup=client_menu_kb()
				)
			except Exception as e:
				logger.warning(f"Не удалось отправить сообщение пользователю tg_id={tg_id}: {e}")

	# Обновляем сообщение: используем тот же рендер, что и в user_view (без card_id)
	parts_text = []
	if user.get("full_name"):
		parts_text.append(user["full_name"])
	if user.get("username"):
		parts_text.append(f"@{user['username']}")
	if user.get("tg_id"):
		parts_text.append(f"(tg_id: {user['tg_id']})")
	if not parts_text:
		text = f"ID: {user.get('user_id')}"
	else:
		text = " ".join(parts_text)

	# Перечитаем привязки
	user_fresh = await db.get_user_by_id(user_id)
	if user_fresh and user_fresh.get("cards"):
		text += "\n\nТекущие привязки:"
		for card in user_fresh["cards"]:
			card_info = await db.get_card_by_id(card["card_id"])
			group_name = ""
			if card_info and card_info.get("group_id"):
				group = await db.get_card_group(card_info["group_id"])
				if group:
					group_name = f" ({group['name']})"
			text += f"\n• {card['card_name']}{group_name}"
	else:
		text += "\n\nНе привязан к карте"

	text += f"\n\nДоступ к боту: {'✅ есть' if has_access else '❌ нет'}"
	text += "\n\nЧто хотите сделать?"

	try:
		await cb.message.edit_text(text, reply_markup=user_action_kb(user_id, "admin:back", has_access=has_access))
	except Exception:
		# fallback: просто ответим алертом
		pass
	await cb.answer(alert)


@admin_router.callback_query(F.data.startswith("user:back_to_requisites:"))
async def user_back_to_requisites(cb: CallbackQuery, bot: Bot):
	"""Возвращает к реквизитам карты при нажатии 'Назад' в меню пользователя"""
	db = get_db()
	# Формат: user:back_to_requisites:{user_id}:{card_id}
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка формата данных", show_alert=True)
		return
	
	user_id = int(parts[2])
	card_id = int(parts[3])
	
	# Отправляем реквизиты заново
	await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=user_id, admin_id=cb.from_user.id if cb.from_user else None)
	
	# Удаляем сообщение с меню пользователя
	try:
		await cb.message.delete()
	except Exception as e:
		logger.warning(f"⚠️ Не удалось удалить сообщение: {e}")
	
	await cb.answer()


@admin_router.callback_query(F.data.startswith("user:bind:") & ~F.data.startswith("user:bind:card:") & ~F.data.startswith("user:bind:group:"))
async def user_bind(cb: CallbackQuery):
	"""Показывает список групп карт для привязки к пользователю"""
	db = get_db()
	# Формат: user:bind:{user_id}
	user_id = int(cb.data.split(":")[-1])
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	
	# Получаем список групп карт
	groups = await db.list_card_groups()
	
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
			# Получаем полную информацию о карте для получения группы
			card_info = await db.get_card_by_id(card["card_id"])
			group_name = ""
			if card_info and card_info.get("group_id"):
				group = await db.get_card_group(card_info["group_id"])
				if group:
					group_name = f" ({group['name']})"
			text += f"\n• {card['card_name']}{group_name}"
	else:
		text += "\n\nНе привязан к карте"
	
	if not groups:
		# Если групп нет, показываем все карты сразу
		rows = await db.list_cards()
		cards = [(r[0], r[1]) for r in rows]
		if not cards:
			text += "\n\n⚠️ Нет доступных карт для привязки"
			await cb.message.edit_text(text, reply_markup=simple_back_kb(f"user:view:{user_id}"))
		else:
			text += "\n\nВыберите карту для изменения привязки:"
			selected_ids = [card["card_id"] for card in user["cards"]]
			await cb.message.edit_text(
				text,
				reply_markup=user_card_select_kb(cards, user_id, f"user:view:{user_id}", selected_ids),
			)
	else:
		text += "\n\nВыберите группу карт:"
		# Создаем клавиатуру с группами, используя специальный формат для привязки пользователю
		kb = InlineKeyboardBuilder()
		for group in groups:
			group_name = group.get("name", "")
			group_id = group.get("id")
			kb.button(text=f"📁 {group_name}", callback_data=f"user:bind:group:{user_id}:{group_id}")
		kb.button(text="📋 Без группы", callback_data=f"user:bind:group:{user_id}:0")
		kb.button(text="⬅️ Назад", callback_data=f"user:view:{user_id}")
		kb.adjust(1)
		await cb.message.edit_text(text, reply_markup=kb.as_markup())
	
	await cb.answer()


@admin_router.callback_query(F.data.startswith("user:bind:group:"))
async def user_bind_group(cb: CallbackQuery):
	"""Показывает карты из выбранной группы для привязки к пользователю"""
	db = get_db()
	# Формат: user:bind:group:{user_id}:{group_id}
	parts = cb.data.split(":")
	user_id = int(parts[3])
	group_id_str = parts[4]
	group_id = int(group_id_str) if group_id_str != "0" else None
	
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Пользователь не найден", show_alert=True)
		return
	
	# Получаем карты из группы или без группы
	if group_id:
		cards = await db.get_cards_by_group(group_id)
		group = await db.get_card_group(group_id)
		group_name = group.get("name", "Группа") if group else "Группа"
		text = f"Карты группы '{group_name}':"
	else:
		cards = await db.get_cards_without_group()
		text = "Карты вне групп:"
	
	if not cards:
		group_text = f"группы '{group_name}'" if group_id else "вне групп"
		await cb.answer(f"В {group_text} нет карт", show_alert=True)
		return
	
	# Формируем информацию о пользователе для заголовка
	parts_text = []
	if user["full_name"]:
		parts_text.append(user["full_name"])
	if user["username"]:
		parts_text.append(f"@{user['username']}")
	if user["tg_id"]:
		parts_text.append(f"(tg_id: {user['tg_id']})")
	
	if not parts_text:
		user_text = f"ID: {user['user_id']}"
	else:
		user_text = " ".join(parts_text)
	
	text = f"{user_text}\n\n{text}\n\nВыберите карту для изменения привязки:"
	
	# Преобразуем формат карт из (id, name, details) в (id, name)
	cards_list = [(c[0], c[1]) for c in cards]
	selected_ids = [card["card_id"] for card in user["cards"]]
	
	await cb.message.edit_text(
		text,
		reply_markup=user_card_select_kb(cards_list, user_id, f"user:bind:{user_id}", selected_ids),
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
	
	# Получаем tg_id и username перед удалением для отзыва доступа
	tg_id = user.get("tg_id")
	username = user.get("username")
	
	# Отзываем доступ к боту перед удалением пользователя
	if tg_id is not None or username:
		await db.revoke_user_access(tg_id=tg_id, username=username)
		logger.debug(f"Revoked access for user_id={user_id}, tg_id={tg_id}, username={username}")
	
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
async def user_bind_card(cb: CallbackQuery, bot: Bot, state: FSMContext):
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
	was_bound = card_id in bound_ids_before
	if was_bound:
		await db.unbind_user_from_card(user_id, card_id)
		action_text = f"❎ Карта {card_name if card_name else card_id} отвязана"
		alert_text = "Карта отвязана ❎"
		logger.debug(f"Unbound user_id={user_id} from card_id={card_id}")
	else:
		await db.bind_user_to_card(user_id, card_id)
		action_text = f"✅ Карта {card_name if card_name else card_id} привязана"
		alert_text = "Карта привязана ✅"
		logger.debug(f"Bound user_id={user_id} to card_id={card_id}")
		
		# Если карта была привязана, логируем доставку и отправляем реквизиты
		user = await db.get_user_by_id(user_id)
		if user and user.get("tg_id"):
			await db.log_card_delivery_by_tg(
				user["tg_id"],
				card_id,
				admin_id=cb.from_user.id if cb.from_user else None,
			)
		else:
			await db.log_card_delivery(
				user_id,
				card_id,
				admin_id=cb.from_user.id if cb.from_user else None,
			)
		
		# Если есть ожидающие реквизиты, обновляем сообщение пользователю
		if user and user.get("tg_id"):
			pending = await db.get_pending_requisites(user["tg_id"])
			if pending:
				requisites = await db.list_card_requisites(card_id)
				requisites_list = [req["requisite_text"] for req in requisites]
				user_msg = await db.get_card_user_message(card_id)
				if user_msg and user_msg.strip():
					requisites_list.append(user_msg)
				requisites_text = "\n".join(requisites_list)
				try:
					order_message = _build_payment_order_message(
						crypto_type=pending["crypto_type"],
						crypto_display=pending["crypto_display"],
						amount=pending["amount"],
						final_amount=pending["final_amount"],
						currency_symbol=pending["currency_symbol"],
						wallet_address=pending["wallet_address"],
						requisites_text=requisites_text
					)
					await bot.edit_message_text(
						chat_id=user["tg_id"],
						message_id=pending["message_id"],
						text=order_message,
						reply_markup=buy_deal_paid_kb()
					)
				except Exception as e:
					logger.warning(f"⚠️ Не удалось обновить сообщение с реквизитами: {e}")
					try:
						sent_msg = await bot.send_message(
							chat_id=user["tg_id"],
							text=order_message,
							reply_markup=buy_deal_paid_kb()
						)
						await db.update_pending_requisites_message_id(user["tg_id"], sent_msg.message_id)
						try:
							await bot.delete_message(chat_id=user["tg_id"], message_id=pending["message_id"])
						except Exception:
							pass
					except Exception:
						pass
		
		# Проверяем, есть ли в state сохраненный текст пересылаемого сообщения для отправки ссылки
		data = await state.get_data()
		forwarded_text = data.get("forwarded_message_text", "")
		if forwarded_text:
			await check_and_send_btc_address_links(bot, cb.message.chat.id, forwarded_text, user_id=user_id)
		
		# Отправляем реквизиты новой карты
		await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=user_id, admin_id=cb.from_user.id if cb.from_user else None)
	
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
			# Получаем полную информацию о карте для получения группы
			card_info = await db.get_card_by_id(card["card_id"])
			group_name = ""
			if card_info and card_info.get("group_id"):
				group = await db.get_card_group(card_info["group_id"])
				if group:
					group_name = f" ({group['name']})"
			text += f"\n• {card['card_name']}{group_name}"
	else:
		text += "\n\nНе привязан к карте"

	has_access = await db.is_allowed_user(user.get("tg_id"), user.get("username"))
	text += f"\n\nДоступ к боту: {'✅ есть' if has_access else '❌ нет'}"
	
	text += f"\n\n{action_text}"
	text += "\n\nЧто хотите сделать?"
	# Возвращаем в меню пользователя после привязки/отвязки карты
	await cb.message.edit_text(
		text,
		reply_markup=user_action_kb(user_id, "admin:back", has_access=has_access),
	)
	await cb.answer(alert_text)


# Обработчик ответа на вопрос пользователя - должен быть ПЕРЕД handle_forwarded_from_admin
@admin_router.message(QuestionReplyStates.waiting_reply)
async def question_reply_send(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик отправки ответа на вопрос пользователя"""
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	question_id = data.get("question_id")
	user_tg_id = data.get("user_tg_id")
	
	if not question_id or not user_tg_id:
		await message.answer("❌ Ошибка: не найдены данные вопроса")
		await state.clear()
		return
	
	# Получаем текст ответа
	reply_text = message.text or message.caption or ""
	if not reply_text.strip():
		await message.answer("❌ Пожалуйста, введите текст ответа.")
		return
	
	# Получаем информацию о вопросе
	db = get_db()
	question = await db.get_question_by_id(question_id)
	if not question:
		await message.answer("❌ Вопрос не найден")
		await state.clear()
		return
	
	# Сохраняем сообщение в БД
	await db.add_question_message(question_id, "admin", reply_text)
	
	# Получаем всю историю переписки
	messages = await db.get_question_messages(question_id)
	
	# Формируем полное сообщение для пользователя: информация о вопросе + история
	question_info = (
		f"❓ <b>Ваш вопрос</b>\n\n"
	)
	
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
			# Отправляем уведомление перед обновлением
			try:
				notif_msg = await bot.send_message(
					chat_id=user_tg_id,
					text="💬 <b>Новое сообщение от администратора</b>",
					parse_mode="HTML"
				)
				# Сохраняем ID уведомления
				from app.notifications import notification_ids
				notification_ids[(user_tg_id, question_id, 'question')] = notif_msg.message_id
			except Exception as e:
				# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
				logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {user_tg_id}: {e}")
			# Обновляем существующее сообщение
			try:
				await bot.edit_message_text(
					chat_id=user_tg_id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=question_user_reply_kb(question_id)
				)
				logger.info(f"✅ Сообщение обновлено пользователю {user_tg_id} по вопросу {question_id}")
			except Exception as e:
				# Если не удалось обновить, отправляем новое
				logger.warning(f"⚠️ Не удалось обновить сообщение {user_message_id}, отправляем новое: {e}")
				sent_msg = await bot.send_message(
					chat_id=user_tg_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=question_user_reply_kb(question_id)
				)
				await db.update_question_user_message_id(question_id, sent_msg.message_id)
				logger.info(f"✅ Новое сообщение отправлено пользователю {user_tg_id} по вопросу {question_id}")
		else:
			# Отправляем новое сообщение
			sent_msg = await bot.send_message(
				chat_id=user_tg_id,
				text=user_message,
				parse_mode="HTML",
				reply_markup=question_user_reply_kb(question_id)
			)
			await db.update_question_user_message_id(question_id, sent_msg.message_id)
			logger.info(f"✅ Сообщение отправлено пользователю {user_tg_id} по вопросу {question_id}")
		
		# Обновляем сообщение админа с полной историей переписки
		if admin_ids and question.get("admin_message_id"):
			try:
				user_name = question.get("user_name", "Не указано")
				user_username = question.get("user_username", "Не указано")
				question_text = question["question_text"]
				initiated_by_admin = bool(question.get("initiated_by_admin"))
				
				# Получаем информацию о последней сделке и профите пользователя
				last_order_info = ""
				try:
					user_id = await db.get_user_id_by_tg(user_tg_id)
					if user_id:
						user_data = await db.get_user_by_id(user_id)
						if user_data:
							last_order_id = user_data.get("last_order_id")
							last_order_profit = user_data.get("last_order_profit")
							
							if last_order_id:
								# Получаем информацию о последней сделке
								last_order = await db.get_order_by_id(last_order_id)
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
							monthly_profit = await db.get_user_monthly_profit(user_tg_id)
					if monthly_profit is not None:
						try:
							monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit_formatted} USD"
						except (ValueError, TypeError):
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit} USD"
				except Exception as e:
					logger.debug(f"Ошибка получения информации о последней сделке: {e}")
				
				# Формируем информацию о вопросе для админа
				if initiated_by_admin:
					admin_question_info = (
						f"💬 <b>Диалог (инициировано администратором)</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>{last_order_info}"
					)
				else:
					admin_question_info = (
						f"❓ <b>Вопрос от пользователя</b>\n\n"
						f"👤 Имя: {user_name}\n"
						f"📱 Username: @{user_username}\n"
						f"🆔 ID: <code>{user_tg_id}</code>{last_order_info}\n\n"
						f"💬 <b>Вопрос:</b>\n{question_text}"
					)
				
				# Формируем историю переписки для админа
				admin_history_lines = []
				for msg in messages:
					if msg["sender_type"] == "admin":
						admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
					else:
						admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
				
				admin_history_text = "\n\n".join(admin_history_lines)
				admin_message = admin_question_info + "\n\n" + admin_history_text
				
				# Обновляем сообщение админа
				from app.keyboards import question_reply_kb
				await bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=question["admin_message_id"],
					text=admin_message,
					parse_mode="HTML",
					reply_markup=question_reply_kb(question_id)
				)
				logger.info(f"✅ Сообщение админа обновлено с историей переписки для вопроса {question_id}")
				
				# Отправляем временное уведомление админу
				import asyncio
				notif_msg = await bot.send_message(
					chat_id=admin_ids[0],
					text="✅ Сообщение отправлено пользователю"
				)
				await asyncio.sleep(2)
				try:
					await bot.delete_message(chat_id=admin_ids[0], message_id=notif_msg.message_id)
				except:
					pass
			except Exception as e:
				logger.error(f"❌ Ошибка обновления сообщения админа: {e}", exc_info=True)
		
		# Удаляем сообщение админа после отправки, чтобы не захламлять чат
		try:
			await message.delete()
		except Exception as e:
			logger.debug(f"Не удалось удалить сообщение админа: {e}")
	except Exception as e:
		logger.error(f"❌ Ошибка отправки ответа пользователю {user_tg_id}: {e}", exc_info=True)
		await message.answer(f"❌ Ошибка отправки ответа: {str(e)}")
	
	# Очищаем состояние
	await state.clear()


# Обработчик отправки сообщения админом по обычной заявке - должен быть ПЕРЕД handle_forwarded_from_admin
@admin_router.message(OrderMessageStates.waiting_message)
async def order_message_send(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик отправки сообщения админом по обычной заявке"""
	logger.info(f"🔵 ORDER_MESSAGE_SEND: Получено сообщение message_id={message.message_id}, text='{message.text or message.caption or ''}'")
	
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		logger.warning(f"🔵 ORDER_MESSAGE_SEND: Пользователь {message.from_user.id} не является админом")
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	order_id = data.get("order_id")
	user_tg_id = data.get("user_tg_id")
	
	if not order_id or not user_tg_id:
		await message.answer("❌ Ошибка: не найдены данные заявки")
		await state.clear()
		return
	
	# Получаем текст сообщения
	message_text = message.text or message.caption or ""
	if not message_text.strip():
		await message.answer("❌ Пожалуйста, введите текст сообщения.")
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await message.answer("❌ Заявка не найдена")
		await state.clear()
		return
	
	# Сохраняем сообщение в БД
	await db.add_buy_order_message(order_id, "admin", message_text)
	
	# Получаем всю историю переписки
	messages = await db.get_buy_order_messages(order_id)
	
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
	
	# Формируем полное сообщение для пользователя: информация о заявке + история
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
	
	# Отправляем или обновляем сообщение пользователю
	from app.keyboards import order_user_reply_kb
	try:
		user_message_id = order.get("user_message_id")
		if user_message_id:
			# Отправляем уведомление перед обновлением
			try:
				notif_msg = await bot.send_message(
					chat_id=user_tg_id,
					text="💬 <b>Новое сообщение от администратора</b>",
					parse_mode="HTML"
				)
				# Сохраняем ID уведомления
				from app.notifications import notification_ids
				notification_ids[(user_tg_id, order_id, 'order')] = notif_msg.message_id
			except Exception as e:
				# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
				logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {user_tg_id}: {e}")
			# Обновляем существующее сообщение
			try:
				await bot.edit_message_text(
					chat_id=user_tg_id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=order_user_reply_kb(order_id)
				)
				logger.info(f"✅ Сообщение обновлено пользователю {user_tg_id} по заявке {order_id}")
			except Exception as e:
				# Если не удалось обновить, отправляем новое
				logger.warning(f"⚠️ Не удалось обновить сообщение {user_message_id}, отправляем новое: {e}")
				sent_msg = await bot.send_message(
					chat_id=user_tg_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=order_user_reply_kb(order_id)
				)
				await db.update_order_user_message_id(order_id, sent_msg.message_id)
				logger.info(f"✅ Новое сообщение отправлено пользователю {user_tg_id} по заявке {order_id}")
		else:
			# Отправляем новое сообщение
			sent_msg = await bot.send_message(
				chat_id=user_tg_id,
				text=user_message,
				parse_mode="HTML",
				reply_markup=order_user_reply_kb(order_id)
			)
			await db.update_order_user_message_id(order_id, sent_msg.message_id)
			logger.info(f"✅ Сообщение отправлено пользователю {user_tg_id} по заявке {order_id}")
		
		# Обновляем сообщение админа с полной историей переписки
		logger.info(f"🔵 ORDER_MESSAGE_SEND: Проверка обновления сообщения админа: admin_ids={admin_ids}, admin_message_id={order.get('admin_message_id')}")
		if admin_ids and order.get("admin_message_id"):
			try:
				user_name = order.get("user_name", "Не указано")
				user_username = order.get("user_username", "Не указано")
				
				# Получаем долг для этой заявки
				debt = await db.get_debt_by_order_id(order_id)
				debt_info = ""
				if debt:
					debt_info = f"\n💳 Долг по этой сделке: {int(debt['debt_amount'])} {debt['currency_symbol']}"
				
				# Получаем общий долг пользователя
				user_debts = await db.get_user_total_debt(order["user_tg_id"])
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
					user_tg_id = order.get("user_tg_id")
					user_id = await db.get_user_id_by_tg(user_tg_id)
					if user_id:
						user_data = await db.get_user_by_id(user_id)
						if user_data:
							last_order_id = user_data.get("last_order_id")
							last_order_profit = user_data.get("last_order_profit")
							
							if last_order_id:
								# Получаем информацию о последней сделке
								last_order = await db.get_order_by_id(last_order_id)
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
							monthly_profit = await db.get_user_monthly_profit(user_tg_id)
					if monthly_profit is not None:
						try:
							monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit_formatted} USD"
						except (ValueError, TypeError):
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit} USD"
				except Exception as e:
					logger.debug(f"Ошибка получения информации о последней сделке: {e}", exc_info=True)
				
				# Формируем информацию о заявке для админа
				admin_order_info = (
					f"Номер заявки за сегодня: {order_number}\n"
					f"Имя пользователя: {user_name or 'Не указано'}\n"
					f"Username: @{user_username}\n"
					f"🆔 ID: <code>{order.get('user_tg_id')}</code>{last_order_info}\n\n"
					f"Количество монет: {amount_str} {crypto_display}\n"
					f"Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
					f"Адрес кошелька: <code>{order.get('wallet_address', '')}</code>{debt_info}{total_debt_info}"
				)
				
				# Формируем историю переписки для админа
				admin_history_lines = []
				for msg in messages:
					if msg["sender_type"] == "admin":
						admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
					else:
						admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
				
				admin_history_text = "\n\n".join(admin_history_lines)
				admin_message = admin_order_info + "\n\n" + admin_history_text
				
				# Обновляем сообщение админа
				from app.keyboards import order_action_kb
				# Используем расширенную клавиатуру, если есть переписка
				is_expanded = len(messages) > 0
				
				logger.info(f"🔵 ORDER_MESSAGE_SEND: Обновление сообщения админа: chat_id={admin_ids[0]}, message_id={order['admin_message_id']}, messages_count={len(messages)}")
				# Пытаемся обновить как caption (для фото/документа), если не получится - как текст
				try:
					await bot.edit_message_caption(
						chat_id=admin_ids[0],
						message_id=order["admin_message_id"],
						caption=admin_message,
						parse_mode="HTML",
						reply_markup=order_action_kb(order_id, expanded=is_expanded)
					)
				except Exception as e:
					# Если не получилось (это текстовое сообщение), используем edit_text
					logger.debug(f"Не удалось обновить caption, пробуем edit_text: {e}")
					await bot.edit_message_text(
						chat_id=admin_ids[0],
						message_id=order["admin_message_id"],
						text=admin_message,
						parse_mode="HTML",
						reply_markup=order_action_kb(order_id, expanded=is_expanded)
					)
				logger.info(f"✅ Сообщение админа обновлено с историей переписки для заявки {order_id}")
				
				# Отправляем временное уведомление админу
				import asyncio
				notif_msg = await bot.send_message(
					chat_id=admin_ids[0],
					text="✅ Сообщение отправлено пользователю"
				)
				await asyncio.sleep(2)
				try:
					await bot.delete_message(chat_id=admin_ids[0], message_id=notif_msg.message_id)
				except:
					pass
			except Exception as e:
				logger.error(f"❌ Ошибка обновления сообщения админа: {e}", exc_info=True)
		else:
			logger.warning(f"⚠️ ORDER_MESSAGE_SEND: Не удалось обновить сообщение админа: admin_ids={admin_ids}, admin_message_id={order.get('admin_message_id')}")
	except Exception as e:
		logger.error(f"❌ Ошибка отправки сообщения пользователю: {e}", exc_info=True)
		await message.answer(f"❌ Ошибка отправки сообщения: {str(e)}")
	
	# Удаляем сообщение админа после отправки
	from app.main import delete_user_message
	await delete_user_message(message)

@admin_router.message(OrderEditStates.waiting_amount)
async def order_edit_amount_save(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик сохранения новой суммы сделки"""
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Проверяем, не является ли это командой
	if message.text and message.text.startswith("/"):
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	order_id = data.get("order_id")
	current_amount = data.get("current_amount_currency", 0)
	currency_symbol = data.get("currency_symbol", "₽")
	
	if not order_id:
		await message.answer("❌ Ошибка: не найден ID заявки")
		await state.clear()
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await message.answer("❌ Заявка не найдена")
		await state.clear()
		return
	
	# Валидируем введенную сумму
	try:
		new_amount_str = message.text.strip().replace(",", ".")
		new_amount = float(new_amount_str)
		if new_amount <= 0:
			await message.answer(f"❌ Сумма должна быть больше нуля. Текущая сумма: {int(current_amount)} {currency_symbol}\nВведите новую сумму:")
			return
	except ValueError:
		await message.answer(f"❌ Неверный формат суммы. Текущая сумма: {int(current_amount)} {currency_symbol}\nВведите число (например: 5000):")
		return
	
	# Обновляем сумму в БД
	await db._db.execute(
		"UPDATE orders SET amount_currency = ? WHERE id = ?",
		(new_amount, order_id)
	)
	await db._db.commit()
	
	logger.info(f"✅ Сумма сделки {order_id} обновлена: {int(current_amount)} {currency_symbol} -> {int(new_amount)} {currency_symbol}")
	
	# Обновляем сообщение админа с новыми данными
	await _update_admin_order_message(bot, order_id, db, admin_ids)
	# Обновляем сообщение пользователя с новыми данными
	await _update_user_order_message(bot, order_id, db)
	
	# Очищаем состояние
	await state.clear()
	
	# Удаляем сообщение админа
	from app.main import delete_user_message
	await delete_user_message(message)
	
	await message.answer(f"✅ Сумма сделки обновлена: {int(new_amount)} {currency_symbol}")

@admin_router.message(OrderEditStates.waiting_crypto_amount)
async def order_edit_crypto_amount_save(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик сохранения нового количества крипты"""
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Проверяем, не является ли это командой
	if message.text and message.text.startswith("/"):
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	order_id = data.get("order_id")
	current_crypto_amount = data.get("current_crypto_amount", 0)
	crypto_display = data.get("crypto_display", "")
	
	if not order_id:
		await message.answer("❌ Ошибка: не найден ID заявки")
		await state.clear()
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await message.answer("❌ Заявка не найдена")
		await state.clear()
		return
	
	# Валидируем введенное количество
	try:
		new_amount_str = message.text.strip().replace(",", ".")
		new_crypto_amount = float(new_amount_str)
		if new_crypto_amount <= 0:
			current_str = f"{current_crypto_amount:.8f}".rstrip('0').rstrip('.') if current_crypto_amount < 1 else f"{current_crypto_amount:.2f}".rstrip('0').rstrip('.')
			await message.answer(f"❌ Количество должно быть больше нуля. Текущее количество: {current_str} {crypto_display}\nВведите новое количество:")
			return
	except ValueError:
		current_str = f"{current_crypto_amount:.8f}".rstrip('0').rstrip('.') if current_crypto_amount < 1 else f"{current_crypto_amount:.2f}".rstrip('0').rstrip('.')
		await message.answer(f"❌ Неверный формат количества. Текущее количество: {current_str} {crypto_display}\nВведите число (например: 0.008 или 100):")
		return
	
	# Обновляем количество крипты в БД
	await db._db.execute(
		"UPDATE orders SET amount = ? WHERE id = ?",
		(new_crypto_amount, order_id)
	)
	await db._db.commit()
	
	current_str = f"{current_crypto_amount:.8f}".rstrip('0').rstrip('.') if current_crypto_amount < 1 else f"{current_crypto_amount:.2f}".rstrip('0').rstrip('.')
	new_str = f"{new_crypto_amount:.8f}".rstrip('0').rstrip('.') if new_crypto_amount < 1 else f"{new_crypto_amount:.2f}".rstrip('0').rstrip('.')
	logger.info(f"✅ Количество крипты сделки {order_id} обновлено: {current_str} {crypto_display} -> {new_str} {crypto_display}")
	
	# Обновляем сообщение админа с новыми данными
	await _update_admin_order_message(bot, order_id, db, admin_ids)
	# Обновляем сообщение пользователя с новыми данными
	await _update_user_order_message(bot, order_id, db)
	
	# Очищаем состояние
	await state.clear()
	
	# Удаляем сообщение админа
	from app.main import delete_user_message
	await delete_user_message(message)
	
	await message.answer(f"✅ Количество крипты обновлено: {new_str} {crypto_display}")

@admin_router.callback_query(F.data.startswith("order:debt:"))
async def order_debt_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик начала добавления долга"""
	# Формат: order:debt:{order_id}
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		order_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await cb.answer("Заявка не найдена", show_alert=True)
		return
	
	# Проверяем, не завершена ли заявка
	if order.get("completed_at"):
		await cb.answer("Заявка уже завершена", show_alert=True)
		return
	
	# Сохраняем данные в FSM
	await state.update_data(
		order_id=order_id,
		user_tg_id=order["user_tg_id"]
	)
	
	# Переводим в состояние ожидания выбора валюты
	await state.set_state(OrderEditStates.waiting_debt_currency)
	
	# Создаем клавиатуру для выбора валюты
	from aiogram.utils.keyboard import InlineKeyboardBuilder
	kb = InlineKeyboardBuilder()
	kb.button(text="Бел. руб (BYN)", callback_data="debt:currency:BYN")
	kb.button(text="Рос. руб (RUB)", callback_data="debt:currency:RUB")
	kb.adjust(1)
	
	# Обновляем сообщение админа
	if cb.message.photo:
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + "\n\n💳 Выберите валюту долга:",
			parse_mode="HTML",
			reply_markup=kb.as_markup()
		)
	elif cb.message.document:
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + "\n\n💳 Выберите валюту долга:",
			parse_mode="HTML",
			reply_markup=kb.as_markup()
		)
	else:
		await cb.message.edit_text(
			cb.message.text + "\n\n💳 Выберите валюту долга:",
			parse_mode="HTML",
			reply_markup=kb.as_markup()
		)
	await cb.answer()

@admin_router.callback_query(F.data.startswith("debt:currency:"))
async def order_debt_currency_selected(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик выбора валюты долга"""
	# Формат: debt:currency:{currency}
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	currency_symbol = parts[2]
	if currency_symbol not in ["BYN", "RUB"]:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Сохраняем валюту в FSM
	await state.update_data(debt_currency=currency_symbol)
	
	# Переводим в состояние ожидания суммы долга
	await state.set_state(OrderEditStates.waiting_debt_amount)
	
	# Получаем информацию о заявке для восстановления клавиатуры
	data = await state.get_data()
	order_id = data.get("order_id")
	if not order_id:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Восстанавливаем клавиатуру заявки
	from app.keyboards import order_action_kb
	db = get_db()
	messages = await db.get_order_messages(order_id)
	is_expanded = len(messages) > 0
	
	# Обновляем сообщение админа
	if cb.message.photo:
		current_caption = cb.message.caption or ""
		# Удаляем строку о выборе валюты
		caption_lines = current_caption.split("\n")
		if caption_lines and "Выберите валюту долга" in caption_lines[-1]:
			caption_lines = caption_lines[:-1]
		current_caption = "\n".join(caption_lines)
		await cb.message.edit_caption(
			caption=current_caption + f"\n\n💳 Валюта: {currency_symbol}\nВведите сумму долга:",
			parse_mode="HTML",
			reply_markup=order_action_kb(order_id, expanded=is_expanded)
		)
	elif cb.message.document:
		current_caption = cb.message.caption or ""
		caption_lines = current_caption.split("\n")
		if caption_lines and "Выберите валюту долга" in caption_lines[-1]:
			caption_lines = caption_lines[:-1]
		current_caption = "\n".join(caption_lines)
		await cb.message.edit_caption(
			caption=current_caption + f"\n\n💳 Валюта: {currency_symbol}\nВведите сумму долга:",
			parse_mode="HTML",
			reply_markup=order_action_kb(order_id, expanded=is_expanded)
		)
	else:
		text_lines = cb.message.text.split("\n")
		if text_lines and "Выберите валюту долга" in text_lines[-1]:
			text_lines = text_lines[:-1]
		text = "\n".join(text_lines)
		await cb.message.edit_text(
			text + f"\n\n💳 Валюта: {currency_symbol}\nВведите сумму долга:",
			parse_mode="HTML",
			reply_markup=order_action_kb(order_id, expanded=is_expanded)
		)
	await cb.answer()

@admin_router.message(OrderEditStates.waiting_debt_amount)
async def order_debt_amount_save(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик сохранения суммы долга"""
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
	
	# Проверяем, не является ли это командой
	if message.text and message.text.startswith("/"):
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	order_id = data.get("order_id")
	user_tg_id = data.get("user_tg_id")
	debt_currency = data.get("debt_currency")
	
	if not order_id or not user_tg_id or not debt_currency:
		await message.answer("❌ Ошибка: не найдены данные заявки")
		await state.clear()
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await message.answer("❌ Заявка не найдена")
		await state.clear()
		return
	
	# Валидируем введенную сумму
	try:
		debt_amount_str = message.text.strip().replace(",", ".")
		debt_amount = float(debt_amount_str)
		if debt_amount <= 0:
			await message.answer(f"❌ Сумма должна быть больше нуля. Введите сумму долга:")
			return
	except ValueError:
		await message.answer(f"❌ Неверный формат суммы. Введите число (например: 5000):")
		return
	
	# Проверяем соответствие валюты долга валюте сделки
	currency_symbol = order.get("currency_symbol", "₽")
	if currency_symbol in ("Br", "BYN"):
		order_currency_code = "BYN"
	elif currency_symbol in ("₽", "RUB"):
		order_currency_code = "RUB"
	else:
		order_currency_code = currency_symbol
	
	if debt_currency != order_currency_code:
		await message.answer("❌ Валюта долга должна совпадать с валютой сделки.")
		return
	
	# Проверяем, есть ли уже долг для этой заявки
	existing_debt = await db.get_debt_by_order_id(order_id)
	
	# Пересчитываем сумму к оплате с учетом долга
	base_amount_currency = order.get("amount_currency", 0)
	if existing_debt and existing_debt.get("currency_symbol") == debt_currency:
		try:
			base_amount_currency = float(base_amount_currency) + float(existing_debt.get("debt_amount", 0))
		except (ValueError, TypeError):
			pass
	
	if debt_amount > base_amount_currency:
		await message.answer("❌ Долг не может быть больше суммы сделки.")
		return
	
	new_amount_currency = base_amount_currency - debt_amount
	await db._db.execute(
		"UPDATE orders SET amount_currency = ? WHERE id = ?",
		(new_amount_currency, order_id)
	)
	await db._db.commit()
	if existing_debt:
		# Обновляем существующий долг
		await db._db.execute(
			"UPDATE debts SET debt_amount = ?, currency_symbol = ? WHERE order_id = ?",
			(debt_amount, debt_currency, order_id)
		)
		await db._db.commit()
		logger.info(f"✅ Долг для заявки {order_id} обновлен: {int(debt_amount)} {debt_currency}")
		await message.answer(f"✅ Долг обновлен: {int(debt_amount)} {debt_currency}")
	else:
		# Создаем новый долг
		await db.create_debt(order_id, user_tg_id, debt_amount, debt_currency)
		logger.info(f"✅ Долг для заявки {order_id} создан: {int(debt_amount)} {debt_currency}")
		await message.answer(f"✅ Долг добавлен: {int(debt_amount)} {debt_currency}")
	
	# Обновляем сообщение админа с новыми данными
	await _update_admin_order_message(bot, order_id, db, admin_ids)
	# Обновляем сообщение пользователя с новыми данными
	await _update_user_order_message(bot, order_id, db)
	
	# Очищаем состояние
	await state.clear()
	
	# Удаляем сообщение админа
	from app.main import delete_user_message
	await delete_user_message(message)

async def _update_admin_order_message(bot: Bot, order_id: int, db, admin_ids: List[int]):
	"""Вспомогательная функция для обновления сообщения админа с данными заявки"""
	try:
		order = await db.get_order_by_id(order_id)
		if not order or not admin_ids:
			return
		
		# Получаем историю переписки
		messages = await db.get_order_messages(order_id)
		
		# Формируем информацию о заявке
		order_number = order.get("order_number", 0)
		user_name = order.get("user_name", "Не указано")
		user_username = order.get("user_username", "Не указано")
		crypto_display = order.get("crypto_display", "")
		amount = order.get("amount", 0)
		amount_currency = order.get("amount_currency", 0)
		currency_symbol = order.get("currency_symbol", "₽")
		wallet_address = order.get("wallet_address", "")
		card_name = ""
		group_name = ""
		user_cards = await db.get_cards_for_user_tg(order["user_tg_id"])
		if user_cards:
			card = user_cards[0]
			card_id = card["card_id"]
			card_info = await db.get_card_by_id(card_id)
			card_name = (card_info.get("name") if card_info else None) or card.get("card_name") or card.get("name") or ""
			if card_info and card_info.get("group_id"):
				group = await db.get_card_group_by_id(card_info["group_id"])
				group_name = group.get("name") if group else ""
		if card_name:
			label = f"{group_name} ({card_name})" if group_name else card_name
			pay_card_info = f"\n💳 Карта для оплаты: {label}"
		else:
			pay_card_info = ""
		
		amount_str = f"{amount:.8f}".rstrip('0').rstrip('.') if amount < 1 else f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Получаем информацию о последней сделке и профите пользователя
		last_order_info = ""
		try:
			user_id = await db.get_user_id_by_tg(order["user_tg_id"])
			if user_id:
				user_data = await db.get_user_by_id(user_id)
				if user_data:
					last_order_id = user_data.get("last_order_id")
					last_order_profit = user_data.get("last_order_profit")
					
					if last_order_id:
						# Получаем информацию о последней сделке
						last_order = await db.get_order_by_id(last_order_id)
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
					monthly_profit = await db.get_user_monthly_profit(order["user_tg_id"])
					if monthly_profit is not None:
						try:
							monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit_formatted} USD"
						except (ValueError, TypeError):
							last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit} USD"
		except Exception as e:
			logger.debug(f"Ошибка получения информации о последней сделке: {e}", exc_info=True)
		
		# Получаем долг для этой заявки
		debt = await db.get_debt_by_order_id(order_id)
		debt_info = ""
		if debt:
			debt_info = f"\n💳 Долг по этой сделке: {int(debt['debt_amount'])} {debt['currency_symbol']}"
		
		# Получаем общий долг пользователя
		user_debts = await db.get_user_total_debt(order["user_tg_id"])
		total_debt_info = ""
		if user_debts:
			debt_lines = []
			for curr, debt_sum in user_debts.items():
				debt_lines.append(f"{int(debt_sum)} {curr}")
			if debt_lines:
				total_debt_info = f"\n💳 Общий долг пользователя: {', '.join(debt_lines)}"
		
		admin_order_info = (
			f"Номер заявки за сегодня: {order_number}\n"
			f"Имя пользователя: {user_name or 'Не указано'}\n"
			f"Username: @{user_username}\n"
			f"🆔 ID: <code>{order['user_tg_id']}</code>{last_order_info}\n\n"
			f"Количество монет: {amount_str} {crypto_display}\n"
			f"Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
			f"Адрес кошелька: <code>{wallet_address}</code>{pay_card_info}{debt_info}{total_debt_info}"
		)
		
		# Формируем историю переписки
		admin_history_lines = []
		for msg in messages:
			if msg["sender_type"] == "admin":
				admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
			else:
				admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
		
		admin_history_text = "\n\n".join(admin_history_lines)
		admin_message = admin_order_info + ("\n\n" + admin_history_text if admin_history_text else "")
		
		# Обновляем сообщение админа
		from app.keyboards import order_action_kb
		is_expanded = len(messages) > 0
		
		if order.get("admin_message_id"):
			try:
				await bot.edit_message_caption(
					chat_id=admin_ids[0],
					message_id=order["admin_message_id"],
					caption=admin_message,
					parse_mode="HTML",
					reply_markup=order_action_kb(order_id, expanded=is_expanded)
				)
			except Exception:
				await bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=order["admin_message_id"],
					text=admin_message,
					parse_mode="HTML",
					reply_markup=order_action_kb(order_id, expanded=is_expanded)
				)
	except Exception as e:
		logger.error(f"❌ Ошибка обновления сообщения админа: {e}", exc_info=True)


async def _update_user_order_message(bot: Bot, order_id: int, db):
	"""Вспомогательная функция для обновления сообщения пользователя с данными заявки"""
	try:
		order = await db.get_order_by_id(order_id)
		if not order:
			return
		
		user_message_id = order.get("user_message_id") or order.get("order_message_id")
		if not user_message_id:
			return
		
		user_tg_id = order["user_tg_id"]
		crypto_display = order.get("crypto_display", "")
		crypto_type = order.get("crypto_type", "")
		amount = order.get("amount", 0)
		amount_currency = order.get("amount_currency", 0)
		currency_symbol = order.get("currency_symbol", "₽")
		wallet_address = order.get("wallet_address", "")
		
		amount_str = f"{amount:.8f}".rstrip('0').rstrip('.') if amount < 1 else f"{amount:.2f}".rstrip('0').rstrip('.')
		
		# Определяем короткое название криптовалюты
		if crypto_type == "XMR":
			crypto_short = "xmr"
		elif crypto_type == "USDT":
			crypto_short = "usdt"
		else:
			crypto_short = crypto_type.lower()
		
		# Получаем реквизиты пользователя
		user_cards = await db.get_cards_for_user_tg(user_tg_id)
		requisites_text = ""
		pay_card_info = ""
		
		if user_cards:
			# Берем первую карту пользователя
			card = user_cards[0]
			card_id = card["card_id"]
			card_info = await db.get_card_by_id(card_id)
			card_name = (card_info.get("name") if card_info else None) or card.get("card_name") or card.get("name") or ""
			group_name = ""
			if card_info and card_info.get("group_id"):
				group = await db.get_card_group_by_id(card_info["group_id"])
				group_name = group.get("name") if group else ""
			if card_name:
				label = f"{group_name} ({card_name})" if group_name else card_name
				pay_card_info = f"\n💳 Карта для оплаты: {label}"
			
			# Получаем реквизиты из таблицы card_requisites
			requisites = await db.list_card_requisites(card_id)
			
			# Формируем текст реквизитов
			requisites_list = []
			for req in requisites:
				requisites_list.append(req["requisite_text"])
			
			# Добавляем user_message, если есть
			if card.get("user_message") and card["user_message"].strip():
				requisites_list.append(card["user_message"])
			
			if requisites_list:
				requisites_text = "\n".join(requisites_list)
		
		# Формируем обновленное сообщение заявки
		order_message = (
			f"☑️Заявка успешно создана.\n"
			f"Вы получаете: {amount_str} {crypto_short}\n"
			f"{crypto_display} - {crypto_type}-адрес: {wallet_address}\n\n"
			f"💳Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
			f"Реквизиты для оплаты:{pay_card_info}\n\n"
		)
		
		if requisites_text:
			order_message += requisites_text + "\n\n"
		else:
			order_message += "Реквизиты не найдены. Идет загрузка, ожидайте.\n\n"
		
		order_message += f"⏰Заявка действительна: 15 минут\n"
		order_message += f"✅После оплаты необходимо нажать на кнопку 'ОПЛАТА СОВЕРШЕНА'"
		
		from app.keyboards import buy_payment_confirmed_kb
		try:
			await bot.edit_message_text(
				chat_id=user_tg_id,
				message_id=user_message_id,
				text=order_message,
				reply_markup=buy_payment_confirmed_kb()
			)
		except Exception as e:
			logger.warning(f"⚠️ Не удалось обновить сообщение пользователя: {e}")
		
		# Обновляем сообщение подтверждения скрина (если есть)
		proof_confirmation_message_id = order.get("proof_confirmation_message_id")
		if proof_confirmation_message_id:
			proof_details = (
				f"\n\nКоличество монет: {amount_str} {crypto_display}\n"
				f"Сумма к оплате: {int(amount_currency)} {currency_symbol}\n"
				f"Адрес кошелька: {wallet_address}"
			)
			proof_text = (
				"✅ Спасибо! Ваш скриншот/чек получен. Ожидайте зачисления средств на указанный адрес кошелька."
				+ proof_details
			)
			try:
				await bot.edit_message_text(
					chat_id=user_tg_id,
					message_id=proof_confirmation_message_id,
					text=proof_text
				)
			except Exception:
				try:
					sent_msg = await bot.send_message(chat_id=user_tg_id, text=proof_text)
					await db._db.execute(
						"UPDATE orders SET proof_confirmation_message_id = ? WHERE id = ?",
						(sent_msg.message_id, order_id)
					)
					await db._db.commit()
				except Exception:
					pass
	except Exception as e:
		logger.error(f"❌ Ошибка обновления сообщения пользователю: {e}", exc_info=True)

# Обработчики для сделок на продажу - должны быть ПЕРЕД handle_forwarded_from_admin
@admin_router.message(SellOrderMessageStates.waiting_message)
async def sell_order_message_send(message: Message, state: FSMContext, bot: Bot):
	"""Обработчик отправки сообщения админом по сделке"""
	logger.info(f"🔵 SELL_ORDER_MESSAGE_SEND: Получено сообщение message_id={message.message_id}, text='{message.text or message.caption or ''}'")
	
	# Проверяем, что это админ
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		logger.warning(f"🔵 SELL_ORDER_MESSAGE_SEND: Пользователь {message.from_user.id} не является админом")
		return
	
	# Получаем данные из FSM
	data = await state.get_data()
	order_id = data.get("sell_order_id")
	user_tg_id = data.get("user_tg_id")
	
	if not order_id or not user_tg_id:
		await message.answer("❌ Ошибка: не найдены данные сделки")
		await state.clear()
		return
	
	# Получаем текст сообщения
	message_text = message.text or message.caption or ""
	if not message_text.strip():
		await message.answer("❌ Пожалуйста, введите текст сообщения.")
		return
	
	# Получаем информацию о сделке
	db = get_db()
	order = await db.get_sell_order_by_id(order_id)
	if not order:
		await message.answer("❌ Сделка не найдена")
		await state.clear()
		return
	
	# Сохраняем сообщение в БД
	await db.add_order_message(order_id, "admin", message_text)
	
	# Получаем всю историю переписки
	messages = await db.get_order_messages(order_id)
	
	# Формируем информацию о сделке
	order_number = order["order_number"]
	crypto_display = order["crypto_display"]
	amount = order["amount"]
	
	# Форматируем сумму
	if amount < 1:
		amount_str = f"{amount:.8f}".rstrip('0').rstrip('.')
	else:
		amount_str = f"{amount:.2f}".rstrip('0').rstrip('.')
	
	# Формируем полное сообщение для пользователя: информация о сделке + история
	order_info = (
		f"💰 <b>Заявка на продажу #{order_number}</b>\n\n"
		f"💵 Криптовалюта: {crypto_display}\n"
		f"💸 Сумма: {amount_str} {crypto_display}\n"
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
	
	# Отправляем или обновляем сообщение пользователю
	from app.keyboards import sell_order_user_reply_kb
	try:
		user_message_id = order.get("user_message_id")
		if user_message_id:
			# Отправляем уведомление перед обновлением
			try:
				notif_msg = await bot.send_message(
					chat_id=user_tg_id,
					text="💬 <b>Новое сообщение от администратора</b>",
					parse_mode="HTML"
				)
				# Сохраняем ID уведомления
				from app.notifications import notification_ids
				notification_ids[(user_tg_id, order_id, 'sell_order')] = notif_msg.message_id
			except Exception as e:
				# Если не удалось отправить уведомление (сетевая ошибка и т.д.), продолжаем работу
				logger.warning(f"⚠️ Не удалось отправить уведомление пользователю {user_tg_id}: {e}")
			# Обновляем существующее сообщение
			try:
				await bot.edit_message_text(
					chat_id=user_tg_id,
					message_id=user_message_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=sell_order_user_reply_kb(order_id)
				)
				logger.info(f"✅ Сообщение обновлено пользователю {user_tg_id} по сделке {order_id}")
			except Exception as e:
				# Если не удалось обновить, отправляем новое
				logger.warning(f"⚠️ Не удалось обновить сообщение {user_message_id}, отправляем новое: {e}")
				sent_msg = await bot.send_message(
					chat_id=user_tg_id,
					text=user_message,
					parse_mode="HTML",
					reply_markup=sell_order_user_reply_kb(order_id)
				)
				await db.update_sell_order_user_message_id(order_id, sent_msg.message_id)
				logger.info(f"✅ Новое сообщение отправлено пользователю {user_tg_id} по сделке {order_id}")
		else:
			# Отправляем новое сообщение
			sent_msg = await bot.send_message(
				chat_id=user_tg_id,
				text=user_message,
				parse_mode="HTML",
				reply_markup=sell_order_user_reply_kb(order_id)
			)
			await db.update_sell_order_user_message_id(order_id, sent_msg.message_id)
			logger.info(f"✅ Сообщение отправлено пользователю {user_tg_id} по сделке {order_id}")
		
		# Обновляем сообщение админа с полной историей переписки
		if admin_ids and order.get("admin_message_id"):
			try:
				user_name = order.get("user_name", "Не указано")
				user_username = order.get("user_username", "Не указано")
				amount_currency = order.get("amount_currency", 0)
				currency_symbol = order.get("currency_symbol", "₽")
				
				# Формируем информацию о сделке для админа
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
				
				# Формируем историю переписки для админа
				admin_history_lines = []
				for msg in messages:
					if msg["sender_type"] == "admin":
						admin_history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
					else:
						admin_history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
				
				admin_history_text = "\n\n".join(admin_history_lines)
				admin_message = admin_order_info + "\n\n" + admin_history_text
				
				# Обновляем сообщение админа
				from app.keyboards import sell_order_admin_kb
				await bot.edit_message_text(
					chat_id=admin_ids[0],
					message_id=order["admin_message_id"],
					text=admin_message,
					parse_mode="HTML",
					reply_markup=sell_order_admin_kb(order_id)
				)
				logger.info(f"✅ Сообщение админа обновлено с историей переписки для сделки {order_id}")
				
				# Отправляем временное уведомление админу
				import asyncio
				notif_msg = await bot.send_message(
					chat_id=admin_ids[0],
					text="✅ Сообщение отправлено пользователю"
				)
				await asyncio.sleep(2)
				try:
					await bot.delete_message(chat_id=admin_ids[0], message_id=notif_msg.message_id)
				except:
					pass
			except Exception as e:
				logger.error(f"❌ Ошибка обновления сообщения админа: {e}", exc_info=True)
		
		# Удаляем сообщение админа после отправки, чтобы не захламлять чат
		try:
			await message.delete()
		except Exception as e:
			logger.debug(f"Не удалось удалить сообщение админа: {e}")
	except Exception as e:
		logger.error(f"❌ Ошибка отправки сообщения пользователю {user_tg_id}: {e}", exc_info=True)
		await message.answer(f"❌ Ошибка отправки сообщения: {str(e)}")
	
	# Очищаем состояние
	await state.clear()


# Обработчик ввода количества криптовалюты - должен быть ПЕРЕД handle_forwarded_from_admin

# Handle any message and process forwarding logic for admins
# Важно: этот обработчик должен быть ПОСЛЕ обработчика editing_crypto_amount
# чтобы не перехватывать сообщения в состоянии редактирования
# ВАЖНО: Используем фильтр чтобы НЕ перехватывать команды
@admin_router.message()
async def handle_forwarded_from_admin(message: Message, bot: Bot, state: FSMContext):
	# Пропускаем команды - они обрабатываются отдельными обработчиками
	# Проверяем это ПЕРВЫМ делом, до любых других проверок
	if message.text and message.text.startswith("/"):
		logger.debug(f"⚠️ Универсальный обработчик: пропускаем команду '{message.text}'")
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
		# Если это не пересылка, проверяем состояние FSM
		# Если пользователь находится в процессе /add или других операциях,
		# не обрабатываем (они должны обрабатываться соответствующими обработчиками состояний)
		current_state = await state.get_state()
		if current_state:
			# Если состояние относится к AddDataStates, CardUserMessageStates, CardRequisiteStates и т.д.,
			# пропускаем обработку - эти состояния имеют свои обработчики
			state_str = str(current_state) if current_state else ""
			if any(state_group in state_str for state_group in [
				"AddDataStates", "CardUserMessageStates", "CardRequisiteStates", 
				"CardColumnBindStates", "CashColumnEditStates", "DeleteRowStates",
				"DeleteRateStates", "DeleteMoveStates", "QuestionReplyStates",
				"SellOrderMessageStates", "SellOrderUserReplyStates", "QuestionUserReplyStates",
				"OrderMessageStates", "OrderUserReplyStates", "AlertMessageStates", "DebtorsStates"
			]):
				# Пользователь находится в состоянии, которое имеет свой обработчик, пропускаем
				logger.debug(f"⚠️ Пропуск обработки: пользователь находится в состоянии {current_state}, которое имеет свой обработчик")
				return
			
			# Если есть активное состояние, проверяем, не является ли это состоянием для пересылок
			# Состояния ForwardBindStates - это состояния для обработки пересылок
			if current_state not in [ForwardBindStates.waiting_select_card.state, 
			                          ForwardBindStates.waiting_select_existing_card.state]:
				# Пользователь находится в другом состоянии (например, /add), пропускаем обработку
				logger.debug(f"⚠️ Пропуск обработки: пользователь находится в состоянии {current_state}")
				return
		return
	
	# Если это пересылка - очищаем состояние FSM перед обработкой
	# Пересылка сообщения пользователя - это отдельная операция, которая не должна зависеть от состояния команды /add, /rate или /move
	if current_state_before_check:
		logger.info(f"🧹 Очистка состояния FSM перед обработкой пересылки: было состояние {current_state_before_check}")
		await state.clear()
	
	# Обычная обработка пересылки
	orig_tg_id, orig_username, orig_full_name = extract_forward_profile(message)
	text = message.text or message.caption or ""
	logger.info(f"📨 Пересылка от админа {message.from_user.id}: tg_id={orig_tg_id}, username={orig_username}, full_name={orig_full_name}, text={text[:50] if text else 'нет'}")
	
	# Сохраняем текст пересылаемого сообщения в state для последующей проверки BTC адресов
	await state.update_data(forwarded_message_text=text)
	
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
						card_id = card.get("card_id")
						
						# Логируем доставку
						await db.log_card_delivery(
							user_id,
							card_id,
							admin_id=message.from_user.id if message.from_user else None,
						)
						
						# Отправляем все реквизиты карты (из card_requisites и user_message)
						await send_card_requisites_to_admin(bot, message.chat.id, card_id, db, user_id=user_id, admin_id=message.from_user.id if message.from_user else None)
						
						# Проверяем и отправляем ссылки на BTC адреса, если они найдены
						if text:
							await check_and_send_btc_address_links(bot, message.chat.id, text, user_id=user_id)
						
						logger.info(f"✅ Отправлено сообщение карты для скрытого пользователя '{orig_full_name}' (user_id={user_id})")
						
						return
					else:
						# Несколько карт - сначала отправляем ссылку, затем показываем выбор
						if text:
							await check_and_send_btc_address_links(bot, message.chat.id, text, user_id=user_id)
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
					# Сначала отправляем ссылку на mempool, если есть BTC адреса
					if text:
						await check_and_send_btc_address_links(bot, message.chat.id, text)
					# Затем показываем выбор карты
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
			# Если пользователь не найден, но есть текст с BTC адресом, отправляем ссылку
			if text:
				logger.info(f"🔍 Пользователь не найден, но есть текст. Проверяем BTC адреса и отправляем ссылку, text='{text[:50]}...', chat_id={message.chat.id}")
				await check_and_send_btc_address_links(bot, message.chat.id, text)
				logger.info(f"✅ Функция check_and_send_btc_address_links завершена для chat_id={message.chat.id}")
			# Предлагаем создать пользователя и привязать карту
			logger.info(f"🔍 Пользователь '{orig_full_name}' не найден, предлагаем создать и привязать карту")
			groups = await db.list_card_groups()
			if groups:
				await state.set_state(ForwardBindStates.waiting_select_group)
				await state.update_data(hidden_user_name=orig_full_name, reply_only=False, existing_user_id=None)
				await message.answer(f"❌ Пользователь '{orig_full_name}' не найден в БД.\n\nВыберите группу карт для привязки:", reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True))
			else:
				rows = await db.list_cards()
				cards = [(r[0], r[1]) for r in rows]
				await state.set_state(ForwardBindStates.waiting_select_card)
				await state.update_data(hidden_user_name=orig_full_name, reply_only=False, existing_user_id=None)
				await message.answer(f"❌ Пользователь '{orig_full_name}' не найден в БД.\n\nГрупп пока нет. Выберите карту для привязки:", reply_markup=cards_select_kb(cards, back_to="admin:back"))
			return
	
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
				logger.info(f"🚀 Вызов send_card_requisites_to_admin для card_id={card_id}, admin_chat_id={message.chat.id}, user_id={user_id}")
				try:
					admin_chat_id = message.chat.id
					sent_count = await send_card_requisites_to_admin(bot, admin_chat_id, card_id, db, user_id=user_id, admin_id=message.from_user.id if message.from_user else None)
					logger.info(f"✅ send_card_requisites_to_admin завершена для card_id={card_id}, отправлено: {sent_count}")
					
					# Проверяем и отправляем ссылки на BTC адреса, если они найдены
					if text:
						logger.info(f"🔍 Отправка ссылки на BTC адрес после отправки реквизитов, text='{text[:50]}...', admin_chat_id={admin_chat_id}, user_id={user_id}")
						await check_and_send_btc_address_links(bot, admin_chat_id, text, user_id=user_id)
						logger.info(f"✅ Функция check_and_send_btc_address_links завершена для admin_chat_id={admin_chat_id}")
				except Exception as e:
					logger.exception(f"❌ КРИТИЧЕСКАЯ ОШИБКА в send_card_requisites_to_admin: {e}")
				return
			# Сначала отправляем ссылку на mempool, если есть BTC адреса
			if text:
				logger.info(f"🔍 Отправка ссылки на BTC адрес перед показом меню выбора карты, text='{text[:50]}...', chat_id={message.chat.id}, user_id={user_id}")
				await check_and_send_btc_address_links(bot, message.chat.id, text, user_id=user_id)
				logger.info(f"✅ Функция check_and_send_btc_address_links завершена для chat_id={message.chat.id}")
			# Затем показываем меню выбора карты
			buttons = [(card["card_id"], card["card_name"]) for card in cards_for_user]
			await state.set_state(ForwardBindStates.waiting_select_existing_card)
			await state.update_data(original_tg_id=orig_tg_id, user_id=user_id)
			await message.answer(
				f"✅ У пользователя привязано несколько карт. Выберите нужную:",
				reply_markup=user_cards_reply_kb(buttons, orig_tg_id, back_to="admin:back"),
			)
			return
		else:
			# Карт нет - показываем выбор группы карт для привязки
			logger.info(f"⚠️ У пользователя {orig_tg_id} нет привязанных карт, предлагаем выбрать группу")
			# Сначала отправляем ссылку на mempool, если есть BTC адреса
			if text:
				logger.info(f"🔍 Отправка ссылки на BTC адрес перед показом меню выбора группы, text='{text[:50]}...', chat_id={message.chat.id}, user_id={user_id}")
				await check_and_send_btc_address_links(bot, message.chat.id, text, user_id=user_id)
				logger.info(f"✅ Функция check_and_send_btc_address_links завершена для chat_id={message.chat.id}")
			# Затем показываем выбор карты
			groups = await db.list_card_groups()
			if groups:
				await state.set_state(ForwardBindStates.waiting_select_group)
				await state.update_data(original_tg_id=orig_tg_id, user_id=user_id, reply_only=False)
				await message.answer(
					"✅ Пользователь найден в БД, но не привязан к карте.\n\nВыберите группу карт:",
					reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True)
				)
			else:
				rows = await db.list_cards()
				cards = [(r[0], r[1]) for r in rows]
				await state.set_state(ForwardBindStates.waiting_select_card)
				await state.update_data(original_tg_id=orig_tg_id, user_id=user_id, reply_only=False)
				await message.answer(
					"✅ Пользователь найден в БД, но не привязан к карте.\n\nГрупп пока нет. Выберите карту:",
					reply_markup=cards_select_kb(cards, back_to="admin:back")
				)
			return
	else:
		# orig_tg_id is None - пользователь не найден после всех попыток
		logger.warning(f"❌ Пользователь не найден после всех попыток поиска. Отправляем ссылку на BTC адрес, если есть в тексте")
		if text:
			logger.info(f"🔍 Пользователь не найден, но есть текст. Проверяем BTC адреса и отправляем ссылку, text='{text[:50]}...', chat_id={message.chat.id}")
			await check_and_send_btc_address_links(bot, message.chat.id, text)
			logger.info(f"✅ Функция check_and_send_btc_address_links завершена для chat_id={message.chat.id}")
		else:
			logger.warning("❌ Пользователь не найден и нет текста для проверки BTC адресов")
		
		# Предлагаем создать пользователя и привязать карту
		# Используем full_name, если он был извлечен из пересылки
		hidden_name = orig_full_name if orig_full_name else "Неизвестный пользователь"
		logger.info(f"🔍 Пользователь не найден после всех попыток, предлагаем создать и привязать карту (hidden_name='{hidden_name}')")
		groups = await db.list_card_groups()
		if groups:
			await state.set_state(ForwardBindStates.waiting_select_group)
			await state.update_data(hidden_user_name=hidden_name, reply_only=False, existing_user_id=None)
			await message.answer(
				"❌ Пользователь не найден в БД.\n\nВыберите группу карт для привязки:",
				reply_markup=card_groups_select_kb(groups, back_to="admin:back", forward_mode=True)
			)
		else:
			rows = await db.list_cards()
			cards = [(r[0], r[1]) for r in rows]
			await state.set_state(ForwardBindStates.waiting_select_card)
			await state.update_data(hidden_user_name=hidden_name, reply_only=False, existing_user_id=None)
			await message.answer(
				"❌ Пользователь не найден в БД.\n\nГрупп пока нет. Выберите карту для привязки:",
				reply_markup=cards_select_kb(cards, back_to="admin:back")
			)


@admin_router.callback_query(F.data.startswith("question:reply:"))
async def question_reply_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик начала ответа на вопрос пользователя"""
	# Формат: question:reply:{question_id}
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		question_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о вопросе из БД
	db = get_db()
	question = await db.get_question_by_id(question_id)
	if not question:
		await cb.answer("Вопрос не найден", show_alert=True)
		return
	
	# Проверяем, не завершен ли вопрос
	if question.get("completed_at"):
		await cb.answer("Вопрос уже завершен", show_alert=True)
		return
	
	# Получаем историю переписки
	messages = await db.get_question_messages(question_id)
	
	# Формируем информацию о вопросе
	user_tg_id = question["user_tg_id"]
	user_name = question.get("user_name", "Не указано")
	user_username = question.get("user_username", "Не указано")
	question_text = question["question_text"]
	initiated_by_admin = bool(question.get("initiated_by_admin"))
	
	# Получаем информацию о последней сделке и профите пользователя
	last_order_info = ""
	try:
		user_id = await db.get_user_id_by_tg(user_tg_id)
		if user_id:
			user_data = await db.get_user_by_id(user_id)
			if user_data:
				last_order_id = user_data.get("last_order_id")
				last_order_profit = user_data.get("last_order_profit")
				
				if last_order_id:
					# Получаем информацию о последней сделке
					last_order = await db.get_order_by_id(last_order_id)
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
				monthly_profit = await db.get_user_monthly_profit(user_tg_id)
				if monthly_profit is not None:
					try:
						monthly_profit_formatted = f"{int(round(monthly_profit)):,}".replace(",", " ")
						last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit_formatted} USD"
					except (ValueError, TypeError):
						last_order_info += f"\n📊 Профит за текущий месяц: {monthly_profit} USD"
	except Exception as e:
		logger.debug(f"Ошибка получения информации о последней сделке: {e}")
	
	# Формируем сообщение для админа с историей
	if initiated_by_admin:
		question_info = (
			f"💬 <b>Диалог (инициировано администратором)</b>\n\n"
			f"👤 Имя: {user_name}\n"
			f"📱 Username: @{user_username}\n"
			f"🆔 ID: <code>{user_tg_id}</code>{last_order_info}"
		)
	else:
		question_info = (
			f"❓ <b>Вопрос от пользователя</b>\n\n"
			f"👤 Имя: {user_name}\n"
			f"📱 Username: @{user_username}\n"
			f"🆔 ID: <code>{user_tg_id}</code>{last_order_info}\n\n"
			f"💬 <b>Вопрос:</b>\n{question_text}"
		)
	
	# Добавляем историю переписки
	history_lines = []
	for msg in messages:
		if msg["sender_type"] == "admin":
			history_lines.append(f"💬 <b>Вы:</b>\n{msg['message_text']}")
		else:
			history_lines.append(f"👤 <b>Пользователь:</b>\n{msg['message_text']}")
	
	history_text = "\n\n".join(history_lines)
	admin_message = question_info + "\n\n" + history_text
	
	# Удаляем уведомление для админа (если есть)
	from app.notifications import notification_ids
	admin_ids = get_admin_ids()
	if admin_ids:
		notification_key = (admin_ids[0], question_id, 'question')
		if notification_key in notification_ids:
			try:
				notif_message_id = notification_ids[notification_key]
				await bot.delete_message(chat_id=admin_ids[0], message_id=notif_message_id)
				del notification_ids[notification_key]
			except Exception as e:
				logger.debug(f"Не удалось удалить уведомление: {e}")
	
	# Сохраняем данные в FSM
	await state.update_data(
		question_id=question_id,
		user_tg_id=user_tg_id
	)
	
	# Переводим в состояние ожидания ответа
	await state.set_state(QuestionReplyStates.waiting_reply)
	
	# Обновляем сообщение админа
	from app.keyboards import question_reply_kb
	try:
		await cb.message.edit_text(
			admin_message + "\n\n📝 Введите ваш ответ:",
			parse_mode="HTML",
			reply_markup=question_reply_kb(question_id)
		)
	except Exception as e:
		logger.error(f"Ошибка редактирования сообщения: {e}")
		try:
			await cb.message.answer(
				admin_message + "\n\n📝 Введите ваш ответ:",
				parse_mode="HTML"
			)
		except Exception as e2:
			logger.error(f"Ошибка отправки нового сообщения: {e2}")
	
	await cb.answer()

@admin_router.callback_query(F.data.startswith("sell:order:message:"))
async def sell_order_message_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик начала переписки по сделке на продажу"""
	# Формат: sell:order:message:{order_id}
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		order_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о сделке
	db = get_db()
	order = await db.get_sell_order_by_id(order_id)
	if not order:
		await cb.answer("Сделка не найдена", show_alert=True)
		return
	
	# Проверяем, не завершена ли сделка
	if order.get("completed_at"):
		await cb.answer("Сделка уже завершена", show_alert=True)
		return
	
	# Удаляем уведомление для админа (если есть)
	from app.notifications import notification_ids
	admin_ids = get_admin_ids()
	if admin_ids:
		notification_key = (admin_ids[0], order_id, 'sell_order')
		if notification_key in notification_ids:
			try:
				notif_message_id = notification_ids[notification_key]
				await bot.delete_message(chat_id=admin_ids[0], message_id=notif_message_id)
				del notification_ids[notification_key]
			except Exception as e:
				logger.debug(f"Не удалось удалить уведомление: {e}")
	
	# Сохраняем данные в FSM
	await state.update_data(
		sell_order_id=order_id,
		user_tg_id=order["user_tg_id"]
	)
	
	# Переводим в состояние ожидания сообщения
	await state.set_state(SellOrderMessageStates.waiting_message)
	
	# Обновляем сообщение или отправляем новое
	try:
		# Пытаемся отредактировать сообщение
		await cb.message.edit_text(
			(cb.message.text or cb.message.caption or "") + "\n\n📝 Введите ваше сообщение пользователю:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup  # Сохраняем клавиатуру
		)
	except Exception as e:
		# Если не удалось отредактировать, отправляем новое сообщение
		logger.error(f"Ошибка редактирования сообщения: {e}")
		try:
			await cb.message.answer(
				"📝 Введите ваше сообщение пользователю:",
				parse_mode="HTML"
			)
		except Exception as e2:
			logger.error(f"Ошибка отправки нового сообщения: {e2}")
	
	await cb.answer()

@admin_router.callback_query(F.data.startswith("sell:order:complete:"))
async def sell_order_complete(cb: CallbackQuery, bot: Bot):
	"""Обработчик завершения сделки на продажу"""
	# Формат: sell:order:complete:{order_id}
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		order_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о сделке
	db = get_db()
	order = await db.get_sell_order_by_id(order_id)
	if not order:
		await cb.answer("Сделка не найдена", show_alert=True)
		return
	
	# Проверяем, не завершена ли сделка
	if order.get("completed_at"):
		await cb.answer("Сделка уже завершена", show_alert=True)
		return
	
	# Завершаем сделку
	await db.complete_sell_order(order_id)
	
	# Удаляем зависшие уведомления у пользователя и админа
	from app.notifications import notification_ids
	admin_ids = get_admin_ids()
	user_tg_id = order["user_tg_id"]
	
	# Удаляем уведомление пользователю
	user_notif_key = (user_tg_id, order_id, 'sell_order')
	if user_notif_key in notification_ids:
		try:
			notif_message_id = notification_ids[user_notif_key]
			await bot.delete_message(chat_id=user_tg_id, message_id=notif_message_id)
		except Exception as e:
			logger.debug(f"Не удалось удалить уведомление пользователю: {e}")
		finally:
			del notification_ids[user_notif_key]
	
	# Удаляем уведомление админу
	if admin_ids:
		admin_notif_key = (admin_ids[0], order_id, 'sell_order')
		if admin_notif_key in notification_ids:
			try:
				notif_message_id = notification_ids[admin_notif_key]
				await bot.delete_message(chat_id=admin_ids[0], message_id=notif_message_id)
			except Exception as e:
				logger.debug(f"Не удалось удалить уведомление админу: {e}")
			finally:
				del notification_ids[admin_notif_key]
	
	# Уведомляем пользователя
	try:
		await bot.send_message(
			chat_id=user_tg_id,
			text="✅ Ваша заявка на продажу завершена.\n\nСпасибо за использование нашего сервиса!",
			parse_mode="HTML"
		)
		logger.info(f"✅ Сделка {order_id} завершена, уведомление отправлено пользователю {user_tg_id}")
	except Exception as e:
		logger.error(f"❌ Ошибка отправки уведомления пользователю {user_tg_id}: {e}", exc_info=True)
	
	# Обновляем сообщение админа
	await cb.message.edit_text(
		cb.message.text + "\n\n✅ <b>Сделка завершена</b>",
		parse_mode="HTML"
	)
	await cb.answer("Сделка завершена ✅")

@admin_router.callback_query(F.data.startswith("order:message:"))
async def order_message_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик начала переписки по обычной заявке"""
	# Формат: order:message:{order_id}
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		order_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await cb.answer("Заявка не найдена", show_alert=True)
		return
	
	# Проверяем, не завершена ли заявка
	if order.get("completed_at"):
		await cb.answer("Заявка уже завершена", show_alert=True)
		return
	
	# Удаляем уведомление для админа (если есть)
	from app.notifications import notification_ids
	admin_ids = get_admin_ids()
	if admin_ids:
		notification_key = (admin_ids[0], order_id, 'order')
		logger.info(f"🔵 Удаление уведомления админа: key={notification_key}, exists={notification_key in notification_ids}, all_keys={list(notification_ids.keys())}")
		if notification_key in notification_ids:
			try:
				notif_message_id = notification_ids[notification_key]
				logger.info(f"🔵 Удаление уведомления админа: message_id={notif_message_id}, chat_id={admin_ids[0]}")
				await bot.delete_message(chat_id=admin_ids[0], message_id=notif_message_id)
				del notification_ids[notification_key]
				logger.info(f"✅ Уведомление админа успешно удалено")
			except Exception as e:
				logger.warning(f"⚠️ Не удалось удалить уведомление админа: {e}")
	
	# Сохраняем данные в FSM
	await state.update_data(
		order_id=order_id,
		user_tg_id=order["user_tg_id"]
	)
	
	# Переводим в состояние ожидания сообщения
	await state.set_state(OrderMessageStates.waiting_message)
	
	# Обновляем сообщение админа
	# Проверяем тип сообщения (кнопки теперь на фото/документе)
	if cb.message.photo:
		# Это фото - используем edit_message_caption
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + "\n\n📝 Введите ваше сообщение пользователю:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	elif cb.message.document:
		# Это документ - используем edit_message_caption
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + "\n\n📝 Введите ваше сообщение пользователю:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	else:
		# Это текстовое сообщение - используем edit_text
		await cb.message.edit_text(
			cb.message.text + "\n\n📝 Введите ваше сообщение пользователю:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	await cb.answer()

@admin_router.callback_query(F.data.startswith("order:edit:amount:"))
async def order_edit_amount_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик начала изменения суммы сделки"""
	# Формат: order:edit:amount:{order_id}
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		order_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await cb.answer("Заявка не найдена", show_alert=True)
		return
	
	# Проверяем, не завершена ли заявка
	if order.get("completed_at"):
		await cb.answer("Заявка уже завершена", show_alert=True)
		return
	
	# Сохраняем данные в FSM
	await state.update_data(
		order_id=order_id,
		current_amount_currency=order.get("amount_currency", 0),
		currency_symbol=order.get("currency_symbol", "₽")
	)
	
	# Переводим в состояние ожидания новой суммы
	await state.set_state(OrderEditStates.waiting_amount)
	
	# Обновляем сообщение админа
	current_amount = order.get("amount_currency", 0)
	currency_symbol = order.get("currency_symbol", "₽")
	
	# Проверяем тип сообщения (кнопки теперь на фото/документе)
	if cb.message.photo:
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + f"\n\n💰 Текущая сумма: {int(current_amount)} {currency_symbol}\nВведите новую сумму сделки:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	elif cb.message.document:
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + f"\n\n💰 Текущая сумма: {int(current_amount)} {currency_symbol}\nВведите новую сумму сделки:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	else:
		await cb.message.edit_text(
			cb.message.text + f"\n\n💰 Текущая сумма: {int(current_amount)} {currency_symbol}\nВведите новую сумму сделки:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	await cb.answer()

@admin_router.callback_query(F.data.startswith("order:edit:crypto:"))
async def order_edit_crypto_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
	"""Обработчик начала изменения количества крипты"""
	# Формат: order:edit:crypto:{order_id}
	parts = cb.data.split(":")
	if len(parts) < 4:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		order_id = int(parts[3])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о заявке
	db = get_db()
	order = await db.get_order_by_id(order_id)
	if not order:
		await cb.answer("Заявка не найдена", show_alert=True)
		return
	
	# Проверяем, не завершена ли заявка
	if order.get("completed_at"):
		await cb.answer("Заявка уже завершена", show_alert=True)
		return
	
	# Сохраняем данные в FSM
	await state.update_data(
		order_id=order_id,
		current_crypto_amount=order.get("amount", 0),
		crypto_display=order.get("crypto_display", "")
	)
	
	# Переводим в состояние ожидания нового количества крипты
	await state.set_state(OrderEditStates.waiting_crypto_amount)
	
	# Обновляем сообщение админа
	current_amount = order.get("amount", 0)
	crypto_display = order.get("crypto_display", "")
	amount_str = f"{current_amount:.8f}".rstrip('0').rstrip('.') if current_amount < 1 else f"{current_amount:.2f}".rstrip('0').rstrip('.')
	
	# Проверяем тип сообщения (кнопки теперь на фото/документе)
	if cb.message.photo:
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + f"\n\n🪙 Текущее количество: {amount_str} {crypto_display}\nВведите новое количество крипты:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	elif cb.message.document:
		current_caption = cb.message.caption or ""
		await cb.message.edit_caption(
			caption=current_caption + f"\n\n🪙 Текущее количество: {amount_str} {crypto_display}\nВведите новое количество крипты:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	else:
		await cb.message.edit_text(
			cb.message.text + f"\n\n🪙 Текущее количество: {amount_str} {crypto_display}\nВведите новое количество крипты:",
			parse_mode="HTML",
			reply_markup=cb.message.reply_markup
		)
	await cb.answer()

@admin_router.callback_query(F.data.startswith("question:complete:"))
async def question_complete(cb: CallbackQuery, bot: Bot):
	"""Обработчик закрытия вопроса"""
	# Формат: question:complete:{question_id}
	parts = cb.data.split(":")
	if len(parts) < 3:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	try:
		question_id = int(parts[2])
	except ValueError:
		await cb.answer("Ошибка данных", show_alert=True)
		return
	
	# Получаем информацию о вопросе
	db = get_db()
	question = await db.get_question_by_id(question_id)
	if not question:
		await cb.answer("Вопрос не найден", show_alert=True)
		return
	
	# Проверяем, не закрыт ли вопрос
	if question.get("completed_at"):
		await cb.answer("Вопрос уже закрыт", show_alert=True)
		return
	
	# Закрываем вопрос
	await db.complete_question(question_id)
	
	# Уведомляем пользователя
	user_tg_id = question["user_tg_id"]
	
	try:
		if question.get("initiated_by_admin"):
			await bot.send_message(
				chat_id=user_tg_id,
				text="✅ Диалог завершен администратором.",
				parse_mode="HTML"
			)
		else:
			await bot.send_message(
				chat_id=user_tg_id,
				text="✅ Ваш вопрос закрыт администратором.\n\nСпасибо за обращение!",
				parse_mode="HTML"
			)
		logger.info(f"✅ Вопрос {question_id} закрыт, уведомление отправлено пользователю {user_tg_id}")
	except Exception as e:
		logger.error(f"❌ Ошибка отправки уведомления пользователю {user_tg_id}: {e}", exc_info=True)
	
	# Обновляем сообщение админа
	await cb.message.edit_text(
		cb.message.text + "\n\n✅ <b>Вопрос закрыт</b>",
		parse_mode="HTML"
	)
	await cb.answer("Вопрос закрыт ✅")


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
			sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=user_id, admin_id=cb.from_user.id if cb.from_user else None)
			
			# Получаем текст пересылаемого сообщения для проверки BTC адресов
			forwarded_text = data.get("forwarded_message_text", "")
			if forwarded_text:
				await check_and_send_btc_address_links(bot, cb.message.chat.id, forwarded_text, user_id=user_id)
		else:
			# Несколько карт - сначала отправляем ссылку, затем показываем выбор
			forwarded_text = data.get("forwarded_message_text", "")
			if forwarded_text:
				await check_and_send_btc_address_links(bot, cb.message.chat.id, forwarded_text, user_id=user_id)
			buttons = [(card["card_id"], card["card_name"]) for card in cards_for_user]
			await state.set_state(ForwardBindStates.waiting_select_existing_card)
			text = f"✅ Выбран: {user.get('full_name', 'Без имени')}\n\nУ пользователя привязано несколько карт. Выберите нужную:"
			await cb.message.edit_text(text, reply_markup=user_cards_reply_kb(buttons, tg_id, back_to="admin:back"))
	else:
		# Не привязан - выбираем группу карт для привязки
		# Сначала отправляем ссылку на mempool, если есть BTC адреса
		forwarded_text = data.get("forwarded_message_text", "")
		if forwarded_text:
			await check_and_send_btc_address_links(bot, cb.message.chat.id, forwarded_text, user_id=user_id)
		# Затем показываем выбор карты
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
	
	# Удаляем меню выбора карты (если это не reply_only, так как там может быть другое меню)
	if not reply_only:
		try:
			await cb.message.delete()
		except Exception as e:
			logger.warning(f"⚠️ Не удалось удалить меню выбора карты: {e}")
	
	# Если это только ответ администратору (reply_only), отправляем реквизиты и завершаем
	if reply_only:
		requisites = await db.list_card_requisites(card_id)
		user_msg = card.get("user_message")
		has_user_message = bool(user_msg)
		
		# Получаем user_id из данных состояния или из карты
		reply_user_id = data.get("user_id_for_hidden") or data.get("existing_user_id")
		if not reply_user_id and original_tg_id:
			reply_user_id = await db.get_user_id_by_tg(original_tg_id)
		
		# Получаем текст пересылаемого сообщения для проверки BTC адресов
		forwarded_text = data.get("forwarded_message_text", "")
		
		await state.clear()
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=reply_user_id, admin_id=cb.from_user.id if cb.from_user else None)
		
		# Проверяем и отправляем ссылки на BTC адреса, если они найдены
		if forwarded_text:
			await check_and_send_btc_address_links(bot, cb.message.chat.id, forwarded_text, user_id=reply_user_id)
		
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
		# Ссылка уже была отправлена при пересылке, поэтому не отправляем повторно
		await state.clear()
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=user_id, admin_id=cb.from_user.id if cb.from_user else None)
		
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
		# Ссылка уже была отправлена при пересылке, поэтому не отправляем повторно
		await state.clear()
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=user_id, admin_id=cb.from_user.id if cb.from_user else None)
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
	
	# Удаляем меню выбора карты
	try:
		await cb.message.delete()
	except Exception as e:
		logger.warning(f"⚠️ Не удалось удалить меню выбора карты: {e}")
	
	await state.clear()
	
	# Получаем реквизиты карты
	requisites = await db.list_card_requisites(card_id)
	user_msg = card.get("user_message")
	has_user_message = bool(user_msg)
	
	# Подсчитываем общее количество реквизитов (из таблицы + user_message если есть)
	total_requisites_count = len(requisites) + (1 if has_user_message else 0)
	
	# Получаем текст пересылаемого сообщения для проверки BTC адресов
	forwarded_text = data.get("forwarded_message_text", "")
	
	# Логируем доставку
	if user_tg_id:
		await db.log_card_delivery_by_tg(
		user_tg_id,
		card_id,
		admin_id=cb.from_user.id if cb.from_user else None,
	)
		# Получаем user_id для обычного пользователя
		reply_user_id = await db.get_user_id_by_tg(user_tg_id)
		
		# Отправляем все реквизиты админу (из таблицы + user_message если есть)
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=reply_user_id, admin_id=cb.from_user.id if cb.from_user else None)
		
		# Отправляем ссылку на BTC адрес, если она есть в пересланном сообщении
		# Это гарантирует, что ссылка будет отправлена даже если при первой пересылке что-то пошло не так
		if forwarded_text:
			logger.info(f"🔍 Отправка ссылки на BTC адрес при выборе карты, forwarded_text='{forwarded_text[:50]}...', chat_id={cb.message.chat.id}, user_id={reply_user_id}")
			await check_and_send_btc_address_links(bot, cb.message.chat.id, forwarded_text, user_id=reply_user_id)
			logger.info(f"✅ Функция check_and_send_btc_address_links завершена при выборе карты для chat_id={cb.message.chat.id}")
	elif user_id_for_hidden:
		# Логируем для скрытого пользователя через user_id
		await db.log_card_delivery(
			user_id_for_hidden,
			card_id,
			admin_id=cb.from_user.id if cb.from_user else None,
		)
		logger.info(f"✅ Логирование доставки для скрытого пользователя '{hidden_user_name}' (user_id={user_id_for_hidden}, card_id={card_id})")
		# Отправляем все реквизиты админу (из таблицы + user_message если есть)
		sent_count = await send_card_requisites_to_admin(bot, cb.message.chat.id, card_id, db, user_id=user_id_for_hidden, admin_id=cb.from_user.id if cb.from_user else None)
		logger.info(f"✅ Отправлено {sent_count} реквизитов админу для скрытого пользователя")
		
		# Отправляем ссылку на BTC адрес, если она есть в пересланном сообщении
		# Это гарантирует, что ссылка будет отправлена даже если при первой пересылке что-то пошло не так
		if forwarded_text:
			logger.info(f"🔍 Отправка ссылки на BTC адрес при выборе карты (скрытый пользователь), forwarded_text='{forwarded_text[:50]}...', chat_id={cb.message.chat.id}, user_id={user_id_for_hidden}")
			await check_and_send_btc_address_links(bot, cb.message.chat.id, forwarded_text, user_id=user_id_for_hidden)
			logger.info(f"✅ Функция check_and_send_btc_address_links завершена при выборе карты (скрытый пользователь) для chat_id={cb.message.chat.id}")
	
	await cb.answer()
