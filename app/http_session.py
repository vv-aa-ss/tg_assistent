"""
Глобальная aiohttp.ClientSession для всего приложения.
Переиспользует одно TCP/SSL-соединение вместо создания нового на каждый запрос.
"""
import logging
from typing import Optional

try:
	import aiohttp
except ImportError:
	aiohttp = None

logger = logging.getLogger("app.http_session")

_session: Optional["aiohttp.ClientSession"] = None


def get_session() -> "aiohttp.ClientSession":
	"""
	Возвращает глобальную aiohttp.ClientSession.
	Создаёт её при первом вызове или если предыдущая была закрыта.
	"""
	global _session
	if aiohttp is None:
		raise RuntimeError("aiohttp не установлен. Установите: pip install aiohttp")
	if _session is None or _session.closed:
		_session = aiohttp.ClientSession()
		logger.debug("🔄 Создана новая глобальная aiohttp.ClientSession")
	return _session


async def close_session() -> None:
	"""Закрывает глобальную сессию. Вызывать при остановке бота."""
	global _session
	if _session and not _session.closed:
		await _session.close()
		logger.info("🔒 Глобальная aiohttp.ClientSession закрыта")
	_session = None
