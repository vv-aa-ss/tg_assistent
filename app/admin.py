from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from typing import Any, Awaitable, Callable, Dict
import logging
import re
from app.db import Database
from app.keyboards import admin_menu_kb, cards_list_kb, users_list_kb, simple_back_kb, cards_select_kb, user_card_select_kb, user_action_kb, card_action_kb
from app.di import get_db, get_admin_ids

admin_router = Router(name="admin")
logger = logging.getLogger("app.admin")


class AdminOnlyMiddleware(BaseMiddleware):
	async def __call__(
		self,
		handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
		event: TelegramObject,
		data: Dict[str, Any],
	) -> Any:
		admin_ids = get_admin_ids()
		from_user = getattr(event, "from_user", None)
		if from_user and from_user.id not in admin_ids:
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


def is_admin(user_id: int, admin_ids: list[int]) -> bool:
	return user_id in admin_ids


def extract_forward_profile(message: Message) -> tuple[int | None, str | None, str | None]:
	try:
		if getattr(message, "forward_origin", None):
			origin = message.forward_origin
			user = getattr(origin, "sender_user", None)
			if user and getattr(user, "id", None):
				username = getattr(user, "username", None)
				full_name = " ".join([x for x in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if x]) or None
				logger.debug(f"forward_origin detected, user_id={user.id}, username={username}")
				return user.id, username, full_name
		ex = getattr(message, "forward_from", None)
		if ex:
			username = getattr(ex, "username", None)
			full_name = " ".join([x for x in [getattr(ex, "first_name", None), getattr(ex, "last_name", None)] if x]) or None
			logger.debug(f"forward_from detected, user_id={ex.id}, username={username}")
			return ex.id, username, full_name
		logger.debug("No forward info found in message")
		return None, None, None
	except Exception as e:
		logger.exception(f"extract_forward_profile error: {e}")
		return None, None, None


@admin_router.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
	admin_ids = get_admin_ids()
	if not is_admin(message.from_user.id, admin_ids):
		logger.debug(f"/admin ignored: user {message.from_user.id} is not admin")
		return
	await message.answer("Админ-панель:", reply_markup=admin_menu_kb())


@admin_router.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery):
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


@admin_router.callback_query(F.data == "admin:users")
async def admin_users(cb: CallbackQuery):
	db = get_db()
	rows = await db.list_users_with_binding()
	items = []
	for r in rows:
		# Используем full_name если есть, иначе username, иначе tg_id
		if r["full_name"]:
			label = r["full_name"]
		elif r["username"]:
			label = f"@{r['username']}"
		elif r["tg_id"]:
			label = f"tg_id: {r['tg_id']}"
		else:
			label = f"ID {r['user_id']}"
		
		if r["card_name"]:
			label += f" → {r['card_name']}"
		items.append((r["user_id"], label))
	logger.debug(f"Show users: count={len(items)}")
	await cb.message.edit_text("Пользователи:", reply_markup=users_list_kb(items))
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
	
	if user["card_name"]:
		text += f"\n\nТекущая привязка: {user['card_name']}"
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
	
	if user["card_name"]:
		text += f"\n\nТекущая привязка: {user['card_name']}"
	else:
		text += "\n\nНе привязан к карте"
	
	if not cards:
		text += "\n\n⚠️ Нет доступных карт для привязки"
		await cb.message.edit_text(text, reply_markup=simple_back_kb(f"user:view:{user_id}"))
	else:
		text += "\n\nВыберите карту для привязки:"
		# Используем специальную клавиатуру для выбора карты с указанием user_id
		await cb.message.edit_text(text, reply_markup=user_card_select_kb(cards, user_id, f"user:view:{user_id}"))
	
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
	
	# Привязываем карту к пользователю
	await db.bind_user_to_card(user_id, card_id)
	logger.debug(f"Bound user_id={user_id} to card_id={card_id}")
	
	# Получаем обновленную информацию о пользователе
	user = await db.get_user_by_id(user_id)
	if not user:
		await cb.answer("Ошибка: пользователь не найден", show_alert=True)
		return
	
	# Получаем информацию о карте
	rows = await db.list_cards()
	card_name = None
	for r in rows:
		if r[0] == card_id:
			card_name = r[1]
			break
	
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
	
	text += f"\n\n✅ Привязан к карте: {card_name if card_name else 'Неизвестная карта'}"
	
	await cb.message.edit_text(text, reply_markup=simple_back_kb("admin:users"))
	await cb.answer("Карта привязана ✅")


# Handle any message and process forwarding logic for admins
@admin_router.message()
async def handle_forwarded_from_admin(message: Message, bot: Bot, state: FSMContext):
	db = get_db()
	admin_ids = get_admin_ids()
	if not message.from_user or not is_admin(message.from_user.id, admin_ids):
		return
	orig_tg_id, orig_username, orig_full_name = extract_forward_profile(message)
	text = message.text or message.caption or ""
	logger.debug(f"Incoming message from admin {message.from_user.id}, forward_user={orig_tg_id}, has_text={bool(text)}")
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
	if orig_tg_id is not None:
		# Ensure user is saved/upserted before any binding/lookup
		await db.get_or_create_user(orig_tg_id, orig_username, orig_full_name)
		card = await db.get_card_for_user_tg(orig_tg_id)
		if card:
			logger.debug(f"User {orig_tg_id} is bound to card_id={card[0]}")
			user_msg = card[3] if len(card) > 3 else None
			admin_text = "Сообщение карты отсутствует" if not user_msg else user_msg
			if user_msg:
				await message.answer(admin_text, parse_mode="HTML")
			else:
				await message.answer(admin_text)
			if user_msg:
				try:
					await bot.send_message(chat_id=orig_tg_id, text=user_msg, parse_mode="HTML")
					logger.debug("Sent user_message to user")
				except Exception as e:
					logger.exception(f"Failed to send user_message: {e}")
			return
		logger.debug("User not bound, offering cards for binding")
		rows = await db.list_cards()
		cards = [(r[0], r[1]) for r in rows]
		await state.set_state(ForwardBindStates.waiting_select_card)
		await state.update_data(original_tg_id=orig_tg_id)
		await message.answer("Пользователь не привязан. Выберите карту для привязки:", reply_markup=cards_select_kb(cards, back_to="admin:back"))
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
	await message.answer("Не удалось определить пользователя. Выберите карту для ответа администратору:", reply_markup=cards_select_kb(cards, back_to="admin:back"))


@admin_router.callback_query(ForwardBindStates.waiting_select_card, F.data.startswith("select:card:"))
async def forward_bind_select_card(cb: CallbackQuery, state: FSMContext, bot: Bot):
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	data = await state.get_data()
	reply_only = bool(data.get("reply_only", False))
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
	orig_tg_id = int(data.get("original_tg_id"))
	logger.debug(f"Bind forwarded user {orig_tg_id} to card_id={card_id}")
	user_id = await db.get_or_create_user(orig_tg_id, None, None)
	await db.bind_user_to_card(user_id, card_id)
	card_row = await db.get_card_for_user_tg(orig_tg_id)
	await state.clear()
	if card_row:
		user_msg = card_row[3] if len(card_row) > 3 else None
		if user_msg:
			admin_text = user_msg
			await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb(), parse_mode="HTML")
		else:
			admin_text = "Сообщение карты отсутствует"
			await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb())
		if user_msg:
			try:
				await bot.send_message(chat_id=orig_tg_id, text=user_msg, parse_mode="HTML")
				logger.debug("Sent user_message after binding")
			except Exception as e:
				logger.exception(f"Failed to send user_message after binding: {e}")
	await cb.answer()
