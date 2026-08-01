"""
CRUD - ฟังก์ชันจัดการข้อมูลหลักของระบบ KJY Inventory Cloud App
รองรับการสลับระหว่าง Supabase Cloud (ถ้ากำหนด) หรือ SQLite Local Fallback
พร้อมระบบแยกสิทธิ์เด็ดขาด (Staff vs Owner)
"""

from database import db_session, supabase_client, supabase_admin
import logging
from datetime import datetime

logger = logging.getLogger("crud")

# คอลัมน์ที่มีอยู่จริงบน Supabase products table (PostgreSQL)
# ใช้ filter payload ก่อนส่งไป Supabase เพื่อป้องกัน request พัง
SUPABASE_PRODUCT_COLUMNS = {
    "sku", "name", "category", "cost_price", "sale_price", "stock_qty",
    "location_code", "image_url", "location_image_url", "status",
    "description", "min_stock", "location"
}

def _sanitize_supabase_payload(payload: dict, allowed_columns: set = None) -> dict:
    """
    กรอง payload ให้มีเฉพาะคอลัมน์ที่มีอยู่จริงบน Supabase
    ป้องกัน request พังเมื่อส่งคอลัมน์ที่ไม่มีอยู่
    """
    if allowed_columns is None:
        allowed_columns = SUPABASE_PRODUCT_COLUMNS
    return {k: v for k, v in payload.items() if k in allowed_columns}


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
    """
    if supabase_client:
        try:
            query = supabase_client.from_("products").select(
                "id, sku, name, category, sale_price, stock_qty, location_code, location, min_stock, description, image_url, location_image_url, status, updated_at"
            ).eq("status", "active")

            if keyword:
                # Multi-keyword for Supabase - use OR for each keyword
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
                item["image_path"] = item.get("image_url")
                item["location_image_path"] = item.get("location_image_url")
                products.append(item)
            return products
        except Exception as e:
            logger.warning(f"Supabase query failed ({e}), falling back to local DB")

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
        return [dict(r) for r in rows]


def get_product_staff(product_id: int):
    """ดึงข้อมูลสินค้าชิ้นเดียวสำหรับ Staff (ไม่มีราคาต้นทุน)"""
    if supabase_client:
        try:
            res = supabase_client.from_("products").select(
                "id, sku, name, category, sale_price, stock_qty, location_code, location, min_stock, description, image_url, location_image_url, status, updated_at"
            ).eq("id", product_id).execute()
            if res.data:
                p = res.data[0]
                p["image_path"] = p.get("image_url")
                p["location_image_path"] = p.get("location_image_url")
                return p
        except Exception:
            pass

    with db_session() as conn:
        row = conn.execute(
            """SELECT id, sku, name, category, sale_price, stock_qty, location_code, location, min_stock, description,
                      image_path, location_image_path, status, created_at, updated_at 
               FROM products WHERE id = ?""", 
            (product_id,)
        ).fetchone()
        return dict(row) if row else None


def add_product_staff(name, sale_price=0, category=None, sku=None,
                      location_code=None, location="", description="",
                      image_path=None, location_image_path=None, stock_qty=0, min_stock=5):
    """Staff เพิ่มสินค้าใหม่เข้าคลัง (สินค้ามีสถานะ active ทันที)"""
    if supabase_admin:
        try:
            payload = {
                "name": name,
                "sale_price": float(sale_price or 0),
                "category": category or None,
                "sku": sku or None,
                "location_code": location_code or None,
                "location": location or "",
                "description": description or "",
                "image_url": image_path,
                "location_image_url": location_image_path,
                "stock_qty": int(stock_qty or 0),
                "min_stock": int(min_stock or 5),
                "status": "active"
            }
            # กรองเฉพาะคอลัมน์ที่มีอยู่จริงบน Supabase
            # ป้องกัน request พังถ้ายังไม่ได้รัน migration
            safe_payload = _sanitize_supabase_payload(payload)
            res = supabase_admin.from_("products").insert(safe_payload).execute()
            if res.data:
                pid = res.data[0]["id"]
                add_audit_log("เพิ่มสินค้า", f"เพิ่มสินค้า '{name}' (SKU: {sku})", "staff")
                return pid
        except Exception as e:
            logger.warning(f"Supabase product insert failed: {e}")

    with db_session() as conn:
        cur = conn.execute(
            """INSERT INTO products (sku, name, category, sale_price, location_code, location, description, image_path, location_image_path, stock_qty, min_stock, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (sku, name, category, sale_price, location_code, location, description, image_path, location_image_path, stock_qty, min_stock),
        )
        pid = cur.lastrowid
        add_audit_log("เพิ่มสินค้า", f"เพิ่มสินค้า '{name}' (SKU: {sku})", "staff")

        if location_code and location_image_path:
            conn.execute(
                """INSERT INTO locations (code, image_path) VALUES (?, ?)
                   ON CONFLICT(code) DO UPDATE SET image_path=excluded.image_path""",
                (location_code, location_image_path)
            )
        return pid


def update_product_staff(product_id: int, **fields):
    """Staff แก้ไขข้อมูลสินค้า (ไม่อนุญาตให้แก้ไขต้นทุน)"""
    allowed_keys = {"name", "category", "sku", "sale_price", "stock_qty", "location_code", "location", "description", "min_stock", "image_path", "location_image_path"}
    filtered_fields = {k: v for k, v in fields.items() if k in allowed_keys and v is not None}
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
            # กรองเฉพาะคอลัมน์ที่มีอยู่จริงบน Supabase
            # ป้องกัน request พังถ้ายังไม่ได้รัน migration
            safe_payload = _sanitize_supabase_payload(sp_payload)
            supabase_admin.from_("products").update(safe_payload).eq("id", product_id).execute()
            add_audit_log("แก้ไขสินค้า", f"แก้ไขสินค้า id={product_id}: {', '.join(filtered_fields.keys())}", "staff")
            return
        except Exception as e:
            logger.warning(f"Supabase update failed: {e}")

    set_clause = ", ".join(f"{k} = ?" for k in filtered_fields)
    set_clause += ", updated_at = datetime('now', 'localtime')"
    values = list(filtered_fields.values())
    values.append(product_id)
    with db_session() as conn:
        conn.execute(f"UPDATE products SET {set_clause} WHERE id = ?", values)
        add_audit_log("แก้ไขสินค้า", f"แก้ไขสินค้า id={product_id}: {', '.join(filtered_fields.keys())}", "staff")


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

                item["latest_cost"] = latest_cost
                item["image_path"] = item.get("image_url")
                item["location_image_path"] = item.get("location_image_url")
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
    """
    products = list_products_owner()
    report_rows = []
    for p in products:
        report_rows.append({
            "SKU/Barcode": p.get("sku") or "-",
            "ชื่อสินค้า": p.get("name") or "-",
            "หมวดหมู่": p.get("category") or "-",
            "ตำแหน่งจัดเก็บ": p.get("location_code") or "-",
            "จำนวนสต็อก": p.get("stock_qty", 0),
            "ต้นทุนล่าสุด": p.get("latest_cost", 0.0),
            "ราคาขาย": p.get("sale_price", 0.0),
            "มูลค่ารวมต้นทุน": p.get("total_cost_val", 0.0),
            "มูลค่ารวมราคาขาย": p.get("total_sale_val", 0.0),
            "กำไรต่อชิ้น": p.get("profit", 0.0),
            "อัตรากำไร %": p.get("margin_pct", 0.0),
            "วันที่อัปเดตล่าสุด": str(p.get("updated_at") or "-")
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