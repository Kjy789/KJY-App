/**
 * KJY Inventory POS Cloud — Frontend Logic (app.js v5.0)
 * ร้านคำเจริญเกษตรยนต์ (Kamjarenkasetyon)
 * 
 * Modern POS UI — Clean, Light, Card-based Design
 * - 3-column layout: Sidebar | Product Grid | Cart Panel
 * - Stock badge: top-left of product card
 * - Inline qty controls after adding to cart
 * - Cart items with thumbnails
 * - Fallback to mock data if API fails
 */

'use strict';

// ==========================================================================
// IMAGE COMPRESSION HELPER
// ==========================================================================

/**
 * บีบอัดรูปภาพจากกล้องมือถือก่อนอัปโหลด
 * - ย่อขนาดให้กว้าง/สูงสุดไม่เกิน 800px
 * - บีบอัดคุณภาพ JPEG/WEBP เหลือ 0.7-0.8
 * - คืนค่าเป็น Promise<Blob> เพื่อให้ Base64 เล็กลง (ไม่เกิน 300-500KB)
 */
function compressImage(file, maxWidth = 800, quality = 0.75) {
    return new Promise(function(resolve, reject) {
        if (!file || !file.type || file.type.indexOf('image/') !== 0) {
            resolve(file);
            return;
        }

        var reader = new FileReader();
        reader.onload = function(e) {
            var img = new Image();
            img.onload = function() {
                var canvas = document.createElement('canvas');
                var scale = Math.min(1, maxWidth / Math.max(img.width, img.height));
                canvas.width = Math.round(img.width * scale);
                canvas.height = Math.round(img.height * scale);

                var ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

                // เลือกฟอร์แมต: JPEG สำหรับภาพทั่วไป, WEBP ถ้ารองรับ
                var mimeType = 'image/jpeg';
                if (file.type === 'image/webp' && canvas.toDataURL('image/webp').length > 0) {
                    mimeType = 'image/webp';
                }

                canvas.toBlob(function(blob) {
                    if (blob) {
                        resolve(blob);
                    } else {
                        // Fallback: ส่งไฟล์เดิมถ้า canvas ไม่รองรับ
                        resolve(file);
                    }
                }, mimeType, quality);
            };
            img.onerror = function() {
                resolve(file);
            };
            img.src = e.target.result;
        };
        reader.onerror = function() {
            resolve(file);
        };
        reader.readAsDataURL(file);
    });
}

// ==========================================================================
// STATE
// ==========================================================================

let currentRole = 'staff';
let currentView = 'pos';
let currentCategory = 'ALL';
let cart = [];
let cartProductMap = {};
let barcodeScannerCtx = 'pos';
let html5QrcodeScanner = null;
let prodImageFile = null;        // Raw File object for AI scan
let prodImageBase64 = null;      // Base64 Data URL for upload
let locImageFile = null;
let locImageBase64 = null;       // Base64 Data URL for location image
let ocrReceiptData = null;
let searchTimerPOS = null;
let searchTimerStock = null;
let searchTimerOwner = null;
let productsCache = [];

// ==========================================================================
// INIT
// ==========================================================================

document.addEventListener('DOMContentLoaded', function() {
    updateCartDateDisplay();
    loadPOSProducts();
    loadCategoryTabs();
    setInterval(updateCartDateDisplay, 60000);
    applyRole(currentRole);
});

function updateCartDateDisplay() {
    const now = new Date();
    const str = now.toLocaleDateString('th-TH', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
    const timeStr = now.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
    const full = str + ' ' + timeStr;
    var els = document.querySelectorAll('.cart-date');
    for (var i = 0; i < els.length; i++) { els[i].textContent = full; }
}

// ==========================================================================
// VIEW SWITCHING
// ==========================================================================

function switchView(view) {
    var ownerViews = ['receive', 'reports'];
    if (ownerViews.indexOf(view) !== -1 && currentRole !== 'owner') {
        showToast('ต้องเข้าสู่โหมด Owner เพื่อดูเมนูนี้', 'error');
        return;
    }

    currentView = view;

    var allViews = document.querySelectorAll('.view');
    for (var i = 0; i < allViews.length; i++) { allViews[i].classList.remove('active'); }

    var targetView = document.getElementById('view-' + view);
    if (targetView) targetView.classList.add('active');

    var navBtns = document.querySelectorAll('.nav-item');
    for (var j = 0; j < navBtns.length; j++) { navBtns[j].classList.remove('active'); }
    var activeNav = document.getElementById('nav-' + view);
    if (activeNav) activeNav.classList.add('active');

    var botBtns = document.querySelectorAll('.bot-btn');
    for (var k = 0; k < botBtns.length; k++) { botBtns[k].classList.remove('active'); }
    var activeBnav = document.getElementById('bn-' + view);
    if (activeBnav) activeBnav.classList.add('active');

    if (view === 'pos') loadPOSProducts();
    else if (view === 'stock') loadStockTable();
    else if (view === 'reports') loadOwnerReports();
}

// ==========================================================================
// ROLE MANAGEMENT
// ==========================================================================

function toggleRole() {
    if (currentRole === 'staff') {
        currentRole = 'owner';
        applyRole('owner');
        showToast('เข้าสู่โหมด Owner 🔑', 'success');
    } else {
        currentRole = 'staff';
        if (['receive', 'reports'].indexOf(currentView) !== -1) {
            switchView('pos');
        }
        applyRole('staff');
        showToast('เข้าสู่โหมด Staff 👷', 'success');
    }
}

function applyRole(role) {
    var isOwner = role === 'owner';

    var roleIcon = document.getElementById('role-icon');
    var roleLabel = document.getElementById('role-label');
    var roleBtn = document.getElementById('role-btn');
    if (roleIcon) roleIcon.className = isOwner ? 'fa-solid fa-crown' : 'fa-solid fa-user-shield';
    if (roleLabel) roleLabel.textContent = isOwner ? 'Owner' : 'Staff';
    if (roleBtn) {
        if (isOwner) roleBtn.classList.add('owner');
        else roleBtn.classList.remove('owner');
    }

    var bnavIcon = document.getElementById('role-icon-bot');
    var bnavLabel = document.getElementById('role-label-bot');
    if (bnavIcon) bnavIcon.className = isOwner ? 'fa-solid fa-crown' : 'fa-solid fa-user-shield';
    if (bnavLabel) bnavLabel.textContent = isOwner ? 'Owner' : 'Staff';

    var ownerEls = document.querySelectorAll('.owner-only');
    for (var i = 0; i < ownerEls.length; i++) {
        if (isOwner) ownerEls[i].classList.remove('hidden');
        else ownerEls[i].classList.add('hidden');
    }
}

// ==========================================================================
// POS: LOAD PRODUCTS
// ==========================================================================

async function loadPOSProducts(keyword, category) {
    if (keyword === undefined) keyword = '';
    if (category === undefined) category = currentCategory;

    var grid = document.getElementById('product-grid');
    if (!grid) return;

    grid.innerHTML = '<div class="grid-msg"><i class="fa-solid fa-spinner fa-spin"></i><p>กำลังโหลดสินค้า...</p></div>';

    try {
        var url = '/api/staff/products?';
        if (keyword) url += 'keyword=' + encodeURIComponent(keyword) + '&';
        if (category && category !== 'ALL') url += 'category=' + encodeURIComponent(category) + '&';

        var res = await fetch(url);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var products = await res.json();

        productsCache = products || [];

        if (!products || products.length === 0) {
            grid.innerHTML = '<div class="grid-msg"><i class="fa-solid fa-box-open" style="color:var(--text-muted)"></i><p>ไม่พบสินค้าที่ตรงกับการค้นหา</p></div>';
            return;
        }

        var html = '';
        for (var i = 0; i < products.length; i++) {
            html += renderProductCard(products[i]);
        }
        grid.innerHTML = html;

    } catch (err) {
        console.error('POS load error:', err);
        // Fallback to mock data
        try {
            var fallbackRes = await fetch('/api/mock/products');
            if (fallbackRes.ok) {
                var mockProducts = await fallbackRes.json();
                productsCache = mockProducts || [];
                if (mockProducts && mockProducts.length > 0) {
                    var filtered = mockProducts;
                    if (keyword) {
                        var kw = keyword.toLowerCase();
                        filtered = mockProducts.filter(function(p) { return p.name.toLowerCase().indexOf(kw) !== -1 || p.sku.toLowerCase().indexOf(kw) !== -1; });
                    }
                    if (category && category !== 'ALL') {
                        filtered = filtered.filter(function(p) { return p.category === category; });
                    }
                    var html = '';
                    for (var i = 0; i < filtered.length; i++) {
                        html += renderProductCard(filtered[i]);
                    }
                    grid.innerHTML = html;
                    return;
                }
            }
        } catch (e) {}
        grid.innerHTML = '<div class="grid-msg"><i class="fa-solid fa-triangle-exclamation" style="color:var(--red)"></i><p style="color:var(--red)">เกิดข้อผิดพลาดในการโหลดสินค้า</p><button class="btn-outline" onclick="loadPOSProducts()" style="margin-top:8px">ลองใหม่</button></div>';
    }
}

function renderProductCard(p) {
    var price = parseFloat(p.sale_price) || 0;
    var stock = parseInt(p.stock_qty) || 0;
    var rawName = p.name || 'ไม่ระบุชื่อ';
    var name = escHtml(rawName);
    var category = escHtml(p.category || '');
    var locImgPath = p.location_image_path || p.location_image_url || '';
    var locationCode = escHtml(p.location_code || '');
    var imgSrc = p.image_path || p.image_url || '';

    var stockBadgeClass = 'in-stock';
    var stockBadgeLabel = stock + ' ชิ้น';
    if (stock === 0) { stockBadgeClass = 'out-stock'; stockBadgeLabel = 'หมด'; }
    else if (stock <= 5) { stockBadgeClass = 'low-stock'; stockBadgeLabel = 'เหลือ ' + stock; }

    var imgHtml = '';
    var placeholderHtml = '<div class="product-card-img-placeholder"><i class="fa-solid fa-image"></i></div>';
    if (imgSrc) {
        imgHtml = '<img src="' + escHtml(imgSrc) + '" alt="' + name + '" class="product-card-img" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">';
        placeholderHtml = '<div class="product-card-img-placeholder" style="display:none"><i class="fa-solid fa-image"></i></div>';
    }

    var locBtn = '';
    if (locImgPath) {
        locBtn = '<button class="btn-view-loc" onclick="openLocationModal(\'' + escHtml(locImgPath) + '\',\'' + name + '\',\'' + locationCode + '\')" title="ดูรูปตำแหน่ง"><i class="fa-solid fa-location-dot"></i></button>';
    }

    var isInCart = cartProductMap[p.id] !== undefined;
    var cartItem = isInCart ? cartProductMap[p.id] : null;
    var footerHtml = '';

    if (isInCart && cartItem) {
        footerHtml = '<div class="inline-qty">' +
            '<button onclick="changeQty(' + p.id + ', -1); event.stopPropagation();">−</button>' +
            '<span>' + cartItem.qty + '</span>' +
            '<button onclick="changeQty(' + p.id + ', 1); event.stopPropagation();">+</button>' +
            '</div>' + locBtn;
    } else {
        var addDisabled = (stock === 0) ? 'disabled' : '';
        footerHtml = '<button class="btn-add-cart" onclick="addToCart(' + p.id + ',\'' + escHtml(rawName) + '\',' + price + ',' + stock + ',\'' + escHtml(imgSrc) + '\'); event.stopPropagation();" ' + addDisabled + '>' +
            '<i class="fa-solid fa-plus"></i> เพิ่ม</button>' + locBtn;
    }

    return '<div class="product-card" data-id="' + p.id + '">' +
        '<div class="product-card-img-wrap">' + imgHtml + placeholderHtml +
        '<span class="stock-badge ' + stockBadgeClass + '">' + stockBadgeLabel + '</span></div>' +
        '<div class="product-card-body">' +
        (category ? '<p class="product-card-category">' + category + '</p>' : '') +
        '<p class="product-card-name" title="' + name + '">' + name + '</p>' +
        '<p class="product-card-price">฿' + fmtMoney(price) + '</p>' +
        '<div class="product-card-footer">' + footerHtml + '</div></div></div>';
}

// ==========================================================================
// POS: SEARCH & CATEGORY FILTER
// ==========================================================================

function debounceSearch(view) {
    if (view === 'pos') {
        clearTimeout(searchTimerPOS);
        searchTimerPOS = setTimeout(function() {
            var kw = document.getElementById('pos-search') ? document.getElementById('pos-search').value.trim() : '';
            loadPOSProducts(kw, currentCategory);
        }, 320);
    } else if (view === 'stock') {
        clearTimeout(searchTimerStock);
        searchTimerStock = setTimeout(function() {
            var kw = document.getElementById('stock-search') ? document.getElementById('stock-search').value.trim() : '';
            loadStockTable(kw);
        }, 320);
    } else if (view === 'owner') {
        clearTimeout(searchTimerOwner);
        searchTimerOwner = setTimeout(function() {
            var kw = document.getElementById('owner-search') ? document.getElementById('owner-search').value.trim() : '';
            loadOwnerReports(kw);
        }, 320);
    }
}

function pickCategory(cat, btn) {
    currentCategory = cat;
    var chips = document.querySelectorAll('.chip');
    for (var i = 0; i < chips.length; i++) { chips[i].classList.remove('active'); }
    if (btn) btn.classList.add('active');
    var kw = document.getElementById('pos-search') ? document.getElementById('pos-search').value.trim() : '';
    loadPOSProducts(kw, cat);
}

function addNewCategory() {
    var catInput = document.getElementById('a-cat');
    if (!catInput) return;
    var newCat = prompt('เพิ่มหมวดหมู่ใหม่:\n(เช่น น้ำมัน, สายพาน, อะไหล่เกษตร)');
    if (!newCat || !newCat.trim()) return;
    newCat = newCat.trim();

    // Add to datalist
    var datalist = document.getElementById('cat-list');
    if (datalist) {
        var exists = false;
        for (var i = 0; i < datalist.options.length; i++) {
            if (datalist.options[i].value === newCat) { exists = true; break; }
        }
        if (!exists) {
            var opt = document.createElement('option');
            opt.value = newCat;
            datalist.appendChild(opt);
        }
    }

    // Set value
    catInput.value = newCat;
    showToast('เพิ่มหมวดหมู่ "' + newCat + '" แล้ว', 'success');
}

// ==========================================================================
// CART MANAGEMENT
// ==========================================================================

function addToCart(id, name, price, maxStock, imgSrc) {
    if (maxStock === undefined) maxStock = Infinity;
    if (imgSrc === undefined) imgSrc = '';

    var existing = null;
    for (var i = 0; i < cart.length; i++) {
        if (cart[i].id === id) { existing = cart[i]; break; }
    }

    if (existing) {
        if (existing.qty + 1 > maxStock) {
            showToast('ไม่สามารถเพิ่มได้เกินสต็อกที่มี (' + maxStock + ' ชิ้น)', 'error');
            return;
        }
        existing.qty += 1;
    } else {
        if (1 > maxStock) {
            showToast('สินค้าชิ้นนี้หมดแล้ว', 'error');
            return;
        }
        var newItem = { id: id, name: name, price: price, qty: 1, maxStock: maxStock, imgSrc: imgSrc };
        cart.push(newItem);
        cartProductMap[id] = newItem;
    }
    renderCart();
    refreshProductGrid();
    showToast('เพิ่ม "' + name.substring(0, 20) + '..." ลงตะกร้า', 'success');
}

function removeFromCart(id) {
    var newCart = [];
    for (var i = 0; i < cart.length; i++) {
        if (cart[i].id !== id) newCart.push(cart[i]);
    }
    cart = newCart;
    delete cartProductMap[id];
    renderCart();
    refreshProductGrid();
}

function changeQty(id, delta) {
    var item = null;
    for (var i = 0; i < cart.length; i++) {
        if (cart[i].id === id) { item = cart[i]; break; }
    }
    if (!item) return;

    var newQty = item.qty + delta;
    if (newQty > item.maxStock) {
        showToast('จำนวนสินค้าเกินสต็อกที่มี (' + item.maxStock + ' ชิ้น)', 'error');
        return;
    }

    if (newQty < 1) {
        removeFromCart(id);
        return;
    }

    item.qty = newQty;
    renderCart();
    refreshProductGrid();
}

function clearCart() {
    if (cart.length === 0) return;
    if (!confirm('ยืนยันล้างรายการในตะกร้าทั้งหมด?')) return;
    cart = [];
    cartProductMap = {};
    renderCart();
    refreshProductGrid();
    showToast('ล้างตะกร้าแล้ว', 'error');
}

function refreshProductGrid() {
    var grid = document.getElementById('product-grid');
    if (!grid) return;
    if (productsCache.length === 0) return;
    var html = '';
    for (var i = 0; i < productsCache.length; i++) {
        html += renderProductCard(productsCache[i]);
    }
    grid.innerHTML = html;
}

function renderCart() {
    var count = 0;
    for (var i = 0; i < cart.length; i++) { count += cart[i].qty; }
    var total = 0;
    for (var j = 0; j < cart.length; j++) { total += cart[j].price * cart[j].qty; }
    var itemCount = cart.length;

    var itemsHtml = '';
    if (cart.length === 0) {
        itemsHtml = '<div class="cart-empty"><i class="fa-regular fa-face-smile-wink"></i><p>ยังไม่มีสินค้าในตะกร้า</p></div>';
    } else {
        for (var k = 0; k < cart.length; k++) {
            var item = cart[k];
            var thumbHtml = '';
            if (item.imgSrc) {
                thumbHtml = '<img src="' + escHtml(item.imgSrc) + '" class="cart-item-thumb" alt="" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">' +
                    '<div class="cart-item-thumb" style="display:none"><i class="fa-solid fa-box"></i></div>';
            } else {
                thumbHtml = '<div class="cart-item-thumb"><i class="fa-solid fa-box"></i></div>';
            }
            itemsHtml += '<div class="cart-item">' +
                thumbHtml +
                '<div class="cart-item-info">' +
                '<p class="cart-item-name" title="' + escHtml(item.name) + '">' + escHtml(item.name) + '</p>' +
                '<p class="cart-item-price">฿' + fmtMoney(item.price) + ' × ' + item.qty + '</p></div>' +
                '<div class="cart-item-controls">' +
                '<button class="qty-btn" onclick="changeQty(' + item.id + ', -1)">−</button>' +
                '<span class="qty-display">' + item.qty + '</span>' +
                '<button class="qty-btn" onclick="changeQty(' + item.id + ', 1)">+</button></div>' +
                '<span class="cart-item-subtotal">฿' + fmtMoney(item.price * item.qty) + '</span>' +
                '<button class="cart-item-del" onclick="removeFromCart(' + item.id + ')" title="ลบออก">' +
                '<i class="fa-solid fa-xmark"></i></button></div>';
        }
    }

    // Desktop Cart
    var desktopItems = document.getElementById('cart-items');
    if (desktopItems) desktopItems.innerHTML = itemsHtml;
    var desktopSubtotal = document.getElementById('cart-subtotal');
    if (desktopSubtotal) desktopSubtotal.textContent = '฿' + fmtMoney(total);
    var desktopDiscount = document.getElementById('cart-discount');
    if (desktopDiscount) desktopDiscount.textContent = '฿0.00';
    var desktopTotal = document.getElementById('cart-total');
    if (desktopTotal) desktopTotal.textContent = '฿' + fmtMoney(total);

    // Mobile Cart Drawer
    var mobileItems = document.getElementById('cart-items-m');
    if (mobileItems) mobileItems.innerHTML = itemsHtml;
    var mobileSubtotal = document.getElementById('cart-subtotal-m');
    if (mobileSubtotal) mobileSubtotal.textContent = '฿' + fmtMoney(total);
    var mobileDiscount = document.getElementById('cart-discount-m');
    if (mobileDiscount) mobileDiscount.textContent = '฿0.00';
    var mobileTotal = document.getElementById('cart-total-m');
    if (mobileTotal) mobileTotal.textContent = '฿' + fmtMoney(total);

    // Mobile FAB
    var fabCount = document.getElementById('fab-count');
    if (fabCount) fabCount.textContent = count;
    var fabTotal = document.getElementById('fab-total');
    if (fabTotal) fabTotal.textContent = '฿' + fmtMoney(total);

    // Enable/disable pay buttons
    var payBtns = document.querySelectorAll('.btn-pay');
    var hasItems = cart.length > 0;
    for (var p = 0; p < payBtns.length; p++) {
        payBtns[p].disabled = !hasItems;
    }
}

// ==========================================================================
// MOBILE CART DRAWER
// ==========================================================================

function toggleCart() {
    var drawer = document.getElementById('cart-drawer');
    var overlay = document.getElementById('drawer-overlay');
    if (!drawer) return;

    var isOpen = drawer.classList.contains('open');
    if (isOpen) {
        drawer.classList.remove('open');
        if (overlay) overlay.classList.add('hidden');
    } else {
        drawer.classList.add('open');
        if (overlay) overlay.classList.remove('hidden');
    }
}

// ==========================================================================
// CHECKOUT MODAL
// ==========================================================================

function openCheckout() {
    if (cart.length === 0) {
        showToast('ตะกร้าสินค้าว่างเปล่า กรุณาเลือกสินค้าก่อน', 'error');
        return;
    }

    var total = 0;
    for (var i = 0; i < cart.length; i++) { total += cart[i].price * cart[i].qty; }

    var totalEl = document.getElementById('ck-total');
    if (totalEl) totalEl.textContent = '฿' + fmtMoney(total);

    var countEl = document.getElementById('ck-count');
    if (countEl) countEl.textContent = cart.length + ' รายการ (' + cart.reduce(function(s, i) { return s + i.qty; }, 0) + ' ชิ้น)';

    var qrAmt = document.getElementById('qr-amount');
    if (qrAmt) qrAmt.textContent = '฿' + fmtMoney(total);

    var preview = document.getElementById('ck-preview');
    if (preview) {
        var html = '';
        for (var j = 0; j < cart.length; j++) {
            html += '<div class="row"><span>' + escHtml(cart[j].name) + ' × ' + cart[j].qty + '</span><span>฿' + fmtMoney(cart[j].price * cart[j].qty) + '</span></div>';
        }
        preview.innerHTML = html;
    }

    var cashInput = document.getElementById('cash-in');
    if (cashInput) cashInput.value = '';

    var changeEl = document.getElementById('change-val');
    if (changeEl) {
        changeEl.textContent = '฿0.00';
        changeEl.style.color = 'var(--green)';
    }

    switchPay('cash');

    var drawer = document.getElementById('cart-drawer');
    if (drawer && drawer.classList.contains('open')) toggleCart();

    openModal('modal-checkout');
}

function switchPay(tab) {
    var cashSection = document.getElementById('pay-cash');
    var qrSection = document.getElementById('pay-qr');
    var tabCash = document.getElementById('ptab-cash');
    var tabQR = document.getElementById('ptab-qr');

    if (tab === 'cash') {
        if (cashSection) cashSection.classList.remove('hidden');
        if (qrSection) qrSection.classList.add('hidden');
        if (tabCash) tabCash.classList.add('active');
        if (tabQR) tabQR.classList.remove('active');
    } else {
        if (cashSection) cashSection.classList.add('hidden');
        if (qrSection) qrSection.classList.remove('hidden');
        if (tabCash) tabCash.classList.remove('active');
        if (tabQR) tabQR.classList.add('active');
    }
}

function calcChange() {
    var total = 0;
    for (var i = 0; i < cart.length; i++) { total += cart[i].price * cart[i].qty; }
    var received = parseFloat(document.getElementById('cash-in') ? document.getElementById('cash-in').value : 0) || 0;
    var change = received - total;
    var el = document.getElementById('change-val');
    if (!el) return;

    el.textContent = '฿' + fmtMoney(Math.max(0, change));
    el.style.color = change < 0 ? 'var(--red)' : 'var(--green)';
}

function setExact() {
    var total = 0;
    for (var i = 0; i < cart.length; i++) { total += cart[i].price * cart[i].qty; }
    var cashInput = document.getElementById('cash-in');
    if (cashInput) {
        cashInput.value = total.toFixed(2);
        calcChange();
    }
}

function addPre(amount) {
    var cashInput = document.getElementById('cash-in');
    if (cashInput) {
        var current = parseFloat(cashInput.value) || 0;
        cashInput.value = (current + amount).toFixed(2);
        calcChange();
    }
}

async function submitCheckout() {
    var btn = document.getElementById('ck-submit');
    if (!btn || cart.length === 0) return;

    var total = 0;
    for (var i = 0; i < cart.length; i++) { total += cart[i].price * cart[i].qty; }
    var cashInput = parseFloat(document.getElementById('cash-in') ? document.getElementById('cash-in').value : 0) || 0;
    var isCashTab = document.getElementById('ptab-cash') ? document.getElementById('ptab-cash').classList.contains('active') : true;
    var payTab = isCashTab ? 'cash' : 'qr';

    if (payTab === 'cash' && cashInput < total) {
        showToast('จำนวนเงินที่รับมาน้อยกว่ายอดรวม', 'error');
        var ci = document.getElementById('cash-in');
        if (ci) ci.focus();
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span> กำลังบันทึก...';

    try {
        var items = [];
        for (var j = 0; j < cart.length; j++) {
            items.push({ product_id: cart[j].id, qty: cart[j].qty });
        }

        var payload = {
            items: items,
            payment_type: payTab,
            total_amount: total,
            received_amount: payTab === 'cash' ? cashInput : total,
            change_amount: payTab === 'cash' ? Math.max(0, cashInput - total) : 0
        };

        var res = await fetch('/api/staff/checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'HTTP ' + res.status);
        }

        cart = [];
        cartProductMap = {};
        renderCart();
        closeModal('modal-checkout');
        showToast('✅ บันทึกการขายเรียบร้อย! ตัดสต็อกสำเร็จ', 'success');

        var kw = document.getElementById('pos-search') ? document.getElementById('pos-search').value.trim() : '';
        loadPOSProducts(kw, currentCategory);

    } catch (err) {
        console.error('Checkout error:', err);
        showToast('เกิดข้อผิดพลาด: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-circle-check"></i> บันทึกการขาย';
    }
}

// ==========================================================================
// STOCK VIEW: LOAD TABLE
// ==========================================================================

async function loadStockTable(keyword) {
    if (keyword === undefined) keyword = '';

    var tbody = document.getElementById('stock-body');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="7" class="empty"><i class="fa-solid fa-spinner fa-spin"></i> กำลังโหลด...</td></tr>';

    try {
        var url = '/api/staff/products?';
        if (keyword) url += 'keyword=' + encodeURIComponent(keyword) + '&';

        var res = await fetch(url);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var products = await res.json();

        if (!products || products.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty">ไม่พบรายการสินค้า</td></tr>';
            return;
        }

        var html = '';
        for (var i = 0; i < products.length; i++) {
            var p = products[i];
            var stock = parseInt(p.stock_qty) || 0;
            var price = parseFloat(p.sale_price) || 0;
            var rawName = p.name || '-';
            var name = escHtml(rawName);
            var sku = escHtml(p.sku || '-');
            var cat = escHtml(p.category || '-');
            var loc = escHtml(p.location_code || '-');
            var imgSrc = p.image_path || p.image_url || '';
            var locImgSrc = p.location_image_path || p.location_image_url || '';

            var stockTagClass = 'ok';
            if (stock === 0) stockTagClass = 'out';
            else if (stock <= 5) stockTagClass = 'low';

            var stockTag = '<span class="stock-tag ' + stockTagClass + '">' + (stock === 0 ? 'หมด' : stock + ' ชิ้น') + '</span>';

            var thumbHtml = '';
            if (imgSrc) {
                thumbHtml = '<img src="' + escHtml(imgSrc) + '" class="product-thumb" alt="' + name + '" onerror="this.style.display=\'none\'">';
            } else {
                thumbHtml = '<div class="product-thumb-icon"><i class="fa-solid fa-image"></i></div>';
            }

            var locPhotoBtn = '';
            if (locImgSrc) {
                locPhotoBtn = '<button class="btn-icon" onclick="openLocationModal(\'' + escHtml(locImgSrc) + '\',\'' + name + '\',\'' + loc + '\')" title="ดูรูปตำแหน่ง"><i class="fa-solid fa-image"></i></button>';
            } else {
                locPhotoBtn = '<span style="color:var(--text-muted);font-size:11px">-</span>';
            }

            html += '<tr><td><div class="product-cell">' + thumbHtml +
                '<div><div class="product-name-cell">' + name + '</div><div class="product-sku-cell">' + sku + '</div></div></div></td>' +
                '<td>' + cat + '</td>' +
                '<td><code style="font-size:11px;color:var(--blue)">' + loc + '</code></td>' +
                '<td class="c">' + stockTag + '</td>' +
                '<td class="r" style="font-family:\'Inter\',sans-serif;font-weight:700;color:var(--blue)">฿' + fmtMoney(price) + '</td>' +
                '<td class="c">' + locPhotoBtn + '</td>' +
                '<td><div class="action-btns">' +
                '<button class="btn-icon" onclick="addToCart(' + p.id + ',\'' + escHtml(rawName) + '\',' + price + ',' + stock + ',\'' + escHtml(imgSrc) + '\')" title="เพิ่มลงตะกร้า"' + (stock === 0 ? ' disabled' : '') + '>' +
                '<i class="fa-solid fa-cart-plus"></i></button>' +
                '<button class="btn-icon" onclick="openEditProduct(' + p.id + ')" title="แก้ไขสินค้า"><i class="fa-solid fa-pen-to-square"></i></button>' +
                '<button class="btn-icon" style="color:var(--red)" onclick="deleteProductDirect(' + p.id + ',\'' + escHtml(rawName) + '\')" title="ลบสินค้า"><i class="fa-solid fa-trash-can"></i></button>' +
                '</div></td></tr>';
        }
        tbody.innerHTML = html;

    } catch (err) {
        console.error('Stock load error:', err);
        tbody.innerHTML = '<tr><td colspan="7" class="empty" style="color:var(--red)">เกิดข้อผิดพลาด: ' + err.message + '</td></tr>';
    }
}

// ==========================================================================
// OWNER: REPORTS & FINANCE
// ==========================================================================

async function loadOwnerReports(keyword) {
    if (keyword === undefined) keyword = '';

    var tbody = document.getElementById('owner-body');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="7" class="empty"><i class="fa-solid fa-spinner fa-spin"></i> กำลังคำนวณ...</td></tr>';

    try {
        var statsRes = await fetch('/api/owner/dashboard');
        if (statsRes.ok) {
            var stats = await statsRes.json();
            var costEl = document.getElementById('stat-cost');
            if (costEl) costEl.textContent = '฿' + fmtMoney(stats.total_cost_value);

            var saleEl = document.getElementById('stat-sale');
            if (saleEl) saleEl.textContent = '฿' + fmtMoney(stats.total_sale_value);

            var profitEl = document.getElementById('stat-profit');
            if (profitEl) profitEl.textContent = '฿' + fmtMoney(stats.potential_profit);

            var lowEl = document.getElementById('stat-low');
            if (lowEl) lowEl.textContent = stats.low_stock_count || '0';
        }

        var url = '/api/owner/products?';
        if (keyword) url += 'keyword=' + encodeURIComponent(keyword) + '&';

        var prodRes = await fetch(url);
        if (!prodRes.ok) throw new Error('HTTP ' + prodRes.status);
        var products = await prodRes.json();

        if (!products || products.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty">ไม่พบรายการสินค้า</td></tr>';
            return;
        }

        var html = '';
        for (var i = 0; i < products.length; i++) {
            var p = products[i];
            var stock = parseInt(p.stock_qty) || 0;
            var cost = parseFloat(p.latest_cost) || 0;
            var sale = parseFloat(p.sale_price) || 0;
            var profit = parseFloat(p.profit) || 0;
            var margin = parseFloat(p.margin_pct) || 0;
            var totalCost = parseFloat(p.total_cost_val) || 0;
            var name = escHtml(p.name || '-');
            var sku = escHtml(p.sku || '-');
            var cat = escHtml(p.category || '-');

            var imgSrc = p.image_path || p.image_url || '';
            var thumbHtml = '';
            if (imgSrc) {
                thumbHtml = '<img src="' + escHtml(imgSrc) + '" class="product-thumb" alt="' + name + '" onerror="this.style.display=\'none\'">';
            } else {
                thumbHtml = '<div class="product-thumb-icon"><i class="fa-solid fa-image"></i></div>';
            }

            var stockTagClass = 'ok';
            if (stock === 0) stockTagClass = 'out';
            else if (stock <= 5) stockTagClass = 'low';

            var profitColor = profit >= 0 ? '#10b981' : 'var(--red)';
            var marginColor = margin >= 20 ? '#10b981' : (margin >= 10 ? '#F59E0B' : 'var(--red)');

            html += '<tr><td><div class="product-cell">' + thumbHtml +
                '<div><div class="product-name-cell">' + name + '</div><div class="product-sku-cell">' + sku + ' • ' + cat + '</div></div></div></td>' +
                '<td class="c"><span class="stock-tag ' + stockTagClass + '">' + stock + ' ชิ้น</span></td>' +
                '<td class="r" style="font-family:\'Inter\',sans-serif;color:var(--text-secondary)">฿' + fmtMoney(cost) + '</td>' +
                '<td class="r" style="font-family:\'Inter\',sans-serif;color:var(--blue)">฿' + fmtMoney(sale) + '</td>' +
                '<td class="r" style="font-family:\'Inter\',sans-serif;font-weight:700;color:' + profitColor + '">฿' + fmtMoney(profit) + '</td>' +
                '<td class="r"><span style="font-family:\'Inter\',sans-serif;font-weight:700;color:' + marginColor + '">' + margin.toFixed(1) + '%</span></td>' +
                '<td class="r" style="font-family:\'Inter\',sans-serif;font-weight:700;color:var(--text)">฿' + fmtMoney(totalCost) + '</td></tr>';
        }
        tbody.innerHTML = html;

    } catch (err) {
        console.error('Owner reports error:', err);
        tbody.innerHTML = '<tr><td colspan="7" class="empty" style="color:var(--red)">เกิดข้อผิดพลาด: ' + err.message + '</td></tr>';
    }
}

// ==========================================================================
// ADD PRODUCT MODAL
// ==========================================================================

function openAddProduct() {
    prodImageFile = null;
    prodImageBase64 = null;
    locImageFile = null;
    locImageBase64 = null;

    var prodPh = document.getElementById('ph-prod');
    var prodImg = document.getElementById('img-prod');
    if (prodPh) prodPh.classList.remove('hidden');
    if (prodImg) prodImg.classList.add('hidden');
    var locPh = document.getElementById('ph-loc');
    var locImg = document.getElementById('img-loc');
    if (locPh) locPh.classList.remove('hidden');
    if (locImg) locImg.classList.add('hidden');

    var aiBar = document.getElementById('ai-bar');
    if (aiBar) aiBar.classList.add('hidden');

    var fiProd = document.getElementById('fi-prod');
    if (fiProd) fiProd.value = '';
    var fiLoc = document.getElementById('fi-loc');
    if (fiLoc) fiLoc.value = '';

    var form = document.getElementById('form-add');
    if (form) form.reset();

    openModal('modal-add');
}

function onProdImg(event) {
    var file = event.target.files[0];
    if (!file) return;

    // บีบอัดรูปก่อนอัปโหลด (กัน Error 1024KB Limit)
    compressImage(file).then(function(compressed) {
        prodImageFile = compressed;
        prodImageBase64 = null; // Reset, will be set after FileReader loads

        var reader = new FileReader();
        reader.onload = function(e) {
            var dataUrl = e.target.result;
            prodImageBase64 = dataUrl; // Store Base64 Data URL for upload
            var preview = document.getElementById('img-prod');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('hidden');
            }
            var ph = document.getElementById('ph-prod');
            if (ph) ph.classList.add('hidden');
        };
        reader.readAsDataURL(compressed);

        aiScanProductImage(compressed);
    });
}

async function aiScanProductImage(file) {
    var analyzeEl = document.getElementById('ai-bar');
    if (analyzeEl) analyzeEl.classList.remove('hidden');

    try {
        var formData = new FormData();
        formData.append('file', file);

        var res = await fetch('/api/staff/scan-product', { method: 'POST', body: formData });
        if (!res.ok) return;

        var data = await res.json();
        if (data.name) {
            var nameField = document.getElementById('a-name');
            if (nameField && !nameField.value) nameField.value = data.name;
        }
        if (data.category) {
            var catField = document.getElementById('a-cat');
            if (catField && !catField.value) catField.value = data.category;
        }
        if (data.description && data.description.trim()) {
            var descField = document.getElementById('a-desc');
            if (descField && !descField.value) descField.value = data.description;
        }
        if (data.suggested_location) {
            var locField = document.getElementById('a-loc');
            if (locField && !locField.value) locField.value = data.suggested_location;
        }
        showToast('AI วิเคราะห์รูปสินค้าสำเร็จ ✨', 'success');
    } catch (err) {
        console.warn('AI scan failed:', err);
    } finally {
        if (analyzeEl) analyzeEl.classList.add('hidden');
    }
}

function onLocImg(event) {
    var file = event.target.files[0];
    if (!file) return;

    // บีบอัดรูปก่อนอัปโหลด (กัน Error 1024KB Limit)
    compressImage(file).then(function(compressed) {
        locImageFile = compressed;
        locImageBase64 = null;

        var reader = new FileReader();
        reader.onload = function(e) {
            var dataUrl = e.target.result;
            locImageBase64 = dataUrl; // Store Base64 Data URL for upload
            var preview = document.getElementById('img-loc');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('hidden');
            }
            var ph = document.getElementById('ph-loc');
            if (ph) ph.classList.add('hidden');
        };
        reader.readAsDataURL(compressed);
    });
}

async function submitAdd(event) {
    event.preventDefault();

    var name = document.getElementById('a-name') ? document.getElementById('a-name').value.trim() : '';
    var category = document.getElementById('a-cat') ? document.getElementById('a-cat').value.trim() : '';
    var sku = document.getElementById('a-sku') ? document.getElementById('a-sku').value.trim() : '';
    var stock = document.getElementById('a-qty') ? document.getElementById('a-qty').value : '0';
    var minStock = document.getElementById('a-min') ? document.getElementById('a-min').value : '5';
    var location = document.getElementById('a-loc') ? document.getElementById('a-loc').value.trim() : '';
    var locationText = document.getElementById('a-location') ? document.getElementById('a-location').value.trim() : '';
    var price = document.getElementById('a-price') ? document.getElementById('a-price').value : '0';
    var cost = document.getElementById('a-cost') ? document.getElementById('a-cost').value : '0';
    var desc = document.getElementById('a-desc') ? document.getElementById('a-desc').value.trim() : '';

    if (!name) {
        showToast('กรุณากรอกชื่อสินค้า', 'error');
        return;
    }

    var submitBtn = document.getElementById('btn-add-submit');
    if (!submitBtn) return;

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-sm"></span> กำลังบันทึก...';

    try {
        // Upload Base64 images first to get permanent URLs, then include in form data
        var imageUrl = '';
        var locationImageUrl = '';

        if (prodImageBase64) {
            try {
                var imgRes = await fetch('/api/upload/image-base64', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data_url: prodImageBase64, prefix: 'prod', folder: 'products' })
                });
                if (imgRes.ok) {
                    var imgData = await imgRes.json();
                    imageUrl = imgData.url || '';
                }
            } catch (e) { console.warn('Image upload failed:', e); }
        }

        if (locImageBase64) {
            try {
                var locRes = await fetch('/api/upload/image-base64', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data_url: locImageBase64, prefix: 'loc', folder: 'locations' })
                });
                if (locRes.ok) {
                    var locData = await locRes.json();
                    locationImageUrl = locData.url || '';
                }
            } catch (e) { console.warn('Location image upload failed:', e); }
        }

        var frontStock = document.getElementById('a-front-stock') ? document.getElementById('a-front-stock').value : stock;
        var warehouseStock = document.getElementById('a-warehouse-stock') ? document.getElementById('a-warehouse-stock').value : '0';

        var formData = new FormData();
        formData.append('name', name);
        formData.append('category', category);
        formData.append('sku', sku);
        formData.append('stock_qty', stock);
        formData.append('front_stock', frontStock);
        formData.append('warehouse_stock', warehouseStock);
        formData.append('min_stock', minStock);
        formData.append('location_code', location);
        formData.append('location', locationText);
        formData.append('sale_price', price);
        formData.append('description', desc);
        if (cost) formData.append('cost_price', cost);
        if (imageUrl) formData.append('image_path', imageUrl);
        if (locationImageUrl) formData.append('location_image_path', locationImageUrl);
        if (prodImageFile) formData.append('file', prodImageFile);
        if (locImageFile) formData.append('location_file', locImageFile);

        var res = await fetch('/api/staff/products/add', { method: 'POST', body: formData });
        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'บันทึกสินค้าไม่สำเร็จ');
        }

        // If new category was created, add it to datalist for future use
        if (category) {
            var datalist = document.getElementById('cat-list');
            if (datalist) {
                var exists = false;
                for (var i = 0; i < datalist.options.length; i++) {
                    if (datalist.options[i].value === category) { exists = true; break; }
                }
                if (!exists) {
                    var opt = document.createElement('option');
                    opt.value = category;
                    datalist.appendChild(opt);
                }
            }
        }

        // Reset form
        document.getElementById('form-add').reset();
        prodImageFile = null; prodImageBase64 = null;
        locImageFile = null; locImageBase64 = null;
        var previewProd = document.getElementById('img-prod');
        var phProd = document.getElementById('ph-prod');
        if (previewProd) { previewProd.src = ''; previewProd.classList.add('hidden'); }
        if (phProd) phProd.classList.remove('hidden');
        var previewLoc = document.getElementById('img-loc');
        var phLoc = document.getElementById('ph-loc');
        if (previewLoc) { previewLoc.src = ''; previewLoc.classList.add('hidden'); }
        if (phLoc) phLoc.classList.remove('hidden');

        closeModal('modal-add');
        showToast('✅ เพิ่มสินค้า "' + name + '" เรียบร้อยแล้ว', 'success');

        // === Refresh BOTH views (Stock + POS) ALWAYS ===
        // 1. Refresh Stock Table
        loadStockTable();
        // 2. Refresh POS Product Grid
        var kw = document.getElementById('pos-search') ? document.getElementById('pos-search').value.trim() : '';
        loadPOSProducts(kw, currentCategory);
        // Add the saved category to the POS filter tabs immediately.
        loadCategoryTabs();

    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> บันทึก';
    }
}

// ==========================================================================
// LOCATION PHOTO VIEWER
// ==========================================================================

function openLocationModal(imgUrl, productName, locationCode) {
    var titleEl = document.getElementById('loc-title');
    var imgEl = document.getElementById('loc-img');
    var codeEl = document.getElementById('loc-code');

    if (titleEl) titleEl.innerHTML = '<i class="fa-solid fa-image"></i> ตำแหน่ง: ' + escHtml(productName);
    if (imgEl) imgEl.src = imgUrl;
    if (codeEl) codeEl.textContent = locationCode ? '📍 ' + locationCode : '';

    openModal('modal-loc');
}

// ==========================================================================
// BARCODE SCANNER
// ==========================================================================

function openScanner(ctx) {
    barcodeScannerCtx = ctx;
    openModal('modal-scan');

    if (typeof Html5Qrcode === 'undefined') {
        showToast('ไม่พบไลบรารี สแกนบาร์โค้ด', 'error');
        closeModal('modal-scan');
        return;
    }

    html5QrcodeScanner = new Html5Qrcode('reader');
    // ร้องขอสิทธิ์กล้อง + ใช้กล้องหลัง (environment) ตามข้อกำหนด
    html5QrcodeScanner.start(
        // facingMode: 'environment' = กล้องหลัง
        // fallback constraints เผื่ออุปกรณ์ไม่รองรับ facingMode
        { facingMode: 'environment' },
        { fps: 12, qrbox: { width: 260, height: 160 }, aspectRatio: 1.0 },
        function(decoded) {
            closeScanner();
            handleBarcodeResult(decoded, ctx);
        },
        function() {}
    ).catch(function(err) {
        console.error('Camera error:', err);
        // Fallback 1: ลองขอสิทธิ์กล้องกึ่งกลาง (ไม่ระบุ facingMode) เผื่อกล้องหลังถูกบล็อก
        try {
            if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
                navigator.mediaDevices.getUserMedia({
                    video: { facingMode: 'environment' },
                    audio: false
                }).then(function(stream) {
                    stream.getTracks().forEach(function(t) { t.stop(); });
                    // ได้สิทธิ์กล้องแล้ว ลองสแกนใหม่
                    showToast('ขอสิทธิ์กล้องสำเร็จ ลองสแกนใหม่', 'success');
                    setTimeout(function() { openScanner(ctx); }, 500);
                }).catch(function(permErr) {
                    console.error('Permission denied:', permErr);
                    closeScanner();
                    enableScannerGunMode();
                    showToast('ไม่สามารถเปิดกล้องได้: ' + (permErr.name || '') + ' — เปิดโหมดพิมพ์บาร์โค้ดจากเครื่องสแกน USB/Bluetooth แทน', 'error');
                });
            } else {
                closeScanner();
                enableScannerGunMode();
                showToast('อุปกรณ์นี้ไม่รองรับกล้องสแกน — เปิดโหมดพิมพ์บาร์โค้ดจากเครื่องสแกนแทน', 'error');
            }
        } catch (e) {
            closeScanner();
            enableScannerGunMode();
            showToast('ไม่สามารถเปิดกล้องได้ — เปิดโหมดพิมพ์บาร์โค้ดแทน', 'error');
        }
    });
}

// ==========================================================================
// SCANNER GUN MODE (USB / Bluetooth HID) — ฟัง keypress พิมพ์รับค่าบาร์โค้ด
// ==========================================================================
let scannerGunActive = false;
let scannerGunBuffer = '';
let scannerGunTimer = null;

function enableScannerGunMode() {
    scannerGunActive = true;
    scannerGunBuffer = '';
    showToast('📷 โหมดสแกนเนอร์: พิมพ์/กรอกบาร์โค้ดแล้วกด Enter หรือรอ 100ms', 'success');
}

function disableScannerGunMode() {
    scannerGunActive = false;
    scannerGunBuffer = '';
}

// ฟัง keypress ทั่วหน้าเว็บ — เครื่องสแกน USB/Bluetooth จะพิมพ์ทีละตัวแล้ว Enter
document.addEventListener('keydown', function(e) {
    if (!scannerGunActive) return;
    // ตัวพิมพ์/ตัวเลขจากเครื่องสแกน
    if (e.key && e.key.length === 1) {
        scannerGunBuffer += e.key;
        // ป้องกันตัวเลขไปกวน input ที่ focus อยู่
        var focused = document.activeElement;
        if (focused && focused.tagName === 'INPUT') {
            // เครื่องสแกนพิมพ์เร็วมาก (ทุกตัว < 100ms) - ถือว่าเป็นสแกน
            clearTimeout(scannerGunTimer);
            scannerGunTimer = setTimeout(function() {
                if (scannerGunBuffer.length >= 3) {
                    var barcode = scannerGunBuffer;
                    scannerGunBuffer = '';
                    handleBarcodeResult(barcode, barcodeScannerCtx);
                }
            }, 100);
        }
    } else if (e.key === 'Enter') {
        // เครื่องสแกนหลายรุ่นปิดท้ายด้วย Enter
        if (scannerGunBuffer.length >= 3) {
            var barcode = scannerGunBuffer;
            scannerGunBuffer = '';
            e.preventDefault();
            e.stopPropagation();
            handleBarcodeResult(barcode, barcodeScannerCtx);
        }
    }
});

function closeScanner() {
    if (html5QrcodeScanner) {
        html5QrcodeScanner.stop()
            .then(function() { html5QrcodeScanner.clear(); html5QrcodeScanner = null; })
            .catch(function() { html5QrcodeScanner = null; });
    }
    closeModal('modal-scan');
}

function handleBarcodeResult(value, ctx) {
    if (ctx === 'pos') {
        // POS: สแกนบาร์โค้ดแล้วดึงสินค้านั้นเข้าตะกร้า (Cart) ทันที
        var input = document.getElementById('pos-search');
        if (input) { input.value = value; }
        addProductByBarcode(value);
    } else if (ctx === 'stock') {
        var input = document.getElementById('stock-search');
        if (input) { input.value = value; debounceSearch('stock'); }
        showToast('สแกน: ' + value, 'success');
    } else if (ctx === 'sku') {
        var skuInput = document.getElementById('a-sku');
        if (skuInput) skuInput.value = value;
        showToast('สแกน SKU: ' + value, 'success');
    }
}

// ค้นหาสินค้าจากบาร์โค้ด/SKU แล้วเพิ่มเข้าตะกร้าทันที (ใช้ในหน้า POS)
async function addProductByBarcode(barcode) {
    if (!barcode) return;
    try {
        var res = await fetch('/api/staff/products?keyword=' + encodeURIComponent(barcode));
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var products = await res.json();
        if (!products || products.length === 0) {
            showToast('ไม่พบสินค้าที่มีบาร์โค้ด: ' + barcode, 'error');
            return;
        }
        // เลือกสินค้าตัวแรกที่ตรงกับ SKU/Barcode เป๊ะๆ ก่อน ถ้าไม่มีค่อยใช้ตัวแรก
        var match = null;
        for (var i = 0; i < products.length; i++) {
            if (products[i].sku && String(products[i].sku).trim() === String(barcode).trim()) {
                match = products[i];
                break;
            }
        }
        if (!match) match = products[0];
        var stock = parseInt(match.stock_qty) || 0;
        if (stock <= 0) {
            showToast('สินค้า "' + (match.name || '') + '" หมดสต็อก', 'error');
            return;
        }
        addToCart(match.id, match.name, parseFloat(match.sale_price) || 0, stock, match.image_path || match.image_url || '');
        showToast('✅ สแกนแล้ว: ' + (match.name || '') + ' เข้าตะกร้า', 'success');
    } catch (err) {
        console.error('Barcode add to cart error:', err);
        showToast('ไม่พบสินค้าบาร์โค้ด: ' + barcode, 'error');
    }
}

// ==========================================================================
// OWNER: BILL OCR UPLOAD
// ==========================================================================

async function handleReceiptUpload(event) {
    var file = event.target.files[0];
    if (!file) return;

    var spinner = document.getElementById('ocr-loading');
    var result = document.getElementById('ocr-result');
    var dropZone = document.getElementById('dropzone');

    if (spinner) spinner.classList.remove('hidden');
    if (dropZone) dropZone.classList.add('hidden');
    if (result) result.classList.add('hidden');

    try {
        var formData = new FormData();
        formData.append('file', file);

        var res = await fetch('/api/owner/upload-receipt', { method: 'POST', body: formData });
        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'อ่านบิลไม่สำเร็จ');
        }

        var data = await res.json();
        ocrReceiptData = data;

        var tbody = document.getElementById('ocr-body');
        if (tbody) {
            if (data.created_products && data.created_products.length > 0) {
                var html = '';
                for (var i = 0; i < data.created_products.length; i++) {
                    var item = data.created_products[i];
                    html += '<tr><td>' + escHtml(item.ocr_name || item.name || '-') + '</td>' +
                        '<td class="r">' + (item.qty || 1) + '</td>' +
                        '<td class="r">฿' + fmtMoney(item.cost_price || 0) + '</td>' +
                        '<td class="r">฿' + fmtMoney((item.cost_price || 0) * (item.qty || 1)) + '</td></tr>';
                }
                tbody.innerHTML = html;
            } else {
                tbody.innerHTML = '<tr><td colspan="4" class="empty">ไม่พบรายการสินค้าในบิล</td></tr>';
            }
        }

        if (result) result.classList.remove('hidden');
        showToast('✅ AI อ่านบิลสำเร็จ พบ ' + (data.created_products ? data.created_products.length : 0) + ' รายการ', 'success');

    } catch (err) {
        console.error('OCR error:', err);
        showToast('เกิดข้อผิดพลาด: ' + err.message, 'error');
        if (dropZone) dropZone.classList.remove('hidden');
    } finally {
        if (spinner) spinner.classList.add('hidden');
        var fileInput = document.getElementById('receipt-input');
        if (fileInput) fileInput.value = '';
    }
}

function confirmOcr() {
    if (!ocrReceiptData) return;
    showToast('✅ บันทึกข้อมูลจากบิลเข้าคลังเรียบร้อยแล้ว', 'success');
    resetOcr();
    loadOwnerReports();
}

function resetOcr() {
    ocrReceiptData = null;
    var result = document.getElementById('ocr-result');
    if (result) result.classList.add('hidden');
    var dropZone = document.getElementById('dropzone');
    if (dropZone) dropZone.classList.remove('hidden');
    var fileInput = document.getElementById('receipt-input');
    if (fileInput) fileInput.value = '';
}

// ==========================================================================
// MODAL HELPERS
// ==========================================================================

function openModal(id) {
    var el = document.getElementById(id);
    if (el) el.classList.remove('hidden');
}

function closeModal(id) {
    var el = document.getElementById(id);
    if (el) el.classList.add('hidden');

    if (id === 'modal-scan' && html5QrcodeScanner) {
        html5QrcodeScanner.stop()
            .then(function() { html5QrcodeScanner.clear(); html5QrcodeScanner = null; })
            .catch(function() { html5QrcodeScanner = null; });
    }
}

document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        var id = e.target.id;
        if (id === 'modal-scan') {
            closeScanner();
        } else if (id) {
            closeModal(id);
        }
    }
});

// ==========================================================================
// TOAST NOTIFICATION
// ==========================================================================

var toastTimer = null;

function showToast(msg, type) {
    if (type === undefined) type = 'success';
    var toast = document.getElementById('toast');
    var toastMsg = document.getElementById('toast-msg');
    var toastIcon = document.getElementById('toast-icon');

    if (!toast) return;

    toast.className = 'toast ' + type;
    if (toastMsg) toastMsg.textContent = msg;
    if (toastIcon) {
        toastIcon.className = type === 'success'
            ? 'fa-solid fa-circle-check'
            : 'fa-solid fa-circle-exclamation';
    }

    toast.classList.remove('hidden');

    clearTimeout(toastTimer);
    toastTimer = setTimeout(function() {
        toast.classList.add('hidden');
    }, 3000);
}

// ==========================================================================
// FORMATTING UTILITIES
// ==========================================================================

function fmtMoney(val) {
    var n = parseFloat(val);
    if (isNaN(n)) return '0.00';
    return n.toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '\x26amp;')
        .replace(/</g, '\x26lt;')
        .replace(/>/g, '\x26gt;')
        .replace(/"/g, '\x26quot;')
        .replace(/'/g, '\x26#039;');
}

// ==========================================================================
// AUTO-SAVE CART TO LOCALSTORAGE
// ==========================================================================

function saveCartToStorage() {
    try {
        var data = { cart: cart, cartProductMap: cartProductMap };
        localStorage.setItem('kjy_cart', JSON.stringify(data));
    } catch (e) {}
}

function loadCartFromStorage() {
    try {
        var saved = localStorage.getItem('kjy_cart');
        if (saved) {
            var data = JSON.parse(saved);
            if (data.cart && data.cart.length > 0) {
                cart = data.cart;
                cartProductMap = data.cartProductMap || {};
                return true;
            }
        }
    } catch (e) {}
    return false;
}

// Override cart functions to auto-save
var _origAddToCart = addToCart;
addToCart = function(id, name, price, maxStock, imgSrc) {
    _origAddToCart(id, name, price, maxStock, imgSrc);
    saveCartToStorage();
};

var _origRemoveFromCart = removeFromCart;
removeFromCart = function(id) {
    _origRemoveFromCart(id);
    saveCartToStorage();
};

var _origChangeQty = changeQty;
changeQty = function(id, delta) {
    _origChangeQty(id, delta);
    saveCartToStorage();
};

var _origClearCart = clearCart;
clearCart = function() {
    _origClearCart();
    try { localStorage.removeItem('kjy_cart'); } catch(e) {}
};

// Load cart from storage on init
document.addEventListener('DOMContentLoaded', function() {
    // Original init
    var origDomReady = function() {
        updateCartDateDisplay();
        loadPOSProducts();
        setInterval(updateCartDateDisplay, 60000);
        applyRole(currentRole);
    };
    
    // Override: load cart from storage first
    var loaded = loadCartFromStorage();
    updateCartDateDisplay();
    if (loaded) {
        renderCart();
    }
    loadPOSProducts();
    setInterval(updateCartDateDisplay, 60000);
    applyRole(currentRole);
});

// ==========================================================================
// PIN VERIFICATION (Boss Mode)
// ==========================================================================

function openPinModal(callback) {
    window._pinCallback = callback;
    document.getElementById('pin-input').value = '';
    document.getElementById('pin-error').classList.add('hidden');
    openModal('modal-pin');
    setTimeout(function() {
        var inp = document.getElementById('pin-input');
        if (inp) inp.focus();
    }, 300);
}

async function verifyPin() {
    var pin = document.getElementById('pin-input').value.trim();
    if (!pin || pin.length < 4) {
        document.getElementById('pin-error').classList.remove('hidden');
        return;
    }

    var btn = document.getElementById('pin-submit');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span> ตรวจสอบ...';

    try {
        var res = await fetch('/api/auth/verify-pin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin: pin })
        });
        var data = await res.json();

        if (data.verified) {
            closeModal('modal-pin');
            if (window._pinCallback) {
                window._pinCallback();
                window._pinCallback = null;
            }
            showToast('✅ PIN ถูกต้อง! เข้าสู่โหมด Owner', 'success');
        } else {
            document.getElementById('pin-error').classList.remove('hidden');
            document.getElementById('pin-input').value = '';
            document.getElementById('pin-input').focus();
        }
    } catch (err) {
        showToast('เกิดข้อผิดพลาด: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-check"></i> ยืนยัน';
    }
}

// Override toggleRole to require PIN for owner mode
var _origToggleRole = toggleRole;
toggleRole = function() {
    if (currentRole === 'staff') {
        openPinModal(function() {
            currentRole = 'owner';
            applyRole('owner');
            showToast('เข้าสู่โหมด Owner 🔑', 'success');
        });
    } else {
        currentRole = 'staff';
        if (['receive', 'reports'].indexOf(currentView) !== -1) {
            switchView('pos');
        }
        applyRole('staff');
        showToast('เข้าสู่โหมด Staff 👷', 'success');
    }
};

// ==========================================================================
// EDIT PRODUCT
// ==========================================================================

function openEditProduct(productId) {
    // Reset edit state variables
    editProdImageFile = null;
    editProdImageBase64 = null;
    editLocImageFile = null;
    editLocImageBase64 = null;
    editExistingImageUrl = '';   // Store current image URL for fallback
    editExistingLocImageUrl = ''; // Store current location image URL

    openModal('modal-edit');

    // Reset UI: show placeholders, hide images
    var imgProd = document.getElementById('img-edit-prod');
    var imgLoc = document.getElementById('img-edit-loc');
    var phProd = document.getElementById('ph-edit-prod');
    var phLoc = document.getElementById('ph-edit-loc');
    if (imgProd) { imgProd.classList.add('hidden'); imgProd.src = ''; }
    if (imgLoc) { imgLoc.classList.add('hidden'); imgLoc.src = ''; }
    if (phProd) phProd.classList.remove('hidden');
    if (phLoc) phLoc.classList.remove('hidden');

    // Fetch product data
    fetch('/api/staff/products/' + productId)
        .then(function(res) { return res.json(); })
        .then(function(p) {
            document.getElementById('e-id').value = p.id || '';
            document.getElementById('e-name').value = p.name || '';
            document.getElementById('e-cat').value = p.category || '';
            document.getElementById('e-sku').value = p.sku || '';
            document.getElementById('e-qty').value = p.stock_qty || 0;
            document.getElementById('e-min').value = p.min_stock || 5;
            document.getElementById('e-price').value = p.sale_price || 0;
            document.getElementById('e-loc').value = p.location_code || '';
            document.getElementById('e-location').value = p.location || '';
            document.getElementById('e-desc').value = p.description || '';
            document.getElementById('edit-title').textContent = '- ' + (p.name || '');

            // Populate front/warehouse stock fields (if available)
            var eFrontStock = document.getElementById('e-front-stock');
            if (eFrontStock) eFrontStock.value = p.front_stock !== undefined ? p.front_stock : (p.stock_qty || 0);
            var eWarehouseStock = document.getElementById('e-warehouse-stock');
            if (eWarehouseStock) eWarehouseStock.value = p.warehouse_stock !== undefined ? p.warehouse_stock : 0;
            var eCost = document.getElementById('e-cost');
            if (eCost) eCost.value = p.cost_price !== undefined ? p.cost_price : (p.latest_cost || '');

            // Store existing image URLs for fallback when saving
            editExistingImageUrl = p.image_path || p.image_url || '';
            editExistingLocImageUrl = p.location_image_path || p.location_image_url || '';

            // Set image previews with onerror fallback
            var imgProd = document.getElementById('img-edit-prod');
            var imgLoc = document.getElementById('img-edit-loc');
            var phProd = document.getElementById('ph-edit-prod');
            var phLoc = document.getElementById('ph-edit-loc');

            if (editExistingImageUrl) {
                imgProd.src = editExistingImageUrl;
                imgProd.classList.remove('hidden');
                imgProd.onerror = function() {
                    this.classList.add('hidden');
                    if (phProd) phProd.classList.remove('hidden');
                };
                if (phProd) phProd.classList.add('hidden');
            }
            if (editExistingLocImageUrl) {
                imgLoc.src = editExistingLocImageUrl;
                imgLoc.classList.remove('hidden');
                imgLoc.onerror = function() {
                    this.classList.add('hidden');
                    if (phLoc) phLoc.classList.remove('hidden');
                };
                if (phLoc) phLoc.classList.add('hidden');
            }

            // Show/hide delete button based on role (Owner only)
            var delBtn = document.getElementById('btn-delete-product');
            if (delBtn) {
                delBtn.classList.remove('hidden');
            }
        })
        .catch(function(err) {
            showToast('ไม่สามารถโหลดข้อมูลสินค้า: ' + err.message, 'error');
            closeModal('modal-edit');
        });
}

var editProdImageFile = null;
var editProdImageBase64 = null;
var editLocImageFile = null;
var editLocImageBase64 = null;
var editExistingImageUrl = '';   // Stores current product image URL from DB
var editExistingLocImageUrl = ''; // Stores current location image URL from DB

function onEditProdImg(event) {
    var file = event.target.files[0];
    if (!file) return;

    // บีบอัดรูปก่อนอัปโหลด (กัน Error 1024KB Limit)
    compressImage(file).then(function(compressed) {
        editProdImageFile = compressed;
        editProdImageBase64 = null;
        var reader = new FileReader();
        reader.onload = function(e) {
            var dataUrl = e.target.result;
            editProdImageBase64 = dataUrl;
            var preview = document.getElementById('img-edit-prod');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('hidden');
            }
            var ph = document.getElementById('ph-edit-prod');
            if (ph) ph.classList.add('hidden');
        };
        reader.readAsDataURL(compressed);
    });
}

function onEditLocImg(event) {
    var file = event.target.files[0];
    if (!file) return;

    // บีบอัดรูปก่อนอัปโหลด (กัน Error 1024KB Limit)
    compressImage(file).then(function(compressed) {
        editLocImageFile = compressed;
        editLocImageBase64 = null;
        var reader = new FileReader();
        reader.onload = function(e) {
            var dataUrl = e.target.result;
            editLocImageBase64 = dataUrl;
            var preview = document.getElementById('img-edit-loc');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('hidden');
            }
            var ph = document.getElementById('ph-edit-loc');
            if (ph) ph.classList.add('hidden');
        };
        reader.readAsDataURL(compressed);
    });
}

async function submitEdit(event) {
    event.preventDefault();

    var id = document.getElementById('e-id').value;
    var name = document.getElementById('e-name').value.trim();
    var category = document.getElementById('e-cat').value.trim();
    var sku = document.getElementById('e-sku').value.trim();
    var stock = document.getElementById('e-qty').value;
    var minStock = document.getElementById('e-min').value;
    var price = document.getElementById('e-price').value;
    var loc = document.getElementById('e-loc').value.trim();
    var location = document.getElementById('e-location').value.trim();
    var desc = document.getElementById('e-desc').value.trim();
    var frontStock = document.getElementById('e-front-stock') ? document.getElementById('e-front-stock').value : stock;
    var warehouseStock = document.getElementById('e-warehouse-stock') ? document.getElementById('e-warehouse-stock').value : '0';
    var cost = document.getElementById('e-cost') ? document.getElementById('e-cost').value : '';

    if (!name || !id) {
        showToast('กรุณากรอกชื่อสินค้า', 'error');
        return;
    }

    var submitBtn = document.getElementById('btn-edit-submit');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-sm"></span> กำลังบันทึก...';

    try {
        // Upload Base64 images first to get permanent URLs, or keep existing ones
        var imageUrl = editExistingImageUrl;  // Default to existing image URL
        var locationImageUrl = editExistingLocImageUrl; // Default to existing location image URL

        // If user selected a new product image, upload it
        if (editProdImageBase64) {
            try {
                var imgRes = await fetch('/api/upload/image-base64', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data_url: editProdImageBase64, prefix: 'prod', folder: 'products' })
                });
                if (imgRes.ok) {
                    var imgData = await imgRes.json();
                    imageUrl = imgData.url || imageUrl;
                }
            } catch (e) { console.warn('Edit image upload failed:', e); }
        }

        // If user selected a new location image, upload it
        if (editLocImageBase64) {
            try {
                var locRes = await fetch('/api/upload/image-base64', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data_url: editLocImageBase64, prefix: 'loc', folder: 'locations' })
                });
                if (locRes.ok) {
                    var locData = await locRes.json();
                    locationImageUrl = locData.url || locationImageUrl;
                }
            } catch (e) { console.warn('Edit location image upload failed:', e); }
        }

        var formData = new FormData();
        formData.append('name', name);
        formData.append('category', category);
        formData.append('sku', sku);
        formData.append('stock_qty', stock);
        formData.append('front_stock', frontStock);
        formData.append('warehouse_stock', warehouseStock);
        formData.append('min_stock', minStock);
        formData.append('location_code', loc);
        formData.append('location', location);
        formData.append('description', desc);
        formData.append('sale_price', price);
        // Owner can update cost_price
        if (cost !== '' && currentRole === 'owner') {
            formData.append('cost_price', cost);
        }
        // Always send image URLs - if no new image, send existing URL to preserve it
        formData.append('image_path', imageUrl);
        formData.append('location_image_path', locationImageUrl);
        if (editProdImageFile) formData.append('file', editProdImageFile);
        if (editLocImageFile) formData.append('location_file', editLocImageFile);

        var res = await fetch('/api/staff/products/' + id + '/edit', {
            method: 'POST',
            body: formData
        });
        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'บันทึกไม่สำเร็จ');
        }

        closeModal('modal-edit');
        showToast('✅ แก้ไขสินค้า "' + name + '" เรียบร้อย', 'success');

        editProdImageFile = null;
        editProdImageBase64 = null;
        editLocImageFile = null;
        editLocImageBase64 = null;

        // === Refresh BOTH views (Stock + POS) ALWAYS ===
        // 1. Refresh Stock Table
        loadStockTable();
        // 2. Refresh POS Product Grid
        var kw = document.getElementById('pos-search') ? document.getElementById('pos-search').value.trim() : '';
        loadPOSProducts(kw, currentCategory);

    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> บันทึกการแก้ไข';
    }
}

// ==========================================================================
// DELETE PRODUCT (Staff & Owner)
// ==========================================================================

var deleteTargetProductId = null;

/**
 * เปิด Modal ยืนยันการลบสินค้าโดยตรงจากตารางคลัง (ไม่ต้องผ่านหน้าแก้ไข)
 * @param {number} productId - รหัสสินค้า
 * @param {string} productName - ชื่อสินค้า
 */
function deleteProductDirect(productId, productName) {
    deleteTargetProductId = productId;
    var nameEl = document.getElementById('delete-product-name');
    if (nameEl) nameEl.textContent = productName || 'สินค้านี้';
    openModal('modal-delete-confirm');
}

function openDeleteConfirm() {
    var id = document.getElementById('e-id').value;
    var name = document.getElementById('e-name').value.trim() || 'สินค้านี้';
    deleteTargetProductId = id;
    document.getElementById('delete-product-name').textContent = name;
    openModal('modal-delete-confirm');
}

async function confirmDeleteProduct() {
    if (!deleteTargetProductId) return;

    var btn = document.getElementById('btn-delete-confirm');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span> กำลังลบ...';

    try {
        var res = await fetch('/api/staff/products/' + deleteTargetProductId, {
            method: 'DELETE'
        });
        if (!res.ok) {
            res = await fetch('/api/owner/products/' + deleteTargetProductId, {
                method: 'DELETE'
            });
        }

        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'HTTP ' + res.status);
        }

        // 1. Close delete confirm modal
        closeModal('modal-delete-confirm');

        // 2. Close edit + detail modals
        closeModal('modal-edit');
        closeModal('modal-detail');

        // 3. Remove deleted product from cart if present
        removeFromCart(parseInt(deleteTargetProductId));

        // 4. Show toast
        showToast('✅ ลบสินค้าเรียบร้อยแล้ว', 'success');

        // 5. Refresh lists
        loadStockTable();
        loadPOSProducts();
        if (currentRole === 'owner') loadOwnerReports();

        deleteTargetProductId = null;
    } catch (err) {
        showToast('❌ ลบสินค้าไม่สำเร็จ: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-trash-can"></i> ลบสินค้า';
    }
}

// ==========================================================================
// PRODUCT DETAIL MODAL
// ==========================================================================

var detailProduct = null;

function openProductDetail(productId) {
    fetch('/api/staff/products/' + productId)
        .then(function(res) { return res.json(); })
        .then(function(p) {
            detailProduct = p;
            document.getElementById('detail-name').textContent = p.name || '-';
            document.getElementById('detail-sku').textContent = 'SKU: ' + (p.sku || '-');
            document.getElementById('detail-img').src = p.image_path || p.image_url || '/static/images/placeholder.svg';
            document.getElementById('detail-price').textContent = '฿' + fmtMoney(p.sale_price || 0);
            document.getElementById('detail-stock').textContent = (p.stock_qty || 0) + ' ชิ้น';
            document.getElementById('detail-location').textContent = p.location_code || '-';

            // Tags
            var tagsHtml = '';
            if (p.category) tagsHtml += '<span class="detail-tag">' + escHtml(p.category) + '</span>';
            if (p.location_code) tagsHtml += '<span class="detail-tag">📍 ' + escHtml(p.location_code) + '</span>';
            if (p.location) tagsHtml += '<span class="detail-tag">🏪 ' + escHtml(p.location) + '</span>';
            document.getElementById('detail-tags').innerHTML = tagsHtml;

            // Spec
            var specEl = document.getElementById('detail-spec');
            if (p.description) {
                specEl.innerHTML = '<strong>📋 สเปกสินค้า:</strong><br>' + escHtml(p.description);
                specEl.classList.remove('hidden');
            } else {
                specEl.classList.add('hidden');
            }

            // Description
            var descEl = document.getElementById('detail-desc');
            var descText = '';
            if (p.min_stock) descText += 'สต็อกขั้นต่ำ: ' + p.min_stock + ' ชิ้น. ';
            if (p.stock_qty <= (p.min_stock || 5)) descText += '⚠️ สินค้าใกล้หมดสต็อก!';
            descEl.textContent = descText || 'ไม่มีข้อมูลเพิ่มเติม';

            document.getElementById('detail-add-btn').onclick = function() {
                addToCart(p.id, p.name, p.sale_price || 0, p.stock_qty || 0, p.image_path || p.image_url || '');
                closeModal('modal-detail');
            };

            openModal('modal-detail');
        })
        .catch(function(err) {
            showToast('เกิดข้อผิดพลาด: ' + err.message, 'error');
        });
}

function addToCartFromDetail() {
    if (detailProduct) {
        addToCart(detailProduct.id, detailProduct.name, detailProduct.sale_price || 0, detailProduct.stock_qty || 0, detailProduct.image_path || detailProduct.image_url || '');
        closeModal('modal-detail');
    }
}

// ==========================================================================
// EXPORT PRODUCTS TO EXCEL (Owner only)
// ==========================================================================

/**
 * ดาวน์โหลดรายงานสินค้าคลังเป็นไฟล์ Excel (.xlsx)
 * เรียกใช้ API /api/owner/export-excel
 */
function exportProductsExcel() {
    if (currentRole !== 'owner') {
        showToast('ต้องเข้าสู่โหมด Owner เพื่อ Export Excel', 'error');
        return;
    }

    showToast('กำลังสร้างไฟล์ Excel...', 'success');
    window.location.href = '/api/owner/export-excel';
}

// ==========================================================================
// AUDIT LOG
// ==========================================================================

function openAuditLog() {
    openPinModal(function() {
        loadAuditLogs();
        openModal('modal-audit');
    });
}

async function loadAuditLogs() {
    var tbody = document.getElementById('audit-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" class="empty"><i class="fa-solid fa-spinner fa-spin"></i> กำลังโหลด...</td></tr>';

    try {
        var res = await fetch('/api/owner/audit-logs?limit=100');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var logs = await res.json();

        if (!logs || logs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="empty">ไม่มีรายการ Audit Log</td></tr>';
            return;
        }

        var html = '';
        for (var i = 0; i < logs.length; i++) {
            var log = logs[i];
            html += '<tr>' +
                '<td style="font-size:11px;color:var(--text-muted);white-space:nowrap">' + escHtml(log.timestamp || log.created_at || '-') + '</td>' +
                '<td><span class="stock-tag" style="background:var(--blue-light);color:var(--blue)">' + escHtml(log.action_type) + '</span></td>' +
                '<td>' + escHtml(log.description || '-') + '</td>' +
                '<td>' + escHtml(log.performed_by || '-') + '</td></tr>';
        }
        tbody.innerHTML = html;

    } catch (err) {
        console.error('Audit log error:', err);
        tbody.innerHTML = '<tr><td colspan="4" class="empty" style="color:var(--red)">เกิดข้อผิดพลาด: ' + err.message + '</td></tr>';
    }
}

// ==========================================================================
// AI SPEC GENERATION
// ==========================================================================

async function generateSpec(mode) {
    var nameField = mode === 'add' ? document.getElementById('a-name') : document.getElementById('e-name');
    var catField = mode === 'add' ? document.getElementById('a-cat') : document.getElementById('e-cat');
    var descField = mode === 'add' ? document.getElementById('a-desc') : document.getElementById('e-desc');

    if (!nameField || !nameField.value.trim()) {
        showToast('กรุณากรอกชื่อสินค้าก่อน', 'error');
        return;
    }

    var btn = event ? event.target : null;
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    try {
        var res = await fetch('/api/ai/generate-spec', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: nameField.value.trim(), category: catField ? catField.value.trim() : '' })
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var data = await res.json();

        if (descField && data.spec) {
            descField.value = data.spec;
            showToast('✨ AI สรุปสเปกสินค้าเรียบร้อย', 'success');
        } else if (data.spec) {
            showToast('✨ AI: ' + data.spec.substring(0, 60) + '...', 'success');
        }
    } catch (err) {
        console.error('AI spec failed:', err);
        showToast('AI สรุปสเปกไม่สำเร็จ: ' + err.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '✨ AI'; }
    }
}

// ==========================================================================
// ENHANCED PRODUCT CARD - Add click to open detail + edit button in stock
// ==========================================================================

// Override renderProductCard to add click handler for image
var _origRenderProductCard = renderProductCard;
renderProductCard = function(p) {
    var html = _origRenderProductCard(p);
    // Add click handler on image to open detail
    var id = p.id;
    html = html.replace('<div class="product-card" data-id="' + id + '">',
        '<div class="product-card" data-id="' + id + '" onclick="openProductDetail(' + id + ')">');
    return html;
};

// ==========================================================================
// QUICK PRICE EDIT (Owner Only - Inline Editing in Stock Table)
// ==========================================================================

function quickEditPrice(productId, currentPrice) {
    if (currentRole !== 'owner') {
        showToast('ต้องเข้าสู่โหมด Owner เพื่อแก้ไขราคา', 'error');
        return;
    }

    var newPrice = prompt('แก้ไขราคาขาย (บาท):\nสินค้า ID: ' + productId, currentPrice);
    if (newPrice === null) return; // Cancelled

    newPrice = parseFloat(newPrice);
    if (isNaN(newPrice) || newPrice < 0) {
        showToast('กรุณากรอกราคาที่ถูกต้อง', 'error');
        return;
    }

    var btn = event.target;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span>';

    fetch('/api/staff/products/' + productId + '/edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'sale_price=' + encodeURIComponent(newPrice.toFixed(2))
    })
    .then(function(res) { return res.json(); })
    .then(function(data) {
        showToast('✅ แก้ไขราคาเรียบร้อย', 'success');
        loadStockTable();
        loadPOSProducts();
    })
    .catch(function(err) {
        showToast('❌ แก้ไขราคาไม่สำเร็จ: ' + err.message, 'error');
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-pen"></i>';
    });
}

// Override loadStockTable to add quick price edit
var _origLoadStockTable = loadStockTable;
loadStockTable = function(keyword) {
    if (keyword === undefined) keyword = '';
    var tbody = document.getElementById('stock-body');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="8" class="empty"><i class="fa-solid fa-spinner fa-spin"></i> กำลังโหลด...</td></tr>';

    try {
        var url = '/api/staff/products?';
        if (keyword) url += 'keyword=' + encodeURIComponent(keyword) + '&';

        fetch(url)
            .then(function(res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function(products) {
                if (!products || products.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" class="empty">ไม่พบรายการสินค้า</td></tr>';
                    return;
                }

                var html = '';
                for (var i = 0; i < products.length; i++) {
                    var p = products[i];
                    var stock = parseInt(p.stock_qty) || 0;
                    var minStock = parseInt(p.min_stock) || 5;
                    var price = parseFloat(p.sale_price) || 0;
                    var rawName = p.name || '-';
                    var name = escHtml(rawName);
                    var sku = escHtml(p.sku || '-');
                    var cat = escHtml(p.category || '-');
                    var loc = escHtml(p.location_code || '-');
                    var imgSrc = p.image_path || p.image_url || '';
                    var locImgSrc = p.location_image_path || p.location_image_url || '';

                    var stockTagClass = 'ok';
                    if (stock === 0) stockTagClass = 'out';
                    else if (stock <= minStock) stockTagClass = 'low-warning';
                    else if (stock <= 5) stockTagClass = 'low';

                    var stockTag = '<span class="stock-tag ' + stockTagClass + '">' + (stock === 0 ? 'หมด' : stock + ' ชิ้น') + '</span>';

                    var thumbHtml = '';
                    if (imgSrc) {
                        thumbHtml = '<img src="' + escHtml(imgSrc) + '" class="product-thumb" alt="' + name + '" onerror="this.src=\'/static/images/placeholder.svg\'">';
                    } else {
                        thumbHtml = '<div class="product-thumb-icon"><i class="fa-solid fa-image"></i></div>';
                    }

                    var locPhotoBtn = '';
                    if (locImgSrc) {
                        locPhotoBtn = '<button class="btn-icon" onclick="openLocationModal(\'' + escHtml(locImgSrc) + '\',\'' + name + '\',\'' + loc + '\')" title="ดูรูปตำแหน่ง"><i class="fa-solid fa-image"></i></button>';
                    } else {
                        locPhotoBtn = '<span style="color:var(--text-muted);font-size:11px">-</span>';
                    }

                    // Quick price edit button (owner only)
                    var priceCell = '<td class="r" style="font-family:\'Inter\',sans-serif;font-weight:700;color:var(--blue);cursor:pointer" onclick="quickEditPrice(' + p.id + ',' + price + ')" title="คลิกเพื่อแก้ไขราคา">฿' + fmtMoney(price) + ' <i class="fa-solid fa-pen" style="font-size:10px;opacity:0.5"></i></td>';

                    html += '<tr><td><div class="product-cell">' + thumbHtml +
                        '<div><div class="product-name-cell">' + name + '</div><div class="product-sku-cell">' + sku + '</div></div></div></td>' +
                        '<td>' + cat + '</td>' +
                        '<td><code style="font-size:11px;color:var(--blue)">' + loc + '</code></td>' +
                        '<td class="c">' + stockTag + '</td>' +
                        priceCell +
                        '<td class="c">' + locPhotoBtn + '</td>' +
                        '<td><div class="action-btns">' +
                        '<button class="btn-icon" onclick="transferStock(' + p.id + ',\'' + escHtml(rawName) + '\')" title="ย้ายสต็อก"><i class="fa-solid fa-right-left"></i></button>' +
                        '<button class="btn-icon" onclick="openEditProduct(' + p.id + ')" title="แก้ไข"><i class="fa-solid fa-pen"></i></button>' +
                        '<button class="btn-icon" onclick="addToCart(' + p.id + ',\'' + escHtml(rawName) + '\',' + price + ',' + stock + ',\'' + escHtml(imgSrc) + '\')" title="เพิ่มลงตะกร้า"' + (stock === 0 ? ' disabled' : '') + '>' +
                        '<i class="fa-solid fa-cart-plus"></i></button>' +
                        '<button class="btn-icon" style="color:var(--red)" onclick="deleteProductDirect(' + p.id + ',\'' + escHtml(rawName) + '\')" title="ลบสินค้า"><i class="fa-solid fa-trash-can"></i></button>' +
                        '</div></td></tr>';
                }
                tbody.innerHTML = html;
            })
            .catch(function(err) {
                console.error('Stock load error:', err);
                tbody.innerHTML = '<tr><td colspan="8" class="empty" style="color:var(--red)">เกิดข้อผิดพลาด: ' + err.message + '</td></tr>';
            });
    } catch (err) {
        console.error('Stock load error:', err);
        tbody.innerHTML = '<tr><td colspan="8" class="empty" style="color:var(--red)">เกิดข้อผิดพลาด: ' + err.message + '</td></tr>';
    }
};

// Update stock table header to include edit column
document.addEventListener('DOMContentLoaded', function() {
    var stockTable = document.querySelector('#view-stock .data-table thead tr');
    if (stockTable) {
        stockTable.innerHTML = '<th>สินค้า</th><th>หมวดหมู่</th><th>ตำแหน่ง</th><th class="c">สต็อก</th><th class="r">ราคาขาย</th><th class="c">รูป</th><th class="c">จัดการ</th>';
    }
    var stockBody = document.getElementById('stock-body');
    if (stockBody) {
        stockBody.innerHTML = '<tr><td colspan="7" class="empty">กำลังโหลด...</td></tr>';
    }
});

// ==========================================================================
// UPDATE ONERROR HANDLERS FOR ALL IMAGES
// ==========================================================================

// This handles the onerror fallback for all dynamically created images
// The static HTML already has onerror on relevant img tags
// For dynamic content, the render functions already have onerror handlers

// ==========================================================================
// DYNAMIC CATEGORY TABS
// ==========================================================================

async function loadCategoryTabs() {
    try {
        var res = await fetch('/api/staff/products?limit=100');
        if (!res.ok) return;
        var products = await res.json();
        if (!products || products.length === 0) return;

        // Extract unique categories
        var cats = {};
        for (var i = 0; i < products.length; i++) {
            var cat = products[i].category;
            if (cat && !cats[cat]) cats[cat] = true;
        }

        var catList = Object.keys(cats);
        if (catList.length === 0) return;

        var chipsContainer = document.querySelector('.chips');
        if (!chipsContainer) return;

        // Keep "ทั้งหมด" button, replace others
        var allBtn = chipsContainer.querySelector('.chip');
        if (!allBtn) return;

        var html = allBtn.outerHTML;
        for (var j = 0; j < catList.length; j++) {
            var cat = catList[j];
            if (cat === 'ALL') continue;
            var icon = '📦';
            if (cat.indexOf('น็อต') !== -1 || cat.indexOf('สกรู') !== -1) icon = '🔩';
            else if (cat.indexOf('สายพาน') !== -1) icon = '⚙️';
            else if (cat.indexOf('กรอง') !== -1) icon = '🌀';
            else if (cat.indexOf('น้ำมัน') !== -1) icon = '🛢️';
            else if (cat.indexOf('ยาง') !== -1) icon = '🔘';
            else if (cat.indexOf('อะไหล่') !== -1 || cat.indexOf('เกษตร') !== -1) icon = '🚜';
            html += '<button class="chip" onclick="pickCategory(\'' + escHtml(cat) + '\',this)">' + icon + ' ' + escHtml(cat) + '</button>';
        }
        chipsContainer.innerHTML = html;
    } catch (e) {
        console.warn('Failed to load category tabs:', e);
    }
}

// ==========================================================================
// AI SALES ASSISTANT FLOATING WIDGET
// ==========================================================================

var salesAssistantOpen = false;
var salesAssistantMessages = [];

function toggleSalesAssistant() {
    salesAssistantOpen = !salesAssistantOpen;
    var widget = document.getElementById('sales-assistant-widget');
    if (widget) {
        if (salesAssistantOpen) {
            widget.classList.remove('hidden');
            widget.classList.add('show');
            if (salesAssistantMessages.length === 0) {
                addAssistantMessage('สวัสดีครับ! 👋 ฉันคือผู้ช่วยขาย AI\nฉันสามารถช่วย:\n• ค้นหาสินค้า\n• เช็คสต็อก\n• แนะนำสินค้า\n• คำนวณราคา\n\nพิมพ์คำถามได้เลยครับ!');
            }
        } else {
            widget.classList.remove('show');
            widget.classList.add('hidden');
        }
    }
}

function addAssistantMessage(text, isUser) {
    var chatBody = document.getElementById('assistant-chat-body');
    if (!chatBody) return;

    var msgDiv = document.createElement('div');
    msgDiv.className = 'assistant-msg ' + (isUser ? 'user' : 'bot');
    msgDiv.textContent = text;
    chatBody.appendChild(msgDiv);
    chatBody.scrollTop = chatBody.scrollHeight;

    salesAssistantMessages.push({ text: text, isUser: isUser });
}

async function sendAssistantMessage() {
    var input = document.getElementById('assistant-input');
    if (!input) return;
    var message = input.value.trim();
    if (!message) return;

    // Add user message
    addAssistantMessage(message, true);
    input.value = '';

    // Show typing indicator
    var typingDiv = document.createElement('div');
    typingDiv.id = 'assistant-typing';
    typingDiv.className = 'assistant-msg bot';
    typingDiv.textContent = 'กำลังคิด...';
    var chatBody = document.getElementById('assistant-chat-body');
    if (chatBody) chatBody.appendChild(typingDiv);
    if (chatBody) chatBody.scrollTop = chatBody.scrollHeight;

    try {
        // Build context from current products
        var context = '';
        if (productsCache && productsCache.length > 0) {
            context = 'สินค้าที่มีอยู่:\n';
            for (var i = 0; i < Math.min(productsCache.length, 20); i++) {
                var p = productsCache[i];
                context += '- ' + p.name + ' (SKU: ' + (p.sku || '-') + ') หมวดหมู่: ' + (p.category || '-') + ' ราคา: ฿' + (p.sale_price || 0) + ' สต็อก: ' + (p.stock_qty || 0) + ' ตำแหน่ง: ' + (p.location_code || '-') + '\n';
            }
        }

        var payload = {
            message: message,
            context: context,
            cart: cart.map(function(item) {
                return { name: item.name, qty: item.qty, price: item.price };
            })
        };

        var res = await fetch('/api/ai/sales-assistant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        // Remove typing indicator
        var typing = document.getElementById('assistant-typing');
        if (typing) typing.remove();

        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'HTTP ' + res.status);
        }

        var data = await res.json();
        var reply = data.reply || 'ขออภัย ฉันไม่เข้าใจคำถาม กรุณาลองใหม่';
        addAssistantMessage(reply, false);

    } catch (err) {
        var typing = document.getElementById('assistant-typing');
        if (typing) typing.remove();
        addAssistantMessage('❌ เกิดข้อผิดพลาด: ' + err.message, false);
    }
}

// ==========================================================================
// EXCEL/CSV IMPORT
// ==========================================================================

async function importProductsFromFile(file) {
    if (!file) return;

    var formData = new FormData();
    formData.append('file', file);

    try {
        var res = await fetch('/api/owner/import-products', { method: 'POST', body: formData });
        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'Import ไม่สำเร็จ');
        }

        var data = await res.json();
        showToast('✅ Import สำเร็จ! เพิ่มสินค้า ' + (data.imported || 0) + ' รายการ', 'success');

        // Refresh views
        loadStockTable();
        loadPOSProducts();
        if (currentRole === 'owner') loadOwnerReports();

    } catch (err) {
        showToast('❌ Import ไม่สำเร็จ: ' + err.message, 'error');
    }
}

function openImportModal() {
    openModal('modal-import');
}

function handleImportFile(event) {
    var file = event.target.files[0];
    if (!file) return;
    importProductsFromFile(file);
}

// ==========================================================================
// STOCK TRANSFER (Front <-> Warehouse)
// ==========================================================================

function transferStock(productId, productName) {
    if (!productId) return;

    var qty = prompt('ย้ายสต็อก "' + productName + '"\n\nกรอกจำนวนที่ต้องการย้าย:', '1');
    if (qty === null) return;

    qty = parseInt(qty);
    if (isNaN(qty) || qty <= 0) {
        showToast('กรุณากรอกจำนวนที่ถูกต้อง', 'error');
        return;
    }

    var direction = confirm('ย้ายจากคลังหลังร้าน -> หน้าร้าน?\n\nกด OK = คลัง -> หน้าร้าน\nกด Cancel = หน้าร้าน -> คลัง') ? 'to_front' : 'to_warehouse';

    fetch('/api/staff/products/' + productId + '/transfer-stock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ qty: qty, direction: direction })
    })
    .then(function(res) { return res.json(); })
    .then(function(data) {
        if (data.status === 'ok') {
            showToast('✅ ย้ายสต็อกเรียบร้อย', 'success');
            loadStockTable();
            loadPOSProducts();
        } else {
            showToast('❌ ' + (data.detail || 'ย้ายสต็อกไม่สำเร็จ'), 'error');
        }
    })
    .catch(function(err) {
        showToast('❌ ย้ายสต็อกไม่สำเร็จ: ' + err.message, 'error');
    });
}

// ==========================================================================
// BULK PRICE ADJUSTER (Owner Only)
// ==========================================================================

function openBulkPriceModal() {
    if (currentRole !== 'owner') {
        showToast('ต้องเข้าสู่โหมด Owner เพื่อใช้ฟีเจอร์นี้', 'error');
        return;
    }
    openModal('modal-bulk-price');
}

async function applyBulkPrice() {
    if (currentRole !== 'owner') {
        showToast('ต้องเข้าสู่โหมด Owner เพื่อใช้ฟีเจอร์นี้', 'error');
        return;
    }

    var category = document.getElementById('bulk-category') ? document.getElementById('bulk-category').value : '';
    var mode = document.getElementById('bulk-mode') ? document.getElementById('bulk-mode').value : 'adjust_percent';
    var value = parseFloat(document.getElementById('bulk-value') ? document.getElementById('bulk-value').value : 0) || 0;
    var applyToCost = document.getElementById('bulk-apply-cost') ? document.getElementById('bulk-apply-cost').checked : false;

    if (value === 0) {
        showToast('กรุณากรอกค่าเป้าหมาย', 'error');
        return;
    }

    if (!confirm('ยืนยันปรับราคาสินค้า' + (category ? ' หมวดหมู่ "' + category + '"' : ' ทั้งหมด') + '?')) return;

    var btn = event.target;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-sm"></span> กำลังปรับ...';

    try {
        var res = await fetch('/api/owner/products/bulk-price', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mode: mode,
                category: category,
                value: value,
                apply_to_cost: applyToCost
            })
        });

        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'HTTP ' + res.status);
        }

        var data = await res.json();
        showToast('✅ ' + (data.message || 'ปรับราคาเรียบร้อย'), 'success');
        closeModal('modal-bulk-price');

        // Refresh views
        loadStockTable();
        loadPOSProducts();
        if (currentRole === 'owner') loadOwnerReports();

    } catch (err) {
        showToast('❌ ปรับราคาไม่สำเร็จ: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-check"></i> ปรับราคา';
    }
}

// ==========================================================================
// ENHANCED LOW STOCK ALERT ON DASHBOARD
// ==========================================================================

// Override loadOwnerReports to show low stock alerts
var _origLoadOwnerReports = loadOwnerReports;
loadOwnerReports = function(keyword) {
    if (keyword === undefined) keyword = '';
    _origLoadOwnerReports(keyword);

    // Also check for low stock products
    fetch('/api/owner/products')
        .then(function(res) { return res.json(); })
        .then(function(products) {
            var lowStockCount = 0;
            for (var i = 0; i < products.length; i++) {
                if (products[i].stock_qty <= (products[i].min_stock || 5)) {
                    lowStockCount++;
                }
            }
            var lowEl = document.getElementById('stat-low');
            if (lowEl) {
                lowEl.textContent = lowStockCount;
                if (lowStockCount > 0) {
                    lowEl.style.color = 'var(--red)';
                }
            }
            // Update sidebar badge
            var badge = document.getElementById('low-stock-badge');
            if (!badge) {
                var navStock = document.getElementById('nav-stock');
                if (navStock && lowStockCount > 0) {
                    badge = document.createElement('span');
                    badge.id = 'low-stock-badge';
                    badge.className = 'low-stock-badge';
                    badge.textContent = lowStockCount;
                    navStock.style.position = 'relative';
                    navStock.appendChild(badge);
                }
            } else {
                badge.textContent = lowStockCount;
                if (lowStockCount === 0) badge.classList.add('hidden');
                else badge.classList.remove('hidden');
            }
        })
        .catch(function() {});
};


// ==========================================================================
// LOCATION AUTO-SUGGEST (Datalist loader)
// ==========================================================================

function loadLocationOptions(targetInputId) {
    var input = document.getElementById(targetInputId);
    if (!input) return;
    var datalist = document.getElementById('loc-list');
    if (!datalist) return;

    fetch('/api/staff/locations')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var locs = (data && data.locations) || [];
            datalist.innerHTML = '';
            for (var i = 0; i < locs.length; i++) {
                var opt = document.createElement('option');
                opt.value = locs[i];
                datalist.appendChild(opt);
            }
        })
        .catch(function(err) { console.warn('Location load failed:', err); });
}

function addNewLocation() {
    var input = document.getElementById('a-location') || document.getElementById('e-location');
    if (!input) return;
    var newLoc = prompt('เพิ่มตำแหน่งจัดเก็บใหม่:\n(เช่น โซน A1, ชั้น B2)');
    if (!newLoc || !newLoc.trim()) return;
    newLoc = newLoc.trim();
    input.value = newLoc;

    var datalist = document.getElementById('loc-list');
    if (datalist) {
        var exists = false;
        for (var i = 0; i < datalist.options.length; i++) {
            if (datalist.options[i].value === newLoc) { exists = true; break; }
        }
        if (!exists) {
            var opt = document.createElement('option');
            opt.value = newLoc;
            datalist.appendChild(opt);
        }
    }
    showToast('เพิ่มตำแหน่ง "' + newLoc + '" แล้ว', 'success');
}

// Load locations on open modal
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(function() {
        loadLocationOptions('a-location');
        loadLocationOptions('e-location');
    }, 500);
});


// ==========================================================================
// CATEGORY AUTO-SUGGEST (จาก products ที่ใช้อยู่จริงเท่านั้น)
// ==========================================================================

function loadCategoryOptions() {
    var datalist = document.getElementById('cat-list');
    if (!datalist) return;
    fetch('/api/staff/categories')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var cats = (data && data.categories) || [];
            datalist.innerHTML = '';
            for (var i = 0; i < cats.length; i++) {
                var opt = document.createElement('option');
                opt.value = cats[i];
                datalist.appendChild(opt);
            }
        })
        .catch(function(err) { console.warn('Category load failed:', err); });
}

// Load categories when opening add/edit modals
var _origOpenAddProduct = openAddProduct;
openAddProduct = function() {
    if (typeof _origOpenAddProduct === 'function') _origOpenAddProduct();
    loadCategoryOptions();
    loadLocationOptions('a-location');
};

var _origOpenEditProduct = openEditProduct;
openEditProduct = function(productId) {
    if (typeof _origOpenEditProduct === 'function') _origOpenEditProduct(productId);
    loadCategoryOptions();
    setTimeout(function() { loadLocationOptions('e-location'); }, 300);
};


// ==========================================================================
// SIDEBAR TOGGLE (Hover-to-Expand + Touch/iPad support)
// ==========================================================================

function toggleSidebar() {
    var sidebar = document.getElementById('sidebar');
    if (sidebar) sidebar.classList.toggle('expanded');
}

// Tap outside to collapse on touch devices
document.addEventListener('click', function(e) {
    var sidebar = document.getElementById('sidebar');
    if (sidebar && sidebar.classList.contains('expanded') && !sidebar.contains(e.target)) {
        sidebar.classList.remove('expanded');
    }
});

// Collapse expanded sidebar when switching views (cleaner navigation)
function collapseSidebar() {
    var sidebar = document.getElementById('sidebar');
    if (sidebar) sidebar.classList.remove('expanded');
}
