
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Dict, Iterable, List, Optional, Set, Tuple, Any


def admin_menu_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	kb.button(text="💵 Наличные", callback_data="admin:cash")
	kb.button(text="📇 Безнал", callback_data="admin:cards")
	kb.button(text="👥 Пользователи", callback_data="admin:users")
	kb.button(text="₿ Крипта", callback_data="admin:crypto")
	kb.button(text="📊 Статистика", callback_data="admin:stats")
	kb.adjust(2, 2, 1)
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


def cards_list_kb(cards: List[Tuple[int, str]], with_add: bool = True, back_to: str = "admin:cards", group_id: Optional[int] = None) -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	# Добавляем все кнопки карт
	for cid, name in cards:
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


def cards_select_kb(cards: List[Tuple[int, str]], back_to: str) -> InlineKeyboardMarkup:
	kb = InlineKeyboardBuilder()
	for cid, name in cards:
		kb.button(text=f"💳 {name}", callback_data=f"select:card:{cid}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
	return kb.as_markup()


def card_groups_select_kb(groups: List[Dict], back_to: str = "admin:back") -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора группы карт.
	
	Args:
		groups: Список словарей с информацией о группах
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for group in groups:
		group_name = group.get("name", "")
		group_id = group.get("id")
		# Определяем, какой callback использовать в зависимости от back_to
		if back_to.startswith("add_data:back:"):
			# Используется в командах /add и /rate
			kb.button(text=f"📁 {group_name}", callback_data=f"{back_to}:group:{group_id}")
		else:
			# Стандартное использование
			kb.button(text=f"📁 {group_name}", callback_data=f"cards:group:{group_id}")
	# Добавляем кнопку для карт без группы
	if back_to.startswith("add_data:back:"):
		kb.button(text="📋 Без группы", callback_data=f"{back_to}:group:0")
	else:
		kb.button(text="📋 Без группы", callback_data="cards:group:0")
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
		cash_columns: Список словарей с ключами cash_name и column
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for cash in cash_columns:
		cash_name = cash.get("cash_name", "")
		column = cash.get("column", "")
		kb.button(text=f"{cash_name} → {column}", callback_data=f"cash:edit:{cash_name}")
	kb.button(text="➕ Новая", callback_data="cash:new")
	kb.button(text="🗑️ Удалить", callback_data="cash:delete_list")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	kb.adjust(1)
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
		cash_columns: Список словарей с ключами cash_name и column
		mode: Режим работы ("add" или "rate")
		back_to: Callback data для кнопки "Назад"
	"""
	kb = InlineKeyboardBuilder()
	for cash in cash_columns:
		cash_name = cash.get("cash_name", "")
		kb.button(text=cash_name, callback_data=f"add_data:cash_select:{cash_name}:{mode}")
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


def crypto_select_kb(back_to: str = "multi:back_to_main", show_confirm: bool = True) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора криптовалюты.
	Первый ряд: три кнопки в ряд (BTC, LTC, XMR)
	Второй ряд: кнопка USDT
	Третий ряд: кнопка "Подтвердить" (если show_confirm=True) и "Назад"
	"""
	kb = InlineKeyboardBuilder()
	
	# Три кнопки валют в ряд
	kb.button(text="BTC", callback_data="crypto:select:BTC")
	kb.button(text="LTC", callback_data="crypto:select:LTC")
	kb.button(text="XMR", callback_data="crypto:select:XMR")
	
	# Кнопка USDT под ними
	kb.button(text="USDT", callback_data="crypto:select:USDT")
	
	# Кнопка "Подтвердить" (если нужно)
	if show_confirm:
		kb.button(text="✅ Подтвердить", callback_data="multi:confirm")
	
	# Кнопка "Назад"
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	# Первый ряд - три кнопки валют, второй ряд - USDT, третий ряд - подтвердить (если есть) и назад
	if show_confirm:
		kb.adjust(3, 1, 1, 1)
	else:
		kb.adjust(3, 1, 1)
	return kb.as_markup()


def add_data_type_kb(mode: str = "add", back_to: str = "admin:back", data: Optional[Dict[str, Any]] = None) -> InlineKeyboardMarkup:
	"""
	Создает клавиатуру для выбора типа данных в командах /add и /rate.
	
	Args:
		mode: Режим работы ("add" или "rate")
		back_to: Callback data для кнопки "Назад"
		data: Словарь с выбранными данными (crypto_data, cash_data, card_data, card_cash_data)
	"""
	kb = InlineKeyboardBuilder()
	
	# Определяем текст для кнопок на основе выбранных данных
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
			cash_text = str(amount)
		
		card_data = data.get("card_data")
		card_cash_data = data.get("card_cash_data")
		if card_data:
			card_name = card_data.get("card_name", "")
			if card_cash_data:
				amount = card_cash_data.get("value", 0)
				card_text = f"{card_name}: {amount}р."
			else:
				card_text = card_name
	
	# Первый ряд: Криптовалюта и Карта
	kb.button(text=crypto_text, callback_data=f"add_data:type:crypto:{mode}")
	kb.button(text=card_text, callback_data=f"add_data:type:card:{mode}")
	# Второй ряд: Наличные
	kb.button(text=cash_text, callback_data=f"add_data:type:cash:{mode}")
	# Третий ряд: Подтвердить и Назад
	kb.button(text="✅ Подтвердить", callback_data=f"add_data:confirm:{mode}")
	kb.button(text="⬅️ Назад", callback_data=back_to)
	
	kb.adjust(2, 1, 2)
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
