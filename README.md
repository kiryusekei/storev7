<div align="center">

# 🏪 AUTOPAY STORE BOT
### Telegram Bot Toko Digital Otomatis dengan QRIS Payment

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python)](https://python.org)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20API-2CA5E0?style=for-the-badge&logo=telegram)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-Private-red?style=for-the-badge)](#)
[![Developer](https://img.shields.io/badge/Developer-NEXUSDEV-green?style=for-the-badge)](https://t.me/nexusweb_dev)

**Bot toko otomatis berbasis Telegram dengan fitur pembayaran QRIS, manajemen stok, nota digital, dan notifikasi grup.**

[📱 Kontak Developer](https://t.me/nexusweb_dev) • [🐛 Laporkan Bug](https://t.me/nexusweb_dev)

</div>

---

## ✨ Fitur Utama

| Fitur | Keterangan |
|-------|------------|
| 💳 **Pembayaran QRIS** | Generate QR otomatis, cek status real-time |
| 📦 **Manajemen Stok** | Upload stok via buffer, pisah per produk |
| 🧾 **Nota Digital** | Generate gambar struk HD putih/hitam otomatis |
| 📢 **Notif Grup** | Kirim notifikasi order + foto nota ke grup |
| 📊 **Dashboard Admin** | Statistik penjualan, user, stok |
| 📣 **Broadcast** | Kirim pesan/foto/video ke semua member |
| ⚡ **Pengiriman Instan** | Produk otomatis terkirim setelah bayar |
| 🔒 **Multi Admin** | Dukung lebih dari 1 admin |

---

## 📋 Persyaratan

Sebelum instalasi, pastikan VPS kamu memenuhi syarat berikut:

- **OS:** Ubuntu 20.04 / 22.04 / Debian 11+ (64-bit)
- **RAM:** Minimal 512 MB
- **Python:** 3.10 atau lebih baru
- **Akses:** Root atau sudo
- **Koneksi:** Internet aktif

---

## 🚀 Instalasi Otomatis (Recommended)

### Langkah 1 — Siapkan Data yang Dibutuhkan

Sebelum mulai, siapkan informasi berikut:

```
✅ Token Bot Telegram    → dari @BotFather
✅ Telegram ID kamu      → cek via @userinfobot
✅ API Key PaymentKu     → dari nexusdev (paymentku.nexusdev.my.id)
✅ Nama Toko             → contoh: Toko VPN Murah
✅ Website / Link        → contoh: t.me/username (opsional)
```

> 💡 **Cara dapat Bot Token:** Chat [@BotFather](https://t.me/BotFather) → `/newbot` → ikuti instruksi → copy token

> 💡 **Cara dapat Telegram ID:** Chat [@userinfobot](https://t.me/userinfobot) → ID kamu akan tampil

> 💡 **Cara dapat API Key PaymentKu:** Hubungi [nexusdev](https://t.me/nexusweb_dev) untuk mendapatkan API Key PaymentKu

---

### Langkah 2 — Jalankan Script Installer

Login ke VPS via SSH, lalu jalankan perintah berikut:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kiryusekei/storev7/main/setup.sh)
```

> ⚠️ Ganti `YOUR_USERNAME/YOUR_REPO` dengan username dan nama repo GitHub kamu.

---

### Langkah 3 — Isi Konfigurasi

Installer akan menampilkan form interaktif. Isi satu per satu:

```
🤖 Bot Token (dari @BotFather): 123456789:AAF...
👤 Admin Telegram ID (angka): 987654321
💳 API Key PaymentKu: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
🏪 Nama Toko (tampil di bot & nota): Toko VPN Ku
🔗 Website / Link toko [default]: t.me/tokoku
```

Tekan **Enter** untuk konfirmasi, lalu installer akan:

1. ✅ Menginstall semua dependensi Python
2. ✅ Mendownload bot dari GitHub
3. ✅ Menulis konfigurasi ke file bot
4. ✅ Membuat dan mengaktifkan systemd service
5. ✅ Menjalankan bot secara otomatis

---

### Langkah 4 — Verifikasi Bot Berjalan

Setelah instalasi selesai, cek status bot:

```bash
systemctl status autoorder-bot
```

Harusnya tampil `active (running)` ✅

Lihat log real-time:
```bash
journalctl -u autoorder-bot -f
```

---

## 📖 Instalasi Manual (Opsional)

Jika ingin install secara manual tanpa script:

```bash
# 1. Clone repo
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

# 2. Install dependensi
pip3 install python-telegram-bot==21.3 aiohttp pillow qrcode

# 3. Edit konfigurasi
nano autoorder.py
# Ubah: BOT_TOKEN, ADMIN_IDS, API_KEY, STORE_NAME, WEBSITE

# 4. Jalankan
python3 autoorder.py
```

---

## ⚙️ Konfigurasi

Semua konfigurasi ada di bagian atas file `autoorder.py`:

```python
# ══════════════════════════════════════════════
#  ⚙️  KONFIGURASI — EDIT HANYA DI SINI SAJA
# ══════════════════════════════════════════════
BOT_TOKEN        = "TOKEN_BOT_KAMU"
ADMIN_IDS        = [123456789]           # bisa lebih dari 1: [111, 222]
API_KEY          = "API_KEY_PAYMENTKU"   # dari nexusdev (paymentku.nexusdev.my.id)
STORE_NAME       = "Nama Toko Kamu"      # tampil di bot & nota
WEBSITE          = "link-toko.com"       # tampil di footer nota
```

---

## 🛠️ Perintah Bot

### 👤 Untuk Pembeli (User)
| Perintah | Fungsi |
|----------|--------|
| `/start` | Buka toko & lihat menu utama |

### 🔑 Untuk Admin
| Perintah | Fungsi |
|----------|--------|
| `/start` | Buka panel admin |
| `/add`   | Upload stok ke produk |
| `/setgroup -1001234567` | Set grup notifikasi order |

---

## 📱 Panduan Admin

### Cara Tambah Produk
1. Masuk panel admin → **📦 Kelola Produk** → **➕ Tambah Produk**
2. Isi nama produk, harga, deskripsi
3. Produk otomatis tersedia di toko

### Cara Upload Stok
1. Kirim isi stok ke bot (format bebas, 1 item per baris)
2. Bot akan masuk mode buffer, konfirmasi via tombol
3. Pilih produk tujuan → stok tersimpan

### Cara Set Grup Notifikasi
```
/setgroup -1001234567890
```
Pastikan bot sudah jadi **Admin** di grup tersebut.

---

## 🔧 Manajemen Service

```bash
# Cek status bot
systemctl status autoorder-bot

# Restart bot
systemctl restart autoorder-bot

# Stop bot
systemctl stop autoorder-bot

# Start bot
systemctl start autoorder-bot

# Lihat log langsung
journalctl -u autoorder-bot -f

# Lihat 100 baris log terakhir
journalctl -u autoorder-bot -n 100
```

---

## 🗂️ Struktur File

```
YOUR_REPO/
├── autoorder.py     # File utama bot
├── setup.sh         # Script installer otomatis
├── README.md        # Dokumentasi ini
└── store.db         # Database SQLite (dibuat otomatis)
```

---

## ❓ Troubleshooting

**Bot tidak aktif setelah install?**
```bash
journalctl -u autoorder-bot -n 50
# Cek error, biasanya token salah atau dependensi gagal install
```

**Error `ModuleNotFoundError`?**
```bash
pip3 install python-telegram-bot==21.3 aiohttp pillow qrcode --break-system-packages
systemctl restart autoorder-bot
```

**Bot tidak menerima pesan?**
- Pastikan token bot benar
- Pastikan bot tidak diblokir

**Nota gambar tidak muncul?**
```bash
apt-get install -y fonts-dejavu-core fonts-dejavu-extra
systemctl restart autoorder-bot
```

**QRIS gambar tidak muncul di Telegram?**
```bash
pip3 install qrcode pillow --break-system-packages
systemctl restart autoorder-bot
```
> Library `qrcode` dibutuhkan untuk generate gambar QR dari string QRIS PaymentKu.

**Notifikasi grup tidak masuk?**
- Pastikan bot sudah jadi **Admin** di grup
- Cek ID grup sudah benar dengan `/setgroup`

---

## 📞 Kontak & Support

<div align="center">

| Platform | Link |
|----------|------|
| 💬 **Telegram Developer** | [@nexusweb_dev](https://t.me/nexusweb_dev) |

> ⏰ Waktu respon: Senin–Sabtu, 08.00–22.00 WIB

</div>

---

## 📄 Lisensi

Proyek ini merupakan karya privat milik **NEXUSDEV**.  
Dilarang mendistribusikan, menjual, atau mengklaim sebagai karya sendiri tanpa izin dari pengembang.

---

<div align="center">

**Dibuat dengan ❤️ oleh [NEXUSDEV](https://t.me/nexusweb_dev)**

*"Solusi digital terpercaya untuk kebutuhan VPN dan otomasi Telegram."*

</div>
