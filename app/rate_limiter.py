"""
Rate limiting и flood protection для бота
"""
import time
import asyncio
from collections import defaultdict
from typing import Dict, Tuple
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
import logging

logger = logging.getLogger("app.rate_limiter")


class RateLimiter:
	"""Простой rate limiter на основе sliding window"""
	
	def __init__(self, max_requests: int, period: float):
		"""
		Args:
			max_requests: Максимальное количество запросов
			period: Период времени в секундах
		"""
		self.max_requests = max_requests
		self.period = period
		# Храним временные метки запросов для каждого пользователя
		self.requests: Dict[int, list[float]] = defaultdict(list)
		self._lock = asyncio.Lock()
	
	async def is_allowed(self, user_id: int) -> Tuple[bool, float]:
		"""
		Проверяет, разрешен ли запрос
		Returns:
			(is_allowed, wait_time) - разрешен ли запрос и сколько ждать
		"""
		async with self._lock:
			now = time.time()
			user_requests = self.requests[user_id]
			
			# Удаляем старые запросы (старше period)
			user_requests[:] = [req_time for req_time in user_requests if now - req_time < self.period]
			
			# Проверяем лимит
			if len(user_requests) >= self.max_requests:
				# Вычисляем время до следующего разрешенного запроса
				oldest_request = min(user_requests)
				wait_time = self.period - (now - oldest_request)
				return False, wait_time
			
			# Добавляем текущий запрос
			user_requests.append(now)
			return True, 0.0
	
	async def cleanup_old_entries(self, max_age: float = 3600):
		"""Удаляет старые записи пользователей (неактивных более max_age секунд)"""
		async with self._lock:
			now = time.time()
			users_to_remove = []
			for user_id, requests in self.requests.items():
				if requests:
					last_request = max(requests)
					if now - last_request > max_age:
						users_to_remove.append(user_id)
			
			for user_id in users_to_remove:
				del self.requests[user_id]
			
			if users_to_remove:
				logger.debug(f"🧹 Очищено {len(users_to_remove)} неактивных пользователей из rate limiter")


# Глобальные rate limiters для разных типов действий
# Инициализируются в init_rate_limiters() с параметрами из config
message_rate_limiter: RateLimiter = None
spam_rate_limiter: RateLimiter = None
deal_creation_limiter: RateLimiter = None
callback_rate_limiter: RateLimiter = None


def init_rate_limiters(settings) -> None:
	"""Инициализирует rate limiters с параметрами из настроек"""
	global message_rate_limiter, spam_rate_limiter, deal_creation_limiter, callback_rate_limiter
	
	# Ограничение: сообщений в период (для обычных пользователей)
	message_rate_limiter = RateLimiter(
		max_requests=settings.rate_limit_messages_max,
		period=float(settings.rate_limit_messages_period)
	)
	
	# Ограничение: сообщений в период (для защиты от быстрого спама)
	spam_rate_limiter = RateLimiter(
		max_requests=settings.rate_limit_spam_max,
		period=float(settings.rate_limit_spam_period)
	)
	
	# Ограничение: сделок в период (защита от массового создания сделок)
	deal_creation_limiter = RateLimiter(
		max_requests=settings.rate_limit_deals_max,
		period=float(settings.rate_limit_deals_period)
	)
	
	# Ограничение: callback запросов в период
	callback_rate_limiter = RateLimiter(
		max_requests=settings.rate_limit_callbacks_max,
		period=float(settings.rate_limit_callbacks_period)
	)
	
	logger.info(
		f"✅ Rate limiters инициализированы: "
		f"messages={settings.rate_limit_messages_max}/{settings.rate_limit_messages_period}s, "
		f"spam={settings.rate_limit_spam_max}/{settings.rate_limit_spam_period}s, "
		f"callbacks={settings.rate_limit_callbacks_max}/{settings.rate_limit_callbacks_period}s, "
		f"deals={settings.rate_limit_deals_max}/{settings.rate_limit_deals_period}s"
	)


class RateLimitMiddleware(BaseMiddleware):
	"""Middleware для rate limiting всех сообщений"""
	
	async def __call__(
		self,
		handler,
		event: TelegramObject,
		data: dict,
	) -> any:
		# Получаем user_id из события
		user_id = None
		if isinstance(event, Message):
			if event.from_user:
				user_id = event.from_user.id
		elif isinstance(event, CallbackQuery):
			if event.from_user:
				user_id = event.from_user.id
		
		if not user_id:
			# Если нет user_id, пропускаем (системные сообщения)
			return await handler(event, data)
		
		# Проверяем быстрый спам (3 сообщения в 10 секунд)
		is_allowed_spam, wait_time_spam = await spam_rate_limiter.is_allowed(user_id)
		if not is_allowed_spam:
			logger.warning(f"⚠️ Rate limit (spam): user_id={user_id}, wait={wait_time_spam:.1f}s")
			if isinstance(event, Message):
				await event.answer(
					f"⏳ Слишком много сообщений. Подождите {int(wait_time_spam)} секунд.",
					show_alert=False
				)
			elif isinstance(event, CallbackQuery):
				await event.answer(
					f"⏳ Слишком много запросов. Подождите {int(wait_time_spam)} секунд.",
					show_alert=True
				)
			return
		
		# Проверяем общий лимит (10 сообщений в 60 секунд)
		is_allowed, wait_time = await message_rate_limiter.is_allowed(user_id)
		if not is_allowed:
			logger.warning(f"⚠️ Rate limit (general): user_id={user_id}, wait={wait_time:.1f}s")
			if isinstance(event, Message):
				await event.answer(
					f"⏳ Превышен лимит сообщений. Подождите {int(wait_time)} секунд.",
					show_alert=False
				)
			elif isinstance(event, CallbackQuery):
				await event.answer(
					f"⏳ Превышен лимит запросов. Подождите {int(wait_time)} секунд.",
					show_alert=True
				)
			return
		
		# Если все проверки пройдены, пропускаем дальше
		return await handler(event, data)


class CallbackRateLimitMiddleware(BaseMiddleware):
	"""Middleware для rate limiting callback запросов"""
	
	async def __call__(
		self,
		handler,
		event: CallbackQuery,
		data: dict,
	) -> any:
		# Проверяем, что rate limiter инициализирован
		if callback_rate_limiter is None:
			return await handler(event, data)
		
		if not event.from_user:
			return await handler(event, data)
		
		user_id = event.from_user.id
		
		# Проверяем лимит для callback запросов
		is_allowed, wait_time = await callback_rate_limiter.is_allowed(user_id)
		if not is_allowed:
			logger.warning(f"⚠️ Rate limit (callback): user_id={user_id}, wait={wait_time:.1f}s")
			await event.answer(
				f"⏳ Слишком много запросов. Подождите {int(wait_time)} секунд.",
				show_alert=True
			)
			return
		
		return await handler(event, data)


async def check_deal_creation_limit(user_id: int) -> Tuple[bool, float]:
	"""
	Проверяет лимит на создание сделок
	Returns:
		(is_allowed, wait_time)
	"""
	if deal_creation_limiter is None:
		# Если не инициализирован, разрешаем (не должно происходить)
		return True, 0.0
	return await deal_creation_limiter.is_allowed(user_id)


async def periodic_cleanup():
	"""Периодическая очистка старых записей в rate limiters"""
	while True:
		await asyncio.sleep(3600)  # Каждый час
		try:
			await message_rate_limiter.cleanup_old_entries()
			await spam_rate_limiter.cleanup_old_entries()
			await deal_creation_limiter.cleanup_old_entries()
			await callback_rate_limiter.cleanup_old_entries()
			logger.debug("🧹 Rate limiter cleanup completed")
		except Exception as e:
			logger.error(f"❌ Error in rate limiter cleanup: {e}")
