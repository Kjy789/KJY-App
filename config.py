"""
Config - ค่าตั้งต้นของระบบ KJY Inventory Cloud App
ร้าน: คำเจริญเกษตรยนต์ (Kamjarenkasetyon / KJY)
"""

import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# โหลดค่าจากไฟล์ .env หรือ .env.example
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv(os.path.join(BASE_DIR, ".env.example"))

SHOP_NAME_TH = "คำเจริญเกษตรยนต์"
SHOP_NAME_EN = "Kamjarenkasetyon"
SHOP_CODE = "KJY"

# SUPABASE CLOUD CONFIG
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
# Clean URL: strip /rest/v1 or trailing slashes
if SUPABASE_URL.endswith("/rest/v1/"):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[:-8]
SUPABASE_URL = SUPABASE_URL.rstrip("/")

SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip() # Anon Key
# รองรับทั้ง SUPABASE_SERVICE_ROLE_KEY และ SUPABASE_SECRET_KEY (ชื่อ env ที่ Supabase dashboard ให้มา)
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_SECRET_KEY", "").strip()
)

# LOCAL FALLBACK DATABASE PATH
DB_PATH = os.path.join(BASE_DIR, "kjy_inventory.db")

# IMAGES DIRECTORIES (สำหรับ Local Storage Fallback & Static hosting)
IMAGES_DIR = os.path.join(BASE_DIR, "images")
RECEIPT_IMAGES_DIR = os.path.join(IMAGES_DIR, "receipts")
PRODUCT_IMAGES_DIR = os.path.join(IMAGES_DIR, "products")
LOCATION_IMAGES_DIR = os.path.join(IMAGES_DIR, "locations")

for d in (IMAGES_DIR, RECEIPT_IMAGES_DIR, PRODUCT_IMAGES_DIR, LOCATION_IMAGES_DIR):
    os.makedirs(d, exist_ok=True)

# GEMINI API KEYS FOR AI OCR & PRODUCT VISION
# รองรับหลายชื่อ Environment Variables (กันกรณีตั้งชื่อไม่ตรงบน Render):
#   Primary : GEMINI_API_KEY_PRIMARY > GEMINI_API_KEY > api.txt
#   Backup  : GEMINI_API_KEY_SECONDARY > GEMINI_API_KEY_BACKUP > api_backup.txt
def _read_key_file(path: str) -> str:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def _pick_env(*names: str) -> str:
    """คืนค่าจาก env ตัวแรกที่ไม่ว่าง (strip แล้ว)"""
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


GEMINI_API_KEY = (
    _pick_env("GEMINI_API_KEY_PRIMARY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    or _read_key_file(os.path.join(BASE_DIR, "api.txt"))
)

GEMINI_API_KEY_BACKUP = (
    _pick_env("GEMINI_API_KEY_SECONDARY", "GEMINI_API_KEY_BACKUP", "GEMINI_API_KEY_2")
    or _read_key_file(os.path.join(BASE_DIR, "api_backup.txt"))
)
