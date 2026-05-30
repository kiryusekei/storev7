#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║         AUTOPAY STORE BOT — NEXUSDEV          ║
║      v10 - Receipt Image | Group Fix         ║
╚══════════════════════════════════════════════╝
"""

import os, asyncio, sqlite3, aiohttp, logging, re, io
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode

# ══════════════════════════════════════════════
#  ⚙️  KONFIGURASI — EDIT HANYA DI SINI SAJA
# ══════════════════════════════════════════════
BOT_TOKEN  = "ISI_TOKEN_BOT"
ADMIN_IDS  = [123456789]
API_KEY    = "ISI_API_KEY_PAKASIR"       # API Key dari dashboard Pakasir
PAKASIR_PROJECT = "ISI_SLUG_PROYEK"      # Slug proyek dari dashboard Pakasir
# ─────────────────────────────────────────────
PAY_BASE      = "https://app.pakasir.com"
STORE_NAME    = "NEXUS MARKETING"       # Nama toko (tampil di bot & nota)
WEBSITE       = WEBSITE        # Website / link toko
DB_PATH       = "store.db"
POLL_INTERVAL = 5
EXPIRE_SEC    = 300

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ── Font paths untuk receipt image ──
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


# ══════════════════════════════════════════════
#                  DATABASE
# ══════════════════════════════════════════════
def init_db():
    c = sqlite3.connect(DB_PATH)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stock_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            is_sold INTEGER DEFAULT 0,
            sold_at TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            transaction_id TEXT UNIQUE,
            product_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    c.commit()
    c.close()


def db():
    return sqlite3.connect(DB_PATH)

def get_setting(key, default=None):
    r = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default

def set_setting(key, value):
    c = db()
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    c.commit(); c.close()

def register_user(uid, username, first_name):
    c = db()
    c.execute("INSERT OR IGNORE INTO users(id,username,first_name) VALUES(?,?,?)",
              (uid, username or "", first_name or ""))
    c.commit(); c.close()

def all_user_ids():
    return [r[0] for r in db().execute("SELECT id FROM users").fetchall()]

def get_stats():
    c = db()
    prods = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    sales = c.execute("SELECT COUNT(*) FROM transactions WHERE status='paid'").fetchone()[0]
    users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    rev   = c.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE status='paid'").fetchone()[0]
    stock = c.execute("SELECT COUNT(*) FROM stock_items WHERE is_sold=0").fetchone()[0]
    c.close()
    return prods, sales, users, rev, stock

def get_all_products():
    return db().execute("""
        SELECT p.id, p.name, p.price,
               COUNT(CASE WHEN s.is_sold=0 THEN 1 END) AS stok
        FROM products p LEFT JOIN stock_items s ON p.id=s.product_id
        GROUP BY p.id ORDER BY p.id
    """).fetchall()

def get_product(pid):
    return db().execute("""
        SELECT p.id, p.name, p.price, p.description,
               COUNT(CASE WHEN s.is_sold=0 THEN 1 END) AS stok
        FROM products p LEFT JOIN stock_items s ON p.id=s.product_id
        WHERE p.id=? GROUP BY p.id
    """, (pid,)).fetchone()

def take_stock(product_id, qty):
    c = db()
    rows = c.execute(
        "SELECT id,content FROM stock_items WHERE product_id=? AND is_sold=0 LIMIT ?",
        (product_id, qty)).fetchall()
    now = datetime.now().isoformat()
    for r in rows:
        c.execute("UPDATE stock_items SET is_sold=1,sold_at=? WHERE id=?", (now, r[0]))
    c.commit(); c.close()
    return rows

def save_transaction(user_id, username, transaction_id, product_id, qty, amount):
    c = db()
    c.execute(
        "INSERT INTO transactions(user_id,username,transaction_id,product_id,quantity,amount,status)"
        " VALUES(?,?,?,?,?,?,'pending')",
        (user_id, username or "", transaction_id, product_id, qty, amount))
    c.commit(); c.close()

def update_transaction_status(transaction_id, status):
    c = db()
    c.execute("UPDATE transactions SET status=? WHERE transaction_id=?", (status, transaction_id))
    c.commit(); c.close()

def rp(n):
    return "Rp " + "{:,}".format(n).replace(",", ".")

def esc(text):
    """Escape karakter Markdown v1 untuk teks dinamis."""
    if not text:
        return ""
    for ch in ["_", "*", "`", "["]:
        text = text.replace(ch, "\\" + ch)
    return text


# ══════════════════════════════════════════════
#              PAYMENT API
# ══════════════════════════════════════════════
async def create_qris(amount: int, order_id: str = None):
    """Buat transaksi QRIS via Pakasir API."""
    if order_id is None:
        order_id = "INV-" + datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        payload = {
            "project": PAKASIR_PROJECT,
            "order_id": order_id,
            "amount": amount,
            "api_key": API_KEY,
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{PAY_BASE}/api/transactioncreate/qris",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    d = await r.json()
                    p = d.get("payment", {})
                    if p:
                        # Kembalikan format yang kompatibel dengan kode lama
                        return {
                            "order_id":       p.get("order_id", order_id),
                            "amount":         p.get("amount", amount),
                            "total_payment":  p.get("total_payment", amount),
                            "qr_string":      p.get("payment_number", ""),
                            "expired_at":     p.get("expired_at", ""),
                        }
    except Exception as e:
        log.error("create_qris: %s", e)
    return None

def generate_qris_image(qr_string: str) -> io.BytesIO | None:
    """
    Generate gambar QR code dari QRIS string.
    Mengembalikan BytesIO PNG siap kirim ke Telegram, atau None jika gagal.
    """
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M

        qr = qrcode.QRCode(
            version=None,           # auto-size
            error_correction=ERROR_CORRECT_M,
            box_size=10,            # piksel per kotak → gambar ~500px
            border=4,               # 4 modul border putih (standar QRIS)
        )
        qr.add_data(qr_string)
        qr.make(fit=True)

        # Buat gambar putih-hitam standar
        img = qr.make_image(fill_color="black", back_color="white")

        # Tambah padding 20px putih agar tidak terpotong Telegram
        from PIL import Image as PILImage
        pil_img = img.get_image() if hasattr(img, 'get_image') else img
        # Pastikan mode RGB
        pil_img = pil_img.convert("RGB")

        padded = PILImage.new("RGB", (pil_img.width + 40, pil_img.height + 40), "white")
        padded.paste(pil_img, (20, 20))

        buf = io.BytesIO()
        padded.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        buf.name = "qris.png"   # Telegram butuh .name agar deteksi sebagai foto
        return buf
    except Exception as e:
        log.error("generate_qris_image gagal: %s", e)
        return None
    """Cek status pembayaran via Pakasir Transaction Detail API."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{PAY_BASE}/api/transactiondetail",
                params={
                    "project":  PAKASIR_PROJECT,
                    "order_id": txid,
                    "amount":   amount,
                    "api_key":  API_KEY,
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    d = await r.json()
                    status = d.get("transaction", {}).get("status", "")
                    return status == "completed"
    except Exception as e:
        log.error("check_qris: %s", e)
    return False


# ══════════════════════════════════════════════
#              FORMATTERS
# ══════════════════════════════════════════════
HARI_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def fmt_welcome(prods, sales, users, stock, uname_bot, user):
    uname = "@{}".format(esc(user.username)) if user.username else "_Tidak ada_"
    fname = esc(user.first_name or "Kawan")
    return (
        "🏪 *" + STORE_NAME + "*\n"
        "_Toko VPN Terpercaya #1_ ✨\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        "👋 Halo, *{}*! Selamat datang~\n\n"
        "📊 *Statistik Toko:*\n"
        "├ 📦 Produk    : *{}* jenis\n"
        "├ ✅ Terjual   : *{}* transaksi\n"
        "├ 👥 Member    : *{}* user\n"
        "└ 🗃️ Stok      : *{}* tersedia\n\n"
        "👤 *Profil Kamu:*\n"
        "├ Username : {}\n"
        "└ User ID  : `{}`\n\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "🔥 _Stok terbatas\\! Buruan order sekarang_ 👇"
    ).format(fname, prods, sales, users, stock, uname, user.id)


def fmt_catalog(products):
    BADGES = ["🔥 _TERLARIS_", "⚡ _TERMURAH_", "⭐ _UNGGULAN_", "💎 _PREMIUM_"]
    lines = [
        "🛒 *KATALOG PRODUK*",
        "🏪 *" + STORE_NAME + "*",
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
    ]
    for i, (pid, name, price, stok) in enumerate(products, 1):
        badge = BADGES[i - 1] if i <= len(BADGES) else ""
        stok_line = "└ 📦 Stok   : *{}* item".format(stok) if stok > 0 else "└ ❌ Stok   : *Habis*"
        lines += [
            "",
            "*[ {} ]  {}* {}".format(i, esc(name), badge),
            "├ 💰 Harga  : *{}*".format(rp(price)),
            stok_line,
        ]
    lines += [
        "",
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        "_Pilih produk yang kamu inginkan_ 👇",
    ]
    return "\n".join(lines)


def fmt_product_detail(prod, qty=1):
    pid_, name, price, desc, stok = prod
    return (
        "📦 *{}* 🔥\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "💰 Harga   : *{}* / akun\n"
        "📦 Stok    : *{}* tersedia\n"
        "⚡ Proses  : _Otomatis & Instan_\n"
        "🔒 Garansi : _Aktif saat diterima_\n\n"
        "📋 *Deskripsi:*\n"
        "{}\n\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "🧾 Total Bayar : *{}*\n\n"
        "_Tentukan jumlah pembelian:_"
    ).format(
        esc(name),
        rp(price),
        stok,
        esc(desc) if desc else "_Tidak ada deskripsi._",
        rp(price * qty),
    )


def fmt_invoice(name, qty, price, fee, total, exp_min, txid):
    return (
        "🧾 *INVOICE PEMBAYARAN*\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "📦 Produk    : *{}*\n"
        "🔢 Jumlah    : `{}` item\n"
        "💵 Subtotal  : `{}`\n"
        "💳 Fee       : `{}`\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "💰 *Total Bayar : `{}`*\n"
        "⏱️ Berlaku      : `{} menit`\n"
        "🔑 Invoice      : `{}`\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        "📲 *Cara Bayar:*\n"
        "1\\. Buka aplikasi e-wallet / m-banking\n"
        "2\\. Pilih menu *Scan QR / QRIS*\n"
        "3\\. Arahkan ke QR Code di atas\n"
        "4\\. Konfirmasi nominal & bayar\n\n"
        "_Produk otomatis dikirim setelah pembayaran terdeteksi_ ✅"
    ).format(esc(name), qty, rp(price * qty), rp(fee), rp(total), exp_min, txid)


def fmt_delivery_card(user_id, username, prod_name, price, qty, txid, content):
    now     = datetime.now()
    hari    = HARI_ID[now.weekday()]
    tanggal = now.strftime("%d.%m.%Y")
    waktu   = now.strftime("%H:%M") + " WIB"
    uname   = "@{}".format(esc(username)) if username else "`{}`".format(user_id)
    total   = rp(price * qty)
    return (
        "🎁 *PEMBELIAN BERHASIL\\!* 🎁\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "👤 Username : {}\n"
        "🪪 User ID  : `{}`\n"
        "├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "📦 Produk   : *{}*\n"
        "💰 Harga    : `{}`\n"
        "🔢 Jumlah   : `{}` item\n"
        "├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "📅 Hari     : {}\n"
        "🗓️ Tanggal  : {}\n"
        "🕐 Waktu    : {}\n"
        "├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "🔑 Invoice  : `{}`\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        "```\n{}\n```\n\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "📋 _Simpan invoice ini untuk keperluan support_\n"
        "⚠️ _Harap baca deskripsi & TOS sebelum digunakan_ 🙏\n\n"
        "🏪 *" + STORE_NAME + "* | " + WEBSITE + " — Terima kasih sudah belanja\\! 💚"
    ).format(uname, user_id, esc(prod_name), total, qty,
             hari, tanggal, waktu, txid, content)


def fmt_expired_msg(txid, name):
    return (
        "⚠️ *Waktu Pembayaran Berakhir* ⚠️\n\n"
        "Invoice `{}` untuk produk *{}* telah kedaluwarsa.\n\n"
        "Pesanan dibatalkan otomatis. Silakan buat pesanan baru."
    ).format(txid, esc(name))


def fmt_stock_notif(name, price, stok, bot_uname):
    return (
        "🚨 *RESTOCK ALERT\\!* 🚨\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "🔥 *{}* _sudah tersedia kembali\\!_\n\n"
        "├ 💰 Harga  : *{}*\n"
        "├ 📦 Stok   : *{}* item _(terbatas\\!)_\n"
        "└ ⚡ Garansi : _Langsung aktif_\n\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "⏰ _Jangan sampai kehabisan lagi\\!_\n\n"
        "🏪 *" + STORE_NAME + "* | @{}"
    ).format(esc(name), rp(price), stok, esc(bot_uname))


def fmt_group_order(user_id, username, prod_name, price, qty, txid, tipe="💳 QRIS"):
    now     = datetime.now()
    hari    = HARI_ID[now.weekday()]
    tanggal = now.strftime("%d.%m.%Y")
    waktu   = now.strftime("%H:%M") + " WIB"
    uname   = "@{}".format(esc(username)) if username else "`{}`".format(user_id)
    return (
        "🔔 *ORDER MASUK\\!* 🔔\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        "👤 Pembeli  : {}\n"
        "🪪 ID       : `{}`\n"
        "├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "📦 Produk   : *{}*\n"
        "💰 Harga    : `{}`\n"
        "🔢 Jumlah   : `{}` item\n"
        "💳 Tipe     : {}\n"
        "├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "📅 Hari     : {}\n"
        "🗓️ Tanggal  : {}\n"
        "🕐 Waktu    : {}\n"
        "├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
        "🔑 Invoice  : `{}`\n"
        "✅ Status   : _Berhasil_\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
    ).format(uname, user_id, esc(prod_name), rp(price * qty), qty,
             tipe, hari, tanggal, waktu, txid)


async def notif_to_group(app, text):
    gid = get_setting("notif_group_id")
    if not gid:
        return
    try:
        await app.bot.send_message(
            chat_id=int(gid), text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.warning("notif_to_group: %s", e)


# ══════════════════════════════════════════════
#         RECEIPT IMAGE GENERATOR
# ══════════════════════════════════════════════
def generate_receipt(user_id, username, prod_name, price, qty, txid,
                     tipe="QRIS", tanggal=None, waktu=None, hari=None):
    """Generate nota HD premium — 2x scale, tipografi besar & cerah, style struk digital modern."""

    # ── Skala HD: render 2x lalu downscale → tajam & anti-alias ──
    SCALE   = 2
    W_BASE  = 600
    W       = W_BASE * SCALE

    # ── Palet warna cerah profesional ──
    BG          = (255, 255, 255)
    WHITE       = (255, 255, 255)
    BLACK       = (10,  10,  10)
    DARK        = (30,  30,  30)
    GRAY        = (100, 100, 100)
    LGRAY       = (195, 195, 195)
    XLGRAY      = (240, 240, 245)
    ACCENT      = (20,  110, 255)       # biru cerah — aksen utama
    ACCENT2     = (0,   190, 120)       # hijau emerald
    RED_VIVID   = (220,  40,  60)       # merah vivid total
    ORANGE      = (255, 140,   0)
    BG_HDR_TOP  = (255, 255, 255)        # header putih
    BG_HDR_BOT  = (245, 245, 245)        # header putih bawah
    BG_STATUS   = (255, 255, 255)
    BG_TOTAL    = (255, 255, 255)
    BG_INV      = (255, 255, 255)
    BG_SEC      = (248, 248, 248)       # strip latar section (abu sangat terang)

    def _font(path, size):
        try:
            return ImageFont.truetype(path, size * SCALE)
        except Exception:
            return ImageFont.load_default()

    # Font sizes di skala 2x (nilai base)
    f_brand    = _font(_FONT_BOLD, 20)
    f_tagline  = _font(_FONT_SANS, 10)
    f_status   = _font(_FONT_BOLD, 12)
    f_sec_hdr  = _font(_FONT_BOLD, 10)
    f_label    = _font(_FONT_SANS, 11)
    f_value    = _font(_FONT_BOLD, 11)
    f_prodname = _font(_FONT_BOLD, 13)
    f_total_l  = _font(_FONT_BOLD, 13)
    f_total_v  = _font(_FONT_BOLD, 22)
    f_inv      = _font(_FONT_MONO, 9)
    f_footer   = _font(_FONT_SANS, 9)
    f_dot      = _font(_FONT_SANS, 9)

    now     = datetime.now()
    _HARI   = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"]
    hari    = hari    or _HARI[now.weekday()]
    tanggal = tanggal or now.strftime("%d / %m / %Y")
    waktu   = waktu   or now.strftime("%H:%M") + " WIB"
    total   = price * qty
    uname   = "@" + username if username else str(user_id)

    def _rp(n): return "Rp {:,}".format(n).replace(",", ".")

    PAD    = 36 * SCALE
    H_EST  = 1100 * SCALE
    img    = Image.new("RGB", (W, H_EST), BG)
    d      = ImageDraw.Draw(img)

    # ── helpers ──
    def solid_line(y, color=LGRAY, w=1):
        d.line([(PAD, y), (W - PAD, y)], fill=color, width=w * SCALE)

    def full_line(y, color=LGRAY, w=1):
        d.line([(0, y), (W, y)], fill=color, width=w * SCALE)

    def dash_line(y, color=LGRAY):
        seg, gap = 10 * SCALE, 5 * SCALE
        x = PAD
        while x < W - PAD:
            d.line([(x, y), (min(x + seg, W - PAD), y)], fill=color, width=1 * SCALE)
            x += seg + gap

    def row(label, value, y, lc=GRAY, vc=DARK, vfont=None):
        vf = vfont or f_value
        d.text((PAD, y), label, font=f_label, fill=lc)
        d.text((W - PAD, y), value, font=vf, fill=vc, anchor="ra")
        return y + 28 * SCALE

    def section_header(title, y):
        """Strip berwarna + judul section."""
        sh = 24 * SCALE
        d.rectangle([(0, y), (W, y + sh)], fill=BG_SEC)
        d.rectangle([(0, y), (6 * SCALE, y + sh)], fill=BLACK)  # strip kiri hitam
        d.text((PAD, y + sh // 2), title,
               font=f_sec_hdr, fill=BLACK, anchor="lm")
        return y + sh + 8 * SCALE

    y = 0

    # ════════════════════════════════════
    #  HEADER — gradient simulasi (navy→biru)
    # ════════════════════════════════════
    HDR_H = 110 * SCALE
    for i in range(HDR_H):
        t   = i / HDR_H
        r   = int(BG_HDR_TOP[0] + (BG_HDR_BOT[0] - BG_HDR_TOP[0]) * t)
        g   = int(BG_HDR_TOP[1] + (BG_HDR_BOT[1] - BG_HDR_TOP[1]) * t)
        b   = int(BG_HDR_TOP[2] + (BG_HDR_BOT[2] - BG_HDR_TOP[2]) * t)
        d.line([(0, y + i), (W, y + i)], fill=(r, g, b))

    # Brand text
    d.text((W // 2, y + 22 * SCALE), STORE_NAME,
           font=f_brand, fill=BLACK, anchor="mt")
    d.text((W // 2, y + 50 * SCALE), "Official Autopay Store • " + WEBSITE,
           font=f_tagline, fill=GRAY, anchor="mt")

    # Garis batas bawah header
    d.rectangle([(0, y + HDR_H - 3 * SCALE), (W, y + HDR_H)], fill=BLACK)
    y += HDR_H

    # ════ STATUS BERHASIL ════
    d.rectangle([(0, y), (W, y + 42 * SCALE)], fill=BG_STATUS)
    # ikon centang bulat
    cx, cy, cr = W // 2 - 80 * SCALE, y + 21 * SCALE, 11 * SCALE
    d.ellipse([(cx - cr, cy - cr), (cx + cr, cy + cr)], fill=BLACK)
    d.text((cx, cy), "✓", font=f_status, fill=WHITE, anchor="mm")
    d.text((W // 2 - 52 * SCALE, y + 21 * SCALE),
           "PEMBAYARAN BERHASIL", font=f_status, fill=BLACK, anchor="lm")
    full_line(y + 42 * SCALE, BLACK, 2)
    y += 42 * SCALE + 16 * SCALE

    # ════ JUDUL STRUK ════
    d.text((W // 2, y), "STRUK PEMBELIAN DIGITAL",
           font=f_sec_hdr, fill=GRAY, anchor="mt"); y += 18 * SCALE
    d.text((W // 2, y), "Simpan struk ini sebagai bukti transaksi",
           font=f_dot, fill=LGRAY, anchor="mt"); y += 14 * SCALE
    solid_line(y, BLACK, 2); y += 14 * SCALE

    # ════ INFORMASI PEMBELI ════
    y = section_header("INFORMASI PEMBELI", y)
    y = row("Username", uname, y, GRAY, DARK)
    y = row("User ID",  str(user_id), y, GRAY, GRAY)
    solid_line(y); y += 12 * SCALE

    # ════ DETAIL PEMBELIAN ════
    y = section_header("DETAIL PEMBELIAN", y)
    # Nama produk bisa panjang, satu baris khusus
    pname = prod_name if len(prod_name) <= 38 else prod_name[:36] + ".."
    d.text((PAD, y), "Produk", font=f_label, fill=GRAY)
    d.text((W - PAD, y), pname, font=f_prodname, fill=BLACK, anchor="ra")
    y += 30 * SCALE
    y = row("Qty", "{} item".format(qty), y, GRAY, DARK)
    y = row("Metode Bayar", tipe, y, GRAY, BLACK)
    solid_line(y); y += 12 * SCALE

    # ════ RINCIAN HARGA ════
    y = section_header("RINCIAN HARGA", y)
    y = row("Harga Satuan", _rp(price), y, GRAY, DARK)
    y = row("{} item x {}".format(qty, _rp(price)), _rp(total), y, GRAY, DARK)
    solid_line(y, BLACK, 2); y += 12 * SCALE

    # ════ TOTAL BOX ════
    BOX_H = 68 * SCALE
    # shadow tipis
    d.rectangle([(PAD - 2, y + 2), (W - PAD + 2, y + BOX_H + 2)],
                fill=(220, 225, 240))
    # kotak utama
    d.rectangle([(PAD - 4, y), (W - PAD + 4, y + BOX_H)],
                fill=BG_TOTAL, outline=BLACK, width=2 * SCALE)
    # label
    d.text((PAD + 8 * SCALE, y + BOX_H // 2 - 6 * SCALE),
           "TOTAL BAYAR", font=f_total_l, fill=BLACK, anchor="lm")
    # nilai besar
    d.text((W - PAD - 8 * SCALE, y + BOX_H // 2),
           _rp(total), font=f_total_v, fill=RED_VIVID, anchor="rm")
    y += BOX_H + 14 * SCALE
    solid_line(y, BLACK, 2); y += 14 * SCALE

    # ════ WAKTU TRANSAKSI ════
    y = section_header("WAKTU TRANSAKSI", y)
    y = row("Hari",    hari,    y)
    y = row("Tanggal", tanggal, y)
    y = row("Waktu",   waktu,   y)
    solid_line(y); y += 12 * SCALE

    # ════ NO. INVOICE ════
    y = section_header("NO. INVOICE", y)
    INV_H = 36 * SCALE
    d.rectangle([(PAD, y), (W - PAD, y + INV_H)],
                fill=BG_INV, outline=LGRAY, width=1 * SCALE)
    d.text((W // 2, y + INV_H // 2), txid,
           font=f_inv, fill=DARK, anchor="mm")
    y += INV_H + 14 * SCALE

    # ════ FOOTER ════
    solid_line(y, BLACK, 2); y += 10 * SCALE

    # barcode dekoratif HD
    bar_x = PAD
    bar_seed = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3, 8, 4,
                6, 2, 6, 4, 3, 3, 8, 3, 2, 7, 9, 5, 0, 2, 8, 8, 4, 1, 9, 7,
                1, 6, 9, 3, 9, 9, 3, 7, 5, 1, 0, 5, 8, 2, 0, 9, 7, 4, 9, 4]
    BAR_H = 28 * SCALE
    for i, s in enumerate(bar_seed):
        bw = max(1, s % 4) * SCALE
        bc = BLACK if i % 2 == 0 else (230, 230, 230)
        d.rectangle([(bar_x, y), (bar_x + bw, y + BAR_H)], fill=bc)
        bar_x += bw + SCALE
        if bar_x >= W - PAD:
            break
    y += BAR_H + 10 * SCALE

    full_line(y, (235, 235, 235), 1); y += 12 * SCALE
    d.text((W // 2, y), "Terima kasih telah berbelanja di " + STORE_NAME + "!",
           font=f_footer, fill=DARK, anchor="mt"); y += 16 * SCALE
    d.text((W // 2, y), "Produk dikirim otomatis  •  Garansi aktif saat diterima",
           font=f_footer, fill=GRAY, anchor="mt"); y += 16 * SCALE
    d.text((W // 2, y), "© " + STORE_NAME + " — " + WEBSITE,
           font=f_footer, fill=LGRAY, anchor="mt")
    y += 28 * SCALE

    # ── Crop & downscale ke W_BASE (anti-alias HD) ──
    img = img.crop((0, 0, W, y))
    img = img.resize((W_BASE, y // SCALE), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True, dpi=(150, 150))
    buf.seek(0)
    return buf



async def notif_to_group_with_receipt(app, user_id, username, prod_name,
                                      price, qty, txid, tipe="💳 QRIS"):
    """Kirim notif order ke grup: gambar nota + caption teks detail."""
    gid = get_setting("notif_group_id")
    if not gid:
        return
    try:
        now     = datetime.now()
        HARI_ID = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"]
        hari    = HARI_ID[now.weekday()]
        tanggal = now.strftime("%d.%m.%Y")
        waktu   = now.strftime("%H:%M") + " WIB"

        # Generate gambar nota
        receipt_buf = generate_receipt(
            user_id   = user_id,
            username  = username,
            prod_name = prod_name,
            price     = price,
            qty       = qty,
            txid      = txid,
            tipe      = tipe.replace("💳 ", "").replace("🔓 ", ""),
            hari      = hari,
            tanggal   = tanggal.replace(".", " / "),
            waktu     = waktu,
        )

        uname   = "@{}".format(esc(username)) if username else "`{}`".format(user_id)
        caption = (
            "🔥 *ORDER MASUK!* 🔥\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👤 *Pembeli*  : {}\n"
            "📦 *Produk*   : *{}*\n"
            "💰 *Harga*    : `{}`\n"
            "🔢 *Jumlah*   : `{}` item\n"
            "💳 *Metode*   : {}\n"
            "🕐 *Waktu*    : {}, {} — {}\n"
            "🔑 *Invoice*  : `{}`\n"
            "✅ *Status*   : _Berhasil_\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🏪 *" + STORE_NAME + "* | " + WEBSITE
        ).format(
            uname, esc(prod_name), rp(price * qty),
            qty, tipe, hari, tanggal, waktu, txid
        )

        await app.bot.send_photo(
            chat_id    = int(gid),
            photo      = receipt_buf,
            caption    = caption,
            parse_mode = ParseMode.MARKDOWN,
        )
    except Exception as e:
        log.warning("notif_to_group_with_receipt: %s", e)
        # Fallback ke teks biasa jika gambar gagal
        try:
            grp = fmt_group_order(user_id, username, prod_name, price, qty, txid, tipe)
            await app.bot.send_message(
                chat_id=int(gid), text=grp, parse_mode=ParseMode.MARKDOWN)
        except Exception as e2:
            log.warning("notif_to_group fallback: %s", e2)


# ══════════════════════════════════════════════
#              KEYBOARDS
# ══════════════════════════════════════════════
def kb_main(is_admin=False):
    rows = [[
        InlineKeyboardButton("🛒 Belanja Sekarang", callback_data="menu_purchase"),
        InlineKeyboardButton("🆘 Bantuan",          callback_data="menu_help"),
    ],[
        InlineKeyboardButton("📢 Promo & Info Toko", callback_data="menu_promo"),
    ]]
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ Panel Admin", callback_data="adm_panel")])
    return InlineKeyboardMarkup(rows)

def kb_catalog(products):
    rows, nums = [], []
    for i, (pid, *_) in enumerate(products, 1):
        nums.append(InlineKeyboardButton(str(i), callback_data="prod_{}".format(pid)))
        if len(nums) == 5:
            rows.append(nums); nums = []
    if nums:
        rows.append(nums)
    rows.append([InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def kb_product(pid, qty, max_stok, is_admin=False):
    rows = [
        [
            InlineKeyboardButton("➖", callback_data="qty_dec_{}_{}".format(pid, qty)),
            InlineKeyboardButton("  {}  ".format(qty), callback_data="noop"),
            InlineKeyboardButton("➕", callback_data="qty_inc_{}_{}".format(pid, qty)),
        ],
        [InlineKeyboardButton("🛍️ Beli Semua Stok ({} item)".format(max_stok),
                              callback_data="qty_all_{}_{}".format(pid, max_stok))],
        [InlineKeyboardButton("✅  BELI SEKARANG  ✅", callback_data="buy_{}_{}".format(pid, qty))],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🔓 Bypass Pembayaran (Admin)",
                                          callback_data="bypass_{}_{}".format(pid, qty))])
    rows.append([InlineKeyboardButton("🔙 Kembali ke Katalog", callback_data="menu_purchase")])
    return InlineKeyboardMarkup(rows)

def kb_admin_main():
    bc_on = get_setting("auto_broadcast_stock", "on") == "on"
    bc_label = "🔔 Auto-BC Stok : ✅ ON" if bc_on else "🔔 Auto-BC Stok : ❌ OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Tambah Produk",          callback_data="adm_add_prod")],
        [InlineKeyboardButton("📋 Daftar & Kelola Produk", callback_data="adm_list_prod")],
        [InlineKeyboardButton("🖼️ Kelola Media Start",     callback_data="adm_photo_menu")],
        [InlineKeyboardButton("📢 Broadcast",              callback_data="adm_broadcast_menu")],
        [InlineKeyboardButton("🔔 Notifikasi Grup",        callback_data="adm_group_menu")],
        [InlineKeyboardButton(bc_label,                    callback_data="adm_toggle_bc")],
        [InlineKeyboardButton("📊 Statistik Toko",         callback_data="adm_stats")],
        [InlineKeyboardButton("🔙 Menu Utama",             callback_data="main_menu")],
    ])

def kb_cancel_admin():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="adm_cancel")]])

def kb_back_admin():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Admin", callback_data="adm_panel")]])

def kb_broadcast_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Broadcast Teks",  callback_data="adm_bc_text")],
        [InlineKeyboardButton("🖼️ Broadcast Foto",  callback_data="adm_bc_photo")],
        [InlineKeyboardButton("🎬 Broadcast Video", callback_data="adm_bc_video")],
        [InlineKeyboardButton("🔙 Kembali",         callback_data="adm_panel")],
    ])

def kb_photo_menu():
    media_id   = get_setting("start_media_id")
    media_type = get_setting("start_media_type", "photo")
    rows = []
    if media_id:
        label = "🖼️ Foto aktif" if media_type == "photo" else "🎬 Video aktif"
        rows.append([InlineKeyboardButton("✅ " + label, callback_data="noop")])
        rows.append([InlineKeyboardButton("🗑️ Hapus Media Start", callback_data="adm_media_del")])
    rows += [
        [InlineKeyboardButton("🖼️ Upload Foto Baru",  callback_data="adm_media_photo")],
        [InlineKeyboardButton("🎬 Upload Video Baru", callback_data="adm_media_video")],
        [InlineKeyboardButton("🔙 Kembali",           callback_data="adm_panel")],
    ]
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════
#            ADMIN STATES
# ══════════════════════════════════════════════
S_PROD_NAME   = "prod_name"
S_PROD_PRICE  = "prod_price"
S_PROD_DESC   = "prod_desc"
S_MEDIA_PHOTO = "media_photo"
S_MEDIA_VIDEO = "media_video"
S_BC_TEXT     = "bc_text"
S_BC_PHOTO    = "bc_photo"
S_BC_VIDEO    = "bc_video"
S_EDIT_NAME   = "edit_name"
S_EDIT_PRICE  = "edit_price"
S_EDIT_DESC   = "edit_desc"
S_GROUP_ID    = "group_id"


def _clear(ctx):
    for k in ["state", "edit_pid", "p_name", "p_price"]:
        ctx.user_data.pop(k, None)

def _buf(ctx) -> list:
    """Ambil buffer stok admin, buat kalau belum ada."""
    if "stock_buffer" not in ctx.user_data:
        ctx.user_data["stock_buffer"] = []
    return ctx.user_data["stock_buffer"]

def _clear_buf(ctx):
    ctx.user_data["stock_buffer"] = []


# ══════════════════════════════════════════════
#      SAFE EDIT HELPER
# ══════════════════════════════════════════════
async def safe_edit(q, text, kb=None):
    """Edit pesan. Kalau pesan berupa foto/video: hapus dulu, kirim teks baru."""
    kw = {"text": text, "parse_mode": ParseMode.MARKDOWN}
    if kb:
        kw["reply_markup"] = kb
    # Coba edit langsung (berhasil jika pesan teks biasa)
    try:
        await q.edit_message_text(**kw)
        return
    except Exception:
        pass
    # Kalau gagal (misalnya pesan foto), hapus dulu lalu kirim teks baru
    try:
        await q.message.delete()
    except Exception:
        pass
    try:
        await q.message.reply_text(**kw)
    except Exception as e:
        log.error("safe_edit fallback: %s", e)


# ══════════════════════════════════════════════
#   BROADCAST STOK ke semua user & grup
# ══════════════════════════════════════════════
async def _broadcast_stock_notif(ctx_or_app, bot, admin_uid, prod_name, price, stok):
    bot_info = await bot.get_me()
    notif    = fmt_stock_notif(prod_name, price, stok, bot_info.username)
    sent     = 0

    # ── Cek toggle auto-broadcast ke user ──
    bc_on = get_setting("auto_broadcast_stock", "on") == "on"
    if bc_on:
        all_uids = all_user_ids()
        for u in all_uids:
            if u == admin_uid:
                continue
            try:
                await bot.send_message(u, notif, parse_mode=ParseMode.MARKDOWN)
                sent += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass

    # ── Selalu kirim ke grup notifikasi (tidak terpengaruh toggle) ──
    gid = get_setting("notif_group_id")
    if gid:
        try:
            await bot.send_message(int(gid), notif, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.warning("notif grup stok: %s", e)
    return sent


# ══════════════════════════════════════════════
#                  /start
# ══════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name)
    prods, sales, users, rev, stock = get_stats()
    bot_info = await ctx.bot.get_me()
    is_admin = user.id in ADMIN_IDS
    text     = fmt_welcome(prods, sales, users, stock, bot_info.username, user)
    kb       = kb_main(is_admin)

    media_id   = get_setting("start_media_id")
    media_type = get_setting("start_media_type", "photo")

    if media_id:
        try:
            if media_type == "photo":
                await update.message.reply_photo(
                    photo=media_id, caption=text,
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            else:
                await update.message.reply_video(
                    video=media_id, caption=text,
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            return
        except Exception:
            pass

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


# ══════════════════════════════════════════════
#   /add  —  Konfirmasi & simpan buffer ke produk
# ══════════════════════════════════════════════
async def cmd_setgroup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/setgroup <id> — Set grup notifikasi langsung tanpa menu."""
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    args = ctx.args
    gid = None
    if args:
        raw = args[0].strip()
        if re.match(r"^-?\d+$", raw):
            gid = raw
    if not gid:
        await update.message.reply_text(
            "🔔 *Set Grup Notifikasi*\n\n"
            "Gunakan: `/setgroup -1001234567890`\n\n"
            "*Cara dapat ID grup:*\n"
            "1. Tambah @userinfobot ke grup\n"
            "2. Kirim /start di grup\n"
            "3. Copy angka ID lalu kirim ke sini\n"
            "   contoh: `/setgroup -1003482263196`",
            parse_mode=ParseMode.MARKDOWN)
        return
    set_setting("notif_group_id", gid)
    _clear(ctx)
    try:
        await ctx.bot.send_message(
            chat_id=int(gid),
            text="🔔 *NOTIFIKASI GRUP AKTIF* 🔔\n"
                 "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                 "✅ Grup ini berhasil dikonfigurasi!\n"
                 "Notifikasi order & stok baru\n"
                 "akan dikirim ke sini otomatis.\n"
                 "━━━━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(
            "✅ *Grup notifikasi berhasil diset!*\n"
            "ID: `{}`\n\nTest notifikasi sudah dikirim.".format(gid),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_admin_main())
    except Exception as e:
        await update.message.reply_text(
            "⚠️ ID tersimpan tapi gagal kirim test.\n`{}`\n\n"
            "Pastikan bot sudah jadi *Admin* di grup!".format(str(e)[:100]),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_admin_main())


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return

    products = get_all_products()
    if not products:
        await update.message.reply_text(
            "❌ Belum ada produk. Tambah produk dulu via Panel Admin.")
        return

    buf = _buf(ctx)

    # ── Buffer kosong → kasih petunjuk ───────────────
    if not buf:
        await update.message.reply_text(
            "📦 *Mode Tambah Stok - Buffer Kosong*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "*Cara pakai:*\n"
            "1. Kirim pesan satu per satu ke bot\n"
            "   _setiap pesan = 1 item stok_\n"
            "2. Setelah semua item dikirim, ketik /add\n"
            "3. Pilih produk tujuan\n"
            "4. Semua item langsung masuk!\n\n"
            "*Contoh pesan 1 item:*\n"
            "```\n"
            "VPS Tencent Services (TC)\n"
            "root@43.156.150.198 | pass: yha123\n"
            "====================\n"
            "Thank you for using our services\n"
            "```\n\n"
            "❌ _Buffer masih kosong. Kirim item dulu._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Buffer ada isi → tampilkan preview + pilih produk ──
    lines = [
        "*Buffer Stok: {} item siap ditambahkan*".format(len(buf)),
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for i, item in enumerate(buf, 1):
        preview = item.strip().split("\n")[0]
        if len(preview) > 50:
            preview = preview[:50] + "..."
        lines.append("*#{}* `{}`".format(i, esc(preview)))
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━━━", "📦 *Pilih produk tujuan:*"]

    rows = []
    for pid, name, price, stok in products:
        rows.append([InlineKeyboardButton(
            "[{}] {}  (stok: {})".format(pid, name, stok),
            callback_data="adm_cstk_{}".format(pid)
        )])
    rows.append([InlineKeyboardButton("🗑️ Hapus Buffer ({} item)".format(len(buf)),
                                      callback_data="adm_clearbuf")])
    rows.append([InlineKeyboardButton("❌ Batal", callback_data="adm_cancel")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows)
    )


# ══════════════════════════════════════════════
#         SINGLE CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    d   = q.data
    await q.answer()

    if d == "noop":
        return

    # ── Menu Utama ────────────────────────────────────
    if d == "main_menu":
        register_user(uid, q.from_user.username, q.from_user.first_name)
        prods, sales, users, rev, stock = get_stats()
        bot_info = await ctx.bot.get_me()
        text = fmt_welcome(prods, sales, users, stock, bot_info.username, q.from_user)
        # Jika pesan aslinya foto/video (start dengan media), hapus dulu
        if q.message.photo or q.message.video:
            try:
                await q.message.delete()
            except Exception:
                pass
            await ctx.bot.send_message(
                chat_id=uid, text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_main(uid in ADMIN_IDS))
        else:
            await safe_edit(q, text, kb_main(uid in ADMIN_IDS))
        return

    # ── Pembelian / Katalog ───────────────────────────
    if d == "menu_purchase":
        products = get_all_products()
        if not products:
            await safe_edit(q, "❌ Belum ada produk tersedia.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="main_menu")
                ]]))
            return
        await safe_edit(q, fmt_catalog(products), kb_catalog(products))
        return

    # ── Bantuan ───────────────────────────────────────
    if d == "menu_help":
        await safe_edit(q,
            "🆘 *BANTUAN & PANDUAN*\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
            "🛒 *Cara Membeli:*\n"
            "1\\. Ketuk *🛒 Belanja Sekarang*\n"
            "2\\. Pilih produk yang diinginkan\n"
            "3\\. Tentukan jumlah pembelian\n"
            "4\\. Ketuk *✅ BELI SEKARANG*\n"
            "5\\. Scan QR Code QRIS yang muncul\n"
            "6\\. Produk otomatis dikirim setelah bayar ✅\n\n"
            "📋 *Ketentuan:*\n"
            "├ ⏱️ Pembayaran berlaku *5 menit*\n"
            "├ 🔒 Produk _Non\\-refundable_\n"
            "└ 📞 Hubungi admin jika ada kendala\n\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            "🏪 *" + STORE_NAME + "* — Siap melayani\\! 💚",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="main_menu")
            ]]))
        return

    # ── Promo ─────────────────────────────────────────
    if d == "menu_promo":
        await safe_edit(q,
            "📢 *PROMO & INFO TOKO*\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
            "🔥 *Keunggulan " + STORE_NAME + ":*\n"
            "├ ⚡ Pengiriman otomatis & instan\n"
            "├ 🔒 Produk bergaransi aktif\n"
            "├ 💰 Harga terjangkau & bersaing\n"
            "└ 🛡️ Transaksi aman via QRIS\n\n"
            "📣 *Info & Update Terbaru:*\n"
            "_Pantau terus bot ini untuk promo\\!_\n\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            "🏪 *" + STORE_NAME + "* 💚",
            InlineKeyboardMarkup([[
                InlineKeyboardButton("🛒 Belanja Sekarang", callback_data="menu_purchase"),
                InlineKeyboardButton("🔙 Kembali", callback_data="main_menu"),
            ]]))
        return

    # ── Detail Produk ─────────────────────────────────
    if d.startswith("prod_"):
        pid  = int(d.split("_")[1])
        prod = get_product(pid)
        if not prod:
            return
        await safe_edit(q, fmt_product_detail(prod, 1),
                        kb_product(pid, 1, prod[4], uid in ADMIN_IDS))
        return

    # ── Qty buttons ───────────────────────────────────
    if d.startswith("qty_"):
        parts  = d.split("_")
        action = parts[1]
        if action == "dec":
            pid, cur = int(parts[2]), int(parts[3])
            qty = max(1, cur - 1)
        elif action == "inc":
            pid, cur = int(parts[2]), int(parts[3])
            prod = get_product(pid)
            qty  = min(cur + 1, prod[4]) if prod else cur
        else:
            pid, qty = int(parts[2]), int(parts[3])
        prod = get_product(pid)
        if not prod:
            return
        qty = max(1, min(qty, prod[4]))
        await safe_edit(q, fmt_product_detail(prod, qty),
                        kb_product(pid, qty, prod[4], uid in ADMIN_IDS))
        return

    # ── Beli Normal ───────────────────────────────────
    if d.startswith("buy_"):
        parts = d.split("_")
        await _process_buy(q, ctx, uid, int(parts[1]), int(parts[2]), bypass=False)
        return

    # ── Bypass Admin ──────────────────────────────────
    if d.startswith("bypass_"):
        if uid not in ADMIN_IDS:
            await q.answer("❌ Hanya admin!", show_alert=True)
            return
        parts = d.split("_")
        await _process_buy(q, ctx, uid, int(parts[1]), int(parts[2]), bypass=True)
        return

    # ══════════════════════════════════════════════════
    #              ADMIN PANEL CALLBACKS
    # ══════════════════════════════════════════════════
    if d.startswith("adm_") and uid not in ADMIN_IDS:
        await q.answer("❌ Bukan admin!", show_alert=True)
        return

    # ── Panel utama ───────────────────────────────────
    if d == "adm_panel":
        prods, sales, users, rev, stock = get_stats()
        await safe_edit(q,
            "⚙️ *PANEL ADMIN*\n━━━━━━━━━━━━━━━━\n"
            "📦 Produk: `{}`  |  🗃️ Stok: `{}`\n"
            "💰 Penjualan: `{}` transaksi\n"
            "👥 Pengguna: `{}`\n"
            "💵 Pendapatan: `{}`\n"
            "━━━━━━━━━━━━━━━━\nPilih menu:".format(prods, stock, sales, users, rp(rev)),
            kb_admin_main())
        return

    if d == "adm_cancel":
        _clear(ctx)
        # Jangan hapus buffer saat cancel — user mungkin mau lanjut add stok
        prods, sales, users, rev, stock = get_stats()
        buf_info = "  |  🗂️ Buffer: `{}` item".format(len(_buf(ctx))) if _buf(ctx) else ""
        await safe_edit(q,
            "⚙️ *PANEL ADMIN*\n━━━━━━━━━━━━━━━━\n"
            "📦 Produk: `{}`  |  🗃️ Stok: `{}`{}\n"
            "━━━━━━━━━━━━━━━━\nPilih menu:".format(prods, stock, buf_info),
            kb_admin_main())
        return

    # ── Tambah Produk ─────────────────────────────────
    if d == "adm_add_prod":
        ctx.user_data["state"] = S_PROD_NAME
        _clear_buf(ctx)  # Bersihkan buffer agar tidak bocor ke produk baru
        await safe_edit(q, "📝 *Tambah Produk Baru*\n\nMasukkan *nama produk*:",
                        kb_cancel_admin())
        return

    # ── Daftar & Kelola Produk ────────────────────────
    if d == "adm_list_prod":
        products = get_all_products()
        if not products:
            await safe_edit(q, "❌ Belum ada produk.", kb_back_admin())
            return
        rows = [
            [InlineKeyboardButton("📦 {}  [{} stok]".format(name, stok),
                                  callback_data="adm_manage_{}".format(pid))]
            for pid, name, price, stok in products
        ]
        rows.append([InlineKeyboardButton("🔙 Kembali", callback_data="adm_panel")])
        await safe_edit(q, "📋 *Daftar Produk*\nPilih produk untuk dikelola:",
                        InlineKeyboardMarkup(rows))
        return

    if d.startswith("adm_manage_"):
        pid  = int(d.split("_")[2])
        prod = get_product(pid)
        if not prod:
            return
        pid_, name, price, desc, stok = prod
        c = db()
        total_sold = c.execute(
            "SELECT COUNT(*) FROM stock_items WHERE product_id=? AND is_sold=1", (pid,)
        ).fetchone()[0]
        c.close()
        # Tampilkan jumlah buffer jika ada
        buf = _buf(ctx)
        buf_info = "  |  🗂️ Buffer: `{}` item".format(len(buf)) if buf else ""
        await safe_edit(q,
            "📦 *{}*\n━━━━━━━━━━━━━━━━\n"
            "💰 Harga: `{}`\n"
            "✅ Stok Tersedia: `{}`{}\n"
            "📊 Total Terjual: `{}`\n"
            "━━━━━━━━━━━━━━━━\n"
            "📋 Deskripsi:\n{}".format(
                esc(name), rp(price), stok, buf_info, total_sold,
                esc(desc) if desc else "_belum ada_"
            ),
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📦 Tambah Stok dari Buffer",
                                      callback_data="adm_stk_{}".format(pid))],
                [InlineKeyboardButton("✏️ Edit Nama",       callback_data="adm_ename_{}".format(pid))],
                [InlineKeyboardButton("💰 Edit Harga",      callback_data="adm_eprice_{}".format(pid))],
                [InlineKeyboardButton("📋 Edit Deskripsi",  callback_data="adm_edesc_{}".format(pid))],
                [InlineKeyboardButton("🗑️ Hapus Produk",    callback_data="adm_delask_{}".format(pid))],
                [InlineKeyboardButton("🔙 Kembali",         callback_data="adm_list_prod")],
            ]))
        return

    # ── Tambah Stok dari buffer ke produk tertentu ────
    if d.startswith("adm_stk_"):
        pid  = int(d.split("_")[2])
        prod = get_product(pid)
        if not prod:
            return
        buf = _buf(ctx)
        if not buf:
            await safe_edit(q,
                "📦 *Tambah Stok: {}*\n\n"
                "❌ Buffer masih kosong!\n\n"
                "*Cara isi buffer:*\n"
                "1. Tutup menu ini\n"
                "2. Kirim pesan satu per satu ke bot\n"
                "   _setiap pesan = 1 item stok_\n"
                "3. Ketik /add atau buka menu ini lagi\n\n"
                "*Contoh isi pesan:*\n"
                "```\n"
                "VPS Tencent Services (TC)\n"
                "root@43.156.150.198 | pass: yha123\n"
                "====================\n"
                "Thank you for using our services\n"
                "```".format(esc(prod[1])),
                InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="adm_manage_{}".format(pid))
                ]]))
            return
        # Ada buffer → tampilkan preview + konfirmasi
        lines = [
            "📦 *{}*".format(esc(prod[1])),
            "💰 Harga: `{}`  |  Stok saat ini: `{}`".format(rp(prod[2]), prod[4]),
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            "*Siap tambahkan {} item berikut:*".format(len(buf)),
            "",
        ]
        for i, item in enumerate(buf, 1):
            preview = item.strip().split("\n")[0]
            if len(preview) > 50:
                preview = preview[:50] + "..."
            lines.append("*#{}* `{}`".format(i, esc(preview)))
        await safe_edit(q, "\n".join(lines),
            InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ Tambahkan {} item ke stok!".format(len(buf)),
                    callback_data="adm_cstk_{}".format(pid))],
                [InlineKeyboardButton("🗑️ Hapus Buffer", callback_data="adm_clearbuf")],
                [InlineKeyboardButton("🔙 Kembali", callback_data="adm_manage_{}".format(pid))],
            ]))
        return

    # ── Konfirmasi simpan buffer ke produk ────────────
    if d.startswith("adm_cstk_"):
        pid  = int(d.split("_")[2])
        prod = get_product(pid)
        if not prod:
            return
        buf = list(_buf(ctx))  # Ambil snapshot buffer sebelum clear
        if not buf:
            await q.answer("❌ Buffer kosong! Kirim item dulu.", show_alert=True)
            return
        # Simpan ke DB hanya untuk produk yang dipilih — tidak menyentuh produk lain
        c = db()
        for item in buf:
            c.execute("INSERT INTO stock_items(product_id,content) VALUES(?,?)",
                      (prod[0], item))
        c.commit(); c.close()
        _clear_buf(ctx)  # Bersihkan buffer setelah berhasil disimpan
        prod_updated = get_product(pid)
        await safe_edit(q,
            "✅ *Berhasil tambah {} item stok!*\n"
            "📦 Produk: *{}*\n"
            "🗃️ Stok sekarang: `{}`".format(
                len(buf), esc(prod[1]), prod_updated[4]),
            kb_back_admin())
        # Broadcast notifikasi
        sent = await _broadcast_stock_notif(
            ctx, ctx.bot, uid,
            prod[1], prod[2], prod_updated[4])
        await q.message.reply_text(
            "📢 Notifikasi stok terkirim ke *{}* pengguna.".format(sent),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_admin_main())
        return

    # ── Hapus buffer ──────────────────────────────────
    if d == "adm_clearbuf":
        n = len(_buf(ctx))
        _clear_buf(ctx)
        await safe_edit(q,
            "🗑️ *Buffer dihapus!* ({} item dibuang)\n\n"
            "Kirim ulang item yang ingin ditambahkan,\n"
            "lalu ketik /add.".format(n),
            kb_back_admin())
        return

    # ── Edit Produk ───────────────────────────────────
    if d.startswith("adm_ename_"):
        pid = int(d.split("_")[2])
        ctx.user_data.update({"state": S_EDIT_NAME, "edit_pid": pid})
        await safe_edit(q, "✏️ Masukkan *nama baru* untuk produk:", kb_cancel_admin())
        return

    if d.startswith("adm_eprice_"):
        pid = int(d.split("_")[2])
        ctx.user_data.update({"state": S_EDIT_PRICE, "edit_pid": pid})
        await safe_edit(q, "💰 Masukkan *harga baru* (angka saja, contoh: `33780`):",
                        kb_cancel_admin())
        return

    if d.startswith("adm_edesc_"):
        pid = int(d.split("_")[2])
        ctx.user_data.update({"state": S_EDIT_DESC, "edit_pid": pid})
        await safe_edit(q, "📋 Masukkan *deskripsi baru*:", kb_cancel_admin())
        return

    if d.startswith("adm_delask_"):
        pid  = int(d.split("_")[2])
        prod = get_product(pid)
        await safe_edit(q,
            "⚠️ Yakin hapus produk *{}*?\n\n"
            "Semua stok (`{}` item) juga ikut terhapus!".format(esc(prod[1]), prod[4]),
            InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Ya, Hapus!", callback_data="adm_dodel_{}".format(pid)),
                InlineKeyboardButton("❌ Batal",      callback_data="adm_panel"),
            ]]))
        return

    if d.startswith("adm_dodel_"):
        pid = int(d.split("_")[2])
        c   = db()
        c.execute("DELETE FROM stock_items WHERE product_id=?", (pid,))
        c.execute("DELETE FROM products WHERE id=?", (pid,))
        c.commit(); c.close()
        await safe_edit(q, "✅ Produk berhasil dihapus!", kb_back_admin())
        return

    # ── Kelola Media Start ────────────────────────────
    if d == "adm_photo_menu":
        await safe_edit(q,
            "🖼️ *Kelola Media Start*\nMedia ditampilkan saat pengguna /start:",
            kb_photo_menu())
        return

    if d == "adm_media_del":
        set_setting("start_media_id", "")
        set_setting("start_media_type", "")
        await safe_edit(q, "✅ Media start berhasil dihapus!", kb_back_admin())
        return

    if d == "adm_media_photo":
        ctx.user_data["state"] = S_MEDIA_PHOTO
        await safe_edit(q, "🖼️ Kirim *foto* yang ingin dijadikan media start:",
                        kb_cancel_admin())
        return

    if d == "adm_media_video":
        ctx.user_data["state"] = S_MEDIA_VIDEO
        await safe_edit(q, "🎬 Kirim *video* yang ingin dijadikan media start:",
                        kb_cancel_admin())
        return

    # ── Broadcast ─────────────────────────────────────
    if d == "adm_broadcast_menu":
        await safe_edit(q, "📢 *Broadcast*\n\nPilih jenis broadcast:", kb_broadcast_menu())
        return

    if d == "adm_bc_text":
        ctx.user_data["state"] = S_BC_TEXT
        await safe_edit(q, "📝 Kirim *pesan teks* untuk di-broadcast:\n_(Markdown didukung)_",
                        kb_cancel_admin())
        return

    if d == "adm_bc_photo":
        ctx.user_data["state"] = S_BC_PHOTO
        await safe_edit(q, "🖼️ Kirim *foto* beserta caption untuk di-broadcast:",
                        kb_cancel_admin())
        return

    if d == "adm_bc_video":
        ctx.user_data["state"] = S_BC_VIDEO
        await safe_edit(q, "🎬 Kirim *video* beserta caption untuk di-broadcast:",
                        kb_cancel_admin())
        return

    # ── Statistik ─────────────────────────────────────
    if d == "adm_stats":
        prods, sales, users, rev, stock = get_stats()
        c = db()
        pending = c.execute("SELECT COUNT(*) FROM transactions WHERE status='pending'").fetchone()[0]
        expired = c.execute("SELECT COUNT(*) FROM transactions WHERE status='expired'").fetchone()[0]
        bypass  = c.execute("SELECT COUNT(*) FROM transactions WHERE status='bypass'").fetchone()[0]
        c.close()
        await safe_edit(q,
            "📊 *STATISTIK TOKO*\n━━━━━━━━━━━━━━━━\n"
            "📦 Total Produk: `{}`\n"
            "🗃️ Total Stok Tersedia: `{}`\n"
            "✅ Transaksi Berhasil: `{}`\n"
            "🔓 Bypass Admin: `{}`\n"
            "⏳ Transaksi Pending: `{}`\n"
            "❌ Transaksi Expired: `{}`\n"
            "👥 Total Pengguna: `{}`\n"
            "💵 Total Pendapatan: `{}`\n"
            "━━━━━━━━━━━━━━━━".format(
                prods, stock, sales, bypass, pending, expired, users, rp(rev)),
            kb_back_admin())
        return

    # ── Notifikasi Grup ───────────────────────────────
    if d == "adm_group_menu":
        gid    = get_setting("notif_group_id") or ""
        status = "✅ Aktif: `{}`".format(gid) if gid else "❌ Belum diset"
        await safe_edit(q,
            "🔔 *Notifikasi Grup*\n━━━━━━━━━━━━━━━━\n"
            "Status: {}\n\n"
            "Bot kirim notifikasi ke grup setiap ada:\n"
            "• 🛒 Order berhasil\n"
            "• 📦 Stok baru ditambahkan\n\n"
            "*Cara set:*\n"
            "1. Tambahkan bot ke grup, jadikan *Admin*\n"
            "2. Ketuk *Set Grup* lalu forward pesan dari grup".format(status),
            InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Set Grup Notifikasi",   callback_data="adm_group_set")],
                [InlineKeyboardButton("🗑️ Hapus Grup Notifikasi", callback_data="adm_group_del")],
                [InlineKeyboardButton("🔔 Test Notifikasi",       callback_data="adm_group_test")],
                [InlineKeyboardButton("🔙 Kembali",               callback_data="adm_panel")],
            ]))
        return

    if d == "adm_group_set":
        ctx.user_data["state"] = S_GROUP_ID
        await safe_edit(q,
            "🔔 *Set Grup Notifikasi*\n\n"
            "Kirim *ID grup/channel* kamu.\n\n"
            "*Cara mudah:*\n"
            "Forward salah satu pesan dari grup ke sini,\n"
            "bot otomatis baca ID-nya.\n\n"
            "*Atau ketik ID manual* (contoh: `-1001234567890`)",
            kb_cancel_admin())
        return

    if d == "adm_group_del":
        set_setting("notif_group_id", "")
        await safe_edit(q, "✅ Grup notifikasi berhasil dihapus!", kb_back_admin())
        return

    if d == "adm_group_test":
        gid = get_setting("notif_group_id")
        if not gid:
            await q.answer("❌ Belum ada grup yang di-set!", show_alert=True)
            return
        try:
            await ctx.bot.send_message(
                chat_id=int(gid),
                text="🔔 *TEST NOTIFIKASI* 🔔\n"
                     "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                     "✅ Notifikasi grup berhasil dikonfigurasi!\n"
                     "Bot akan mengirim notifikasi ke sini\n"
                     "setiap ada order masuk & stok baru.\n"
                     "━━━━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.MARKDOWN)
            await q.answer("✅ Test berhasil!", show_alert=True)
        except Exception as e:
            await q.answer("❌ Gagal: {}".format(str(e)[:60]), show_alert=True)
        return

    # ── Toggle Auto-Broadcast Stok ────────────────────
    if d == "adm_toggle_bc":
        current = get_setting("auto_broadcast_stock", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("auto_broadcast_stock", new_val)
        status_txt = "✅ *ON* — Notif stok baru otomatis dikirim ke semua user" if new_val == "on" \
                else "❌ *OFF* — Notif stok baru _tidak_ dikirim ke user"
        await q.answer(
            "Auto-BC sekarang: {}".format("ON" if new_val == "on" else "OFF"),
            show_alert=True)
        # Refresh panel admin agar label tombol update
        prods, sales, users, rev, stock = get_stats()
        await safe_edit(q,
            "⚙️ *PANEL ADMIN*\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            "📦 Produk: `{}`  |  🗃️ Stok: `{}`\n"
            "💰 Penjualan: `{}` transaksi\n"
            "👥 Pengguna: `{}`\n"
            "💵 Pendapatan: `{}`\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            "📢 *Auto-BC Stok:* {}\n\n"
            "Pilih menu:".format(prods, stock, sales, users, rp(rev), status_txt),
            kb_admin_main())
        return


# ══════════════════════════════════════════════
#         PROSES BUY (NORMAL + BYPASS)
# ══════════════════════════════════════════════
async def _process_buy(q, ctx, uid, pid, qty, bypass=False):
    prod = get_product(pid)
    if not prod:
        await q.answer("Produk tidak ditemukan!", show_alert=True)
        return
    pid_, name, price, desc, stok = prod

    if stok < qty:
        await q.answer("⚠️ Stok hanya {}!".format(stok), show_alert=True)
        return

    username = q.from_user.username

    # ── BYPASS: langsung kirim tanpa bayar ───────────
    if bypass:
        items = take_stock(pid, qty)
        if not items:
            await q.message.reply_text("❌ Stok habis saat bypass!")
            return

        txid = "BYPASS{}{}".format(uid, int(datetime.now().timestamp()))
        save_transaction(uid, username, txid, pid, qty, 0)
        update_transaction_status(txid, "bypass")

        # Hapus pesan detail produk
        try:
            await q.message.delete()
        except Exception:
            pass

        for _, content in items:
            card = fmt_delivery_card(uid, username, name, price, qty, txid, content)
            try:
                sent_msg = await ctx.bot.send_message(
                    chat_id=uid, text=card, parse_mode=ParseMode.MARKDOWN)
                try:
                    await ctx.bot.pin_chat_message(
                        chat_id=uid,
                        message_id=sent_msg.message_id,
                        disable_notification=True)
                except Exception:
                    pass
            except Exception as e:
                log.error("bypass delivery: %s", e)
                # Fallback tanpa markdown
                await ctx.bot.send_message(chat_id=uid, text=card)

        grp = fmt_group_order(uid, username, name, price, qty, txid, tipe="🔓 Bypass Admin")
        await notif_to_group_with_receipt(
            ctx.application, uid, username, name, price, qty, txid, tipe="🔓 Bypass Admin")
        return

    # ── NORMAL: buat QRIS ────────────────────────────
    # Hapus pesan detail produk, ganti loading
    try:
        await q.message.delete()
    except Exception:
        pass

    loading = await ctx.bot.send_message(
        chat_id=uid,
        text="⏳ _Membuat QRIS pembayaran..._",
        parse_mode=ParseMode.MARKDOWN)

    order_id = "INV-{}-{}".format(uid, datetime.now().strftime("%Y%m%d%H%M%S"))
    pay = await create_qris(price * qty, order_id)
    if not pay:
        await loading.edit_text("❌ Gagal membuat QRIS. Silakan coba lagi.")
        return

    txid      = pay["order_id"]
    qr_string = pay["qr_string"]    # QRIS string dari Pakasir
    fee       = pay["total_payment"] - pay["amount"]
    total     = pay["total_payment"]
    exp_min   = EXPIRE_SEC // 60

    save_transaction(uid, username, txid, pid, qty, total)
    await loading.delete()

    # Generate QR image dari qr_string
    qris_photo = generate_qris_image(qr_string)

    # Simpan message_id QRIS agar bisa dihapus setelah bayar
    caption_text = fmt_invoice(name, qty, price, fee, total, exp_min, txid)
    if qris_photo:
        qris_msg = await ctx.bot.send_photo(
            chat_id=uid,
            photo=qris_photo,
            caption=caption_text,
            parse_mode=ParseMode.MARKDOWN)
    else:
        # Fallback: kirim teks saja jika generate gambar gagal
        qris_msg = await ctx.bot.send_message(
            chat_id=uid,
            text=caption_text,
            parse_mode=ParseMode.MARKDOWN)

    ctx.application.create_task(
        _poll_payment(ctx.application, uid, username, txid, pid, qty,
                      name, price, EXPIRE_SEC, qris_msg.message_id))


async def _poll_payment(app, user_id, username, txid, pid, qty,
                        prod_name, price, timeout, qris_msg_id=None):
    elapsed = 0
    total_amount = price * qty  # simpan untuk cek API Pakasir
    while elapsed < timeout:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        if await check_qris(txid, total_amount):
            update_transaction_status(txid, "paid")
            # Hapus pesan QRIS setelah pembayaran berhasil
            if qris_msg_id:
                try:
                    await app.bot.delete_message(
                        chat_id=user_id, message_id=qris_msg_id)
                except Exception:
                    pass
            items = take_stock(pid, qty)
            if not items:
                await app.bot.send_message(
                    user_id,
                    "✅ *Pembayaran berhasil!*\n"
                    "⚠️ Stok kosong, segera hubungi admin!",
                    parse_mode=ParseMode.MARKDOWN)
                return
            for _, content in items:
                card = fmt_delivery_card(
                    user_id, username, prod_name, price, qty, txid, content)
                try:
                    sent_msg = await app.bot.send_message(
                        user_id, card, parse_mode=ParseMode.MARKDOWN)
                    try:
                        await app.bot.pin_chat_message(
                            chat_id=user_id,
                            message_id=sent_msg.message_id,
                            disable_notification=True)
                    except Exception:
                        pass
                except Exception as e:
                    log.error("delivery uid=%s: %s", user_id, e)
                    await app.bot.send_message(user_id, card)
            grp = fmt_group_order(user_id, username, prod_name, price, qty, txid)
            await notif_to_group_with_receipt(
                app, user_id, username, prod_name, price, qty, txid, tipe="💳 QRIS")
            return
    update_transaction_status(txid, "expired")
    # Hapus pesan QRIS saat expired
    if qris_msg_id:
        try:
            await app.bot.delete_message(
                chat_id=user_id, message_id=qris_msg_id)
        except Exception:
            pass
    # Kirim notifikasi expired
    await app.bot.send_message(
        user_id, fmt_expired_msg(txid, prod_name),
        parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════
#     ADMIN MESSAGE HANDLER
# ══════════════════════════════════════════════
async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # ── FIX: Abaikan semua pesan dari grup/supergroup/channel ──
    if update.effective_chat.type != "private":
        return

    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return

    msg   = update.message
    state = ctx.user_data.get("state")

    # ══════════════════════════════════════════════
    # BUFFER MODE: admin kirim teks bebas tanpa state
    # → setiap pesan otomatis masuk ke stock_buffer
    # ══════════════════════════════════════════════
    if not state and msg.text and not msg.text.startswith("/"):
        raw = msg.text.strip()
        # Jika teks murni angka negatif (kemungkinan Group ID)
        # jangan masuk buffer — kasih petunjuk
        if re.match(r"^-?\d{5,}$", raw):
            await msg.reply_text(
                "⚠️ Sepertinya itu ID grup/channel.\n\n"
                "Untuk set notifikasi grup, buka dulu:\n"
                "*Panel Admin → 🔔 Notifikasi Grup → Set Grup*\n\n"
                "Lalu kirim ID ini lagi.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔔 Buka Notifikasi Grup",
                        callback_data="adm_group_menu")
                ]])
            )
            return
        buf = _buf(ctx)
        buf.append(msg.text)
        total = len(buf)
        preview = msg.text.strip().split("\n")[0]
        if len(preview) > 60:
            preview = preview[:60] + "..."
        await msg.reply_text(
            "📥 *Item #{} tersimpan ke buffer!*\n\n"
            "`{}`\n\n"
            "_Buffer: {} item. Kirim lagi atau ketik /add_".format(
                total, esc(preview), total),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "📦 /add — Simpan {} item ke produk".format(total),
                    callback_data="adm_show_buf")],
                [InlineKeyboardButton(
                    "🗑️ Hapus Buffer ({} item)".format(total),
                    callback_data="adm_clearbuf")],
            ])
        )
        return

    if not state:
        return

    # ── Tambah Produk: Nama ───────────────────────────
    if state == S_PROD_NAME:
        ctx.user_data.update({"state": S_PROD_PRICE, "p_name": msg.text})
        await msg.reply_text(
            "✅ Nama: *{}*\n\n💰 Masukkan *harga* (contoh: `33780`):".format(esc(msg.text)),
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_cancel_admin())
        return

    # ── Tambah Produk: Harga ──────────────────────────
    if state == S_PROD_PRICE:
        try:
            price = int(msg.text.replace(".", "").replace(",", ""))
            ctx.user_data.update({"state": S_PROD_DESC, "p_price": price})
            await msg.reply_text(
                "✅ Harga: *{}*\n\n📋 Masukkan *deskripsi produk*:".format(rp(price)),
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_cancel_admin())
        except ValueError:
            await msg.reply_text(
                "❌ Harga harus angka! Contoh: `33780`",
                parse_mode=ParseMode.MARKDOWN)
        return

    # ── Tambah Produk: Deskripsi → Simpan ────────────
    if state == S_PROD_DESC:
        name  = ctx.user_data["p_name"]
        price = ctx.user_data["p_price"]
        c = db()
        c.execute("INSERT INTO products(name,description,price) VALUES(?,?,?)",
                  (name, msg.text, price))
        c.commit(); c.close()
        _clear(ctx)
        await msg.reply_text(
            "✅ *Produk berhasil ditambahkan!*\n\n"
            "📦 *Nama:* {}\n"
            "💰 *Harga:* `{}`\n"
            "📋 *Deskripsi:* {}\n\n"
            "_Untuk tambah stok: kirim pesan satu per satu lalu ketik /add_".format(
                esc(name), rp(price),
                esc(msg.text[:150]) + ("..." if len(msg.text) > 150 else "")),
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_main())
        return

    # ── Edit Nama ─────────────────────────────────────
    if state == S_EDIT_NAME:
        pid = ctx.user_data["edit_pid"]
        c = db()
        c.execute("UPDATE products SET name=? WHERE id=?", (msg.text, pid))
        c.commit(); c.close()
        _clear(ctx)
        await msg.reply_text(
            "✅ Nama produk diubah ke *{}*!".format(esc(msg.text)),
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_main())
        return

    # ── Edit Harga ────────────────────────────────────
    if state == S_EDIT_PRICE:
        try:
            price = int(msg.text.replace(".", "").replace(",", ""))
            pid   = ctx.user_data["edit_pid"]
            c = db()
            c.execute("UPDATE products SET price=? WHERE id=?", (price, pid))
            c.commit(); c.close()
            _clear(ctx)
            await msg.reply_text(
                "✅ Harga diubah ke *{}*!".format(rp(price)),
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_main())
        except ValueError:
            await msg.reply_text("❌ Harga harus angka!", reply_markup=kb_cancel_admin())
        return

    # ── Edit Deskripsi ────────────────────────────────
    if state == S_EDIT_DESC:
        pid = ctx.user_data["edit_pid"]
        c = db()
        c.execute("UPDATE products SET description=? WHERE id=?", (msg.text, pid))
        c.commit(); c.close()
        _clear(ctx)
        await msg.reply_text("✅ Deskripsi berhasil diperbarui!",
                             reply_markup=kb_admin_main())
        return

    # ── Upload Media Start ────────────────────────────
    if state == S_MEDIA_PHOTO:
        if msg.photo:
            set_setting("start_media_id",   msg.photo[-1].file_id)
            set_setting("start_media_type", "photo")
            _clear(ctx)
            await msg.reply_text("✅ Foto start berhasil disimpan!", reply_markup=kb_admin_main())
        else:
            await msg.reply_text("⚠️ Kirim *foto* ya!", parse_mode=ParseMode.MARKDOWN)
        return

    if state == S_MEDIA_VIDEO:
        if msg.video:
            set_setting("start_media_id",   msg.video.file_id)
            set_setting("start_media_type", "video")
            _clear(ctx)
            await msg.reply_text("✅ Video start berhasil disimpan!", reply_markup=kb_admin_main())
        else:
            await msg.reply_text("⚠️ Kirim *video* ya!", parse_mode=ParseMode.MARKDOWN)
        return

    # ── Broadcast Teks ────────────────────────────────
    if state == S_BC_TEXT:
        text_bc = msg.text or ""
        if not text_bc:
            await msg.reply_text("⚠️ Pesan kosong!")
            return
        all_uids = all_user_ids()
        wait_msg = await msg.reply_text("📢 Mengirim ke {} pengguna...".format(len(all_uids)))
        sent = 0
        for u in all_uids:
            try:
                await ctx.bot.send_message(u, text_bc, parse_mode=ParseMode.MARKDOWN)
                sent += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass
        _clear(ctx)
        await wait_msg.edit_text(
            "✅ Broadcast selesai! Terkirim ke *{}* pengguna.".format(sent),
            parse_mode=ParseMode.MARKDOWN)
        await msg.reply_text("Kembali ke panel:", reply_markup=kb_admin_main())
        return

    # ── Broadcast Foto ────────────────────────────────
    if state == S_BC_PHOTO:
        if not msg.photo:
            await msg.reply_text("⚠️ Kirim *foto* ya!", parse_mode=ParseMode.MARKDOWN)
            return
        photo_id = msg.photo[-1].file_id
        caption  = msg.caption or ""
        all_uids = all_user_ids()
        wait_msg = await msg.reply_text("📢 Mengirim foto ke {} pengguna...".format(len(all_uids)))
        sent = 0
        for u in all_uids:
            try:
                await ctx.bot.send_photo(u, photo=photo_id, caption=caption,
                                         parse_mode=ParseMode.MARKDOWN)
                sent += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass
        _clear(ctx)
        await wait_msg.edit_text(
            "✅ Broadcast foto selesai! Terkirim ke *{}* pengguna.".format(sent),
            parse_mode=ParseMode.MARKDOWN)
        await msg.reply_text("Kembali ke panel:", reply_markup=kb_admin_main())
        return

    # ── Broadcast Video ───────────────────────────────
    if state == S_BC_VIDEO:
        if not msg.video:
            await msg.reply_text("⚠️ Kirim *video* ya!", parse_mode=ParseMode.MARKDOWN)
            return
        video_id = msg.video.file_id
        caption  = msg.caption or ""
        all_uids = all_user_ids()
        wait_msg = await msg.reply_text("📢 Mengirim video ke {} pengguna...".format(len(all_uids)))
        sent = 0
        for u in all_uids:
            try:
                await ctx.bot.send_video(u, video=video_id, caption=caption,
                                         parse_mode=ParseMode.MARKDOWN)
                sent += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass
        _clear(ctx)
        await wait_msg.edit_text(
            "✅ Broadcast video selesai! Terkirim ke *{}* pengguna.".format(sent),
            parse_mode=ParseMode.MARKDOWN)
        await msg.reply_text("Kembali ke panel:", reply_markup=kb_admin_main())
        return

    # ── Set Group ID ──────────────────────────────────
    if state == S_GROUP_ID:
        gid   = None
        gname = None
        # Prioritas 1: forward dari channel/grup resmi
        if msg.forward_from_chat:
            gid   = str(msg.forward_from_chat.id)
            gname = msg.forward_from_chat.title or gid
        elif msg.text:
            raw = msg.text.strip()
            # Prioritas 2: teks murni angka = ID langsung
            if re.match(r"^-?\d+$", raw):
                gid   = raw
                gname = raw
            else:
                # Prioritas 3: extract angka negatif dari kalimat apapun
                # contoh: "This chat's ID is: -1003482263196"
                m = re.search(r"(-\d{7,})", raw)
                if m:
                    gid   = m.group(1)
                    gname = gid
        if not gid:
            await msg.reply_text(
                "⚠️ Tidak bisa baca ID!\n\n"
                "*Cara 1 (paling mudah):*\n"
                "Ketik langsung: `/setgroup -1003482263196`\n\n"
                "*Cara 2:*\n"
                "Kirim teks angka ID saja\n"
                "contoh: `-1003482263196`\n\n"
                "*Cara 3:*\n"
                "Forward pesan dari grup ke sini",
                parse_mode=ParseMode.MARKDOWN)
            return
        set_setting("notif_group_id", gid)
        _clear(ctx)
        try:
            await ctx.bot.send_message(
                chat_id=int(gid),
                text="🔔 *NOTIFIKASI GRUP AKTIF* 🔔\n"
                     "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                     "✅ Grup ini berhasil dikonfigurasi!\n"
                     "Notifikasi order & stok baru akan\n"
                     "dikirim ke sini secara otomatis.\n"
                     "━━━━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=ParseMode.MARKDOWN)
            await msg.reply_text(
                "✅ *Grup notifikasi berhasil diset!*\n"
                "ID/Nama: `{}`\n\n"
                "Test notifikasi sudah dikirim ke grup.".format(gname),
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_main())
        except Exception as e:
            await msg.reply_text(
                "⚠️ ID tersimpan tapi gagal kirim test.\n`{}`\n\n"
                "Pastikan bot sudah jadi *Admin* di grup!".format(str(e)[:100]),
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_admin_main())
        return


# ── Callback: tombol "Simpan buffer ke produk" dari pesan buffer ──
async def callback_show_buf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Shortcut dari tombol di pesan buffer → jalankan logika /add."""
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()

    if uid not in ADMIN_IDS:
        return

    products = get_all_products()
    buf      = _buf(ctx)

    if not buf:
        await safe_edit(q, "❌ Buffer kosong!", kb_back_admin())
        return

    lines = [
        "*Buffer Stok: {} item siap ditambahkan*".format(len(buf)),
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for i, item in enumerate(buf, 1):
        preview = item.strip().split("\n")[0]
        if len(preview) > 50:
            preview = preview[:50] + "..."
        lines.append("*#{}* `{}`".format(i, esc(preview)))
    lines += ["", "━━━━━━━━━━━━━━━━━━━━━━━━━", "📦 *Pilih produk tujuan:*"]

    rows = []
    for pid, name, price, stok in products:
        rows.append([InlineKeyboardButton(
            "[{}] {}  (stok: {})".format(pid, name, stok),
            callback_data="adm_cstk_{}".format(pid)
        )])
    rows.append([InlineKeyboardButton("🗑️ Hapus Buffer ({} item)".format(len(buf)),
                                      callback_data="adm_clearbuf")])
    rows.append([InlineKeyboardButton("❌ Batal", callback_data="adm_cancel")])

    await safe_edit(q, "\n".join(lines), InlineKeyboardMarkup(rows))


# ══════════════════════════════════════════════
#                    MAIN
# ══════════════════════════════════════════════
def main():
    init_db()
    if BOT_TOKEN == "ISI_TOKEN_BOTFATHER_DISINI":
        print("=" * 55)
        print("⚠️  Isi BOT_TOKEN di baris 22 bot.py!")
        print("=" * 55)
        return

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)   # Handle update paralel = lebih cepat
        .connect_timeout(10)
        .read_timeout(10)
        .write_timeout(10)
        .build()
    )

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("add",      cmd_add))
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CallbackQueryHandler(callback_show_buf, pattern="^adm_show_buf$"))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.FORWARDED) & ~filters.COMMAND,
        message_handler))

    log.info("🤖 " + STORE_NAME + " BOT aktif!")
    app.run_polling(drop_pending_updates=True, pool_timeout=30)


if __name__ == "__main__":
    main()
