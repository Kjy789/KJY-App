-- ============================================================
-- KJY Fix: แก้ปัญหา RLS บล็อก service_role / anon
-- วิธีใช้: ไปที่ Supabase Dashboard -> SQL Editor -> New Query -> วางโค้ดนี้ -> Run
-- ============================================================

-- ============================================================
-- STEP 1: ลบ RLS Policies เก่าที่เข้มงวดเกินไป (ที่ต้อง authenticated)
-- ============================================================

-- Products policies
DROP POLICY IF EXISTS "Authenticated users can read products" ON public.products;
DROP POLICY IF EXISTS "Authenticated users can insert products" ON public.products;
DROP POLICY IF EXISTS "Authenticated users can update products" ON public.products;
DROP POLICY IF EXISTS "Owner can delete products" ON public.products;
DROP POLICY IF EXISTS "Public read products" ON public.products;
DROP POLICY IF EXISTS "Public insert products" ON public.products;
DROP POLICY IF EXISTS "Public update products" ON public.products;
DROP POLICY IF EXISTS "Public delete products" ON public.products;
DROP POLICY IF EXISTS "Allow all for service_role" ON public.products;
DROP POLICY IF EXISTS "Allow anon read products" ON public.products;
DROP POLICY IF EXISTS "Allow anon insert products" ON public.products;
DROP POLICY IF EXISTS "Allow anon update products" ON public.products;
DROP POLICY IF EXISTS "Allow anon delete products" ON public.products;

-- Locations policies
DROP POLICY IF EXISTS "Allow all for locations" ON public.locations;
DROP POLICY IF EXISTS "Public read locations" ON public.locations;

-- Purchase receipts policies
DROP POLICY IF EXISTS "Authenticated users can select receipts" ON public.purchase_receipts;
DROP POLICY IF EXISTS "Authenticated users can insert receipts" ON public.purchase_receipts;
DROP POLICY IF EXISTS "Owner can update receipts" ON public.purchase_receipts;
DROP POLICY IF EXISTS "Public read receipts" ON public.purchase_receipts;
DROP POLICY IF EXISTS "Allow all purchase_receipts" ON public.purchase_receipts;

-- Receipt items policies
DROP POLICY IF EXISTS "Allow all receipt_items" ON public.receipt_items;

-- Cost history policies
DROP POLICY IF EXISTS "Owner can access cost history" ON public.cost_history;
DROP POLICY IF EXISTS "Allow all cost_history" ON public.cost_history;

-- Storage policies
DROP POLICY IF EXISTS "Public Read Access for kjy-images" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated Upload Access for kjy-images" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated Update Access for kjy-images" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated Delete Access for kjy-images" ON storage.objects;
DROP POLICY IF EXISTS "Allow public read kjy-images" ON storage.objects;
DROP POLICY IF EXISTS "Allow public upload kjy-images" ON storage.objects;
DROP POLICY IF EXISTS "Allow public update kjy-images" ON storage.objects;
DROP POLICY IF EXISTS "Allow public delete kjy-images" ON storage.objects;


-- ============================================================
-- STEP 2: ปิด RLS ชั่วคราว (เพื่อให้ service_role + anon เข้าถึงได้)
-- เหมาะสำหรับแอปที่จัดการ Auth/Permission ฝั่ง Backend เอง (FastAPI)
-- ============================================================

ALTER TABLE public.products DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.locations DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.purchase_receipts DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.receipt_items DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.cost_history DISABLE ROW LEVEL SECURITY;


-- ============================================================
-- STEP 3: Grant สิทธิ์ให้ทุก role สามารถเข้าถึงตารางได้
-- ============================================================

GRANT ALL ON public.products TO anon, authenticated, service_role;
GRANT ALL ON public.locations TO anon, authenticated, service_role;
GRANT ALL ON public.purchase_receipts TO anon, authenticated, service_role;
GRANT ALL ON public.receipt_items TO anon, authenticated, service_role;
GRANT ALL ON public.cost_history TO anon, authenticated, service_role;

-- Grant sequence usage (เพื่อให้ INSERT ได้)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;


-- ============================================================
-- STEP 4: ตรวจสอบว่าคอลัมน์ migration มีครบหรือยัง
-- (เพิ่ม description, min_stock, location ถ้ายังไม่มี)
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='products' AND column_name='description') THEN
        ALTER TABLE public.products ADD COLUMN description TEXT DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='products' AND column_name='min_stock') THEN
        ALTER TABLE public.products ADD COLUMN min_stock INT DEFAULT 5;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='products' AND column_name='location') THEN
        ALTER TABLE public.products ADD COLUMN location TEXT DEFAULT '';
    END IF;
END $$;


-- ============================================================
-- STEP 5: ตั้งค่า Storage Bucket ให้ public
-- ============================================================

-- สร้าง bucket (ถ้ายังไม่มี)
INSERT INTO storage.buckets (id, name, public)
VALUES ('kjy-images', 'kjy-images', true)
ON CONFLICT (id) DO UPDATE SET public = true;

-- Storage Policies (อนุญาตทุกคนอ่าน/เขียน/ลบ ใน kjy-images)
CREATE POLICY "Allow public read kjy-images"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'kjy-images');

CREATE POLICY "Allow public upload kjy-images"
    ON storage.objects FOR INSERT
    WITH CHECK (bucket_id = 'kjy-images');

CREATE POLICY "Allow public update kjy-images"
    ON storage.objects FOR UPDATE
    USING (bucket_id = 'kjy-images');

CREATE POLICY "Allow public delete kjy-images"
    ON storage.objects FOR DELETE
    USING (bucket_id = 'kjy-images');


-- ============================================================
-- ✅ เสร็จ! ลอง Refresh แอปบน Render แล้วทดสอบอีกครั้ง
-- ============================================================
