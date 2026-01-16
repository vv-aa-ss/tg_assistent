
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import (
	InlineKeyboardMarkup,
	InlineKeyboardButton,
	ReplyKeyboardMarkup,
	KeyboardButton,
)
from typing import Dict, Iterable, List, Optional, Set, Tuple, Any


def admin_menu_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="💵 Наличные", callback_data="admin:cash")
	kb.button(text="📇 Безнал", callback_data="admin:cards")
	kb.button(text="👥 Пользователи", callback_data="admin:users")
	kb.button(text="₿ Крипта", callback_data="admin:crypto")
	kb.button(text="⚙️ Настройки", callback_data="admin:settings")
	kb.button(text="💰 Расходы", callback_data="admin:expenses")
	kb.adjust(2, 2, 2)
	return kb.as_markup()


def client_menu_kb() -> ReplyKeyboardMarkup:
	"""Меню для пользователей из группы 'Пользователи' (широкие кнопки снизу, как на скрине)."""
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text="🚀 Купить"),
				KeyboardButton(text="⚡ Продать"),
			],
			[
				KeyboardButton(text="❓ Задать вопрос"),
			]
		],
		resize_keyboard=True,
	)


def buy_country_kb() -> ReplyKeyboardMarkup:
	"""Подменю 'Купить': выбор страны."""
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text="🇷🇺 Россия"),
				KeyboardButton(text="🇧🇾 Беларусь"),
			],
			[
				KeyboardButton(text="⬅️ Назад"),
			],
		],
		resize_keyboard=True,
	)


def buy_crypto_kb() -> ReplyKeyboardMarkup:
	"""Подменю 'Купить': выбор криптовалюты после выбора страны."""
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text="Bitcoin - BTC"),
			],
			[
				KeyboardButton(text="Litecoin - LTC"),
			],
			[
				KeyboardButton(text="USDT - TRC20"),
			],
			[
				KeyboardButton(text="Monero - XMR"),
			],
			[
				KeyboardButton(text="⬅️ Назад"),
			],
		],
		resize_keyboard=True,
		one_time_keyboard=False,
	)


def sell_crypto_kb() -> ReplyKeyboardMarkup:
	"""Подменю 'Продать': выбор криптовалюты."""
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text="Bitcoin - BTC"),
			],
			[
				KeyboardButton(text="Litecoin - LTC"),
			],
			[
				KeyboardButton(text="USDT - TRC20"),
			],
			[
				KeyboardButton(text="Monero - XMR"),
			],
			[
				KeyboardButton(text="⬅️ Назад"),
			],
		],
		resize_keyboard=True,
		one_time_keyboard=False,
	)


def sell_confirmation_kb() -> InlineKeyboardMarkup:
	"""Клавиатура для подтверждения продажи"""
	kb = InlineKeyboardBuilder()
	kb.button(text="✅ Согласен", callback_data="sell:confirm:yes")
	kb.button(text="❌ Не согласен", callback_data="sell:confirm:no")
	kb.adjust(2)
	return kb.as_markup()


def sell_order_admin_kb(sell_order_id: int) -> InlineKeyboardMarkup:
	"""Клавиатура для админа при работе со сделкой на продажу"""
	kb = InlineKeyboardBuilder()
	kb.button(text="💬 Написать сообщение", callback_data=f"sell:order:message:{sell_order_id}")
	kb.button(text="✅ Завершить", callback_data=f"sell:order:complete:{sell_order_id}")
	kb.adjust(1)
	return kb.as_markup()


def order_user_reply_kb(order_id: int) -> InlineKeyboardMarkup:
	"""Клавиатура для ответа пользователя на сообщение админа по обычной заявке"""
	kb = InlineKeyboardBuilder()
	kb.button(text="💬 Ответить", callback_data=f"order:user:reply:{order_id}")
	kb.adjust(1)
	return kb.as_markup()

def sell_order_user_reply_kb(order_id: int) -> InlineKeyboardMarkup:
	"""Клавиатура для пользователя с кнопкой 'Ответить' по сделке на продажу"""
	kb = InlineKeyboardBuilder()
	kb.button(text="💬 Ответить", callback_data=f"sell:order:user:reply:{order_id}")
	kb.adjust(1)
	return kb.as_markup()


def admin_settings_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="👥 Пользователи", callback_data="settings:users")
	kb.button(text="🧮 Расчет покупки", callback_data="settings:buy_calc")
	kb.button(text="⬅️ Назад", callback_data="admin:back")
	kb.adjust(1)
	return kb.as_markup()


def cards_groups_kb(groups: List[Dict], back_to: str = "admin:back") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для списка групп карт в главном меню.
	
	Args:
		groups: Список словарей с информацией о группах
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	
	# Добавляем кнопки групп
	for group in groups:
		group_name = group.get("name", "")
		group_id = group.get("id")
		kb.button(text=f"📁 {group_name}", callback_data=f"cards:group:{group_id}")
	
	# Добавляем кнопку "Вне групп" для карт без группы
	kb.button(text="📋 Вне групп", callback_data="cards:group:0")
	
	# Добавляем остальные кнопки
	kb.button(text="➕ Добавить карту", callback_data="card:add")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	# Формируем параметры для adjust: группы по 1 в ряд, остальные по 1
	adjust_params = [1] * (len(groups) + 1)  # Группы + "Вне групп"
	adjust_params.extend([1, 1])  # Добавить карту, Назад
	kb.adjust(*adjust_params)
	
	return kb.as_markup()


def cards_list_kb(cards: List[Tuple[int, str]], with_add: bool = True, back_to: str = "admin:cards", group_id: Optional[int] = None, card_groups: Optional[Dict[int, str]] = None) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для списка карт.
	
	Args:
		cards: Список кортежей (card_id, card_name)
		with_add: Показывать ли кнопку "Добавить карту"
		back_to: Callback data для кнопки "Назад"
		group_id: ID группы (для отображения кнопки "Удалить группу")
		card_groups: Словарь {card_id: group_name} для отображения группы в скобках
	"""
	kb = InlineKeyboardBuilder()
	# Добавляем все кнопки карт
	for cid, name in cards:
		# Если есть информация о группе, добавляем её в скобках
		if card_groups and cid in card_groups:
			group_name = card_groups[cid]
			kb.button(text=f"💳 {name} ({group_name})", callback_data=f"card:view:{cid}")
		else:
			kb.button(text=f"💳 {name}", callback_data=f"card:view:{cid}")
	
	# Добавляем остальные кнопки
	if with_add:
		kb.button(text="➕ Добавить карту", callback_data="card:add")
	
	# Если это список карт группы, показываем кнопку "Удалить группу"
	if group_id is not None:
		kb.button(text="🗑️ Удалить группу", callback_data=f"cards:delete_group:{group_id}")
	
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	# Формируем параметры для adjust: карты по 2 в ряд, остальные по 1
	# Количество дополнительных кнопок
	additional_buttons = 1  # Назад (всегда есть)
	if group_id is not None:
		additional_buttons += 1  # Удалить группу
	if with_add:
		additional_buttons += 1  # Добавить карту
	
	if len(cards) > 0:
		# Для карт: по 2 в ряд
		adjust_params = [2] * (len(cards) // 2)
		if len(cards) % 2 == 1:
			adjust_params.append(1)  # Последняя карта одна, если нечетное количество
		# Для остальных кнопок: по 1 в ряд
		adjust_params.extend([1] * additional_buttons)
		kb.adjust(*adjust_params)
	else:
		# Если карт нет, все кнопки по одной
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


def user_menu_button_kb(user_id: int, card_id: Optional[int] = None) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру с кнопкой "Меню пользователя" для перехода к user_view.
	
	Args:
		user_id: ID пользователя в базе данных
		card_id: ID карты для возврата к реквизитам (опционально)
	"""
	kb = InlineKeyboardBuilder()
	if card_id is not None:
		kb.button(text="👤 Меню пользователя", callback_data=f"user:view:{user_id}:card:{card_id}")
	else:
		kb.button(text="👤 Меню пользователя", callback_data=f"user:view:{user_id}")
	return kb.as_markup()


def stat_u_menu_kb(back_to: str = "stat_u:menu") -> InlineKeyboardMarkup:
	"""Клавиатура для меню статистики пользователей"""
	kb = InlineKeyboardBuilder()
	kb.button(text="🔥 По активности", callback_data="stat_u:activity")
	kb.button(text="🕒 По давности", callback_data="stat_u:inactivity")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(2, 1)
	return kb.as_markup()


def cards_select_kb(cards: List[Tuple[int, str]], back_to: str) -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	for cid, name in cards:
		kb.button(text=f"💳 {name}", callback_data=f"select:card:{cid}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def card_groups_select_kb(groups: List[Dict], back_to: str = "admin:back", recent_cards: Optional[List[Tuple[int, str]]] = None, forward_mode: bool = False, recent_cards_groups: Optional[Dict[int, str]] = None) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора группы карт.
	
	Args:
		groups: Список словарей с информацией о группах
		back_to: Callback data для кнопки "Назад"
		recent_cards: Список кортежей (card_id, card_name) последних используемых карт (максимум 4)
		forward_mode: Если True, используется callback для пересылки (forward:group:)
		recent_cards_groups: Словарь {card_id: group_name} для отображения группы последних карт
	"""
	kb = InlineKeyboardBuilder()
	for group in groups:
		group_name = group.get("name", "")
		group_id = group.get("id")
		# Определяем, какой callback использовать в зависимости от back_to и forward_mode
		if back_to.startswith("add_data:back:"):
			# Используется в командах /add и /rate
			kb.button(text=f"📁 {group_name}", callback_data=f"{back_to}:group:{group_id}")
		elif forward_mode:
			# Используется при пересылке
			kb.button(text=f"📁 {group_name}", callback_data=f"forward:group:{group_id}")
		else:
			# Стандартное использование
			kb.button(text=f"📁 {group_name}", callback_data=f"cards:group:{group_id}")
	# Добавляем кнопку для карт без группы
	if back_to.startswith("add_data:back:"):
		kb.button(text="📋 Без группы", callback_data=f"{back_to}:group:0")
	elif forward_mode:
		kb.button(text="📋 Без группы", callback_data="forward:group:0")
	else:
		kb.button(text="📋 Без группы", callback_data="cards:group:0")
	
	# Добавляем кнопки последних используемых карт (максимум 4)
	if recent_cards and back_to.startswith("add_data:back:"):
		# Добавляем только если используется в /add или /rate
		for card_id, card_name in recent_cards[:4]:  # Ограничиваем до 4 карт
			# Если есть информация о группе, добавляем её в скобках
			if recent_cards_groups and card_id in recent_cards_groups:
				group_name = recent_cards_groups[card_id]
				kb.button(text=f"💳 {card_name} ({group_name})", callback_data=f"card:view:{card_id}")
			else:
				kb.button(text=f"💳 {card_name}", callback_data=f"card:view:{card_id}")
	
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	# Настраиваем расположение: группы по 1 в ряд, последние карты по 2 в ряд (если есть), затем "Назад"
	if recent_cards and back_to.startswith("add_data:back:"):
		recent_count = min(len(recent_cards), 4)
		adjust_params = [1] * (len(groups) + 1)  # Группы + "Без группы"
		if recent_count > 0:
			# Последние карты по 2 в ряд
			recent_rows = (recent_count + 1) // 2  # Количество рядов для последних карт
			for i in range(recent_rows):
				if i == recent_rows - 1 and recent_count % 2 == 1:
					adjust_params.append(1)  # Последняя карта одна, если нечетное количество
				else:
					adjust_params.append(2)  # По 2 карты в ряд
		adjust_params.append(1)  # Кнопка "Назад"
		kb.adjust(*adjust_params)
	else:
		# Стандартное расположение: все по 1 в ряд
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


def user_action_kb(user_id: int, back_to: str = "admin:users", has_access: Optional[bool] = None) -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="Карты", callback_data=f"user:bind:{user_id}")
	if has_access is None:
		kb.button(text="🔑 Доступ", callback_data=f"user:access:toggle:{user_id}")
	else:
		kb.button(
			text=("🚫 Забрать доступ" if has_access else "✅ Дать доступ"),
			callback_data=f"user:access:toggle:{user_id}",
		)
	kb.button(text="🗑️ Удалить пользователя", callback_data=f"user:delete:{user_id}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def card_action_kb(card_id: int, back_to: str = "admin:cards") -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="✏️ Изменить название", callback_data=f"card:edit_name:{card_id}")
	kb.button(text="✏️ Изменить реквизиты", callback_data=f"card:edit:{card_id}")
	kb.button(text="➕ Реквизиты", callback_data=f"card:add_requisite:{card_id}")
	kb.button(text="🔗 Привязать ячейку", callback_data=f"card:bind_column:{card_id}")
	kb.button(text="📁 Группы", callback_data=f"card:groups:{card_id}")
	kb.button(text="🗑️ Удалить карту", callback_data=f"card:delete:{card_id}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def requisites_list_kb(requisites: List[Dict], card_id: int, has_user_message: bool = False, back_to: str = None) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру со списком реквизитов карты.
	
	Args:
		requisites: Список реквизитов из таблицы card_requisites
		card_id: ID карты
		has_user_message: Есть ли user_message (основной реквизит)
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	
	# Добавляем основной реквизит (user_message) если есть
	if has_user_message:
		kb.button(text="📝 Основной реквизит", callback_data=f"req:edit_main:{card_id}")
	
	# Добавляем дополнительные реквизиты
	for idx, req in enumerate(requisites, 1):
		# Обрезаем текст для отображения в кнопке (максимум 50 символов)
		text_preview = req['requisite_text'][:50]
		if len(req['requisite_text']) > 50:
			text_preview += "..."
		kb.button(text=f"📄 Реквизит {idx}: {text_preview}", callback_data=f"req:select:{req['id']}")
	
	# Кнопка "Назад"
	if back_to:
		kb.button(text="⬅️ Назад", callback_data=back_to)
	else:
		kb.button(text="⬅️ Назад", callback_data=f"card:view:{card_id}")
	
	kb.adjust(1)
	return kb.as_markup()


def requisite_action_kb(requisite_id: int = None, card_id: int = None, is_main: bool = False, back_to: str = None) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру с действиями для реквизита.
	
	Args:
		requisite_id: ID реквизита (для дополнительных реквизитов)
		card_id: ID карты (для основного реквизита)
		is_main: Является ли это основным реквизитом (user_message)
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	
	if is_main:
		edit_callback = f"req:edit:main:{card_id}"
		delete_callback = f"req:delete:main:{card_id}"
	else:
		edit_callback = f"req:edit:{requisite_id}"
		delete_callback = f"req:delete:{requisite_id}"
	
	kb.button(text="✏️ Изменить", callback_data=edit_callback)
	kb.button(text="🗑️ Удалить", callback_data=delete_callback)
	
	if back_to:
		kb.button(text="⬅️ Назад", callback_data=back_to)
	else:
		if is_main and card_id:
			kb.button(text="⬅️ Назад", callback_data=f"card:edit:{card_id}")
		elif requisite_id:
			# Для дополнительных реквизитов нужно получить card_id, но проще вернуться к списку
			kb.button(text="⬅️ Назад", callback_data="card:edit:0")  # Будет исправлено в обработчике
		else:
			kb.button(text="⬅️ Назад", callback_data="admin:cards")
	
	kb.adjust(2, 1)
	return kb.as_markup()


def card_groups_list_kb(groups: List[Dict], card_id: int, back_to: str = "admin:cards") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для списка групп карт.
	
	Args:
		groups: Список словарей с информацией о группах
		card_id: ID карты
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for group in groups:
		group_name = group.get("name", "")
		group_id = group.get("id")
		kb.button(text=f"📁 {group_name}", callback_data=f"card:select_group:{card_id}:{group_id}")
	kb.button(text="➕ Новая группа", callback_data=f"card:new_group:{card_id}")
	kb.button(text="⬅️ Назад", callback_data=f"card:view:{card_id}")
	kb.adjust(1)
	return kb.as_markup()


def crypto_list_kb(crypto_columns: List[Dict], back_to: str = "admin:back") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для списка криптовалют с их адресами столбцов.
	
	Args:
		crypto_columns: Список словарей с ключами crypto_type и column
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for crypto in crypto_columns:
		crypto_type = crypto.get("crypto_type", "")
		column = crypto.get("column", "")
		kb.button(text=f"{crypto_type} → {column}", callback_data=f"crypto:edit:{crypto_type}")
	kb.button(text="➕ Новая", callback_data="crypto:new")
	kb.button(text="🗑️ Удалить", callback_data="crypto:delete_list")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def crypto_delete_kb(crypto_columns: List[Dict], back_to: str = "admin:crypto") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора криптовалюты для удаления.
	
	Args:
		crypto_columns: Список словарей с ключами crypto_type и column
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for crypto in crypto_columns:
		crypto_type = crypto.get("crypto_type", "")
		column = crypto.get("column", "")
		kb.button(text=f"{crypto_type} → {column}", callback_data=f"crypto:delete:{crypto_type}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def cash_list_kb(cash_columns: List[Dict], back_to: str = "admin:back") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для списка наличных с их адресами столбцов.
	
	Args:
		cash_columns: Список словарей с ключами cash_name, column, currency, display_name
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for cash in cash_columns:
		cash_name = cash.get("cash_name", "")
		column = cash.get("column", "")
		display_name = cash.get("display_name", "")
		currency = cash.get("currency", "RUB")
		# Показываем имя валюты, если оно есть, иначе название
		display = display_name if display_name else cash_name
		kb.button(text=f"{display} → {column} ({currency})", callback_data=f"cash:edit:{cash_name}")
	kb.button(text="➕ Новая", callback_data="cash:new")
	kb.button(text="🗑️ Удалить", callback_data="cash:delete_list")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def cash_edit_menu_kb(cash_name: str) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для меню редактирования валюты.
	
	Args:
		cash_name: Название валюты
	"""
	kb = InlineKeyboardBuilder()
	kb.button(text="📍 Ячейка", callback_data=f"cash:edit_column:{cash_name}")
	kb.button(text="💵 Имя валюты", callback_data=f"cash:edit_display_name:{cash_name}")
	kb.button(text="💰 Номинал валюты", callback_data=f"cash:edit_currency:{cash_name}")
	kb.button(text="⬅️ Назад", callback_data="admin:cash")
	kb.adjust(1)
	return kb.as_markup()


def cash_currency_select_kb(cash_name: str, current_currency: str) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора номинала валюты.
	
	Args:
		cash_name: Название валюты
		current_currency: Текущий номинал валюты
	"""
	kb = InlineKeyboardBuilder()
	
	# Кнопки валют
	currencies = ["RUB", "BYN", "USD"]
	for currency in currencies:
		# Выделяем текущую валюту
		text = f"✅ {currency}" if currency == current_currency else currency
		kb.button(text=text, callback_data=f"cash:set_currency:{cash_name}:{currency}")
	
	# Кнопка "Назад"
	kb.button(text="⬅️ Назад", callback_data=f"cash:edit:{cash_name}")
	
	# Первый ряд - три валюты, второй ряд - назад
	kb.adjust(3, 1)
	return kb.as_markup()


def cash_delete_kb(cash_columns: List[Dict], back_to: str = "admin:cash") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для удаления наличных.
	
	Args:
		cash_columns: Список словарей с ключами cash_name и column
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for cash in cash_columns:
		cash_name = cash.get("cash_name", "")
		kb.button(text=f"🗑️ {cash_name}", callback_data=f"cash:delete:{cash_name}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def cash_select_kb(cash_columns: List[Dict], mode: str = "add", back_to: str = "add_data:back") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора названия наличных в командах /add и /rate.
	
	Args:
		cash_columns: Список словарей с ключами cash_name, column, currency, display_name
		mode: Режим работы ("add" или "rate")
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for cash in cash_columns:
		cash_name = cash.get("cash_name", "")
		display_name = cash.get("display_name", "")
		# Показываем имя валюты, если оно есть, иначе название
		display = display_name if display_name else cash_name
		kb.button(text=display, callback_data=f"add_data:cash_select:{cash_name}:{mode}")
	kb.button(text="⬅️ Назад", callback_data=f"{back_to}:{mode}")
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


def xmr_select_kb(back_to: str = "multi:back_to_main") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора XMR-1, XMR-2, XMR-3.
	Три кнопки в ряд, под ними кнопка "Назад".
	"""
	kb = InlineKeyboardBuilder()
	
	# Три кнопки XMR в ряд
	kb.button(text="XMR-1", callback_data="multi:select:xmr:1")
	kb.button(text="XMR-2", callback_data="multi:select:xmr:2")
	kb.button(text="XMR-3", callback_data="multi:select:xmr:3")
	
	# Кнопка "Назад"
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	# Три кнопки в ряд, затем "Назад"
	kb.adjust(3, 1)
	return kb.as_markup()


def crypto_edit_kb(current_currency: str, amount: float) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для редактирования криптовалюты.
	Первый ряд: две кнопки с другими типами монет (если текущая BTC - LTC и XMR, и т.д.)
	Второй ряд: кнопка "Количество"
	Третий ряд: кнопка "Назад"
	"""
	kb = InlineKeyboardBuilder()
	
	# Определяем какие кнопки показать в первом ряду
	all_currencies = ["BTC", "LTC", "XMR"]
	other_currencies = [c for c in all_currencies if c != current_currency]
	
	# Добавляем кнопки с другими типами монет
	for currency in other_currencies[:2]:  # Берем первые две
		kb.button(text=currency, callback_data=f"crypto:change_type:{currency}")
	
	# Кнопка "Количество" (теперь пользователь вводит USD)
	kb.button(text="Количество", callback_data="crypto:change_amount")
	
	# Кнопка "Назад"
	kb.button(text="⬅️ Назад", callback_data="crypto:back")
	
	# Первый ряд - две кнопки типов, второй ряд - количество, третий - назад
	kb.adjust(2, 1, 1)
	return kb.as_markup()


def cash_edit_kb(current_currency: str, amount: int) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для редактирования наличных.
	Первый ряд: две кнопки с валютами (BYN и RUB)
	Второй ряд: кнопка "Изменить"
	Третий ряд: кнопка "Назад"
	"""
	kb = InlineKeyboardBuilder()
	
	# Определяем какие кнопки показать в первом ряду
	all_currencies = ["BYN", "RUB"]
	other_currencies = [c for c in all_currencies if c != current_currency]
	
	# Добавляем кнопки с валютами
	for currency in other_currencies:
		kb.button(text=currency, callback_data=f"cash:change_currency:{currency}")
	
	# Кнопка "Изменить"
	kb.button(text="Изменить", callback_data="cash:change_amount")
	
	# Кнопка "Назад"
	kb.button(text="⬅️ Назад", callback_data="cash:back")
	
	# Первый ряд - две кнопки валют, второй ряд - изменить, третий - назад
	kb.adjust(2, 1, 1)
	return kb.as_markup()


def crypto_select_kb(back_to: str = "multi:back_to_main", show_confirm: bool = True, crypto_columns: Optional[List[Dict[str, Any]]] = None) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора криптовалюты на основе данных из crypto_columns.
	Исключает монеты с названием "🐿" и "💵".
	Все XMR объединяются в одну кнопку.
	
	Args:
		back_to: Callback data для кнопки "Назад"
		show_confirm: Показывать ли кнопку "Подтвердить"
		crypto_columns: Список криптовалют из БД (если None, используется хардкод для обратной совместимости)
	"""
	kb = InlineKeyboardBuilder()
	
	# Если передан список криптовалют из БД, используем его
	if crypto_columns:
		# Фильтруем: исключаем монеты с названием "🐿" и "💵"
		filtered_crypto = [
			crypto for crypto in crypto_columns
			if crypto.get("crypto_type", "") not in ["🐿", "💵"]
		]
		
		# Разделяем на XMR и остальные
		xmr_types = []
		other_crypto = []
		
		for crypto in filtered_crypto:
			crypto_type = crypto.get("crypto_type", "")
			if crypto_type.startswith("XMR"):
				xmr_types.append(crypto_type)
			else:
				other_crypto.append(crypto_type)
		
		# Добавляем кнопки для остальных криптовалют (не XMR)
		for crypto_type in other_crypto:
			kb.button(text=crypto_type, callback_data=f"crypto:select:{crypto_type}")
		
		# Добавляем одну кнопку XMR, если есть хотя бы один XMR
		if xmr_types:
			kb.button(text="XMR", callback_data="crypto:select:XMR")
		
		# Определяем количество кнопок для adjust
		other_count = len(other_crypto)
		has_xmr = len(xmr_types) > 0
		
		# Кнопка "Подтвердить" (если нужно)
		if show_confirm:
			kb.button(text="✅ Подтвердить", callback_data="multi:confirm")
		
		# Кнопка "Назад"
		kb.button(text="⬅️ Назад", callback_data=back_to)
		
		# Настраиваем расположение
		# Сначала размещаем остальные криптовалюты по 3 в ряд
		adjust_list = []
		if other_count > 0:
			full_rows = other_count // 3
			remainder = other_count % 3
			if full_rows > 0:
				adjust_list.extend([3] * full_rows)
			if remainder > 0:
				adjust_list.append(remainder)
		
		# XMR в отдельный ряд (если есть)
		if has_xmr:
			adjust_list.append(1)
		
		# Добавляем кнопки подтверждения и назад
		if show_confirm:
			adjust_list.append(1)  # Подтвердить
		adjust_list.append(1)  # Назад
		
		if adjust_list:
			kb.adjust(*adjust_list)
	else:
		# Обратная совместимость: используем хардкод, если список не передан
		kb.button(text="BTC", callback_data="crypto:select:BTC")
		kb.button(text="LTC", callback_data="crypto:select:LTC")
		kb.button(text="XMR", callback_data="crypto:select:XMR")
		kb.button(text="USDT", callback_data="crypto:select:USDT")
		
		if show_confirm:
			kb.button(text="✅ Подтвердить", callback_data="multi:confirm")
		
		kb.button(text="⬅️ Назад", callback_data=back_to)
		
		if show_confirm:
			kb.adjust(3, 1, 1, 1)
		else:
			kb.adjust(3, 1, 1)
	
	return kb.as_markup()


def add_data_type_kb(
	mode: str = "add", 
	back_to: str = "admin:back", 
	data: Optional[Dict[str, Any]] = None,
	recent_cryptos: Optional[List[str]] = None,
	recent_cards: Optional[List[Tuple[int, str, Optional[str]]]] = None,
	recent_cash: Optional[List[Tuple[str, str]]] = None
) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора типа данных в командах /add и /rate.
	Показывает все сохраненные блоки + текущий блок.
	
	Args:
		mode: Режим работы ("add", "rate" или "move")
		back_to: Callback data для кнопки "Назад"
		data: Словарь с выбранными данными (saved_blocks, crypto_data, cash_data, card_data, card_cash_data)
		recent_cryptos: Список последних используемых криптовалют (например, ['BTC', 'LTC', 'XMR-1'])
		recent_cards: Список кортежей (card_id, card_name, group_name) последних используемых карт
			group_name может быть None, если карта не привязана к группе
		recent_cash: Список кортежей (cash_name, display_name) последних используемых наличных
	"""
	kb = InlineKeyboardBuilder()
	
	# Получаем сохраненные блоки
	saved_blocks = data.get("saved_blocks", []) if data else []
	
	# Добавляем кнопки для каждого сохраненного блока
	for block_idx, block in enumerate(saved_blocks):
		crypto_text = "🪙"
		cash_text = "💵"
		card_text = "💳"
		
		block_crypto = block.get("crypto_data")
		if block_crypto:
			currency = block_crypto.get("currency", "")
			usd_amount = block_crypto.get("usd_amount", 0)
			xmr_number = block_crypto.get("xmr_number")
			if xmr_number:
				crypto_text = f"{int(usd_amount)}USD (XMR-{xmr_number})"
			else:
				crypto_text = f"{int(usd_amount)}USD ({currency})"
		
		block_cash = block.get("cash_data")
		if block_cash:
			amount = block_cash.get("value", 0)
			cash_name = block_cash.get("cash_name", "Наличные")
			# Берем первую букву категории
			cash_letter = cash_name[0].upper() if cash_name else "Н"
			cash_text = f"{amount} {cash_letter}"
		
		block_card = block.get("card_data")
		block_card_cash = block.get("card_cash_data")
		if block_card:
			card_name = block_card.get("card_name", "")
			if block_card_cash:
				amount = block_card_cash.get("value", 0)
				card_text = f"{card_name}: {amount}р."
			else:
				card_text = card_name
		
		# Каждый сохраненный блок - отдельная строка с индексом блока в callback_data
		kb.button(text=crypto_text, callback_data=f"add_data:type:crypto:block:{block_idx}:{mode}")
		kb.button(text=card_text, callback_data=f"add_data:type:card:block:{block_idx}:{mode}")
		kb.button(text=cash_text, callback_data=f"add_data:type:cash:block:{block_idx}:{mode}")
	
	# Определяем текст для кнопок текущего блока
	crypto_text = "🪙"
	cash_text = "💵"
	card_text = "💳"
	
	if data:
		crypto_data = data.get("crypto_data")
		if crypto_data:
			currency = crypto_data.get("currency", "")
			usd_amount = crypto_data.get("usd_amount", 0)
			xmr_number = crypto_data.get("xmr_number")
			if xmr_number:
				crypto_text = f"{int(usd_amount)}USD (XMR-{xmr_number})"
			else:
				crypto_text = f"{int(usd_amount)}USD ({currency})"
		
		cash_data = data.get("cash_data")
		if cash_data:
			amount = cash_data.get("value", 0)
			cash_name = cash_data.get("cash_name", "Наличные")
			# Берем первую букву категории
			cash_letter = cash_name[0].upper() if cash_name else "Н"
			cash_text = f"{amount} {cash_letter}"
		
		card_data = data.get("card_data")
		card_cash_data = data.get("card_cash_data")
		if card_data:
			card_name = card_data.get("card_name", "")
			if card_cash_data:
				amount = card_cash_data.get("value", 0)
				card_text = f"{card_name}: {amount}р."
			else:
				card_text = card_name
	
	# Добавляем текущий блок (используем "current" вместо индекса)
	kb.button(text=crypto_text, callback_data=f"add_data:type:crypto:current:{mode}")
	kb.button(text=card_text, callback_data=f"add_data:type:card:current:{mode}")
	kb.button(text=cash_text, callback_data=f"add_data:type:cash:current:{mode}")
	
	# Кнопка "+" для добавления еще одного блока
	kb.button(text="➕", callback_data=f"add_data:add_block:{mode}")
	
	# Кнопка "Примечание" только для режима rate
	if mode == "rate":
		kb.button(text="📝 Примечание", callback_data=f"add_data:note:{mode}")
	
	# Подготавливаем отдельные списки для каждого типа
	# Формат: (display_name, callback_data)
	crypto_items = []
	card_items = []
	cash_items = []
	
	# Формируем список криптовалют (ограничиваем до 3 элементов)
	if recent_cryptos:
		for crypto_id in recent_cryptos[:3]:  # Берем только первые 3 криптовалюты
			if crypto_id.startswith("XMR-"):
				display_name = crypto_id  # "XMR-1", "XMR-2", "XMR-3"
			else:
				display_name = crypto_id  # "BTC", "LTC", "USDT", etc.
			crypto_items.append((display_name, f"add_data:quick:crypto:{crypto_id}:{mode}"))
	
	# Формируем список карт
	if recent_cards:
		for card_tuple in recent_cards:
			card_id = card_tuple[0]
			card_name = card_tuple[1]
			group_name = card_tuple[2] if len(card_tuple) > 2 else None
			# Отображаем группу в скобках, если она есть
			if group_name:
				display_name = f"{card_name} ({group_name})"
			else:
				display_name = card_name
			card_items.append((display_name, f"add_data:quick:card:{card_id}:{mode}"))
	
	# Формируем список наличных
	if recent_cash:
		for cash_tuple in recent_cash:
			if isinstance(cash_tuple, tuple) and len(cash_tuple) == 2:
				cash_name, display_name = cash_tuple
			else:
				# Обратная совместимость: если передан просто cash_name
				cash_name = cash_tuple
				display_name = cash_name
			cash_items.append((display_name, f"add_data:quick:cash:{cash_name}:{mode}"))
	
	# Формируем кнопки по столбцам:
	# Столбец 1: криптовалюты
	# Столбец 2: карты (первые половина, если карт > 3, иначе все)
	# Столбец 3: карты (вторая половина, если карт > 3, иначе наличные)
	
	# Разделяем карты на две части для столбцов 2 и 3
	# Столбец 2: первые карты (по количеству строк крипты)
	# Столбец 3: остальные карты
	card_items_col2 = []
	card_items_col3 = []
	
	# Распределяем карты на два столбца
	# Распределяем так, чтобы максимально заполнить все 3 строки
	# Если карт >= 6, то по 3 в каждом столбце
	# Если карт < 6, то распределяем так, чтобы в каждом столбце было примерно поровну
	card_items_col2 = []
	card_items_col3 = []
	
	if len(card_items) > 0:
		# Ограничиваем до 6 карт максимум (по 3 в каждом столбце для 3 строк)
		card_items_limited = card_items[:6]
		
		# Делим пополам: первые идут в столбец 2, вторые в столбец 3
		# Это обеспечит максимальное заполнение всех строк
		mid = (len(card_items_limited) + 1) // 2
		card_items_col2 = card_items_limited[:mid]
		card_items_col3 = card_items_limited[mid:]
	
	# Вычисляем максимальное количество строк
	# Всегда 3 строки, если есть хотя бы одна крипта
	if len(crypto_items) > 0:
		max_rows = 3
	else:
		# Если нет крипт, используем максимум из остальных столбцов, но не больше 3
		max_rows = min(3, max(len(card_items_col2), len(card_items_col3), len(cash_items)))
	
	recent_items_combined = []
	total_items = 0
	max_total = 9
	
	# Список для хранения количества элементов в каждой строке
	rows_sizes = []
	
	# Индекс для наличных
	cash_idx = 0
	
	for row in range(max_rows):
		if total_items >= max_total:
			break
		
		row_items = []
		
		# Столбец 1: криптовалюта
		if row < len(crypto_items) and total_items < max_total:
			row_items.append(crypto_items[row])
			total_items += 1
		
		# Столбец 2: карта (по номеру строки)
		if row < len(card_items_col2) and total_items < max_total:
			row_items.append(card_items_col2[row])
			total_items += 1
		
		# Столбец 3: карта (по номеру строки) или наличные
		if total_items < max_total:
			# Сначала пытаемся взять карту из столбца 3
			if row < len(card_items_col3):
				row_items.append(card_items_col3[row])
				total_items += 1
			# Если карт в столбце 3 нет, пытаемся взять наличные
			elif cash_idx < len(cash_items):
				row_items.append(cash_items[cash_idx])
				cash_idx += 1
				total_items += 1
		
		# Добавляем все элементы строки (даже если строка неполная)
		if row_items:
			recent_items_combined.extend(row_items)
			rows_sizes.append(len(row_items))
	
	# Добавляем кнопки
	for display_name, callback_data in recent_items_combined:
		kb.button(text=display_name, callback_data=callback_data)
	
	# Подтвердить и Назад
	kb.button(text="✅ Подтвердить", callback_data=f"add_data:confirm:{mode}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	# Настраиваем расположение:
	# saved_blocks + 1 (текущий блок) строк по 3 кнопки
	# затем 1 кнопка "+"
	# затем 1 кнопка "Примечание" (если rate)
	# затем до 9 кнопок быстрого доступа (по 3 в ряд) - криптовалюты, карты, наличные
	# затем 2 кнопки ("Подтвердить" и "Назад")
	adjust_list = [3] * (len(saved_blocks) + 1) + [1]  # Блоки + "+"
	if mode == "rate":
		adjust_list.append(1)  # Кнопка "Примечание"
	
	# Быстрый доступ (до 9 элементов, по 3 в ряд)
	# Используем реальные размеры строк для правильного формирования adjust_list
	if rows_sizes:
		for row_size in rows_sizes:
			adjust_list.append(row_size)
	
	# Подтвердить и Назад
	adjust_list.append(2)
	
	kb.adjust(*adjust_list)
	return kb.as_markup()


def delete_confirmation_kb() -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для подтверждения удаления с кнопками "Да" и "Нет".
	"""
	kb = InlineKeyboardBuilder()
	kb.button(text="✅ Да", callback_data="delete:confirm:yes")
	kb.button(text="❌ Нет", callback_data="delete:confirm:no")
	kb.adjust(2)
	return kb.as_markup()


def add_data_xmr_select_kb(mode: str = "add", back_to: str = "add_data:back") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора номера XMR (1, 2, 3).
	
	Args:
		mode: Режим работы ("add" или "rate")
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	
	kb.button(text="XMR-1", callback_data=f"add_data:xmr:1:{mode}")
	kb.button(text="XMR-2", callback_data=f"add_data:xmr:2:{mode}")
	kb.button(text="XMR-3", callback_data=f"add_data:xmr:3:{mode}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	kb.adjust(3, 1)
	return kb.as_markup()


def multipliers_settings_kb(multiplier_byn: float, multiplier_rub: float) -> InlineKeyboardMarkup:
	"""Клавиатура для настройки коэффициентов"""
	kb = InlineKeyboardBuilder()
	kb.button(text=f"🇧🇾 BYN: {multiplier_byn}", callback_data="settings:multiplier:byn")
	kb.button(text=f"🇷🇺 RUB: {multiplier_rub}", callback_data="settings:multiplier:rub")
	kb.button(text="⬅️ Назад", callback_data="admin:settings")
	kb.adjust(1)
	return kb.as_markup()


def markup_percents_settings_kb(percent_small: float, percent_large: float) -> InlineKeyboardMarkup:
	"""Клавиатура для настройки процентов наценки"""
	kb = InlineKeyboardBuilder()
	kb.button(text=f"📉 Меньше $100: {percent_small}%", callback_data="settings:markup_percent:small")
	kb.button(text=f"📈 Больше $100: {percent_large}%", callback_data="settings:markup_percent:large")
	kb.button(text="⬅️ Назад", callback_data="admin:settings")
	kb.adjust(1)
	return kb.as_markup()


def buy_calc_settings_kb(settings: dict) -> InlineKeyboardMarkup:
	"""Клавиатура для настройки расчета покупки"""
	kb = InlineKeyboardBuilder()
	kb.button(text=f"📉 $0-100: {settings['buy_markup_percent_small']}%", callback_data="settings:buy_calc:edit:buy_markup_percent_small")
	kb.button(text=f"📈 $101-449: {settings['buy_markup_percent_101_449']}%", callback_data="settings:buy_calc:edit:buy_markup_percent_101_449")
	kb.button(text=f"📈 $450-699: {settings['buy_markup_percent_450_699']}%", callback_data="settings:buy_calc:edit:buy_markup_percent_450_699")
	kb.button(text=f"📈 $700-999: {settings['buy_markup_percent_700_999']}%", callback_data="settings:buy_calc:edit:buy_markup_percent_700_999")
	kb.button(text=f"📈 $1000-1499: {settings['buy_markup_percent_1000_1499']}%", callback_data="settings:buy_calc:edit:buy_markup_percent_1000_1499")
	kb.button(text=f"📈 $1500-1999: {settings['buy_markup_percent_1500_1999']}%", callback_data="settings:buy_calc:edit:buy_markup_percent_1500_1999")
	kb.button(text=f"📈 $2000+: {settings['buy_markup_percent_2000_plus']}%", callback_data="settings:buy_calc:edit:buy_markup_percent_2000_plus")
	kb.button(text=f"✅ Мин $: {settings['buy_min_usd']}", callback_data="settings:buy_calc:edit:buy_min_usd")
	kb.button(text=f"💵 $< {settings['buy_extra_fee_usd_low']}: +BYN {settings['buy_extra_fee_low_byn']}", callback_data="settings:buy_calc:edit:buy_extra_fee_low_byn")
	kb.button(text=f"💵 $< {settings['buy_extra_fee_usd_mid']}: +BYN {settings['buy_extra_fee_mid_byn']}", callback_data="settings:buy_calc:edit:buy_extra_fee_mid_byn")
	kb.button(text=f"💵 $< {settings['buy_extra_fee_usd_low']}: +RUB {settings['buy_extra_fee_low_rub']}", callback_data="settings:buy_calc:edit:buy_extra_fee_low_rub")
	kb.button(text=f"💵 $< {settings['buy_extra_fee_usd_mid']}: +RUB {settings['buy_extra_fee_mid_rub']}", callback_data="settings:buy_calc:edit:buy_extra_fee_mid_rub")
	kb.button(text=f"🚨 Алерт от $: {settings['buy_alert_usd_threshold']}", callback_data="settings:buy_calc:edit:buy_alert_usd_threshold")
	kb.button(text=f"💱 USD→BYN: {settings['buy_usd_to_byn_rate']}", callback_data="settings:buy_calc:edit:buy_usd_to_byn_rate")
	kb.button(text=f"💱 USD→RUB: {settings['buy_usd_to_rub_rate']}", callback_data="settings:buy_calc:edit:buy_usd_to_rub_rate")
	kb.button(text="⬅️ Назад", callback_data="admin:settings")
	kb.adjust(1)
	return kb.as_markup()


def buy_confirmation_kb() -> ReplyKeyboardMarkup:
	"""Клавиатура для подтверждения покупки криптовалюты"""
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text="✅ Согласен"),
				KeyboardButton(text="❌ Не согласен"),
			]
		],
		resize_keyboard=True,
	)


def buy_delivery_method_kb(currency_symbol: str, is_byn: bool) -> ReplyKeyboardMarkup:
	"""Клавиатура для выбора способа доставки"""
	keyboard = [
		[
			KeyboardButton(text=f"VIP (1-25 минут) (+4 {currency_symbol})" if is_byn else f"VIP (1-25 минут) (+1000 {currency_symbol})"),
		],
		[
			KeyboardButton(text="Обычная (25-80 минут)"),
		],
		[
			KeyboardButton(text="⬅️ Назад"),
		],
	]
	return ReplyKeyboardMarkup(
		keyboard=keyboard,
		resize_keyboard=True,
	)


def buy_payment_confirmed_kb() -> ReplyKeyboardMarkup:
	"""Клавиатура с кнопкой подтверждения оплаты"""
	return ReplyKeyboardMarkup(
		keyboard=[
			[
				KeyboardButton(text="ОПЛАТА СОВЕРШЕНА"),
			]
		],
		resize_keyboard=True,
	)


def order_action_kb(order_id: int, expanded: bool = False) -> InlineKeyboardMarkup:
	"""Клавиатура для действий с заявкой админом."""
	kb = InlineKeyboardBuilder()
	kb.button(text="✅ Выполнил", callback_data=f"order:completed:{order_id}")
	if expanded:
		kb.button(text="📋 Дополнительно", callback_data=f"order:details:{order_id}")
		kb.button(text="💬 Написать", callback_data=f"order:message:{order_id}")
		kb.button(text="💰 Изменить сумму сделки", callback_data=f"order:edit:amount:{order_id}")
		kb.button(text="🪙 Изменить количество крипты", callback_data=f"order:edit:crypto:{order_id}")
		kb.button(text="💳 Долг", callback_data=f"order:debt:{order_id}")
		kb.adjust(2, 2, 1)
	else:
		kb.button(text="📋 Дополнительно", callback_data=f"order:details:{order_id}:expanded")
		kb.adjust(2)
	return kb.as_markup()


def xmr_wallet_select_kb(order_id: int) -> InlineKeyboardMarkup:
	"""Клавиатура для выбора кошелька XMR при завершении заявки."""
	kb = InlineKeyboardBuilder()
	kb.button(text="XMR-1", callback_data=f"order:xmr:wallet:{order_id}:1")
	kb.button(text="XMR-2", callback_data=f"order:xmr:wallet:{order_id}:2")
	kb.button(text="XMR-3", callback_data=f"order:xmr:wallet:{order_id}:3")
	kb.adjust(3)  # Все три кнопки в один ряд
	return kb.as_markup()


def question_reply_kb(question_id: int) -> InlineKeyboardMarkup:
	"""Клавиатура для ответа на вопрос пользователя."""
	kb = InlineKeyboardBuilder()
	kb.button(text="💬 Ответить", callback_data=f"question:reply:{question_id}")
	kb.button(text="✅ Закрыть вопрос", callback_data=f"question:complete:{question_id}")
	kb.adjust(1)
	return kb.as_markup()

def question_user_reply_kb(question_id: int) -> InlineKeyboardMarkup:
	"""Клавиатура для ответа пользователя на вопрос админа"""
	kb = InlineKeyboardBuilder()
	kb.button(text="💬 Ответить", callback_data=f"question:user:reply:{question_id}")
	kb.adjust(1)
	return kb.as_markup()


def delete_message_kb() -> InlineKeyboardMarkup:
	"""Клавиатура с кнопкой 'Удалить' для удаления сообщения"""
	kb = InlineKeyboardBuilder()
	kb.button(text="🗑️ Удалить", callback_data="delete_message")
	kb.adjust(1)
	return kb.as_markup()


def user_access_request_kb(user_id: int) -> InlineKeyboardMarkup:
	"""Клавиатура для запроса доступа пользователя с кнопкой 'Разрешить' и 'Меню пользователя'"""
	kb = InlineKeyboardBuilder()
	kb.button(text="✅ Разрешить", callback_data=f"settings:users:set:{user_id}:allow")
	kb.button(text="👤 Меню пользователя", callback_data=f"user:view:{user_id}")
	kb.adjust(1, 1)
	return kb.as_markup()
