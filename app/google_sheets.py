"""
Модуль для работы с Google Sheets API
"""
import logging
import asyncio
import re
from typing import Optional, Dict, Any, List
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


def _get_worksheet(spreadsheet: gspread.Spreadsheet, sheet_name: Optional[str] = None) -> gspread.Worksheet:
	"""
	Получает лист из таблицы по имени или первый лист по умолчанию.
	
	Args:
		spreadsheet: Объект таблицы Google Sheets
		sheet_name: Название листа (если None или пустое, используется первый лист)
	
	Returns:
		Объект листа Google Sheets
	"""
	if sheet_name and sheet_name.strip():
		try:
			worksheet = spreadsheet.worksheet(sheet_name.strip())
			logger.debug(f"✅ Используется лист '{sheet_name}'")
			return worksheet
		except gspread.exceptions.WorksheetNotFound:
			logger.warning(f"⚠️ Лист '{sheet_name}' не найден, используется первый лист")
			return spreadsheet.sheet1
	else:
		logger.debug("✅ Используется первый лист (по умолчанию)")
		return spreadsheet.sheet1


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


def _find_empty_cell_in_column(sheet: gspread.Worksheet, column: str, start_row: int = 348, max_row: Optional[int] = None) -> int:
	"""
	Находит первую пустую ячейку в указанном столбце, начиная с start_row.
	Возвращает номер строки первой пустой ячейки.
	Использует batch чтение для оптимизации (читает по 50 строк за раз).
	
	Args:
		sheet: Рабочий лист Google Sheets
		column: Буква столбца (например, "G")
		start_row: Номер строки, с которой начинать поиск
		max_row: Максимальный номер строки для поиска (если None, ищет до start_row + 1000)
	"""
	try:
		batch_size = 50
		row = start_row
		
		# Определяем максимальную строку для поиска
		if max_row is not None:
			search_limit = max_row
		else:
			search_limit = start_row + 1000
		
		while row <= search_limit:
			# Читаем batch строк за один запрос
			end_row = min(row + batch_size - 1, search_limit)
			range_str = f"{column}{row}:{column}{end_row}"
			
			try:
				values = sheet.get(range_str)
				# values - это список списков, например [['1'], ['2'], [], ...]
				# Если весь диапазон пустой, API может вернуть None или пустой список
				expected_rows = end_row - row + 1
				received_rows = len(values) if values else 0
				logger.info(f"🔍 Прочитан диапазон {range_str}: ожидалось {expected_rows} строк, получено {received_rows} значений")
				
				# Детальное логирование всех полученных значений
				if values:
					logger.info(f"📋 Детализация значений в диапазоне {range_str}:")
					for i, cell_list in enumerate(values):
						current_row = row + i
						if cell_list and len(cell_list) > 0:
							cell_value = cell_list[0]
							cell_str = str(cell_value) if cell_value else ""
							cell_length = len(cell_str)
							logger.info(f"  Строка {current_row}: значение='{cell_value}' (длина: {cell_length}, тип: {type(cell_value).__name__})")
						else:
							logger.info(f"  Строка {current_row}: ПУСТАЯ (пустой список)")
				else:
					logger.info(f"📋 Диапазон {range_str}: values = None или пустой список")
				
				# Если values пустой или None, значит все ячейки в диапазоне пустые
				if not values or len(values) == 0:
					logger.info(f"✅ Диапазон {range_str} полностью пустой, возвращаем первую строку {row}")
					return row
				
				# Если получено меньше значений, чем ожидалось, нужно проверить каждую строку по отдельности,
				# так как Google Sheets API может вернуть только непустые значения, и они могут быть не в начале диапазона
				if received_rows < expected_rows:
					logger.info(f"⚠️ Получено меньше значений ({received_rows} из {expected_rows}), проверяем каждую строку по отдельности")
					# Проверяем каждую строку в диапазоне, начиная с start_row
					for check_row in range(row, end_row + 1):
						if max_row is not None and check_row > max_row:
							logger.warning(f"⚠️ Достигнут лимит строки {max_row} в столбце {column}")
							return max_row + 1
						try:
							check_value = sheet.acell(f"{column}{check_row}").value
							if check_value is None or str(check_value).strip() == "":
								logger.info(f"✅ Найдена первая пустая ячейка в строке {check_row}")
								return check_row
							else:
								logger.info(f"  Строка {check_row}: заполнена значением '{check_value}' (длина: {len(str(check_value))})")
						except Exception as e:
							logger.warning(f"⚠️ Ошибка проверки строки {check_row}: {e}, считаем пустой")
							return check_row
					# Если все строки в диапазоне заполнены, продолжаем поиск дальше
					first_empty_row = end_row + 1
					if max_row is not None and first_empty_row > max_row:
						logger.warning(f"⚠️ Первая пустая ячейка {first_empty_row} превышает лимит {max_row}")
						return max_row + 1
					logger.info(f"✅ Все строки в диапазоне {range_str} заполнены, продолжаем поиск с строки {first_empty_row}")
					row = first_empty_row
					continue
				
				# Проверяем каждую полученную ячейку
				for i, cell_list in enumerate(values):
					current_row = row + i
					
					# Проверяем, не превысили ли лимит
					if max_row is not None and current_row > max_row:
						logger.warning(f"⚠️ Достигнут лимит строки {max_row} в столбце {column}, начиная с {start_row}")
						return max_row + 1  # Возвращаем значение больше лимита, чтобы показать, что места нет
					
					# Если список пустой или содержит пустую строку, значит ячейка пустая
					if not cell_list or len(cell_list) == 0:
						logger.info(f"✅ Найдена пустая ячейка в строке {current_row} (пустой список)")
						return current_row
					
					cell_value = cell_list[0] if cell_list else None
					
					# Проверяем, является ли значение пустым (None, пустая строка, или только пробелы)
					if cell_value is None:
						logger.info(f"✅ Найдена пустая ячейка в строке {current_row} (None)")
						return current_row
					
					# Преобразуем в строку и убираем пробелы для проверки
					cell_str = str(cell_value).strip() if cell_value else ""
					if cell_str == "":
						logger.info(f"✅ Найдена пустая ячейка в строке {current_row} (пустая строка или пробелы)")
						return current_row
					
					logger.debug(f"Строка {current_row}: значение='{cell_value}' (тип: {type(cell_value)})")
				
				# Если в этом batch не нашли пустую, переходим к следующему
				row = end_row + 1
				
			except Exception as e:
				logger.warning(f"Ошибка чтения диапазона {range_str}: {e}, пробуем по одной ячейке")
				# Fallback: читаем по одной ячейке
				if max_row is not None and row > max_row:
					logger.warning(f"Достигнут лимит строки {max_row} в столбце {column}, начиная с {start_row}")
					return max_row + 1
				
				cell_value = sheet.acell(f"{column}{row}").value
				if cell_value is None or cell_value == "":
					return row
				row += 1
		
		logger.warning(f"Не найдена пустая ячейка в столбце {column}, начиная с {start_row} до {search_limit}")
		return search_limit + 1
		
	except Exception as e:
		logger.exception(f"Ошибка поиска пустой ячейки: {e}")
		return start_row


def _find_empty_row_by_row(sheet: gspread.Worksheet, start_row: int = 5, max_row: Optional[int] = None, start_column: str = "A", end_column: str = "BB") -> int:
	"""
	Находит первую полностью пустую строку, проверяя все столбцы от start_column до end_column.
	Использует batch чтение для оптимизации (читает по несколько строк за раз).
	
	Args:
		sheet: Рабочий лист Google Sheets
		start_row: Номер строки, с которой начинать поиск
		max_row: Максимальный номер строки для поиска (если None, ищет до start_row + 1000)
		start_column: Начальный столбец для проверки (по умолчанию "A")
		end_column: Конечный столбец для проверки (по умолчанию "BB")
	
	Returns:
		Номер первой полностью пустой строки или max_row + 1, если не найдено
	"""
	try:
		# Для небольших диапазонов (например, 375-406 = 32 строки) читаем весь диапазон за раз
		# Для больших диапазонов используем batch чтение по 10-15 строк
		if max_row is not None:
			total_rows = max_row - start_row + 1
			if total_rows <= 50:
				# Читаем весь диапазон за один запрос
				batch_size = total_rows
			else:
				# Используем batch чтение
				batch_size = 15
			search_limit = max_row
		else:
			batch_size = 15
			search_limit = start_row + 1000
		
		row = start_row
		
		while row <= search_limit:
			# Читаем batch строк за один запрос
			end_row = min(row + batch_size - 1, search_limit)
			range_str = f"{start_column}{row}:{end_column}{end_row}"
			
			try:
				# Читаем весь диапазон строк за один запрос
				values = sheet.get(range_str)
				# values - это список списков строк, например:
				# [
				#   [['val1', 'val2', ...], ['val3', 'val4', ...]],  # строка 1
				#   [[], []],  # строка 2 (пустая)
				#   ...
				# ]
				
				expected_rows = end_row - row + 1
				received_rows = len(values) if values else 0
				
				logger.debug(f"🔍 Прочитан диапазон {range_str}: ожидалось {expected_rows} строк, получено {received_rows} строк")
				
				# Если values пустой или None, значит все строки в диапазоне пустые
				if not values or len(values) == 0:
					logger.info(f"✅ Диапазон {range_str} полностью пустой, возвращаем первую строку {row}")
					return row
				
				# Проверяем каждую строку
				# Google Sheets API возвращает данные в формате:
				# При чтении диапазона A375:BB406, values[i] - это список значений строки
				# values[i] = ['val1', 'val2', ..., 'valBB'] или [] если строка пустая
				
				for i in range(expected_rows):
					current_row = row + i
					
					# Проверяем, не превысили ли лимит
					if max_row is not None and current_row > max_row:
						logger.warning(f"⚠️ Достигнут лимит строки {max_row}")
						return max_row + 1
					
					# Если строка не в полученных данных, значит она пустая
					if i >= received_rows:
						logger.info(f"✅ Найдена пустая строка {current_row} (не найдена в ответе API)")
						return current_row
					
					# Получаем данные строки
					# values[i] - это уже список значений строки (не список списков)
					row_values = values[i] if i < len(values) else []
					
					# Проверяем, есть ли в строке хотя бы одно непустое значение
					is_empty = True
					if row_values and len(row_values) > 0:
						for cell_value in row_values:
							if cell_value is not None:
								# Преобразуем в строку и проверяем, не пустая ли она
								cell_str = str(cell_value).strip() if cell_value else ""
								if cell_str != "":
									# Найдено непустое значение
									is_empty = False
									break
					
					if is_empty:
						logger.info(f"✅ Найдена полностью пустая строка {current_row}")
						return current_row
					else:
						logger.debug(f"Строка {current_row} содержит данные, пропускаем")
				
				# Если в этом batch не нашли пустую строку, переходим к следующему
				row = end_row + 1
				
			except Exception as e:
				logger.warning(f"Ошибка чтения диапазона {range_str}: {e}, пробуем по одной строке")
				# Fallback: проверяем по одной строке
				if max_row is not None and row > max_row:
					return max_row + 1
				
				try:
					# Читаем одну строку
					row_range = f"{start_column}{row}:{end_column}{row}"
					row_data = sheet.get(row_range)
					
					# Проверяем, пустая ли строка
					# row_data - это список, содержащий один список со всеми значениями строки
					# row_data = [['val1', 'val2', ..., 'valBB']] или [] если строка пустая
					is_empty = True
					if row_data and len(row_data) > 0:
						# row_data[0] - это список всех значений в строке
						row_values = row_data[0] if row_data[0] else []
						for cell_value in row_values:
							if cell_value is not None:
								cell_str = str(cell_value).strip() if cell_value else ""
								if cell_str != "":
									is_empty = False
									break
					
					if is_empty:
						return row
					row += 1
				except Exception as e2:
					logger.warning(f"Ошибка чтения строки {row}: {e2}")
					row += 1
		
		logger.warning(f"Не найдена пустая строка в диапазоне {start_row}-{search_limit}")
		return search_limit + 1
		
	except Exception as e:
		logger.exception(f"Ошибка поиска пустой строки построчно: {e}")
		return start_row


def _find_empty_row_in_column(sheet: gspread.Worksheet, column: str, start_row: int = 5, max_row: Optional[int] = None) -> int:
	"""
	Находит первую строку с 0 в указанном столбце, начиная с start_row.
	Возвращает номер строки.
	Использует batch чтение для оптимизации (читает по 50 строк за раз).
	
	Args:
		sheet: Рабочий лист Google Sheets
		column: Буква столбца (например, "BC")
		start_row: Номер строки, с которой начинать поиск
		max_row: Максимальный номер строки для поиска (если None, ищет до start_row + 1000)
	"""
	try:
		batch_size = 50
		row = start_row
		
		# Определяем максимальную строку для поиска
		if max_row is not None:
			search_limit = max_row
		else:
			search_limit = start_row + 1000
		
		while row <= search_limit:
			# Читаем batch строк за один запрос
			end_row = min(row + batch_size - 1, search_limit)
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
		
		logger.warning(f"Не найдена свободная строка в столбце {column}, начиная с {start_row} до {search_limit}")
		return search_limit + 1
		
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
	crypto_column: Optional[str] = None,
	sheet_name: Optional[str] = None
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
			worksheet = _get_worksheet(spreadsheet, sheet_name)
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
			
			if usd_amount != 0:  # Разрешаем как положительные, так и отрицательные значения
				usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
				
				if crypto_column:
					# Записываем USD в столбец из базы данных (метод update требует список списков)
					worksheet.update(f"{crypto_column}{empty_row}", [[usd_amount_rounded]])
					logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {crypto_column}{empty_row} ({crypto_currency})")
				else:
					logger.warning(f"⚠️ Не найден адрес столбца для криптовалюты {crypto_currency}")
			else:
				logger.warning(f"⚠️ USD сумма равна 0 для криптовалюты {crypto_currency}")
		
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
	card_data: Optional[Dict],
	sheet_name: Optional[str] = None
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
		crypto_column = None
		if crypto_data:
			db = get_db()
			crypto_currency = crypto_data.get("currency")
			if crypto_currency:
				crypto_column = await db.get_crypto_column(crypto_currency)
				if crypto_column:
					logger.info(f"✅ Получен столбец для криптовалюты '{crypto_currency}': {crypto_column}")
				else:
					logger.warning(f"⚠️ Не найден столбец для криптовалюты '{crypto_currency}'")
		
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
			crypto_column,
			sheet_name
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
	xmr_number: int,
	sheet_name: Optional[str] = None
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
			None,  # xmr_price больше не нужен
			sheet_name
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
	xmr_price: Optional[float],  # Оставлено для обратной совместимости, но не используется
	sheet_name: Optional[str] = None
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
			worksheet = _get_worksheet(spreadsheet, sheet_name)
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
			
			if usd_amount != 0:  # Разрешаем как положительные, так и отрицательные значения
				usd_amount_rounded = int(round(usd_amount))  # Округляем до целого
				# Записываем USD в соответствующий столбец (метод update требует список списков)
				worksheet.update(f"{usd_column}{empty_row}", [[usd_amount_rounded]])
				logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {usd_column}{empty_row} (XMR-{xmr_number})")
			else:
				logger.warning(f"⚠️ USD сумма равна 0 для XMR-{xmr_number}")
		
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


async def write_all_to_google_sheet_one_row(
	sheet_id: str,
	credentials_path: str,
	crypto_list: list,  # [{"currency": "BTC", "usd_amount": 100}, ...]
	xmr_list: list,  # [{"xmr_number": 1, "usd_amount": 50}, ...]
	cash_list: list,  # [{"currency": "RUB", "value": 5000}, ...] - для наличных без карты
	card_cash_pairs: list,  # [{"card": {...}, "cash": {...}}, ...] - пары карта-наличные
	sheet_name: Optional[str] = None,
	start_row: Optional[int] = None,
	max_row: Optional[int] = None
) -> Dict[str, Any]:
	"""
	Записывает все данные в одну строку Google Sheets.
	Объединяет все криптовалюты, XMR, наличные и карты в одну запись.
	
	Args:
		sheet_id: ID Google Sheet
		credentials_path: Путь к файлу с учетными данными
		crypto_list: Список криптовалют (BTC, LTC, USDT)
		xmr_list: Список XMR данных
		cash_list: Список наличных
		card_list: Список карт
		
	Returns:
		Словарь с результатами: {"success": bool}
	"""
	try:
		# Получаем адреса столбцов для криптовалют из базы данных
		db = get_db()
		# Используем словарь для хранения столбцов всех криптовалют
		crypto_columns = {}  # {currency: column}
		
		# Получаем адреса столбцов для всех используемых криптовалют
		for crypto in crypto_list:
			currency = crypto.get("currency")
			if currency and currency not in crypto_columns:
				column = await db.get_crypto_column(currency)
				if column:
					crypto_columns[currency] = column
					logger.info(f"✅ Получен столбец для криптовалюты '{currency}': {column}")
				else:
					logger.warning(f"⚠️ Не найден столбец для криптовалюты '{currency}'")
		
		# Получаем адреса столбцов для XMR
		xmr_columns = {}
		for xmr in xmr_list:
			xmr_number = xmr.get("xmr_number")
			if xmr_number not in xmr_columns:
				xmr_columns[xmr_number] = await get_xmr_column(xmr_number)
		
		# Вычисляем адреса столбцов для карт (по card_id для правильного суммирования)
		card_columns = {}
		for pair in card_cash_pairs:
			card_data = pair.get("card")
			card_id = card_data.get("card_id")
			if card_id and card_id not in card_columns:
				# Получаем column из базы данных по card_id
				card_column = await db.get_card_column(card_id)
				card_columns[card_id] = card_column
				# Добавляем адрес столбца в данные карты
				card_data["column"] = card_column
			elif card_id and card_id in card_columns:
				# Если column уже определен, используем его
				card_data["column"] = card_columns[card_id]
		
		# Получаем адреса столбцов для наличных
		cash_columns = {}
		logger.info(f"🔍 Обработка наличных в режиме /add: cash_list={cash_list}")
		for cash in cash_list:
			cash_name = cash.get("cash_name")
			logger.info(f"🔍 Обработка наличных: cash_name={cash_name}, cash={cash}")
			if cash_name:
				if cash_name not in cash_columns:
					cash_column = await db.get_cash_column(cash_name)
					cash_columns[cash_name] = cash_column
					logger.info(f"🔍 Получен адрес столбца для наличных: cash_name={cash_name}, column={cash_column}")
				# Всегда добавляем адрес столбца в данные наличных (даже если уже был получен ранее)
				cash["column"] = cash_columns[cash_name]
			else:
				logger.warning(f"⚠️ Наличные без названия: cash={cash}")
		
		# Получаем диапазон столбцов из БД для построчной проверки
		# Используется для /add, /move и других режимов, где нужна построчная проверка
		db = get_db()
		delete_range_str = await db.get_google_sheets_setting("delete_range", "A:BB")
		start_column = "A"
		end_column = "BB"
		if delete_range_str and ":" in delete_range_str:
			parts = delete_range_str.split(":")
			if len(parts) == 2:
				start_column = parts[0].strip()
				end_column = parts[1].strip()
				logger.info(f"📍 Используется диапазон столбцов из БД: {start_column}:{end_column}")
		
		# Выполняем синхронную запись в отдельном потоке
		return await asyncio.to_thread(
			_write_all_to_google_sheet_one_row_sync,
			sheet_id,
			credentials_path,
			crypto_list,
			xmr_list,
			cash_list,
			card_cash_pairs,
			crypto_columns,
			xmr_columns,
			sheet_name,
			start_row,
			max_row,
			start_column,
			end_column
		)
	except Exception as e:
		logger.exception(f"Ошибка записи всех данных в Google Sheet: {e}")
		return {"success": False}


def _write_all_to_google_sheet_one_row_sync(
	sheet_id: str,
	credentials_path: str,
	crypto_list: list,
	xmr_list: list,
	cash_list: list,
	card_cash_pairs: list,
	crypto_columns: Dict[str, Optional[str]],  # {currency: column}
	xmr_columns: Dict[int, Optional[str]],
	sheet_name: Optional[str] = None,
	start_row: Optional[int] = None,
	max_row: Optional[int] = None,
	start_column: str = "A",
	end_column: str = "BB"
) -> Dict[str, Any]:
	"""
	Синхронная функция для записи всех данных в одну строку Google Sheets.
	"""
	try:
		# Создаем клиент
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return {"success": False, "written_cells": []}
		
		# Открываем таблицу
		try:
			spreadsheet = client.open_by_key(sheet_id)
			worksheet = _get_worksheet(spreadsheet, sheet_name)
		except PermissionError as e:
			logger.error(f"Ошибка доступа к таблице: {e}")
			raise
		
		# Находим одну свободную строку
		# Если start_row не указан, используем значение по умолчанию 5
		search_start_row = start_row if start_row is not None else 5
		
		# Используем построчную проверку для всех режимов, где запись идет в одну строку
		# Это более надежно, чем проверка по столбцу BC, так как профит может быть 0
		# Для /move (start_row >= 375) и /add (start_row < 375) используем построчную проверку
		empty_row = _find_empty_row_by_row(
			worksheet, 
			start_row=search_start_row, 
			max_row=max_row,
			start_column=start_column,
			end_column=end_column
		)
		mode_name = "/move" if (max_row is not None and search_start_row >= 375) else "/add"
		logger.info(f"📍 Построчная проверка для {mode_name} (диапазон {start_column}:{end_column}): найдена свободная строка {empty_row}")
		
		# Проверяем, не превышен ли лимит
		if max_row is not None and empty_row > max_row:
			logger.warning(f"⚠️ Не найдена свободная строка в диапазоне {search_start_row}-{max_row}, найдена строка {empty_row}")
			return {"success": False, "written_cells": [], "message": f"Нет свободных строк в диапазоне {search_start_row}-{max_row}"}
		
		logger.info(f"📍 Найдена свободная строка для объединенной записи: {empty_row}")
		
		written_cells = []  # Список записанных ячеек для отчета
		
		# Суммируем криптовалюты с одинаковой валютой
		crypto_sum = {}  # {currency: total_amount}
		for crypto in crypto_list:
			currency = crypto.get("currency")
			usd_amount = crypto.get("usd_amount", 0.0)
			if usd_amount != 0:
				if currency not in crypto_sum:
					crypto_sum[currency] = 0.0
				crypto_sum[currency] += usd_amount
		
		# Записываем суммированные криптовалюты (все, не только BTC, LTC, USDT)
		for currency, total_amount in crypto_sum.items():
			usd_amount_rounded = int(round(total_amount))
			column = crypto_columns.get(currency)
			
			# Записываем в соответствующий столбец, если он найден
			if column:
				worksheet.update(f"{column}{empty_row}", [[usd_amount_rounded]])
				written_cells.append(f"{column}{empty_row} ({currency}: {usd_amount_rounded} USD)")
				logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {column}{empty_row} ({currency})")
			else:
				logger.warning(f"⚠️ Не записано {currency}: {usd_amount_rounded} USD - не найден столбец для криптовалюты '{currency}'")
				written_cells.append(f"⚠️ {currency}: {usd_amount_rounded} USD (столбец не найден)")
		
		# Суммируем XMR с одинаковым номером
		xmr_sum = {}  # {xmr_number: total_amount}
		for xmr in xmr_list:
			xmr_number = xmr.get("xmr_number")
			usd_amount = xmr.get("usd_amount", 0.0)
			if usd_amount != 0:
				if xmr_number not in xmr_sum:
					xmr_sum[xmr_number] = 0.0
				xmr_sum[xmr_number] += usd_amount
		
		# Записываем суммированные XMR
		for xmr_number, total_amount in xmr_sum.items():
			usd_amount_rounded = int(round(total_amount))
			usd_column = xmr_columns.get(xmr_number)
			
			if usd_column:
				worksheet.update(f"{usd_column}{empty_row}", [[usd_amount_rounded]])
				written_cells.append(f"{usd_column}{empty_row} (XMR-{xmr_number}: {usd_amount_rounded} USD)")
				logger.info(f"✅ Записано {usd_amount_rounded} USD в ячейку {usd_column}{empty_row} (XMR-{xmr_number})")
		
		# Суммируем наличные для каждой карты (по card_id для правильного суммирования)
		card_cash_sum = {}  # {card_id: {"column": column, "amount": total_amount, "card_name": card_name}}
		for pair in card_cash_pairs:
			card_data = pair.get("card")
			cash_data = pair.get("cash")
			card_id = card_data.get("card_id")
			column = card_data.get("column")
			
			if card_id and column and cash_data:
				cash_amount = cash_data.get("value", 0)
				if cash_amount != 0:
					if card_id not in card_cash_sum:
						card_cash_sum[card_id] = {
							"column": column,
							"amount": 0,
							"card_name": card_data.get("card_name", "")
						}
					card_cash_sum[card_id]["amount"] += cash_amount
		
		# Записываем суммированные наличные для карт
		for card_id, card_info in card_cash_sum.items():
			column = card_info["column"]
			total_amount = card_info["amount"]
			card_name = card_info["card_name"]
			
			if total_amount != 0:
				worksheet.update(f"{column}{empty_row}", [[total_amount]])
				written_cells.append(f"{column}{empty_row} (Карта {card_name}: {total_amount} RUB)")
				logger.info(f"✅ Записано {total_amount} RUB в ячейку {column}{empty_row} (карта: {card_name})")
		
		# Суммируем наличные без карты (по cash_name)
		cash_sum = {}  # {cash_name: {"column": column, "amount": total_amount, "currency": currency}}
		logger.info(f"🔍 Запись наличных без карты в режиме /add: cash_list={cash_list}, len={len(cash_list)}")
		for cash in cash_list:
			cash_name = cash.get("cash_name", "")
			cash_currency = cash.get("currency", "RUB")
			cash_amount = cash.get("value", 0)
			column = cash.get("column")
			logger.info(f"🔍 Наличные для записи: cash_name={cash_name}, amount={cash_amount}, column={column}")
			
			if column and cash_amount != 0:
				if cash_name not in cash_sum:
					cash_sum[cash_name] = {"column": column, "amount": 0, "currency": cash_currency}
				cash_sum[cash_name]["amount"] += cash_amount
		
		# Записываем суммированные наличные без карты
		for cash_name, cash_data in cash_sum.items():
			column = cash_data["column"]
			total_amount = cash_data["amount"]
			cash_currency = cash_data["currency"]
			
			if total_amount != 0:
				worksheet.update(f"{column}{empty_row}", [[total_amount]])
				written_cells.append(f"{column}{empty_row} (Наличные {cash_name}: {total_amount} {cash_currency})")
				logger.info(f"✅ Записано {total_amount} {cash_currency} в ячейку {column}{empty_row} (наличные: {cash_name})")
		
		return {"success": True, "written_cells": written_cells, "row": empty_row}
		
	except Exception as e:
		logger.exception(f"Ошибка записи всех данных в Google Sheet: {e}")
		return {"success": False}


async def delete_last_row_from_google_sheet(
	sheet_id: str,
	credentials_path: str,
	sheet_name: Optional[str] = None
) -> Dict[str, Any]:
	"""
	Удаляет последнюю добавленную строку из Google Sheets.
	Ищет последнюю заполненную строку построчно в диапазоне текущего дня недели (как в /add).
	Удаляет найденную строку в диапазоне, указанном в настройках (по умолчанию A:BB).
	
	Args:
		sheet_id: ID Google Sheet
		credentials_path: Путь к файлу с учетными данными
		sheet_name: Название листа (опционально)
		
	Returns:
		Словарь с результатами: {"success": bool, "deleted_row": int | None, "message": str}
	"""
	try:
		# Определяем текущий день недели
		from datetime import datetime
		current_date = datetime.now()
		weekday = current_date.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
		
		# Ключи настроек для каждого дня недели
		day_setting_keys = {
			0: ("add_monday_start", "add_monday_max"),    # Понедельник
			1: ("add_tuesday_start", "add_tuesday_max"),  # Вторник
			2: ("add_wednesday_start", "add_wednesday_max"), # Среда
			3: ("add_thursday_start", "add_thursday_max"), # Четверг
			4: ("add_friday_start", "add_friday_max"),    # Пятница
			5: ("add_saturday_start", "add_saturday_max"), # Суббота
			6: ("add_sunday_start", "add_sunday_max")     # Воскресенье
		}
		
		# Значения по умолчанию (на случай, если настройки не найдены)
		default_ranges = {
			0: (5, 54),    # Понедельник
			1: (55, 104),  # Вторник
			2: (105, 154), # Среда
			3: (155, 204), # Четверг
			4: (205, 254), # Пятница
			5: (255, 304), # Суббота
			6: (305, 364)  # Воскресенье
		}
		
		# Получаем настройки из базы данных
		db = get_db()
		delete_range = await db.get_google_sheets_setting("delete_range", "A:BB")
		
		# Получаем диапазон для текущего дня недели
		start_key, max_key = day_setting_keys.get(weekday, ("add_monday_start", "add_monday_max"))
		default_start, default_max = default_ranges.get(weekday, (5, 54))
		
		start_row_str = await db.get_google_sheets_setting(start_key, str(default_start))
		max_row_str = await db.get_google_sheets_setting(max_key, str(default_max))
		
		try:
			start_row = int(start_row_str) if start_row_str else default_start
			max_row = int(max_row_str) if max_row_str else default_max
		except (ValueError, TypeError):
			start_row, max_row = default_start, default_max
			logger.warning(f"Неверные значения для дня недели {weekday}, используем значения по умолчанию")
		
		# Парсим диапазон столбцов
		start_column = "A"
		end_column = "BB"
		if delete_range and ":" in delete_range:
			parts = delete_range.split(":")
			if len(parts) == 2:
				start_column = parts[0].strip()
				end_column = parts[1].strip()
		
		# Названия дней недели для логирования
		day_names = {
			0: "Понедельник",
			1: "Вторник",
			2: "Среда",
			3: "Четверг",
			4: "Пятница",
			5: "Суббота",
			6: "Воскресенье"
		}
		day_name = day_names.get(weekday, "Понедельник")
		logger.info(f"🗑️ /del: {day_name}, поиск последней строки в диапазоне {start_row}-{max_row}")
		
		# Выполняем синхронное удаление в отдельном потоке
		return await asyncio.to_thread(
			_delete_last_row_from_google_sheet_sync,
			sheet_id,
			credentials_path,
			delete_range,
			start_row,
			max_row,
			start_column,
			end_column,
			sheet_name
		)
	except Exception as e:
		logger.exception(f"Ошибка удаления последней строки из Google Sheet: {e}")
		return {"success": False, "deleted_row": None, "message": f"Ошибка: {str(e)}"}


def _find_last_filled_row_by_row(worksheet: gspread.Worksheet, start_row: int = 5, start_column: str = "A", end_column: str = "BB", max_row: Optional[int] = None) -> Optional[int]:
	"""
	Находит последнюю заполненную строку, проверяя все столбцы от start_column до end_column.
	Возвращает номер последней строки, которая содержит хотя бы одно непустое значение.
	
	Args:
		worksheet: Рабочий лист Google Sheets
		start_row: Номер строки, с которой начинать поиск
		start_column: Начальный столбец для проверки
		end_column: Конечный столбец для проверки
		max_row: Максимальный номер строки для проверки (включительно). Если None, проверяет до конца таблицы.
	
	Returns:
		Номер последней заполненной строки или None, если не найдено
	"""
	try:
		# Читаем значения пакетами для оптимизации
		batch_size = 50
		current_row = start_row
		last_filled_row = None
		
		# Определяем максимальный номер строки для проверки
		if max_row is None:
			# Если max_row не указан, используем большое число
			max_row = start_row + 10000
		
		# Флаг для остановки при выходе за пределы таблицы
		exceeded_limits = False
		
		while current_row <= max_row and not exceeded_limits:
			try:
				# Читаем пакет строк
				end_row = min(current_row + batch_size - 1, max_row)
				range_str = f"{start_column}{current_row}:{end_column}{end_row}"
				values = worksheet.get(range_str)
				
				if not values or len(values) == 0:
					# Если нет значений, значит достигли конца данных
					break
				
				# Проверяем каждую строку в пакете
				for i in range(len(values)):
					row_num = current_row + i
					if row_num > max_row:
						break
					row_data = values[i] if i < len(values) else []
					
					# Проверяем, есть ли в строке хотя бы одно непустое значение
					has_data = False
					if row_data and len(row_data) > 0:
						for cell_value in row_data:
							if cell_value is not None:
								cell_str = str(cell_value).strip() if cell_value else ""
								if cell_str != "":
									has_data = True
									break
					
					if has_data:
						last_filled_row = row_num
				
				# Переходим к следующему пакету
				current_row = end_row + 1
				
			except Exception as e:
				error_str = str(e)
				# Проверяем, не вышли ли мы за пределы таблицы
				if "exceeds grid limits" in error_str or "400" in error_str:
					logger.info(f"Достигнут предел таблицы при чтении диапазона {range_str}, прекращаем поиск")
					exceeded_limits = True
					break
				
				logger.warning(f"Ошибка чтения диапазона {range_str}: {e}")
				# Пробуем по одной строке
				try:
					if current_row > max_row:
						break
					row_range = f"{start_column}{current_row}:{end_column}{current_row}"
					row_data = worksheet.get(row_range)
					
					has_data = False
					if row_data and len(row_data) > 0:
						row_values = row_data[0] if row_data[0] else []
						for cell_value in row_values:
							if cell_value is not None:
								cell_str = str(cell_value).strip() if cell_value else ""
								if cell_str != "":
									has_data = True
									break
					
					if has_data:
						last_filled_row = current_row
					
					current_row += 1
				except Exception as e2:
					error_str2 = str(e2)
					if "exceeds grid limits" in error_str2 or "400" in error_str2:
						logger.info(f"Достигнут предел таблицы при чтении строки {current_row}, прекращаем поиск")
						exceeded_limits = True
						break
					logger.warning(f"Ошибка чтения строки {current_row}: {e2}")
					current_row += 1
		
		return last_filled_row
		
	except Exception as e:
		logger.exception(f"Ошибка поиска последней заполненной строки: {e}")
		return None


def _delete_last_row_from_google_sheet_sync(
	sheet_id: str,
	credentials_path: str,
	delete_range: str,
	start_row: int,
	max_row: int,
	start_column: str,
	end_column: str,
	sheet_name: Optional[str] = None
) -> Dict[str, Any]:
	"""
	Синхронная функция для удаления последней строки из Google Sheets.
	Ищет последнюю заполненную строку построчно в указанном диапазоне и удаляет её.
	"""
	try:
		# Создаем клиент
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return {"success": False, "deleted_row": None, "message": "Не удалось создать клиент Google Sheets"}
		
		# Открываем таблицу
		try:
			spreadsheet = client.open_by_key(sheet_id)
			worksheet = _get_worksheet(spreadsheet, sheet_name)
		except PermissionError as e:
			logger.error(f"Ошибка доступа к таблице: {e}")
			raise
		
		logger.info(f"🔍 Поиск последней заполненной строки построчно (диапазон {start_column}:{end_column}), в диапазоне строк {start_row}-{max_row}")
		
		# Ищем последнюю заполненную строку построчно в указанном диапазоне
		last_filled_row = _find_last_filled_row_by_row(worksheet, start_row=start_row, max_row=max_row, start_column=start_column, end_column=end_column)
		
		if not last_filled_row:
			return {"success": False, "deleted_row": None, "message": "Не найдена заполненная строка для удаления"}
		
		if last_filled_row < start_row:
			return {"success": False, "deleted_row": None, "message": f"Нельзя удалить строку {last_filled_row}, она меньше начальной строки {start_row}"}
		
		if last_filled_row > max_row:
			return {"success": False, "deleted_row": None, "message": f"Нельзя удалить строку {last_filled_row}, она превышает максимальную строку {max_row} для текущего дня недели"}
		
		# Формируем диапазон для удаления
		delete_range_full = f"{start_column}{last_filled_row}:{end_column}{last_filled_row}"
		
		# Очищаем диапазон (удаляем значения)
		# Используем batch_clear для очистки диапазона
		try:
			worksheet.batch_clear([delete_range_full])
		except AttributeError:
			# Если batch_clear не поддерживается, используем clear
			worksheet.clear(delete_range_full)
		logger.info(f"✅ Удалена строка {last_filled_row} в диапазоне {delete_range_full}")
		
		return {"success": True, "deleted_row": last_filled_row, "message": f"Удалена строка {last_filled_row}"}
		
	except Exception as e:
		logger.exception(f"Ошибка удаления строки из Google Sheet: {e}")
		return {"success": False, "deleted_row": None, "message": f"Ошибка: {str(e)}"}


async def write_to_google_sheet_rate_mode(
	sheet_id: str,
	credentials_path: str,
	crypto_list: list,  # [{"currency": "BTC", "usd_amount": 100}, ...]
	xmr_list: list,  # [{"xmr_number": 1, "usd_amount": 50}, ...]
	cash_list: list,  # [{"currency": "RUB", "value": 5000}, ...] - для наличных без карты
	card_cash_pairs: list,  # [{"card": {...}, "cash": {...}}, ...] - пары карта-наличные
	sheet_name: Optional[str] = None
) -> Dict[str, Any]:
	"""
	Записывает данные в режиме rate: каждая запись идет в первую пустую ячейку соответствующего столбца,
	начиная со строки 348.
	
	Args:
		sheet_id: ID Google Sheet
		credentials_path: Путь к файлу с учетными данными
		crypto_list: Список криптовалют (BTC, LTC, USDT)
		xmr_list: Список XMR данных
		cash_list: Список наличных
		card_cash_pairs: Список пар карта-наличные
		
	Returns:
		Словарь с результатами: {"success": bool, "written_cells": list}
	"""
	try:
		db = get_db()
		# Используем словарь для хранения столбцов всех криптовалют
		crypto_columns = {}  # {currency: column}
		
		# Получаем адреса столбцов для всех используемых криптовалют
		for crypto in crypto_list:
			currency = crypto.get("currency")
			if currency and currency not in crypto_columns:
				column = await db.get_crypto_column(currency)
				if column:
					crypto_columns[currency] = column
					logger.info(f"✅ Получен столбец для криптовалюты '{currency}': {column}")
				else:
					logger.warning(f"⚠️ Не найден столбец для криптовалюты '{currency}'")
		
		# Получаем адреса столбцов для XMR
		xmr_columns = {}
		for xmr in xmr_list:
			xmr_number = xmr.get("xmr_number")
			if xmr_number not in xmr_columns:
				xmr_columns[xmr_number] = await get_xmr_column(xmr_number)
		
		# Вычисляем адреса столбцов для карт
		card_columns = {}
		for pair in card_cash_pairs:
			card_data = pair.get("card")
			card_name = card_data.get("card_name", "")
			user_name = card_data.get("user_name", "")
			key = f"{card_name}_{user_name}"
			if key not in card_columns:
				card_columns[key] = await get_card_column(card_name, user_name)
				# Добавляем адрес столбца в данные карты
				card_data["column"] = card_columns[key]
		
		# Получаем адреса столбцов для наличных
		cash_columns = {}
		logger.info(f"🔍 Обработка наличных: cash_list={cash_list}")
		for cash in cash_list:
			cash_name = cash.get("cash_name")
			logger.info(f"🔍 Обработка наличных: cash_name={cash_name}, cash={cash}")
			if cash_name and cash_name not in cash_columns:
				cash_column = await db.get_cash_column(cash_name)
				cash_columns[cash_name] = cash_column
				# Добавляем адрес столбца в данные наличных
				cash["column"] = cash_column
				logger.info(f"🔍 Получен адрес столбца для наличных: cash_name={cash_name}, column={cash_column}")
			elif not cash_name:
				logger.warning(f"⚠️ Наличные без названия: cash={cash}")
		
		# Получаем настройки диапазона строк для режима rate из базы данных
		rate_start_row_str = await db.get_google_sheets_setting("rate_start_row", "407")
		rate_max_row_str = await db.get_google_sheets_setting("rate_max_row", "419")
		rate_start_row = int(rate_start_row_str) if rate_start_row_str else 407
		rate_max_row = int(rate_max_row_str) if rate_max_row_str else 419
		
		# Выполняем синхронную запись в отдельном потоке
		result = await asyncio.to_thread(
			_write_to_google_sheet_rate_mode_sync,
			sheet_id,
			credentials_path,
			crypto_list,
			xmr_list,
			cash_list,
			card_cash_pairs,
			crypto_columns,
			xmr_columns,
			rate_max_row,
			rate_start_row,
			sheet_name
		)
		
		# В режиме rate всегда начинаем с rate_start_row (по умолчанию 407), не сохраняем последние использованные строки
		# (убрано сохранение rate_last_row_{column} для каждого столбца)
		
		return result
	except Exception as e:
		logger.exception(f"Ошибка записи данных в режиме rate: {e}")
		return {"success": False, "written_cells": []}


def _write_to_google_sheet_rate_mode_sync(
	sheet_id: str,
	credentials_path: str,
	crypto_list: list,
	xmr_list: list,
	cash_list: list,
	card_cash_pairs: list,
	crypto_columns: Dict[str, Optional[str]],  # {currency: column}
	xmr_columns: Dict[int, Optional[str]],
	rate_max_row: int = 419,
	start_row: int = 407,
	sheet_name: Optional[str] = None
) -> Dict[str, Any]:
	"""
	Синхронная функция для записи данных в режиме rate.
	Каждая запись идет в первую пустую ячейку соответствующего столбца, начиная со строки start_row (по умолчанию 348).
	Если найденная пустая ячейка превышает rate_max_row, запись не выполняется.
	"""
	try:
		# Создаем клиент
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return {"success": False, "written_cells": []}
		
		# Открываем таблицу
		try:
			spreadsheet = client.open_by_key(sheet_id)
			worksheet = _get_worksheet(spreadsheet, sheet_name)
		except PermissionError as e:
			logger.error(f"Ошибка доступа к таблице: {e}")
			raise
		
		written_cells = []
		failed_writes = []  # Список данных, которые не удалось записать из-за лимита
		column_rows = {}  # Словарь {column: row} для обновления последних строк (не используется, но оставлен для совместимости)
		
		# Записываем криптовалюты (все, не только BTC, LTC, USDT)
		for crypto in crypto_list:
			currency = crypto.get("currency")
			usd_amount = crypto.get("usd_amount", 0.0)
			
			if usd_amount != 0:  # Разрешаем как положительные, так и отрицательные значения
				usd_amount_rounded = int(round(usd_amount))
				# В режиме rate записываем со знаком минус (если значение положительное)
				# Если значение уже отрицательное, оставляем как есть
				if usd_amount_rounded > 0:
					usd_amount_negative = -usd_amount_rounded
				else:
					usd_amount_negative = usd_amount_rounded  # Уже отрицательное
				
				# Получаем столбец из словаря
				column = crypto_columns.get(currency)
				
				if column:
					empty_row = _find_empty_cell_in_column(worksheet, column, start_row=start_row, max_row=rate_max_row)
					if empty_row > rate_max_row:
						failed_writes.append(f"{currency}: {usd_amount_rounded} USD (нет места, последняя строка: {rate_max_row})")
						logger.warning(f"⚠️ Не записано {currency}: {usd_amount_rounded} USD - превышен лимит строки {rate_max_row}, найдена строка {empty_row}")
					else:
						worksheet.update(f"{column}{empty_row}", [[usd_amount_negative]])
						written_cells.append(f"{column}{empty_row} ({currency}: {usd_amount_negative} USD)")
						column_rows[column] = empty_row
						logger.info(f"✅ Записано {usd_amount_negative} USD в ячейку {column}{empty_row} ({currency})")
				else:
					failed_writes.append(f"{currency}: {usd_amount_rounded} USD (столбец не найден)")
					logger.warning(f"⚠️ Не записано {currency}: {usd_amount_rounded} USD - не найден столбец для криптовалюты '{currency}'")
		
		# Записываем XMR
		for xmr in xmr_list:
			xmr_number = xmr.get("xmr_number")
			usd_amount = xmr.get("usd_amount", 0.0)
			
			if usd_amount != 0:  # Разрешаем как положительные, так и отрицательные значения
				usd_amount_rounded = int(round(usd_amount))
				# В режиме rate записываем со знаком минус (если значение положительное)
				# Если значение уже отрицательное, оставляем как есть
				if usd_amount_rounded > 0:
					usd_amount_negative = -usd_amount_rounded
				else:
					usd_amount_negative = usd_amount_rounded  # Уже отрицательное
				usd_column = xmr_columns.get(xmr_number)
				
				if usd_column:
					empty_row = _find_empty_cell_in_column(worksheet, usd_column, start_row=start_row, max_row=rate_max_row)
					if empty_row > rate_max_row:
						failed_writes.append(f"XMR-{xmr_number}: {usd_amount_rounded} USD (нет места, последняя строка: {rate_max_row})")
						logger.warning(f"⚠️ Не записано XMR-{xmr_number}: {usd_amount_rounded} USD - превышен лимит строки {rate_max_row}, найдена строка {empty_row}")
					else:
						worksheet.update(f"{usd_column}{empty_row}", [[usd_amount_negative]])
						written_cells.append(f"{usd_column}{empty_row} (XMR-{xmr_number}: {usd_amount_negative} USD)")
						column_rows[usd_column] = empty_row
						logger.info(f"✅ Записано {usd_amount_negative} USD в ячейку {usd_column}{empty_row} (XMR-{xmr_number})")
		
		# Записываем наличные для каждой карты
		for pair in card_cash_pairs:
			card_data = pair.get("card")
			cash_data = pair.get("cash")
			card_name = card_data.get("card_name", "")
			column = card_data.get("column")
			
			if column and cash_data:
				cash_currency = cash_data.get("currency", "RUB")
				cash_amount = cash_data.get("value", 0)
				
				if cash_amount != 0:  # Разрешаем как положительные, так и отрицательные значения
					# В режиме rate записываем со знаком минус (если значение положительное)
					# Если значение уже отрицательное, оставляем как есть
					if cash_amount > 0:
						cash_amount_negative = -cash_amount
					else:
						cash_amount_negative = cash_amount  # Уже отрицательное
					empty_row = _find_empty_cell_in_column(worksheet, column, start_row=start_row, max_row=rate_max_row)
					if empty_row > rate_max_row:
						failed_writes.append(f"Карта {card_name}: {cash_amount} {cash_currency} (нет места, последняя строка: {rate_max_row})")
						logger.warning(f"⚠️ Не записано {cash_amount} {cash_currency} для карты {card_name} - превышен лимит строки {rate_max_row}, найдена строка {empty_row}")
					else:
						worksheet.update(f"{column}{empty_row}", [[cash_amount_negative]])
						written_cells.append(f"{column}{empty_row} (Карта {card_name}: {cash_amount_negative} {cash_currency})")
						column_rows[column] = empty_row
						logger.info(f"✅ Записано {cash_amount_negative} {cash_currency} в ячейку {column}{empty_row} (карта: {card_name})")
		
		# Записываем наличные без карты
		logger.info(f"🔍 Запись наличных без карты: cash_list={cash_list}, len={len(cash_list)}")
		for cash in cash_list:
			cash_name = cash.get("cash_name", "")
			cash_currency = cash.get("currency", "RUB")
			cash_amount = cash.get("value", 0)
			column = cash.get("column")
			
			if column and cash_amount != 0:  # Разрешаем как положительные, так и отрицательные значения
				# В режиме rate записываем со знаком минус (если значение положительное)
				# Если значение уже отрицательное, оставляем как есть
				if cash_amount > 0:
					cash_amount_negative = -cash_amount
				else:
					cash_amount_negative = cash_amount  # Уже отрицательное
				empty_row = _find_empty_cell_in_column(worksheet, column, start_row=start_row, max_row=rate_max_row)
				if empty_row > rate_max_row:
					failed_writes.append(f"Наличные {cash_name}: {cash_amount} {cash_currency} (нет места, последняя строка: {rate_max_row})")
					logger.warning(f"⚠️ Не записано {cash_amount} {cash_currency} для наличных {cash_name} - превышен лимит строки {rate_max_row}, найдена строка {empty_row}")
				else:
					worksheet.update(f"{column}{empty_row}", [[cash_amount_negative]])
					written_cells.append(f"{column}{empty_row} (Наличные {cash_name}: {cash_amount_negative} {cash_currency})")
					column_rows[column] = empty_row
					logger.info(f"✅ Записано {cash_amount_negative} {cash_currency} в ячейку {column}{empty_row} (наличные: {cash_name})")
			elif not column:
				failed_writes.append(f"Наличные {cash_name}: {cash_amount} {cash_currency} (не указан адрес столбца)")
				logger.warning(f"⚠️ Не записано {cash_amount} {cash_currency} для наличных {cash_name} - не указан адрес столбца")
			elif cash_amount == 0:
				logger.warning(f"⚠️ Пропущено наличные {cash_name}: сумма равна 0")
		
		return {
			"success": len(written_cells) > 0 or len(failed_writes) == 0,
			"written_cells": written_cells,
			"failed_writes": failed_writes,
			"column_rows": column_rows
		}
		
	except Exception as e:
		logger.exception(f"Ошибка записи данных в режиме rate: {e}")
		return {"success": False, "written_cells": []}


def _get_crypto_values_from_row_4_sync(
	sheet_id: str,
	credentials_path: str,
	crypto_columns: List[Dict[str, str]],
	sheet_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
	"""
	Синхронная функция для чтения значений криптовалют из строки 4 Google Sheets.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		crypto_columns: Список словарей с ключами crypto_type и column
	
	Returns:
		Словарь {crypto_type: value} с значениями из строки 4
	"""
	result = {}
	
	if not sheet_id or not credentials_path:
		logger.warning("Google Sheets не настроен для чтения криптовалют")
		return result
	
	try:
		# Создаем клиент Google Sheets
		client = _get_google_sheets_client(credentials_path)
		
		if not client:
			logger.warning("Не удалось создать клиент Google Sheets")
			return result
		
		# Открываем таблицу
		spreadsheet = client.open_by_key(sheet_id)
		worksheet = _get_worksheet(spreadsheet, sheet_name)
		
		# Собираем адреса ячеек для batch чтения
		cell_addresses = []
		crypto_mapping = {}  # {cell_address: crypto_type}
		
		for crypto in crypto_columns:
			crypto_type = crypto.get("crypto_type", "")
			column = crypto.get("column", "")
			
			if not column:
				logger.warning(f"Пропущена криптовалюта {crypto_type}: нет столбца")
				continue
			
			cell_address = f"{column}4"
			cell_addresses.append(cell_address)
			crypto_mapping[cell_address] = crypto_type
		
		# Читаем все значения одним batch запросом
		logger.info(f"Начинаем batch чтение значений криптовалют из строки 4. Всего криптовалют: {len(cell_addresses)}")
		
		if cell_addresses:
			try:
				# Используем batch_get для чтения всех ячеек за один запрос
				values = worksheet.batch_get(cell_addresses)
				
				# Обрабатываем результаты
				for i, cell_address in enumerate(cell_addresses):
					crypto_type = crypto_mapping[cell_address]
					
					try:
						# values[i] - это список строк для данной ячейки (обычно одна строка)
						# values[i][0] - первая строка
						# values[i][0][0] - первое значение в строке
						if i < len(values) and values[i] and len(values[i]) > 0:
							row = values[i][0]
							if row and len(row) > 0:
								value = str(row[0]).strip()
								# Если значение пустое после strip, считаем его None
								if not value:
									value = None
								logger.debug(f"Прочитано значение для {crypto_type} из {cell_address}: '{value}'")
							else:
								value = None
								logger.debug(f"Ячейка {cell_address} для {crypto_type} пустая")
						else:
							value = None
							logger.debug(f"Ячейка {cell_address} для {crypto_type} не найдена в ответе")
						
						result[crypto_type] = value
						
					except (IndexError, TypeError) as e:
						logger.warning(f"Ошибка обработки ячейки {cell_address} для {crypto_type}: {e}")
						result[crypto_type] = None
			except Exception as e:
				logger.exception(f"Ошибка batch чтения криптовалют: {e}")
				# В случае ошибки batch чтения, помечаем все как None
				for cell_address, crypto_type in crypto_mapping.items():
					result[crypto_type] = None
		
	except Exception as e:
		logger.exception(f"Ошибка чтения значений криптовалют из строки 4: {e}")
	
	return result


async def get_crypto_values_from_row_4(
	sheet_id: str,
	credentials_path: str,
	crypto_columns: List[Dict[str, str]],
	sheet_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
	"""
	Читает значения криптовалют из строки 4 Google Sheets.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		crypto_columns: Список словарей с ключами crypto_type и column
	
	Returns:
		Словарь {crypto_type: value} с значениями из строки 4
	"""
	loop = asyncio.get_event_loop()
	return await loop.run_in_executor(
		None,
		_get_crypto_values_from_row_4_sync,
		sheet_id,
		credentials_path,
		crypto_columns,
		sheet_name
	)


def _read_card_balance_sync(
	sheet_id: str,
	credentials_path: str,
	column: str,
	balance_row: int = 4,
	sheet_name: Optional[str] = None
) -> Optional[str]:
	"""
	Синхронная функция для чтения баланса карты из указанной строки.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		column: Столбец карты (например, "D")
		balance_row: Номер строки с балансом (по умолчанию 4)
	
	Returns:
		Значение баланса или None
	"""
	cell_address = f"{column}{balance_row}"  # Определяем сразу, чтобы использовать в except
	try:
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return None
		
		spreadsheet = client.open_by_key(sheet_id)
		worksheet = _get_worksheet(spreadsheet, sheet_name)
		
		logger.info(f"🔍 Чтение баланса карты из ячейки {cell_address}")
		
		cell = worksheet.acell(cell_address)
		if cell and cell.value:
			value = str(cell.value).strip()
			logger.info(f"✅ Прочитан баланс из {cell_address}: '{value}'")
			return value
		else:
			logger.info(f"⚠️ Ячейка {cell_address} пустая или не найдена")
			return None
	except Exception as e:
		logger.exception(f"❌ Ошибка чтения баланса из {cell_address}: {e}")
		return None


async def read_card_balance(
	sheet_id: str,
	credentials_path: str,
	column: str,
	balance_row: int = 4,
	sheet_name: Optional[str] = None
) -> Optional[str]:
	"""
	Читает баланс карты из указанной строки.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		column: Столбец карты (например, "D")
		balance_row: Номер строки с балансом (по умолчанию 4)
	
	Returns:
		Значение баланса или None
	"""
	loop = asyncio.get_event_loop()
	return await loop.run_in_executor(
		None,
		_read_card_balance_sync,
		sheet_id,
		credentials_path,
		column,
		balance_row,
		sheet_name
	)


def _read_card_balances_batch_sync(
	sheet_id: str,
	credentials_path: str,
	cell_addresses: List[str],
	sheet_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
	"""
	Синхронная функция для чтения балансов нескольких карт за один запрос.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		cell_addresses: Список адресов ячеек (например, ["D4", "E4", "F4"])
	
	Returns:
		Словарь {адрес_ячейки: значение} или None при ошибке
	"""
	try:
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return {}
		
		spreadsheet = client.open_by_key(sheet_id)
		worksheet = _get_worksheet(spreadsheet, sheet_name)
		
		logger.info(f"🔍 Batch чтение балансов из {len(cell_addresses)} ячеек")
		
		# Используем batch_get для чтения нескольких ячеек за один запрос
		# batch_get возвращает список списков: [[['value1']], [['value2']], ...]
		values = worksheet.batch_get(cell_addresses)
		
		result = {}
		for i, cell_address in enumerate(cell_addresses):
			try:
				# values[i] - это список строк для данной ячейки (обычно одна строка)
				# values[i][0] - первая строка
				# values[i][0][0] - первое значение в строке
				if i < len(values) and values[i] and len(values[i]) > 0:
					row = values[i][0]
					if row and len(row) > 0:
						value = str(row[0]).strip()
						result[cell_address] = value
						logger.debug(f"✅ Прочитан баланс из {cell_address}: '{value}'")
					else:
						result[cell_address] = None
						logger.debug(f"⚠️ Ячейка {cell_address} пустая")
				else:
					result[cell_address] = None
					logger.debug(f"⚠️ Ячейка {cell_address} не найдена в ответе")
			except (IndexError, TypeError) as e:
				logger.warning(f"⚠️ Ошибка обработки ячейки {cell_address}: {e}")
				result[cell_address] = None
		
		logger.info(f"✅ Batch чтение завершено: прочитано {len([v for v in result.values() if v])} значений из {len(cell_addresses)} ячеек")
		return result
	except Exception as e:
		logger.exception(f"❌ Ошибка batch чтения балансов: {e}")
		return {}


async def read_card_balances_batch(
	sheet_id: str,
	credentials_path: str,
	cell_addresses: List[str],
	sheet_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
	"""
	Читает балансы нескольких карт за один запрос.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		cell_addresses: Список адресов ячеек (например, ["D4", "E4", "F4"])
	
	Returns:
		Словарь {адрес_ячейки: значение}
	"""
	loop = asyncio.get_event_loop()
	return await loop.run_in_executor(
		None,
		_read_card_balances_batch_sync,
		sheet_id,
		credentials_path,
		cell_addresses,
		sheet_name
	)


def _read_profit_sync(
	sheet_id: str,
	credentials_path: str,
	row: int,
	profit_column: str = "BC",
	sheet_name: Optional[str] = None
) -> Optional[str]:
	"""
	Синхронная функция для чтения профита из указанного столбца.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		row: Номер строки, куда записали данные
		profit_column: Столбец с профитом (по умолчанию "BC")
	
	Returns:
		Значение профита или None
	"""
	try:
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return None
		
		spreadsheet = client.open_by_key(sheet_id)
		worksheet = _get_worksheet(spreadsheet, sheet_name)
		
		cell_address = f"{profit_column}{row}"
		logger.info(f"🔍 Чтение профита из ячейки {cell_address}")
		
		cell = worksheet.acell(cell_address)
		if cell and cell.value:
			value = str(cell.value).strip()
			logger.info(f"✅ Прочитан профит из {cell_address}: '{value}'")
			return value
		else:
			logger.info(f"⚠️ Ячейка {cell_address} пустая или не найдена")
			return None
	except Exception as e:
		logger.exception(f"❌ Ошибка чтения профита из {cell_address}: {e}")
		return None


async def read_profit(
	sheet_id: str,
	credentials_path: str,
	row: int,
	profit_column: str = "BC",
	sheet_name: Optional[str] = None
) -> Optional[str]:
	"""
	Читает профит из указанного столбца.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		row: Номер строки, куда записали данные
		profit_column: Столбец с профитом (по умолчанию "BC")
	
	Returns:
		Значение профита или None
	"""
	loop = asyncio.get_event_loop()
	return await loop.run_in_executor(
		None,
		_read_profit_sync,
		sheet_id,
		credentials_path,
		row,
		profit_column,
		sheet_name
	)


def _read_profits_batch_sync(
	sheet_id: str,
	credentials_path: str,
	cell_addresses: List[str],
	sheet_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
	"""
	Синхронная функция для чтения профитов из нескольких ячеек за один запрос.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		cell_addresses: Список адресов ячеек (например, ["BC123", "BC124", "BC125"])
	
	Returns:
		Словарь {адрес_ячейки: значение}
	"""
	try:
		client = _get_google_sheets_client(credentials_path)
		if not client:
			logger.error("Не удалось создать клиент Google Sheets")
			return {}
		
		spreadsheet = client.open_by_key(sheet_id)
		worksheet = _get_worksheet(spreadsheet, sheet_name)
		
		logger.info(f"🔍 Batch чтение профитов из {len(cell_addresses)} ячеек")
		
		# Используем batch_get для чтения нескольких ячеек за один запрос
		values = worksheet.batch_get(cell_addresses)
		
		result = {}
		for i, cell_address in enumerate(cell_addresses):
			try:
				# values[i] - это список строк для данной ячейки (обычно одна строка)
				# values[i][0] - первая строка
				# values[i][0][0] - первое значение в строке
				if i < len(values) and values[i] and len(values[i]) > 0:
					row = values[i][0]
					if row and len(row) > 0:
						value = str(row[0]).strip()
						result[cell_address] = value
						logger.debug(f"✅ Прочитан профит из {cell_address}: '{value}'")
					else:
						result[cell_address] = None
						logger.debug(f"⚠️ Ячейка {cell_address} пустая")
				else:
					result[cell_address] = None
					logger.debug(f"⚠️ Ячейка {cell_address} не найдена в ответе")
			except (IndexError, TypeError) as e:
				logger.warning(f"⚠️ Ошибка обработки ячейки {cell_address}: {e}")
				result[cell_address] = None
		
		logger.info(f"✅ Batch чтение профитов завершено: прочитано {len([v for v in result.values() if v])} значений из {len(cell_addresses)} ячеек")
		return result
	except Exception as e:
		logger.exception(f"❌ Ошибка batch чтения профитов: {e}")
		return {}


async def read_profits_batch(
	sheet_id: str,
	credentials_path: str,
	cell_addresses: List[str],
	sheet_name: Optional[str] = None
) -> Dict[str, Optional[str]]:
	"""
	Читает профиты из нескольких ячеек за один запрос.
	
	Args:
		sheet_id: ID Google Sheets таблицы
		credentials_path: Путь к файлу с учетными данными
		cell_addresses: Список адресов ячеек (например, ["BC123", "BC124", "BC125"])
	
	Returns:
		Словарь {адрес_ячейки: значение}
	"""
	loop = asyncio.get_event_loop()
	return await loop.run_in_executor(
		None,
		_read_profits_batch_sync,
		sheet_id,
		credentials_path,
		cell_addresses,
		sheet_name
	)