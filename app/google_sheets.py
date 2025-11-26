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

from app.di import get_db

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


async def get_card_column(card_name: str, user_name: Optional[str] = None) -> Optional[str]:
	"""
	Определяет столбец для записи суммы RUB на основе карты.
	Использует базу данных для поиска соответствия.
	Возвращает букву столбца или None, если не найдено соответствие.
	
	Args:
		card_name: Название карты
		user_name: Имя пользователя (не используется, оставлено для обратной совместимости)
	
	Returns:
		Адрес столбца или None, если не найдено
	"""
	if not card_name:
		logger.warning(f"❌ get_card_column: card_name пустое")
		return None
	
	logger.debug(f"🔍 get_card_column: card_name='{card_name}'")
	
	# Получаем базу данных
	db = get_db()
	
	# Ищем карту по названию в базе данных
	cards = await db.list_cards()
	card_id = None
	for card in cards:
		# Проверяем точное совпадение или частичное (если название карты содержится в card_name)
		if card[1].upper() in card_name.upper() or card_name.upper() in card[1].upper():
			card_id = card[0]
			logger.debug(f"✅ Найдена карта в БД: id={card_id}, name='{card[1]}'")
			break
	
	if not card_id:
		logger.warning(f"❌ get_card_column: карта '{card_name}' не найдена в базе данных")
		return None
	
	# Получаем адрес столбца для карты
	column = await db.get_card_column(card_id)
	if column:
		logger.info(f"✅ Найден адрес столбца: card_id={card_id}, card_name='{card_name}' -> column='{column}'")
		return column
	
	logger.warning(f"❌ get_card_column: не найден адрес столбца для card_id={card_id}, card_name='{card_name}'")
	return None


def _write_to_google_sheet_sync(
	sheet_id: str,
	credentials_path: str,
	crypto_data: Optional[Dict],
	cash_data: Optional[Dict],
	card_data: Optional[Dict],
	btc_price: Optional[float],
	ltc_price: Optional[float],
	btc_column: Optional[str] = None,
	ltc_column: Optional[str] = None,
	usdt_column: Optional[str] = None
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
		# Теперь пользователь вводит USD напрямую, не нужно вычислять
		usd_amount_rounded = None
		if crypto_data:
			crypto_currency = crypto_data.get("currency")
			# Получаем USD напрямую из данных (пользователь ввел USD)
			usd_amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
			
			if usd_amount > 0:
				usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
				
				if crypto_currency == "BTC" and btc_column:
					# Записываем USD в столбец из базы данных (метод update требует список списков)
					worksheet.update(f"{btc_column}{empty_row}", [[usd_amount_rounded]])
					logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {btc_column}{empty_row} (BTC)")
				elif crypto_currency == "LTC" and ltc_column:
					# Записываем USD в столбец из базы данных (метод update требует список списков)
					worksheet.update(f"{ltc_column}{empty_row}", [[usd_amount_rounded]])
					logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {ltc_column}{empty_row} (LTC)")
				elif crypto_currency == "USDT" and usdt_column:
					# Записываем USD в столбец из базы данных (метод update требует список списков)
					worksheet.update(f"{usdt_column}{empty_row}", [[usd_amount_rounded]])
					logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {usdt_column}{empty_row} (USDT)")
				elif crypto_currency in ["BTC", "LTC", "USDT"]:
					logger.warning(f"⚠️ Не найден адрес столбца для криптовалюты {crypto_currency}")
			else:
				logger.warning(f"⚠️ USD сумма не указана для криптовалюты {crypto_currency}")
		
		# Обрабатываем наличные (RUB, BYN и другие валюты)
		if cash_data and card_data:
			cash_currency = cash_data.get("currency", "")
			cash_amount = cash_data.get("value", 0)
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			
			# Получаем адрес столбца из параметра (вычислен заранее в асинхронной функции)
			column = card_data.get("column")
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
		
		# Вычисляем адрес столбца для наличных, если есть карта
		if cash_data and card_data:
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			column = await get_card_column(card_name, user_name)
			if column:
				# Добавляем адрес столбца в данные карты
				card_data = card_data.copy()
				card_data["column"] = column
				logger.debug(f"✅ Адрес столбца вычислен: card_name='{card_name}', user_name='{user_name}' -> column='{column}'")
			else:
				logger.warning(f"⚠️ Не удалось определить адрес столбца для card_name='{card_name}', user_name='{user_name}'")
		
		# Получаем адреса столбцов для криптовалют из базы данных
		btc_column = None
		ltc_column = None
		usdt_column = None
		if crypto_data:
			db = get_db()
			crypto_currency = crypto_data.get("currency")
			if crypto_currency == "BTC":
				btc_column = await db.get_crypto_column("BTC")
			elif crypto_currency == "LTC":
				ltc_column = await db.get_crypto_column("LTC")
			elif crypto_currency == "USDT":
				usdt_column = await db.get_crypto_column("USDT")
		
		# Выполняем синхронную запись в отдельном потоке
		return await asyncio.to_thread(
			_write_to_google_sheet_sync,
			sheet_id,
			credentials_path,
			crypto_data,
			cash_data,
			card_data,
			btc_price,
			ltc_price,
			btc_column,
			ltc_column,
			usdt_column
		)
	except Exception as e:
		logger.exception(f"Ошибка записи в Google Sheet: {e}")
		return {"success": False, "usd_amount": None}


async def get_xmr_column(xmr_number: int) -> Optional[str]:
	"""
	Определяет столбец для записи USD по номеру XMR из базы данных.
	
	Args:
		xmr_number: Номер XMR (1, 2 или 3)
	
	Returns:
		Буква столбца (AU, AV, AW в зависимости от номера) или None, если не найдено
	"""
	db = get_db()
	crypto_type = f"XMR-{xmr_number}"
	column = await db.get_crypto_column(crypto_type)
	if column:
		return column
	# Fallback на старые значения, если не найдено в базе
	fallback_columns = {
		1: "AU",  # XMR-1 → USD в столбец AU
		2: "AV",  # XMR-2 → USD в столбец AV
		3: "AW"   # XMR-3 → USD в столбец AW
	}
	return fallback_columns.get(xmr_number, "AU")  # По умолчанию AU


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
		# Теперь курс XMR не нужен, так как пользователь вводит USD напрямую
		# Определяем столбец для записи USD из базы данных
		usd_column = await get_xmr_column(xmr_number)
		if not usd_column:
			logger.warning(f"⚠️ Не найден адрес столбца для XMR-{xmr_number}")
			return {"success": False, "usd_amount": None}
		
		# Вычисляем адрес столбца для наличных, если есть карта
		if cash_data and card_data:
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			column = await get_card_column(card_name, user_name)
			if column:
				# Добавляем адрес столбца в данные карты
				card_data = card_data.copy()
				card_data["column"] = column
				logger.debug(f"✅ Адрес столбца вычислен: card_name='{card_name}', user_name='{user_name}' -> column='{column}'")
			else:
				logger.warning(f"⚠️ Не удалось определить адрес столбца для card_name='{card_name}', user_name='{user_name}'")
		
		# Выполняем синхронную запись в отдельном потоке
		# Передаем None для xmr_price, так как он больше не используется
		return await asyncio.to_thread(
			_write_xmr_to_google_sheet_sync,
			sheet_id,
			credentials_path,
			crypto_data,
			cash_data,
			card_data,
			xmr_number,
			usd_column,
			None  # xmr_price больше не нужен
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
	xmr_price: Optional[float]  # Оставлено для обратной совместимости, но не используется
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
		
		# Обрабатываем XMR: записываем USD напрямую (пользователь ввел USD)
		usd_amount_rounded = None
		if crypto_data and crypto_data.get("currency") == "XMR":
			# Получаем USD напрямую из данных (пользователь ввел USD)
			usd_amount = crypto_data.get("usd_amount", crypto_data.get("value", 0.0))
			
			if usd_amount > 0:
				usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
				# Записываем USD в соответствующий столбец (метод update требует список списков)
				worksheet.update(f"{usd_column}{empty_row}", [[usd_amount_rounded]])
				logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {usd_column}{empty_row} (XMR-{xmr_number})")
			else:
				logger.warning(f"⚠️ USD сумма не указана для XMR-{xmr_number}")
		
		# Обрабатываем наличные (RUB, BYN и другие валюты)
		if cash_data and card_data:
			cash_currency = cash_data.get("currency", "")
			cash_amount = cash_data.get("value", 0)
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			
			# Получаем адрес столбца из параметра (вычислен заранее в асинхронной функции)
			column = card_data.get("column")
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

