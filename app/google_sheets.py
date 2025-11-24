"""
Модуль для работы с Google Sheets API
"""
import logging
import asyncio
import re
from typing import Optional, Dict, Any
import gspread
from google.oauth2.service_account import Credentials
import aiohttp

logger = logging.getLogger("app.google_sheets")


def _get_google_sheets_client(credentials_path: str) -> Optional[gspread.Client]:
	"""Создает клиент для работы с Google Sheets (синхронная функция)"""
	try:
		scope = [
			"https://spreadsheets.google.com/feeds",
			"https://www.googleapis.com/auth/drive"
		]
		creds = Credentials.from_service_account_file(credentials_path, scopes=scope)
		client = gspread.authorize(creds)
		return client
	except Exception as e:
		logger.exception(f"Ошибка создания клиента Google Sheets: {e}")
		return None


async def _get_btc_from_binance() -> Optional[float]:
	"""Получает курс BTC/USDT с Binance API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["price"])
					logger.info(f"✅ Binance: курс BTC = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"Binance API недоступен: {e}")
	return None


async def _get_btc_from_coinbase() -> Optional[float]:
	"""Получает курс BTC/USD с Coinbase API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.coinbase.com/v2/exchange-rates?currency=BTC",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["data"]["rates"]["USD"])
					logger.info(f"✅ Coinbase: курс BTC = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"Coinbase API недоступен: {e}")
	return None


async def _get_btc_from_coingecko() -> Optional[float]:
	"""Получает курс BTC/USD с CoinGecko API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["bitcoin"]["usd"])
					logger.info(f"✅ CoinGecko: курс BTC = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"CoinGecko API недоступен: {e}")
	return None


async def get_btc_price_usd() -> Optional[float]:
	"""
	Получает текущий курс BTC в USD, пробуя несколько источников.
	Порядок приоритета: Binance -> Coinbase -> CoinGecko
	"""
	# Пробуем Binance (самый быстрый и надежный)
	price = await _get_btc_from_binance()
	if price:
		return price
	
	# Пробуем Coinbase
	price = await _get_btc_from_coinbase()
	if price:
		return price
	
	# Пробуем CoinGecko
	price = await _get_btc_from_coingecko()
	if price:
		return price
	
	logger.error("❌ Не удалось получить курс BTC ни с одного источника")
	return None


async def _get_ltc_from_binance() -> Optional[float]:
	"""Получает курс LTC/USDT с Binance API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.binance.com/api/v3/ticker/price?symbol=LTCUSDT",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["price"])
					logger.info(f"✅ Binance: курс LTC = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"Binance API недоступен для LTC: {e}")
	return None


async def _get_ltc_from_coinbase() -> Optional[float]:
	"""Получает курс LTC/USD с Coinbase API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.coinbase.com/v2/exchange-rates?currency=LTC",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["data"]["rates"]["USD"])
					logger.info(f"✅ Coinbase: курс LTC = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"Coinbase API недоступен для LTC: {e}")
	return None


async def _get_ltc_from_coingecko() -> Optional[float]:
	"""Получает курс LTC/USD с CoinGecko API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["litecoin"]["usd"])
					logger.info(f"✅ CoinGecko: курс LTC = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"CoinGecko API недоступен для LTC: {e}")
	return None


async def get_ltc_price_usd() -> Optional[float]:
	"""
	Получает текущий курс LTC в USD, пробуя несколько источников.
	Порядок приоритета: Binance -> Coinbase -> CoinGecko
	"""
	# Пробуем Binance (самый быстрый и надежный)
	price = await _get_ltc_from_binance()
	if price:
		return price
	
	# Пробуем Coinbase
	price = await _get_ltc_from_coinbase()
	if price:
		return price
	
	# Пробуем CoinGecko
	price = await _get_ltc_from_coingecko()
	if price:
		return price
	
	logger.error("❌ Не удалось получить курс LTC ни с одного источника")
	return None


async def _get_xmr_from_binance() -> Optional[float]:
	"""Получает курс XMR/USDT с Binance API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.binance.com/api/v3/ticker/price?symbol=XMRUSDT",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["price"])
					logger.info(f"✅ Binance: курс XMR = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"Binance API недоступен для XMR: {e}")
	return None


async def _get_xmr_from_coinbase() -> Optional[float]:
	"""Получает курс XMR/USD с Coinbase API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.coinbase.com/v2/exchange-rates?currency=XMR",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["data"]["rates"]["USD"])
					logger.info(f"✅ Coinbase: курс XMR = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"Coinbase API недоступен для XMR: {e}")
	return None


async def _get_xmr_from_coingecko() -> Optional[float]:
	"""Получает курс XMR/USD с CoinGecko API"""
	try:
		async with aiohttp.ClientSession() as session:
			async with session.get(
				"https://api.coingecko.com/api/v3/simple/price?ids=monero&vs_currencies=usd",
				timeout=aiohttp.ClientTimeout(total=5)
			) as response:
				if response.status == 200:
					data = await response.json()
					price = float(data["monero"]["usd"])
					logger.info(f"✅ CoinGecko: курс XMR = ${price:,.2f} USD")
					return price
	except Exception as e:
		logger.debug(f"CoinGecko API недоступен для XMR: {e}")
	return None


async def get_xmr_price_usd() -> Optional[float]:
	"""
	Получает текущий курс XMR в USD, пробуя несколько источников.
	Порядок приоритета: Binance -> Coinbase -> CoinGecko
	"""
	# Пробуем Binance (самый быстрый и надежный)
	price = await _get_xmr_from_binance()
	if price:
		return price
	
	# Пробуем Coinbase
	price = await _get_xmr_from_coinbase()
	if price:
		return price
	
	# Пробуем CoinGecko
	price = await _get_xmr_from_coingecko()
	if price:
		return price
	
	logger.error("❌ Не удалось получить курс XMR ни с одного источника")
	return None


def _find_empty_row_in_column(sheet: gspread.Worksheet, column: str, start_row: int = 5) -> int:
	"""
	Находит первую строку с 0 в указанном столбце, начиная с start_row.
	Возвращает номер строки.
	Использует batch чтение для оптимизации (читает по 50 строк за раз).
	"""
	try:
		batch_size = 50
		row = start_row
		
		while row <= start_row + 1000:
			# Читаем batch строк за один запрос
			end_row = min(row + batch_size - 1, start_row + 1000)
			range_str = f"{column}{row}:{column}{end_row}"
			
			try:
				values = sheet.get(range_str)
				# values - это список списков, например [['1'], ['2'], ['0'], ...]
				
				for i, cell_list in enumerate(values):
					current_row = row + i
					# Если список пустой или содержит пустую строку, значит ячейка пустая
					if not cell_list or len(cell_list) == 0:
						return current_row
					
					cell_value = cell_list[0] if cell_list else None
					
					# Проверяем, является ли значение 0 или пустым
					if cell_value is None or cell_value == "":
						return current_row
					
					# Пытаемся преобразовать в число и проверить на 0
					try:
						num_value = float(cell_value)
						if num_value == 0:
							return current_row
					except (ValueError, TypeError):
						# Не число, пропускаем
						pass
				
				# Если в этом batch не нашли, переходим к следующему
				row = end_row + 1
				
			except Exception as e:
				logger.warning(f"Ошибка чтения диапазона {range_str}: {e}, пробуем по одной ячейке")
				# Fallback: читаем по одной ячейке
				cell_value = sheet.acell(f"{column}{row}").value
				if cell_value is None or cell_value == "" or (isinstance(cell_value, (int, float)) and float(cell_value) == 0):
					return row
				row += 1
		
		logger.warning(f"Не найдена свободная строка в столбце {column}, начиная с {start_row}")
		return start_row + 1000
		
	except Exception as e:
		logger.exception(f"Ошибка поиска свободной строки: {e}")
		return start_row


def get_card_column(card_name: str, user_name: str) -> Optional[str]:
	"""
	Определяет столбец для записи суммы RUB на основе карты и имени пользователя.
	Возвращает букву столбца или None, если не найдено соответствие.
	
	Приоритет: сначала пытается извлечь информацию о пользователе из названия карты,
	если там есть скобки с именем (например, "ТИНЕК (Артём С)"), 
	иначе использует переданный user_name.
	"""
	# Нормализуем имена для сравнения
	card_upper = card_name.upper() if card_name else ""
	user_upper = user_name.upper() if user_name else ""
	
	# Пытаемся извлечь имя пользователя из названия карты (если есть скобки)
	# Например: "ТИНЕК  (Артём С)" -> "Артём С"
	extracted_user_name = None
	if card_name:
		# Ищем паттерн: название карты, затем скобки с именем
		# Паттерн: любое количество пробелов, открывающая скобка, имя с инициалом, закрывающая скобка
		match = re.search(r'\(([А-ЯЁA-Z][а-яёa-z]+\s+[А-ЯЁA-Z]\.?)\)', card_name)
		if match:
			extracted_user_name = match.group(1)
			logger.debug(f"🔍 Извлечено имя из названия карты: '{extracted_user_name}'")
	
	# Используем извлеченное имя из карты, если оно есть, иначе используем переданное user_name
	final_user_name = extracted_user_name if extracted_user_name else user_name
	final_user_upper = final_user_name.upper() if final_user_name else ""
	
	logger.debug(f"🔍 get_card_column: card_name='{card_name}', user_name='{user_name}' -> final_user_name='{final_user_name}' (card_upper='{card_upper}', final_user_upper='{final_user_upper}')")
	
	# Проверяем наличие имени и инициала
	has_artem = "АРТЕМ" in final_user_upper or "АРТЁМ" in final_user_upper
	has_evgeniy = "ЕВГЕНИЙ" in final_user_upper
	
	# Проверяем инициалы как отдельные слова (после пробела или в конце строки)
	# Используем регулярные выражения для точной проверки
	# Паттерн: пробел + инициал + опциональная точка + конец строки или пробел
	v_match = re.search(r'\sВ\.?$|\sВ\.?\s', final_user_upper)
	s_match = re.search(r'\sС\.?$|\sС\.?\s', final_user_upper)
	r_match = re.search(r'\sР\.?$|\sР\.?\s', final_user_upper)
	
	has_v = bool(v_match) and not bool(s_match)
	has_s = bool(s_match) and not bool(v_match)
	has_r = bool(r_match) and not bool(s_match) and not bool(v_match)
	
	logger.debug(f"🔍 Проверка инициалов: has_artem={has_artem}, has_evgeniy={has_evgeniy}, has_v={has_v}, has_s={has_s}, has_r={has_r} (v_match={bool(v_match)}, s_match={bool(s_match)}, r_match={bool(r_match)})")
	
	# ТИНЕК (Артём В) - столбец E
	if "ТИНЕК" in card_upper and has_artem and has_v:
		logger.info(f"✅ Найдено соответствие: ТИНЕК (Артём В) -> столбец E")
		return "E"
	
	# СБЕР (Евгений Р) - столбец B
	if "СБЕР" in card_upper and has_evgeniy and has_r:
		logger.info(f"✅ Найдено соответствие: СБЕР (Евгений Р) -> столбец B")
		return "B"
	
	# ТИНЕК (Артем С) - столбец C
	if "ТИНЕК" in card_upper and has_artem and has_s:
		logger.info(f"✅ Найдено соответствие: ТИНЕК (Артем С) -> столбец C")
		return "C"
	
	# СБЕР (Артём С) - столбец D
	if "СБЕР" in card_upper and has_artem and has_s:
		logger.info(f"✅ Найдено соответствие: СБЕР (Артём С) -> столбец D")
		return "D"
	
	logger.warning(f"❌ Не найдено соответствие для карты '{card_name}' и пользователя '{final_user_name}' (card_upper='{card_upper}', final_user_upper='{final_user_upper}')")
	return None


def _write_to_google_sheet_sync(
	sheet_id: str,
	credentials_path: str,
	crypto_data: Optional[Dict],
	cash_data: Optional[Dict],
	card_data: Optional[Dict],
	btc_price: Optional[float],
	ltc_price: Optional[float]
) -> Dict[str, Any]:
	"""
	Синхронная функция для записи данных в Google Sheet.
	
	Args:
		sheet_id: ID Google Sheet
		credentials_path: Путь к файлу с учетными данными
		crypto_data: Данные о криптовалюте (currency, value)
		cash_data: Данные о наличных (currency, value)
		card_data: Данные о карте (card_name, user_name)
		btc_price: Курс BTC в USD
		ltc_price: Курс LTC в USD
	
	Returns:
		True если успешно, False в противном случае
	"""
	try:
		# Создаем клиент
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return False
		
		# Получаем email сервисного аккаунта для отладки
		import json
		with open(credentials_path, 'r') as f:
			creds_data = json.load(f)
			service_account_email = creds_data.get('client_email', 'не найден')
		logger.info(f"Используется сервисный аккаунт: {service_account_email}")
		
		# Открываем таблицу
		try:
			spreadsheet = client.open_by_key(sheet_id)
			worksheet = spreadsheet.sheet1  # Используем первый лист
		except PermissionError as e:
			logger.error(f"Ошибка доступа к таблице. Убедитесь, что сервисный аккаунт {service_account_email} добавлен в список пользователей с доступом к таблице.")
			raise
		
		# Логируем входящие данные
		logger.info(f"📊 Данные для записи: crypto={crypto_data}, cash={cash_data}, card={card_data}, btc_price={btc_price}, ltc_price={ltc_price}")
		
		# Находим свободную строку в столбце BC
		empty_row = _find_empty_row_in_column(worksheet, "BC", start_row=5)
		logger.info(f"📍 Найдена свободная строка: {empty_row}")
		
		# Обрабатываем криптовалюту (BTC или LTC)
		usd_amount_rounded = None
		if crypto_data:
			crypto_currency = crypto_data.get("currency")
			crypto_amount = crypto_data.get("value", 0.0)
			
			if crypto_currency == "BTC":
				if btc_price:
					usd_amount = crypto_amount * btc_price
					usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
					# Записываем USD в столбец AS (метод update требует список списков)
					worksheet.update(f"AS{empty_row}", [[usd_amount_rounded]])
					logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку AS{empty_row} (BTC: {crypto_amount}, курс: {btc_price})")
				else:
					logger.warning(f"⚠️ Не удалось получить курс BTC, пропускаем запись криптовалюты. BTC количество: {crypto_amount}")
			elif crypto_currency == "LTC":
				if ltc_price:
					usd_amount = crypto_amount * ltc_price
					usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
					# Записываем USD в столбец AY (метод update требует список списков)
					worksheet.update(f"AY{empty_row}", [[usd_amount_rounded]])
					logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку AY{empty_row} (LTC: {crypto_amount}, курс: {ltc_price})")
				else:
					logger.warning(f"⚠️ Не удалось получить курс LTC, пропускаем запись криптовалюты. LTC количество: {crypto_amount}")
		
		# Обрабатываем наличные (RUB, BYN и другие валюты)
		if cash_data and card_data:
			cash_currency = cash_data.get("currency", "")
			cash_amount = cash_data.get("value", 0)
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			
			# Определяем столбец для записи
			column = get_card_column(card_name, user_name)
			if column:
				# Метод update требует список списков
				worksheet.update(f"{column}{empty_row}", [[cash_amount]])
				logger.info(f"✅ Записано {cash_amount} {cash_currency} в ячейку {column}{empty_row}")
			else:
				logger.warning(f"⚠️ Не найден столбец для карты '{card_name}' и пользователя '{user_name}'")
		
		return {"success": True, "usd_amount": usd_amount_rounded}
		
	except Exception as e:
		logger.exception(f"Ошибка записи в Google Sheet: {e}")
		return {"success": False, "usd_amount": None}


async def write_to_google_sheet(
	sheet_id: str,
	credentials_path: str,
	crypto_data: Optional[Dict],
	cash_data: Optional[Dict],
	card_data: Optional[Dict]
) -> Dict[str, Any]:
	"""
	Асинхронная функция для записи данных в Google Sheet.
	
	Args:
		sheet_id: ID Google Sheet
		credentials_path: Путь к файлу с учетными данными
		crypto_data: Данные о криптовалюте (currency, value)
		cash_data: Данные о наличных (currency, value)
		card_data: Данные о карте (card_name, user_name)
	
	Returns:
		Словарь с результатами: {"success": bool, "usd_amount": int | None}
	"""
	try:
		# Получаем курс криптовалюты
		btc_price = None
		ltc_price = None
		if crypto_data:
			crypto_currency = crypto_data.get("currency")
			if crypto_currency == "BTC":
				btc_price = await get_btc_price_usd()
			elif crypto_currency == "LTC":
				ltc_price = await get_ltc_price_usd()
		
		# Выполняем синхронную запись в отдельном потоке
		return await asyncio.to_thread(
			_write_to_google_sheet_sync,
			sheet_id,
			credentials_path,
			crypto_data,
			cash_data,
			card_data,
			btc_price,
			ltc_price
		)
	except Exception as e:
		logger.exception(f"Ошибка записи в Google Sheet: {e}")
		return {"success": False, "usd_amount": None}


def get_xmr_column(xmr_number: int) -> str:
	"""
	Определяет столбец для записи USD по номеру XMR.
	
	Args:
		xmr_number: Номер XMR (1, 2 или 3)
	
	Returns:
		Буква столбца (AU, AV, AW в зависимости от номера)
	"""
	# Столбцы для USD при выборе XMR-1, XMR-2, XMR-3
	xmr_columns = {
		1: "AU",  # XMR-1 → USD в столбец AU
		2: "AV",  # XMR-2 → USD в столбец AV
		3: "AW"   # XMR-3 → USD в столбец AW
	}
	return xmr_columns.get(xmr_number, "AU")  # По умолчанию AU


async def write_xmr_to_google_sheet(
	sheet_id: str,
	credentials_path: str,
	crypto_data: Optional[Dict],
	cash_data: Optional[Dict],
	card_data: Optional[Dict],
	xmr_number: int
) -> Dict[str, Any]:
	"""
	Асинхронная функция для записи данных XMR в Google Sheet.
	Конвертирует XMR в USD и записывает USD в соответствующий столбец.
	
	Args:
		sheet_id: ID Google Sheet
		credentials_path: Путь к файлу с учетными данными
		crypto_data: Данные о криптовалюте (currency, value) - должен быть XMR
		cash_data: Данные о наличных (currency, value)
		card_data: Данные о карте (card_name, user_name)
		xmr_number: Номер XMR (1, 2 или 3)
	
	Returns:
		Словарь с результатами: {"success": bool, "usd_amount": int | None}
	"""
	try:
		# Получаем курс XMR
		xmr_price = await get_xmr_price_usd()
		
		# Определяем столбец для записи USD
		usd_column = get_xmr_column(xmr_number)
		
		# Выполняем синхронную запись в отдельном потоке
		return await asyncio.to_thread(
			_write_xmr_to_google_sheet_sync,
			sheet_id,
			credentials_path,
			crypto_data,
			cash_data,
			card_data,
			xmr_number,
			usd_column,
			xmr_price
		)
	except Exception as e:
		logger.exception(f"Ошибка записи XMR в Google Sheet: {e}")
		return {"success": False, "usd_amount": None}


def _write_xmr_to_google_sheet_sync(
	sheet_id: str,
	credentials_path: str,
	crypto_data: Optional[Dict],
	cash_data: Optional[Dict],
	card_data: Optional[Dict],
	xmr_number: int,
	usd_column: str,
	xmr_price: Optional[float]
) -> Dict[str, Any]:
	"""
	Синхронная функция для записи данных XMR в Google Sheet.
	Конвертирует XMR в USD и записывает USD в соответствующий столбец.
	
	Args:
		sheet_id: ID Google Sheet
		credentials_path: Путь к файлу с учетными данными
		crypto_data: Данные о криптовалюте (currency, value) - должен быть XMR
		cash_data: Данные о наличных (currency, value)
		card_data: Данные о карте (card_name, user_name)
		xmr_number: Номер XMR (1, 2 или 3)
		usd_column: Столбец для записи USD (AU, AV или AW)
		xmr_price: Курс XMR в USD
	
	Returns:
		Словарь с результатами: {"success": bool, "usd_amount": int | None}
	"""
	try:
		# Создаем клиент
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return {"success": False, "usd_amount": None}
		
		# Открываем таблицу
		try:
			spreadsheet = client.open_by_key(sheet_id)
			worksheet = spreadsheet.sheet1
		except PermissionError as e:
			logger.error(f"Ошибка доступа к таблице: {e}")
			raise
		
		# Логируем входящие данные
		logger.info(f"📊 Данные XMR-{xmr_number} для записи: crypto={crypto_data}, cash={cash_data}, card={card_data}, xmr_price={xmr_price}")
		
		# Находим свободную строку в столбце BC
		empty_row = _find_empty_row_in_column(worksheet, "BC", start_row=5)
		logger.info(f"📍 Найдена свободная строка: {empty_row}")
		
		# Обрабатываем XMR: конвертируем в USD и записываем в соответствующий столбец
		usd_amount_rounded = None
		if crypto_data and crypto_data.get("currency") == "XMR":
			xmr_amount = crypto_data.get("value", 0.0)
			
			if xmr_price:
				usd_amount = xmr_amount * xmr_price
				usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
				# Записываем USD в соответствующий столбец (метод update требует список списков)
				worksheet.update(f"{usd_column}{empty_row}", [[usd_amount_rounded]])
				logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {usd_column}{empty_row} (XMR-{xmr_number}: {xmr_amount} XMR, курс: {xmr_price})")
			else:
				logger.warning(f"⚠️ Не удалось получить курс XMR, пропускаем запись криптовалюты. XMR количество: {xmr_amount}")
		
		# Обрабатываем наличные (RUB, BYN и другие валюты)
		if cash_data and card_data:
			cash_currency = cash_data.get("currency", "")
			cash_amount = cash_data.get("value", 0)
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			
			# Определяем столбец для записи
			column = get_card_column(card_name, user_name)
			if column:
				# Метод update требует список списков
				worksheet.update(f"{column}{empty_row}", [[cash_amount]])
				logger.info(f"✅ Записано {cash_amount} {cash_currency} в ячейку {column}{empty_row}")
			else:
				logger.warning(f"⚠️ Не найден столбец для карты '{card_name}' и пользователя '{user_name}'")
		
		return {"success": True, "usd_amount": usd_amount_rounded}
		
	except Exception as e:
		logger.exception(f"Ошибка записи XMR в Google Sheet: {e}")
		return {"success": False, "usd_amount": None}

