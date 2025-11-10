from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup
from typing import Iterable, List, Optional, Set, Tuple


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


def users_list_kb(users: List[Tuple[int, str]], back_to: str = "admin:back") -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	for uid, title in users:
		kb.button(text=title, callback_data=f"user:view:{uid}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


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
