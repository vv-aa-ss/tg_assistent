from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from typing import Any, Awaitable, Callable, Dict, List, Tuple
from datetime import datetime, timedelta
import logging
import re
from html import escape
from app.keyboards import (
	admin_menu_kb,
	cards_list_kb,
	users_list_kb,
	simple_back_kb,
	cards_select_kb,
	user_card_select_kb,
	user_action_kb,
	card_action_kb,
	user_cards_reply_kb,
	similar_users_select_kb,
)
from app.di import get_db, get_admin_ids, get_admin_usernames

admin_router = Router(name="admin")
logger = logging.getLogger("app.admin")

USERS_PER_PAGE = 6


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
		if from_user:
			user_id = getattr(from_user, "id", None)
			username = getattr(from_user, "username", None)
			if not is_admin(user_id, username, admin_ids, admin_usernames):
				return
		return await handler(event, data)


admin_router.message.middleware(AdminOnlyMiddleware())
admin_router.callback_query.middleware(AdminOnlyMiddleware())


class AddCardStates(StatesGroup):
	waiting_name = State()


class CardUserMessageStates(StatesGroup):
	waiting_message = State()


class ForwardBindStates(StatesGroup):
	waiting_select_card = State()
	waiting_select_existing_card = State()


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
		return dt.strftime("%Y-%m-%d %H:%M")
	if delta <= timedelta(minutes=1):
		return "только что"
	if delta < timedelta(hours=1):
		minutes = int(delta.total_seconds() // 60)
		return f"{minutes} мин назад"
	if delta < timedelta(days=1):
		hours = int(delta.total_seconds() // 3600)
		return f"{hours} ч назад"
	if delta < timedelta(days=7):
		days = delta.days
		return f"{days} д назад"
	return dt.strftime("%Y-%m-%d %H:%M")


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


@admin_router.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery, state: FSMContext):
	await state.clear()
	await cb.message.edit_text("Админ-панель:", reply_markup=admin_menu_kb())
	await cb.answer()


@admin_router.callback_query(F.data == "admin:cards")
async def admin_cards(cb: CallbackQuery):
	db = get_db()
	rows = await db.list_cards()
	cards = [(r[0], r[1]) for r in rows]
	logger.debug(f"Show cards: count={len(cards)}")
	text = "Список карт:" if cards else "Список карт пуст."
	await cb.message.edit_text(text, reply_markup=cards_list_kb(cards))
	await cb.answer()


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
	if card['user_message']:
		text += f"\n\nТекущее сообщение:\n{card['user_message']}"
	else:
		text += "\n\nСообщение не задано"
	
	text += "\n\nЧто хотите сделать?"
	
	await cb.message.edit_text(text, reply_markup=card_action_kb(card_id, "admin:cards"), parse_mode="HTML")
	await cb.answer()


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
	text = "\n".join(lines)
	await cb.message.edit_text(text, reply_markup=simple_back_kb("admin:back"), parse_mode="HTML")
	await cb.answer()


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


# Handle any message and process forwarding logic for admins
@admin_router.message()
async def handle_forwarded_from_admin(message: Message, bot: Bot, state: FSMContext):
	db = get_db()
	admin_ids = get_admin_ids()
	admin_usernames = get_admin_usernames()
	if not message.from_user or not is_admin(message.from_user.id, message.from_user.username, admin_ids, admin_usernames):
		return
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
