# Atomic Bot

Zarinpal callbacks go through the shared reverse proxy (or optional Caddy profile) to the bot.

Public URLs:
- https://botatomic.atomicshop.ir/payment/callback
- https://botatomic.atomicshop.ir/payment/wallet-callback
- https://botatomic.atomicshop.ir/health
- https://botatomic.atomicshop.ir/ready

DNS: create an A record for `botatomic` pointing to your VPS IP (or use the domain already wired in nginx).

Then:

```bash
docker compose up -d --build
docker compose ps
curl -fsS https://botatomic.atomicshop.ir/ready
```

برای فعال‌شدن سفارش‌های «جم با اطلاعات»، یک کلید Fernet پایدار در `.env`
قرار دهید (پس از ثبت سفارش‌ها آن را بدون برنامه مهاجرت تغییر ندهید):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# خروجی را در .env بگذارید:
ACCOUNT_CREDENTIALS_KEY=...
```

اطلاعات ورود فقط رمزگذاری‌شده نگهداری می‌شود و با تکمیل، لغو، انقضا یا اعلام
نقص اطلاعات حذف می‌شود. OTP و Recovery Code نباید داخل بات ارسال شوند.

Fresh Postgres volumes run `db/init.sql` once. Existing volumes keep user balances, wallets, and orders; schema upgrades run idempotently via `ensure_admin_schema()` on bot startup.

Admin panel and deployment guide: [ADMIN_PANEL.md](ADMIN_PANEL.md)
