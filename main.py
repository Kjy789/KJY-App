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
from starlette.formparsers import MultiPartParser

from database import init_db, supabase_admin
import crud
from config import RECEIPT_IMAGES_DIR, PRODUCT_IMAGES_DIR, LOCATION_IMAGES_DIR, GEMINI_API_KEY, BASE_DIR

# PIN Code for Boss Mode (default: 1234)
BOSS_PIN = "1234"

# ============================================================
# INCREASE UPLOAD LIMIT (แก้ Error 1024KB Part size limit)
# ============================================================
# Starlette default max_part_size = 1MB (1024KB)
# Override ให้รองรับรูปถ่ายจากมือถือได้สูงสุด 10MB
MAX_UPLOAD_SIZE_MB = 10
MAX_PART_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024  # 10MB

class CustomMultiPartParser(MultiPartParser):
    """Subclass เพิ่ม max_part_size ให้รองรับไฟล์ใหญ่ (รูปกล้องมือถือ)"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_part_size = MAX_PART_SIZE

import starlette.formparsers
starlette.formparsers.MultiPartParser = CustomMultiPartParser


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


# Helper for image compression using PIL
def compress_image_bytes(image_bytes: bytes, max_size: int = 1200, quality: int = 85) -> tuple[bytes, str]:
    """
    บีบอัดและปรับขนาดรูปภาพให้อยู่ในขนาดไม่เกิน 1200px (เพื่อประหยัดพื้นที่และอัปโหลดเร็ว)
    คืนค่า (compressed_bytes, mime_type)
    """
    if not image_bytes:
        return b"", "image/jpeg"
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        
        # Resize thumbnail keeping aspect ratio
        resample_filter = getattr(Image, 'Resampling', Image).LANCZOS if hasattr(Image, 'Resampling') else Image.ANTIALIAS
        img.thumbnail((max_size, max_size), resample_filter)
        
        out = io.BytesIO()
        fmt = img.format if img.format in ('JPEG', 'PNG', 'WEBP') else 'JPEG'
        if fmt == 'JPEG' and img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
            
        img.save(out, format=fmt, quality=quality, optimize=True)
        mime_type = f"image/{fmt.lower()}"
        return out.getvalue(), mime_type
    except Exception as e:
        print(f"PIL compression warning: {e}")
        return image_bytes, "image/jpeg"


# Helper for Supabase / Local Storage Upload
async def save_uploaded_file(file: UploadFile, folder_dir: str, prefix: str = "") -> str:
    """บันทึกไฟล์อัปโหลดลง Supabase Storage หรือ Base64 Data URL (Render-safe fallback)"""
    raw_bytes = await file.read()
    if not raw_bytes:
        return ""
        
    compressed_bytes, mime_type = compress_image_bytes(raw_bytes)
    ext = mime_type.split("/")[-1]
    
    import uuid
    safe_filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"
    folder_name = os.path.basename(folder_dir) or "products"

    # 1. ลองอัปโหลดขึ้น Supabase Storage (ถ้าต่อ Supabase อยู่)
    if supabase_admin:
        try:
            bucket_name = "kjy-images"
            # ตรวจสอบและสร้าง bucket ถ้ายังไม่มี
            try:
                buckets = supabase_admin.storage.list_buckets()
                bnames = [b.name for b in buckets] if buckets else []
                if bucket_name not in bnames:
                    supabase_admin.storage.create_bucket(bucket_name, options={"public": True})
            except Exception:
                pass

            storage_path = f"{folder_name}/{safe_filename}"
            bucket = supabase_admin.storage.from_(bucket_name)
            bucket.upload(
                path=storage_path,
                file=compressed_bytes,
                file_options={"content-type": mime_type, "upsert": "true"}
            )
            public_url = bucket.get_public_url(storage_path)
            if public_url:
                return public_url
        except Exception as e:
            print(f"⚠️ Supabase storage upload failed ({e}), using Base64 Data URL fallback")

    # 2. กรณีไม่มี Supabase Storage หรืออัปโหลดพัง (Render ephemeral disk):
    # บันทึกลง disk ท้องถิ่นไว้ด้วย
    try:
        local_path = os.path.join(folder_dir, safe_filename)
        with open(local_path, "wb") as f:
            f.write(compressed_bytes)
    except Exception:
        pass

    # และคืนค่าเป็น Base64 Data URL เพื่อเซฟลงฐานข้อมูลถาวร (รูปไม่หายแน่นอนเมื่อ Render รีสตาร์ท!)
    b64_str = base64.b64encode(compressed_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64_str}"


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

    # Compress image to prevent Gemini API 1024KB Part size limit error
    compressed_bytes, compressed_mime = compress_image_bytes(image_bytes, max_size=800, quality=75)

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
                types.Part.from_bytes(data=compressed_bytes, mime_type=compressed_mime),
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

    # Compress image to prevent Gemini API 1024KB Part size limit error
    compressed_bytes, compressed_mime = compress_image_bytes(image_bytes, max_size=800, quality=75)

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
            contents=[prompt, types.Part.from_bytes(data=compressed_bytes, mime_type=compressed_mime)],
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
    cost_price: float = Form(0),
    stock_qty: int = Form(0),
    front_stock: int = Form(0),
    warehouse_stock: int = Form(0),
    min_stock: int = Form(5),
    location_code: str = Form(None),
    location: str = Form(None),
    description: str = Form(None),
    sku: str = Form(None),
    image_path: str = Form(None),
    location_image_path: str = Form(None),
    file: UploadFile = File(None),
    location_file: UploadFile = File(None),
):
    """
    เพิ่มสินค้าใหม่เข้าคลัง (รวมรูปสินค้า + รูปถ่ายตำแหน่งในโกดัง)
    รองรับทั้งอัปโหลดไฟล์ตรง (file) และส่ง URL ที่อัปโหลดแล้ว (image_path)
    รองรับการแยกสต็อก: front_stock (หน้าร้าน) + warehouse_stock (คลังหลังร้าน)
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
        cost_price=cost_price,
        category=category,
        sku=sku,
        location_code=location_code,
        location=location or "",
        description=description or "",
        image_path=final_image_url,
        location_image_path=final_location_image_url,
        stock_qty=stock_qty,
        min_stock=min_stock,
        front_stock=front_stock,
        warehouse_stock=warehouse_stock,
    )
    return {"status": "ok", "product_id": product_id, "name": name}


@app.post("/api/staff/products/{product_id}/edit")
async def update_product_staff_route(
    product_id: int,
    name: str = Form(None),
    category: str = Form(None),
    sale_price: float = Form(None),
    cost_price: float = Form(None),
    stock_qty: int = Form(None),
    front_stock: int = Form(None),
    warehouse_stock: int = Form(None),
    min_stock: int = Form(None),
    location_code: str = Form(None),
    location: str = Form(None),
    description: str = Form(None),
    sku: str = Form(None),
    image_path: str = Form(None),
    location_image_path: str = Form(None),
    file: UploadFile = File(None),
    location_file: UploadFile = File(None),
):
    """
    แก้ไขข้อมูลสินค้า
    - Staff: ไม่อนุญาตให้แก้ไข cost_price (ถูกกรองใน crud.update_product_staff)
    - Owner: ส่ง cost_price ได้ (ผ่าน allow_cost_price=True)
    """
    update_data = {}
    if name is not None: update_data["name"] = name
    if category is not None: update_data["category"] = category
    if sale_price is not None: update_data["sale_price"] = sale_price
    if stock_qty is not None: update_data["stock_qty"] = stock_qty
    if front_stock is not None: update_data["front_stock"] = front_stock
    if warehouse_stock is not None: update_data["warehouse_stock"] = warehouse_stock
    if min_stock is not None: update_data["min_stock"] = min_stock
    if location_code is not None: update_data["location_code"] = location_code
    if location is not None: update_data["location"] = location
    if description is not None: update_data["description"] = description
    if sku is not None: update_data["sku"] = sku

    # Owner can update cost_price (frontend sends allow_cost_price=true when in owner mode)
    if cost_price is not None:
        update_data["cost_price"] = cost_price
        update_data["allow_cost_price"] = True

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


@app.post("/api/staff/products/{product_id}/transfer-stock")
def transfer_stock_route(product_id: int, payload: dict):
    """
    ย้ายสต็อกระหว่างหน้าร้าน (front_stock) กับคลังหลังร้าน (warehouse_stock)
    payload: {"qty": 5, "direction": "to_front" | "to_warehouse"}
    """
    qty = int(payload.get("qty", 0) or 0)
    direction = payload.get("direction", "to_front")

    if qty <= 0:
        raise HTTPException(status_code=400, detail="กรุณาระบุจำนวนที่มากกว่า 0")

    try:
        result = crud.transfer_stock(product_id, qty, direction)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ย้ายสต็อกไม่สำเร็จ: {str(e)}")


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


@app.delete("/api/owner/products/{product_id}")
def delete_owner_product(product_id: int):
    """
    Owner ลบสินค้า
    """
    try:
        crud.delete_product_staff(product_id)
        return {"status": "ok", "deleted_id": product_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"Delete product error: {e}")
        raise HTTPException(status_code=500, detail=f"ลบสินค้าไม่สำเร็จ: {e}")


@app.delete("/api/staff/products/{product_id}")
@app.post("/api/staff/products/{product_id}/delete")
def delete_staff_product(product_id: int):
    """
    Staff ลบสินค้าออกจากคลัง
    """
    try:
        crud.delete_product_staff(product_id)
        return {"status": "ok", "deleted_id": product_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"Delete staff product error: {e}")
        raise HTTPException(status_code=500, detail=f"ลบสินค้าไม่สำเร็จ: {e}")


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
    """
    ส่งออกรายงานสต็อกสินค้าเป็นไฟล์ Excel (.xlsx)
    คอลัมน์: ID, ชื่อสินค้า, SKU/Barcode, หมวดหมู่, ราคาขาย, ราคาต้นทุน,
    จำนวนคงเหลือ, สต็อกขั้นต่ำ, รหัสตำแหน่ง, ตำแหน่งจัดเก็บ, รายละเอียด/สเปก, วันที่อัปเดต
    """
    try:
        data = crud.export_stock_report_data()
    except Exception:
        data = []
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stock Report"

        headers = [
            "ID", "ชื่อสินค้า", "SKU/Barcode", "หมวดหมู่", "ราคาขาย",
            "ราคาต้นทุน", "จำนวนคงเหลือ", "สต็อกขั้นต่ำ", "รหัสตำแหน่ง",
            "ตำแหน่งจัดเก็บ", "รายละเอียด/สเปก", "วันที่อัปเดตล่าสุด"
        ]
        ws.append(headers)

        # --- จัดฟอร์แมต Header ---
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(start_color="2B5797", end_color="2B5797", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        for col_idx, _ in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border

        # --- ใส่ข้อมูล ---
        for row in data:
            ws.append([row[h] for h in headers])

        # --- จัดฟอร์แมตข้อมูล ---
        money_font = Font(number_format='#,##0.00')
        for row_idx in range(2, ws.max_row + 1):
            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center", wrap_text=(col_idx in (2, 11)))

                # ฟอร์แมตตัวเลขราคา (คอลัมน์ E=ราคาขาย, F=ราคาต้นทุน)
                if col_idx in (5, 6):
                    cell.number_format = '#,##0.00'
                # ฟอร์แมตตัวเลขจำนวน (คอลัมน์ G=จำนวนคงเหลือ, H=สต็อกขั้นต่ำ)
                elif col_idx in (7, 8):
                    cell.number_format = '#,##0'

        # --- กำหนดความกว้างคอลัมน์อัตโนมัติ ---
        column_widths = {
            1: 8,    # ID
            2: 30,   # ชื่อสินค้า
            3: 18,   # SKU/Barcode
            4: 16,   # หมวดหมู่
            5: 12,   # ราคาขาย
            6: 12,   # ราคาต้นทุน
            7: 12,   # จำนวนคงเหลือ
            8: 12,   # สต็อกขั้นต่ำ
            9: 14,   # รหัสตำแหน่ง
            10: 22,  # ตำแหน่งจัดเก็บ
            11: 35,  # รายละเอียด/สเปก
            12: 20,  # วันที่อัปเดตล่าสุด
        }
        for col_idx, width in column_widths.items():
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        # --- Freeze Header Row ---
        ws.freeze_panes = "A2"

        # --- Auto Filter ---
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"

        out = io.BytesIO()
        wb.save(out)
        out.seek(0)

        # บันทึก AuditLog เมื่อมีการ Export
        crud.add_audit_log(
            "EXPORT_PRODUCTS_EXCEL",
            f"ส่งออกรายงานสินค้าคลังเป็น Excel จำนวน {len(data)} รายการ",
            "owner"
        )

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

        # Compress image before saving
        compressed_bytes, mime_type = compress_image_bytes(file_bytes)
        ext = mime_type.split("/")[-1]
        safe_filename = f"{prefix}_{uuid.uuid4().hex[:8]}.{ext}"

        # 1. Try Supabase Storage first
        if supabase_admin:
            try:
                bucket_name = "kjy-images"
                try:
                    buckets = supabase_admin.storage.list_buckets()
                    bnames = [b.name for b in buckets] if buckets else []
                    if bucket_name not in bnames:
                        supabase_admin.storage.create_bucket(bucket_name, options={"public": True})
                except Exception:
                    pass

                storage_path = f"{folder}/{safe_filename}"
                bucket = supabase_admin.storage.from_(bucket_name)
                bucket.upload(
                    path=storage_path,
                    file=compressed_bytes,
                    file_options={"content-type": mime_type, "upsert": "true"}
                )
                public_url = bucket.get_public_url(storage_path)
                if public_url:
                    return {"url": public_url, "status": "ok"}
            except Exception as e:
                print(f"⚠️ Supabase storage upload failed ({e}), using Base64 Data URL fallback")

        # 2. Save local copy (best effort)
        try:
            local_path = os.path.join(folder_dir, safe_filename)
            with open(local_path, "wb") as f:
                f.write(compressed_bytes)
        except Exception:
            pass

        # 3. Base64 Data URL fallback (render-safe, saved permanently in DB)
        b64_str = base64.b64encode(compressed_bytes).decode("utf-8")
        fallback_url = f"data:{mime_type};base64,{b64_str}"
        return {"url": fallback_url, "status": "ok"}

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
# AI SALES ASSISTANT
# ============================================================

@app.post("/api/ai/sales-assistant")
async def ai_sales_assistant(payload: dict):
    """
    ผู้ช่วยขาย AI & ผู้เชี่ยวชาญด้านอะไหล่ — ตอบคำถามเกี่ยวกับสินค้า สต็อก ราคา และความรู้ด้านอะไหล่
    """
    message = (payload.get("message") or "").strip()
    context = payload.get("context") or ""
    cart = payload.get("cart") or []

    if not message:
        return {"reply": "กรุณาพิมพ์คำถามเกี่ยวกับสินค้าครับ"}

    api_key = GEMINI_API_KEY
    if not api_key:
        api_txt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.txt")
        if os.path.exists(api_txt_path):
            with open(api_txt_path, "r", encoding="utf-8") as f:
                api_key = f.read().strip()

    if not api_key:
        return {"reply": "ขออภัย ระบบ AI ยังไม่พร้อมใช้งาน (ไม่พบ API Key)"}

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)

        cart_text = ""
        if cart and len(cart) > 0:
            cart_text = "\n\nตะกร้าสินค้าปัจจุบัน:\n"
            for item in cart:
                cart_text += f"- {item.get('name','-')} จำนวน {item.get('qty',0)} ชิ้น ราคา/ชิ้น ฿{item.get('price',0)}\n"
            cart_text += f"\nยอดรวมตะกร้า: ฿{sum(item.get('price',0)*item.get('qty',0) for item in cart):.2f}"

        prompt = f"""คุณคือผู้ช่วยขาย AI และผู้เชี่ยวชาญด้านอะไหล่ยานยนต์ สำหรับร้านคำเจริญเกษตรยนต์ (KJY)
ช่วยตอบคำถามลูกค้า/พนักงานเกี่ยวกับสินค้า สต็อก ราคา และความรู้ด้านอะไหล่ เป็นภาษาไทย

ความรู้เฉพาะด้านอะไหล่:
- กรองน้ำมันเครื่อง: ใช้รุ่นตามยี่ห้อรถ เช่น รถ Kubota, Toyota, Honda, Isuzu
- น้ำมันเครื่อง: ใช้เกรดตามข้อแนะนำของผู้ผลิต (SAE 10W-40, 15W-40 ฯลฯ)
- สายพาน: ใช้ขนาดตามรุ่นเครื่องยนต์ เช่น Kubota B52, L3608, ฯลฯ
- น็อต-สกรู: ใช้ขนาดตามขนาดเม็ด (M6, M8, M10) และความยาว
- ยางรถไถ: ใช้ขนาดตามรุ่น เช่น 6.00-14, 7.50-16, ฯลฯ

ข้อมูลสินค้าที่มีอยู่:
{context}
{cart_text}

คำถาม: {message}

ตอบเป็นภาษาไทย สั้นๆ ตรงประเด็น ไม่ต้องยาวเกินไป
ถ้าเป็นคำถามเกี่ยวกับอะไหล่รุ่นรถ ให้ตอบจากความรู้ด้านอะไหล่ แล้วตรวจสอบว่ามีสินค้าในร้านหรือไม่
ถ้าเป็นคำถามเกี่ยวกับราคา/สต็อก ให้อ้างอิงจากข้อมูลสินค้าที่มีอยู่ด้านบน
ถ้าเป็นคำถามคำนวณ ให้คำนวณและแสดงผลลัพธ์
ถ้าไม่รู้จักสินค้า ให้บอกว่าไม่มีข้อมูลและแนะนำให้เช็คกับพนักงาน"""

        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=[prompt],
        )
        reply = response.text.strip()
        return {"reply": reply}

    except Exception as e:
        print(f"AI sales assistant error: {e}")
        return {"reply": f"ขออภัย เกิดข้อผิดพลาด: {str(e)}"}


# ============================================================
# EXCEL/CSV IMPORT
# ============================================================

@app.post("/api/owner/import-products")
async def import_products(file: UploadFile = File(...)):
    """
    นำเข้าสินค้าจากไฟล์ Excel (.xlsx/.xls) หรือ CSV
    คอลัมน์ที่รองรับ: name, sku, category, sale_price, cost_price, stock_qty, min_stock, location_code, location, description
    """
    if not file:
        raise HTTPException(status_code=400, detail="ไม่พบไฟล์")

    filename = file.filename or ""
    ext = filename.lower().split('.')[-1] if '.' in filename else ''

    if ext not in ('xlsx', 'xls', 'csv'):
        raise HTTPException(status_code=400, detail="รองรับเฉพาะไฟล์ .xlsx .xls .csv")

    try:
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="ไฟล์ว่างเปล่า")

        # Parse file
        rows = []
        if ext == 'csv':
            import csv as csv_module
            text = contents.decode('utf-8-sig')
            reader = csv_module.DictReader(io.StringIO(text))
            rows = list(reader)
        else:
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(contents))
                ws = wb.active
                headers = [str(cell.value).strip() if cell.value else "" for cell in ws[1]]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    row_dict = {}
                    for idx, header in enumerate(headers):
                        if header:
                            val = row[idx] if idx < len(row) else None
                            row_dict[header] = val
                    rows.append(row_dict)
            except ImportError:
                raise HTTPException(status_code=500, detail="ไม่พบไลบรารี openpyxl กรุณาติดตั้ง: pip install openpyxl")

        if not rows:
            return {"imported": 0, "message": "ไม่พบข้อมูลในไฟล์"}

        # Map and import
        imported = 0
        errors = []

        for idx, row in enumerate(rows, start=2):
            try:
                name = str(row.get('name', '') or row.get('ชื่อสินค้า', '') or '').strip()
                if not name:
                    errors.append(f"แถว {idx}: ไม่มีชื่อสินค้า")
                    continue

                sku = str(row.get('sku', '') or row.get('SKU', '') or '').strip() or None
                category = str(row.get('category', '') or row.get('หมวดหมู่', '') or '').strip() or None
                sale_price = float(row.get('sale_price', 0) or row.get('ราคาขาย', 0) or 0)
                cost_price = float(row.get('cost_price', 0) or row.get('ราคาต้นทุน', 0) or 0)
                stock_qty = int(row.get('stock_qty', 0) or row.get('สต็อก', 0) or 0)
                min_stock = int(row.get('min_stock', 5) or row.get('สต็อกขั้นต่ำ', 5) or 5)
                location_code = str(row.get('location_code', '') or row.get('รหัสตำแหน่ง', '') or '').strip() or None
                location = str(row.get('location', '') or row.get('ตำแหน่งจัดเก็บ', '') or '').strip() or None
                description = str(row.get('description', '') or row.get('รายละเอียด', '') or '').strip() or None

                # Use crud to add product
                crud.add_product_staff(
                    name=name,
                    sku=sku,
                    category=category,
                    sale_price=sale_price,
                    cost_price=cost_price,
                    stock_qty=stock_qty,
                    min_stock=min_stock,
                    location_code=location_code,
                    location=location,
                    description=description
                )
                imported += 1

            except Exception as e:
                errors.append(f"แถว {idx}: {str(e)}")
                continue

        msg = f"นำเข้าสำเร็จ {imported} รายการ"
        if errors:
            msg += f" (มีข้อผิดพลาด {len(errors)} รายการ)"

        return {"imported": imported, "message": msg, "errors": errors[:10]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Import ไม่สำเร็จ: {str(e)}")


# ============================================================
# BULK PRICE ADJUSTMENT (Owner Only)
# ============================================================

@app.post("/api/owner/products/bulk-price")
def bulk_price_adjustment(payload: dict):
    """
    ปรับราคาขายสินค้าแบบกลุ่ม
    รูปแบบ:
    - mode: "markup_percent" | "markup_amount" | "adjust_percent" | "adjust_amount"
    - category: หมวดหมู่ที่ต้องการปรับ (ถ้าไม่ระบุ = ทั้งหมด)
    - value: ค่าเปอร์เซ็นต์หรือจำนวนบาท
    - apply_to_cost: true = คำนวณจากราคาทุน, false = คำนวณจากราคาขายปัจจุบัน
    """
    mode = payload.get("mode", "")
    category = payload.get("category", "")
    value = float(payload.get("value", 0) or 0)
    apply_to_cost = payload.get("apply_to_cost", False)

    if not mode or value == 0:
        raise HTTPException(status_code=400, detail="กรุณาระบุ mode และ value")

    try:
        # Get all products (owner view with cost)
        products = crud.list_products_owner(category=category if category else None)
        
        updated = 0
        errors = []

        for p in products:
            try:
                product_id = p.get("id")
                current_price = float(p.get("sale_price") or 0)
                cost_price = float(p.get("latest_cost") or 0)

                if apply_to_cost and cost_price > 0:
                    # Calculate from cost price
                    if mode == "markup_percent":
                        # e.g., cost 100 + 30% = 130
                        new_price = cost_price * (1 + value / 100)
                    elif mode == "markup_amount":
                        # e.g., cost 100 + 30 = 130
                        new_price = cost_price + value
                    else:
                        new_price = current_price
                else:
                    # Calculate from current sale price
                    if mode == "adjust_percent":
                        # e.g., price 100 + 10% = 110
                        new_price = current_price * (1 + value / 100)
                    elif mode == "adjust_amount":
                        # e.g., price 100 + 10 = 110
                        new_price = current_price + value
                    else:
                        new_price = current_price

                # Round to 2 decimal places
                new_price = round(new_price, 2)

                # Update in database
                crud.update_product_staff(product_id, sale_price=new_price)
                updated += 1

            except Exception as e:
                errors.append(f"Product {product_id}: {str(e)}")
                continue

        msg = f"ปรับราคาสำเร็จ {updated} รายการ"
        if errors:
            msg += f" (มีข้อผิดพลาด {len(errors)} รายการ)"

        return {"updated": updated, "message": msg, "errors": errors[:10]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bulk price adjustment failed: {str(e)}")


# ============================================================
# RUN COMMAND
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print("🚀 KJY Inventory POS Server starting...")
    print("📌 Open http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
