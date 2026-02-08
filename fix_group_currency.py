"""Скрипт для проверки и исправления currency в card_groups"""
import sqlite3

db_path = r"data\bot.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Проверяем наличие таблицы
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='card_groups'")
if not cur.fetchone():
    print("Таблица card_groups не найдена!")
    conn.close()
    exit()

# Показываем текущие значения
cur.execute("SELECT id, name, currency FROM card_groups")
rows = cur.fetchall()
print("Текущие группы карт:")
for r in rows:
    print(f"  id={r[0]}, name={r[1]}, currency={r[2]}")

conn.close()
