import sqlite3
import os

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "invest.db")


def get_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            currency TEXT DEFAULT 'CNY',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER DEFAULT 1,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            business_type TEXT NOT NULL,
            cash_flow REAL NOT NULL,
            shares REAL,
            price REAL,
            market_value REAL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );

        INSERT OR IGNORE INTO accounts (id, name, currency) VALUES (1, '默认账户', 'CNY');
    """)
    conn.commit()
    conn.close()
