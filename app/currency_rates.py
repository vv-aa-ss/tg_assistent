"""
Модуль для автоматического получения курсов валют из интернета
"""
import asyncio
import logging
import re
from typing import Optional, Tuple
from datetime import datetime, timedelta

try:
	import aiohttp
	from bs4 import BeautifulSoup
except ImportError:
	aiohttp = None
	BeautifulSoup = None

from app.http_session import get_session

logger = logging.getLogger("app.currency_rates")

# Счетчики неудачных попыток
_failure_count_byn = 0
_failure_count_rub = 0
_last_success_byn = None
_last_success_rub = None
_last_alert_sent_byn = None
_last_alert_sent_rub = None

# Кэш курсов (обновляется каждые 30 минут)
_rate_cache_byn: Optional[float] = None
_rate_cache_rub: Optional[float] = None
_cache_timestamp_byn: Optional[datetime] = None
_cache_timestamp_rub: Optional[datetime] = None
CACHE_DURATION = timedelta(minutes=30)


async def get_usd_to_byn_rate(bot=None) -> Optional[float]:
	"""
	Получает курс USD→BYN с сайта myfin.by
	
	Returns:
		Курс валюты или None в случае ошибки
	"""
	global _rate_cache_byn, _cache_timestamp_byn, _failure_count_byn, _last_alert_sent_byn
	
	# Проверяем кэш
	if _rate_cache_byn and _cache_timestamp_byn:
		if datetime.now() - _cache_timestamp_byn < CACHE_DURATION:
			return _rate_cache_byn
	
	if not aiohttp or not BeautifulSoup:
		logger.error("⚠️ Библиотеки aiohttp и BeautifulSoup не установлены. Установите: pip install aiohttp beautifulsoup4")
		return None
	
	url = "https://myfin.by/currency/minsk?utm_source=myfin&utm_medium=organic&utm_campaign=menu&working=0"
	
	try:
		session = get_session()
		async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
				if response.status != 200:
					logger.warning(f"⚠️ Не удалось получить курс BYN: HTTP {response.status}")
					_failure_count_byn += 1
					await _check_and_alert_byn(bot)
					return None
				
				html = await response.text()
				soup = BeautifulSoup(html, 'html.parser')
				
				# Ищем элемент по XPath: //*[@id='bank-row-62']/td[3]/span
				# Пытаемся найти элемент с id='bank-row-62'
				bank_row = soup.find(id='bank-row-62')
				if not bank_row:
					logger.warning("⚠️ Не найден элемент bank-row-62 на странице myfin.by")
					_failure_count_byn += 1
					await _check_and_alert_byn()
					return None
				
				# Ищем третью ячейку (td[3]) и span внутри
				td_cells = bank_row.find_all('td')
				if len(td_cells) < 3:
					logger.warning("⚠️ Не найдено достаточно ячеек в bank-row-62")
					_failure_count_byn += 1
					await _check_and_alert_byn()
					return None
				
				span = td_cells[2].find('span')
				if not span:
					logger.warning("⚠️ Не найден span с курсом в bank-row-62")
					_failure_count_byn += 1
					await _check_and_alert_byn()
					return None
				
				# Извлекаем текст и парсим число
				rate_text = span.get_text(strip=True)
				# Заменяем запятую на точку и извлекаем число
				rate_text = rate_text.replace(',', '.')
				# Ищем число с точкой
				match = re.search(r'(\d+\.?\d*)', rate_text)
				if not match:
					logger.warning(f"⚠️ Не удалось распарсить курс из текста: {rate_text}")
					_failure_count_byn += 1
					await _check_and_alert_byn()
					return None
				
				rate = float(match.group(1))
				
				# Проверяем разумность значения (курс BYN обычно 2-4)
				if rate < 1 or rate > 10:
					logger.warning(f"⚠️ Получен неразумный курс BYN: {rate}")
					_failure_count_byn += 1
					await _check_and_alert_byn()
					return None
				
				# Успешно получили курс
				_rate_cache_byn = rate
				_cache_timestamp_byn = datetime.now()
				_failure_count_byn = 0
				_last_success_byn = datetime.now()
				logger.info(f"✅ Курс USD→BYN успешно получен: {rate}")
				
				return rate
				
	except asyncio.TimeoutError:
		logger.warning("⚠️ Таймаут при получении курса BYN")
		_failure_count_byn += 1
		await _check_and_alert_byn(bot)
		return None
	except Exception as e:
		logger.error(f"❌ Ошибка при получении курса BYN: {e}", exc_info=True)
		_failure_count_byn += 1
		await _check_and_alert_byn(bot)
		return None


async def get_usd_to_rub_rate(bot=None) -> Optional[float]:
	"""
	Получает курс USD→RUB с сайта myfin.by (курсы в России)
	
	Returns:
		Курс валюты или None в случае ошибки
	"""
	global _rate_cache_rub, _cache_timestamp_rub, _failure_count_rub, _last_alert_sent_rub
	
	# Проверяем кэш
	if _rate_cache_rub and _cache_timestamp_rub:
		if datetime.now() - _cache_timestamp_rub < CACHE_DURATION:
			return _rate_cache_rub
	
	if not aiohttp or not BeautifulSoup:
		logger.error("⚠️ Библиотеки aiohttp и BeautifulSoup не установлены. Установите: pip install aiohttp beautifulsoup4")
		return None
	
	# Используем myfin.by страницу курса USD/RUB в России
	url = "https://myfin.by/currency/usdrub/ross"
	
	try:
		headers = {
			'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
		}
		session = get_session()
		async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
				if response.status != 200:
					logger.warning(f"⚠️ Не удалось получить курс RUB: HTTP {response.status}")
					_failure_count_rub += 1
					await _check_and_alert_rub(bot)
					return None
				
				html = await response.text()
				soup = BeautifulSoup(html, 'html.parser')
				
				# Ищем курс на странице USD/RUB
				# Метод 1: Ищем элемент с классом rate или курсом
				rate = None
				
				# Пробуем найти через data-атрибуты или классы с курсом
				# Ищем span/div с числом похожим на курс (60-120 для USD/RUB)
				for tag in soup.find_all(['span', 'div', 'td']):
					text = tag.get_text(strip=True)
					# Ищем число с точкой или запятой (формат курса)
					text_clean = text.replace(',', '.').replace(' ', '')
					match = re.search(r'^(\d{2,3}(?:\.\d{1,4})?)$', text_clean)
					if match:
						try:
							value = float(match.group(1))
							# Курс USD/RUB обычно в диапазоне 60-120
							if 60 <= value <= 120:
								# Проверяем, что это не просто случайное число
								# Ищем контекст - рядом должны быть слова про курс/доллар/рубль
								parent = tag.parent
								if parent:
									parent_text = parent.get_text().lower()
									if any(word in parent_text for word in ['курс', 'usd', 'рубл', 'доллар', 'продаж', 'покупк', 'цб', 'банк']):
										rate = value
										break
								# Если первое найденное значение в диапазоне, используем его
								if rate is None:
									rate = value
						except ValueError:
							continue
				
				# Метод 2: Ищем через более специфичные селекторы
				if rate is None:
					# Ищем таблицу с курсами банков
					tables = soup.find_all('table')
					for table in tables:
						rows = table.find_all('tr')
						for row in rows:
							cells = row.find_all(['td', 'th'])
							for cell in cells:
								text = cell.get_text(strip=True).replace(',', '.').replace(' ', '')
								match = re.search(r'(\d{2}\.\d{2,4})', text)
								if match:
									try:
										value = float(match.group(1))
										if 60 <= value <= 120:
											rate = value
											break
									except ValueError:
										continue
							if rate:
								break
						if rate:
							break
				
				if rate:
					_rate_cache_rub = rate
					_cache_timestamp_rub = datetime.now()
					_failure_count_rub = 0
					_last_success_rub = datetime.now()
					logger.info(f"✅ Курс USD→RUB успешно получен: {rate}")
					return rate
				
				logger.warning("⚠️ Не удалось найти курс RUB на странице myfin.by/currency/usdrub/ross")
				_failure_count_rub += 1
				await _check_and_alert_rub(bot)
				return None
				
	except asyncio.TimeoutError:
		logger.warning("⚠️ Таймаут при получении курса RUB")
		_failure_count_rub += 1
		await _check_and_alert_rub(bot)
		return None
	except Exception as e:
		logger.error(f"❌ Ошибка при получении курса RUB: {e}", exc_info=True)
		_failure_count_rub += 1
		await _check_and_alert_rub(bot)
		return None


async def _check_and_alert_byn(bot=None):
	"""Проверяет количество неудачных попыток и отправляет алерт админу"""
	global _failure_count_byn, _last_alert_sent_byn
	
	if _failure_count_byn >= 3:
		# Проверяем, не отправляли ли уже алерт недавно (чтобы не спамить)
		if _last_alert_sent_byn is None or datetime.now() - _last_alert_sent_byn > timedelta(hours=1):
			await _send_alert_to_admins("USD→BYN", _failure_count_byn, bot)
			_last_alert_sent_byn = datetime.now()


async def _check_and_alert_rub(bot=None):
	"""Проверяет количество неудачных попыток и отправляет алерт админу"""
	global _failure_count_rub, _last_alert_sent_rub
	
	if _failure_count_rub >= 3:
		# Проверяем, не отправляли ли уже алерт недавно (чтобы не спамить)
		if _last_alert_sent_rub is None or datetime.now() - _last_alert_sent_rub > timedelta(hours=1):
			await _send_alert_to_admins("USD→RUB", _failure_count_rub, bot)
			_last_alert_sent_rub = datetime.now()


async def _send_alert_to_admins(currency_pair: str, failure_count: int, bot=None):
	"""Отправляет алерт админам о проблемах с получением курса"""
	try:
		from app.di import get_admin_ids
		from aiogram.types import ParseMode
		
		admin_ids = get_admin_ids()
		message_text = (
			f"🚨 <b>Проблема с получением курса валют</b>\n\n"
			f"💱 Пара: {currency_pair}\n"
			f"❌ Неудачных попыток подряд: {failure_count}\n"
			f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
			f"⚠️ Курс не обновляется автоматически. Проверьте настройки или обновите вручную."
		)
		
		logger.error(f"🚨 АЛЕРТ: {message_text}")
		
		# Отправляем сообщение админам, если bot доступен
		if bot and admin_ids:
			for admin_id in admin_ids:
				try:
					await bot.send_message(
						chat_id=admin_id,
						text=message_text,
						parse_mode=ParseMode.HTML
					)
					logger.info(f"✅ Алерт отправлен админу {admin_id}")
				except Exception as e:
					logger.error(f"❌ Ошибка при отправке алерта админу {admin_id}: {e}")
		
	except Exception as e:
		logger.error(f"❌ Ошибка при отправке алерта админам: {e}", exc_info=True)


async def get_rate_with_fallback(currency: str, db, bot=None) -> float:
	"""
	Получает курс валюты с автоматическим обновлением или использует значение из БД
	
	Args:
		currency: 'BYN' или 'RUB'
		db: Экземпляр базы данных
		bot: Экземпляр бота для отправки алертов (опционально)
	
	Returns:
		Курс валюты
	"""
	if currency == "BYN":
		rate = await get_usd_to_byn_rate(bot)
		setting_key = "buy_usd_to_byn_rate"
		default_rate = 2.97
	elif currency == "RUB":
		rate = await get_usd_to_rub_rate(bot)
		setting_key = "buy_usd_to_rub_rate"
		default_rate = 95.0
	else:
		logger.warning(f"⚠️ Неизвестная валюта: {currency}")
		return default_rate
	
	# Если не удалось получить курс из интернета, используем значение из БД
	if rate is None:
		logger.warning(f"⚠️ Не удалось получить курс {currency} из интернета, используем значение из БД")
		rate_str = await db.get_setting(setting_key, str(default_rate))
		try:
			rate = float(rate_str) if rate_str else default_rate
		except (ValueError, TypeError):
			rate = default_rate
	else:
		# Обновляем значение в БД
		try:
			await db.set_setting(setting_key, str(rate))
			logger.info(f"✅ Курс {currency} обновлен в БД: {rate}")
		except Exception as e:
			logger.error(f"❌ Ошибка при обновлении курса в БД: {e}")
	
	return rate
