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
"""
import logging
import os
import tempfile
from typing import Optional
from aiogram import Bot
from aiogram.types import Message, Voice

logger = logging.getLogger(__name__)


def _setup_ffmpeg_path():
	"""
	Настраивает путь к ffmpeg.exe для pydub.
	Ищет ffmpeg в папке проекта (ffmpeg/bin/ffmpeg.exe).
	"""
	try:
		# Получаем путь к корню проекта (на уровень выше app/)
		project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
		ffmpeg_path = os.path.join(project_root, "ffmpeg", "bin", "ffmpeg.exe")
		
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
		
		recognizer = sr.Recognizer()
		
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
		try:
			text = recognizer.recognize_google(audio_data, language="ru-RU")
			logger.info(f"✅ Распознан текст: {text}")
			return text.lower().strip()
		except sr.UnknownValueError:
			logger.warning("⚠️ Не удалось распознать речь (неизвестное значение)")
			return None
		except sr.RequestError as e:
			logger.error(f"❌ Ошибка сервиса распознавания речи: {e}")
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
	
	# Команда /stat_k: "крипта", "баланс крипта", "crypto" (проверяем первым, так как более специфично)
	crypto_keywords = [
		"баланс крипта", "баланс крипты", "баланс криптовалют",
		"крипта", "крипты", "криптовалют", "криптовалюта",
		"crypto", "cryptocurrency", "баланс crypto", "баланс cryptocurrency"
	]
	if any(keyword in text for keyword in crypto_keywords):
		return "stat_k"
	
	# Команда /stat_bk: "карты", "баланс карты", "баланс карт"
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

