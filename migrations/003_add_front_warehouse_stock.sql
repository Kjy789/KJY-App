-- ============================================================
-- Migration 003: Add Front Stock & Warehouse Stock fields
-- ร้านคำเจริญเกษตรยนต์ (Kamjarenkasetyon)
--
-- เพิ่มคอลัมน์ front_stock (สต็อกหน้าร้าน) และ warehouse_stock (สต็อกคลังหลังร้าน)
-- stock_qty ยังใช้เป็นคอลัมน์หลัก = front_stock + warehouse_stock (สำหรับ Backward Compat)
-- ============================================================

-- 1. เพิ่มคอลัมน์ front_stock (สต็อกหน้าร้าน)
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS front_stock INT DEFAULT 0 NOT NULL;

-- 2. เพิ่มคอลัมน์ warehouse_stock (สต็อกคลังหลังร้าน)
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS warehouse_stock INT DEFAULT 0 NOT NULL;

-- 3. Backfill: ย้ายค่าจาก stock_qty ปัจจุบันไปยัง front_stock
--    (ให้ front_stock = stock_qty เดิม เพื่อไม่ให้สินค้าเก่าหายจากการมองเห็น)
UPDATE public.products
SET front_stock = stock_qty
WHERE front_stock = 0 AND stock_qty > 0;

-- 4. คอลัมน์ stock_qty จะถูกประมวลผลเป็นผลรวมอัตโนมัติ
--    (เราจะอัปเดต stock_qty = front_stock + warehouse_stock ใน code)
--    ตรงนี้เผื่อ schema trigger ให้เป็น Auto-sync ด้วย
CREATE OR REPLACE FUNCTION public.sync_stock_qty()
RETURNS TRIGGER AS $$
BEGIN
    NEW.stock_qty := COALESCE(NEW.front_stock, 0) + COALESCE(NEW.warehouse_stock, 0);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_stock_qty ON public.products;
CREATE TRIGGER trg_sync_stock_qty
    BEFORE INSERT OR UPDATE ON public.products
    FOR EACH ROW EXECUTE FUNCTION public.sync_stock_qty();

-- 5. Index สำหรับค้นหา stock
CREATE INDEX IF NOT EXISTS idx_products_front_stock ON public.products (front_stock);
CREATE INDEX IF NOT EXISTS idx_products_warehouse_stock ON public.products (warehouse_stock);