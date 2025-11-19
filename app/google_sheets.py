"""
Модуль для работы с Google Sheets API
"""
import logging
import asyncio
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
	"""
	# Нормализуем имена для сравнения
	card_upper = card_name.upper() if card_name else ""
	user_upper = user_name.upper() if user_name else ""
	
	logger.debug(f"🔍 get_card_column: card_name='{card_name}', user_name='{user_name}' -> card_upper='{card_upper}', user_upper='{user_upper}'")
	
	# Проверяем наличие имени и инициала
	has_artem = "АРТЕМ" in user_upper or "АРТЁМ" in user_upper
	has_evgeniy = "ЕВГЕНИЙ" in user_upper
	has_v = ("В" in user_upper or "В." in user_upper) and not ("С" in user_upper or "С." in user_upper)
	has_s = ("С" in user_upper or "С." in user_upper) and not ("В" in user_upper or "В." in user_upper)
	has_r = ("Р" in user_upper or "Р." in user_upper) and not ("С" in user_upper or "С." in user_upper) and not ("В" in user_upper or "В." in user_upper)
	
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
	
	logger.warning(f"❌ Не найдено соответствие для карты '{card_name}' и пользователя '{user_name}' (card_upper='{card_upper}', user_upper='{user_upper}')")
	return None


def _write_to_google_sheet_sync(
	sheet_id: str,
	credentials_path: str,
	crypto_data: Optional[Dict],
	cash_data: Optional[Dict],
	card_data: Optional[Dict],
	btc_price: Optional[float]
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
		logger.info(f"📊 Данные для записи: crypto={crypto_data}, cash={cash_data}, card={card_data}, btc_price={btc_price}")
		
		# Находим свободную строку в столбце BC
		empty_row = _find_empty_row_in_column(worksheet, "BC", start_row=5)
		logger.info(f"📍 Найдена свободная строка: {empty_row}")
		
		# Обрабатываем криптовалюту (BTC)
		usd_amount_rounded = None
		if crypto_data and crypto_data.get("currency") == "BTC":
			btc_amount = crypto_data.get("value", 0.0)
			
			if btc_price:
				usd_amount = btc_amount * btc_price
				usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
				# Записываем USD в столбец AS (метод update требует список списков)
				worksheet.update(f"AS{empty_row}", [[usd_amount_rounded]])
				logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку AS{empty_row} (BTC: {btc_amount}, курс: {btc_price})")
			else:
				logger.warning(f"⚠️ Не удалось получить курс BTC, пропускаем запись криптовалюты. BTC количество: {btc_amount}")
		
		# Обрабатываем наличные (RUB)
		if cash_data and cash_data.get("currency") == "RUB" and card_data:
			rub_amount = cash_data.get("value", 0)
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			
			# Определяем столбец для записи
			column = get_card_column(card_name, user_name)
			if column:
				# Метод update требует список списков
				worksheet.update(f"{column}{empty_row}", [[rub_amount]])
				logger.info(f"✅ Записано {rub_amount} RUB в ячейку {column}{empty_row}")
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
		# Получаем курс BTC
		btc_price = None
		if crypto_data and crypto_data.get("currency") == "BTC":
			btc_price = await get_btc_price_usd()
		
		# Выполняем синхронную запись в отдельном потоке
		return await asyncio.to_thread(
			_write_to_google_sheet_sync,
			sheet_id,
			credentials_path,
			crypto_data,
			cash_data,
			card_data,
			btc_price
		)
	except Exception as e:
		logger.exception(f"Ошибка записи в Google Sheet: {e}")
		return {"success": False, "usd_amount": None}

