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
// STATE
// ==========================================================================

let currentRole = 'staff';
let currentView = 'pos';
let currentCategory = 'ALL';
let cart = [];
let cartProductMap = {};
let barcodeScannerCtx = 'pos';
let html5QrcodeScanner = null;
let prodImageFile = null;
let locImageFile = null;
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
                '<i class="fa-solid fa-cart-plus"></i></button></div></td></tr>';
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
    locImageFile = null;

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
    prodImageFile = file;

    var reader = new FileReader();
    reader.onload = function(e) {
        var preview = document.getElementById('img-prod');
        if (preview) {
            preview.src = e.target.result;
            preview.classList.remove('hidden');
        }
        var ph = document.getElementById('ph-prod');
        if (ph) ph.classList.add('hidden');
    };
    reader.readAsDataURL(file);

    aiScanProductImage(file);
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
    locImageFile = file;

    var reader = new FileReader();
    reader.onload = function(e) {
        var preview = document.getElementById('img-loc');
        if (preview) {
            preview.src = e.target.result;
            preview.classList.remove('hidden');
        }
        var ph = document.getElementById('ph-loc');
        if (ph) ph.classList.add('hidden');
    };
    reader.readAsDataURL(file);
}

async function submitAdd(event) {
    event.preventDefault();

    var name = document.getElementById('a-name') ? document.getElementById('a-name').value.trim() : '';
    var category = document.getElementById('a-cat') ? document.getElementById('a-cat').value.trim() : '';
    var sku = document.getElementById('a-sku') ? document.getElementById('a-sku').value.trim() : '';
    var stock = document.getElementById('a-qty') ? document.getElementById('a-qty').value : '0';
    var location = document.getElementById('a-loc') ? document.getElementById('a-loc').value.trim() : '';
    var price = document.getElementById('a-price') ? document.getElementById('a-price').value : '0';

    if (!name) {
        showToast('กรุณากรอกชื่อสินค้า', 'error');
        return;
    }

    var submitBtn = document.getElementById('btn-add-submit');
    if (!submitBtn) return;

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-sm"></span> กำลังบันทึก...';

    try {
        var formData = new FormData();
        formData.append('name', name);
        formData.append('category', category);
        formData.append('sku', sku);
        formData.append('stock_qty', stock);
        formData.append('location_code', location);
        formData.append('sale_price', price);
        if (prodImageFile) formData.append('file', prodImageFile);
        if (locImageFile) formData.append('location_file', locImageFile);

        var res = await fetch('/api/staff/products/add', { method: 'POST', body: formData });
        if (!res.ok) {
            var errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || 'บันทึกสินค้าไม่สำเร็จ');
        }

        closeModal('modal-add');
        showToast('✅ เพิ่มสินค้า "' + name + '" เรียบร้อยแล้ว', 'success');

        if (currentView === 'stock') loadStockTable();
        else if (currentView === 'pos') {
            var kw = document.getElementById('pos-search') ? document.getElementById('pos-search').value.trim() : '';
            loadPOSProducts(kw, currentCategory);
        }

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
    html5QrcodeScanner.start(
        { facingMode: 'environment' },
        { fps: 12, qrbox: { width: 260, height: 160 } },
        function(decoded) {
            closeScanner();
            handleBarcodeResult(decoded, ctx);
        },
        function() {}
    ).catch(function(err) {
        console.error('Camera error:', err);
        closeScanner();
        showToast('ไม่สามารถเปิดกล้องได้ กรุณาอนุญาตการเข้าถึงกล้อง', 'error');
    });
}

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
        var input = document.getElementById('pos-search');
        if (input) { input.value = value; debounceSearch('pos'); }
        showToast('สแกน: ' + value, 'success');
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