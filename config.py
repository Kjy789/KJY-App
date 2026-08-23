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
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

# LOCAL FALLBACK DATABASE PATH
DB_PATH = os.path.join(BASE_DIR, "kjy_inventory.db")

# IMAGES DIRECTORIES (สำหรับ Local Storage Fallback & Static hosting)
IMAGES_DIR = os.path.join(BASE_DIR, "images")
RECEIPT_IMAGES_DIR = os.path.join(IMAGES_DIR, "receipts")
PRODUCT_IMAGES_DIR = os.path.join(IMAGES_DIR, "products")
LOCATION_IMAGES_DIR = os.path.join(IMAGES_DIR, "locations")

for d in (IMAGES_DIR, RECEIPT_IMAGES_DIR, PRODUCT_IMAGES_DIR, LOCATION_IMAGES_DIR):
    os.makedirs(d, exist_ok=True)

# GEMINI API KEY FOR AI OCR & PRODUCT VISION
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if not GEMINI_API_KEY:
    api_txt_path = os.path.join(BASE_DIR, "api.txt")
    if os.path.exists(api_txt_path):
        try:
            with open(api_txt_path, "r", encoding="utf-8") as f:
                GEMINI_API_KEY = f.read().strip()
        except Exception:
            pass


# GEMINI BACKUP API KEY
GEMINI_API_KEY_BACKUP = os.getenv("GEMINI_API_KEY_BACKUP", "").strip()
if not GEMINI_API_KEY_BACKUP:
    p = os.path.join(BASE_DIR, "api_backup.txt")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                GEMINI_API_KEY_BACKUP = f.read().strip()
        except Exception:
            pass
