"""
CRUD - ฟังก์ชันจัดการข้อมูลหลักของระบบ KJY Inventory Cloud App
รองรับการสลับระหว่าง Supabase Cloud (ถ้ากำหนด) หรือ SQLite Local Fallback
พร้อมระบบแยกสิทธิ์เด็ดขาด (Staff vs Owner)
"""

from database import db_session, supabase_client, supabase_admin
import logging
from datetime import datetime

logger = logging.getLogger("crud")

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
    "description", "min_stock", "location"
}

# คอลัมน์ที่เพิ่มจาก Migration 003 (front_stock / warehouse_stock)
SUPABASE_STOCK_COLUMNS = {
    "front_stock", "warehouse_stock"
}

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
# AUDIT LOG
# ============================================================

def add_audit_log(action_type: str, description: str, performed_by: str = "staff"):
    """บันทึก Audit Log อัตโนมัติ"""
    try:
        with db_session() as conn:
            conn.execute(
                """INSERT INTO audit_log (action_type, description, performed_by, timestamp)
                   VALUES (?, ?, ?, datetime('now', 'localtime'))""",
                (action_type, description, performed_by)
            )
    except Exception as e:
        logger.warning(f"Failed to write audit log: {e}")


def get_audit_logs(limit: int = 100):
    """ดึง Audit Log ล่าสุด"""
    try:
        with db_session() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
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
                      front_stock=0, warehouse_stock=0):
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
                add_audit_log("เพิ่มสินค้า", f"เพิ่มสินค้า '{name}' (SKU: {sku})", "staff")
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

        add_audit_log("เพิ่มสินค้า", f"เพิ่มสินค้า '{name}' (SKU: {sku})", "staff")

        if location_code and location_image_path:
            conn.execute(
                """INSERT INTO locations (code, image_path) VALUES (?, ?)
                   ON CONFLICT(code) DO UPDATE SET image_path=excluded.image_path""",
                (location_code, location_image_path)
            )
        return pid


def update_product_staff(product_id: int, **fields):
    """
    แก้ไขข้อมูลสินค้า
    - Staff: ไม่อนุญาตให้แก้ไข cost_price
    - Owner: แก้ไข cost_price ได้ (ผ่าน allow_cost_price=True)
    """
    # ถ้าไม่มีการส่ง allow_cost_price=True จะกรอง cost_price ออก
    allow_cost_price = fields.pop("allow_cost_price", False)

    allowed_keys = {
        "name", "category", "sku", "sale_price", "stock_qty", "cost_price",
        "location_code", "location", "description", "min_stock",
        "image_path", "location_image_path",
        "front_stock", "warehouse_stock"
    }
    filtered_fields = {k: v for k, v in fields.items() if k in allowed_keys and v is not None}

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

    if supabase_admin:
        try:
            sp_payload = {}
            for k, v in filtered_fields.items():
                if k == "image_path":
                    sp_payload["image_url"] = v
                elif k == "location_image_path":
                    sp_payload["location_image_url"] = v
                else:
                    sp_payload[k] = v
            # กรองเฉพาะคอลัมน์ที่มีอยู่จริงบน Supabase (รวม migration 002 + 003)
            allowed_cols = SUPABASE_PRODUCT_COLUMNS | SUPABASE_MIGRATED_COLUMNS | SUPABASE_STOCK_COLUMNS
            safe_payload = {k: v for k, v in sp_payload.items() if k in allowed_cols}
            logger.info(f"[SUPABASE] Updating product id={product_id} payload: {safe_payload}")
            res = supabase_admin.from_("products").update(safe_payload).eq("id", product_id).execute()
            logger.info(f"[SUPABASE] Update success for id={product_id}, response data: {res.data}")
            add_audit_log("แก้ไขสินค้า", f"แก้ไขสินค้า id={product_id}: {', '.join(filtered_fields.keys())}", "staff")
            return
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
        add_audit_log("แก้ไขสินค้า", f"แก้ไขสินค้า id={product_id}: {', '.join(filtered_fields.keys())}", "staff")


def transfer_stock(product_id: int, qty: int, direction: str = "to_front"):
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

            add_audit_log("ย้ายสต็อก", f"ย้ายสต็อก '{name}' จำนวน {qty} ชิ้น ({'คลัง->หน้าร้าน' if direction=='to_front' else 'หน้าร้าน->คลัง'})", "staff")
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

        add_audit_log("ย้ายสต็อก", f"ย้ายสต็อก '{name}' จำนวน {qty} ชิ้น ({'คลัง->หน้าร้าน' if direction=='to_front' else 'หน้าร้าน->คลัง'})", "staff")
        return {"status": "ok", "front_stock": new_front, "warehouse_stock": new_warehouse}


def delete_product_staff(product_id: int):
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
                add_audit_log("PRODUCT_DELETE", f"ลบสินค้า '{product_name}' (id={product_id}) จาก Supabase", "staff")
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
                add_audit_log("PRODUCT_DELETE", f"ลบสินค้า '{pname}' (id={product_id}) จาก SQLite", "staff")
                logger.info(f"Deleted product id={product_id} from SQLite")
    except Exception as e:
        logger.error(f"[DELETE] SQLite delete failed for product id={product_id}: {e}")

    if not deleted:
        raise ValueError(f"ไม่พบสินค้า id={product_id}")

    return product_id


def process_checkout(cart_items: list, payment_type: str = "cash", total_amount: float = 0.0):
    """
    ประมวลผลการชำระเงินหน้าร้านและตัดสต็อกสินค้าในคลัง
    cart_items: [{"product_id": 1, "qty": 2}, ...]
    """
    if not cart_items:
        raise ValueError("ตะกร้าสินค้าว่างเปล่า")

    with db_session() as conn:
        for item in cart_items:
            pid = item.get("product_id")
            qty = int(item.get("qty", 1))

            if not pid or qty <= 0:
                continue

            # Get product name for audit log
            p = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()
            pname = p["name"] if p else f"id={pid}"

            # Local SQLite update
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
                    p = supabase_admin.from_("products").select("stock_qty").eq("id", pid).single().execute()
                    if p.data:
                        current_stock = p.data.get("stock_qty", 0)
                        new_stock = max(0, current_stock - qty)
                        supabase_admin.from_("products").update({"stock_qty": new_stock}).eq("id", pid).execute()
                except Exception as e:
                    logger.warning(f"Supabase checkout stock update failed for id={pid}: {e}")

            add_audit_log("ขายสินค้า", f"ขาย '{pname}' จำนวน {qty} ชิ้น (รวม {total_amount:.2f} บาท)", "staff")

    return {"status": "ok", "message": "บันทึกการขายและตัดสต็อกเรียบร้อยแล้ว"}



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


def create_pending_product_from_receipt(receipt_id, ocr_name, qty, unit_cost):
    """Phase A: สร้างสินค้าแบบ pending จากบิลสั่งซื้อ"""
    with db_session() as conn:
        cur = conn.execute(
            """INSERT INTO products (name, latest_cost, stock_qty, status)
               VALUES (?, ?, ?, 'pending')""",
            (ocr_name, unit_cost, qty),
        )
        product_id = cur.lastrowid

        conn.execute(
            """INSERT INTO receipt_items (receipt_id, product_id, ocr_name, qty, unit_cost, line_total, matched)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (receipt_id, product_id, ocr_name, qty, unit_cost, qty * unit_cost),
        )

        conn.execute(
            """INSERT INTO cost_history (product_id, receipt_id, cost, note)
               VALUES (?, ?, ?, ?)""",
            (product_id, receipt_id, unit_cost, "จากบิลสั่งของ (สร้างใหม่)"),
        )
        return product_id


def list_pending_products():
    """รายการสินค้า pending สำหรับหน้า 'รอเติมข้อมูล' (ไม่แสดงต้นทุน)"""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT id, name, stock_qty, created_at FROM products
               WHERE status = 'pending'
               ORDER BY created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def complete_product(product_id, sale_price, location_code, image_path=None, location_image_path=None):
    """Phase B: ครอบครัวเติมข้อมูลสินค้า pending ให้ครบ และเปลี่ยนสถานะเป็น active"""
    with db_session() as conn:
        conn.execute(
            """UPDATE products
               SET sale_price = ?, location_code = ?, 
                   image_path = COALESCE(?, image_path),
                   location_image_path = COALESCE(?, location_image_path),
                   status = 'active', updated_at = datetime('now','localtime')
               WHERE id = ?""",
            (sale_price, location_code, image_path, location_image_path, product_id),
        )
        add_audit_log("เติมข้อมูลสินค้า", f"เติมข้อมูลสินค้า pending id={product_id}", "owner")


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
