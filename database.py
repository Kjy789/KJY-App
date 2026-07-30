"""
Database - จัดการการเชื่อมต่อ Supabase PostgreSQL / Local SQLite Fallback
ระบบ: KJY Inventory Cloud App (คำเจริญเกษตรยนต์)
"""

import sqlite3
import logging
from contextlib import contextmanager
from config import DB_PATH, SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY

logger = logging.getLogger("database")

# Supabase Client setup (Optional if credentials configured)
supabase_client = None
supabase_admin = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client, Client
        supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        if SUPABASE_SERVICE_ROLE_KEY:
            supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        else:
            supabase_admin = supabase_client
        logger.info("Connected to Supabase Cloud Database successfully!")
    except Exception as e:
        logger.warning(f"Could not initialize Supabase client: {e}. Falling back to SQLite local database.")

# Local SQLite Schema fallback
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sku                 TEXT UNIQUE,
    name                TEXT NOT NULL,
    category            TEXT,
    sale_price          REAL DEFAULT 0,
    latest_cost         REAL DEFAULT 0,
    stock_qty           INTEGER DEFAULT 0,
    location_code       TEXT,
    image_path          TEXT,
    location_image_path TEXT,
    status              TEXT DEFAULT 'active',
    created_at          TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at          TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
CREATE INDEX IF NOT EXISTS idx_products_location ON products(location_code);

CREATE TABLE IF NOT EXISTS purchase_receipts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_date    TEXT,
    supplier_name   TEXT,
    receipt_no      TEXT,
    image_path      TEXT,
    ocr_status      TEXT DEFAULT 'pending',
    ocr_raw_json    TEXT,
    total_amount    REAL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS receipt_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id      INTEGER NOT NULL REFERENCES purchase_receipts(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    ocr_name        TEXT,
    qty             REAL DEFAULT 0,
    unit_cost       REAL DEFAULT 0,
    line_total      REAL DEFAULT 0,
    matched         INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS cost_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    receipt_id      INTEGER REFERENCES purchase_receipts(id) ON DELETE SET NULL,
    cost            REAL NOT NULL,
    effective_date  TEXT DEFAULT (datetime('now', 'localtime')),
    note            TEXT
);

CREATE TABLE IF NOT EXISTS locations (
    code            TEXT PRIMARY KEY,
    image_path      TEXT,
    note            TEXT,
    created_at      TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


def get_connection():
    """เปิดการเชื่อมต่อ DB (SQLite) พร้อมตั้งค่าเป็น Dict Row"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_session():
    """Context manager สำหรับ SQLite transaction"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """สร้างตารางในฐานข้อมูล SQLite และใส่ข้อมูล Mock อัตโนมัติถ้ายังไม่มีสินค้า"""
    with db_session() as conn:
        conn.executescript(SCHEMA)

        # Migrate check: ensure location_image_path column exists
        cur = conn.execute("PRAGMA table_info(products);")
        columns = [row["name"] for row in cur.fetchall()]
        if "location_image_path" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN location_image_path TEXT;")

        # Seed initial mock products if database is empty
        count_res = conn.execute("SELECT COUNT(*) as cnt FROM products;").fetchone()
        if count_res and count_res["cnt"] == 0:
            mock_items = [
                ("NUT-M10-01", "น็อตหกเหลี่ยม M10 x 25mm", "น็อต-สกรู", 15.0, 8.0, 121, "A-01-05", "https://images.unsplash.com/photo-1586864387967-d02ef85d93e8?w=500&auto=format&fit=crop&q=60"),
                ("BELT-B52", "สายพานพัดลม Kubota B52", "สายพาน", 280.0, 180.0, 45, "B-02-12", "https://images.unsplash.com/photo-1581092160607-ee22621dd758?w=500&auto=format&fit=crop&q=60"),
                ("FILT-OIL-K", "กรองน้ำมันเครื่อง Kubota L3608", "กรองอากาศ", 190.0, 120.0, 68, "C-01-02", "https://images.unsplash.com/photo-1486262715619-67b85e0b08d3?w=500&auto=format&fit=crop&q=60"),
                ("OIL-4T-1L", "น้ำมันเครื่องเกรดพรีเมียม 4T 1L", "น้ำมัน", 160.0, 110.0, 80, "D-05-01", "https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=500&auto=format&fit=crop&q=60"),
                ("TIRE-600-14", "ยางรถไถ 6.00-14 6PR", "ยาง", 1850.0, 1400.0, 14, "E-01-01", "https://images.unsplash.com/photo-1578844251758-2f71da64c96f?w=500&auto=format&fit=crop&q=60"),
                ("BLADE-K18", "ใบโรตารี่ ตราช้าง K18", "อะไหล่เกษตร", 220.0, 150.0, 90, "A-03-08", "https://images.unsplash.com/photo-1504917595217-d4dc5ebe6122?w=500&auto=format&fit=crop&q=60")
            ]
            for sku, name, cat, sale, cost, qty, loc, img in mock_items:
                conn.execute(
                    """INSERT INTO products (sku, name, category, sale_price, latest_cost, stock_qty, location_code, image_path, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                    (sku, name, cat, sale, cost, qty, loc, img)
                )

# Run DB init on module import
try:
    init_db()
except Exception as e:
    logger.error(f"Error initializing local DB: {e}")
