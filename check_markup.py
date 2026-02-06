import sqlite3

conn = sqlite3.connect('./data/bot.db')
cursor = conn.cursor()

# Обновляем наценки на правильные значения
updates = [
    ("buy_markup_percent_small", "20"),
    ("buy_markup_percent_101_449", "15"),
    ("buy_markup_percent_450_699", "14"),
    ("buy_markup_percent_700_999", "13"),
    ("buy_markup_percent_1000_1499", "12"),
    ("buy_markup_percent_1500_1999", "11"),
    ("buy_markup_percent_2000_plus", "10"),
]

print("=== Обновление настроек наценки ===")
for key, value in updates:
    cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
    print(f"  {key}: → {value}%")

conn.commit()

# Показать обновленные наценки из settings
print("\n=== Обновленные настройки наценки ===")
cursor.execute("SELECT key, value FROM settings WHERE key LIKE 'buy_markup%' OR key LIKE 'markup_percent%'")
rows = cursor.fetchall()
for row in rows:
    print(f"  {row[0]}: {row[1]}%")

conn.close()
print("\n✅ Готово! Перезапустите бота для применения изменений.")
