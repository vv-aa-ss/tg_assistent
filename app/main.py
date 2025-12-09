import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram import F

from app.config import get_settings
from app.db import Database
from app.admin import admin_router, is_admin
from app.keyboards import admin_menu_kb
from app.di import set_dependencies


async def main() -> None:
	os.makedirs("logs", exist_ok=True)
	logging.basicConfig(
		level=logging.DEBUG,  # Увеличиваем уровень для детального логирования
		format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
		handlers=[logging.FileHandler("logs/bot.log", encoding="utf-8")],
	)
	logger = logging.getLogger("app.start")

	settings = get_settings()
	logger.debug(f"Loaded settings: db={settings.database_path}, admins={settings.admin_ids}")
	if not settings.telegram_bot_token:
		raise RuntimeError("TELEGRAM_BOT_TOKEN не задан. Создайте .env с токеном.")

	db = Database(settings.database_path)
	await db.connect()
	set_dependencies(db, settings.admin_ids, settings.admin_usernames)
	logger.debug("Database connected and dependencies set")

	bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
	dp = Dispatcher(storage=MemoryStorage())
	
	# Определяем команды для админов
	from aiogram.types import BotCommand, BotCommandScopeDefault
	admin_commands = [
		BotCommand(command="add", description="Операция"),
		BotCommand(command="rate", description="Расход"),
		BotCommand(command="del_rate", description="Удалить расход"),
		BotCommand(command="move", description="Пердвижение средств"),
		BotCommand(command="del", description="Удалить последнюю операцию"),
		BotCommand(command="del_move", description="Удалить последнее передвижение"),
		BotCommand(command="stat_bk", description="Балансы карт"),
		BotCommand(command="stat_k", description="Баланс крипты"),
		BotCommand(command="stat_u", description="Статистика пользователей"),
		BotCommand(command="start", description="Меню"),
	]
	
	# Скрываем команды для всех пользователей по умолчанию
	try:
		await bot.set_my_commands(commands=[], scope=BotCommandScopeDefault())
		logger.info("✅ Команды скрыты для обычных пользователей")
	except Exception as e:
		logger.warning(f"⚠️ Не удалось скрыть команды для обычных пользователей: {e}")
	
	# Middleware для логирования всех сообщений
	class LoggingMiddleware:
		async def __call__(self, handler, event, data):
			if isinstance(event, Message):
				has_voice = bool(getattr(event, "voice", None))
				logger.info(f"🟢 DISPATCHER: Получено сообщение message_id={event.message_id}, text='{event.text}', has_voice={has_voice}, user_id={event.from_user.id if event.from_user else None}")
			return await handler(event, data)
	
	dp.message.middleware(LoggingMiddleware())

	@dp.message(CommandStart())
	async def on_start(message: Message, state):
		logger.debug(f"/start from user_id={getattr(message.from_user,'id',None)} username={getattr(message.from_user,'username',None)}")
		if message.from_user and is_admin(
			message.from_user.id,
			message.from_user.username,
			settings.admin_ids,
			settings.admin_usernames
		):
			# Очищаем состояние FSM при вызове /start
			current_state = await state.get_state()
			if current_state:
				logger.debug(f"🧹 Очистка состояния при /start. Текущее состояние: {current_state}")
				await state.clear()
				logger.debug(f"✅ Состояние очищено при /start")
			
			# Устанавливаем команды для админа после начала диалога
			# Для личных чатов это работает только после того, как пользователь начал диалог
			try:
				from aiogram.types import BotCommandScopeChat
				await bot.set_my_commands(
					commands=admin_commands,
					scope=BotCommandScopeChat(chat_id=message.from_user.id)
				)
				logger.info(f"✅ Команды установлены для админа {message.from_user.id} после /start")
			except Exception as e:
				logger.warning(f"⚠️ Не удалось установить команды для админа {message.from_user.id}: {e}")
			
			await message.answer("Добро пожаловать, администратор!", reply_markup=admin_menu_kb())
		# non-admins: ignore (no reply)

	# ВАЖНО: Сначала включаем admin_router, чтобы команды из него обрабатывались первыми
	dp.include_router(admin_router)

	# Регистрировать пользователя только когда нет активного состояния и сообщение не переслано
	# Исключаем команды - они обрабатываются отдельными обработчиками
	# Исключаем голосовые сообщения - они обрабатываются отдельным обработчиком
	# ВАЖНО: Фильтр ~F.text.startswith("/") исключает команды на уровне декоратора
	@dp.message(
		~(F.forward_origin.as_(bool) | F.forward_from.as_(bool)),
		StateFilter(None),
		~F.voice,  # Исключаем голосовые сообщения
		~(F.text.startswith("/") if F.text else False)
	)
	async def register_user_on_any_message(message: Message):
		logger.info(f"🟡 MAIN register_user_on_any_message: message_id={message.message_id}, text='{message.text}', user_id={message.from_user.id if message.from_user else None}")
		
		from app.di import get_db
		logger_msg = logging.getLogger("app.msg")
		db_local = get_db()
		if message.from_user:
			logger_msg.debug(f"Ensure user: id={message.from_user.id} username={message.from_user.username} full_name={message.from_user.full_name}")
			await db_local.get_or_create_user(
				message.from_user.id,
				message.from_user.username,
				message.from_user.full_name,
			)
			await db_local.touch_user_by_tg(message.from_user.id)
		# не отвечаем
	logger.debug("Starting polling...")
	try:
		await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
	finally:
		logger.debug("Shutting down, closing DB")
		await db.close()


if __name__ == "__main__":
	asyncio.run(main())
