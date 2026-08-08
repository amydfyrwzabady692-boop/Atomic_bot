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

Fresh Postgres volumes run `db/init.sql` once. Existing volumes keep user balances, wallets, and orders; schema upgrades run idempotently via `ensure_admin_schema()` on bot startup.

Admin panel and deployment guide: [ADMIN_PANEL.md](ADMIN_PANEL.md)
