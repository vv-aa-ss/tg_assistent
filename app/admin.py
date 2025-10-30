from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram import Bot
import logging
from app.db import Database
from app.keyboards import admin_menu_kb, cards_list_kb, users_list_kb, simple_back_kb, cards_select_kb
from app.di import get_db, get_admin_ids

admin_router = Router(name="admin")
logger = logging.getLogger("app.admin")


class AddCardStates(StatesGroup):
	waiting_name = State()


class CardUserMessageStates(StatesGroup):
	waiting_message = State()


class ForwardBindStates(StatesGroup):
	waiting_select_card = State()


def is_admin(user_id: int, admin_ids: list[int]) -> bool:
	return user_id in admin_ids


def extract_forward_user_id(message: Message) -> int | None:
	try:
		if getattr(message, "forward_origin", None):
			origin = message.forward_origin
			user = getattr(origin, "sender_user", None)
			if user and getattr(user, "id", None):
				logger.debug(f"forward_origin detected, user_id={user.id}")
				return user.id
		ex = getattr(message, "forward_from", None)
		if ex:
			logger.debug(f"forward_from detected, user_id={ex.id}")
			return ex.id
		logger.debug("No forward info found in message")
		return None
	except Exception as e:
		logger.exception(f"extract_forward_user_id error: {e}")
		return None


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
async def card_view(cb: CallbackQuery, state: FSMContext):
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	current = await db.get_card_user_message(card_id)
	logger.debug(f"Edit user_message for card_id={card_id}")
	await state.set_state(CardUserMessageStates.waiting_message)
	await state.update_data(card_id=card_id)
	pref = f"Текущее сообщение карты:\n\n{current}\n\n" if current else ""
	await cb.message.edit_text(
		pref + "Отправьте новое сообщение этой карты.\nДля очистки отправьте: СБРОС",
		reply_markup=simple_back_kb("admin:cards"),
	)
	await cb.answer()


@admin_router.message(CardUserMessageStates.waiting_message)
async def card_set_user_message(message: Message, state: FSMContext):
	db = get_db()
	data = await state.get_data()
	card_id = int(data.get("card_id"))
	text = message.text.strip()
	logger.debug(f"Set user_message for card_id={card_id}, reset={(text.upper()=='СБРОС')}")
	if text.upper() == "СБРОС":
		await db.set_card_user_message(card_id, None)
		await state.clear()
		await message.answer("Сообщение карты очищено ✅", reply_markup=admin_menu_kb())
		return
	await db.set_card_user_message(card_id, text)
	await state.clear()
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
		label = f"{r['full_name'] or ''} @{r['username'] or ''} (tg_id: {r['tg_id'] or '—'})"
		if r["card_name"]:
			label += f" → {r['card_name']}"
		items.append((r["user_id"], label))
	logger.debug(f"Show users: count={len(items)}")
	await cb.message.edit_text("Пользователи:", reply_markup=users_list_kb(items))
	await cb.answer()


# Handle any message and process forwarding logic for admins
@admin_router.message()
async def handle_forwarded_from_admin(message: Message, bot: Bot, state: FSMContext):
	db = get_db()
	admin_ids = get_admin_ids()
	if not message.from_user or not is_admin(message.from_user.id, admin_ids):
		return
	original_tg_id = extract_forward_user_id(message)
	text = message.text or message.caption or ""
	logger.debug(f"Incoming message from admin {message.from_user.id}, forward_user={original_tg_id}, has_text={bool(text)}")
	if original_tg_id is not None:
		card = await db.get_card_for_user_tg(original_tg_id)
		if card:
			logger.debug(f"User {original_tg_id} is bound to card_id={card[0]}")
			user_msg = card[3] if len(card) > 3 else None
			admin_text = "Сообщение карты отсутствует" if not user_msg else user_msg
			await message.answer(admin_text)
			if user_msg:
				try:
					await bot.send_message(chat_id=original_tg_id, text=user_msg)
					logger.debug("Sent user_message to user")
				except Exception as e:
					logger.exception(f"Failed to send user_message: {e}")
			return
		logger.debug("User not bound, offering cards for binding")
		await db.get_or_create_user(original_tg_id, None, None)
		rows = await db.list_cards()
		cards = [(r[0], r[1]) for r in rows]
		await state.set_state(ForwardBindStates.waiting_select_card)
		await state.update_data(original_tg_id=original_tg_id)
		await message.answer("Пользователь не привязан. Выберите карту для привязки:", reply_markup=cards_select_kb(cards, back_to="admin:back"))
		return
	# fallback by text
	if text:
		card = await db.find_card_by_text(text)
		logger.debug(f"Pattern search result: {bool(card)}")
		if card:
			user_msg = await db.get_card_user_message(card[0])
			await message.answer(user_msg or "Сообщение карты отсутствует")
			return


@admin_router.callback_query(ForwardBindStates.waiting_select_card, F.data.startswith("select:card:"))
async def forward_bind_select_card(cb: CallbackQuery, state: FSMContext, bot: Bot):
	db = get_db()
	card_id = int(cb.data.split(":")[-1])
	data = await state.get_data()
	original_tg_id = int(data.get("original_tg_id"))
	logger.debug(f"Bind forwarded user {original_tg_id} to card_id={card_id}")
	user_id = await db.get_or_create_user(original_tg_id, None, None)
	await db.bind_user_to_card(user_id, card_id)
	card_row = await db.get_card_for_user_tg(original_tg_id)
	await state.clear()
	if card_row:
		user_msg = card_row[3] if len(card_row) > 3 else None
		admin_text = user_msg or "Сообщение карты отсутствует"
		await cb.message.edit_text(admin_text, reply_markup=admin_menu_kb())
		if user_msg:
			try:
				await bot.send_message(chat_id=original_tg_id, text=user_msg)
				logger.debug("Sent user_message after binding")
			except Exception as e:
				logger.exception(f"Failed to send user_message after binding: {e}")
	await cb.answer()
