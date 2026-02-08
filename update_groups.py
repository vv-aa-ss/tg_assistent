import sqlite3

conn = sqlite3.connect('./data/bot.db')
cur = conn.cursor()

# Добавляем колонку currency, если её нет
try:
    cur.execute("ALTER TABLE card_groups ADD COLUMN currency TEXT DEFAULT 'BYN'")
    print("✅ Добавлена колонка currency")
except sqlite3.OperationalError:
    print("ℹ️ Колонка currency уже существует")

# Устанавливаем RUB для российской группы
cur.execute("UPDATE card_groups SET currency = 'RUB' WHERE name = 'РАШКА'")

# Устанавливаем BYN для остальных
cur.execute("UPDATE card_groups SET currency = 'BYN' WHERE name != 'РАШКА' AND (currency IS NULL OR currency = '')")

conn.commit()

# Показываем результат
cur.execute("SELECT id, name, currency FROM card_groups")
print("Обновленные группы карт:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} -> {row[2]}")

conn.close()
print("\n✅ Готово!")
