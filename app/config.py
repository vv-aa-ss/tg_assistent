import os
from typing import List
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv(override=True)


class Settings(BaseModel):
	telegram_bot_token: str
	admin_ids: List[int]
	admin_usernames: List[str] = []
	database_path: str = "./data/bot.db"
	google_sheet_id: str = ""
	google_credentials_path: str = ""
	google_sheet_name: str = ""  # Название листа в таблице (если пусто, используется первый лист)
	log_level: str = "INFO"  # DEBUG/INFO/WARNING/ERROR
	
	# Rate limiting параметры
	rate_limit_messages_max: int = 10  # Максимум сообщений
	rate_limit_messages_period: int = 60  # Период в секундах
	rate_limit_spam_max: int = 3  # Максимум сообщений для защиты от быстрого спама
	rate_limit_spam_period: int = 10  # Период в секундах для защиты от спама
	rate_limit_callbacks_max: int = 20  # Максимум callback запросов
	rate_limit_callbacks_period: int = 60  # Период в секундах для callback
	rate_limit_deals_max: int = 5  # Максимум созданий сделок
	rate_limit_deals_period: int = 60  # Период в секундах для создания сделок

	@field_validator("admin_ids", mode="before")
	@classmethod
	def parse_admin_ids(cls, v):
		if isinstance(v, list):
			result = []
			for x in v:
				try:
					result.append(int(x))
				except (ValueError, TypeError):
					# Пропускаем нечисловые значения (они должны быть в admin_usernames)
					pass
			return result
		if isinstance(v, str):
			items = [s.strip() for s in v.split(",") if s.strip()]
			result = []
			for x in items:
				try:
					result.append(int(x))
				except (ValueError, TypeError):
					# Пропускаем нечисловые значения (они должны быть в admin_usernames)
					pass
			return result
		return []

	@field_validator("admin_usernames", mode="before")
	@classmethod
	def parse_admin_usernames(cls, v):
		if isinstance(v, list):
			return [str(x).strip().lstrip("@") for x in v if x]
		if isinstance(v, str):
			items = [s.strip().lstrip("@") for s in v.split(",") if s.strip()]
			return items
		return []


def get_settings() -> Settings:
	return Settings(
		telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
		admin_ids=os.getenv("ADMIN_IDS", ""),
		admin_usernames=os.getenv("ADMIN_USERNAMES", ""),
		database_path=os.getenv("DATABASE_PATH", "./data/bot.db"),
		google_sheet_id=os.getenv("GOOGLE_SHEET_ID", ""),
		google_credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", ""),
		google_sheet_name=os.getenv("GOOGLE_SHEET_NAME", ""),
		log_level=os.getenv("LOG_LEVEL", "INFO"),
		rate_limit_messages_max=int(os.getenv("RATE_LIMIT_MESSAGES_MAX", "10")),
		rate_limit_messages_period=int(os.getenv("RATE_LIMIT_MESSAGES_PERIOD", "60")),
		rate_limit_spam_max=int(os.getenv("RATE_LIMIT_SPAM_MAX", "3")),
		rate_limit_spam_period=int(os.getenv("RATE_LIMIT_SPAM_PERIOD", "10")),
		rate_limit_callbacks_max=int(os.getenv("RATE_LIMIT_CALLBACKS_MAX", "20")),
		rate_limit_callbacks_period=int(os.getenv("RATE_LIMIT_CALLBACKS_PERIOD", "60")),
		rate_limit_deals_max=int(os.getenv("RATE_LIMIT_DEALS_MAX", "5")),
		rate_limit_deals_period=int(os.getenv("RATE_LIMIT_DEALS_PERIOD", "60")),
	)
