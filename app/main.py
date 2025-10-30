import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart, StateFilter
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
	logging.basicConfig(
		level=logging.DEBUG,
		format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
	)
	logger = logging.getLogger("app.start")

	settings = get_settings()
	logger.debug(f"Loaded settings: db={settings.database_path}, admins={settings.admin_ids}")
	if not settings.telegram_bot_token:
		raise RuntimeError("TELEGRAM_BOT_TOKEN не задан. Создайте .env с токеном.")

	db = Database(settings.database_path)
	await db.connect()
	set_dependencies(db, settings.admin_ids)
	logger.debug("Database connected and dependencies set")

	bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
	dp = Dispatcher(storage=MemoryStorage())

	@dp.message(CommandStart())
	async def on_start(message: Message):
		logger.debug(f"/start from user_id={getattr(message.from_user,'id',None)} username={getattr(message.from_user,'username',None)}")
		if message.from_user and is_admin(message.from_user.id, settings.admin_ids):
			await message.answer("Добро пожаловать, администратор!", reply_markup=admin_menu_kb())
		else:
			await message.answer("Бот активен. Команды доступны администратору.")

	# Регистрировать пользователя только когда нет активного состояния и сообщение не переслано
	@dp.message(~(F.forward_origin.as_(bool) | F.forward_from.as_(bool)), StateFilter(None))
	async def register_user_on_any_message(message: Message):
		from app.di import get_db
		logger_msg = logging.getLogger("app.msg")
		db_local = get_db()
		if message.from_user:
			logger_msg.debug(f"Register/ensure user: {message.from_user.id} @{message.from_user.username}")
			await db_local.get_or_create_user(
				message.from_user.id,
				message.from_user.username,
				message.from_user.full_name,
			)
		# не отвечаем

	dp.include_router(admin_router)
	logger.debug("Starting polling...")
	try:
		await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
	finally:
		logger.debug("Shutting down, closing DB")
		await db.close()


if __name__ == "__main__":
	asyncio.run(main())
