"""
test_supabase_conn.py — ทดสอบการเชื่อมต่อ Supabase และ Insert ข้อมูลสินค้าทดสอบ
วิธีรัน: python test_supabase_conn.py

สคริปต์นี้จะ:
1. ตรวจสอบ Environment Variables (SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY)
2. Initialize supabase-py client ด้วย SERVICE_ROLE_KEY (บายพาส RLS)
3. ทดสอบ SELECT ข้อมูลจากตาราง products
4. ทดสอบ INSERT สินค้าทดสอบ (SKU: 'TEST-001')
5. แสดง Log ชัดเจนว่าเชื่อมต่อสำเร็จหรือติด Error ตรงไหน
"""

import os
import sys
import traceback

# ============================================================
# STEP 1: ตรวจสอบ Environment Variables
# ============================================================
print("=" * 70)
print("🔌 STEP 1: ตรวจสอบ Environment Variables")
print("=" * 70)

# โหลด .env จาก kjy app
from dotenv import load_dotenv
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
    print("✅ พบไฟล์ .env และโหลดเรียบร้อย")
else:
    print("⚠️ ไม่พบไฟล์ .env — ใช้ค่าจาก Environment ของระบบ")
    load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

print(f"✅ SUPABASE_URL = {SUPABASE_URL}")
print(f"✅ SUPABASE_KEY = {SUPABASE_KEY[:20]}... (ความยาว {len(SUPABASE_KEY)} ตัวอักษร)")
print(f"✅ SUPABASE_SERVICE_ROLE_KEY = {SUPABASE_SERVICE_ROLE_KEY[:20]}... (ความยาว {len(SUPABASE_SERVICE_ROLE_KEY)} ตัวอักษร)")

# Clean URL
if SUPABASE_URL.endswith("/rest/v1/"):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[:-8]
SUPABASE_URL = SUPABASE_URL.rstrip("/")

if not SUPABASE_URL:
    print("❌ ERROR: ไม่พบ SUPABASE_URL ใน Environment Variables")
    sys.exit(1)
if not SUPABASE_SERVICE_ROLE_KEY:
    print("❌ ERROR: ไม่พบ SUPABASE_SERVICE_ROLE_KEY — จำเป็นต้องใช้เพื่อบายพาส RLS")
    sys.exit(1)

print("✅ Environment Variables ครบถ้วน")

# ============================================================
# STEP 2: Initialize Supabase Client (ใช้ SERVICE_ROLE_KEY)
# ============================================================
print("\n" + "=" * 70)
print("🔑 STEP 2: Initialize Supabase Client (SERVICE_ROLE_KEY)")
print("=" * 70)

try:
    from supabase import create_client, Client
    print("✅ import supabase สำเร็จ")

    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    print("✅ สร้าง Client สำเร็จ (ใช้ SERVICE_ROLE_KEY สำหรับ admin client)")
    print("   - supabase_client: ใช้ Anon Key")
    print("   - supabase_admin : ใช้ Service Role Key (บายพาส RLS)")
except Exception as e:
    print(f"❌ ERROR: ไม่สามารถ initialize Supabase client ได้: {e}")
    traceback.print_exc()
    sys.exit(1)

# ============================================================
# STEP 3: ทดสอบ SELECT ข้อมูลจากตาราง products
# ============================================================
print("\n" + "=" * 70)
print("📖 STEP 3: ทดสอบ SELECT จากตาราง products")
print("=" * 70)

try:
    res = supabase_admin.from_("products").select("*").limit(5).execute()
    row_count = len(res.data) if res.data else 0
    print(f"✅ SELECT สำเร็จ! พบข้อมูล {row_count} รายการ")
    if row_count > 0:
        for item in res.data[:3]:
            print(f"   - id={item.get('id')} | name={item.get('name')} | sku={item.get('sku')}")
except Exception as e:
    print(f"❌ ERROR: SELECT ล้มเหลว: {e}")
    print("   แปลว่า: ปัญหาอาจมาจาก Connection, Authentication, หรือ RLS")
    traceback.print_exc()

# ============================================================
# STEP 4: ทดสอบ INSERT สินค้าทดสอบ (SKU: 'TEST-001')
# ============================================================
print("\n" + "=" * 70)
print("➕ STEP 4: ทดสอบ INSERT สินค้าทดสอบ (SKU: 'TEST-001')")
print("=" * 70)

# ตรวจสอบว่าคอลัมน์ที่มีบน Supabase (จาก schema.sql)
test_payload = {
    "sku": "TEST-001",
    "name": "สินค้าทดสอบ Supabase Conn",
    "category": "ทดสอบ",
    "sale_price": 99.0,
    "stock_qty": 10,
    "location_code": "T-01",
    "status": "active"
}

try:
    # ลบ TEST-001 เดิมทิ้งก่อน (กันซ้ำ)
    try:
        del_res = supabase_admin.from_("products").delete().eq("sku", "TEST-001").execute()
        print(f"🗑️ ลบข้อมูล TEST-001 เดิม (ถ้ามี): {len(del_res.data) if del_res.data else 0} รายการ")
    except Exception as e:
        print(f"⚠️ ลบข้อมูลเดิมไม่สำเร็จ (ไม่เป็นไร): {e}")

    print(f"📦 กำลัง INSERT payload: {test_payload}")
    insert_res = supabase_admin.from_("products").insert(test_payload).execute()

    if insert_res.data:
        new_product = insert_res.data[0]
        print(f"✅✅✅ INSERT สำเร็จ!!!")
        print(f"   - ID: {new_product.get('id')}")
        print(f"   - SKU: {new_product.get('sku')}")
        print(f"   - Name: {new_product.get('name')}")
        print(f"   - ไปตรวจสอบตาราง products ใน Supabase Table Editor ได้เลย!")
    else:
        print("⚠️ INSERT ไม่มีข้อมูลตอบกลับ (response.data ว่าง)")

except Exception as e:
    print(f"❌❌❌ INSERT ล้มเหลว: {e}")
    print("\n" + "=" * 70)
    print("🔍 วิเคราะห์สาเหตุ:")
    print("=" * 70)
    err_str = str(e).lower()
    if "permission denied" in err_str or "row-level security" in err_str or "rls" in err_str:
        print("👉 สาเหตุ: RLS (Row Level Security) ยังบล็อกการ INSERT อยู่")
        print("   วิธีแก้: ไปที่ Supabase Dashboard → SQL Editor")
        print("          แล้วรันโค้ดในไฟล์ fix_rls_permissions.sql")
    elif "could not connect" in err_str or "connection" in err_str or "timeout" in err_str:
        print("👉 สาเหตุ: ไม่สามารถเชื่อมต่อ Supabase ได้ (Network/Connection)")
        print("   วิธีแก้: ตรวจสอบ SUPABASE_URL และอินเทอร์เน็ต")
    elif "invalid api key" in err_str or "apikey" in err_str or "unauthorized" in err_str:
        print("👉 สาเหตุ: API Key ไม่ถูกต้อง (Invalid/Expired)")
        print("   วิธีแก้: ไป Supabase Dashboard → Settings → API")
        print("          คัดลอก Service Role Key ใหม่มาใส่ใน .env")
    else:
        print(f"👉 สาเหตุ: {e}")
    traceback.print_exc()
    sys.exit(1)

# ============================================================
# STEP 5: ยืนยันว่า INSERT สำเร็จโดย SELECT กลับมา
# ============================================================
print("\n" + "=" * 70)
print("🔍 STEP 5: ยืนยันข้อมูลโดย SELECT กลับมา")
print("=" * 70)

try:
    check_res = supabase_admin.from_("products").select("*").eq("sku", "TEST-001").execute()
    if check_res.data:
        item = check_res.data[0]
        print(f"✅ ยืนยันสำเร็จ! พบสินค้า TEST-001 ใน Supabase:")
        print(f"   - ID: {item.get('id')}")
        print(f"   - Name: {item.get('name')}")
        print(f"   - Sale Price: {item.get('sale_price')}")
        print(f"   - Stock: {item.get('stock_qty')}")
    else:
        print("❌ ไม่พบสินค้า TEST-001 หลัง INSERT — ข้อมูลอาจไม่ถูกบันทึกจริง")
except Exception as e:
    print(f"❌ ERROR ในการ SELECT ยืนยัน: {e}")
    traceback.print_exc()

print("\n" + "=" * 70)
print("🏁 สรุปผลการทดสอบ")
print("=" * 70)
print("✅ หากทุกขั้นตอนผ่าน: การเชื่อมต่อ Supabase ทำงานถูกต้อง")
print("❌ หากติด Error: ดูข้อความ Error ด้านบนและแก้ตามคำแนะนำ")
print("=" * 70)