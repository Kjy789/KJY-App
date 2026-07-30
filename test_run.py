"""
ทดสอบระบบ: จำลองสถานการณ์จริง
1. สร้างสินค้า 2 รายการ
2. สร้างบิลสั่งของ 1 ใบ พร้อม 2 รายการ
3. ยืนยัน match แล้วดูว่าต้นทุน/สต็อกอัปเดตถูกไหม
4. ดูประวัติต้นทุน และคำนวณกำไร
"""

import os
from config import DB_PATH
from database import init_db
import crud

# ลบ DB เก่าทิ้งก่อน เพื่อทดสอบสะอาดๆ
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

init_db()

print("\n--- 1. เพิ่มสินค้า ---")
p1 = crud.add_product(name="น็อตหกเหลี่ยม M8", category="น็อต-สกรู",
                       sale_price=3.5, location_code="A-1-05")
p2 = crud.add_product(name="สายพานร่อง B-52", category="สายพาน",
                       sale_price=180, location_code="B-2-10")
print(f"เพิ่มสินค้า id={p1}, id={p2}")

print("\n--- 2. สร้างบิลสั่งของ ---")
receipt_id = crud.create_receipt(
    image_path="images/receipts/bill_20260708_01.jpg",
    receipt_date="2026-07-08",
    supplier_name="ร้านอะไหล่รุ่งเรือง",
    receipt_no="INV-0099",
)
print(f"สร้างบิล id={receipt_id}")

item1 = crud.add_receipt_item(receipt_id, ocr_name="น็อต M8 หกเหลี่ยม", qty=100, unit_cost=1.2)
item2 = crud.add_receipt_item(receipt_id, ocr_name="สายพาน B52", qty=5, unit_cost=140)
print(f"เพิ่มรายการในบิล item_id={item1}, item_id={item2}")

print("\n--- 3. ยืนยัน match กับสินค้าที่มีอยู่ ---")
crud.confirm_receipt_item(item1, product_id=p1)
crud.confirm_receipt_item(item2, product_id=p2)
print("match และอัปเดตต้นทุน/สต็อกเรียบร้อย")

print("\n--- 4. ตรวจสอบผลลัพธ์ ---")
print(crud.get_product(p1))
print(crud.get_product(p2))

print("\n--- 5. ประวัติต้นทุน ---")
print(crud.get_cost_history(p1))

print("\n--- 6. คำนวณกำไร ---")
print(crud.calc_profit(p1))
print(crud.calc_profit(p2))

print("\n--- 7. ค้นหาสินค้า ---")
print(crud.search_products(keyword="น็อต"))


