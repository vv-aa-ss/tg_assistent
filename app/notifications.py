# Глобальный словарь для хранения ID уведомлений
# Ключ: (chat_id, order_id/question_id, type) где type: 'order', 'sell_order', 'question'
# Значение: message_id уведомления
notification_ids: dict = {}
