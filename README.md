# Bot PPOB H2H.id

Bot Telegram PPOB pribadi untuk melakukan transaksi produk H2H.id seperti:
- Pulsa & Paket Data
- Token PLN & Tagihan Listrik
- E-Money (GoPay, OVO, DANA, ShopeePay)
- Games & Voucher
- TV & Streaming
- BPJS & Tagihan lainnya

## Install di VPS

```bash
bash <(curl -s -H "Authorization: token TOKEN_GITHUB" \
  https://raw.githubusercontent.com/dwiexe/h2hbot/main/deploy.sh)
```
```
bash <(curl -s https://raw.githubusercontent.com/dwiexe/h2hbot/main/deploy.sh)
```
```
sudo systemctl restart h2hbot
```
## Persyaratan VPS
- Ubuntu 20 / 22 / 24
- RAM minimal 1GB
- Python 3.10+
- Git

## Fitur
- Transaksi PPOB H2H.id
- Sinkronisasi produk otomatis
- Menu kategori → operator → varian → produk
- Format teks + tombol angka
- Webhook notifikasi instan
- Riwayat transaksi
- Mode private (hanya owner)
- Auto restart service
