"""دسترسی به دیتابیس PostgreSQL مشترک با سایت Atomic Shop (accshop).

جدول‌ها و ستون‌ها PascalCase هستند و داخل گیومه قرار می‌گیرند.
"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import g2bulk

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

_CONN = {
    'host': os.getenv('DB_HOST', os.getenv('DB_SERVER', 'localhost')),
    'port': os.getenv('DB_PORT', '5432'),
    'dbname': os.getenv('DB_NAME', 'accshop'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
}


def get_conn():
    return psycopg.connect(**_CONN)


# ─── Users ──────────────────────────────────────────────────────────────────────
def get_or_create_user(telegram_id, first_name='', last_name='', username=''):
    tg = str(telegram_id)
    uname_tg = (username or '').lstrip('@').strip()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "Id" FROM "Users" WHERE "TelegramId"=%s', (tg,))
        row = cur.fetchone()
        if row:
            # همیشه نام و آیدی تلگرام را تازه نگه دار
            cur.execute(
                'UPDATE "Users" SET "FirstName"=%s, "LastName"=%s, "TelegramUsername"=%s '
                'WHERE "Id"=%s',
                (first_name or '', last_name or '', uname_tg, row[0]),
            )
            conn.commit()
            return row[0], False

        uname = uname_tg or f"tg_{tg}"
        cur.execute('SELECT 1 FROM "Users" WHERE "Username"=%s', (uname,))
        if cur.fetchone():
            uname = f"tg_{tg}"
        email = f"tg_{tg}@telegram.bot"

        cur.execute(
            'INSERT INTO "Users" '
            '("password", "Username", "Email", "FirstName", "LastName", '
            '"IsStaff", "IsActive", "IsSuperUser", "TelegramId", "TelegramUsername", "DateJoined") '
            'VALUES (%s, %s, %s, %s, %s, false, true, false, %s, %s, now()) '
            'RETURNING "Id"',
            ('', uname, email, first_name or '', last_name or '', tg, uname_tg)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id, True


# ─── Gem Packages ───────────────────────────────────────────────────────────────
# خروجی get_gems/get_gem:
# Id, Title, Amount, BonusAmount, Price, OldPrice, PlanType, PurchaseType,
# AutoDeliver, G2BulkCatalogueName, Stock, IsAvailable

_GEM_COLS = (
    '"Id", "Title", "Amount", "BonusAmount", "Price", "OldPrice", '
    '"PlanType", "PurchaseType", "AutoDeliver", "G2BulkCatalogueName", '
    '"Stock", "IsAvailable"'
)


def get_gems_by_id():
    """بسته‌های جم با آیدی — مثل سایت (once + ME amounts)."""
    amounts = tuple(g2bulk.G2BULK_ME_AMOUNTS)
    placeholders = ','.join(['%s'] * len(amounts))
    sql = (
        f'SELECT {_GEM_COLS} FROM "GemPackages" '
        'WHERE "IsActive"=true '
        'AND "PurchaseType"=\'by_id\' '
        'AND "PlanType"=\'once\' '
        f'AND "Amount" IN ({placeholders}) '
        'ORDER BY "Price"'
    )
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, amounts)
            return cur.fetchall()
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception('get_gems_by_id failed: %s', e)
        return []


def get_gem(pk):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_GEM_COLS} FROM "GemPackages" '
            'WHERE "Id"=%s AND "IsActive"=true',
            (pk,),
        )
        return cur.fetchone()


def decrement_gem_stock(gem_package_id, qty=1):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "GemPackages" SET "Stock" = GREATEST("Stock" - %s, 0) '
            'WHERE "Id"=%s AND COALESCE("AutoDeliver", false)=false',
            (qty, gem_package_id),
        )
        conn.commit()


# ─── Orders ─────────────────────────────────────────────────────────────────────
def create_order(user_db_id, total, telegram_id='', full_name='', phone='',
                 payment_method='zarinpal'):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "Orders" '
            '("UserId", "FullName", "Email", "Phone", "TelegramId", "TotalAmount", '
            '"DiscountAmount", "PaymentMethod", "Status", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, now()) '
            'RETURNING "Id"',
            (
                user_db_id,
                full_name or 'کاربر تلگرام',
                f"tg_{telegram_id or user_db_id}@telegram.bot",
                phone or '',
                str(telegram_id),
                total,
                payment_method,
                'pending',
            ),
        )
        order_id = cur.fetchone()[0]
        conn.commit()
        return order_id


def set_order_authority(order_id, authority, payment_method='zarinpal'):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "Orders" SET "PaymentAuthority"=%s, "PaymentMethod"=%s WHERE "Id"=%s',
            (authority, payment_method, order_id),
        )
        conn.commit()


def set_order_payment_method(order_id, payment_method):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "Orders" SET "PaymentMethod"=%s WHERE "Id"=%s',
            (payment_method, order_id),
        )
        conn.commit()


def get_order(order_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "UserId", "TotalAmount", "Status", "PaymentMethod", '
            '"PaymentAuthority", "TelegramId" '
            'FROM "Orders" WHERE "Id"=%s',
            (order_id,),
        )
        return cur.fetchone()


def add_order_item(order_id, product_name, price, qty=1, product_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "OrderItems" '
            '("OrderId", "ProductId", "ProductName", "Price", "Quantity") '
            'VALUES (%s, %s, %s, %s, %s) RETURNING "Id"',
            (order_id, product_id, product_name, price, qty),
        )
        item_id = cur.fetchone()[0]
        conn.commit()
        return item_id


def add_gem_order_info(order_id, order_item_id, gem_package_id, purchase_type,
                       telegram_id='', game_uid=None, player_name=None,
                       login_method=None, login_email=None,
                       login_password=None, backup_code=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "GemOrderInfo" '
            '("OrderId", "OrderItemId", "GemPackageId", "PurchaseType", "TelegramId", '
            '"GameUID", "PlayerName", "LoginMethod", "LoginEmail", "LoginPassword", "BackupCode") '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING "Id"',
            (
                order_id, order_item_id, gem_package_id, purchase_type, str(telegram_id),
                game_uid, player_name, login_method, login_email, login_password, backup_code,
            ),
        )
        info_id = cur.fetchone()[0]
        conn.commit()
        return info_id


def update_order_status(order_id, status):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('UPDATE "Orders" SET "Status"=%s WHERE "Id"=%s', (status, order_id))
        conn.commit()


def get_user_orders(user_db_id, limit=10):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "TotalAmount", "Status", "CreatedAt" FROM "Orders" '
            'WHERE "UserId"=%s ORDER BY "Id" DESC LIMIT %s',
            (user_db_id, limit),
        )
        return cur.fetchall()


def get_gem_infos_for_order(order_id):
    """(InfoId, GemPackageId, GameUID, PlayerName, AutoDeliver, CatalogueName, G2BulkOrderId)"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT g."Id", g."GemPackageId", g."GameUID", g."PlayerName", '
            'p."AutoDeliver", p."G2BulkCatalogueName", g."G2BulkOrderId", p."Amount" '
            'FROM "GemOrderInfo" g '
            'JOIN "GemPackages" p ON p."Id"=g."GemPackageId" '
            'WHERE g."OrderId"=%s',
            (order_id,),
        )
        return cur.fetchall()


def update_gem_g2bulk(info_id, order_id_g2=None, status=None, player_name=None):
    with get_conn() as conn, conn.cursor() as cur:
        if order_id_g2 is not None:
            cur.execute(
                'UPDATE "GemOrderInfo" SET "G2BulkOrderId"=%s, "G2BulkStatus"=%s WHERE "Id"=%s',
                (str(order_id_g2), status or 'PENDING', info_id),
            )
        elif status is not None:
            cur.execute(
                'UPDATE "GemOrderInfo" SET "G2BulkStatus"=%s WHERE "Id"=%s',
                (status, info_id),
            )
        if player_name:
            cur.execute(
                'UPDATE "GemOrderInfo" SET "PlayerName"=%s '
                'WHERE "Id"=%s AND ("PlayerName" IS NULL OR "PlayerName"=\'\')',
                (player_name, info_id),
            )
        conn.commit()


def fulfill_order(order_id):
    """پس از پرداخت موفق: وضعیت paid → تحویل G2Bulk → delivered در صورت موفقیت."""
    order = get_order(order_id)
    if not order:
        return False, 'سفارش پیدا نشد.'
    if order[3] in ('paid', 'delivered', 'completed'):
        # قبلاً پرداخت شده؛ سعی می‌کنیم تحویل را تکمیل کنیم
        pass
    else:
        update_order_status(order_id, 'paid')

    infos = get_gem_infos_for_order(order_id)
    delivered = 0
    total_auto = 0
    for info in infos:
        info_id, pkg_id, game_uid, player_name, auto_deliver, catalogue, g2_id, amount = info
        if not auto_deliver:
            decrement_gem_stock(pkg_id, 1)
            continue
        total_auto += 1
        if g2_id:
            delivered += 1
            continue
        catalogue_name = catalogue or str(amount)
        result = g2bulk.place_game_order(
            catalogue_name=catalogue_name,
            player_id=game_uid,
            remark=f'Atomic Bot order #{order_id}',
            idempotency_key=g2bulk.idempotency_key(order_id, info_id),
        )
        if result['ok']:
            update_gem_g2bulk(
                info_id,
                order_id_g2=result['order_id'],
                status=result.get('status', 'PENDING'),
                player_name=result.get('player_name') or player_name,
            )
            delivered += 1
        else:
            update_gem_g2bulk(info_id, status='FAILED')

    if total_auto and delivered == total_auto:
        update_order_status(order_id, 'delivered')
        return True, 'delivered'
    if total_auto == 0:
        update_order_status(order_id, 'paid')
        return True, 'paid'
    if delivered:
        update_order_status(order_id, 'processing')
        return True, 'processing'
    return False, 'تحویل خودکار ناموفق بود. پشتیبانی بررسی می‌کند.'


# ─── Wallet ─────────────────────────────────────────────────────────────────────
def get_or_create_wallet(user_db_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "Id", "Balance" FROM "Wallets" WHERE "UserId"=%s', (user_db_id,))
        row = cur.fetchone()
        if row:
            return row[0], row[1]
        cur.execute(
            'INSERT INTO "Wallets" ("UserId", "Balance", "UpdatedAt") '
            'VALUES (%s, 0, now()) RETURNING "Id", "Balance"',
            (user_db_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0], row[1]


def get_wallet_balance(user_db_id):
    _, balance = get_or_create_wallet(user_db_id)
    return balance


def wallet_charge(user_db_id, amount, desc='', authority=None):
    amount = int(amount)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "Id", "Balance" FROM "Wallets" WHERE "UserId"=%s FOR UPDATE', (user_db_id,))
        row = cur.fetchone()
        if not row:
            cur.execute(
                'INSERT INTO "Wallets" ("UserId", "Balance", "UpdatedAt") '
                'VALUES (%s, 0, now()) RETURNING "Id", "Balance"',
                (user_db_id,),
            )
            row = cur.fetchone()
        wallet_id, balance = row
        new_bal = balance + amount
        cur.execute(
            'UPDATE "Wallets" SET "Balance"=%s, "UpdatedAt"=now() WHERE "Id"=%s',
            (new_bal, wallet_id),
        )
        cur.execute(
            'INSERT INTO "WalletTransactions" '
            '("WalletId", "Amount", "Kind", "Description", "Authority", "IsPaid", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, %s, true, now())',
            (wallet_id, amount, 'charge', desc or f'شارژ {amount:,} تومان', authority),
        )
        conn.commit()
        return new_bal


def wallet_spend(user_db_id, amount, desc=''):
    amount = int(amount)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "Id", "Balance" FROM "Wallets" WHERE "UserId"=%s FOR UPDATE', (user_db_id,))
        row = cur.fetchone()
        if not row or row[1] < amount:
            return False, (row[1] if row else 0)
        wallet_id, balance = row
        new_bal = balance - amount
        cur.execute(
            'UPDATE "Wallets" SET "Balance"=%s, "UpdatedAt"=now() WHERE "Id"=%s',
            (new_bal, wallet_id),
        )
        cur.execute(
            'INSERT INTO "WalletTransactions" '
            '("WalletId", "Amount", "Kind", "Description", "IsPaid", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, true, now())',
            (wallet_id, amount, 'spend', desc or f'پرداخت {amount:,} تومان'),
        )
        conn.commit()
        return True, new_bal


def create_wallet_charge_tx(user_db_id, amount, authority):
    """ثبت تراکنش شارژ در انتظار تایید درگاه."""
    wallet_id, _ = get_or_create_wallet(user_db_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "WalletTransactions" '
            '("WalletId", "Amount", "Kind", "Description", "Authority", "IsPaid", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, %s, false, now()) RETURNING "Id"',
            (wallet_id, int(amount), 'charge', f'شارژ کیف پول {int(amount):,} تومان', authority),
        )
        tx_id = cur.fetchone()[0]
        conn.commit()
        return tx_id


def complete_wallet_charge_by_authority(authority):
    """پس از verify زرین‌پال، موجودی را شارژ کن. خروجی: (ok, user_id, amount, new_balance)"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id", t."WalletId", t."Amount", t."IsPaid", w."UserId", w."Balance" '
            'FROM "WalletTransactions" t '
            'JOIN "Wallets" w ON w."Id"=t."WalletId" '
            'WHERE t."Authority"=%s AND t."Kind"=\'charge\'',
            (authority,),
        )
        row = cur.fetchone()
        if not row:
            return False, None, 0, 0
        tx_id, wallet_id, amount, is_paid, user_id, balance = row
        if is_paid:
            return True, user_id, amount, balance
        new_bal = balance + amount
        cur.execute(
            'UPDATE "Wallets" SET "Balance"=%s, "UpdatedAt"=now() WHERE "Id"=%s',
            (new_bal, wallet_id),
        )
        cur.execute(
            'UPDATE "WalletTransactions" SET "IsPaid"=true WHERE "Id"=%s',
            (tx_id,),
        )
        conn.commit()
        return True, user_id, amount, new_bal


def get_order_by_authority(authority):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "UserId", "TotalAmount", "Status", "PaymentMethod", '
            '"PaymentAuthority", "TelegramId" '
            'FROM "Orders" WHERE "PaymentAuthority"=%s',
            (authority,),
        )
        return cur.fetchone()


# ─── Schema patch (ادمین / بلاک / پشتیبانی) ─────────────────────────────────────
def ensure_admin_schema():
    """ستون‌های لازم برای بلاک و پشتیبانی را اگر نبود اضافه کن."""
    stmts = [
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "IsBlocked" BOOLEAN NOT NULL DEFAULT false',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "BlockedReason" VARCHAR(255) NOT NULL DEFAULT \'\'',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "BlockedAt" TIMESTAMPTZ',
        'ALTER TABLE "SupportTickets" ADD COLUMN IF NOT EXISTS "UpdatedAt" TIMESTAMPTZ DEFAULT now()',
        'ALTER TABLE "SupportTickets" ADD COLUMN IF NOT EXISTS "TelegramId" VARCHAR(64)',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "KycStatus" VARCHAR(20) NOT NULL DEFAULT \'none\'',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "KycCode" VARCHAR(32) NOT NULL DEFAULT \'\'',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "KycVerifiedAt" TIMESTAMPTZ',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "KycRejectReason" VARCHAR(255) NOT NULL DEFAULT \'\'',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "TelegramUsername" VARCHAR(150) NOT NULL DEFAULT \'\'',
    ]
    with get_conn() as conn, conn.cursor() as cur:
        for sql in stmts:
            try:
                cur.execute(sql)
            except Exception as e:
                print(f'[DB] schema patch skipped: {sql[:40]}… ({e})')
        conn.commit()


def sync_gem_prices():
    """قیمت بسته‌های ME را با لیست فعلی هماهنگ کن."""
    prices = {
        110: 200_000,
        231: 400_000,
        583: 1_000_000,
        1188: 2_000_000,
        2420: 4_000_000,
    }
    with get_conn() as conn, conn.cursor() as cur:
        for amount, price in prices.items():
            cur.execute(
                'UPDATE "GemPackages" SET "Price"=%s '
                'WHERE "Amount"=%s AND "PurchaseType"=\'by_id\' AND "PlanType"=\'once\'',
                (price, amount),
            )
        conn.commit()


def is_user_blocked(telegram_id) -> bool:
    tg = str(telegram_id)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT COALESCE("IsBlocked", false) FROM "Users" WHERE "TelegramId"=%s',
                (tg,),
            )
            row = cur.fetchone()
            return bool(row and row[0])
    except Exception:
        return False


def set_user_blocked(telegram_id, blocked=True, reason=''):
    tg = str(telegram_id)
    with get_conn() as conn, conn.cursor() as cur:
        if blocked:
            cur.execute(
                'UPDATE "Users" SET "IsBlocked"=true, "BlockedReason"=%s, "BlockedAt"=now() '
                'WHERE "TelegramId"=%s',
                (reason or '', tg),
            )
        else:
            cur.execute(
                'UPDATE "Users" SET "IsBlocked"=false, "BlockedReason"=\'\', "BlockedAt"=NULL '
                'WHERE "TelegramId"=%s',
                (tg,),
            )
        conn.commit()
        return cur.rowcount > 0


def get_user_profile(telegram_id=None, db_id=None):
    """(Id, TelegramId, TelegramUsername, FirstName, LastName, IsBlocked, BlockedReason, Balance, DateJoined)"""
    with get_conn() as conn, conn.cursor() as cur:
        cols = (
            'SELECT u."Id", u."TelegramId", '
            'COALESCE(NULLIF(u."TelegramUsername", \'\'), '
            'CASE WHEN LEFT(u."Username", 3) = \'tg_\' THEN \'\' ELSE u."Username" END, \'\'), '
            'u."FirstName", u."LastName", '
            'COALESCE(u."IsBlocked", false), COALESCE(u."BlockedReason", \'\'), '
            'COALESCE(w."Balance", 0), u."DateJoined" '
            'FROM "Users" u '
            'LEFT JOIN "Wallets" w ON w."UserId"=u."Id" '
        )
        if telegram_id is not None:
            cur.execute(cols + 'WHERE u."TelegramId"=%s', (str(telegram_id),))
        else:
            cur.execute(cols + 'WHERE u."Id"=%s', (db_id,))
        return cur.fetchone()


def find_user_by_username(username):
    """جستجو با @username — خروجی مثل get_user_profile."""
    un = (username or '').lstrip('@').strip()
    if not un:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT u."Id", u."TelegramId", '
            'COALESCE(NULLIF(u."TelegramUsername", \'\'), '
            'CASE WHEN LEFT(u."Username", 3) = \'tg_\' THEN \'\' ELSE u."Username" END, \'\'), '
            'u."FirstName", u."LastName", '
            'COALESCE(u."IsBlocked", false), COALESCE(u."BlockedReason", \'\'), '
            'COALESCE(w."Balance", 0), u."DateJoined" '
            'FROM "Users" u '
            'LEFT JOIN "Wallets" w ON w."UserId"=u."Id" '
            'WHERE LOWER(u."TelegramUsername")=%s '
            'OR (LEFT(u."Username", 3) <> \'tg_\' AND LOWER(u."Username")=%s) '
            'LIMIT 1',
            (un.lower(), un.lower()),
        )
        return cur.fetchone()


def list_recent_users(limit=15):
    """(Id, TelegramId, FirstName, TelegramUsername, IsBlocked, Balance)"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT u."Id", u."TelegramId", u."FirstName", '
            'COALESCE(NULLIF(u."TelegramUsername", \'\'), '
            'CASE WHEN LEFT(u."Username", 3) = \'tg_\' THEN \'\' ELSE u."Username" END, \'\'), '
            'COALESCE(u."IsBlocked", false), COALESCE(w."Balance", 0) '
            'FROM "Users" u '
            'LEFT JOIN "Wallets" w ON w."UserId"=u."Id" '
            'ORDER BY u."Id" DESC LIMIT %s',
            (limit,),
        )
        return cur.fetchall()


def get_admin_stats():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM "Users"')
        users = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM "Users" WHERE COALESCE("IsBlocked", false)=true')
        blocked = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM "Orders"')
        orders = cur.fetchone()[0]
        cur.execute(
            'SELECT COUNT(*) FROM "Orders" WHERE "Status" IN (\'pending\', \'processing\', \'paid\')'
        )
        open_orders = cur.fetchone()[0]
        cur.execute(
            'SELECT COUNT(*) FROM "GemOrderInfo" WHERE "G2BulkStatus"=\'FAILED\''
        )
        failed_g2 = cur.fetchone()[0]
        cur.execute(
            'SELECT COUNT(*) FROM "SupportTickets" WHERE "Status"=\'open\''
        )
        open_tickets = cur.fetchone()[0]
        cur.execute('SELECT COALESCE(SUM("Balance"), 0) FROM "Wallets"')
        wallet_sum = cur.fetchone()[0]
        return {
            'users': users,
            'blocked': blocked,
            'orders': orders,
            'open_orders': open_orders,
            'failed_g2': failed_g2,
            'open_tickets': open_tickets,
            'wallet_sum': int(wallet_sum or 0),
        }


def list_failed_deliveries(limit=20):
    """سفارش‌هایی که تحویل G2Bulk شکست خورده."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT DISTINCT o."Id", o."TelegramId", o."TotalAmount", o."Status", '
            'o."PaymentMethod", g."GameUID", g."G2BulkStatus" '
            'FROM "Orders" o '
            'JOIN "GemOrderInfo" g ON g."OrderId"=o."Id" '
            'WHERE g."G2BulkStatus"=\'FAILED\' OR o."Status"=\'processing\' '
            'ORDER BY o."Id" DESC LIMIT %s',
            (limit,),
        )
        return cur.fetchall()


def list_open_orders(limit=20):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "TelegramId", "TotalAmount", "Status", "PaymentMethod", "CreatedAt" '
            'FROM "Orders" '
            'WHERE "Status" IN (\'pending\', \'paid\', \'processing\') '
            'ORDER BY "Id" DESC LIMIT %s',
            (limit,),
        )
        return cur.fetchall()


def admin_adjust_wallet(user_db_id, amount, desc='تنظیم ادمین'):
    """amount مثبت = شارژ، منفی = کسر. موجودی منفی نمی‌شود. خروجی: (ok, new_balance, error)"""
    amount = int(amount)
    if amount == 0:
        return False, 0, 'مبلغ صفر است.'
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "Balance" FROM "Wallets" WHERE "UserId"=%s FOR UPDATE',
            (user_db_id,),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                'INSERT INTO "Wallets" ("UserId", "Balance", "UpdatedAt") '
                'VALUES (%s, 0, now()) RETURNING "Id", "Balance"',
                (user_db_id,),
            )
            row = cur.fetchone()
        wallet_id, balance = row
        new_bal = balance + amount
        if new_bal < 0:
            return False, balance, 'موجودی کافی نیست.'
        kind = 'charge' if amount > 0 else 'spend'
        cur.execute(
            'UPDATE "Wallets" SET "Balance"=%s, "UpdatedAt"=now() WHERE "Id"=%s',
            (new_bal, wallet_id),
        )
        cur.execute(
            'INSERT INTO "WalletTransactions" '
            '("WalletId", "Amount", "Kind", "Description", "IsPaid", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, true, now())',
            (wallet_id, abs(amount), kind, f'[admin] {desc}'),
        )
        conn.commit()
        return True, new_bal, None


def list_wallet_txs(user_db_id, limit=10):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Amount", t."Kind", t."Description", t."IsPaid", t."CreatedAt" '
            'FROM "WalletTransactions" t '
            'JOIN "Wallets" w ON w."Id"=t."WalletId" '
            'WHERE w."UserId"=%s ORDER BY t."Id" DESC LIMIT %s',
            (user_db_id, limit),
        )
        return cur.fetchall()


# ─── Support ────────────────────────────────────────────────────────────────────
def create_ticket(user_db_id, subject, message, category='other', telegram_id=''):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "SupportTickets" '
            '("UserId", "Subject", "Category", "Priority", "Message", "Status", '
            '"CreatedAt", "UpdatedAt", "TelegramId") '
            "VALUES (%s, %s, %s, 'normal', %s, 'open', now(), now(), %s) RETURNING \"Id\"",
            (user_db_id, subject[:255], category, message, str(telegram_id or '')),
        )
        ticket_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO "TicketMessages" ("TicketId", "Sender", "Text", "CreatedAt") '
            "VALUES (%s, 'user', %s, now())",
            (ticket_id, message),
        )
        conn.commit()
        return ticket_id


def add_ticket_message(ticket_id, sender, text):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "TicketMessages" ("TicketId", "Sender", "Text", "CreatedAt") '
            'VALUES (%s, %s, %s, now())',
            (ticket_id, sender, text),
        )
        cur.execute(
            'UPDATE "SupportTickets" SET "UpdatedAt"=now() WHERE "Id"=%s',
            (ticket_id,),
        )
        conn.commit()


def get_ticket(ticket_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id", t."UserId", t."Subject", t."Message", t."Status", '
            't."TelegramId", u."TelegramId", u."FirstName" '
            'FROM "SupportTickets" t '
            'LEFT JOIN "Users" u ON u."Id"=t."UserId" '
            'WHERE t."Id"=%s',
            (ticket_id,),
        )
        return cur.fetchone()


def list_open_tickets(limit=20):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id", t."Subject", t."Status", t."CreatedAt", '
            'COALESCE(t."TelegramId", u."TelegramId"), u."FirstName" '
            'FROM "SupportTickets" t '
            'LEFT JOIN "Users" u ON u."Id"=t."UserId" '
            'WHERE t."Status"=\'open\' '
            'ORDER BY t."UpdatedAt" DESC NULLS LAST, t."Id" DESC LIMIT %s',
            (limit,),
        )
        return cur.fetchall()


def close_ticket(ticket_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "SupportTickets" SET "Status"=\'closed\', "UpdatedAt"=now() WHERE "Id"=%s',
            (ticket_id,),
        )
        conn.commit()


def get_active_ticket_for_user(user_db_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id" FROM "SupportTickets" '
            'WHERE "UserId"=%s AND "Status"=\'open\' ORDER BY "Id" DESC LIMIT 1',
            (user_db_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


# ─── KYC (احراز برای بسته‌های گران — فقط درگاه) ────────────────────────────────
KYC_REQUIRED_AMOUNTS = (1188, 2420)


def get_order_gem_amount(order_id):
    """مقدار جم سفارش (Amount بسته)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT p."Amount" FROM "GemOrderInfo" g '
            'JOIN "GemPackages" p ON p."Id"=g."GemPackageId" '
            'WHERE g."OrderId"=%s LIMIT 1',
            (order_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None


def order_requires_kyc(order_id) -> bool:
    amount = get_order_gem_amount(order_id)
    return amount in KYC_REQUIRED_AMOUNTS


def get_kyc_status(telegram_id=None, user_db_id=None) -> str:
    """none | pending | approved | rejected"""
    with get_conn() as conn, conn.cursor() as cur:
        if telegram_id is not None:
            cur.execute(
                'SELECT COALESCE("KycStatus", \'none\') FROM "Users" WHERE "TelegramId"=%s',
                (str(telegram_id),),
            )
        else:
            cur.execute(
                'SELECT COALESCE("KycStatus", \'none\') FROM "Users" WHERE "Id"=%s',
                (user_db_id,),
            )
        row = cur.fetchone()
        return (row[0] if row else 'none') or 'none'


def is_kyc_approved(telegram_id) -> bool:
    return get_kyc_status(telegram_id=telegram_id) == 'approved'


def set_kyc_status(telegram_id, status, code=None, reject_reason=''):
    tg = str(telegram_id)
    with get_conn() as conn, conn.cursor() as cur:
        if status == 'approved':
            cur.execute(
                'UPDATE "Users" SET "KycStatus"=\'approved\', "KycVerifiedAt"=now(), '
                '"KycRejectReason"=\'\' WHERE "TelegramId"=%s',
                (tg,),
            )
        elif status == 'pending':
            cur.execute(
                'UPDATE "Users" SET "KycStatus"=\'pending\', "KycCode"=%s, '
                '"KycRejectReason"=\'\' WHERE "TelegramId"=%s',
                (code or '', tg),
            )
        elif status == 'rejected':
            cur.execute(
                'UPDATE "Users" SET "KycStatus"=\'rejected\', "KycRejectReason"=%s, '
                '"KycVerifiedAt"=NULL WHERE "TelegramId"=%s',
                (reject_reason or '', tg),
            )
        else:
            cur.execute(
                'UPDATE "Users" SET "KycStatus"=\'none\', "KycCode"=\'\', '
                '"KycRejectReason"=\'\', "KycVerifiedAt"=NULL WHERE "TelegramId"=%s',
                (tg,),
            )
        conn.commit()


def set_kyc_code(telegram_id, code):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "Users" SET "KycCode"=%s WHERE "TelegramId"=%s',
            (code or '', str(telegram_id)),
        )
        conn.commit()


def get_kyc_code(telegram_id) -> str:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT COALESCE("KycCode", \'\') FROM "Users" WHERE "TelegramId"=%s',
            (str(telegram_id),),
        )
        row = cur.fetchone()
        return (row[0] if row else '') or ''
