# 🚀 KJY Inventory Cloud App (คำเจริญเกษตรยนต์)

ระบบจัดการคลังสินค้า อะไหล่ และอุปกรณ์เกษตร รองรับการใช้งานบนคอมพิวเตอร์, iPad และโทรศัพท์มือถือ (Cloud-ready)

---

## 🛠️ โครงสร้างระบบ & เทคโนโลยี (Tech Stack)

1. **Backend**: Python (FastAPI) พร้อม Deploy บน Cloud (เช่น Render / Railway / Fly.io)
2. **Database & Storage**: Supabase (PostgreSQL + Supabase Storage + Supabase Auth) พร้อม Local SQLite Fallback
3. **Frontend**: HTML5 + Tailwind CSS + JavaScript (Modern Responsive Dashboard, Camera Barcode Scanner, AI Product Vision)
4. **AI & Vision**: Google Gemini API สำหรับ OCR อ่านบิลสั่งซื้อสินค้า และระบุชื่อสินค้า/หมวดหมู่อัตโนมัติจากรูปถ่าย

---

## 🔑 ระบบสิทธิ์ผู้ใช้งาน (Role-Based Access Control)

1. **STAFF (ลูกน้อง / ครอบครัว)**:
   - สแกนบาร์โค้ด, ค้นหาสินค้าแบบ Real-time, ถ่ายรูปสินค้าและรูปถ่ายตำแหน่งโกดัง
   - **ปิดกั้นการเข้าถึงราคาต้นทุนและกำไรโดยเด็ดขาด** (มองเห็นเฉพาะ ราคาขาย, สต็อกคงเหลือ, SKU, หมวดหมู่ และตำแหน่งโกดัง)
2. **OWNER (เจ้าของร้าน)**:
   - มองเห็นข้อมูลทั้งหมด (รวมถึงต้นทุนล่าสุด, มูลค่าสต็อกรวม, กำไรต่อหน่วย, และอัตรากำไร %)
   - ระบบอัปโหลดบิลสั่งซื้อ (AI OCR) และปุ่มดาวน์โหลดรายงานสต็อกสินค้า Excel/CSV (Vertical Layout)

---

## ⚙️ ขั้นตอนการตั้งค่า Supabase & การรันระบบ

### 1. ตั้งค่า Supabase Database
1. เปิด [Supabase Dashboard](https://supabase.com) แล้วสร้าง Project ใหม่
2. ไปที่เมนู **SQL Editor** แล้วคัดลอกคำสั่งทั้งหมดจากไฟล์ `schema.sql` ไปกด **Run**
3. ไปที่ **Project Settings -> API** นำค่า `SUPABASE_URL`, `SUPABASE_KEY` และ `SUPABASE_SERVICE_ROLE_KEY` ไปใส่ในไฟล์ `.env`

### 2. ติดตั้งและรันบนเครื่อง Local
```bash
pip install -r requirements.txt  # หรือ uvicorn fastapi google-genai supabase python-dotenv openpyxl
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
เปิดเบราว์เซอร์ไปที่: `http://127.0.0.1:8000`

---

## 📊 รายงานสต็อกสินค้า (Excel Export)
ระบบสร้างไฟล์รายงาน Excel (.xlsx) และ CSV ตามโครงสร้าง Vertical Layout:
- **Header แถวบนสุด (Row 1)**: `[SKU/Barcode | ชื่อสินค้า | หมวดหมู่ | ตำแหน่งจัดเก็บ | จำนวนสต็อก | ต้นทุนล่าสุด | ราคาขาย | มูลค่ารวมต้นทุน | มูลค่ารวมราคาขาย | กำไรต่อชิ้น | อัตรากำไร % | วันที่อัปเดตล่าสุด]`
- **ข้อมูลสินค้า (Row 2 เป็นต้นไป)**
