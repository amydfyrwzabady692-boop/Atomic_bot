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
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "Id" FROM "Users" WHERE "TelegramId"=%s', (tg,))
        row = cur.fetchone()
        if row:
            return row[0], False

        uname = username or f"tg_{tg}"
        cur.execute('SELECT 1 FROM "Users" WHERE "Username"=%s', (uname,))
        if cur.fetchone():
            uname = f"tg_{tg}"
        email = f"tg_{tg}@telegram.bot"

        cur.execute(
            'INSERT INTO "Users" '
            '("password", "Username", "Email", "FirstName", "LastName", '
            '"IsStaff", "IsActive", "IsSuperUser", "TelegramId", "DateJoined") '
            'VALUES (%s, %s, %s, %s, %s, false, true, false, %s, now()) '
            'RETURNING "Id"',
            ('', uname, email, first_name or '', last_name or '', tg)
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
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, amounts)
        return cur.fetchall()


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


def set_order_authority(order_id, authority):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "Orders" SET "PaymentAuthority"=%s WHERE "Id"=%s',
            (authority, order_id),
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


# ─── Support (kept for later) ───────────────────────────────────────────────────
def create_ticket(user_db_id, subject, message, category='other'):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "SupportTickets" '
            '("UserId", "Subject", "Category", "Priority", "Message", "Status", "CreatedAt") '
            "VALUES (%s, %s, %s, 'normal', %s, 'open', now()) RETURNING \"Id\"",
            (user_db_id, subject, category, message),
        )
        ticket_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO "TicketMessages" ("TicketId", "Sender", "Text", "CreatedAt") '
            "VALUES (%s, 'user', %s, now())",
            (ticket_id, message),
        )
        conn.commit()
        return ticket_id
