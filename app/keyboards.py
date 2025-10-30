from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup
from typing import List, Tuple


def admin_menu_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="📇 Карты", callback_data="admin:cards")
	kb.button(text="👥 Пользователи", callback_data="admin:users")
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
