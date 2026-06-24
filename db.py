"""دسترسی به دیتابیس PostgreSQL مشترک با سایت Atomic Shop (accshop).

نکته: جدول‌ها و ستون‌ها با همان نام‌گذاری سایت (PascalCase) هستند، پس همه‌ی
شناسه‌ها داخل «"» قرار می‌گیرند چون PostgreSQL به بزرگ/کوچکی حساس است.
"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

_CONN = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'dbname': os.getenv('DB_NAME', 'accshop'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
}


def get_conn():
    return psycopg.connect(**_CONN)


# ─── Categories / Products (فروشگاه اکانت) ──────────────────────────────────────
def get_categories():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "id", "Name" FROM "Categories" ORDER BY "Name"')
        return cur.fetchall()


def get_products(category_id=None, search=None, limit=20):
    sql = ('SELECT "Id", "Name", "Price", "OldPrice", "Badge", "Image" '
           'FROM "Products" WHERE "IsActive"=true')
    params = []
    if category_id:
        sql += ' AND "CategoryId"=%s'
        params.append(category_id)
    if search:
        sql += ' AND "Name" ILIKE %s'
        params.append(f"%{search}%")
    sql += ' ORDER BY "CreatedAt" DESC LIMIT %s'
    params.append(limit)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_product(pk):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "Name", "Price", "OldPrice", "Badge", "Image", "Details" '
            'FROM "Products" WHERE "Id"=%s AND "IsActive"=true', (pk,)
        )
        return cur.fetchone()


# ─── Gem Packages (جم فری‌فایر) ─────────────────────────────────────────────────
# ستون خروجی: Id, Title, Amount, BonusAmount, Price, OldPrice, PlanType, PurchaseType
_GEM_COLS = ('"Id", "Title", "Amount", "BonusAmount", "Price", "OldPrice", '
             '"PlanType", "PurchaseType"')


def get_gems(purchase_type=None, plan_type=None):
    """بسته‌های جم قابل‌خرید (فعال و موجود) با امکان فیلتر نوع خرید و نوع پلن."""
    sql = (f'SELECT {_GEM_COLS} FROM "GemPackages" '
           'WHERE "IsActive"=true AND "IsAvailable"=true')
    params = []
    if purchase_type:
        sql += ' AND "PurchaseType"=%s'
        params.append(purchase_type)
    if plan_type and plan_type != 'all':
        sql += ' AND "PlanType"=%s'
        params.append(plan_type)
    sql += ' ORDER BY "Price"'
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_gem(pk):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_GEM_COLS}, "Stock", "IsAvailable" FROM "GemPackages" '
            'WHERE "Id"=%s AND "IsActive"=true', (pk,)
        )
        return cur.fetchone()


def decrement_gem_stock(gem_package_id, qty=1):
    """کم‌کردن موجودی پس از خرید موفق (مثل سایت). زیر صفر نمی‌رود."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "GemPackages" SET "Stock" = GREATEST("Stock" - %s, 0) WHERE "Id"=%s',
            (qty, gem_package_id)
        )
        conn.commit()


# ─── Sensitivity Packs (پک سنس) ─────────────────────────────────────────────────
def get_sensitivity_packs(platform='mobile', device=None):
    sql = ('SELECT "Id", "Title", "Price", "OldPrice", "Badge", "DeviceType" '
           'FROM "SensitivityPacks" WHERE "IsActive"=true AND "IsAvailable"=true '
           'AND "Platform"=%s')
    params = [platform]
    if device and device != 'all':
        sql += ' AND "DeviceType"=%s'
        params.append(device)
    sql += ' ORDER BY "Price"'
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_sensitivity_pack(pk):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "Title", "Price" FROM "SensitivityPacks" WHERE "Id"=%s', (pk,)
        )
        return cur.fetchone()


# ─── Users ──────────────────────────────────────────────────────────────────────
def get_or_create_user(telegram_id, first_name='', last_name='', username=''):
    """کاربر تلگرام را بر اساس TelegramId پیدا یا ایجاد می‌کند. (user_id, is_new)"""
    tg = str(telegram_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "Id" FROM "Users" WHERE "TelegramId"=%s', (tg,))
        row = cur.fetchone()
        if row:
            return row[0], False

        uname = username or f"tg_{tg}"
        # یوزرنیم باید یکتا باشد
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


# ─── Orders ─────────────────────────────────────────────────────────────────────
def create_order(user_db_id, total, telegram_id='', full_name='', phone=''):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "Orders" '
            '("UserId", "FullName", "Email", "Phone", "TelegramId", "TotalAmount", '
            '"DiscountAmount", "PaymentMethod", "Status", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, now()) '
            'RETURNING "Id"',
            (user_db_id, full_name or 'کاربر تلگرام',
             f"tg_{telegram_id or user_db_id}@telegram.bot", phone or '',
             str(telegram_id), total, 'zarinpal', 'pending')
        )
        order_id = cur.fetchone()[0]
        conn.commit()
        return order_id


def add_order_item(order_id, product_name, price, qty=1, product_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "OrderItems" '
            '("OrderId", "ProductId", "ProductName", "Price", "Quantity") '
            'VALUES (%s, %s, %s, %s, %s) RETURNING "Id"',
            (order_id, product_id, product_name, price, qty)
        )
        item_id = cur.fetchone()[0]
        conn.commit()
        return item_id


def add_gem_order_info(order_id, order_item_id, gem_package_id, purchase_type,
                       telegram_id='', game_uid=None, login_method=None,
                       login_email=None, login_password=None, backup_code=None):
    """ثبت اطلاعات اختصاصی سفارش جم تا مدیر بداند چطور تحویل دهد."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "GemOrderInfo" '
            '("OrderId", "OrderItemId", "GemPackageId", "PurchaseType", "TelegramId", '
            '"GameUID", "LoginMethod", "LoginEmail", "LoginPassword", "BackupCode") '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (order_id, order_item_id, gem_package_id, purchase_type, str(telegram_id),
             game_uid, login_method, login_email, login_password, backup_code)
        )
        conn.commit()


def update_order_status(order_id, status):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('UPDATE "Orders" SET "Status"=%s WHERE "Id"=%s', (status, order_id))
        conn.commit()


def get_user_orders(user_db_id, limit=10):
    """خروجی: (Id, TotalAmount, Status, CreatedAt) — جدیدترین اول."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "TotalAmount", "Status", "CreatedAt" FROM "Orders" '
            'WHERE "UserId"=%s ORDER BY "Id" DESC LIMIT %s', (user_db_id, limit)
        )
        return cur.fetchall()


# ─── Support Tickets ────────────────────────────────────────────────────────────
def create_ticket(user_db_id, subject, message, category='other'):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "SupportTickets" '
            '("UserId", "Subject", "Category", "Priority", "Message", "Status", "CreatedAt") '
            "VALUES (%s, %s, %s, 'normal', %s, 'open', now()) RETURNING \"Id\"",
            (user_db_id, subject, category, message)
        )
        ticket_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO "TicketMessages" ("TicketId", "Sender", "Text", "CreatedAt") '
            "VALUES (%s, 'user', %s, now())",
            (ticket_id, message)
        )
        conn.commit()
        return ticket_id
