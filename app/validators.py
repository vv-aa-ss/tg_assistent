"""
Модуль для валидации входных данных
"""
import re
from typing import Optional, Tuple
import logging

logger = logging.getLogger("app.validators")


def validate_amount(amount: float, min_value: float = 0.0, max_value: float = 1000000.0) -> Tuple[bool, Optional[str]]:
	"""
	Валидирует сумму
	
	Args:
		amount: Сумма для проверки
		min_value: Минимальное значение (по умолчанию 0)
		max_value: Максимальное значение (по умолчанию 1,000,000)
	
	Returns:
		(is_valid, error_message) - валидна ли сумма и сообщение об ошибке
	"""
	if amount <= min_value:
		return False, f"❌ Сумма должна быть больше {min_value}"
	
	if amount > max_value:
		return False, f"❌ Сумма не может превышать {max_value:,.0f}"
	
	# Проверка на очень маленькие значения (возможная ошибка)
	if 0 < amount < 0.00000001:
		return False, "❌ Сумма слишком мала. Проверьте правильность ввода."
	
	return True, None


def validate_text(text: str, max_length: int = 1000, min_length: int = 1) -> Tuple[bool, Optional[str]]:
	"""
	Валидирует текст сообщения
	
	Args:
		text: Текст для проверки
		max_length: Максимальная длина (по умолчанию 1000)
		min_length: Минимальная длина (по умолчанию 1)
	
	Returns:
		(is_valid, error_message) - валиден ли текст и сообщение об ошибке
	"""
	if not text or not text.strip():
		return False, "❌ Текст не может быть пустым"
	
	if len(text) < min_length:
		return False, f"❌ Текст слишком короткий (минимум {min_length} символов)"
	
	if len(text) > max_length:
		return False, f"❌ Текст слишком длинный (максимум {max_length} символов)"
	
	# Проверка на опасные символы/паттерны (XSS защита)
	dangerous_patterns = [
		r'<script',
		r'javascript:',
		r'onerror\s*=',
		r'onclick\s*=',
		r'onload\s*=',
		r'<iframe',
		r'<object',
		r'<embed',
	]
	
	text_lower = text.lower()
	for pattern in dangerous_patterns:
		if re.search(pattern, text_lower):
			logger.warning(f"⚠️ Обнаружен опасный паттерн в тексте: {pattern}")
			return False, "❌ Текст содержит недопустимые символы"
	
	return True, None


def validate_deal_id(deal_id: int) -> Tuple[bool, Optional[str]]:
	"""
	Валидирует ID сделки
	
	Args:
		deal_id: ID сделки для проверки
	
	Returns:
		(is_valid, error_message) - валиден ли ID и сообщение об ошибке
	"""
	if deal_id <= 0:
		return False, "❌ Неверный ID сделки"
	
	# Проверка на разумные пределы (ID не может быть больше 2^31-1 для SQLite INTEGER)
	if deal_id > 2147483647:
		return False, "❌ ID сделки вне допустимого диапазона"
	
	return True, None


def validate_wallet_address(address: str, crypto_type: str = "BTC") -> Tuple[bool, Optional[str]]:
	"""
	Валидирует адрес кошелька
	
	Args:
		address: Адрес кошелька
		crypto_type: Тип криптовалюты (BTC, LTC, USDT, XMR)
	
	Returns:
		(is_valid, error_message) - валиден ли адрес и сообщение об ошибке
	"""
	if not address or not address.strip():
		return False, "❌ Адрес кошелька не может быть пустым"
	
	address = address.strip()
	
	# Базовые проверки длины
	if len(address) < 10:
		return False, "❌ Адрес кошелька слишком короткий"
	
	if len(address) > 200:
		return False, "❌ Адрес кошелька слишком длинный"
	
	# Проверка на опасные символы
	if re.search(r'[<>"\']', address):
		return False, "❌ Адрес содержит недопустимые символы"
	
	# Базовые паттерны для разных криптовалют
	if crypto_type == "BTC":
		# Bitcoin: начинается с 1, 3, или bc1
		if not re.match(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$|^bc1[a-z0-9]{39,59}$', address):
			return False, "❌ Неверный формат Bitcoin адреса"
	elif crypto_type == "LTC":
		# Litecoin: начинается с L, M, или ltc1
		if not re.match(r'^[LM][a-km-zA-HJ-NP-Z1-9]{25,34}$|^ltc1[a-z0-9]{39,59}$', address):
			return False, "❌ Неверный формат Litecoin адреса"
	elif crypto_type == "USDT":
		# USDT TRC20: начинается с T
		if not re.match(r'^T[A-Za-z1-9]{33}$', address):
			return False, "❌ Неверный формат USDT (TRC20) адреса"
	elif crypto_type == "XMR":
		# Monero: начинается с 4
		if not re.match(r'^4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}$', address):
			return False, "❌ Неверный формат Monero адреса"
	
	return True, None


def validate_file(document, max_size_mb: int = 10, allowed_extensions: list = None) -> Tuple[bool, Optional[str]]:
	"""
	Валидирует файл
	
	Args:
		document: Объект документа из aiogram
		max_size_mb: Максимальный размер в МБ (по умолчанию 10)
		allowed_extensions: Список разрешенных расширений (по умолчанию изображения и PDF)
	
	Returns:
		(is_valid, error_message) - валиден ли файл и сообщение об ошибке
	"""
	if not document:
		return False, "❌ Файл не найден"
	
	if allowed_extensions is None:
		allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.doc', '.docx']
	
	max_size_bytes = max_size_mb * 1024 * 1024
	
	# Проверка размера
	if hasattr(document, 'file_size') and document.file_size:
		if document.file_size > max_size_bytes:
			return False, f"❌ Файл слишком большой (максимум {max_size_mb} МБ)"
	
	# Проверка расширения
	if hasattr(document, 'file_name') and document.file_name:
		file_ext = None
		for ext in allowed_extensions:
			if document.file_name.lower().endswith(ext.lower()):
				file_ext = ext
				break
		
		if not file_ext:
			return False, f"❌ Неподдерживаемый тип файла. Разрешенные: {', '.join(allowed_extensions)}"
	
	return True, None


def sanitize_text(text: str) -> str:
	"""
	Очищает текст от потенциально опасных символов
	
	Args:
		text: Текст для очистки
	
	Returns:
		Очищенный текст
	"""
	if not text:
		return ""
	
	# Удаляем нулевые байты
	text = text.replace('\x00', '')
	
	# Удаляем управляющие символы (кроме переносов строк и табуляции)
	text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', text)
	
	return text.strip()
