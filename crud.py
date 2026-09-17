"""
CRUD - ฟังก์ชันจัดการข้อมูลหลักของระบบ KJY Inventory Cloud App
รองรับการสลับระหว่าง Supabase Cloud (ถ้ากำหนด) หรือ SQLite Local Fallback
พร้อมระบบแยกสิทธิ์เด็ดขาด (Staff vs Owner)
"""

from database import db_session, supabase_client, supabase_admin
import logging
import json
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("crud")


# ============================================================
# TIMEZONE (Asia/Bangkok, UTC+7)
# ใช้คำนวณ "ยอดขายวันนี้" ให้ตรงกับเวลาประเทศไทยเสมอ
# ============================================================

BANGKOK_TZ = timezone(timedelta(hours=7))


def bangkok_now() -> datetime:
    """เวลาปัจจุบันตามเขตเวลาไทย (Asia/Bangkok)"""
    return datetime.now(BANGKOK_TZ)


def bangkok_today_str() -> str:
    """วันที่ปัจจุบัน (YYYY-MM-DD) ตามเขตเวลาไทย (Asia/Bangkok)"""
    return bangkok_now().strftime("%Y-%m-%d")


def to_bangkok_date(value):
    """แปลง timestamp จากฐานข้อมูล (str / datetime) ให้เป็นวันที่ตามเขตเวลาไทย (Asia/Bangkok)

    - ถ้า timestamp มี timezone ติดมาด้วย (เช่น ISO จาก Supabase) จะแปลงตรงๆ
    - ถ้าเป็น naive timestamp (SQLite datetime('now','localtime')) จะถือว่าเป็นเวลาเครื่อง server
      แล้วปรับด้วย offset ของ server เพื่อให้ได้วันเวลาไทยที่ถูกต้อง
    คืนค่าเป็น datetime.date หรือ None ถ้าแปลงไม่ได้
    """
    if not value:
        return None

    dt = None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        iso = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except Exception:
            dt = None
        if dt is None:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(s[:19].replace("T", " ").strip(), fmt)
                    break
                except Exception:
                    dt = None
        if dt is None:
            return None

    if dt.tzinfo is None:
        # naive -> มองว่าเป็นเวลาท้องถิ่นของ server แล้วเทียบกับ UTC จริง
        server_offset = datetime.now() - datetime.utcnow()
        dt = (dt - server_offset).replace(tzinfo=timezone.utc)
    return dt.astimezone(BANGKOK_TZ).date()

# ============================================================
# SUPABASE COLUMN COMPATIBILITY
# ============================================================
# คอลัมน์ที่มีอยู่จริงบน Supabase products table (ตาม schema.sql)
# หมายเหตุ: location / min_stock / description ต้องรัน migration 
# migrations/002_add_missing_product_columns.sql ก่อน จึงจะใช้ได้จริงบน Supabase
# ถ้ายังไม่รัน migration ฟิลด์เหล่านี้จะถูกกรองออกโดยอัตโนมัติ (ไม่ทำให้ request พัง)
SUPABASE_PRODUCT_COLUMNS = {
    "sku", "name", "category", "cost_price", "sale_price", "stock_qty",
        "location_code", "image_url", "location_image_url", "status", "created_at", "updated_at"
}

# คอลัมน์เพิ่มเติมที่ต้องรัน migration ก่อน (002_add_missing_product_columns.sql)
SUPABASE_MIGRATED_COLUMNS = {
    "description", "min_stock", "location", "is_complete"
}

# คอลัมน์ที่เพิ่มจาก Migration 003 (front_stock / warehouse_stock)
SUPABASE_STOCK_COLUMNS = {
    "front_stock", "warehouse_stock"
}

# ตรวจสอบว่าคอลัมน์ is_complete มีอยู่จริงบน Supabase แล้วหรือยัง (migration 004)
# None = ยังไม่ทราบผล, True/False = ทราบผลแล้ว (จำไว้ เพื่อไม่ยิง query ที่พังซ้ำ)
_CLOUD_HAS_IS_COMPLETE = None


def _cloud_is_complete_available() -> bool:
    """ตรวจ (และจำผล) ว่าคอลัมน์ is_complete มีอยู่บน Supabase แล้วหรือยัง

    ถ้ายังไม่ได้รัน supabase_migration.sql จะคืน False และระบบจะใช้ข้อมูลจาก SQLite local ต่อไป
    """
    global _CLOUD_HAS_IS_COMPLETE
    if _CLOUD_HAS_IS_COMPLETE is not None:
        return _CLOUD_HAS_IS_COMPLETE
    if not supabase_admin:
        _CLOUD_HAS_IS_COMPLETE = False
        return False
    try:
        supabase_admin.from_("products").select("is_complete").limit(1).execute()
        _CLOUD_HAS_IS_COMPLETE = True
    except Exception as e:
        logger.info(f"Supabase ยังไม่มีคอลัมน์ is_complete (ยังไม่รัน migration): {e}")
        _CLOUD_HAS_IS_COMPLETE = False
    return _CLOUD_HAS_IS_COMPLETE


def _cloud_product_completion_map():
    """คืนค่า {ชื่อสินค้าพิมพ์เล็ก: is_complete} ของสินค้าทั้งหมดบน Supabase

    ใช้รวมรายการ 'ยังลงไม่ครบ' ระหว่าง Cloud + SQLite โดยไม่ให้ซ้ำกัน
    และตัดสินค้าที่ Owner เติมข้อมูลครบแล้วบน Cloud ออกจากรายการของเครื่อง local
    """
    result = {}
    if not supabase_admin:
        return result
    try:
        if _cloud_is_complete_available():
            res = supabase_admin.from_("products").select("name, is_complete").limit(2000).execute()
        else:
            res = supabase_admin.from_("products").select("name").limit(2000).execute()
        for r in (res.data or []):
            key = str(r.get("name") or "").strip().lower()
            if not key:
                continue
            result[key] = bool(r.get("is_complete"))
    except Exception as e:
        logger.info(f"Supabase product completion map skipped: {e}")
    return result


def _cloud_row_to_local_shape(row: dict, with_cost: bool = True) -> dict:
    """แปลงแถวสินค้าจาก Supabase ให้มีชื่อฟิลด์เหมือน SQLite (image_path / latest_cost ฯลฯ)"""
    p = dict(row)
    p["image_path"] = p.get("image_path") or p.get("image_url") or ""
    p["location_image_path"] = p.get("location_image_path") or p.get("location_image_url") or ""
    if with_cost:
        p["latest_cost"] = float(p.get("latest_cost") or p.get("cost_price") or 0)
    else:
        p.pop("cost_price", None)
        p.pop("latest_cost", None)
    p["is_complete"] = 1 if p.get("is_complete") in (True, 1, "1", "true", "True") else 0
    return p

def _sanitize_supabase_payload(payload: dict, allowed_columns: set = None) -> dict:
    """
    กรอง payload ให้มีเฉพาะคอลัมน์ที่มีอยู่จริงบน Supabase
    ป้องกัน request พังเมื่อส่งคอลัมน์ที่ไม่มีอยู่
    """
    if allowed_columns is None:
        allowed_columns = SUPABASE_PRODUCT_COLUMNS
    return {k: v for k, v in payload.items() if k in allowed_columns}

def _sanitize_supabase_payload_with_migration(payload: dict) -> dict:
    """
    กรอง payload สำหรับกรณีที่รัน migration 002 แล้ว
    รวมคอลัมน์ migration (description, min_stock, location) ด้วย
    """
    allowed = SUPABASE_PRODUCT_COLUMNS | SUPABASE_MIGRATED_COLUMNS
    return {k: v for k, v in payload.items() if k in allowed}


# ============================================================
# AUDIT LOG (บันทึกประวัติกิจกรรมทั้ง Staff และ Owner)
# ============================================================

def _actor_name(performed_by: str = "staff") -> str:
    """แปลง role เป็นชื่อผู้ใช้สำหรับแสดงใน Audit Log เช่น 'เจ้าของร้าน (Owner)'"""
    by = str(performed_by or "staff").strip().lower()
    if by in ("owner", "boss", "เจ้าของ", "เจ้าของร้าน"):
        return "เจ้าของร้าน (Owner)"
    return f"พนักงาน (Staff: {by})" if by and by != "staff" else "พนักงาน (Staff)"


def add_audit_log(action_type: str, description: str, performed_by: str = "staff"):
    """บันทึก Audit Log อัตโนมัติ (บันทึกลงทั้ง SQLite และ Supabase ถ้าพร้อมใช้งาน)"""
    # 1. บันทึกลง SQLite
    try:
        with db_session() as conn:
            conn.execute(
                """INSERT INTO audit_log (action_type, description, performed_by, timestamp)
                   VALUES (?, ?, ?, datetime('now', 'localtime'))""",
                (action_type, description, performed_by)
            )
    except Exception as e:
        logger.warning(f"Failed to write SQLite audit log: {e}")

    # 2. บันทึกลง Supabase ถ้ามี cloud connection
    if supabase_admin:
        try:
            supabase_admin.from_("audit_log").insert({
                "action_type": action_type,
                "description": description,
                "performed_by": performed_by,
                "timestamp": datetime.now().isoformat()
            }).execute()
        except Exception as e:
            # ไม่ throw error เพื่อไม่ให้ขัดจังหวะการทำงานหลัก
            pass


def get_audit_logs(limit: int = 100, user: str = None, keyword: str = None):
    """ดึง Audit Log ล่าสุด (ลอง Supabase ก่อน ถ้าไม่มีใช้ SQLite)

    รองรับการกรอง:
    - user: "all" (ค่าเริ่มต้น) / "owner" (รวม boss) / "staff"
    - keyword: ค้นหาจาก action_type, description, performed_by
    """
    user = (user or "").strip().lower()
    keyword = (keyword or "").strip().lower()

    def _matches(row: dict) -> bool:
        by = str(row.get("performed_by") or "").strip().lower()
        if user and user not in ("all", "ทุกคน", "ทั้งหมด"):
            if user in ("owner", "boss", "เจ้าของ"):
                if by not in ("owner", "boss"):
                    return False
            elif by != user:
                return False
        if keyword:
            hay = " ".join([
                str(row.get("action_type") or ""),
                str(row.get("description") or ""),
                by,
            ]).lower()
            if keyword not in hay:
                return False
        return True

    if supabase_admin:
        try:
            res = supabase_admin.from_("audit_log").select("*").order("timestamp", desc=True).limit(limit).execute()
            if res.data and len(res.data) > 0:
                return [r for r in res.data if _matches(r)]
        except Exception:
            pass

    try:
        with db_session() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows if _matches(dict(r))]
    except Exception as e:
        logger.warning(f"Failed to read audit logs: {e}")
        return []


# ============================================================
# MULTI-KEYWORD SEARCH HELPER
# ============================================================

def build_multi_keyword_search(keyword: str, fields: list) -> tuple:
    """
    แยกคำค้นหาด้วย space และสร้าง WHERE clause สำหรับหลายฟิลด์
    รองรับการค้นหาผสมภาษาไทย-อังกฤษ, บาร์โค้ด, SKU, ตำแหน่งชั้นวาง
    """
    if not keyword:
        return "", []
    
    keywords = keyword.strip().split()
    conditions = []
    params = []
    
    for kw in keywords:
        kw_cond = []
        for field in fields:
            kw_cond.append(f"{field} LIKE ?")
            params.append(f"%{kw}%")
        conditions.append("(" + " OR ".join(kw_cond) + ")")
    
    if conditions:
        return " AND ".join(conditions), params
    return "", []


# ============================================================
# STAFF FUNCTIONS (ห้ามเข้าถึงต้นทุนเด็ดขาด!)
# ============================================================

def list_products_staff(keyword=None, location_code=None, category=None):
    """
    ดึงรายการสินค้าสำหรับ Staff
    *** กฎเหล็ก: ปิดกั้นการมองเห็น latest_cost / cost_price โดยเด็ดขาด ***
    รองรับ Multi-Keyword Search
    *** รวมผลลัพธ์จากทั้ง Supabase และ SQLite เพื่อให้สินค้าที่บันทึกแล้วแสดงผลเสมอ ***
    """
    products = []

    # 1. ดึงจาก Supabase (ถ้าพร้อมใช้งาน)
    if supabase_admin:
        try:
            query = supabase_admin.from_("products").select("*").eq("status", "active")
            if keyword:
                kw_parts = keyword.strip().split()
                for kw in kw_parts:
                    query = query.or_(f"name.ilike.%{kw}%,sku.ilike.%{kw}%,location_code.ilike.%{kw}%")
            if location_code:
                query = query.eq("location_code", location_code)
            if category:
                query = query.eq("category", category)

            res = query.order("name").execute()
            for item in res.data:
                # STRIP COST DATA for staff view - security rule
                item.pop("cost_price", None)
                item.pop("latest_cost", None)
                img = item.get("image_url") or item.get("image_path") or ""
                loc_img = item.get("location_image_url") or item.get("location_image_path") or ""
                item["image_url"] = img
                item["image_path"] = img
                item["location_image_url"] = loc_img
                item["location_image_path"] = loc_img
                if "min_stock" not in item:
                    item["min_stock"] = 5
                if "description" not in item:
                    item["description"] = ""
                if "location" not in item:
                    item["location"] = ""
                products.append(item)
            logger.info(f"[SUPABASE] list_products_staff: found {len(products)} products (cost data stripped)")
            # Supabase is the source of truth. Do not mix local seeded/demo data
            # into the live catalogue when the cloud query succeeds.
            return products
        except Exception as e:
            import traceback
            logger.error(f"[SUPABASE] list_products_staff query FAILED: {e}")
            logger.error(f"[SUPABASE] Traceback:\n{traceback.format_exc()}")
            logger.error(f"[SUPABASE] Falling back to local DB")

    # 2. ดึงจาก SQLite local (เพื่อรวมสินค้าที่บันทึกผ่าน fallback)
    try:
        query = """
            SELECT id, sku, name, category, sale_price, stock_qty, location_code, location, min_stock, description,
                   image_path, location_image_path, status, created_at, updated_at
            FROM products 
            WHERE status = 'active'
        """
        params = []
        if keyword:
            kw_condition, kw_params = build_multi_keyword_search(
                keyword, ["name", "sku", "location_code", "location", "category"]
            )
            if kw_condition:
                query += f" AND {kw_condition}"
                params.extend(kw_params)
        if location_code:
            query += " AND location_code = ?"
            params.append(location_code)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY name"

        with db_session() as conn:
            rows = conn.execute(query, params).fetchall()
            for r in rows:
                p = dict(r)
                img = p.get("image_path") or p.get("image_url") or ""
                loc_img = p.get("location_image_path") or p.get("location_image_url") or ""
                p["image_url"] = img
                p["image_path"] = img
                p["location_image_url"] = loc_img
                p["location_image_path"] = loc_img
                p.pop("latest_cost", None)
                p.pop("cost_price", None)
                products.append(p)
    except Exception as e:
        logger.warning(f"SQLite list_products_staff failed: {e}")

    # 3. Deduplicate by id (Supabase มาก่อน)
    seen = set()
    unique = []
    for p in products:
        pid = p.get("id")
        if pid in seen:
            continue
        seen.add(pid)
        unique.append(p)

    return unique


def get_product_staff(product_id: int):
    """ดึงข้อมูลสินค้าชิ้นเดียวสำหรับ Staff (ไม่มีราคาต้นทุน)"""
    if supabase_admin:
        try:
            res = supabase_admin.from_("products").select("*").eq("id", product_id).execute()
            if res.data:
                p = res.data[0]
                # STRIP COST DATA for staff view - security rule
                p.pop("cost_price", None)
                p.pop("latest_cost", None)
                
                img = p.get("image_url") or p.get("image_path") or ""
                loc_img = p.get("location_image_url") or p.get("location_image_path") or ""
                p["image_url"] = img
                p["image_path"] = img
                p["location_image_url"] = loc_img
                p["location_image_path"] = loc_img
                # ป้องกัน KeyError ถ้าคอลัมน์ migration ยังไม่มี
                if "min_stock" not in p:
                    p["min_stock"] = 5
                if "description" not in p:
                    p["description"] = ""
                if "location" not in p:
                    p["location"] = ""
                return p
        except Exception as e:
            logger.error(f"Supabase get_product_staff query failed: {e} — falling back to local DB")

    with db_session() as conn:
        row = conn.execute(
            """SELECT id, sku, name, category, sale_price, stock_qty, location_code, location, min_stock, description,
                      image_path, location_image_path, status, created_at, updated_at 
               FROM products WHERE id = ?""", 
            (product_id,)
        ).fetchone()
        if row:
            p = dict(row)
            # STRIP COST DATA for staff view - security rule
            p.pop("latest_cost", None)
            p.pop("cost_price", None)
            return p
        return None


def add_product_staff(name, sale_price=0, cost_price=0, category=None, sku=None,
                      location_code=None, location="", description="",
                      image_path=None, location_image_path=None, stock_qty=0, min_stock=5,
                      front_stock=0, warehouse_stock=0, performed_by="staff"):
    """
    Staff เพิ่มสินค้าใหม่เข้าคลัง (สินค้ามีสถานะ active ทันที)
    รองรับการแยกสต็อก:
    - front_stock = สต็อกหน้าร้าน
    - warehouse_stock = สต็อกคลังหลังร้าน
    - stock_qty = front_stock + warehouse_stock (คำนวณอัตโนมัติ)
    """
    # ถ้าไม่ได้ระบุ front/warehouse แยก ให้ใช้ stock_qty ทั้งหมดไปที่ front_stock
    if front_stock == 0 and warehouse_stock == 0:
        front_stock = int(stock_qty or 0)
        warehouse_stock = 0
    else:
        front_stock = int(front_stock or 0)
        warehouse_stock = int(warehouse_stock or 0)

    # stock_qty = ผลรวม (เผื่อ database ไม่มี trigger)
    total_stock = front_stock + warehouse_stock

    if supabase_admin:
        try:
            payload = {
                "name": name,
                "sale_price": float(sale_price or 0),
                "cost_price": float(cost_price or 0),
                "category": category or None,
                "sku": sku or None,
                "location_code": location_code or None,
                "location": location or "",
                "description": description or "",
                "image_url": image_path,
                "location_image_url": location_image_path,
                "stock_qty": total_stock,
                "min_stock": int(min_stock or 5),
                "status": "active"
            }
            # รวม front_stock / warehouse_stock ถ้ามีคอลัมน์ใน Supabase (Migration 003)
            if supabase_admin:
                try:
                    # ตรวจสอบว่าคอลัมน์ front_stock มีจริงไหม โดยลอง query
                    test = supabase_admin.from_("products").select("front_stock").limit(1).execute()
                    payload["front_stock"] = front_stock
                    payload["warehouse_stock"] = warehouse_stock
                except Exception:
                    # คอลัมน์ยังไม่มีบน Supabase — ใช้ stock_qty อย่างเดียว
                    pass

            # กรองเฉพาะคอลัมน์ที่มีอยู่จริงบน Supabase (รวม migration 002 + 003)
            allowed_columns = SUPABASE_PRODUCT_COLUMNS | SUPABASE_MIGRATED_COLUMNS | SUPABASE_STOCK_COLUMNS
            safe_payload = {k: v for k, v in payload.items() if k in allowed_columns}
            logger.info(f"[SUPABASE] Inserting product payload: {safe_payload}")
            res = supabase_admin.from_("products").insert(safe_payload).execute()
            if res.data:
                pid = res.data[0]["id"]
                logger.info(f"[SUPABASE] Insert success! product_id={pid}")
                add_audit_log("ลงสินค้า", f"{_actor_name(performed_by)} ได้ทำการลงสินค้า '{name}' (SKU: {sku or 'ยังไม่ระบุ'})", performed_by)
                return pid
            else:
                logger.warning(f"[SUPABASE] Insert returned empty data. Response: {res}")
        except Exception as e:
            import traceback
            logger.error(f"[SUPABASE] Insert FAILED for product '{name}': {e}")
            logger.error(f"[SUPABASE] Traceback:\n{traceback.format_exc()}")
            logger.error(f"[SUPABASE] URL={supabase_admin.supabase_url if hasattr(supabase_admin, 'supabase_url') else 'N/A'}")
            logger.error(f"[SUPABASE] Payload sent: {safe_payload}")
            logger.error(f"[SUPABASE] RLS check: ถ้า error เป็น 'permission denied' หรือ 'new row violates row-level security policy' แปลว่า RLS ยังบล็อกอยู่ — ต้องรัน fix_rls_permissions.sql ใน Supabase SQL Editor")

    with db_session() as conn:
        cur = conn.execute(
            """INSERT INTO products (sku, name, category, sale_price, location_code, location, description, image_path, location_image_path, stock_qty, min_stock, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (sku, name, category, sale_price, location_code, location, description, image_path, location_image_path, total_stock, min_stock),
        )
        pid = cur.lastrowid

        # อัปเดต front_stock / warehouse_stock แยก (ถ้าตาราง SQLite มีคอลัมน์)
        try:
            conn.execute(
                "UPDATE products SET front_stock = ?, warehouse_stock = ? WHERE id = ?",
                (front_stock, warehouse_stock, pid)
            )
        except Exception:
            pass

        add_audit_log("ลงสินค้า", f"{_actor_name(performed_by)} ได้ทำการลงสินค้า '{name}' (SKU: {sku or 'ยังไม่ระบุ'})", performed_by)

        if location_code and location_image_path:
            conn.execute(
                """INSERT INTO locations (code, image_path) VALUES (?, ?)
                   ON CONFLICT(code) DO UPDATE SET image_path=excluded.image_path""",
                (location_code, location_image_path)
            )
        return pid


def update_product_staff(product_id: int, performed_by: str = "staff", **fields):
    """
    แก้ไขข้อมูลสินค้า
    - Staff: ไม่อนุญาตให้แก้ไข cost_price
    - Owner: แก้ไข cost_price ได้ (ผ่าน allow_cost_price=True)
    - ค่ารวมถึง '' (ค่าว่าง) จะถูกอัปเดตจริง — เมื่อผู้ใช้ลบข้อความแล้วกดบันทึก ค่าใน DB จะถูกลบออก (เซ็ตเป็น NULL) ด้วย
    """
    # ถ้าไม่มีการส่ง allow_cost_price=True จะกรอง cost_price ออก
    allow_cost_price = fields.pop("allow_cost_price", False)

    allowed_keys = {
        "name", "category", "sku", "sale_price", "stock_qty", "cost_price",
        "location_code", "location", "description", "min_stock",
        "image_path", "location_image_path",
        "front_stock", "warehouse_stock"
    }

    # ฟิลด์ข้อความและรูปภาพที่สามารถเป็นค่าว่างได้ — เมื่อผู้ใช้ลบข้อความ
    # จะแปลงค่าว่าง "" ให้เป็น None (NULL) ในฐานข้อมูล
    TEXT_FIELDS_NULLABLE = {
        "sku", "category", "location_code", "location", "description",
        "image_path", "location_image_path"
    }

    filtered_fields = {}
    for k, v in fields.items():
        if k not in allowed_keys:
            continue
        if k == "name":
            # ชื่อสินค้าเป็นฟิลด์บังคับ ไม่ให้เซ็ตเป็นค่าว่างหรือ NULL
            if isinstance(v, str) and v.strip():
                filtered_fields["name"] = v.strip()
            continue

        if isinstance(v, str):
            # แปลงค่าว่าง "" หรือช่องว่างล้วน -> None (NULL) สำหรับฟิลด์ที่อนุญาต
            if k in TEXT_FIELDS_NULLABLE and v.strip() == "":
                filtered_fields[k] = None
            elif k in TEXT_FIELDS_NULLABLE:
                filtered_fields[k] = v.strip()
            else:
                filtered_fields[k] = v
        elif v is None:
            if k in TEXT_FIELDS_NULLABLE:
                filtered_fields[k] = None
        else:
            filtered_fields[k] = v

    # Staff ไม่มีสิทธิ์แก้ไขต้นทุน (กรอง cost_price ออก)
    if not allow_cost_price:
        filtered_fields.pop("cost_price", None)

    # คำนวณ stock_qty ใหม่ถ้ามีการส่ง front_stock / warehouse_stock
    if "front_stock" in filtered_fields or "warehouse_stock" in filtered_fields:
        f_stock = int(filtered_fields.get("front_stock", 0) or 0)
        w_stock = int(filtered_fields.get("warehouse_stock", 0) or 0)
        filtered_fields["stock_qty"] = f_stock + w_stock

    if not filtered_fields:
        return

    # Auto-Complete: ถ้าสินค้าเดิมยัง "ลงไม่ครบ" (is_complete = 0) และครั้งนี้มีการกรอก
    # SKU + ราคาขาย (+ รูปสินค้า) ครบ ให้เปลี่ยนสถานะเป็นลงครบแล้ว (is_complete = 1)
    try:
        current = None
        if supabase_admin:
            try:
                r = supabase_admin.from_("products").select("is_complete, image_url, sku, sale_price").eq("id", product_id).single().execute()
                if r.data:
                    current = {
                        "is_complete": r.data.get("is_complete"),
                        "image_path": r.data.get("image_url"),
                        "sku": r.data.get("sku"),
                        "sale_price": r.data.get("sale_price"),
                    }
            except Exception:
                current = None
        if current is None:
            with db_session() as conn:
                row = conn.execute(
                    "SELECT is_complete, image_path, location_image_path, sku, sale_price FROM products WHERE id = ?",
                    (product_id,),
                ).fetchone()
                if row is not None:
                    current = dict(row)
        if current is not None and not int(current.get("is_complete") or 0):
            new_sku = filtered_fields.get("sku", current.get("sku"))
            new_price = filtered_fields.get("sale_price", current.get("sale_price"))
            new_img = filtered_fields.get("image_path", current.get("image_path"))
            has_sku = bool(new_sku and str(new_sku).strip())
            has_price = float(new_price or 0) > 0
            has_img = bool(new_img)
            if has_sku and has_price and has_img:
                filtered_fields["is_complete"] = 1
    except Exception as comp_err:
        logger.warning(f"Auto-complete check skipped for product id={product_id}: {comp_err}")

    if supabase_admin:
        try:
            sp_payload = {}
            # ฟิลด์ข้อความที่ Supabase อาจมี NOT NULL constraint — ส่ง "" แทน None เสมอ
            # หมายเหตุ: sku ไม่รวมในนี้เพราะเป็น UNIQUE column — ถ้าหลายสินค้า sku="" จะชน constraint
            TEXT_FIELDS_NOTNULL = {"description", "location", "location_code", "category"}
            for k, v in filtered_fields.items():
                if k == "image_path":
                    sp_payload["image_url"] = v if v is not None else ""
                elif k == "location_image_path":
                    sp_payload["location_image_url"] = v if v is not None else ""
                elif v is None and k in TEXT_FIELDS_NOTNULL:
                    # ผู้ใช้ลบข้อความ → ส่งค่าว่าง "" ไม่ส่ง NULL เพราะ Supabase มี NOT NULL
                    sp_payload[k] = ""
                else:
                    sp_payload[k] = v
            # กรองเฉพาะคอลัมน์ที่มีอยู่จริงบน Supabase (รวม migration 002 + 003)
            allowed_cols = SUPABASE_PRODUCT_COLUMNS | SUPABASE_MIGRATED_COLUMNS | SUPABASE_STOCK_COLUMNS
            safe_payload = {k: v for k, v in sp_payload.items() if k in allowed_cols}
            logger.info(f"[SUPABASE] Updating product id={product_id} payload: {safe_payload}")
            res = supabase_admin.from_("products").update(safe_payload).eq("id", product_id).execute()
            if res.data:
                logger.info(f"[SUPABASE] Update success for id={product_id}, updated fields: {list(safe_payload.keys())}")
                add_audit_log("แก้ไขสินค้า", f"แก้ไขสินค้า id={product_id}: {', '.join(filtered_fields.keys())}", performed_by)
                return
            else:
                # Supabase updated 0 rows — อาจเกิดจาก RLS, product_id ไม่มี, หรือ service role key ผิด
                logger.warning(f"[SUPABASE] Update returned 0 rows for id={product_id}. Check RLS policies or service role key. Payload was: {safe_payload}")
                raise Exception(f"Supabase update 0 rows for id={product_id} — RLS อาจบล็อก หรือ SUPABASE_SERVICE_ROLE_KEY ไม่ถูกต้อง")
        except Exception as e:
            import traceback
            logger.error(f"[SUPABASE] Update FAILED for product id={product_id}: {e}")
            logger.error(f"[SUPABASE] Traceback:\n{traceback.format_exc()}")
            logger.error(f"[SUPABASE] Payload sent: {safe_payload}")
            logger.error(f"[SUPABASE] ตรวจสอบว่า product id={product_id} มีอยู่จริงบน Supabase หรือ RLS บล็อก UPDATE")

    set_clause = ", ".join(f"{k} = ?" for k in filtered_fields)
    set_clause += ", updated_at = datetime('now', 'localtime')"
    values = list(filtered_fields.values())
    values.append(product_id)
    with db_session() as conn:
        conn.execute(f"UPDATE products SET {set_clause} WHERE id = ?", values)
        add_audit_log("แก้ไขสินค้า", f"แก้ไขสินค้า id={product_id}: {', '.join(filtered_fields.keys())}", performed_by)


def transfer_stock(product_id: int, qty: int, direction: str = "to_front", performed_by: str = "staff"):
    """
    ย้ายสต็อกระหว่างหน้าร้าน (front_stock) กับคลังหลังร้าน (warehouse_stock)
    - direction="to_front": ย้ายจากคลังหลังร้าน -> หน้าร้าน
    - direction="to_warehouse": ย้ายจากหน้าร้าน -> คลังหลังร้าน
    """
    if not product_id or qty <= 0:
        raise ValueError("กรุณาระบุสินค้าและจำนวนที่ถูกต้อง")

    if supabase_admin:
        try:
            # ดึงข้อมูลสต็อกปัจจุบัน
            p = supabase_admin.from_("products").select("front_stock, warehouse_stock, name").eq("id", product_id).single().execute()
            if not p.data:
                raise ValueError(f"ไม่พบสินค้า id={product_id}")

            current_front = int(p.data.get("front_stock", 0) or 0)
            current_warehouse = int(p.data.get("warehouse_stock", 0) or 0)
            name = p.data.get("name", f"id={product_id}")

            if direction == "to_front":
                # คลัง -> หน้าร้าน
                if qty > current_warehouse:
                    raise ValueError(f"สต็อกคลังหลังร้านไม่พอ (มี {current_warehouse} ชิ้น)")
                new_front = current_front + qty
                new_warehouse = current_warehouse - qty
            else:
                # หน้าร้าน -> คลัง
                if qty > current_front:
                    raise ValueError(f"สต็อกหน้าร้านไม่พอ (มี {current_front} ชิ้น)")
                new_front = current_front - qty
                new_warehouse = current_warehouse + qty

            # อัปเดต Supabase
            supabase_admin.from_("products").update({
                "front_stock": new_front,
                "warehouse_stock": new_warehouse,
                "stock_qty": new_front + new_warehouse
            }).eq("id", product_id).execute()

            add_audit_log("ย้ายสต็อก", f"ย้ายสต็อก '{name}' จำนวน {qty} ชิ้น ({'คลัง->หน้าร้าน' if direction=='to_front' else 'หน้าร้าน->คลัง'})", performed_by)
            return {"status": "ok", "front_stock": new_front, "warehouse_stock": new_warehouse}
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"[SUPABASE] Transfer stock FAILED for id={product_id}: {e}")
            # Fall through to SQLite

    with db_session() as conn:
        p = conn.execute(
            "SELECT front_stock, warehouse_stock, name FROM products WHERE id = ?",
            (product_id,)
        ).fetchone()
        if not p:
            raise ValueError(f"ไม่พบสินค้า id={product_id}")

        current_front = int(p["front_stock"] or 0) if p["front_stock"] is not None else int(p["stock_qty"] or 0)
        current_warehouse = int(p["warehouse_stock"] or 0) if p["warehouse_stock"] is not None else 0
        name = p["name"]

        if direction == "to_front":
            if qty > current_warehouse:
                raise ValueError(f"สต็อกคลังหลังร้านไม่พอ (มี {current_warehouse} ชิ้น)")
            new_front = current_front + qty
            new_warehouse = current_warehouse - qty
        else:
            if qty > current_front:
                raise ValueError(f"สต็อกหน้าร้านไม่พอ (มี {current_front} ชิ้น)")
            new_front = current_front - qty
            new_warehouse = current_warehouse + qty

        try:
            conn.execute(
                """UPDATE products
                   SET front_stock = ?, warehouse_stock = ?, stock_qty = ?,
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (new_front, new_warehouse, new_front + new_warehouse, product_id)
            )
        except Exception:
            # SQLite ยังไม่มีคอลัมน์ front_stock/warehouse_stock
            conn.execute(
                """UPDATE products
                   SET stock_qty = MAX(0, stock_qty + ?),
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (qty if direction == "to_front" else -qty, product_id)
            )
            new_front = None
            new_warehouse = None

        add_audit_log("ย้ายสต็อก", f"ย้ายสต็อก '{name}' จำนวน {qty} ชิ้น ({'คลัง->หน้าร้าน' if direction=='to_front' else 'หน้าร้าน->คลัง'})", performed_by)
        return {"status": "ok", "front_stock": new_front, "warehouse_stock": new_warehouse}


def delete_product_staff(product_id: int, performed_by: str = "staff"):
    """
    Owner ลบสินค้า (ทั้งจาก Supabase และ SQLite)
    ลบ record ที่เกี่ยวข้องใน cost_history ด้วย (ON DELETE CASCADE)
    """
    # 1. ตรวจสอบว่าสินค้ามีอยู่จริง
    deleted = False

    # 2. ลบจาก Supabase (ถ้าพร้อมใช้งาน)
    if supabase_admin:
        try:
            # ตรวจสอบว่ามีสินค้าอยู่ก่อนลบ
            check = supabase_admin.from_("products").select("id, name").eq("id", product_id).execute()
            if check.data:
                product_name = check.data[0].get("name", f"id={product_id}")
                logger.info(f"[SUPABASE] Deleting product id={product_id} name='{product_name}'")
                supabase_admin.from_("products").delete().eq("id", product_id).execute()
                deleted = True
                add_audit_log("PRODUCT_DELETE", f"ลบสินค้า '{product_name}' (id={product_id}) จาก Supabase", performed_by)
                logger.info(f"Deleted product id={product_id} from Supabase")
        except Exception as e:
            import traceback
            logger.error(f"[SUPABASE] DELETE FAILED for product id={product_id}: {e}")
            logger.error(f"[SUPABASE] Traceback:\n{traceback.format_exc()}")
            logger.error(f"[SUPABASE] RLS check: DELETE policy อาจไม่มีสำหรับ role นี้ — ต้องรัน fix_rls_permissions.sql")

    # 3. ลบจาก SQLite local fallback (เสมอ เพื่อให้ sync กัน)
    try:
        with db_session() as conn:
            # ตรวจสอบว่ามีสินค้า
            p = conn.execute(
                "SELECT name FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if p:
                pname = p["name"]
                conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
                # cost_history ตารางมี ON DELETE CASCADE อยู่แล้ว แต่ลบตรงๆ เผื่อไว้
                conn.execute("DELETE FROM cost_history WHERE product_id = ?", (product_id,))
                conn.execute("DELETE FROM receipt_items WHERE product_id = ?", (product_id,))
                deleted = True
                add_audit_log("PRODUCT_DELETE", f"ลบสินค้า '{pname}' (id={product_id}) จาก SQLite", performed_by)
                logger.info(f"Deleted product id={product_id} from SQLite")
    except Exception as e:
        logger.error(f"[DELETE] SQLite delete failed for product id={product_id}: {e}")

    if not deleted:
        raise ValueError(f"ไม่พบสินค้า id={product_id}")

    return product_id


def process_checkout(cart_items: list, payment_type: str = "cash", total_amount: float = 0.0, received_amount: float = None, change_amount: float = 0.0, sold_by: str = "staff"):
    """
    ประมวลผลการชำระเงินหน้าร้าน ตัดสต็อกสินค้าในคลัง และบันทึกประวัติลงตาราง sales
    cart_items: [{"product_id": 1, "qty": 2}, ...]
    """
    if not cart_items:
        raise ValueError("ตะกร้าสินค้าว่างเปล่า")

    if received_amount is None:
        received_amount = total_amount

    # สร้างเลขที่บิลขาย
    receipt_no = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    items_detailed = []
    total_qty = 0

    with db_session() as conn:
        for item in cart_items:
            pid = item.get("product_id")
            qty = int(item.get("qty", 1))

            if not pid or qty <= 0:
                continue

            total_qty += qty

            # ดึงข้อมูลสินค้าเพื่อเก็บในบิล
            p = conn.execute("SELECT id, name, sku, sale_price, category, COALESCE(latest_cost, 0) AS latest_cost FROM products WHERE id = ?", (pid,)).fetchone()
            if p:
                pname = p["name"]
                price = float(p["sale_price"] or 0.0)
                sku = p["sku"] or ""
                unit_cost = float(p["latest_cost"] or 0.0)
            else:
                pname = f"สินค้า id={pid}"
                price = 0.0
                sku = ""
                unit_cost = 0.0

            items_detailed.append({
                "product_id": pid,
                "name": pname,
                "sku": sku,
                "qty": qty,
                "price": price,
                "unit_cost": unit_cost,
                "line_total": price * qty
            })

            # Local SQLite update stock
            conn.execute(
                """UPDATE products 
                   SET stock_qty = MAX(0, stock_qty - ?),
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (qty, pid)
            )

            # Supabase update if active
            if supabase_admin:
                try:
                    sp_prod = supabase_admin.from_("products").select("stock_qty").eq("id", pid).single().execute()
                    if sp_prod.data:
                        current_stock = sp_prod.data.get("stock_qty", 0)
                        new_stock = max(0, current_stock - qty)
                        supabase_admin.from_("products").update({"stock_qty": new_stock}).eq("id", pid).execute()
                except Exception as e:
                    logger.warning(f"Supabase checkout stock update failed for id={pid}: {e}")

        # ถ้าฝั่งหน้าบ้านไม่ได้ส่งยอดรวมมา (หรือส่งมาเป็น 0) ให้คำนวณจากราคาสินค้าจริงในบิล
        computed_total = sum(float(i["line_total"]) for i in items_detailed)
        if float(total_amount or 0) <= 0:
            total_amount = round(computed_total, 2)
        if received_amount is None or float(received_amount or 0) <= 0:
            received_amount = total_amount

        # บันทึกลงตาราง sales ใน SQLite
        items_json_str = json.dumps(items_detailed, ensure_ascii=False)
        conn.execute(
            """INSERT INTO sales (receipt_no, total_amount, received_amount, change_amount, payment_type, items_json, items_count, sold_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))""",
            (receipt_no, total_amount, received_amount, change_amount, payment_type, items_json_str, total_qty, sold_by)
        )

        # บันทึกลง Supabase sales ถ้ามีตารางรองรับ
        if supabase_admin:
            try:
                supabase_admin.from_("sales").insert({
                    "receipt_no": receipt_no,
                    "total_amount": total_amount,
                    "received_amount": received_amount,
                    "change_amount": change_amount,
                    "payment_type": payment_type,
                    "items_json": items_json_str,
                    "items_count": total_qty,
                    "sold_by": sold_by,
                    "created_at": datetime.now().isoformat()
                }).execute()
            except Exception as sp_err:
                logger.info(f"Supabase sales table insert skipped: {sp_err}")

        # Audit Log: บันทึกทุกรายการที่ขาย ระบุผู้ขาย ชื่อสินค้า จำนวนชิ้น และยอดเงิน
        sale_actor = _actor_name(sold_by)
        for item in items_detailed:
            add_audit_log(
                "ขายสินค้า",
                f"{sale_actor} ได้ทำการขายสินค้า '{item['name']}' จำนวน {int(item['qty'])} ชิ้น เป็นเงิน {item['line_total']:,.2f} บาท (บิล {receipt_no})",
                sold_by
            )

    return {
        "status": "ok",
        "receipt_no": receipt_no,
        "total_amount": total_amount,
        "items_count": total_qty,
        "message": "บันทึกการขายและตัดสต็อกเรียบร้อยแล้ว"
    }


def list_sales(limit: int = 100, keyword: str = None):
    """ดึงรายงานประวัติการขายทั้งหมด"""
    # 1. ลองดึงจาก Supabase ก่อน
    if supabase_admin:
        try:
            q = supabase_admin.from_("sales").select("*").order("created_at", desc=True).limit(limit)
            if keyword:
                kw = keyword.strip()
                q = q.or_(f"receipt_no.ilike.%{kw}%,sold_by.ilike.%{kw}%,payment_type.ilike.%{kw}%,items_json.ilike.%{kw}%")
            res = q.execute()
            if res.data and len(res.data) > 0:
                sales = []
                for r in res.data:
                    items = []
                    try:
                        items = json.loads(r.get("items_json") or "[]")
                    except Exception:
                        pass
                    r["items"] = items
                    sales.append(r)
                return sales
        except Exception:
            pass

    # 2. SQLite local fallback
    try:
        with db_session() as conn:
            query = "SELECT * FROM sales"
            params = []
            if keyword:
                kw = f"%{keyword.strip()}%"
                query += " WHERE receipt_no LIKE ? OR sold_by LIKE ? OR payment_type LIKE ? OR items_json LIKE ?"
                params.extend([kw, kw, kw, kw])
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            sales = []
            for r in rows:
                item_dict = dict(r)
                items = []
                try:
                    items = json.loads(item_dict.get("items_json") or "[]")
                except Exception:
                    pass
                item_dict["items"] = items
                sales.append(item_dict)
            return sales
    except Exception as e:
        logger.warning(f"Failed to read sales history: {e}")
        return []


def get_sales_summary():
    """ดึงสถิติสรุปยอดขาย (วันนี้ และ ยอดรวมทั้งหมด)"""
    summary = {
        "today_sales": 0.0,
        "today_orders": 0,
        "total_sales": 0.0,
        "total_orders": 0,
        "total_items_sold": 0
    }
    try:
        with db_session() as conn:
            row_all = conn.execute(
                "SELECT COALESCE(SUM(total_amount), 0) as total_rev, COUNT(*) as total_cnt, COALESCE(SUM(items_count), 0) as total_items FROM sales"
            ).fetchone()
            if row_all:
                summary["total_sales"] = float(row_all["total_rev"] or 0)
                summary["total_orders"] = int(row_all["total_cnt"] or 0)
                summary["total_items_sold"] = int(row_all["total_items"] or 0)

            row_today = conn.execute(
                "SELECT COALESCE(SUM(total_amount), 0) as today_rev, COUNT(*) as today_cnt FROM sales WHERE date(created_at) = date('now', 'localtime')",
            ).fetchone()
            if row_today:
                summary["today_sales"] = float(row_today["today_rev"] or 0)
                summary["today_orders"] = int(row_today["today_cnt"] or 0)
    except Exception as e:
        logger.warning(f"Failed to get sales summary: {e}")
    return summary


# ============================================================
# TODAY'S SALES REPORT (จากตาราง sales เท่านั้น - ข้อมูลจริงประจำวัน)
# ============================================================

def _sale_line_amount(sale: dict) -> float:
    """คำนวณยอดเงินของบิล 1 ใบ

    ใช้ total_amount ของบิลเป็นหลัก ถ้าบิลนั้นมียอดรวมเป็น 0 (ข้อมูลเก่า/บันทึกไม่ครบ)
    จะคำนวณยอดจากผลรวมราคารวมของทุกรายการในบิล (line_total) ให้อัตโนมัติ
    เพื่อไม่ให้ยอดขายขึ้น 0 บาท
    """
    items = sale.get("items") or []
    lines_sum = 0.0
    for it in items:
        qty = float(it.get("qty") or 0)
        line_total = float(it.get("line_total") or 0)
        if line_total <= 0:
            line_total = float(it.get("price") or 0) * qty
        lines_sum += line_total
    amount = float(sale.get("total_amount") or 0)
    if amount <= 0:
        amount = lines_sum
    return round(amount, 2)


def get_today_sales_summary(keyword: str = None):
    """
    สรุปรายงานยอดขายของวันนี้ (Today's Sales) ตามเขตเวลาไทย (Asia/Bangkok)
    อ่านจากตาราง sales / transactions เท่านั้น
    - ห้ามนำรายการสินค้าที่เพิ่งเพิ่มเข้าคลัง (products) มาแสดง
    - ยอดขายคำนวณจาก total_amount ของบิลจริง ถ้าบิลมียอด 0 จะคำนวณจากผลรวม line_total ของทุกรายการให้เอง
    - คืนค่า: ยอดขายรวม, จำนวนบิล, จำนวนชิ้นที่ขายได้, รายการสินค้าที่ขายได้จริงวันนี้ (Grouped), และรายการบิลวันนี้
    """
    keyword = (keyword or "").strip().lower()
    today_str = bangkok_today_str()
    today_data = {
        "date": today_str,
        "timezone": "Asia/Bangkok",
        "total_sales": 0.0,
        "total_cost": 0.0,
        "net_profit": 0.0,
        "total_orders": 0,
        "total_items_sold": 0,
        "items": [],   # สรุปรายการสินค้าที่ขายได้จริงวันนี้
        "bills": [],   # รายการบิลขายที่เกิดขึ้นวันนี้
    }

    bills = []

    # 1) ลอง Supabase ก่อน (ถ้ามี cloud connection) — เทียบช่วงเวลาไทย -> UTC
    if supabase_admin:
        try:
            day_start = datetime.strptime(today_str, "%Y-%m-%d").replace(tzinfo=BANGKOK_TZ)
            day_end = day_start + timedelta(days=1)
            res = (
                supabase_admin.from_("sales")
                .select("*")
                .gte("created_at", day_start.astimezone(timezone.utc).isoformat())
                .lt("created_at", day_end.astimezone(timezone.utc).isoformat())
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )
            for r in (res.data or []):
                # ข้ามบิลที่ถูกคืนของ (void) ไปแล้ว
                if r.get("voided"):
                    continue
                # กันคลาดเคลื่อนเรื่อง timezone — ตรวจซ้ำว่าเป็นวันนี้ (เวลาไทย) จริง
                if str(to_bangkok_date(r.get("created_at"))) != today_str:
                    continue
                try:
                    r["items"] = json.loads(r.get("items_json") or "[]")
                except Exception:
                    r["items"] = []
                bills.append(r)
        except Exception as sp_err:
            logger.info(f"Supabase today sales query skipped: {sp_err}")
            bills = []

    # 2) SQLite local fallback (ดึงย้อนหลัง 3 วัน แล้วกรองตามวันที่เวลาไทย)
    if not bills:
        try:
            with db_session() as conn:
                rows = conn.execute(
                    """SELECT * FROM sales
                       WHERE created_at >= datetime('now', 'localtime', '-3 days')
                       ORDER BY created_at DESC LIMIT 1000"""
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    # ข้ามบิลที่ถูกคืนของ (void) ไปแล้ว
                    if d.get("voided"):
                        continue
                    if str(to_bangkok_date(d.get("created_at"))) != today_str:
                        continue
                    try:
                        d["items"] = json.loads(d.get("items_json") or "[]")
                    except Exception:
                        d["items"] = []
                    bills.append(d)
        except Exception as e:
            logger.warning(f"Failed to read today sales: {e}")
            bills = []

    if not bills:
        return today_data

    items_agg = {}
    total_cost = 0.0

    # แผนที่ product_id -> ข้อมูลสินค้าจริง (แก้บิลเก่าที่เก็บชื่อเป็น "สินค้า id=xx" / ราคาเป็น 0)
    pids = set()
    for sale in bills:
        for it in (sale.get("items") or []):
            pid = it.get("product_id")
            if pid is not None:
                pids.add(str(pid))
    pmap = _get_product_info_map(pids)

    for sale in bills:
        sale_items = sale.get("items") or []
        amount = _sale_line_amount(sale)
        sale["total_amount"] = amount

        qty_count = int(sale.get("items_count") or 0)
        if qty_count <= 0:
            qty_count = int(sum(float(i.get("qty") or 0) for i in sale_items))
        sale["items_count"] = qty_count

        today_data["total_sales"] += amount
        today_data["total_orders"] += 1
        today_data["total_items_sold"] += qty_count

        for it in sale_items:
            pid = it.get("product_id")
            info = pmap.get(str(pid)) or {}

            # แก้ชื่อสินค้า: บิลเก่าอาจเก็บ "สินค้า id=69" — ดึงชื่อจริงจากตาราง products
            raw_name = str(it.get("name") or "").strip()
            name = raw_name
            if (not name) or name.startswith("สินค้า id=") or info.get("name"):
                name = (info.get("name") or name or "สินค้าไม่ทราบชื่อ").strip()

            qty = float(it.get("qty") or 0)
            price = float(it.get("price") or 0)
            if price <= 0:
                price = float(info.get("sale_price") or 0)
            line_total = float(it.get("line_total") or 0)
            if line_total <= 0:
                line_total = price * qty

            # ต้นทุนสินค้าที่ขาย (สำหรับคำนวณกำไรสุทธิ)
            unit_cost = float(it.get("unit_cost") or 0)
            if unit_cost <= 0:
                unit_cost = float(info.get("latest_cost") or 0)
            line_cost = unit_cost * qty
            total_cost += line_cost

            agg = items_agg.get(name)
            if agg is None:
                agg = {
                    "name": name,
                    "sku": it.get("sku") or info.get("sku") or "",
                    "qty": 0.0,
                    "revenue": 0.0,
                    "cost": 0.0,
                    "product_id": pid,
                }
                items_agg[name] = agg
            agg["qty"] += qty
            agg["revenue"] += line_total
            agg["cost"] += line_cost

            # เขียนข้อมูลที่แก้แล้วกลับลง sale_items เพื่อแสดงชื่อ/ราคาจริงในตารางบิล
            it["name"] = name
            it["sku"] = agg["sku"]
            it["price"] = price
            it["line_total"] = round(line_total, 2)
            it["unit_cost"] = unit_cost

    today_data["total_sales"] = round(today_data["total_sales"], 2)
    today_data["total_cost"] = round(total_cost, 2)
    today_data["net_profit"] = round(today_data["total_sales"] - total_cost, 2)
    today_data["items"] = sorted(items_agg.values(), key=lambda x: -x["qty"])

    if keyword:
        filtered_items = [
            it for it in today_data["items"]
            if keyword in str(it.get("name") or "").lower() or keyword in str(it.get("sku") or "").lower()
        ]
        if filtered_items:
            today_data["items"] = filtered_items

        filtered_bills = []
        for b in bills:
            hay = " ".join([
                str(b.get("receipt_no") or ""),
                str(b.get("sold_by") or ""),
                str(b.get("payment_type") or ""),
                str(b.get("items_json") or ""),
            ]).lower()
            if keyword in hay:
                filtered_bills.append(b)
        today_data["bills"] = filtered_bills
    else:
        today_data["bills"] = bills

    return today_data


def _get_product_info_map(pids) -> dict:
    """แผนที่ product_id -> {name, sku, sale_price, latest_cost}

    ใช้แก้บิลเก่าที่ items_json เก็บชื่อเป็น "สินค้า id=xx" หรือราคาเป็น 0
    โดยดึงชื่อ/ราคาจริงจากตาราง products (SQLite + Supabase fallback)
    """
    result = {}
    ids = []
    for p in (pids or []):
        s = str(p).strip()
        if not s or s in ("None", "null"):
            continue
        try:
            ids.append(int(s))
        except Exception:
            continue
    if not ids:
        return result
    try:
        with db_session() as conn:
            qmarks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"""SELECT id, name, sku, sale_price, COALESCE(latest_cost, 0) AS latest_cost
                    FROM products WHERE id IN ({qmarks})""",
                tuple(ids),
            ).fetchall()
            for r in rows:
                result[str(r["id"])] = {
                    "name": r["name"] or "",
                    "sku": r["sku"] or "",
                    "sale_price": float(r["sale_price"] or 0),
                    "latest_cost": float(r["latest_cost"] or 0),
                }
    except Exception as e:
        logger.warning(f"product info map (local) failed: {e}")

    # เติมชื่อจาก Supabase สำหรับ id ที่ยังไม่เจอใน local
    if supabase_admin:
        missing = [i for i in ids if str(i) not in result]
        if missing:
            try:
                res = (
                    supabase_admin.from_("products")
                    .select("id,name,sku,sale_price,cost_price")
                    .in_("id", missing)
                    .execute()
                )
                for r in (res.data or []):
                    result[str(r.get("id"))] = {
                        "name": r.get("name") or "",
                        "sku": r.get("sku") or "",
                        "sale_price": float(r.get("sale_price") or 0),
                        "latest_cost": float(r.get("cost_price") or 0),
                    }
            except Exception as e:
                logger.info(f"product info map (supabase) skipped: {e}")
    return result


def void_sale(sale_id: int, performed_by: str = "owner"):
    """คืนของ / ยกเลิกบิลขาย (Void Transaction)

    - ทำเครื่องหมายบิลว่าถูกคืนแล้ว (voided = 1) — ไม่ลบบิล เพื่อเก็บประวัติตรวจสอบได้
    - คืนสต็อกสินค้าทุกรายการในบิลเข้าคลัง (SQLite + Supabase)
    - บิลที่ถูก void จะไม่ถูกนับในรายงานยอดขายวันนี้อีกต่อไป
    """
    with db_session() as conn:
        row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if row is None:
            raise ValueError(f"ไม่พบบิล id={sale_id}")
        sale = dict(row)
        if int(sale.get("voided") or 0) == 1:
            raise ValueError("บิลนี้ถูกคืนของไปแล้ว")

        try:
            items = json.loads(sale.get("items_json") or "[]")
        except Exception:
            items = []

        restored = []
        for it in items:
            pid = it.get("product_id")
            qty = int(float(it.get("qty") or 0))
            name = str(it.get("name") or "").strip()
            if not pid or qty <= 0:
                continue
            # คืนสต็อก SQLite
            conn.execute(
                """UPDATE products
                   SET stock_qty = COALESCE(stock_qty, 0) + ?,
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (qty, pid),
            )
            restored.append({"product_id": pid, "name": name, "qty": qty})
            # คืนสต็อก Supabase
            if supabase_admin:
                try:
                    sp = (
                        supabase_admin.from_("products")
                        .select("stock_qty")
                        .eq("id", pid)
                        .single()
                        .execute()
                    )
                    if sp.data is not None:
                        cur = int(sp.data.get("stock_qty") or 0)
                        supabase_admin.from_("products").update(
                            {"stock_qty": cur + qty}
                        ).eq("id", pid).execute()
                except Exception as e:
                    logger.warning(f"Supabase restore stock failed id={pid}: {e}")

        conn.execute(
            "UPDATE sales SET voided = 1, voided_at = datetime('now', 'localtime') WHERE id = ?",
            (sale_id,),
        )

    actor = _actor_name(performed_by)
    items_desc = ", ".join(f"{r['name']} x{r['qty']}" for r in restored[:10])
    add_audit_log(
        "คืนของ / ยกเลิกบิล",
        (f"{actor} ได้ทำการคืนของบิล {sale.get('receipt_no') or ('#' + str(sale_id))} "
         f"ยอด {float(sale.get('total_amount') or 0):,.2f} บาท "
         f"(คืนสต็อก {sum(r['qty'] for r in restored)} ชิ้น: {items_desc})"),
        performed_by or "owner",
    )

    return {
        "status": "ok",
        "sale_id": sale_id,
        "receipt_no": sale.get("receipt_no"),
        "restored_amount": float(sale.get("total_amount") or 0),
        "restored_items": restored,
        "message": "คืนของและปรับสต็อกเรียบร้อยแล้ว",
    }







# ============================================================
# OWNER FUNCTIONS (มีสิทธิ์เข้าถึงต้นทุน + กำไร + สถิติรวม)
# ============================================================

def list_products_owner(keyword=None, location_code=None, category=None):
    """ดึงรายการสินค้าฉบับเต็มสำหรับ Owner (มีราคาต้นทุน + กำไร + มูลค่ารวม)"""
    if supabase_admin:
        try:
            query = supabase_admin.from_("products").select("*").eq("status", "active")
            if keyword:
                kw_parts = keyword.strip().split()
                for kw in kw_parts:
                    query = query.or_(f"name.ilike.%{kw}%,sku.ilike.%{kw}%,location_code.ilike.%{kw}%,location.ilike.%{kw}%")
            if location_code:
                query = query.eq("location_code", location_code)
            if category:
                query = query.eq("category", category)

            res = query.order("name").execute()
            products = []
            for item in res.data:
                latest_cost = float(item.get("cost_price", 0) or 0)
                sale_price = float(item.get("sale_price", 0) or 0)
                stock_qty = int(item.get("stock_qty", 0) or 0)
                profit = sale_price - latest_cost
                margin_pct = (profit / sale_price * 100) if sale_price > 0 else 0

                img = item.get("image_url") or item.get("image_path") or ""
                loc_img = item.get("location_image_url") or item.get("location_image_path") or ""

                item["latest_cost"] = latest_cost
                item["image_url"] = img
                item["image_path"] = img
                item["location_image_url"] = loc_img
                item["location_image_path"] = loc_img
                item["profit"] = profit
                item["margin_pct"] = round(margin_pct, 2)
                item["total_cost_val"] = round(latest_cost * stock_qty, 2)
                item["total_sale_val"] = round(sale_price * stock_qty, 2)
                products.append(item)
            return products
        except Exception as e:
            logger.warning(f"Supabase owner query failed ({e})")

    query = "SELECT * FROM products WHERE status = 'active'"
    params = []
    
    if keyword:
        kw_condition, kw_params = build_multi_keyword_search(
            keyword, ["name", "sku", "location_code", "location", "category"]
        )
        if kw_condition:
            query += f" AND {kw_condition}"
            params.extend(kw_params)
    
    if location_code:
        query += " AND location_code = ?"
        params.append(location_code)
    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY name"
    with db_session() as conn:
        rows = conn.execute(query, params).fetchall()
        products = []
        for r in rows:
            p = dict(r)
            latest_cost = float(p.get("latest_cost") or 0)
            sale_price = float(p.get("sale_price") or 0)
            stock_qty = int(p.get("stock_qty") or 0)
            profit = sale_price - latest_cost
            margin_pct = (profit / sale_price * 100) if sale_price > 0 else 0

            p["profit"] = round(profit, 2)
            p["margin_pct"] = round(margin_pct, 2)
            p["total_cost_val"] = round(latest_cost * stock_qty, 2)
            p["total_sale_val"] = round(sale_price * stock_qty, 2)
            products.append(p)
        return products


def get_owner_dashboard_stats():
    """คำนวณสถิติและมูลค่ารวมของคลังสินค้าสำหรับ Owner"""
    products = list_products_owner()
    total_items = len(products)
    total_stock_count = sum(p["stock_qty"] for p in products)
    total_cost_value = sum(p["total_cost_val"] for p in products)
    total_sale_value = sum(p["total_sale_val"] for p in products)
    potential_profit = total_sale_value - total_cost_value
    low_stock_count = sum(1 for p in products if p["stock_qty"] <= (p.get("min_stock") or 5))

    pending_list = list_pending_products()

    return {
        "total_items": total_items,
        "total_stock_count": total_stock_count,
        "total_cost_value": round(total_cost_value, 2),
        "total_sale_value": round(total_sale_value, 2),
        "potential_profit": round(potential_profit, 2),
        "low_stock_count": low_stock_count,
        "pending_count": len(pending_list)
    }


def export_stock_report_data():
    """
    ดึงข้อมูลสินค้าในคลังสินค้าเพื่อส่งออกรายงาน Excel/CSV
    จัดเรียงข้อมูลแบบ Vertical Layout (Row 1 Header, Row 2..N ข้อมูล)
    ครอบคลุมคอลัมน์: ID, ชื่อ, SKU, หมวดหมู่, ราคาขาย, ต้นทุน, สต็อก, Min Stock,
    ตำแหน่ง, รายละเอียด, วันที่อัปเดต
    """
    products = list_products_owner()
    report_rows = []
    for p in products:
        report_rows.append({
            "ID": p.get("id", ""),
            "ชื่อสินค้า": p.get("name") or "-",
            "SKU/Barcode": p.get("sku") or "-",
            "หมวดหมู่": p.get("category") or "-",
            "ราคาขาย": p.get("sale_price", 0.0),
            "ราคาต้นทุน": p.get("latest_cost", 0.0),
            "จำนวนคงเหลือ": p.get("stock_qty", 0),
            "สต็อกหน้าร้าน": p.get("front_stock", 0),
            "สต็อกคลังหลังร้าน": p.get("warehouse_stock", 0),
            "จำนวนคงเหลือรวม": p.get("stock_qty", 0),
            "สต็อกขั้นต่ำ": p.get("min_stock", 5),
            "รหัสตำแหน่ง": p.get("location_code") or "-",
            "ตำแหน่งจัดเก็บ": p.get("location") or "-",
            "รายละเอียด/สเปก": p.get("description") or "",
            "วันที่อัปเดตล่าสุด": str(p.get("updated_at") or "-"),
            "รูปภาพ": p.get("image_path") or p.get("image_url") or ""
        })
    return report_rows


# ============================================================
# PENDING & RECEIPT WORKFLOW (Phase A/B)
# ============================================================

def create_receipt(image_path, receipt_date=None, supplier_name=None,
                   receipt_no=None, ocr_raw_json=None, total_amount=0):
    """สร้างบิลสั่งซื้อใหม่"""
    with db_session() as conn:
        cur = conn.execute(
            """INSERT INTO purchase_receipts
               (receipt_date, supplier_name, receipt_no, image_path, ocr_raw_json, total_amount)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (receipt_date, supplier_name, receipt_no, image_path, ocr_raw_json, total_amount),
        )
        rid = cur.lastrowid
        add_audit_log("รับบิล", f"สร้างบิลสั่งซื้อ id={rid} จาก {supplier_name or 'ไม่ระบุ'}", "owner")
        return rid


def list_receipts():
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM purchase_receipts ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_receipt_items(receipt_id: int):
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM receipt_items WHERE receipt_id = ?", (receipt_id,)).fetchall()
        return [dict(r) for r in rows]


def create_pending_product_from_receipt(receipt_id, ocr_name, qty, unit_cost, line_total=None, performed_by="owner"):
    """Phase A: สร้าง/รับเข้าสินค้าในคลังจากบิลสั่งซื้อ (สแกนด้วย AI OCR)

    ใช้ข้อมูลจาก Gemini Vision เพียง 3 ค่า: ชื่อสินค้า, ราคาต้นทุน, จำนวนชิ้น
    - ถ้าไม่พบสินค้าชื่อเดิม -> สร้างสินค้าใหม่ status='active' แต่ยังไม่ครบข้อมูล
      โดยตั้ง flag is_complete = 0 (รอ Owner เติมรูปสินค้า/รูปตำแหน่ง/ราคาขาย/SKU ภายหลัง)
    - ถ้ามีสินค้าชื่อเดิมอยู่แล้ว -> บวกสต็อกเพิ่มและอัปเดตต้นทุนล่าสุดให้ (ไม่สร้างรายการซ้ำ)

    คืนค่า: dict {"product_id": int, "created": bool}
    """
    if line_total is None:
        line_total = float(qty) * float(unit_cost)
    qty = float(qty or 0)
    unit_cost = float(unit_cost or 0)

    product_id = None
    created = False

    with db_session() as conn:
        # 1) หาสินค้าชื่อเดิม (เทียบแบบไม่สนตัวพิมพ์เล็ก/ใหญ่ และตัดช่องว่างหัวท้าย)
        try:
            existing = conn.execute(
                """SELECT id FROM products
                   WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
                   ORDER BY id LIMIT 1""",
                (ocr_name,),
            ).fetchone()
        except Exception:
            existing = None

        if existing:
            product_id = existing["id"]
            conn.execute(
                """UPDATE products
                   SET stock_qty = COALESCE(stock_qty, 0) + ?,
                       latest_cost = ?,
                       updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (qty, unit_cost, product_id),
            )
        else:
            # 2) สร้างสินค้าใหม่ในคลังทันที แต่ยังลงไม่ครบ (is_complete = 0)
            try:
                cur = conn.execute(
                    """INSERT INTO products (name, latest_cost, stock_qty, status, is_complete)
                       VALUES (?, ?, ?, 'active', 0)""",
                    (ocr_name, unit_cost, qty),
                )
            except Exception:
                # เผื่อฐานข้อมูลยังไม่มีคอลัมน์ is_complete
                cur = conn.execute(
                    """INSERT INTO products (name, latest_cost, stock_qty, status)
                       VALUES (?, ?, ?, 'active')""",
                    (ocr_name, unit_cost, qty),
                )
            product_id = cur.lastrowid
            created = True

        conn.execute(
            """INSERT INTO receipt_items (receipt_id, product_id, ocr_name, qty, unit_cost, line_total, matched)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (receipt_id, product_id, ocr_name, qty, unit_cost, line_total),
        )

        conn.execute(
            """INSERT INTO cost_history (product_id, receipt_id, cost, note)
               VALUES (?, ?, ?, ?)""",
            (product_id, receipt_id, unit_cost, "จากบิลสั่งของ (สแกนบิล)"),
        )

    if created:
        add_audit_log(
            "สแกนบิลลงสินค้า",
            (f"{_actor_name(performed_by)} ได้ทำการสแกนบิลสินค้า '{ocr_name}' "
             f"(รับเข้า {qty:g} ชิ้น ต้นทุน ฿{unit_cost:,.2f}) — สถานะ: ยังลงไม่ครบ (รอเติมรูป/ราคาขาย/SKU)"),
            performed_by,
        )
    else:
        add_audit_log(
            "สแกนบิลลงสินค้า",
            (f"{_actor_name(performed_by)} ได้ทำการสแกนบิลสินค้า '{ocr_name}' "
             f"(สินค้ามีอยู่แล้ว รับเข้าเพิ่ม {qty:g} ชิ้น ต้นทุน ฿{unit_cost:,.2f})"),
            performed_by,
        )

    # 3) Sync ขึ้น Supabase Cloud แบบ best-effort (ถ้ามีการเชื่อมต่อ)
    #    เพื่อให้ทุกเครื่อง/พนักงานที่อ่านข้อมูลจาก Cloud มองเห็นสินค้าที่ยังลงไม่ครบเหมือนกัน
    _sync_pending_product_to_cloud(ocr_name, unit_cost, product_id)

    return {"product_id": product_id, "created": created}


def _sync_pending_product_to_cloud(ocr_name, unit_cost, local_product_id):
    """Sync สินค้าจากการสแกนบิลขึ้น Supabase (ถ้ามี Cloud) — ล้มเหลวได้โดยไม่กระทบการทำงานหลัก"""
    if not supabase_admin:
        return
    try:
        # สต็อกรวมล่าสุดจาก SQLite (กรณีเป็นสินค้าซ้ำที่เพิ่งบวกสต็อกไป)
        total_stock = None
        with db_session() as conn:
            row = conn.execute(
                "SELECT stock_qty, latest_cost FROM products WHERE id = ?", (local_product_id,)
            ).fetchone()
            if row is not None:
                total_stock = int(row["stock_qty"] or 0)
                unit_cost = float(row["latest_cost"] or unit_cost)
        if total_stock is None:
            return

        payload = {
            "name": ocr_name,
            "cost_price": unit_cost,
            "stock_qty": total_stock,
            "status": "active",
        }
        if _cloud_is_complete_available():
            payload["is_complete"] = False

        found = (
            supabase_admin.from_("products")
            .select("id")
            .ilike("name", ocr_name)
            .limit(1)
            .execute()
        )
        try:
            if found.data:
                supabase_admin.from_("products").update(payload).eq("id", found.data[0]["id"]).execute()
            else:
                supabase_admin.from_("products").insert(payload).execute()
        except Exception:
            # เผื่อคอลัมน์ is_complete ยังไม่มีบน Cloud -> ลองใหม่โดยไม่ส่งคอลัมน์นั้น
            if "is_complete" not in payload:
                raise
            payload.pop("is_complete")
            if found.data:
                supabase_admin.from_("products").update(payload).eq("id", found.data[0]["id"]).execute()
            else:
                supabase_admin.from_("products").insert(payload).execute()
    except Exception as sp_err:
        logger.info(f"Supabase sync of scanned product skipped: {sp_err}")






def list_pending_products():
    """รายการสินค้า 'ยังลงไม่ครบ' (is_complete = 0) สำหรับหน้า 'รอเติมข้อมูล'

    - รวมข้อมูลจาก Supabase Cloud (ถ้ามี) + SQLite local โดยไม่ให้ซ้ำกัน (เทียบจากชื่อสินค้า)
    - หมายเหตุ: ไม่ส่งราคาต้นทุน (latest_cost) ออกไป เพราะ endpoint นี้ถูกเรียกใช้จากฝั่ง Staff ด้วย
    """
    items = []
    seen_names = set()

    # 1) Supabase Cloud (ถ้ามีการเชื่อมต่อ + รัน migration แล้ว)
    if supabase_admin and _cloud_is_complete_available():
        try:
            try:
                res = (
                    supabase_admin.from_("products")
                    .select("*")
                    .or_("is_complete.eq.false,status.eq.pending")
                    .order("created_at", desc=True)
                    .limit(500)
                    .execute()
                )
            except Exception:
                res = (
                    supabase_admin.from_("products")
                    .select("*")
                    .eq("is_complete", False)
                    .order("created_at", desc=True)
                    .limit(500)
                    .execute()
                )
            for r in (res.data or []):
                p = _cloud_row_to_local_shape(r, with_cost=False)
                key = str(p.get("name") or "").strip().lower()
                if key and key in seen_names:
                    continue
                seen_names.add(key)
                items.append(p)
        except Exception as e:
            logger.info(f"Supabase pending products query skipped: {e}")

    # 2) SQLite local fallback (ตัดสินค้าที่เติมข้อมูลครบแล้วบน Cloud ออก)
    completion = _cloud_product_completion_map()
    try:
        with db_session() as conn:
            rows = conn.execute(
                """SELECT id, name, sku, stock_qty, sale_price, category, location_code,
                          image_path, created_at
                   FROM products
                   WHERE COALESCE(is_complete, 1) = 0 OR status = 'pending'
                   ORDER BY created_at DESC"""
            ).fetchall()
            for r in rows:
                p = dict(r)
                key = str(p.get("name") or "").strip().lower()
                if key and (key in seen_names or completion.get(key) is True):
                    continue
                seen_names.add(key)
                items.append(p)
    except Exception as e:
        logger.warning(f"Failed to read pending products: {e}")

    return items






def complete_product(product_id, sale_price, location_code, image_path=None, location_image_path=None):
    """Phase B: เติมข้อมูลสินค้า pending ให้ครบ แล้วเปลี่ยนสถานะเป็น 'ลงครบแล้ว' (is_complete = 1)"""
    with db_session() as conn:
        try:
            conn.execute(
                """UPDATE products
                   SET sale_price = ?, location_code = ?,
                       image_path = COALESCE(?, image_path),
                       location_image_path = COALESCE(?, location_image_path),
                       status = 'active', is_complete = 1,
                       updated_at = datetime('now','localtime')
                   WHERE id = ?""",
                (sale_price, location_code, image_path, location_image_path, product_id),
            )
        except Exception:
            conn.execute(
                """UPDATE products
                   SET sale_price = ?, location_code = ?,
                       image_path = COALESCE(?, image_path),
                       location_image_path = COALESCE(?, location_image_path),
                       status = 'active', updated_at = datetime('now','localtime')
                   WHERE id = ?""",
                (sale_price, location_code, image_path, location_image_path, product_id),
            )
        add_audit_log(
            "เติมข้อมูลสินค้า",
            f"เจ้าของร้าน (Owner) เติมข้อมูลสินค้านครบ (id={product_id}) — ราคาขาย ฿{float(sale_price or 0):,.2f}",
            "owner",
        )

    # Sync ขึ้น Supabase แบบ best-effort (ถ้ามี Cloud)
    if supabase_admin:
        try:
            payload = {
                "sale_price": sale_price,
                "location_code": location_code,
                "status": "active",
            }
            if image_path:
                payload["image_url"] = image_path
            if location_image_path:
                payload["location_image_url"] = location_image_path
            if _cloud_is_complete_available():
                payload["is_complete"] = True
            try:
                supabase_admin.from_("products").update(payload).eq("id", product_id).execute()
            except Exception:
                if "is_complete" not in payload:
                    raise
                payload.pop("is_complete")
                supabase_admin.from_("products").update(payload).eq("id", product_id).execute()
        except Exception as sp_err:
            logger.info(f"Supabase complete_product sync skipped: {sp_err}")


def merge_pending_product(pending_id: int, active_id: int):
    """ยุบรวมสินค้า pending เข้ากับสินค้า active ที่มีอยู่แล้ว"""
    with db_session() as conn:
        pending = conn.execute(
            "SELECT stock_qty, latest_cost FROM products WHERE id = ? AND status = 'pending'",
            (pending_id,),
        ).fetchone()
        if not pending:
            raise ValueError(f"ไม่พบสินค้า pending id={pending_id}")

        active = conn.execute(
            "SELECT id FROM products WHERE id = ? AND status = 'active'",
            (active_id,),
        ).fetchone()
        if not active:
            raise ValueError(f"ไม่พบสินค้า active id={active_id}")

        conn.execute(
            """UPDATE products
               SET stock_qty = stock_qty + ?,
                   latest_cost = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (pending["stock_qty"], pending["latest_cost"], active_id),
        )

        conn.execute(
            "UPDATE receipt_items SET product_id = ?, matched = 1 WHERE product_id = ?",
            (active_id, pending_id),
        )
        conn.execute(
            "UPDATE cost_history SET product_id = ? WHERE product_id = ?",
            (active_id, pending_id),
        )
        conn.execute("DELETE FROM products WHERE id = ?", (pending_id,))
        add_audit_log("ยุบรวมสินค้า", f"ยุบรวม pending id={pending_id} เข้า active id={active_id}", "owner")


def list_incomplete_products(keyword=None):
    """รายการ 'สินค้ายังลงไม่ครบ' (is_complete = 0) สำหรับ Owner เท่านั้น

    - ใช้ในแท็บ/ฟิลเตอร์หน้าคลังสินค้า เพื่อให้ Owner กดปุ่มแก้ไข (Modal) เติมรูปสินค้า,
      รูปตำแหน่ง, ราคาขาย และรหัส SKU ภายหลังได้
    - Owner เท่านั้นที่เห็นต้นทุน จึงคืนค่า latest_cost มาด้วย (สำหรับอ้างอิงตอนตั้งราคาขาย)
    - ดึงทั้งจาก Supabase Cloud และ SQLite local แล้วรวมกันโดยไม่ซ้ำ (เทียบจากชื่อสินค้า)
    """
    keyword = (keyword or "").strip()
    kw_lower = keyword.lower()
    items = []
    seen_names = set()

    def _matches(p: dict) -> bool:
        if not kw_lower:
            return True
        hay = " ".join([
            str(p.get("name") or ""),
            str(p.get("sku") or ""),
            str(p.get("category") or ""),
        ]).lower()
        return kw_lower in hay

    # 1) Supabase Cloud
    if supabase_admin and _cloud_is_complete_available():
        try:
            try:
                res = (
                    supabase_admin.from_("products")
                    .select("*")
                    .or_("is_complete.eq.false,status.eq.pending")
                    .order("created_at", desc=True)
                    .limit(500)
                    .execute()
                )
            except Exception:
                res = (
                    supabase_admin.from_("products")
                    .select("*")
                    .eq("is_complete", False)
                    .order("created_at", desc=True)
                    .limit(500)
                    .execute()
                )
            for r in (res.data or []):
                p = _cloud_row_to_local_shape(r, with_cost=True)
                key = str(p.get("name") or "").strip().lower()
                if key and key in seen_names:
                    continue
                seen_names.add(key)
                items.append(p)
        except Exception as e:
            logger.info(f"Supabase incomplete products query skipped: {e}")

    # 2) SQLite local (ตัดสินค้าที่เติมข้อมูลครบแล้วบน Cloud ออก)
    completion = _cloud_product_completion_map()
    try:
        with db_session() as conn:
            rows = conn.execute(
                """SELECT id, sku, name, category, latest_cost, sale_price, stock_qty,
                          location_code, location, image_path, location_image_path,
                          status, COALESCE(is_complete, 1) AS is_complete, created_at
                   FROM products
                   WHERE COALESCE(is_complete, 1) = 0 OR status = 'pending'
                   ORDER BY created_at DESC"""
            ).fetchall()
            for r in rows:
                p = dict(r)
                key = str(p.get("name") or "").strip().lower()
                if key and (key in seen_names or completion.get(key) is True):
                    continue
                seen_names.add(key)
                items.append(p)
    except Exception as e:
        logger.warning(f"Failed to read incomplete products: {e}")

    result = []
    for p in items:
        if not _matches(p):
            continue
        stock = int(p.get("stock_qty") or 0)
        cost = float(p.get("latest_cost") or 0)
        sale = float(p.get("sale_price") or 0)
        p["total_cost_val"] = round(cost * stock, 2)
        p["profit"] = round(sale - cost, 2)
        p["is_complete"] = int(p.get("is_complete") or 0)
        result.append(p)
    return result


def _load_sales_for_range(days: int = 90):
    """ดึงบิลขายย้อนหลัง N วัน (Supabase ถ้ามี + fallback SQLite) พร้อม parse items_json"""
    bills = []
    if supabase_admin:
        try:
            since = (bangkok_now() - timedelta(days=days)).astimezone(timezone.utc).isoformat()
            res = (
                supabase_admin.from_("sales")
                .select("*")
                .gte("created_at", since)
                .order("created_at", desc=True)
                .limit(3000)
                .execute()
            )
            for r in (res.data or []):
                try:
                    r["items"] = json.loads(r.get("items_json") or "[]")
                except Exception:
                    r["items"] = []
                bills.append(r)
        except Exception as sp_err:
            logger.info(f"Supabase sales range query skipped: {sp_err}")
            bills = []

    if not bills:
        try:
            with db_session() as conn:
                rows = conn.execute(
                    """SELECT * FROM sales
                       WHERE created_at >= datetime('now', 'localtime', ?)
                       ORDER BY created_at DESC LIMIT 3000""",
                    (f"-{int(days)} days",),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    try:
                        d["items"] = json.loads(d.get("items_json") or "[]")
                    except Exception:
                        d["items"] = []
                    bills.append(d)
        except Exception as e:
            logger.warning(f"Failed to read sales range: {e}")
    return bills


def _product_cost_map():
    """แผนที่ราคาต้นทุนล่าสุดของสินค้า: (cost_by_id, cost_by_name_lower)"""
    cost_by_id = {}
    cost_by_name = {}
    try:
        with db_session() as conn:
            rows = conn.execute("SELECT id, name, COALESCE(latest_cost, 0) AS cost FROM products").fetchall()
            for r in rows:
                cost_by_id[int(r["id"])] = float(r["cost"] or 0)
                cost_by_name[str(r["name"] or "").strip().lower()] = float(r["cost"] or 0)
    except Exception as e:
        logger.warning(f"Failed to build cost map: {e}")
    return cost_by_id, cost_by_name


def get_financial_analytics(period: str = "daily", days: int = 14):
    """กราฟวิเคราะห์การเงิน: ยอดขายรวม vs ต้นทุน vs กำไรสุทธิ (รายวัน / รายสัปดาห์)

    - period = 'daily'  -> ย้อนหลัง `days` วัน (ค่าเริ่มต้น 14 วัน)
    - period = 'weekly' -> ย้อนหลัง 8 สัปดาห์
    - ต้นทุนต่อหน่วยดึงจาก items_json (ถ้ามี unit_cost/cost) ถ้าไม่มีใช้ต้นทุนล่าสุดของสินค้า
    - คำนวณตามเขตเวลา Asia/Bangkok
    """
    period = (period or "daily").strip().lower()
    if period not in ("daily", "weekly"):
        period = "daily"

    today = bangkok_now().date()
    buckets = []
    key_map = {}

    def _bucket_for(d):
        if period == "weekly":
            iso_year, iso_week, _ = d.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
            start = d - timedelta(days=d.weekday())
            label = f"สัปดาห์ {start.strftime('%d/%m')}"
        else:
            key = d.strftime("%Y-%m-%d")
            label = d.strftime("%d/%m")
        if key not in key_map:
            b = {"key": key, "label": label, "revenue": 0.0, "cost": 0.0,
                 "profit": 0.0, "qty": 0.0, "bill_count": 0}
            key_map[key] = b
            buckets.append(b)
        return key_map[key]

    if period == "weekly":
        for i in range(7, -1, -1):
            _bucket_for(today - timedelta(days=i * 7))
        range_days = 56
    else:
        try:
            days = int(days or 14)
        except Exception:
            days = 14
        days = max(1, min(days, 90))
        for i in range(days - 1, -1, -1):
            _bucket_for(today - timedelta(days=i))
        range_days = days

    cutoff = today - timedelta(days=range_days)
    cost_by_id, cost_by_name = _product_cost_map()
    bills = _load_sales_for_range(days=range_days + 2)

    for bill in bills:
        bdate = to_bangkok_date(bill.get("created_at"))
        if bdate is None or bdate < cutoff or bdate > today:
            continue
        bucket = _bucket_for(bdate)
        items = bill.get("items") or []
        bucket["bill_count"] += 1
        bucket["revenue"] += _sale_line_amount(bill)

        bill_cost = 0.0
        for it in items:
            qty = float(it.get("qty") or 0)
            unit_cost = float(it.get("unit_cost") or it.get("cost") or 0)
            if unit_cost <= 0:
                pid = it.get("product_id")
                if pid is not None and int(pid) in cost_by_id:
                    unit_cost = cost_by_id[int(pid)]
                else:
                    unit_cost = cost_by_name.get(str(it.get("name") or "").strip().lower(), 0.0)
            bill_cost += unit_cost * qty
            bucket["qty"] += qty
        bucket["cost"] += bill_cost

    for b in buckets:
        b["revenue"] = round(b["revenue"], 2)
        b["cost"] = round(b["cost"], 2)
        b["profit"] = round(b["revenue"] - b["cost"], 2)
        b["qty"] = int(b["qty"])

    totals = {
        "revenue": round(sum(b["revenue"] for b in buckets), 2),
        "cost": round(sum(b["cost"] for b in buckets), 2),
        "profit": round(sum(b["profit"] for b in buckets), 2),
        "qty": sum(b["qty"] for b in buckets),
        "bill_count": sum(b["bill_count"] for b in buckets),
    }
    totals["margin_pct"] = round((totals["profit"] / totals["revenue"] * 100), 2) if totals["revenue"] > 0 else 0.0

    return {
        "period": period,
        "timezone": "Asia/Bangkok",
        "labels": [b["label"] for b in buckets],
        "revenue": [b["revenue"] for b in buckets],
        "cost": [b["cost"] for b in buckets],
        "profit": [b["profit"] for b in buckets],
        "qty": [b["qty"] for b in buckets],
        "rows": buckets,
        "totals": totals,
    }


def get_finance_summary(days: int = 30):
    """สรุปภาพรวมรายรับ ต้นทุน และกำไรสุทธิย้อนหลัง N วัน (ใช้กับ Export Excel หน้ารายงานการเงิน)"""
    return get_financial_analytics(period="daily", days=days)
