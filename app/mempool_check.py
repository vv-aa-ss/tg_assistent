"""Модуль для проверки транзакций через API mempool.space"""
import aiohttp
import logging
from typing import Optional

from app.http_session import get_session

logger = logging.getLogger("app.mempool_check")


async def check_btc_transaction(wallet_address: str) -> tuple[bool, Optional[str]]:
	"""
	Проверяет наличие транзакций для BTC адреса через API mempool.space
	
	Args:
		wallet_address: Адрес кошелька Bitcoin
		
	Returns:
		Кортеж (has_transactions, error_message)
		has_transactions: True если есть транзакции (chain_stats.tx_count >= 1), False иначе
		error_message: Сообщение об ошибке или None
	"""
	if not wallet_address:
		return False, "Адрес кошелька не указан"
	
	api_url = f"https://mempool.space/api/address/{wallet_address}"
	
	try:
		session = get_session()
		async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
				if response.status == 200:
					data = await response.json()
					chain_stats = data.get("chain_stats", {})
					tx_count = chain_stats.get("tx_count", 0)
					
					has_transactions = tx_count >= 1
					logger.info(f"✅ Проверка адреса {wallet_address}: tx_count={tx_count}, has_transactions={has_transactions}")
					return has_transactions, None
				else:
					error_msg = f"Ошибка API: статус {response.status}"
					logger.warning(f"⚠️ {error_msg} для адреса {wallet_address}")
					return False, error_msg
	except aiohttp.ClientError as e:
		error_msg = f"Ошибка подключения к API: {e}"
		logger.warning(f"⚠️ {error_msg}")
		return False, error_msg
	except Exception as e:
		error_msg = f"Неожиданная ошибка: {e}"
		logger.error(f"❌ {error_msg}", exc_info=True)
		return False, error_msg
