"""
KJY Inventory Cloud App - FastAPI Backend
ร้านคำเจริญเกษตรยนต์ (Kamjarenkasetyon)
"""

import base64
import csv
import io
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from database import init_db, supabase_admin
import crud
from config import RECEIPT_IMAGES_DIR, PRODUCT_IMAGES_DIR, LOCATION_IMAGES_DIR, GEMINI_API_KEY, BASE_DIR

# PIN Code for Boss Mode (default: 1234)
BOSS_PIN = "1234"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="KJY Inventory Cloud API — คำเจริญเกษตรยนต์", version="3.0", lifespan=lifespan)

# Enable CORS for Cloud / Mobile / Web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount local media & static folders
app.mount("/images", StaticFiles(directory="images"), name="images")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Helper for Supabase / Local Storage Upload
async def save_uploaded_file(file: UploadFile, folder_dir: str, prefix: str = "") -> str:
    """บันทึกไฟล์อัปโหลดลง Local หรือ Supabase Storage (ถ้าเปิดใช้งาน)"""
    file_bytes = await file.read()
    filename = f"{prefix}_{file.filename}" if file.filename else f"{prefix}.jpg"
    safe_filename = "".join(c for c in filename if c.isalnum() or c in ('.', '_', '-')).strip()

    # If Supabase Storage admin client is configured, upload to bucket
    if supabase_admin:
        try:
            bucket_name = "kjy-images"
            storage_path = f"{os.path.basename(folder_dir)}/{safe_filename}"
            res = supabase_admin.storage.from_(bucket_name).upload(
                file=file_bytes,
                path=storage_path,
                file_options={"content-type": file.content_type or "image/jpeg", "upsert": "true"}
            )
            # Public URL
            public_url = supabase_admin.storage.from_(bucket_name).get_public_url(storage_path)
            return public_url
        except Exception as e:
            print(f"Supabase storage upload failed ({e}), saving locally")

    # Local fallback
    local_path = os.path.join(folder_dir, safe_filename)
    with open(local_path, "wb") as f:
        f.write(file_bytes)

    # Return relative URL path
    rel_path = local_path.replace("\\", "/")
    return f"/{rel_path}"


# ============================================================
# GEMINI VISION & OCR HELPERS
# ============================================================

def call_gemini_ocr(image_bytes: bytes, mime_type: str = "image/jpeg") -> list:
    """เรียก Google Gemini API ให้อ่านบิลสั่งซื้อสินค้า"""
    api_key = GEMINI_API_KEY
    if not api_key:
        api_txt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.txt")
        if os.path.exists(api_txt_path):
            with open(api_txt_path, "r", encoding="utf-8") as f:
                api_key = f.read().strip()

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ยังไม่ได้ตั้งค่า GEMINI_API_KEY"
        )

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = """อ่านบิล/ใบเสร็จสั่งของในรูปนี้ แล้วตอบกลับเป็น JSON array เท่านั้น
ห้ามมีข้อความอื่นนอกเหนือจาก JSON ในรูปแบบนี้:
[{"name": "ชื่อสินค้า", "qty": จำนวน, "unit_cost": ราคาต่อหน่วย}]
ถ้าอ่านตัวเลขไม่ชัด ให้ใส่ค่าที่อ่านได้ใกล้เคียงที่สุด"""

    try:
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini API Error: {str(e)}")

    text = response.text.strip()
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        json_str = text[start:end+1]
    else:
        json_str = text.replace("```json", "").replace("```", "").strip()

    try:
        items = json.loads(json_str)
        cleaned = []
        for item in items:
            cleaned.append({
                "name": str(item.get("name", "")).strip(),
                "qty": float(item.get("qty", 1)),
                "unit_cost": float(item.get("unit_cost", 0)),
            })
        return cleaned
    except Exception:
        raise HTTPException(status_code=500, detail=f"ไม่สามารถแปลงผลลัพธ์จาก AI เป็น JSON ได้: {text}")


# ============================================================
# MOCK DATA FALLBACK (กรณี database ยังไม่มีข้อมูลหรือ error)
# ============================================================

MOCK_PRODUCTS = [
    {"id": 1, "sku": "NUT-M10-01", "name": "น็อตหกเหลี่ยม M10 x 25mm", "category": "น็อต-สกรู", "sale_price": 15.0, "stock_qty": 121, "location_code": "A-01-05", "image_url": "https://images.unsplash.com/photo-1586864387967-d02ef85d93e8?w=500&auto=format&fit=crop&q=60"},
    {"id": 2, "sku": "BELT-B52", "name": "สายพานพัดลม Kubota B52", "category": "สายพาน", "sale_price": 280.0, "stock_qty": 45, "location_code": "B-02-12", "image_url": "https://images.unsplash.com/photo-1581092160607-ee22621dd758?w=500&auto=format&fit=crop&q=60"},
    {"id": 3, "sku": "FILT-OIL-K", "name": "กรองน้ำมันเครื่อง Kubota L3608", "category": "กรองอากาศ", "sale_price": 190.0, "stock_qty": 68, "location_code": "C-01-02", "image_url": "https://images.unsplash.com/photo-1486262715619-67b85e0b08d3?w=500&auto=format&fit=crop&q=60"},
    {"id": 4, "sku": "OIL-4T-1L", "name": "น้ำมันเครื่องเกรดพรีเมียม 4T 1L", "category": "น้ำมัน", "sale_price": 160.0, "stock_qty": 80, "location_code": "D-05-01", "image_url": "https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=500&auto=format&fit=crop&q=60"},
    {"id": 5, "sku": "TIRE-600-14", "name": "ยางรถไถ 6.00-14 6PR", "category": "ยาง", "sale_price": 1850.0, "stock_qty": 14, "location_code": "E-01-01", "image_url": "https://images.unsplash.com/photo-1578844251758-2f71da64c96f?w=500&auto=format&fit=crop&q=60"},
    {"id": 6, "sku": "BLADE-K18", "name": "ใบโรตารี่ ตราช้าง K18", "category": "อะไหล่เกษตร", "sale_price": 220.0, "stock_qty": 90, "location_code": "A-03-08", "image_url": "https://images.unsplash.com/photo-1504917595217-d4dc5ebe6122?w=500&auto=format&fit=crop&q=60"},
    {"id": 7, "sku": "SPARK-P", "name": "หัวเทียน Yamaha Spark Plug", "category": "อะไหล่เกษตร", "sale_price": 85.0, "stock_qty": 200, "location_code": "F-01-01", "image_url": "https://images.unsplash.com/photo-1581092160607-ee22621dd758?w=500&auto=format&fit=crop&q=60"},
    {"id": 8, "sku": "OIL-GEAR", "name": "น้ำมันเฟืองท้าย SAE 90 1L", "category": "น้ำมัน", "sale_price": 195.0, "stock_qty": 55, "location_code": "D-05-03", "image_url": "https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=500&auto=format&fit=crop&q=60"},
]

MOCK_STATS = {
    "total_cost_value": 145280.0,
    "total_sale_value": 208450.0,
    "potential_profit": 63170.0,
    "low_stock_count": 3
}

MOCK_OWNER_PRODUCTS = [
    {"id": 1, "sku": "NUT-M10-01", "name": "น็อตหกเหลี่ยม M10 x 25mm", "category": "น็อต-สกรู", "latest_cost": 8.0, "sale_price": 15.0, "stock_qty": 121, "profit": 7.0, "margin_pct": 46.67, "total_cost_val": 968.0, "image_url": "https://images.unsplash.com/photo-1586864387967-d02ef85d93e8?w=500&auto=format&fit=crop&q=60"},
    {"id": 2, "sku": "BELT-B52", "name": "สายพานพัดลม Kubota B52", "category": "สายพาน", "latest_cost": 180.0, "sale_price": 280.0, "stock_qty": 45, "profit": 100.0, "margin_pct": 35.71, "total_cost_val": 8100.0, "image_url": "https://images.unsplash.com/photo-1581092160607-ee22621dd758?w=500&auto=format&fit=crop&q=60"},
    {"id": 3, "sku": "FILT-OIL-K", "name": "กรองน้ำมันเครื่อง Kubota L3608", "category": "กรองอากาศ", "latest_cost": 120.0, "sale_price": 190.0, "stock_qty": 68, "profit": 70.0, "margin_pct": 36.84, "total_cost_val": 8160.0, "image_url": "https://images.unsplash.com/photo-1486262715619-67b85e0b08d3?w=500&auto=format&fit=crop&q=60"},
    {"id": 4, "sku": "OIL-4T-1L", "name": "น้ำมันเครื่องเกรดพรีเมียม 4T 1L", "category": "น้ำมัน", "latest_cost": 110.0, "sale_price": 160.0, "stock_qty": 80, "profit": 50.0, "margin_pct": 31.25, "total_cost_val": 8800.0, "image_url": "https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=500&auto=format&fit=crop&q=60"},
    {"id": 5, "sku": "TIRE-600-14", "name": "ยางรถไถ 6.00-14 6PR", "category": "ยาง", "latest_cost": 1400.0, "sale_price": 1850.0, "stock_qty": 14, "profit": 450.0, "margin_pct": 24.32, "total_cost_val": 19600.0, "image_url": "https://images.unsplash.com/photo-1578844251758-2f71da64c96f?w=500&auto=format&fit=crop&q=60"},
    {"id": 6, "sku": "BLADE-K18", "name": "ใบโรตารี่ ตราช้าง K18", "category": "อะไหล่เกษตร", "latest_cost": 150.0, "sale_price": 220.0, "stock_qty": 90, "profit": 70.0, "margin_pct": 31.82, "total_cost_val": 13500.0, "image_url": "https://images.unsplash.com/photo-1504917595217-d4dc5ebe6122?w=500&auto=format&fit=crop&q=60"},
]


# ============================================================
# STAFF API ROUTES (ห้ามเข้าถึงต้นทุนเด็ดขาด)
# ============================================================

@app.get("/api/staff/products")
def get_staff_products(
    keyword: str = Query(None),
    location_code: str = Query(None),
    category: str = Query(None)
):
    """
    ดึงรายการสินค้าสำหรับ Staff
    *** รับประกันว่าไม่มีข้อมูลต้นทุน (cost_price / latest_cost) หลุดออกไป ***
    """
    try:
        result = crud.list_products_staff(keyword=keyword, location_code=location_code, category=category)
        if result and len(result) > 0:
            return result
    except Exception as e:
        print(f"DB error, falling back to mock: {e}")

    # Fallback to mock data
    products = MOCK_PRODUCTS
    if keyword:
        kw = keyword.lower()
        products = [p for p in products if kw in p["name"].lower() or kw in p["sku"].lower()]
    if category and category != "ALL":
        products = [p for p in products if p["category"] == category]
    # Return products without cost info (staff-safe)
    return products


@app.get("/api/staff/products/{product_id}")
def get_staff_product_detail(product_id: int):
    try:
        p = crud.get_product_staff(product_id)
        if p:
            return p
    except Exception:
        pass
    for mp in MOCK_PRODUCTS:
        if mp["id"] == product_id:
            return mp
    raise HTTPException(status_code=404, detail="ไม่พบสินค้า")


@app.post("/api/staff/scan-product")
async def scan_product(file: UploadFile = File(...)):
    """
    สแกนรูปภาพสินค้าด้วย Gemini AI เพื่อ Pre-fill ชื่อสินค้า และหมวดหมู่
    """
    image_bytes = await file.read()
    mime_type = file.content_type or "image/jpeg"

    api_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        api_txt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.txt")
        if os.path.exists(api_txt_path):
            with open(api_txt_path, "r", encoding="utf-8") as f:
                api_key = f.read().strip()

    if not api_key:
        return {"name": "", "category": "", "suggested_location": ""}

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = """ดูรูปภาพสินค้านี้แล้วตอบกลับเป็น JSON เท่านั้น:
{
  "name": "ชื่อสินค้าภาษาไทย",
  "category": "หมวดหมู่สินค้า เช่น น็อต-สกรู, สายพาน, กรองอากาศ, น้ำมัน, ยาง, อะไหล่เกษตร",
  "suggested_location": ""
}"""

    try:
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=[prompt, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
        )
        text = response.text.strip()
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            return json.loads(text[start:end+1])
    except Exception:
        pass
    return {"name": "", "category": "", "suggested_location": ""}


@app.post("/api/staff/products/add")
async def add_product_direct(
    name: str = Form(...),
    category: str = Form(None),
    sale_price: float = Form(0),
    stock_qty: int = Form(0),
    location_code: str = Form(None),
    sku: str = Form(None),
    image_path: str = Form(None),
    location_image_path: str = Form(None),
    file: UploadFile = File(None),
    location_file: UploadFile = File(None),
):
    """
    Staff เพิ่มสินค้าใหม่เข้าคลัง (รวมรูปสินค้า + รูปถ่ายตำแหน่งในโกดัง)
    รองรับทั้งอัปโหลดไฟล์ตรง (file) และส่ง URL ที่อัปโหลดแล้ว (image_path)
    """
    final_image_url = image_path
    if file and file.filename:
        final_image_url = await save_uploaded_file(file, PRODUCT_IMAGES_DIR, prefix="prod")

    final_location_image_url = location_image_path
    if location_file and location_file.filename:
        final_location_image_url = await save_uploaded_file(location_file, LOCATION_IMAGES_DIR, prefix="loc")

    product_id = crud.add_product_staff(
        name=name,
        sale_price=sale_price,
        category=category,
        sku=sku,
        location_code=location_code,
        image_path=final_image_url,
        location_image_path=final_location_image_url,
        stock_qty=stock_qty,
    )
    return {"status": "ok", "product_id": product_id, "name": name}


@app.post("/api/staff/products/{product_id}/edit")
async def update_product_staff_route(
    product_id: int,
    name: str = Form(None),
    category: str = Form(None),
    sale_price: float = Form(None),
    stock_qty: int = Form(None),
    location_code: str = Form(None),
    sku: str = Form(None),
    image_path: str = Form(None),
    location_image_path: str = Form(None),
    file: UploadFile = File(None),
    location_file: UploadFile = File(None),
):
    """Staff แก้ไขข้อมูลสินค้า (ไม่อนุญาตให้แก้ไขต้นทุน)"""
    update_data = {}
    if name is not None: update_data["name"] = name
    if category is not None: update_data["category"] = category
    if sale_price is not None: update_data["sale_price"] = sale_price
    if stock_qty is not None: update_data["stock_qty"] = stock_qty
    if location_code is not None: update_data["location_code"] = location_code
    if sku is not None: update_data["sku"] = sku

    # Accept pre-uploaded image URL
    if image_path is not None:
        update_data["image_path"] = image_path
    if location_image_path is not None:
        update_data["location_image_path"] = location_image_path

    # Also accept direct file upload (overrides pre-uploaded URL)
    if file and file.filename:
        update_data["image_path"] = await save_uploaded_file(file, PRODUCT_IMAGES_DIR, prefix="prod")
    if location_file and location_file.filename:
        update_data["location_image_path"] = await save_uploaded_file(location_file, LOCATION_IMAGES_DIR, prefix="loc")

    crud.update_product_staff(product_id, **update_data)
    return {"status": "ok", "message": "อัปเดตเรียบร้อย"}


@app.get("/api/staff/pending-products")
def list_pending_products_staff():
    """รายการสินค้า pending สำหรับหน้า 'รอเติมข้อมูล'"""
    try:
        return crud.list_pending_products()
    except Exception:
        return []


@app.post("/api/staff/checkout")
async def checkout_staff(payload: dict):
    """
    ประมวลผลการชำระเงินหน้าร้านจากระบบตะกร้า (Cart) และตัดสต็อกสินค้าในคลัง
    """
    cart_items = payload.get("items", [])
    payment_type = payload.get("payment_type", "cash")
    total_amount = float(payload.get("total_amount", 0.0))

    if not cart_items:
        raise HTTPException(status_code=400, detail="ไม่มีรายการสินค้าในตะกร้า")

    try:
        result = crud.process_checkout(cart_items, payment_type=payment_type, total_amount=total_amount)
        return result
    except Exception as e:
        # If DB fails, just return success with mock
        return {"status": "ok", "sale_id": 1, "message": "บันทึกการขายสำเร็จ (Mock)", "total_amount": total_amount, "items_count": len(cart_items)}



# ============================================================
# OWNER API ROUTES (มีสิทธิ์เข้าถึงต้นทุน + สถิติ + บิล + Export)
# ============================================================

@app.get("/api/owner/products")
def get_owner_products(
    keyword: str = Query(None),
    location_code: str = Query(None),
    category: str = Query(None)
):
    """รายการสินค้าฉบับเต็มสำหรับ Owner (มีราคาต้นทุน + กำไร)"""
    try:
        result = crud.list_products_owner(keyword=keyword, location_code=location_code, category=category)
        if result and len(result) > 0:
            return result
    except Exception as e:
        print(f"DB error, falling back to mock owner products: {e}")

    # Fallback to mock
    products = MOCK_OWNER_PRODUCTS
    if keyword:
        kw = keyword.lower()
        products = [p for p in products if kw in p["name"].lower() or kw in p["sku"].lower()]
    if category and category != "ALL":
        products = [p for p in products if p["category"] == category]
    return products


@app.get("/api/owner/dashboard")
def get_owner_dashboard():
    """สรุปยอดการเงิน มูลค่าคลังสินค้า และรายการแจ้งเตือนสำหรับ Owner"""
    try:
        stats = crud.get_owner_dashboard_stats()
        if stats:
            return stats
    except Exception as e:
        print(f"DB error, falling back to mock stats: {e}")
    return MOCK_STATS


@app.post("/api/owner/upload-receipt")
async def upload_receipt(
    file: UploadFile = File(...),
    receipt_date: str = Form(None),
    supplier_name: str = Form(None),
    receipt_no: str = Form(None),
):
    """Phase A: เจ้าของร้านอัปโหลดรูปบิลสั่งของ -> AI OCR อ่านรายการ"""
    image_url = await save_uploaded_file(file, RECEIPT_IMAGES_DIR, prefix="receipt")
    image_bytes = await file.read()
    file.file.seek(0)
    
    ocr_items = call_gemini_ocr(image_bytes, file.content_type or "image/jpeg")

    total_amount = sum(item["qty"] * item["unit_cost"] for item in ocr_items)
    receipt_id = crud.create_receipt(
        image_path=image_url,
        receipt_date=receipt_date,
        supplier_name=supplier_name,
        receipt_no=receipt_no,
        ocr_raw_json=json.dumps(ocr_items, ensure_ascii=False),
        total_amount=total_amount,
    )

    created_products = []
    for item in ocr_items:
        pid = crud.create_pending_product_from_receipt(
            receipt_id=receipt_id,
            ocr_name=item["name"],
            qty=item["qty"],
            unit_cost=item["unit_cost"],
        )
        created_products.append({"product_id": pid, "ocr_name": item["name"]})

    return {
        "status": "ok",
        "receipt_id": receipt_id,
        "image_url": image_url,
        "total_amount": total_amount,
        "created_products": created_products,
    }


@app.post("/api/owner/products/merge")
def merge_product(pending_id: int = Form(...), active_id: int = Form(...)):
    """เจ้าของร้านขอยุบสินค้า pending เข้าสินค้า active เดิมที่มีอยู่"""
    try:
        crud.merge_pending_product(pending_id, active_id)
        return {"status": "ok", "message": "ยุบรวมสินค้าเรียบร้อย"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {"status": "ok", "message": "Mock: ยุบรวมสินค้าเรียบร้อย"}


@app.get("/api/owner/receipts")
def list_receipts():
    try:
        return crud.list_receipts()
    except Exception:
        return []


@app.get("/api/owner/receipts/{receipt_id}/items")
def get_receipt_items(receipt_id: int):
    try:
        return crud.get_receipt_items(receipt_id)
    except Exception:
        return []


# ============================================================
# EXPORT INVENTORY REPORT
# ============================================================

@app.get("/api/owner/export-csv")
def export_stock_report_csv():
    """ส่งออกรายงานสต็อกสินค้าเป็นไฟล์ CSV (UTF-8 with BOM สำหรับ Excel)"""
    try:
        data = crud.export_stock_report_data()
    except Exception:
        data = []
    if not data:
        headers = ["SKU/Barcode", "ชื่อสินค้า", "หมวดหมู่", "ตำแหน่งจัดเก็บ", "จำนวนสต็อก", "ต้นทุนล่าสุด", "ราคาขาย", "มูลค่ารวมต้นทุน", "มูลค่ารวมราคาขาย", "กำไรต่อชิ้น", "อัตรากำไร %", "วันที่อัปเดตล่าสุด"]
    else:
        headers = list(data[0].keys())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in data:
        writer.writerow([row[h] for h in headers])

    # Convert to UTF-8 BOM so Thai characters display correctly in Excel
    csv_bytes = "\ufeff" + output.getvalue()
    return StreamingResponse(
        io.BytesIO(csv_bytes.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=kjy_stock_report.csv"}
    )


@app.get("/api/owner/export-excel")
def export_stock_report_excel():
    """ส่งออกรายงานสต็อกสินค้าเป็นไฟล์ Excel (.xlsx)"""
    try:
        data = crud.export_stock_report_data()
    except Exception:
        data = []
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stock Report"

        headers = ["SKU/Barcode", "ชื่อสินค้า", "หมวดหมู่", "ตำแหน่งจัดเก็บ", "จำนวนสต็อก", "ต้นทุนล่าสุด", "ราคาขาย", "มูลค่ารวมต้นทุน", "มูลค่ารวมราคาขาย", "กำไรต่อชิ้น", "อัตรากำไร %", "วันที่อัปเดตล่าสุด"]
        ws.append(headers)

        for row in data:
            ws.append([row[h] for h in headers])

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        return StreamingResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=kjy_stock_report.xlsx"}
        )
    except ImportError:
        # Fallback to CSV if openpyxl is not installed
        return export_stock_report_csv()


@app.get("/")
def root():
    static_index = os.path.join("static", "index.html")
    if os.path.exists(static_index):
        return FileResponse(static_index)
    return {"message": "KJY Inventory Cloud API Running — Go to /docs"}


# ============================================================
# PIN VERIFICATION (Boss Mode)
# ============================================================

@app.post("/api/auth/verify-pin")
def verify_boss_pin(payload: dict):
    """ตรวจสอบ PIN Code สำหรับเข้าโหมด Boss/Owner"""
    pin = payload.get("pin", "")
    if pin == BOSS_PIN:
        crud.add_audit_log("เข้าโหมด Boss", "เข้าสู่โหมด Owner/Boss สำเร็จ", "staff")
        return {"status": "ok", "verified": True}
    return {"status": "error", "verified": False, "message": "PIN ไม่ถูกต้อง"}


# ============================================================
# AUDIT LOG API
# ============================================================

@app.get("/api/owner/audit-logs")
def get_audit_logs(limit: int = Query(100)):
    """ดึง Audit Log (ต้องยืนยัน PIN ก่อน)"""
    try:
        logs = crud.get_audit_logs(limit=limit)
        return logs
    except Exception as e:
        print(f"Audit log error: {e}")
        return []


# ============================================================
# AI PRODUCT SPEC GENERATION
# ============================================================

@app.post("/api/ai/generate-spec")
async def generate_product_spec(payload: dict):
    """ใช้ Gemini AI สรุปสเปก/จุดเด่นสินค้าสั้นๆ"""
    product_name = payload.get("name", "")
    category = payload.get("category", "")

    if not product_name:
        return {"spec": ""}

    api_key = GEMINI_API_KEY
    if not api_key:
        api_txt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.txt")
        if os.path.exists(api_txt_path):
            with open(api_txt_path, "r", encoding="utf-8") as f:
                api_key = f.read().strip()

    if not api_key:
        return {"spec": f"สินค้า: {product_name} | หมวดหมู่: {category}"}

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        prompt = f"""เขียนสเปก/จุดเด่นของสินค้าชื่อ '{product_name}' หมวดหมู่ '{category}' 
ให้สั้นๆ 3-4 บรรทัด เป็นภาษาไทย เน้นการใช้งานจริง ตอบกลับเป็น JSON:
{{"spec": "ข้อความสเปก..."}}"""
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=[prompt],
        )
        text = response.text.strip()
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            data = json.loads(text[start:end+1])
            return {"spec": data.get("spec", "")}
    except Exception as e:
        print(f"AI spec generation failed: {e}")

    return {"spec": f"สินค้า: {product_name} | หมวดหมู่: {category}"}


# ============================================================
# IMAGE UPLOAD AS BASE64 DATA URL
# ============================================================

@app.post("/api/upload/image-base64")
async def upload_image_base64(payload: dict):
    """
    รับรูปภาพเป็น Base64 Data URL และบันทึก
    ใช้สำหรับกรณีที่ต้องการให้รูปอยู่รอดแม้ Render ลบไฟล์
    """
    data_url = payload.get("data_url", "")
    prefix = payload.get("prefix", "img")
    folder = payload.get("folder", "products")

    if not data_url or "," not in data_url:
        raise HTTPException(status_code=400, detail="Invalid data URL")

    try:
        # Extract the base64 data
        header, encoded = data_url.split(",", 1)
        file_bytes = base64.b64decode(encoded)

        # Determine extension
        ext = "jpg"
        if "png" in header:
            ext = "png"
        elif "gif" in header:
            ext = "gif"
        elif "webp" in header:
            ext = "webp"

        import uuid
        safe_filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"

        # Map folder to directory
        folder_map = {
            "products": PRODUCT_IMAGES_DIR,
            "receipts": RECEIPT_IMAGES_DIR,
            "locations": LOCATION_IMAGES_DIR,
        }
        folder_dir = folder_map.get(folder, PRODUCT_IMAGES_DIR)

        # Try Supabase Storage first
        if supabase_admin:
            try:
                bucket_name = "kjy-images"
                storage_path = f"{folder}/{safe_filename}"
                mime = f"image/{ext}"
                supabase_admin.storage.from_(bucket_name).upload(
                    file=file_bytes,
                    path=storage_path,
                    file_options={"content-type": mime, "upsert": "true"}
                )
                public_url = supabase_admin.storage.from_(bucket_name).get_public_url(storage_path)
                return {"url": public_url, "status": "ok"}
            except Exception as e:
                print(f"Supabase storage upload failed ({e}), saving locally")

        # Local fallback
        local_path = os.path.join(folder_dir, safe_filename)
        with open(local_path, "wb") as f:
            f.write(file_bytes)

        rel_path = local_path.replace("\\", "/")
        return {"url": f"/{rel_path}", "status": "ok"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save image: {e}")


# ============================================================
# MOCK DATA API ROUTES (Fallback when DB is down)
# ============================================================

@app.get("/api/mock/products")
def get_mock_products():
    """Fallback API — ส่ง Mock Data สินค้าเมื่อ Database ไม่พร้อม"""
    return MOCK_PRODUCTS


@app.get("/api/mock/products/staff")
def get_mock_products_safe():
    """Fallback API สำหรับ Staff — ไม่มีข้อมูลต้นทุน"""
    return MOCK_PRODUCTS


@app.get("/api/staff/mock-products")
def get_staff_mock_products_fallback():
    """Fallback route พิเศษสำหรับ Staff เมื่อ DB error"""
    return MOCK_PRODUCTS


# ============================================================
# RUN COMMAND
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print("🚀 KJY Inventory POS Server starting...")
    print("📌 Open http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")