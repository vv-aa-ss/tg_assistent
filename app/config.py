import os
from typing import List
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv(override=True)


class Settings(BaseModel):
	telegram_bot_token: str
	admin_ids: List[int]
	database_path: str = "./data/bot.db"

	@field_validator("admin_ids", mode="before")
	@classmethod
	def parse_admin_ids(cls, v):
		if isinstance(v, list):
			return [int(x) for x in v]
		if isinstance(v, str):
			items = [s.strip() for s in v.split(",") if s.strip()]
			return [int(x) for x in items]
		return []


def get_settings() -> Settings:
	return Settings(
		telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
		admin_ids=os.getenv("ADMIN_IDS", ""),
		database_path=os.getenv("DATABASE_PATH", "./data/bot.db"),
	)
