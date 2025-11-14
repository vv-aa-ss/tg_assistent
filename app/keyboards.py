from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Dict, Iterable, List, Optional, Set, Tuple


def admin_menu_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="📇 Карты", callback_data="admin:cards")
	kb.button(text="👥 Пользователи", callback_data="admin:users")
	kb.button(text="📊 Статистика", callback_data="admin:stats")
	kb.adjust(2)
	return kb.as_markup()


def cards_list_kb(cards: List[Tuple[int, str]], with_add: bool = True) -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	for cid, name in cards:
		kb.button(text=f"💳 {name}", callback_data=f"card:view:{cid}")
	if with_add:
		kb.button(text="➕ Добавить карту", callback_data="card:add")
	kb.button(text="⬅️ Назад", callback_data="admin:back")
	kb.adjust(1)
	return kb.as_markup()


def users_list_kb(
	users: List[Tuple[int, str]],
	back_to: str = "admin:back",
	page: int = 0,
	per_page: Optional[int] = None,
	total: Optional[int] = None,
) -> InlineKeyboardMarkup:
	inline_keyboard: List[List[InlineKeyboardButton]] = []
	for uid, title in users:
		inline_keyboard.append(
			[InlineKeyboardButton(text=title, callback_data=f"user:view:{uid}")]
		)
	if per_page and total and per_page > 0:
		total_pages = max(1, (total + per_page - 1) // per_page)
		if total_pages > 1:
			nav_row: List[InlineKeyboardButton] = []
			if page > 0:
				nav_row.append(
					InlineKeyboardButton(text="◀️", callback_data=f"admin:users:{page-1}")
				)
			nav_row.append(
				InlineKeyboardButton(
					text=f"{page+1}/{total_pages}", callback_data="admin:users:noop"
				)
			)
			if page < total_pages - 1:
				nav_row.append(
					InlineKeyboardButton(text="▶️", callback_data=f"admin:users:{page+1}")
				)
			inline_keyboard.append(nav_row)
	inline_keyboard.append(
		[InlineKeyboardButton(text="⬅️ Назад", callback_data=back_to)]
	)
	return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def simple_back_kb(back_to: str = "admin:back") -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="⬅️ Назад", callback_data=back_to)
	return kb.as_markup()


def cards_select_kb(cards: List[Tuple[int, str]], back_to: str) -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	for cid, name in cards:
		kb.button(text=f"💳 {name}", callback_data=f"select:card:{cid}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def user_card_select_kb(
	cards: List[Tuple[int, str]],
	user_id: int,
	back_to: str = "admin:users",
	selected_card_ids: Optional[Iterable[int]] = None,
) -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	selected: Set[int] = set(selected_card_ids or [])
	for cid, name in cards:
		prefix = "✅" if cid in selected else "💳"
		kb.button(text=f"{prefix} {name}", callback_data=f"user:bind:card:{user_id}:{cid}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def user_action_kb(user_id: int, back_to: str = "admin:users") -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="Карты", callback_data=f"user:bind:{user_id}")
	kb.button(text="🗑️ Удалить пользователя", callback_data=f"user:delete:{user_id}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def card_action_kb(card_id: int, back_to: str = "admin:cards") -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="✏️ Изменить сообщение", callback_data=f"card:edit:{card_id}")
	kb.button(text="🗑️ Удалить карту", callback_data=f"card:delete:{card_id}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def user_cards_reply_kb(cards: List[Tuple[int, str]], user_tg_id: int, back_to: str = "admin:back") -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	for cid, name in cards:
		kb.button(text=f"💳 {name}", callback_data=f"user:reply:card:{user_tg_id}:{cid}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def similar_users_select_kb(similar_users: List[Dict], hidden_name: str, back_to: str = "admin:back") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора пользователя из списка похожих.
	similar_users: список словарей с полями user_id, tg_id, username, full_name
	"""
	kb = InlineKeyboardBuilder()
	for user in similar_users:
		tg_id = user.get("tg_id")
		full_name = user.get("full_name") or "Без имени"
		username = user.get("username")
		if username:
			label = f"{full_name} (@{username})"
		else:
			label = full_name
		# Используем только tg_id в callback_data (без hidden_name, чтобы избежать проблем с длиной)
		kb.button(text=f"👤 {label}", callback_data=f"hidden:select:{tg_id}")
	kb.button(text="❌ Нет в списке", callback_data="hidden:no_match")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()
