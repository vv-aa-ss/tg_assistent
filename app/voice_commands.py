"""
Модуль для обработки голосовых команд бота.
Обеспечивает распознавание речи и парсинг команд из голосовых сообщений.

Зависимости:
- speech_recognition: для распознавания речи через Google Speech Recognition API
- pydub: для конвертации аудио форматов (опционально, но рекомендуется)
- ffmpeg: системная утилита для работы с аудио (требуется для pydub)

Поддерживаемые команды:
- "статистика" или "статистика пользователей" -> /stat_u
- "карты", "баланс", "баланс карты" -> /stat_bk
- "крипта", "баланс крипта" -> /stat_k
- "операция", "добавить операцию", "новая операция" -> /add (с парсингом данных)
- "операция", "добавить операцию", "новая операция" -> /add (с парсингом данных)
"""
import logging
import os
import sys
import tempfile
import re
from typing import Optional, Dict, List, Any, Tuple
from aiogram import Bot
from aiogram.types import Message, Voice

logger = logging.getLogger(__name__)


def _setup_ffmpeg_path():
	"""
	Настраивает путь к ffmpeg.exe для pydub.
	Ищет ffmpeg в папке проекта (ffmpeg/bin/ffmpeg.exe).
	Поддерживает работу как в обычном режиме, так и в EXE (PyInstaller).
	"""
	try:
		# Проверяем, запущена ли программа как EXE (PyInstaller)
		if getattr(sys, 'frozen', False):
			# Если это EXE, используем путь к папке, где находится EXE файл
			# sys.executable содержит путь к EXE файлу
			exe_dir = os.path.dirname(sys.executable)
			ffmpeg_path = os.path.join(exe_dir, "ffmpeg", "bin", "ffmpeg.exe")
			
			logger.debug(f"🔍 EXE режим: ищем ffmpeg в {ffmpeg_path}")
			
			# Если не найдено рядом с EXE, пробуем в папке _MEIPASS (временная папка PyInstaller)
			if not os.path.exists(ffmpeg_path):
				meipass = getattr(sys, '_MEIPASS', None)
				if meipass:
					ffmpeg_path_meipass = os.path.join(meipass, "ffmpeg", "bin", "ffmpeg.exe")
					logger.debug(f"🔍 EXE режим: пробуем _MEIPASS: {ffmpeg_path_meipass}")
					if os.path.exists(ffmpeg_path_meipass):
						ffmpeg_path = ffmpeg_path_meipass
		else:
			# Обычный режим: получаем путь к корню проекта (на уровень выше app/)
			project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
			ffmpeg_path = os.path.join(project_root, "ffmpeg", "bin", "ffmpeg.exe")
			logger.debug(f"🔍 Обычный режим: ищем ffmpeg в {ffmpeg_path}")
		
		if os.path.exists(ffmpeg_path):
			# Устанавливаем путь для pydub
			# pydub использует переменные окружения или атрибуты класса AudioSegment
			import pydub
			
			# Устанавливаем пути к ffmpeg и ffprobe
			ffmpeg_dir = os.path.dirname(ffmpeg_path)
			ffprobe_path = os.path.join(ffmpeg_dir, "ffprobe.exe")
			
			# Устанавливаем через атрибуты класса AudioSegment
			pydub.AudioSegment.converter = ffmpeg_path
			pydub.AudioSegment.ffmpeg = ffmpeg_path
			pydub.AudioSegment.ffprobe = ffprobe_path if os.path.exists(ffprobe_path) else ffmpeg_path
			
			# Также добавляем директорию ffmpeg в PATH для поиска зависимостей
			current_path = os.environ.get("PATH", "")
			if ffmpeg_dir not in current_path:
				os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path
			
			logger.info(f"✅ Настроен путь к ffmpeg: {ffmpeg_path}")
			return True
		else:
			logger.warning(f"⚠️ FFmpeg не найден по пути: {ffmpeg_path}")
			return False
	except Exception as e:
		logger.warning(f"⚠️ Не удалось настроить путь к ffmpeg: {e}")
		return False


async def download_voice_file(bot: Bot, voice: Voice) -> Optional[str]:
	"""
	Скачивает голосовой файл с серверов Telegram во временный файл.
	
	Args:
		bot: Экземпляр бота
		voice: Объект Voice из сообщения
		
	Returns:
		Путь к временному файлу или None в случае ошибки
	"""
	try:
		file = await bot.get_file(voice.file_id)
		file_path = file.file_path
		
		# Создаем временный файл
		temp_dir = tempfile.gettempdir()
		temp_file = os.path.join(temp_dir, f"voice_{voice.file_id}.ogg")
		
		# Скачиваем файл
		await bot.download_file(file_path, destination=temp_file)
		logger.info(f"✅ Голосовой файл скачан: {temp_file}")
		
		return temp_file
	except Exception as e:
		logger.exception(f"❌ Ошибка скачивания голосового файла: {e}")
		return None


def correct_recognition_errors(text: str) -> str:
	"""
	Исправляет известные ошибки распознавания речи.
	
	Использует словарь исправлений для замены неправильно распознанных слов
	на правильные варианты. Словарь можно расширять по мере обнаружения ошибок.
	
	Args:
		text: Распознанный текст
		
	Returns:
		Исправленный текст
	"""
	# Словарь исправлений: неправильное распознавание -> правильное
	# Формат: {неправильный_вариант: правильный_вариант}
	corrections = {
		# Частые ошибки распознавания имен и названий
		"коваль": "кавал",
		"ковал": "кавал",  # Исправляем "ковал" на "кавал" (правильное название группы)
		"канал": "кавал",
		"коваля": "кавал",
		"ковалю": "кавал",
		"kaval": "кавал",
		"ковалем": "кавал",
		"ковале": "кавал",
		"белвест": "белвэб",
		"белвеб": "белвэб",
		"Bell веб": "белвэб",
		"бел веб": "белвэб",
		"бел вест": "белвэб",
		# "неправильный_вариант": "правильный_вариант",
	}
	
	# Применяем исправления (с учетом границ слов, чтобы не заменять части других слов)
	corrected_text = text
	for wrong, correct in corrections.items():
		# Используем регулярное выражение для замены с учетом границ слов
		# Это гарантирует, что "коваль" заменится, но "ковальский" не затронется
		pattern = r'\b' + re.escape(wrong) + r'\b'
		before = corrected_text
		corrected_text = re.sub(pattern, correct, corrected_text, flags=re.IGNORECASE)
		if before != corrected_text:
			logger.debug(f"🔧 Исправление применено: '{wrong}' -> '{correct}' в тексте '{before}' -> '{corrected_text}'")
	
	if corrected_text != text:
		logger.debug(f"🔧 Итоговое исправление распознавания: '{text}' -> '{corrected_text}'")
	
	return corrected_text


async def transcribe_voice(bot: Bot, voice: Voice) -> Optional[str]:
	"""
	Распознает речь из голосового сообщения и возвращает текст.
	
	Args:
		bot: Экземпляр бота
		voice: Объект Voice из сообщения
		
	Returns:
		Распознанный текст или None в случае ошибки
	"""
	voice_file = None
	wav_file = None
	
	try:
		# Скачиваем файл
		voice_file = await download_voice_file(bot, voice)
		if not voice_file:
			return None
		
		# Используем speech_recognition для распознавания
		import speech_recognition as sr
		
		# Создаем распознаватель с увеличенным таймаутом (30 секунд вместо 5 по умолчанию)
		recognizer = sr.Recognizer()
		recognizer.operation_timeout = 30  # Увеличиваем таймаут до 30 секунд
		
		# Конвертируем OGG в WAV для распознавания
		# Используем pydub для конвертации, если доступен
		try:
			from pydub import AudioSegment
			
			# Настраиваем путь к ffmpeg перед использованием
			_setup_ffmpeg_path()
			
			# Загружаем OGG файл
			audio = AudioSegment.from_ogg(voice_file)
			
			# Конвертируем в WAV
			wav_file = voice_file.replace(".ogg", ".wav")
			audio.export(wav_file, format="wav")
			
			# Используем WAV файл для распознавания
			with sr.AudioFile(wav_file) as source:
				# Настраиваем распознаватель для шумной записи
				recognizer.adjust_for_ambient_noise(source, duration=0.5)
				audio_data = recognizer.record(source)
			
		except ImportError:
			logger.warning("⚠️ pydub не установлен, попытка прямого распознавания OGG")
			# Если pydub не установлен, пробуем напрямую с OGG
			# Многие распознаватели не поддерживают OGG, поэтому используем Google API
			try:
				with sr.AudioFile(voice_file) as source:
					recognizer.adjust_for_ambient_noise(source, duration=0.5)
					audio_data = recognizer.record(source)
			except Exception as e:
				logger.error(f"❌ Не удалось открыть OGG файл напрямую: {e}")
				return None
		except Exception as e:
			logger.error(f"❌ Ошибка конвертации аудио: {e}")
			# Пробуем без конвертации
			try:
				with sr.AudioFile(voice_file) as source:
					recognizer.adjust_for_ambient_noise(source, duration=0.5)
					audio_data = recognizer.record(source)
			except Exception as e2:
				logger.error(f"❌ Не удалось открыть файл: {e2}")
				return None
		
		# Распознаем речь (используем Google Speech Recognition)
		# Для русского языка указываем язык
		# Увеличиваем таймаут для запросов (по умолчанию 5 секунд, увеличиваем до 30)
		max_retries = 3
		for attempt in range(max_retries):
			try:
				text = recognizer.recognize_google(audio_data, language="ru-RU", show_all=False)
				# Исправляем известные ошибки распознавания
				text = correct_recognition_errors(text)
				logger.info(f"✅ Распознан текст: {text}")
				return text.lower().strip()
			except sr.UnknownValueError:
				logger.warning("⚠️ Не удалось распознать речь (неизвестное значение)")
				return None
			except (sr.RequestError, TimeoutError, OSError) as e:
				if attempt < max_retries - 1:
					logger.warning(f"⚠️ Ошибка при распознавании речи (попытка {attempt + 1}/{max_retries}): {e}. Повторяю...")
					import time
					time.sleep(1)  # Небольшая задержка перед повторной попыткой
					continue
				else:
					logger.error(f"❌ Ошибка сервиса распознавания речи после {max_retries} попыток: {e}")
					return None
		
	except Exception as e:
		logger.exception(f"❌ Ошибка распознавания речи: {e}")
		return None
	finally:
		# Удаляем временные файлы
		for file_path in [voice_file, wav_file]:
			if file_path and os.path.exists(file_path):
				try:
					os.remove(file_path)
				except Exception as e:
					logger.warning(f"⚠️ Не удалось удалить временный файл {file_path}: {e}")


def parse_voice_command(text: str) -> Optional[str]:
	"""
	Парсит текст и определяет, какую команду нужно выполнить.
	
	Args:
		text: Распознанный текст из голосового сообщения
		
	Returns:
		Название команды (stat_u, stat_k, stat_bk) или None
	"""
	if not text:
		return None
	
	text = text.lower().strip()
	
	# Команда /add: "операция", "добавить операцию", "новая операция" (проверяем ПЕРВОЙ, так как она более специфична)
	# Если в тексте есть "операция", это точно команда /add, даже если есть слова "карта", "крипта" и т.д.
	add_keywords = ["операция", "добавить операцию", "новая операция", "add operation"]
	if any(keyword in text for keyword in add_keywords):
		return "add"
	
	# Также проверяем, есть ли в тексте крипта/карта/наличные с числами - это может быть операция
	# Ищем паттерны: крипта + число, карта + число, наличные + число
	has_crypto_with_number = any(kw in text for kw in ["биток", "бтц", "bitcoin", "btc", "лайткоин", "litecoin", "ltc", "лтк", "ltk", "монеро", "monero", "xmr", "тезер", "tez"]) and bool(re.search(r'\d+', text))
	has_card_with_number = any(kw in text for kw in ["карта", "card"]) and bool(re.search(r'\d+', text))
	has_cash_with_number = any(kw in text for kw in ["наличные", "нал", "cash", "белки", "баксы", "юсд", "usd"]) and bool(re.search(r'\d+', text))
	
	# Если есть хотя бы два типа данных с числами, это скорее всего операция
	if (has_crypto_with_number and (has_card_with_number or has_cash_with_number)) or (has_card_with_number and has_cash_with_number):
		return "add"
	
	# Команда /stat_k: "крипта", "баланс крипта", "crypto" (проверяем вторым, так как более специфично)
	crypto_keywords = [
		"баланс крипта", "баланс крипты", "баланс криптовалют",
		"крипта", "крипты", "криптовалют", "криптовалюта",
		"crypto", "cryptocurrency", "баланс crypto", "баланс cryptocurrency"
	]
	if any(keyword in text for keyword in crypto_keywords):
		return "stat_k"
	
	# Команда /stat_bk: "карты", "баланс карты", "баланс карт"
	# Проверяем, что это не про операцию (если есть "операция", уже вернули "add" выше)
	card_keywords = ["баланс карты", "баланс карт", "карты", "карта", "cards", "баланс cards"]
	if any(keyword in text for keyword in card_keywords):
		# Проверяем, что это не про крипту (на русском или английском)
		if "крипт" not in text and "crypto" not in text:
			return "stat_bk"
	
	# Команда /stat_u: "статистика" или "статистика пользователей"
	stats_keywords = ["статистика пользователей", "статистика", "statistics", "статистика users"]
	if any(keyword in text for keyword in stats_keywords):
		# Проверяем, что это не про карты или крипту (на русском или английском)
		if "карт" not in text and "крипт" not in text and "crypto" not in text and "card" not in text:
			return "stat_u"
	
	return None


async def handle_voice_command(message: Message, bot: Bot) -> Optional[str]:
	"""
	Обрабатывает голосовое сообщение и возвращает команду для выполнения.
	
	Args:
		message: Сообщение с голосовым сообщением
		bot: Экземпляр бота
		
	Returns:
		Название команды для выполнения или None
	"""
	if not message.voice:
		return None
	
	logger.info(f"🎤 Получено голосовое сообщение от пользователя {message.from_user.id if message.from_user else None}")
	
	# Распознаем речь
	text = await transcribe_voice(bot, message.voice)
	if not text:
		return None
	
	# Парсим команду
	command = parse_voice_command(text)
	
	if command:
		logger.info(f"✅ Определена команда из голосового сообщения: {command}")
	else:
		logger.warning(f"⚠️ Не удалось определить команду из текста: {text}")
	
	return command


def parse_add_operation_data(text: str) -> Dict[str, Any]:
	"""
	Парсит данные операции из голосового ввода.
	
	Пример: "Операция, биток 100, жанна белвеб 300, наличные белки 300, наличные юсд 200"
	
	Args:
		text: Распознанный текст из голосового сообщения
		
	Returns:
		Словарь с распарсенными данными:
		{
			"blocks": [
				{
					"crypto": {"currency": "BTC", "amount": 100},
					"card": {"group": "ЖАННА", "name": "БЕЛВЕБ", "amount": 300},
					"cash": {"name": "белки", "amount": 300}
				},
				{
					"cash": {"name": "юсд", "amount": 200}
				}
			]
		}
	"""
	result = {
		"blocks": []
	}
	
	if not text:
		return result
	
	# Убираем команду "операция" в начале
	text = text.lower().strip()
	for keyword in ["операция", "добавить операцию", "новая операция", "add operation"]:
		if text.startswith(keyword):
			text = text[len(keyword):].strip()
			# Убираем запятую после команды
			if text.startswith(","):
				text = text[1:].strip()
			break
	
	# Разбиваем на части по запятым или по ключевым словам (если запятых нет)
	parts = [p.strip() for p in text.split(",") if p.strip()]
	
	# Если запятых нет, разбиваем по ключевым словам "наличные" и "карта"
	if len(parts) == 1 and "," not in text:
		# Ключевые слова, которые указывают на начало нового элемента
		separator_keywords = ["наличные", "нал", "cash", "карта", "карты", "card", "cards"]
		
		# Находим позиции всех ключевых слов
		keyword_positions = []
		for keyword in separator_keywords:
			# Используем границы слов для точного поиска
			pattern = r'\b' + re.escape(keyword) + r'\b'
			for match in re.finditer(pattern, text, re.IGNORECASE):
				keyword_positions.append((match.start(), match.end(), keyword))
		
		# Сортируем по позиции
		keyword_positions.sort(key=lambda x: x[0])
		
		if len(keyword_positions) > 0:
			parts = []
			start_idx = 0
			
			for i, (kw_start, kw_end, keyword) in enumerate(keyword_positions):
				# Часть до ключевого слова (если есть)
				if start_idx < kw_start:
					part = text[start_idx:kw_start].strip()
					if part:
						parts.append(part)
				
				# Часть с ключевым словом до следующего ключевого слова или конца текста
				next_start = keyword_positions[i+1][0] if i+1 < len(keyword_positions) else len(text)
				part = text[kw_start:next_start].strip()
				if part:
					parts.append(part)
				
				start_idx = next_start
			
			# Если остался текст после последнего ключевого слова
			if start_idx < len(text):
				remaining = text[start_idx:].strip()
				if remaining:
					parts.append(remaining)
		else:
			# Если ключевых слов нет, разбиваем по числам (старая логика)
			numbers = list(re.finditer(r'\d+(?:\.\d+)?', text))
			if len(numbers) > 1:
				parts = []
				# Первая часть - от начала до первого числа включительно
				first_match = numbers[0]
				parts.append(text[:first_match.end()].strip())
				
				# Остальные части - от конца предыдущего числа до конца текущего числа
				for i in range(1, len(numbers)):
					prev_match = numbers[i-1]
					curr_match = numbers[i]
					parts.append(text[prev_match.end():curr_match.end()].strip())
				
				# Последняя часть - от конца последнего числа до конца текста (если есть текст после числа)
				last_match = numbers[-1]
				if last_match.end() < len(text):
					parts.append(text[last_match.end():].strip())
			elif len(numbers) == 1:
				# Если только одно число, оставляем как есть
				parts = [text]
	
	logger.debug(f"🔍 Распарсенные части: {parts}")
	
	# Определяем названия наличных для использования в разных местах
	cash_names = {
		"белки": ["белки", "белка", "squirrel"],
		"юсд": ["юсд", "usd", "баксы", "бакс", "доллар", "доллары", "dollar", "dollars"],
		"руб": ["руб", "рубль", "рубли", "rub", "ruble"]
	}
	
	current_block = {}
	previous_part = None  # Сохраняем предыдущую часть для проверки наличных
	
	for part_idx, part in enumerate(parts):
		part = part.strip()
		if not part:
			continue
		
		logger.debug(f"🔍 Обработка части {part_idx + 1}/{len(parts)}: '{part}'")
		
		# Ищем числа в конце
		numbers = re.findall(r'\d+(?:\.\d+)?', part)
		# Преобразуем в целое число (округление, если есть десятичная часть)
		amount = int(float(numbers[-1])) if numbers else None
		logger.debug(f"🔍 Часть '{part}': найдено чисел={len(numbers)}, amount={amount}")
		
		# Если в части нет числа, проверяем специальные случаи
		if amount is None:
			# Проверяем, содержит ли часть слово "карта" - это может быть карта без суммы
			# (сумма может быть в следующей части или отсутствовать)
			has_card_keyword = any(kw in part.lower() for kw in ["карта", "карты", "card", "cards"])
			
			if has_card_keyword:
				# Если есть слово "карта", ищем число в следующей части
				# Если следующая часть начинается с "наличные", число относится к наличным, не к карте
				if part_idx + 1 < len(parts):
					next_part = parts[part_idx + 1].strip()
					next_has_cash = any(kw in next_part.lower() for kw in ["наличные", "нал", "cash"])
					if not next_has_cash:
						# Ищем число в следующей части
						next_numbers = re.findall(r'\d+(?:\.\d+)?', next_part)
						if next_numbers:
							amount = int(float(next_numbers[0]))
							logger.debug(f"🔍 Найдено слово 'карта' без числа, используем число из следующей части: {amount}")
						else:
							amount = 0
							logger.debug(f"🔍 Найдено слово 'карта' без числа, используем amount=0")
					else:
						# Следующая часть - наличные, число относится к ним, не к карте
						amount = 0
						logger.debug(f"🔍 Найдено слово 'карта' без числа, следующая часть - наличные, используем amount=0")
				else:
					# Нет следующей части
					amount = 0
					logger.debug(f"🔍 Найдено слово 'карта' без числа, нет следующей части, используем amount=0")
			else:
				# Проверяем, содержит ли часть только название наличных
				cash_found_in_part = None
				for cash_name, keywords in cash_names.items():
					if any(kw in part.lower() for kw in keywords):
						cash_found_in_part = cash_name
						break
				
				# Если это название наличных, проверяем предыдущую часть
				if cash_found_in_part and previous_part:
					prev_numbers = re.findall(r'\d+(?:\.\d+)?', previous_part)
					prev_amount = int(float(prev_numbers[-1])) if prev_numbers else None
					prev_text = re.sub(r'\d+(?:\.\d+)?', '', previous_part).strip().lower()
					
					# Проверяем, есть ли в предыдущей части ключевое слово "наличные" и число
					cash_keywords = ["наличные", "нал", "cash"]
					if prev_amount and any(kw in prev_text for kw in cash_keywords):
						# Используем число из предыдущей части
						amount = prev_amount
						# Объединяем части для original_text
						original_part_name = part
						part = f"{previous_part} {part}".strip()
						logger.debug(f"🔍 Объединены части для наличных: '{previous_part}' + '{original_part_name}' = '{part}', сумма {amount}")
						# Не обновляем previous_part здесь, так как объединенная часть будет обработана дальше
					else:
						# Пропускаем эту часть
						previous_part = part
						continue
				else:
					# Пропускаем эту часть (нет ни карты, ни наличных)
					previous_part = part
					continue
		
		# Сохраняем текущую часть как предыдущую для следующей итерации
		# (только если часть была обработана, не пропущена)
		previous_part = part
		
		# Убираем число из текста для анализа
		text_part = re.sub(r'\d+(?:\.\d+)?', '', part).strip()
		logger.debug(f"🔍 Обработка части '{part}', amount={amount}, text_part='{text_part}'")
		
		# Проверяем на криптовалюту
		# Важно: проверяем более длинные фразы первыми (например, "тезер траст" перед "тезер")
		crypto_keywords = {
			"tez_trust": ["тезер траст", "тезертраст", "tez trust", "teztrust"],
			"btc": ["биток", "бтц", "биткоин", "bitcoin", "btc"],
			"ltc": ["лайткоин", "litecoin", "ltc", "лтк", "ltk"],
			"xmr": ["монеро", "monero", "xmr"],
			"usdt": ["юсдт", "usdt", "tether"],
			"tez": ["тезер"]
		}
		
		crypto_found = None
		crypto_text_used = None
		for crypto_code, keywords in crypto_keywords.items():
			for kw in keywords:
				if kw in text_part:
					crypto_found = crypto_code.upper()
					crypto_text_used = kw
					break
			if crypto_found:
				break
		
		# Если найдена крипта, сохраняем её, но продолжаем проверку на карту и наличные
		if crypto_found:
			# Если в текущем блоке уже есть крипта, сохраняем текущий блок и создаем новый
			if "crypto" in current_block and current_block["crypto"]:
				# Сохраняем текущий блок
				if current_block:
					# Если есть несколько наличных, обрабатываем их отдельно
					if "cash" in current_block and isinstance(current_block["cash"], list) and len(current_block["cash"]) > 1:
						# Первый блок с первыми наличными
						first_cash = current_block["cash"][0]
						first_block = {k: v for k, v in current_block.items() if k != "cash"}
						first_block["cash"] = first_cash
						result["blocks"].append(first_block)
						
						# Остальные блоки только с наличными
						for cash_item in current_block["cash"][1:]:
							result["blocks"].append({"cash": cash_item})
					else:
						# Если один элемент наличных, преобразуем список в словарь
						if "cash" in current_block and isinstance(current_block["cash"], list) and len(current_block["cash"]) == 1:
							current_block["cash"] = current_block["cash"][0]
						result["blocks"].append(current_block)
				
				# Создаем новый блок для новой крипты
				current_block = {}
			
			current_block["crypto"] = {
				"currency": crypto_found,
				"amount": amount,
				"original_text": part  # Сохраняем оригинальный текст для обучения
			}
			# Убираем найденное слово крипты из текста для дальнейшего анализа
			if crypto_text_used:
				text_part = text_part.replace(crypto_text_used, "").strip()
				logger.debug(f"🔍 После удаления крипты '{crypto_text_used}' осталось: '{text_part}'")
		
		# Проверяем на наличные (сначала проверяем с ключевыми словами, потом без)
		cash_keywords = ["наличные", "нал", "cash"]
		has_cash_keyword = any(kw in text_part for kw in cash_keywords)
		
		# Ищем название наличных (cash_names уже определен выше)
		cash_found = None
		for cash_name, keywords in cash_names.items():
			if any(kw in text_part for kw in keywords):
				cash_found = cash_name
				break
		
		# Если найдены наличные (с ключевым словом или без), обрабатываем
		# Но продолжаем проверку карты, так как в одной части может быть и карта, и наличные
		if cash_found:
			if has_cash_keyword:
				# Убираем ключевые слова наличных
				for kw in cash_keywords:
					text_part = text_part.replace(kw, "").strip()
			
			# Убираем найденное название наличных из текста для дальнейшего анализа
			for cash_name, keywords in cash_names.items():
				if cash_name == cash_found:
					for kw in keywords:
						if kw in text_part:
							text_part = text_part.replace(kw, "").strip()
							break
			
			if "cash" not in current_block:
				current_block["cash"] = []
			current_block["cash"].append({
				"name": cash_found,
				"amount": amount,
				"original_text": part  # Сохраняем оригинальный текст для обучения
			})
			logger.debug(f"🔍 После удаления наличных '{cash_found}' осталось: '{text_part}'")
		
		# Проверяем на карту (формат: "группа название_карты сумма" или "карта группа название_карты сумма")
		# Проверяем карту даже если уже найдена крипта (в одной части может быть и крипта, и карта)
		words = text_part.split()
		logger.debug(f"🔍 Проверка карты: text_part='{text_part}', words={words}")
		
		# Убираем слово "карта" из начала, если оно есть (это указание типа, а не название группы)
		if words and words[0].lower() in ["карта", "карты", "card", "cards"]:
			words = words[1:]
			logger.debug(f"🔍 После удаления 'карта' из начала: words={words}")
		
		# Проверяем, есть ли достаточно слов для карты (минимум 2 слова: группа + название)
		if len(words) >= 2:
			# Предполагаем, что первое слово - группа, остальные - название карты
			group_name = words[0].upper()
			card_name = " ".join(words[1:]).upper()
			
			logger.debug(f"🔍 Парсинг карты: группа='{group_name}', название='{card_name}', часть='{part}', text_part='{text_part}'")
			
			# Если в текущем блоке уже есть карта, сохраняем текущий блок и создаем новый
			if "card" in current_block and current_block["card"]:
				# Сохраняем текущий блок
				if current_block:
					# Если есть несколько наличных, обрабатываем их отдельно
					if "cash" in current_block and isinstance(current_block["cash"], list) and len(current_block["cash"]) > 1:
						# Первый блок с первыми наличными
						first_cash = current_block["cash"][0]
						first_block = {k: v for k, v in current_block.items() if k != "cash"}
						first_block["cash"] = first_cash
						result["blocks"].append(first_block)
						
						# Остальные блоки только с наличными
						for cash_item in current_block["cash"][1:]:
							result["blocks"].append({"cash": cash_item})
					else:
						# Если один элемент наличных, преобразуем список в словарь
						if "cash" in current_block and isinstance(current_block["cash"], list) and len(current_block["cash"]) == 1:
							current_block["cash"] = current_block["cash"][0]
						result["blocks"].append(current_block)
				
				# Создаем новый блок для новой карты
				current_block = {}
			
			# Сохраняем карту
			current_block["card"] = {
				"group": group_name,
				"name": card_name,
				"amount": amount,
				"original_text": part  # Сохраняем оригинальный текст для обучения
			}
			
			# Также сохраняем original_text для наличных
			if "cash" in current_block and isinstance(current_block["cash"], list):
				for cash_item in current_block["cash"]:
					if "original_text" not in cash_item:
						cash_item["original_text"] = part
	
	# Если есть данные в текущем блоке, добавляем его
	if current_block:
		# Если есть несколько наличных, создаем отдельные блоки
		if "cash" in current_block and isinstance(current_block["cash"], list) and len(current_block["cash"]) > 1:
			# Первый блок с первыми наличными
			first_cash = current_block["cash"][0]
			first_block = {k: v for k, v in current_block.items() if k != "cash"}
			first_block["cash"] = first_cash
			result["blocks"].append(first_block)
			
			# Остальные блоки только с наличными
			for cash_item in current_block["cash"][1:]:
				result["blocks"].append({"cash": cash_item})
		else:
			# Если один элемент наличных, преобразуем список в словарь
			if "cash" in current_block and isinstance(current_block["cash"], list) and len(current_block["cash"]) == 1:
				current_block["cash"] = current_block["cash"][0]
			result["blocks"].append(current_block)
	
	return result


async def find_crypto_by_name(crypto_name: str, db, original_text: Optional[str] = None) -> Optional[str]:
	"""
	Находит криптовалюту по названию.
	Сначала проверяет сохраненные соответствия из базы данных.
	
	Args:
		crypto_name: Название криптовалюты (BTC, биткоин, биток и т.д.)
		db: Экземпляр базы данных
		original_text: Оригинальный текст из голосового сообщения (для поиска соответствий)
		
	Returns:
		Код криптовалюты (BTC, LTC, XMR, USDT, TEZ, TEZ_TRUST) или None
	"""
	logger.debug(f"🔍 Поиск криптовалюты: название='{crypto_name}', оригинал='{original_text}'")
	
	# Сначала проверяем сохраненные соответствия
	if original_text:
		mapping = await db.get_voice_mapping("crypto", original_text.lower())
		if not mapping:
			# Если не найдено, пробуем нормализованный текст (без чисел)
			normalized_text = re.sub(r'\d+(?:\.\d+)?', '', original_text.lower()).strip()
			if normalized_text:
				mapping = await db.get_voice_mapping("crypto", normalized_text)
		
		if mapping and mapping.get("target_name"):
			crypto_type = mapping["target_name"]
			# Проверяем, что такая крипта есть в базе
			crypto_columns = await db.list_crypto_columns()
			for crypto in crypto_columns:
				if crypto["crypto_type"].upper() == crypto_type.upper():
					logger.debug(f"✅ Криптовалюта найдена по сохраненному соответствию: {crypto_type}")
					return crypto["crypto_type"]
	
	crypto_columns = await db.list_crypto_columns()
	
	# Маппинг названий на коды
	# Важно: проверяем более длинные фразы первыми (например, "тезер траст" перед "тезер")
	crypto_map = {
		"tez_trust": ["тезер траст", "тезертраст", "tez trust", "teztrust"],
		"btc": ["биток", "бтц", "биткоин", "bitcoin", "btc"],
		"ltc": ["лайткоин", "litecoin", "ltc", "лтк", "ltk"],
		"xmr": ["монеро", "monero", "xmr"],
		"usdt": ["юсдт", "usdt", "tether"],
		"tez": ["тезер"]
	}
	
	crypto_name_lower = crypto_name.lower()
	
	# Проверяем маппинг
	for code, keywords in crypto_map.items():
		if any(kw in crypto_name_lower for kw in keywords):
			# Проверяем, что такая крипта есть в базе
			for crypto in crypto_columns:
				if crypto["crypto_type"].upper() == code.upper():
					logger.debug(f"✅ Криптовалюта найдена по маппингу: {crypto['crypto_type']}")
					return crypto["crypto_type"]
	
	logger.warning(f"⚠️ Криптовалюта не найдена: название='{crypto_name}'")
	return None


async def find_card_by_group_and_name(group_name: str, card_name: str, db, original_text: Optional[str] = None) -> Optional[Dict[str, Any]]:
	"""
	Находит карту по названию группы и названию карты.
	Сначала проверяет сохраненные соответствия из базы данных.
	
	Args:
		group_name: Название группы карт
		card_name: Название карты
		db: Экземпляр базы данных
		original_text: Оригинальный текст из голосового сообщения (для поиска соответствий)
		
	Returns:
		Словарь с информацией о карте или None
	"""
	logger.debug(f"🔍 Поиск карты: группа='{group_name}', название='{card_name}', оригинал='{original_text}'")
	
	# Сначала проверяем сохраненные соответствия
	if original_text:
		# Пробуем найти по точному тексту
		mapping = await db.get_voice_mapping("card", original_text.lower())
		if not mapping:
			# Если не найдено, пробуем нормализованный текст (без слова "карта" и без чисел)
			normalized_text = original_text.lower()
			# Убираем слово "карта" в начале
			for word in ["карта", "карты", "card", "cards"]:
				if normalized_text.startswith(word + " "):
					normalized_text = normalized_text[len(word):].strip()
			# Убираем числа
			normalized_text = re.sub(r'\d+(?:\.\d+)?', '', normalized_text).strip()
			if normalized_text:
				mapping = await db.get_voice_mapping("card", normalized_text)
		
		if mapping and mapping.get("target_id"):
			card = await db.get_card_by_id(mapping["target_id"])
			if card:
				logger.debug(f"✅ Карта найдена по сохраненному соответствию: {card['name']}")
				return {
					"card_id": card["card_id"],
					"card_name": card["name"],
					"group_id": card.get("group_id")
				}
	
	# Получаем все группы
	groups = await db.list_card_groups()
	logger.debug(f"🔍 Доступные группы карт: {[g['name'] for g in groups]}")
	
	# Ищем группу по названию (без учета регистра, включая частичное совпадение)
	group_id = None
	group_name_upper = group_name.upper()
	
	# Функция для нормализации названия группы (убираем пробелы, дефисы, приводим к единому виду)
	def normalize_group_name(name: str) -> str:
		normalized = name.replace(" ", "").replace("-", "").replace("_", "").upper()
		return normalized
	
	group_name_normalized = normalize_group_name(group_name_upper)
	
	for group in groups:
		group_db_name_upper = group["name"].upper()
		group_db_name_normalized = normalize_group_name(group_db_name_upper)
		
		# Точное совпадение
		if group_db_name_upper == group_name_upper:
			group_id = group["id"]
			logger.debug(f"✅ Группа найдена (точное совпадение): '{group['name']}' (id={group_id})")
			break
		
		# Совпадение нормализованных названий
		if group_name_normalized == group_db_name_normalized:
			group_id = group["id"]
			logger.debug(f"✅ Группа найдена (нормализованное совпадение): '{group['name']}' (id={group_id})")
			break
		
		# Частичное совпадение (одно название содержит другое)
		if group_name_upper in group_db_name_upper or group_db_name_upper in group_name_upper:
			group_id = group["id"]
			logger.debug(f"✅ Группа найдена (частичное совпадение): '{group['name']}' (id={group_id})")
			break
		
		# Частичное совпадение нормализованных названий
		if group_name_normalized in group_db_name_normalized or group_db_name_normalized in group_name_normalized:
			group_id = group["id"]
			logger.debug(f"✅ Группа найдена (частичное нормализованное совпадение): '{group['name']}' (id={group_id})")
			break
	
	# Если группа не найдена, пробуем найти карту без группы
	if group_id is None:
		cards = await db.get_cards_without_group()
	else:
		cards = await db.get_cards_by_group(group_id)
	
	# Ищем карту по названию (без учета регистра, включая частичное совпадение)
	card_name_upper = card_name.upper()
	logger.debug(f"🔍 Ищем карту среди {len(cards)} карт в группе {group_id}")
	
	# Функция для нормализации названия (убираем скобки, пробелы, специальные символы)
	def normalize_card_name(name: str) -> str:
		# Убираем скобки и их содержимое, пробелы, дефисы
		normalized = re.sub(r'\([^)]*\)', '', name)  # Убираем скобки и содержимое
		normalized = normalized.replace(" ", "").replace("-", "").replace("_", "")
		return normalized
	
	card_name_normalized = normalize_card_name(card_name_upper)
	
	for card_id, card_name_db, _ in cards:
		card_name_db_upper = card_name_db.upper()
		card_name_db_normalized = normalize_card_name(card_name_db_upper)
		logger.debug(f"🔍 Сравниваем: '{card_name_upper}' (норм: '{card_name_normalized}') с '{card_name_db_upper}' (норм: '{card_name_db_normalized}')")
		
		# Точное совпадение
		if card_name_db_upper == card_name_upper:
			card = await db.get_card_by_id(card_id)
			if card:
				logger.debug(f"✅ Карта найдена (точное совпадение): {card_name_db}")
				return {
					"card_id": card_id,
					"card_name": card_name_db,
					"group_id": group_id
				}
		
		# Совпадение нормализованных названий (без скобок, пробелов, специальных символов)
		if card_name_normalized == card_name_db_normalized:
			card = await db.get_card_by_id(card_id)
			if card:
				logger.debug(f"✅ Карта найдена (нормализованное совпадение): {card_name_db}")
				return {
					"card_id": card_id,
					"card_name": card_name_db,
					"group_id": group_id
				}
		
		# Частичное совпадение нормализованных названий
		if card_name_normalized in card_name_db_normalized or card_name_db_normalized in card_name_normalized:
			# Проверяем, что совпадение достаточно значимое (минимум 3 символа)
			common_chars = set(card_name_normalized) & set(card_name_db_normalized)
			if len(common_chars) >= 3:
				card = await db.get_card_by_id(card_id)
				if card:
					logger.debug(f"✅ Карта найдена (частичное нормализованное совпадение): {card_name_db}")
					return {
						"card_id": card_id,
						"card_name": card_name_db,
						"group_id": group_id
					}
		
		# Проверяем обратное вхождение нормализованных названий
		if len(card_name_normalized) >= 3 and len(card_name_db_normalized) >= 3:
			if card_name_normalized in card_name_db_normalized or card_name_db_normalized in card_name_normalized:
				# Проверяем, что общая часть достаточно значимая
				min_len = min(len(card_name_normalized), len(card_name_db_normalized))
				if min_len >= 3:
					card = await db.get_card_by_id(card_id)
					if card:
						logger.debug(f"✅ Карта найдена (обратное вхождение нормализованных): {card_name_db}")
						return {
							"card_id": card_id,
							"card_name": card_name_db,
							"group_id": group_id
						}
	
	logger.warning(f"⚠️ Карта не найдена: группа='{group_name}', название='{card_name}'")
	return None


async def find_cash_by_name(cash_name: str, db, original_text: Optional[str] = None) -> Optional[Dict[str, Any]]:
	"""
	Находит наличные по названию.
	Сначала проверяет сохраненные соответствия из базы данных.
	
	Args:
		cash_name: Название наличных (белки, юсд, баксы и т.д.)
		db: Экземпляр базы данных
		original_text: Оригинальный текст из голосового сообщения (для поиска соответствий)
		
	Returns:
		Словарь с информацией о наличных или None
	"""
	logger.debug(f"🔍 Поиск наличных: название='{cash_name}', оригинал='{original_text}'")
	
	# Сначала проверяем сохраненные соответствия
	if original_text:
		# Пробуем найти по точному тексту
		mapping = await db.get_voice_mapping("cash", original_text.lower())
		if not mapping:
			# Если не найдено, пробуем нормализованный текст (без ключевых слов "наличные", "нал", "cash" и без чисел)
			normalized_text = original_text.lower()
			# Убираем ключевые слова
			for word in ["наличные", "нал", "cash"]:
				normalized_text = normalized_text.replace(word, "").strip()
			# Убираем числа
			normalized_text = re.sub(r'\d+(?:\.\d+)?', '', normalized_text).strip()
			if normalized_text:
				mapping = await db.get_voice_mapping("cash", normalized_text)
		
		if mapping and mapping.get("target_name"):
			cash_columns = await db.list_cash_columns()
			for cash in cash_columns:
				if cash["cash_name"] == mapping["target_name"]:
					logger.debug(f"✅ Наличные найдены по сохраненному соответствию: {cash['cash_name']}")
					return cash
	
	cash_columns = await db.list_cash_columns()
	
	# Маппинг названий
	cash_map = {
		"белки": ["белки", "белка", "squirrel"],
		"юсд": ["юсд", "usd", "баксы", "бакс", "доллар", "доллары", "dollar", "dollars"],
		"руб": ["руб", "рубль", "рубли", "rub", "ruble"]
	}
	
	cash_name_lower = cash_name.lower()
	
	# Определяем тип наличных
	cash_type = None
	for cash_key, keywords in cash_map.items():
		if any(kw in cash_name_lower for kw in keywords):
			cash_type = cash_key
			break
	
	if not cash_type:
		return None
	
	# Ищем в базе по display_name или cash_name
	for cash in cash_columns:
		display_name = cash.get("display_name", "") or ""
		cash_name_db = cash.get("cash_name", "") or ""
		
		# Используем display_name, если оно есть, иначе cash_name
		search_text = (display_name if display_name else cash_name_db).lower()
		cash_name_db_lower = cash_name_db.lower()
		
		logger.debug(f"🔍 Сравниваем наличные: тип='{cash_type}', display='{display_name}', name='{cash_name_db}', search='{search_text}'")
		
		# Проверяем по display_name или cash_name (например, "🐿" для белок)
		if cash_type == "белки":
			if "🐿" in display_name or "🐿" in cash_name_db or "бел" in search_text:
				logger.debug(f"✅ Наличные найдены (белки): {cash.get('cash_name')}")
				return cash
		elif cash_type == "юсд":
			if "💵" in display_name or "💵" in cash_name_db or "usd" in search_text or "долл" in search_text:
				logger.debug(f"✅ Наличные найдены (USD): {cash.get('cash_name')}")
				return cash
		elif cash_type == "руб":
			if "руб" in search_text or "rub" in search_text:
				logger.debug(f"✅ Наличные найдены (RUB): {cash.get('cash_name')}")
				return cash
		
		# Проверяем по cash_name (если cash_name содержит тип)
		if cash_type in cash_name_db_lower:
			logger.debug(f"✅ Наличные найдены (по имени): {cash.get('cash_name')}")
			return cash
	
	logger.warning(f"⚠️ Наличные не найдены: название='{cash_name}', тип='{cash_type}'")
	return None

