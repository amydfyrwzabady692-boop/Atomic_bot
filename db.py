"""دسترسی به دیتابیس PostgreSQL مشترک با سایت Atomic Shop (accshop).

جدول‌ها و ستون‌ها PascalCase هستند و داخل گیومه قرار می‌گیرند.
"""
import os
import logging
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

from psycopg_pool import ConnectionPool
from dotenv import load_dotenv

import g2bulk
import profitability
from payment_safety import checked_amount, order_amounts, valid_owner

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

_CONN = {
    'host': os.getenv('DB_HOST', os.getenv('DB_SERVER', 'localhost')),
    'port': os.getenv('DB_PORT', '5432'),
    'dbname': os.getenv('DB_NAME', 'accshop'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
}
_POOL = None
_POOL_LOCK = threading.Lock()
_LOG = logging.getLogger(__name__)


def _payment_ttl_minutes():
    try:
        value = int(os.getenv('ORDER_PAYMENT_TTL_MINUTES', '15'))
    except ValueError:
        value = 15
    return max(5, min(value, 120))


def open_db_pool():
    """Open a bounded reusable connection pool once per bot process."""
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is None:
            try:
                max_size = int(os.getenv('DB_POOL_MAX_SIZE', '16'))
            except ValueError:
                max_size = 16
            max_size = max(2, min(max_size, 50))
            pool = ConnectionPool(
                conninfo='',
                kwargs=_CONN,
                min_size=1,
                max_size=max_size,
                timeout=5,
                open=False,
                name='atomic-telegram-db',
            )
            pool.open(wait=True, timeout=10)
            _POOL = pool
    return _POOL


def close_db_pool():
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.close()


def get_conn():
    """Return a context manager that safely returns the connection to the pool."""
    return open_db_pool().connection()


# ─── Users ──────────────────────────────────────────────────────────────────────
def get_or_create_user(telegram_id, first_name='', last_name='', username='',
                       is_premium=None):
    tg = str(telegram_id)
    uname_tg = (username or '').lstrip('@').strip()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT "Id" FROM "Users" WHERE "TelegramId"=%s', (tg,))
        row = cur.fetchone()
        if row:
            # همیشه نام و آیدی تلگرام را تازه نگه دار
            if is_premium is None:
                cur.execute(
                    'UPDATE "Users" SET "FirstName"=%s, "LastName"=%s, '
                    '"TelegramUsername"=%s WHERE "Id"=%s',
                    (first_name or '', last_name or '', uname_tg, row[0]),
                )
            else:
                cur.execute(
                    'UPDATE "Users" SET "FirstName"=%s, "LastName"=%s, '
                    '"TelegramUsername"=%s, "IsTelegramPremium"=%s WHERE "Id"=%s',
                    (first_name or '', last_name or '', uname_tg,
                     bool(is_premium), row[0]),
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
            '"IsStaff", "IsActive", "IsSuperUser", "TelegramId", "TelegramUsername", '
            '"IsTelegramPremium", "DateJoined") '
            'VALUES (%s, %s, %s, %s, %s, false, true, false, %s, %s, %s, now()) '
            'RETURNING "Id"',
            ('', uname, email, first_name or '', last_name or '', tg, uname_tg,
             bool(is_premium))
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
    """همه بسته‌های فعال جم با آیدی که مدیر ساخته است."""
    sql = (
        f'SELECT {_GEM_COLS} FROM "GemPackages" '
        'WHERE "IsActive"=true '
        'AND "PurchaseType"=\'by_id\' '
        'AND "PlanType"=\'once\' '
        'ORDER BY "SortOrder", "Id"'
    )
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception('get_gems_by_id failed: %s', e)
        return []


def get_gems_by_credentials():
    """Active weekly/monthly packages fulfilled through temporary account access."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_GEM_COLS} FROM "GemPackages" '
            'WHERE "IsActive"=true AND "IsAvailable"=true '
            'AND "PurchaseType"=\'by_credentials\' '
            'AND "PlanType" IN (\'weekly\',\'monthly\') '
            'ORDER BY "SortOrder","Id"'
        )
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
    qty = checked_amount(qty, maximum=1_000, label='تعداد')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "GemPackages" SET "Stock" = "Stock" - %s '
            'WHERE "Id"=%s AND COALESCE("AutoDeliver", false)=false AND "Stock">=%s',
            (qty, gem_package_id, qty),
        )
        conn.commit()
        return cur.rowcount == 1


# ─── Orders ─────────────────────────────────────────────────────────────────────
def create_order(user_db_id, total, telegram_id='', full_name='', phone='',
                 payment_method='zarinpal'):
    total = checked_amount(total, label='مبلغ سفارش')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "Orders" '
            '("UserId", "FullName", "Email", "Phone", "TelegramId", "TotalAmount", '
            '"DiscountAmount", "PaymentMethod", "Status","PaymentExpiresAt","CreatedAt") '
            'VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s,'
            'now()+(%s*interval \'1 minute\'),now()) '
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
                _payment_ttl_minutes(),
            ),
        )
        order_id = cur.fetchone()[0]
        conn.commit()
        return order_id


def _insert_pending_order(cur, user_db_id, total, telegram_id='', full_name='',
                          phone='', payment_method='pending'):
    """Insert an order using the caller's transaction and return its id."""
    total = checked_amount(total, label='مبلغ سفارش')
    cur.execute(
        'INSERT INTO "Orders" '
        '("UserId", "FullName", "Email", "Phone", "TelegramId", "TotalAmount", '
        '"DiscountAmount", "PaymentMethod", "Status","PaymentExpiresAt","CreatedAt") '
        'VALUES (%s, %s, %s, %s, %s, %s, 0, %s, \'pending\','
        'now()+(%s*interval \'1 minute\'),now()) RETURNING "Id"',
        (
            user_db_id,
            full_name or 'کاربر تلگرام',
            f"tg_{telegram_id or user_db_id}@telegram.bot",
            phone or '',
            str(telegram_id),
            total,
            payment_method,
            _payment_ttl_minutes(),
        ),
    )
    return cur.fetchone()[0]


def create_gem_order_atomic(user_db_id, gem_package_id, expected_price, *,
                            telegram_id='', full_name='', game_uid='',
                            player_name=None):
    """Create the gem order, item snapshot and delivery row atomically.

    The current package is locked and its price is compared with the price the
    buyer confirmed.  An admin price/availability change therefore cannot
    silently produce a partial or differently-priced order.
    """
    expected_price = checked_amount(expected_price, label='قیمت مورد تأیید')
    game_uid = str(game_uid or '').strip()
    if not game_uid or len(game_uid) > 128:
        raise ValueError('شناسه بازی نامعتبر است.')
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                'SELECT "Title","Price","Stock",COALESCE("AutoDeliver",false) '
                'FROM "GemPackages" WHERE "Id"=%s AND "IsActive"=true '
                'AND "IsAvailable"=true AND "PurchaseType"=\'by_id\' '
                'AND "PlanType"=\'once\' FOR UPDATE',
                (int(gem_package_id),),
            )
            package = cur.fetchone()
            if not package:
                raise ValueError('این بسته دیگر فعال یا قابل خرید نیست.')
            title, price, stock, auto_deliver = package
            price = checked_amount(price, label='قیمت بسته')
            if price != expected_price:
                raise ValueError('قیمت بسته تغییر کرده است؛ دوباره از فهرست انتخاب کن.')
            if not auto_deliver and int(stock or 0) < 1:
                raise ValueError('موجودی این بسته تمام شده است.')

            reservation_status = ''
            if not auto_deliver:
                cur.execute(
                    'UPDATE "GemPackages" SET "Stock"="Stock"-1 '
                    'WHERE "Id"=%s AND "Stock">0',
                    (int(gem_package_id),),
                )
                if cur.rowcount != 1:
                    raise ValueError('موجودی این بسته تمام شده است.')
                reservation_status = 'MANUAL_RESERVED'

            order_id = _insert_pending_order(
                cur, user_db_id, price, telegram_id=telegram_id,
                full_name=full_name, payment_method='pending',
            )
            cur.execute(
                'INSERT INTO "OrderItems" '
                '("OrderId", "ProductId", "ProductName", "Price", "Quantity") '
                'VALUES (%s, NULL, %s, %s, 1) RETURNING "Id"',
                (order_id, title, price),
            )
            item_id = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO "GemOrderInfo" '
                '("OrderId", "OrderItemId", "GemPackageId", "PurchaseType", '
                '"TelegramId", "GameUID", "PlayerName", "G2BulkStatus") '
                'VALUES (%s, %s, %s, \'by_id\', %s, %s, %s, %s)',
                (
                    order_id, item_id, int(gem_package_id), str(telegram_id),
                    game_uid, player_name, reservation_status,
                ),
            )
            conn.commit()
            return order_id, str(title), price
        except Exception:
            conn.rollback()
            raise


def create_credential_gem_order_atomic(
    user_db_id, gem_package_id, expected_price, *, telegram_id='',
    full_name='', login_method='', credential_ciphertext='', two_factor_enabled=False,
    quantity=1,
):
    """Create a manual membership order without ever persisting plaintext secrets."""
    expected_price = checked_amount(expected_price, label='قیمت مورد تأیید')
    quantity = int(quantity or 1)
    if quantity < 1 or quantity > 50:
        raise ValueError('تعداد نامعتبر است.')
    login_method = str(login_method or '').strip().lower()
    if login_method not in ('google', 'facebook', 'vk'):
        raise ValueError('روش ورود نامعتبر است.')
    credential_ciphertext = str(credential_ciphertext or '').strip()
    if not credential_ciphertext or len(credential_ciphertext) > 10_000:
        raise ValueError('اطلاعات رمزگذاری‌شده نامعتبر است.')
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                'SELECT "Title","Price","Stock" FROM "GemPackages" '
                'WHERE "Id"=%s AND "IsActive"=true AND "IsAvailable"=true '
                'AND "PurchaseType"=\'by_credentials\' '
                'AND "PlanType" IN (\'weekly\',\'monthly\') FOR UPDATE',
                (int(gem_package_id),),
            )
            package = cur.fetchone()
            if not package:
                raise ValueError('این محصول دیگر فعال یا موجود نیست.')
            title, unit_price, stock = package
            unit_price = checked_amount(unit_price, label='قیمت محصول')
            total = checked_amount(unit_price * quantity, label='مبلغ کل')
            if total != expected_price:
                raise ValueError('قیمت تغییر کرده است؛ دوباره محصول را انتخاب کن.')
            if int(stock or 0) < quantity:
                raise ValueError('ظرفیت این محصول برای این تعداد کافی نیست.')
            cur.execute(
                'UPDATE "GemPackages" SET "Stock"="Stock"-%s '
                'WHERE "Id"=%s AND "Stock">=%s',
                (quantity, int(gem_package_id), quantity),
            )
            if cur.rowcount != 1:
                raise ValueError('ظرفیت این محصول برای این تعداد کافی نیست.')
            order_id = _insert_pending_order(
                cur, user_db_id, total, telegram_id=telegram_id,
                full_name=full_name, payment_method='pending',
            )
            cur.execute(
                'INSERT INTO "OrderItems" '
                '("OrderId","ProductId","ProductName","Price","Quantity") '
                'VALUES (%s,NULL,%s,%s,%s) RETURNING "Id"',
                (order_id, title, unit_price, quantity),
            )
            item_id = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO "GemOrderInfo" '
                '("OrderId","OrderItemId","GemPackageId","PurchaseType",'
                '"TelegramId","LoginMethod","CredentialCiphertext",'
                '"CredentialStatus","CredentialTwoFactorEnabled",'
                '"CredentialUpdatedAt","G2BulkStatus") '
                'VALUES (%s,%s,%s,\'by_credentials\',%s,%s,%s,\'awaiting_payment\','
                '%s,now(),\'MANUAL_RESERVED\')',
                (
                    order_id, item_id, int(gem_package_id), str(telegram_id),
                    login_method, credential_ciphertext, bool(two_factor_enabled),
                ),
            )
            conn.commit()
            return order_id, str(title), total
        except Exception:
            conn.rollback()
            raise


def create_sense_order_atomic(user_db_id, package_id, expected_price, *,
                              telegram_id='', full_name=''):
    """Create a sensitivity order and its immutable item snapshot atomically."""
    expected_price = checked_amount(expected_price, label='قیمت مورد تأیید')
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                'SELECT "Title","Price" FROM "SensePackages" '
                'WHERE "Id"=%s AND "IsActive"=true FOR UPDATE',
                (int(package_id),),
            )
            package = cur.fetchone()
            if not package:
                raise ValueError('این بسته دیگر فعال یا قابل خرید نیست.')
            title, price = package
            price = checked_amount(price, label='قیمت بسته')
            if price != expected_price:
                raise ValueError('قیمت بسته تغییر کرده است؛ دوباره از فهرست انتخاب کن.')
            order_id = _insert_pending_order(
                cur, user_db_id, price, telegram_id=telegram_id,
                full_name=full_name, payment_method='pending',
            )
            cur.execute(
                'INSERT INTO "OrderItems" '
                '("OrderId", "ProductId", "ProductName", "Price", "Quantity") '
                'VALUES (%s, NULL, %s, %s, 1)',
                (order_id, title, price),
            )
            conn.commit()
            return order_id, str(title), price
        except Exception:
            conn.rollback()
            raise


def set_order_authority(order_id, authority, payment_method='zarinpal',
                        expected_amount=None, user_db_id=None):
    """سازگاری با فراخوان‌های قدیمی؛ ثبت بدون مبلغ مورد انتظار ممنوع است."""
    if payment_method != 'zarinpal' or expected_amount is None:
        return False, 'ثبت درگاه بدون مبلغ ثابت مجاز نیست.'
    return bind_order_authority(
        order_id, authority, expected_amount, user_db_id=user_db_id
    )


def _locked_order_financials(cur, order_id):
    cur.execute(
        'SELECT "Id","UserId","TelegramId","TotalAmount","DiscountAmount",'
        'COALESCE("WalletPaid",0),"Status","PaymentMethod","PaymentAuthority",'
        '"PaymentExpectedAmount","PaymentVerifiedAt",'
        'COALESCE("PaymentExpiresAt"<=now(),false) '
        'FROM "Orders" WHERE "Id"=%s FOR UPDATE',
        (int(order_id),),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError('سفارش پیدا نشد.')
    cur.execute(
        'SELECT COALESCE(SUM("Price"*"Quantity"),0) FROM "OrderItems" WHERE "OrderId"=%s',
        (int(order_id),),
    )
    item_total = int(cur.fetchone()[0] or 0)
    net_total, payable = order_amounts(row[3], row[4], row[5], item_total)
    return row, net_total, payable


def validate_order_financials(order_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            row, net_total, payable = _locked_order_financials(cur, order_id)
            conn.rollback()
            return True, net_total, payable, None
    except ValueError as e:
        return False, 0, 0, str(e)


def order_belongs_to(order_id, *, user_db_id=None, telegram_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "UserId","TelegramId" FROM "Orders" WHERE "Id"=%s',
            (int(order_id),),
        )
        row = cur.fetchone()
        return bool(row and valid_owner(
            row[0], row[1], user_db_id=user_db_id, telegram_id=telegram_id
        ))


def bind_order_authority(order_id, authority, expected_amount, user_db_id=None):
    """Authority را همراه مبلغ ثابت و فقط روی سفارش سالم/درانتظار ثبت می‌کند."""
    authority = str(authority or '').strip()
    if not authority or len(authority) > 100:
        return False, 'کد درگاه نامعتبر است.'
    try:
        expected_amount = checked_amount(
            expected_amount, minimum=1, label='مبلغ مورد انتظار درگاه'
        )
        with get_conn() as conn, conn.cursor() as cur:
            row, _net, payable = _locked_order_financials(cur, order_id)
            if row[6] != 'pending' or row[10]:
                return False, 'سفارش دیگر قابل پرداخت نیست.'
            if row[11]:
                return False, 'مهلت پرداخت سفارش تمام شده است.'
            if user_db_id is not None and int(row[1]) != int(user_db_id):
                return False, 'سفارش متعلق به این کاربر نیست.'
            if row[8] and row[8] != authority:
                return False, 'برای سفارش از قبل یک لینک درگاه فعال است.'
            if payable != expected_amount:
                return False, 'مبلغ سفارش هنگام ساخت لینک تغییر کرده است.'
            cur.execute(
                'UPDATE "Orders" SET "PaymentAuthority"=%s,"PaymentMethod"=\'zarinpal\','
                '"PaymentExpectedAmount"=%s WHERE "Id"=%s AND "Status"=\'pending\'',
                (authority, expected_amount, int(order_id)),
            )
            conn.commit()
            return cur.rowcount == 1, None
    except ValueError as e:
        return False, str(e)


def prepare_card_order_payment(order_id, user_db_id):
    """انتخاب کارت‌به‌کارت فقط برای سفارش سالمی که لینک درگاه فعال ندارد."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            row, _net, payable = _locked_order_financials(cur, order_id)
            if row[6] != 'pending' or row[10]:
                return False, 0, 'سفارش قابل پرداخت نیست.'
            if row[11]:
                return False, 0, 'مهلت پرداخت سفارش تمام شده است.'
            if int(row[1]) != int(user_db_id):
                return False, 0, 'سفارش متعلق به این کاربر نیست.'
            if row[8]:
                return False, 0, (
                    'برای این سفارش لینک زرین‌پال فعال است؛ همان لینک را بررسی کن '
                    'یا سفارش را لغو و دوباره ثبت کن.'
                )
            checked_amount(payable, label='مبلغ کارت‌به‌کارت')
            cur.execute(
                'UPDATE "Orders" SET "PaymentMethod"=\'card_transfer\','
                '"PaymentExpectedAmount"=%s WHERE "Id"=%s AND "Status"=\'pending\'',
                (payable, int(order_id)),
            )
            conn.commit()
            return cur.rowcount == 1, payable, None
    except ValueError as e:
        return False, 0, str(e)


def detach_order_authority_to_wallet(order_id, user_db_id):
    """Allow a safe method change while preserving a possible late payment.

    The old authority becomes a pending wallet charge. If the old link is paid
    later, its callback can only credit the wallet and cannot fulfill the order.
    """
    try:
        with get_conn() as conn, conn.cursor() as cur:
            row, _net, payable = _locked_order_financials(cur, order_id)
            if row[6] != 'pending' or row[10]:
                return False, 'سفارش قابل تغییر نیست.'
            if int(row[1]) != int(user_db_id):
                return False, 'سفارش متعلق به این کاربر نیست.'
            authority = str(row[8] or '').strip()
            expected = int(row[9] or 0)
            if not authority or row[7] != 'zarinpal' or expected != payable:
                return False, 'لینک درگاه فعالی برای تغییر پیدا نشد.'
            cur.execute(
                'SELECT "Id" FROM "Wallets" WHERE "UserId"=%s FOR UPDATE',
                (int(user_db_id),),
            )
            wallet = cur.fetchone()
            if not wallet:
                cur.execute(
                    'INSERT INTO "Wallets" ("UserId","Balance","UpdatedAt") '
                    'VALUES (%s,0,now()) RETURNING "Id"',
                    (int(user_db_id),),
                )
                wallet = cur.fetchone()
            cur.execute(
                'INSERT INTO "WalletTransactions" '
                '("WalletId","Amount","Kind","Description","Authority","IsPaid",'
                '"PaymentExpectedAmount","CreatedAt") '
                'VALUES (%s,%s,\'charge\',%s,%s,false,%s,now())',
                (
                    wallet[0], expected,
                    f'لینک کنارگذاشته‌شده سفارش #{order_id}',
                    authority, expected,
                ),
            )
            cur.execute(
                'UPDATE "Orders" SET "PaymentMethod"=\'pending\','
                '"PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL '
                'WHERE "Id"=%s AND "Status"=\'pending\' AND "PaymentAuthority"=%s',
                (int(order_id), authority),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False, 'روش پرداخت هم‌زمان تغییر کرده؛ دوباره بررسی کن.'
            conn.commit()
            return True, None
    except (ValueError, TypeError) as e:
        return False, str(e)


def get_gateway_wallet_charge(authority):
    authority = str(authority or '').strip()
    if not authority:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id",t."Amount",t."IsPaid",w."UserId",u."TelegramId" '
            'FROM "WalletTransactions" t '
            'JOIN "Wallets" w ON w."Id"=t."WalletId" '
            'LEFT JOIN "Users" u ON u."Id"=w."UserId" '
            'WHERE t."Authority"=%s AND t."Kind"=\'charge\'',
            (authority,),
        )
        return cur.fetchone()


def get_order_payment_expected(order_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "PaymentExpectedAmount" FROM "Orders" WHERE "Id"=%s',
            (int(order_id),),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


def is_order_payment_expired(order_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT COALESCE("PaymentExpiresAt"<=now(),false) '
            'FROM "Orders" WHERE "Id"=%s',
            (int(order_id),),
        )
        row = cur.fetchone()
        return bool(row and row[0])


def record_order_payment_verified(order_id, method, expected_amount,
                                  authority=None, ref_id=None):
    """اثبات پرداخت را اتمیک ثبت و سفارش را برای تحویل claim می‌کند."""
    try:
        expected_amount = checked_amount(expected_amount, label='مبلغ تأییدشده')
        with get_conn() as conn, conn.cursor() as cur:
            row, _net, payable = _locked_order_financials(cur, order_id)
            if row[10]:
                if row[7] != method:
                    return False, 'payment method mismatch'
                if payable != expected_amount or int(row[9] or 0) != expected_amount:
                    return False, 'payment amount mismatch'
                if authority and row[8] != authority:
                    return False, 'authority mismatch'
                return True, 'already_verified'
            if row[6] != 'pending':
                return False, f'invalid status: {row[6]}'
            if row[7] != method:
                return False, 'payment method mismatch'
            if payable != expected_amount or int(row[9] or 0) != expected_amount:
                return False, 'payment amount mismatch'
            if method == 'zarinpal' and (
                not authority or row[8] != authority
            ):
                return False, 'authority mismatch'
            if method == 'zarinpal' and not str(ref_id or '').strip():
                return False, 'missing gateway reference'
            cur.execute(
                'UPDATE "Orders" SET "PaymentVerifiedAt"=now(),"PaymentRefId"=%s,'
                '"Status"=\'processing\' WHERE "Id"=%s AND "Status"=\'pending\' '
                'AND "PaymentVerifiedAt" IS NULL',
                (str(ref_id or '')[:100], int(order_id)),
            )
            conn.commit()
            return cur.rowcount == 1, 'verified'
    except ValueError as e:
        return False, str(e)


def get_order(order_id):
    """Id, UserId, TotalAmount, Status, PaymentMethod, PaymentAuthority, TelegramId, WalletPaid"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "UserId", "TotalAmount", "Status", "PaymentMethod", '
            '"PaymentAuthority", "TelegramId", COALESCE("WalletPaid", 0) '
            'FROM "Orders" WHERE "Id"=%s',
            (order_id,),
        )
        return cur.fetchone()


def get_order_payable(order_id):
    ok, _net, payable, _error = validate_order_financials(order_id)
    return payable if ok else 0


def apply_wallet_to_order(user_db_id, order_id):
    """کسر موجودی به اندازه ممکن از مبلغ باقی‌مانده.
    خروجی: (ok, used, remaining, new_balance, error)"""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            row, net_total, remaining = _locked_order_financials(cur, order_id)
            if row[6] != 'pending' or row[10]:
                return False, 0, 0, 0, 'سفارش قابل پرداخت نیست.'
            if row[11]:
                return False, 0, remaining, 0, 'مهلت پرداخت سفارش تمام شده است.'
            if int(row[1]) != int(user_db_id):
                return False, 0, remaining, 0, 'سفارش متعلق به این کاربر نیست.'
            if row[8]:
                return False, 0, remaining, 0, (
                    'برای این سفارش لینک درگاه فعال است؛ ابتدا سفارش را لغو و دوباره ثبت کن.'
                )
            if remaining <= 0:
                return False, 0, 0, 0, 'مبلغی برای پرداخت نمانده.'
            cur.execute(
                'SELECT "Id","Balance" FROM "Wallets" WHERE "UserId"=%s FOR UPDATE',
                (int(user_db_id),),
            )
            wallet = cur.fetchone()
            balance = int(wallet[1] if wallet else 0)
            if not wallet or balance <= 0:
                return False, 0, remaining, balance, 'موجودی کیف پول صفر است.'
            use = min(balance, remaining)
            checked_amount(use, label='مبلغ کسر کیف پول')
            new_balance = balance - use
            new_wallet_paid = int(row[5]) + use
            new_remaining = net_total - new_wallet_paid
            cur.execute(
                'UPDATE "Wallets" SET "Balance"=%s,"UpdatedAt"=now() WHERE "Id"=%s',
                (new_balance, wallet[0]),
            )
            cur.execute(
                'INSERT INTO "WalletTransactions" '
                '("WalletId","Amount","Kind","Description","IsPaid","CreatedAt") '
                'VALUES (%s,%s,\'spend\',%s,true,now())',
                (wallet[0], use, f'پرداخت سفارش #{order_id} (کیف پول)'),
            )
            if new_remaining == 0:
                cur.execute(
                    'UPDATE "Orders" SET "WalletPaid"=%s,"PaymentMethod"=\'wallet\','
                    '"PaymentExpectedAmount"=%s,"PaymentVerifiedAt"=now(),'
                    '"PaymentRefId"=%s,"Status"=\'processing\' WHERE "Id"=%s',
                    (new_wallet_paid, net_total, f'wallet:{order_id}', int(order_id)),
                )
            else:
                cur.execute(
                    'UPDATE "Orders" SET "WalletPaid"=%s,"PaymentMethod"=\'pending\','
                    '"PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL WHERE "Id"=%s',
                    (new_wallet_paid, int(order_id)),
                )
            conn.commit()
            return True, use, new_remaining, new_balance, None
    except ValueError as e:
        return False, 0, 0, 0, str(e)


def refund_order_wallet(order_id):
    """اگر از کیف پول چیزی کسر شده، برگردان و WalletPaid را صفر کن."""
    raise RuntimeError(
        'Unsafe legacy refund path is disabled; use an atomic order cancellation flow.'
    )


def _release_manual_gem_reservations(cur, order_id):
    """Release only unpaid manual inventory reservations in caller transaction."""
    cur.execute(
        'SELECT "Id","GemPackageId","OrderItemId" FROM "GemOrderInfo" '
        'WHERE "OrderId"=%s AND "G2BulkStatus"=\'MANUAL_RESERVED\' '
        'FOR UPDATE',
        (int(order_id),),
    )
    reservations = cur.fetchall()
    for info_id, package_id, item_id in reservations:
        qty = 1
        if item_id:
            cur.execute(
                'SELECT COALESCE("Quantity",1) FROM "OrderItems" WHERE "Id"=%s',
                (int(item_id),),
            )
            item = cur.fetchone()
            if item:
                qty = max(1, int(item[0] or 1))
        cur.execute(
            'UPDATE "GemPackages" SET "Stock"="Stock"+%s WHERE "Id"=%s',
            (qty, int(package_id)),
        )
        if cur.rowcount != 1:
            raise ValueError('بسته رزروشده برای آزادسازی پیدا نشد.')
        cur.execute(
            'UPDATE "GemOrderInfo" SET "G2BulkStatus"=\'MANUAL_RELEASED\','
            '"CredentialCiphertext"=CASE WHEN "PurchaseType"=\'by_credentials\' '
            'THEN NULL ELSE "CredentialCiphertext" END,'
            '"CredentialStatus"=CASE WHEN "PurchaseType"=\'by_credentials\' '
            'THEN \'deleted\' ELSE "CredentialStatus" END,'
            '"CredentialDeletedAt"=CASE WHEN "PurchaseType"=\'by_credentials\' '
            'THEN now() ELSE "CredentialDeletedAt" END '
            'WHERE "Id"=%s AND "G2BulkStatus"=\'MANUAL_RESERVED\'',
            (int(info_id),),
        )
    return len(reservations)


def cancel_order_and_refund(order_id, telegram_id=None):
    """لغو و بازپرداخت سهم کیف پول در یک تراکنش و فقط پیش از اثبات پرداخت."""
    with get_conn() as conn, conn.cursor() as cur:
        try:
            row, _net, _payable = _locked_order_financials(cur, order_id)
        except ValueError as e:
            return False, 0, str(e)
        if telegram_id is not None and str(row[2] or '') != str(telegram_id):
            return False, 0, 'سفارش متعلق به این کاربر نیست.'
        if row[6] != 'pending' or row[10]:
            return False, 0, 'سفارش پرداخت‌شده یا در حال پردازش قابل لغو نیست.'
        if row[8]:
            return False, 0, (
                'برای سفارش لینک درگاه صادر شده است. برای جلوگیری از گم‌شدن پرداخت، '
                'لغو خودکار ممکن نیست؛ ابتدا وضعیت پرداخت باید بررسی شود.'
            )
        _release_manual_gem_reservations(cur, order_id)
        refunded = int(row[5] or 0)
        if refunded:
            cur.execute(
                'SELECT "Id","Balance" FROM "Wallets" WHERE "UserId"=%s FOR UPDATE',
                (row[1],),
            )
            wallet = cur.fetchone()
            if not wallet:
                return False, 0, 'کیف پول کاربر پیدا نشد.'
            cur.execute(
                'UPDATE "Wallets" SET "Balance"=%s,"UpdatedAt"=now() WHERE "Id"=%s',
                (int(wallet[1]) + refunded, wallet[0]),
            )
            cur.execute(
                'INSERT INTO "WalletTransactions" '
                '("WalletId","Amount","Kind","Description","IsPaid","CreatedAt") '
                'VALUES (%s,%s,\'charge\',%s,true,now())',
                (wallet[0], refunded, f'برگشت کیف پول سفارش #{order_id}'),
            )
        cur.execute(
            'UPDATE "Orders" SET "WalletPaid"=0,"Status"=\'canceled\','
            '"PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL WHERE "Id"=%s',
            (int(order_id),),
        )
        conn.commit()
        return True, refunded, None


def list_expired_unpaid_orders(limit=50):
    """سفارش منقضی بدون اثبات پرداخت/رسید در انتظار بررسی."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT o."Id",o."PaymentMethod",o."PaymentAuthority",'
            'o."PaymentExpectedAmount",o."TelegramId" '
            'FROM "Orders" o '
            'WHERE o."Status"=\'pending\' AND o."PaymentVerifiedAt" IS NULL '
            'AND o."PaymentExpiresAt" IS NOT NULL AND o."PaymentExpiresAt"<=now() '
            'AND NOT EXISTS (SELECT 1 FROM "PaymentReceipts" r '
            ' WHERE r."OrderId"=o."Id" AND r."Status"=\'pending\' '
            ' AND r."FileId"<>\'\') '
            'ORDER BY o."PaymentExpiresAt" LIMIT %s',
            (max(1, min(int(limit), 100)),),
        )
        return cur.fetchall()


def expire_order_and_refund(order_id):
    """لغو اتمیک فقط اگر هنوز منقضی و پرداخت‌نشده و بدون رسید باشد."""
    with get_conn() as conn, conn.cursor() as cur:
        try:
            row, _net, _payable = _locked_order_financials(cur, order_id)
        except ValueError as exc:
            return False, 0, str(exc)
        if row[6] != 'pending' or row[10]:
            return False, 0, 'سفارش دیگر منقضی‌شدنی نیست.'
        cur.execute(
            'SELECT 1 FROM "Orders" WHERE "Id"=%s '
            'AND "PaymentExpiresAt" IS NOT NULL AND "PaymentExpiresAt"<=now()',
            (int(order_id),),
        )
        if not cur.fetchone():
            return False, 0, 'مهلت سفارش هنوز تمام نشده است.'
        cur.execute(
            'SELECT 1 FROM "PaymentReceipts" WHERE "OrderId"=%s '
            'AND "Status"=\'pending\' AND "FileId"<>\'\' LIMIT 1',
            (int(order_id),),
        )
        if cur.fetchone():
            return False, 0, 'رسید سفارش در انتظار بررسی است.'
        _release_manual_gem_reservations(cur, order_id)
        refunded = int(row[5] or 0)
        if refunded:
            cur.execute(
                'SELECT "Id","Balance" FROM "Wallets" '
                'WHERE "UserId"=%s FOR UPDATE',
                (row[1],),
            )
            wallet = cur.fetchone()
            if not wallet:
                return False, 0, 'کیف پول کاربر پیدا نشد.'
            cur.execute(
                'UPDATE "Wallets" SET "Balance"=%s,"UpdatedAt"=now() '
                'WHERE "Id"=%s',
                (int(wallet[1]) + refunded, wallet[0]),
            )
            cur.execute(
                'INSERT INTO "WalletTransactions" '
                '("WalletId","Amount","Kind","Description","IsPaid","CreatedAt") '
                'VALUES (%s,%s,\'charge\',%s,true,now())',
                (wallet[0], refunded, f'برگشت انقضای سفارش #{order_id}'),
            )
        cur.execute(
            'UPDATE "Orders" SET "WalletPaid"=0,"Status"=\'canceled\','
            '"PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL,'
            '"PaymentRefId"=\'expired\' WHERE "Id"=%s '
            'AND "Status"=\'pending\' AND "PaymentVerifiedAt" IS NULL',
            (int(order_id),),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False, 0, 'وضعیت سفارش هم‌زمان تغییر کرد.'
        conn.commit()
        return True, refunded, None


def approve_card_order_payment(order_id):
    """تأیید اتمیک کارت‌به‌کارت؛ بدون رسید تصویری، مبلغ سالم و وضعیت pending ممنوع."""
    with get_conn() as conn, conn.cursor() as cur:
        try:
            row, _net, payable = _locked_order_financials(cur, order_id)
        except ValueError as e:
            return False, str(e)
        if row[6] != 'pending' or row[10]:
            return False, 'سفارش قبلاً بررسی یا پردازش شده است.'
        if row[7] != 'card_transfer':
            return False, 'روش پرداخت سفارش کارت‌به‌کارت نیست.'
        if payable <= 0:
            return False, 'مبلغ قابل پرداخت سفارش نامعتبر است.'
        if int(row[9] or 0) != payable:
            return False, 'مبلغ ثابت رسید با مانده سفارش تطابق ندارد.'
        cur.execute(
            'SELECT "Id" FROM "PaymentReceipts" '
            'WHERE "OrderId"=%s AND "Status"=\'pending\' AND "FileId"<>\'\' '
            'ORDER BY "Id" DESC LIMIT 1 FOR UPDATE',
            (int(order_id),),
        )
        receipt = cur.fetchone()
        if not receipt:
            return False, 'رسید تصویری تأییدنشده‌ای برای سفارش وجود ندارد.'
        cur.execute(
            'UPDATE "Orders" SET "PaymentExpectedAmount"=%s,'
            '"PaymentVerifiedAt"=now(),"PaymentRefId"=%s,"Status"=\'processing\' '
            'WHERE "Id"=%s AND "Status"=\'pending\'',
            (payable, f'card-receipt:{receipt[0]}', int(order_id)),
        )
        if cur.rowcount != 1:
            return False, 'سفارش هم‌زمان توسط درخواست دیگری پردازش شد.'
        cur.execute(
            'UPDATE "PaymentReceipts" SET "Status"=\'approved\',"ReviewedAt"=now() '
            'WHERE "OrderId"=%s AND "Status"=\'pending\'',
            (int(order_id),),
        )
        conn.commit()
        return True, 'verified'


def reject_card_order_payment(order_id):
    """رد رسید و بازگشت اتمیک سهم کیف پول در یک تراکنش."""
    with get_conn() as conn, conn.cursor() as cur:
        try:
            row, _net, _payable = _locked_order_financials(cur, order_id)
        except ValueError as e:
            return False, 0, str(e)
        if row[6] != 'pending' or row[10]:
            return False, 0, 'سفارش پرداخت‌شده یا در حال پردازش قابل رد نیست.'
        if row[7] != 'card_transfer':
            return False, 0, 'روش پرداخت سفارش کارت‌به‌کارت نیست.'
        # حتی اگر لینک درگاه هم بوده، رد رسید کارت باید ممکن باشد
        _release_manual_gem_reservations(cur, order_id)
        refunded = int(row[5] or 0)
        if refunded:
            cur.execute(
                'SELECT "Id","Balance" FROM "Wallets" WHERE "UserId"=%s FOR UPDATE',
                (row[1],),
            )
            wallet = cur.fetchone()
            if not wallet:
                cur.execute(
                    'INSERT INTO "Wallets" ("UserId","Balance","UpdatedAt") '
                    'VALUES (%s,0,now()) '
                    'ON CONFLICT ("UserId") DO UPDATE SET "UpdatedAt"=now() '
                    'RETURNING "Id","Balance"',
                    (row[1],),
                )
                wallet = cur.fetchone()
            if not wallet:
                return False, 0, 'کیف پول کاربر پیدا نشد.'
            cur.execute(
                'UPDATE "Wallets" SET "Balance"=%s,"UpdatedAt"=now() WHERE "Id"=%s',
                (int(wallet[1]) + refunded, wallet[0]),
            )
            cur.execute(
                'INSERT INTO "WalletTransactions" '
                '("WalletId","Amount","Kind","Description","IsPaid","CreatedAt") '
                'VALUES (%s,%s,\'charge\',%s,true,now())',
                (wallet[0], refunded, f'برگشت کیف پول سفارش #{order_id}'),
            )
        cur.execute(
            'UPDATE "PaymentReceipts" SET "Status"=\'rejected\',"ReviewedAt"=now() '
            'WHERE "OrderId"=%s AND "Status"=\'pending\'',
            (int(order_id),),
        )
        cur.execute(
            'UPDATE "Orders" SET "WalletPaid"=0,"Status"=\'canceled\','
            '"PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL WHERE "Id"=%s',
            (int(order_id),),
        )
        conn.commit()
        return True, refunded, None


def add_order_item(order_id, product_name, price, qty=1, product_id=None):
    price = checked_amount(price, label='قیمت قلم سفارش')
    qty = checked_amount(qty, maximum=1_000, label='تعداد قلم سفارش')
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
    if login_password or backup_code:
        raise ValueError(
            'ذخیره رمز یا کد بازیابی به‌صورت متن ساده ممنوع است؛ از خزانه رمزگذاری استفاده کن.'
        )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "GemOrderInfo" '
            '("OrderId", "OrderItemId", "GemPackageId", "PurchaseType", "TelegramId", '
            '"GameUID", "PlayerName", "LoginMethod", "LoginEmail") '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING "Id"',
            (
                order_id, order_item_id, gem_package_id, purchase_type, str(telegram_id),
                game_uid, player_name, login_method, login_email,
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
    """Delivery rows including the persisted provider submission state."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT g."Id", g."GemPackageId", g."GameUID", g."PlayerName", '
            'p."AutoDeliver", p."G2BulkCatalogueName", g."G2BulkOrderId", '
            'p."Amount", COALESCE(g."G2BulkStatus",\'\') '
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


def apply_g2bulk_webhook(
    local_order_id, info_id, provider_order_id, player_id, status,
    player_name='',
):
    """Apply one authenticated terminal callback without submitting an order."""
    local_order_id = int(local_order_id)
    info_id = int(info_id)
    provider_order_id = str(provider_order_id or '').strip()
    player_id = str(player_id or '').strip()
    status = str(status or '').strip().upper()
    if status == 'CANCELED':
        status = 'FAILED'
    if status not in ('COMPLETED', 'FAILED'):
        return False, 'non-terminal status'
    if not provider_order_id or not player_id:
        return False, 'provider order id and player id are required'

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT g."GameUID",g."G2BulkOrderId",g."G2BulkStatus",'
            'o."PaymentVerifiedAt",o."PaymentRefId",o."Status" '
            'FROM "GemOrderInfo" g JOIN "Orders" o ON o."Id"=g."OrderId" '
            'WHERE g."Id"=%s AND g."OrderId"=%s FOR UPDATE OF g,o',
            (info_id, local_order_id),
        )
        row = cur.fetchone()
        if not row:
            return False, 'local item not found'
        (
            game_uid, stored_provider_id, existing_g2_status,
            verified_at, payment_ref, order_status,
        ) = row
        if str(stored_provider_id or '').strip() != provider_order_id:
            return False, 'provider order id mismatch'
        if str(game_uid or '').strip() != player_id:
            return False, 'player id mismatch'
        if not verified_at or not str(payment_ref or '').strip():
            return False, 'payment is not verified'
        # Terminal success is monotonic: a delayed/reordered FAILED webhook
        # must never downgrade a delivery that was already confirmed.
        if (
            str(existing_g2_status or '').upper() == 'COMPLETED'
            or order_status in ('delivered', 'completed')
        ):
            return True, 'delivered'

        cur.execute(
            'UPDATE "GemOrderInfo" SET "G2BulkStatus"=%s,'
            '"PlayerName"=COALESCE(NULLIF(%s,\'\'),"PlayerName") '
            'WHERE "Id"=%s',
            (status, str(player_name or ''), info_id),
        )
        if status == 'FAILED':
            # اگر همه‌ی اقلام اتوماتیک رد شده‌اند، ریفاند کن
            cur.execute(
                'SELECT '
                'COUNT(*) FILTER (WHERE p."AutoDeliver"=true),'
                'COUNT(*) FILTER (WHERE p."AutoDeliver"=true '
                'AND COALESCE(g."G2BulkStatus",\'\') IN '
                '(\'FAILED\',\'REJECTED\',\'CANCELED\',\'CANCELLED\')),'
                'COUNT(*) FILTER (WHERE p."AutoDeliver"=true '
                'AND COALESCE(g."G2BulkStatus",\'\')=\'COMPLETED\') '
                'FROM "GemOrderInfo" g '
                'JOIN "GemPackages" p ON p."Id"=g."GemPackageId" '
                'WHERE g."OrderId"=%s',
                (local_order_id,),
            )
            total_auto, failed_auto, completed_auto = cur.fetchone()
            conn.commit()
            if (
                total_auto
                and int(failed_auto or 0) == int(total_auto)
                and int(completed_auto or 0) == 0
            ):
                ok_refund, refunded = _refund_failed_order(local_order_id)
                if ok_refund:
                    return True, f'refunded:{int(refunded)}'
            return True, 'failed'

        cur.execute(
            'SELECT '
            'COUNT(*) FILTER (WHERE p."AutoDeliver"=true),'
            'COUNT(*) FILTER (WHERE p."AutoDeliver"=true '
            'AND g."G2BulkStatus"=\'COMPLETED\'),'
            'COUNT(*) FILTER (WHERE p."AutoDeliver"=false) '
            'FROM "GemOrderInfo" g '
            'JOIN "GemPackages" p ON p."Id"=g."GemPackageId" '
            'WHERE g."OrderId"=%s',
            (local_order_id,),
        )
        total_auto, completed_auto, total_manual = cur.fetchone()
        if total_auto and completed_auto == total_auto and not total_manual:
            cur.execute(
                'UPDATE "Orders" SET "Status"=\'delivered\' WHERE "Id"=%s',
                (local_order_id,),
            )
            result = 'delivered'
        elif total_auto and completed_auto == total_auto:
            cur.execute(
                'UPDATE "Orders" SET "Status"=\'paid\' WHERE "Id"=%s',
                (local_order_id,),
            )
            result = 'paid'
        else:
            cur.execute(
                'UPDATE "Orders" SET "Status"=\'processing\' WHERE "Id"=%s '
                'AND "Status" NOT IN (\'delivered\',\'completed\')',
                (local_order_id,),
            )
            result = order_status
        conn.commit()
        return True, result


def record_gem_profit_snapshot(info_id, order_id, supplier_cost_usd, rate_info):
    """هزینه و نرخ همان لحظه را یک‌بار و بدون امکان بازنویسی ذخیره می‌کند."""
    try:
        cost_usd = Decimal(str(supplier_cost_usd))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if cost_usd <= 0:
        return False
    rate_info = rate_info or {}
    rate = rate_info.get('rate') if rate_info.get('ok') else None
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT o."TotalAmount",o."DiscountAmount",oi."Price",oi."Quantity",'
                '(SELECT COALESCE(SUM(x."Price"*x."Quantity"),0) '
                ' FROM "OrderItems" x WHERE x."OrderId"=o."Id") '
                'FROM "GemOrderInfo" g '
                'JOIN "Orders" o ON o."Id"=g."OrderId" '
                'JOIN "OrderItems" oi ON oi."Id"=g."OrderItemId" '
                'WHERE g."Id"=%s AND o."Id"=%s',
                (int(info_id), int(order_id)),
            )
            row = cur.fetchone()
            if not row or int(row[4] or 0) <= 0:
                return False
            net_sale = int(row[0]) - int(row[1] or 0)
            item_subtotal = int(row[2]) * int(row[3])
            sale_amount = profitability.allocate_sale_amount(
                net_sale, item_subtotal, int(row[4])
            )
            supplier_toman = gross_profit = None
            if rate:
                supplier_toman, gross_profit = profitability.calculate_gross_profit(
                    sale_amount, cost_usd, int(rate)
                )
            cur.execute(
                'INSERT INTO "OrderProfitSnapshots" '
                '("OrderId","GemOrderInfoId","SaleAmountToman","SupplierCostUsd",'
                '"UsdTomanRate","SupplierCostToman","GrossProfitToman","FxSource",'
                '"FxObservedMs","CapturedAt") '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) '
                'ON CONFLICT ("GemOrderInfoId") DO NOTHING',
                (
                    int(order_id), int(info_id), sale_amount, cost_usd,
                    int(rate) if rate else None, supplier_toman, gross_profit,
                    str(rate_info.get('source') or 'rate_unavailable')[:80],
                    rate_info.get('observed_ms'),
                ),
            )
            conn.commit()
            return cur.rowcount == 1
    except Exception:
        _LOG.exception(
            "Could not persist profit snapshot for order=%s info=%s",
            order_id,
            info_id,
        )
        return False


def profit_report_stats():
    """سود ناخالص تحقق‌یافته؛ فقط سفارش G2 تکمیل‌شده با snapshot دقیق."""
    with get_conn() as conn, conn.cursor() as cur:
        condition = (
            'g."G2BulkStatus"=\'COMPLETED\' '
            'AND p."GrossProfitToman" IS NOT NULL'
        )
        cur.execute(
            'SELECT COUNT(*),COALESCE(SUM(p."SaleAmountToman"),0),'
            'COALESCE(SUM(p."SupplierCostToman"),0),'
            'COALESCE(SUM(p."GrossProfitToman"),0),'
            'COALESCE(SUM(p."SupplierCostUsd"),0) '
            'FROM "OrderProfitSnapshots" p '
            'JOIN "GemOrderInfo" g ON g."Id"=p."GemOrderInfoId" '
            f'WHERE {condition}'
        )
        all_time = cur.fetchone()
        cur.execute(
            'SELECT COUNT(*),COALESCE(SUM(p."SaleAmountToman"),0),'
            'COALESCE(SUM(p."SupplierCostToman"),0),'
            'COALESCE(SUM(p."GrossProfitToman"),0) '
            'FROM "OrderProfitSnapshots" p '
            'JOIN "GemOrderInfo" g ON g."Id"=p."GemOrderInfoId" '
            f'WHERE {condition} AND p."CapturedAt">=now()-interval \'30 days\''
        )
        month = cur.fetchone()
        cur.execute(
            'SELECT COUNT(*) FROM "GemOrderInfo" g '
            'JOIN "Orders" o ON o."Id"=g."OrderId" '
            'LEFT JOIN "OrderProfitSnapshots" p ON p."GemOrderInfoId"=g."Id" '
            'WHERE g."G2BulkStatus"=\'COMPLETED\' '
            'AND o."PaymentVerifiedAt" IS NOT NULL '
            'AND (p."Id" IS NULL OR p."GrossProfitToman" IS NULL)'
        )
        missing = int(cur.fetchone()[0] or 0)
        return {
            'count': int(all_time[0] or 0),
            'sales': int(all_time[1] or 0),
            'cost': int(all_time[2] or 0),
            'profit': int(all_time[3] or 0),
            'cost_usd': Decimal(str(all_time[4] or 0)),
            'month_count': int(month[0] or 0),
            'month_sales': int(month[1] or 0),
            'month_cost': int(month[2] or 0),
            'month_profit': int(month[3] or 0),
            'missing': missing,
        }


def list_profit_snapshots(limit=20):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT p."OrderId",gp."Amount",p."SaleAmountToman",'
            'p."SupplierCostUsd",p."UsdTomanRate",p."SupplierCostToman",'
            'p."GrossProfitToman",p."FxSource",p."CapturedAt",g."G2BulkStatus" '
            'FROM "OrderProfitSnapshots" p '
            'JOIN "GemOrderInfo" g ON g."Id"=p."GemOrderInfoId" '
            'JOIN "GemPackages" gp ON gp."Id"=g."GemPackageId" '
            'ORDER BY p."Id" DESC LIMIT %s',
            (max(1, min(int(limit), 50)),),
        )
        return cur.fetchall()


def get_order_items(order_id):
    """(Id, ProductName, Price, Quantity, ProductId)"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "ProductName", "Price", "Quantity", "ProductId" '
            'FROM "OrderItems" WHERE "OrderId"=%s',
            (order_id,),
        )
        return cur.fetchall()


def is_sense_order(order_id) -> bool:
    items = get_order_items(order_id)
    return any('پک سنس' in (it[1] or '') for it in items)


def _reserve_manual_gem(info_id, package_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT COALESCE("G2BulkStatus",\'\') FROM "GemOrderInfo" '
            'WHERE "Id"=%s FOR UPDATE',
            (int(info_id),),
        )
        row = cur.fetchone()
        if not row:
            return False
        if row[0] == 'MANUAL_PENDING':
            return True
        if row[0] == 'MANUAL_RESERVED':
            cur.execute(
                'UPDATE "GemOrderInfo" SET "G2BulkStatus"=\'MANUAL_PENDING\','
                '"CredentialStatus"=CASE WHEN "PurchaseType"=\'by_credentials\' '
                'THEN \'ready\' ELSE "CredentialStatus" END '
                'WHERE "Id"=%s AND "G2BulkStatus"=\'MANUAL_RESERVED\'',
                (int(info_id),),
            )
            conn.commit()
            return cur.rowcount == 1
        cur.execute(
            'UPDATE "GemPackages" SET "Stock"="Stock"-1 '
            'WHERE "Id"=%s AND COALESCE("AutoDeliver",false)=false AND "Stock">0',
            (int(package_id),),
        )
        if cur.rowcount != 1:
            return False
        cur.execute(
            'UPDATE "GemOrderInfo" SET "G2BulkStatus"=\'MANUAL_PENDING\','
            '"CredentialStatus"=CASE WHEN "PurchaseType"=\'by_credentials\' '
            'THEN \'ready\' ELSE "CredentialStatus" END WHERE "Id"=%s',
            (int(info_id),),
        )
        conn.commit()
        return True


def _refund_failed_order(order_id):
    """برگرداندن مبلغ سفارش ردشده به کیف پول و لغو امن سفارش.

    خروجی: (ok, refunded_amount)
    - ok=False یعنی سفارش پیدا نشد / قابل ریفاند نبود
    - refunded_amount می‌تواند ۰ باشد اگر قبلاً ریفاند شده بود (idempotent)
    """
    order_id = int(order_id)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                '''SELECT "Id","UserId","Status","TotalAmount","DiscountAmount",
                      "WalletPaid","PaymentMethod","PaymentExpectedAmount",
                      "PaymentVerifiedAt","TelegramId"
                   FROM "Orders" WHERE "Id"=%s FOR UPDATE''',
                (order_id,),
            )
            order = cur.fetchone()
            if not order:
                return False, 0
            (
                _id, user_db_id, status, _total, _discount, wallet_paid,
                payment_method, expected, verified_at, telegram_id,
            ) = order
            if status in ('delivered', 'completed'):
                return False, 0
            if status in ('cancelled', 'canceled'):
                # قبلاً لغو شده — مبلغ ریفاند قبلی را برگردان (برای پیام‌ها)
                cur.execute(
                    '''SELECT COALESCE(SUM(wt."Amount"),0)
                       FROM "WalletTransactions" wt
                       JOIN "Wallets" wa ON wa."Id"=wt."WalletId"
                       WHERE wa."UserId"=%s AND wt."Kind"='charge'
                         AND (wt."Description"=%s OR wt."Description"=%s)''',
                    (
                        user_db_id,
                        f'برگشت تحویل ناموفق سفارش #{order_id}',
                        f'لغو توسط ادمین سفارش #{order_id}',
                    ),
                )
                return True, int(cur.fetchone()[0] or 0)

            total_paid = int(wallet_paid or 0)
            if payment_method != 'wallet' and expected and verified_at:
                total_paid += int(expected)

            refund_desc = f'برگشت تحویل ناموفق سفارش #{order_id}'
            cur.execute(
                '''SELECT COALESCE(SUM(wt."Amount"),0)
                   FROM "WalletTransactions" wt
                   JOIN "Wallets" wa ON wa."Id"=wt."WalletId"
                   WHERE wa."UserId"=%s AND wt."Kind"='charge'
                     AND (wt."Description"=%s OR wt."Description"=%s)''',
                (
                    user_db_id, refund_desc,
                    f'لغو توسط ادمین سفارش #{order_id}',
                ),
            )
            already = int(cur.fetchone()[0] or 0)
            refunded = 0
            if already > 0:
                refunded = already
            elif total_paid > 0 and user_db_id:
                cur.execute(
                    '''SELECT "Id","Balance" FROM "Wallets"
                       WHERE "UserId"=%s FOR UPDATE''',
                    (user_db_id,),
                )
                wallet = cur.fetchone()
                if not wallet:
                    cur.execute(
                        '''INSERT INTO "Wallets" ("UserId","Balance","UpdatedAt")
                           VALUES (%s,0,now())
                           ON CONFLICT ("UserId") DO UPDATE
                           SET "UpdatedAt"=now()
                           RETURNING "Id","Balance"''',
                        (user_db_id,),
                    )
                    wallet = cur.fetchone()
                if not wallet:
                    conn.rollback()
                    return False, 0
                wallet_id, balance = wallet
                cur.execute(
                    '''UPDATE "Wallets" SET "Balance"=%s,"UpdatedAt"=now()
                       WHERE "Id"=%s''',
                    (int(balance) + total_paid, wallet_id),
                )
                cur.execute(
                    '''INSERT INTO "WalletTransactions"
                       ("WalletId","Amount","Kind","Description","IsPaid","CreatedAt")
                       VALUES (%s,%s,'charge',%s,true,now())''',
                    (wallet_id, total_paid, refund_desc),
                )
                refunded = total_paid

            _release_manual_gem_reservations(cur, order_id)
            cur.execute(
                '''UPDATE "GemOrderInfo"
                   SET "G2BulkStatus"=CASE
                        WHEN COALESCE("G2BulkStatus",'') IN
                             ('COMPLETED','MANUAL_CANCELLED') THEN "G2BulkStatus"
                        WHEN COALESCE("G2BulkStatus",'') IN
                             ('FAILED','REJECTED','CANCELED','CANCELLED')
                             THEN "G2BulkStatus"
                        ELSE 'FAILED'
                   END,
                   "CredentialStatus"=CASE WHEN "PurchaseType"='by_credentials'
                        THEN 'deleted' ELSE "CredentialStatus" END,
                   "CredentialCiphertext"=CASE WHEN "PurchaseType"='by_credentials'
                        THEN NULL ELSE "CredentialCiphertext" END,
                   "CredentialDeletedAt"=CASE WHEN "PurchaseType"='by_credentials'
                        THEN now() ELSE "CredentialDeletedAt" END
                   WHERE "OrderId"=%s''',
                (order_id,),
            )
            cur.execute(
                '''UPDATE "Orders"
                   SET "Status"='cancelled',"WalletPaid"=0,
                       "PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL,
                       "DeliveryUserNotifiedAt"=NULL,
                       "DeliveryAdminNotifiedAt"=NULL
                   WHERE "Id"=%s''',
                (order_id,),
            )
            conn.commit()
            _LOG.info(
                'Refunded failed order #%s amount=%s telegram=%s',
                order_id, refunded, telegram_id,
            )
            return True, refunded
    except Exception:
        _LOG.exception("Failed to refund failed order %s", order_id)
        return False, 0


def _is_terminal_g2_failure(status):
    return str(status or '').strip().upper() in {
        'FAILED', 'REJECTED', 'CANCELED', 'CANCELLED',
    }


def fulfill_order(order_id):
    """تحویل فقط پس از اثبات پرداخت و با قفل سراسری idempotent برای هر سفارش.

    خروجی: (ok, status)
    statusهای مهم:
      delivered | paid | processing | sense_manual |
      refunded:<amount> | پیام خطا
    """
    order_id = int(order_id)
    lock_namespace = 41827
    with get_conn() as lock_conn, lock_conn.cursor() as lock_cur:
        lock_cur.execute(
            'SELECT pg_try_advisory_lock(%s,%s)',
            (lock_namespace, order_id),
        )
        if not lock_cur.fetchone()[0]:
            return True, 'processing'
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT "Status","PaymentVerifiedAt","PaymentMethod",'
                    '"PaymentExpectedAmount","PaymentRefId",COALESCE("WalletPaid",0) '
                    'FROM "Orders" WHERE "Id"=%s',
                    (order_id,),
                )
                payment = cur.fetchone()
            if not payment:
                return False, 'سفارش پیدا نشد.'
            status, verified_at, method, expected, payment_ref, wallet_paid = payment
            if status in ('delivered', 'completed'):
                return True, 'delivered'
            if status in ('cancelled', 'canceled'):
                ok, refunded = _refund_failed_order(order_id)
                if ok:
                    return False, f'refunded:{int(refunded)}'
                return False, 'سفارش لغو شده است.'
            if not verified_at or not payment_ref:
                return False, 'پرداخت سفارش تأیید نشده است.'
            ok, net_total, payable, error = validate_order_financials(order_id)
            if not ok:
                return False, error
            try:
                expected = checked_amount(expected, label='مبلغ تأییدشده سفارش')
            except ValueError as e:
                return False, str(e)
            if method == 'wallet':
                if wallet_paid != net_total or payable != 0 or expected != net_total:
                    return False, 'اثبات پرداخت کیف پول با مبلغ سفارش تطابق ندارد.'
            elif method in ('zarinpal', 'card_transfer'):
                if payable <= 0 or expected != payable:
                    return False, 'اثبات پرداخت با مانده سفارش تطابق ندارد.'
            else:
                return False, 'روش پرداخت تأییدشده معتبر نیست.'
            update_order_status(order_id, 'processing')

            infos = get_gem_infos_for_order(order_id)
            if not infos and is_sense_order(order_id):
                update_order_status(order_id, 'delivered')
                return True, 'sense_manual'
            if not infos:
                return False, 'سفارش قلم قابل تحویل ندارد.'

            delivered = 0
            total_auto = 0
            processing_auto = 0
            total_manual = 0
            manual_ok = True
            for info in infos:
                (info_id, pkg_id, game_uid, player_name, auto_deliver,
                 catalogue, g2_id, amount, g2_status) = info
                if not auto_deliver:
                    total_manual += 1
                    manual_ok = _reserve_manual_gem(info_id, pkg_id) and manual_ok
                    if manual_ok and str(catalogue or '').startswith('itunes_try:'):
                        supplier_cost = credential_cost_for_package(amount)
                        if supplier_cost:
                            record_gem_profit_snapshot(
                                info_id, order_id, supplier_cost,
                                profitability.get_purchase_rate_snapshot(),
                            )
                    continue
                total_auto += 1
                g2_status = str(g2_status or '').strip().upper()
                if g2_id:
                    live = g2bulk.get_game_order_status(g2_id)
                    if live.get('ok'):
                        live_status = str(live.get('status') or '').upper()
                        update_gem_g2bulk(
                            info_id, status=live_status,
                            player_name=live.get('player_name') or player_name,
                        )
                        if live_status == 'COMPLETED':
                            delivered += 1
                        elif live_status in ('PENDING', 'PROCESSING'):
                            processing_auto += 1
                        elif _is_terminal_g2_failure(live_status):
                            pass
                        else:
                            processing_auto += 1
                    else:
                        # وضعیت زنده نامشخص — صبر کن، دوباره تلاش نشود به‌عنوان fail
                        if _is_terminal_g2_failure(g2_status):
                            pass
                        else:
                            processing_auto += 1
                    continue
                if g2_status in ('SUBMITTING', 'SUBMIT_UNKNOWN', 'FAILED'):
                    recovered = g2bulk.find_game_order_by_remark(
                        f'Atomic Bot order #{order_id}'
                    )
                    if recovered.get('found'):
                        recovered_status = str(
                            recovered.get('status') or 'PENDING'
                        ).upper()
                        update_gem_g2bulk(
                            info_id,
                            order_id_g2=recovered['order_id'],
                            status=recovered_status,
                            player_name=(
                                recovered.get('player_name') or player_name
                            ),
                        )
                        if recovered_status == 'COMPLETED':
                            delivered += 1
                        elif recovered_status in ('PENDING', 'PROCESSING'):
                            processing_auto += 1
                        elif _is_terminal_g2_failure(recovered_status):
                            pass
                        else:
                            processing_auto += 1
                    else:
                        # هنوز معلوم نیست سفارش سمت تأمین ثبت شده یا نه
                        processing_auto += 1
                    continue
                if _is_terminal_g2_failure(g2_status):
                    # رد قطعی قبلی — دوباره به G2B نفرست
                    continue
                if not game_uid or not g2bulk.is_supported_catalogue(
                    amount, catalogue or str(amount)
                ):
                    update_gem_g2bulk(info_id, status='FAILED')
                    continue
                if not claim_gem_submission(info_id):
                    processing_auto += 1
                    continue
                purchase_rate = profitability.get_purchase_rate_snapshot()
                result = g2bulk.place_game_order(
                    catalogue_name=catalogue or str(amount),
                    player_id=game_uid,
                    remark=f'Atomic Bot order #{order_id}',
                    idempotency_key=g2bulk.idempotency_key(order_id, info_id),
                    callback_url=g2bulk.build_callback_url(order_id, info_id),
                )
                if result.get('ok') and result.get('order_id'):
                    api_status = str(result.get('status') or 'PENDING').upper()
                    update_gem_g2bulk(
                        info_id,
                        order_id_g2=result['order_id'],
                        status=api_status,
                        player_name=result.get('player_name') or player_name,
                    )
                    supplier_cost = result.get('cost_usd')
                    if not supplier_cost:
                        _available, supplier_cost, _balance, _error = (
                            g2bulk.can_fulfill(
                                amount, catalogue or str(amount), force=False
                            )
                        )
                    if supplier_cost:
                        record_gem_profit_snapshot(
                            info_id, order_id, supplier_cost,
                            purchase_rate,
                        )
                    if api_status == 'COMPLETED':
                        delivered += 1
                    elif _is_terminal_g2_failure(api_status):
                        pass
                    else:
                        processing_auto += 1
                else:
                    if result.get('uncertain'):
                        update_gem_g2bulk(info_id, status='SUBMIT_UNKNOWN')
                        processing_auto += 1
                    else:
                        update_gem_g2bulk(info_id, status='REJECTED')

            if total_auto and delivered == total_auto and manual_ok:
                if total_manual:
                    update_order_status(order_id, 'paid')
                    return True, 'paid'
                update_order_status(order_id, 'delivered')
                return True, 'delivered'
            if total_auto == 0 and manual_ok:
                update_order_status(order_id, 'paid')
                return True, 'paid'
            if delivered or processing_auto or (total_manual and manual_ok):
                update_order_status(order_id, 'processing')
                return True, 'processing'
            # تمام تحویل‌های اتوماتیک ناموفق — مبلغ را به کیف پول برگردان
            ok_refund, refunded = _refund_failed_order(order_id)
            if ok_refund:
                return False, f'refunded:{int(refunded)}'
            return False, 'تحویل خودکار ناموفق بود و بازپرداخت انجام نشد.'
        finally:
            lock_cur.execute(
                'SELECT pg_advisory_unlock(%s,%s)',
                (lock_namespace, order_id),
            )


# ─── Admin manual order management ─────────────────────────────────────────────
def admin_mark_order_delivered(order_id):
    """سفارشی که ادمین دستی تحویل داده را terminal 'delivered' کن.

    Idempotent است: روی سفارش delivered/completed دوباره اجرا شود خطا نمی‌سازد.
    فقط از وضعیت paid/processing به delivered منتقل می‌کند و ردیف‌های GemOrderInfo
    را COMPLETED می‌کند تا از لیست «تحویل ناموفق» حذف شوند. از همان advisory lock
    فلو تحویل استفاده می‌کند تا با reconcile همزمان race نشود.
    خروجی: (ok, status) که status ∈ {'delivered','already','refused'}
    """
    order_id = int(order_id)
    lock_namespace = 41827
    with get_conn() as lock_conn, lock_conn.cursor() as lock_cur:
        lock_cur.execute(
            'SELECT pg_try_advisory_lock(%s,%s)',
            (lock_namespace, order_id),
        )
        if not lock_cur.fetchone()[0]:
            return False, 'busy'
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT "Status" FROM "Orders" WHERE "Id"=%s FOR UPDATE',
                    (order_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False, 'not_found'
                status = row[0]
                if status in ('delivered', 'completed'):
                    return True, 'already'
                if status not in ('paid', 'processing'):
                    return False, 'refused'
                cur.execute(
                    'UPDATE "Orders" SET "Status"=\'delivered\' WHERE "Id"=%s',
                    (order_id,),
                )
                cur.execute(
                    'UPDATE "GemOrderInfo" SET "G2BulkStatus"=\'COMPLETED\','
                    '"CredentialStatus"=CASE WHEN "PurchaseType"=\'by_credentials\' '
                    'THEN \'completed\' ELSE "CredentialStatus" END,'
                    '"CredentialCiphertext"=CASE WHEN "PurchaseType"=\'by_credentials\' '
                    'THEN NULL ELSE "CredentialCiphertext" END,'
                    '"CredentialDeletedAt"=CASE WHEN "PurchaseType"=\'by_credentials\' '
                    'THEN now() ELSE "CredentialDeletedAt" END '
                    'WHERE "OrderId"=%s',
                    (order_id,),
                )
                conn.commit()
                return True, 'delivered'
        finally:
            lock_cur.execute(
                'SELECT pg_advisory_unlock(%s,%s)',
                (lock_namespace, order_id),
            )


def admin_cancel_stuck_order(order_id):
    """لغو ایمن سفارش گیرکرده و بازپرداخت مبلغ به کیف پول کاربر.

    فقط روی وضعیت pending/paid/processing اجرا می‌شود؛ سفارش نهایی‌شده
    (delivered/completed/cancelled/canceled) را رد می‌کند تا بازپرداخت دوباره
    رخ ندهد. مبلغ بازپرداخت از ستون‌های خود "Orders" محاسبه می‌شود:
      - WalletPaid: سهم کیف پول (همان لحظه کسر می‌شود)
      - PaymentExpectedAmount: سهم درگاه/کارت‌به‌کارت فقط اگر پرداخت تایید شده باشد
    خروجی: (ok, refunded, error)
    """
    order_id = int(order_id)
    lock_namespace = 41827
    with get_conn() as lock_conn, lock_conn.cursor() as lock_cur:
        lock_cur.execute(
            'SELECT pg_try_advisory_lock(%s,%s)',
            (lock_namespace, order_id),
        )
        if not lock_cur.fetchone()[0]:
            return False, 0, 'سفارش در حال پردازش است؛ چند لحظه بعد دوباره تلاش کن.'
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT "Id","UserId","Status","PaymentMethod","WalletPaid",'
                    '"PaymentExpectedAmount","PaymentVerifiedAt" '
                    'FROM "Orders" WHERE "Id"=%s FOR UPDATE',
                    (order_id,),
                )
                order = cur.fetchone()
                if not order:
                    return False, 0, 'سفارش پیدا نشد.'
                _oid, user_db_id, status, method, wallet_paid, expected, verified_at = order
                if status in ('delivered', 'completed', 'cancelled', 'canceled'):
                    return False, 0, 'این سفارش قبلاً نهایی شده است.'
                if status not in ('pending', 'paid', 'processing'):
                    return False, 0, 'وضعیت سفارش قابل لغو نیست.'

                # محاسبه مبلغ قابل بازپرداخت.
                # - سهم کیف پول همیشه برمی‌گردد چون همان لحظه کسر شده است.
                # - سهم درگاه/کارت فقط وقتی که پرداخت تایید شده باشد (verified).
                #   برای سفارش wallet خالص، PaymentExpectedAmount=WalletPaid است و
                #   تکرار نمی‌شود (دوبار بازپرداخت نشود).
                total_paid = int(wallet_paid or 0)
                if method != 'wallet' and expected and verified_at:
                    total_paid += int(expected)

                # Idempotency: اگر برای این سفارش از قبل بازپرداختی ثبت شده
                # (برگشت تحویل ناموفق یا لغو ادمین)، دوباره بازپرداخت نکن؛ فقط
                # سفارش را لغو کن تا کاربر دو بار پول نگیرد.
                cur.execute(
                    '''SELECT 1 FROM "WalletTransactions" wt
                       JOIN "Wallets" wa ON wa."Id"=wt."WalletId"
                       WHERE wa."UserId"=%s
                         AND wt."Kind"='charge'
                         AND (wt."Description"=%s OR wt."Description"=%s)
                       LIMIT 1''',
                    (user_db_id,
                     f'برگشت تحویل ناموفق سفارش #{order_id}',
                     f'لغو توسط ادمین سفارش #{order_id}'),
                )
                if cur.fetchone():
                    total_paid = 0

                # بازپرداخت به کیف پول
                refunded = 0
                if total_paid > 0:
                    cur.execute(
                        'SELECT "Id","Balance" FROM "Wallets" '
                        'WHERE "UserId"=%s FOR UPDATE',
                        (user_db_id,),
                    )
                    wallet = cur.fetchone()
                    if not wallet:
                        cur.execute(
                            'INSERT INTO "Wallets" ("UserId","Balance","UpdatedAt") '
                            'VALUES (%s,0,now()) '
                            'ON CONFLICT ("UserId") DO UPDATE SET "UpdatedAt"=now() '
                            'RETURNING "Id","Balance"',
                            (user_db_id,),
                        )
                        wallet = cur.fetchone()
                    if not wallet:
                        return False, 0, 'کیف پول کاربر پیدا نشد.'
                    cur.execute(
                        'UPDATE "Wallets" SET "Balance"=%s,"UpdatedAt"=now() '
                        'WHERE "Id"=%s',
                        (int(wallet[1]) + total_paid, wallet[0]),
                    )
                    cur.execute(
                        'INSERT INTO "WalletTransactions" '
                        '("WalletId","Amount","Kind","Description","IsPaid","CreatedAt") '
                        'VALUES (%s,%s,\'charge\',%s,true,now())',
                        (wallet[0], total_paid, f'لغو توسط ادمین سفارش #{order_id}'),
                    )
                    refunded = total_paid

                _release_manual_gem_reservations(cur, order_id)

                cur.execute(
                    'UPDATE "GemOrderInfo" SET "G2BulkStatus"=\'MANUAL_CANCELLED\','
                    '"CredentialStatus"=CASE WHEN "PurchaseType"=\'by_credentials\' '
                    'THEN \'deleted\' ELSE "CredentialStatus" END,'
                    '"CredentialCiphertext"=CASE WHEN "PurchaseType"=\'by_credentials\' '
                    'THEN NULL ELSE "CredentialCiphertext" END,'
                    '"CredentialDeletedAt"=CASE WHEN "PurchaseType"=\'by_credentials\' '
                    'THEN now() ELSE "CredentialDeletedAt" END '
                    'WHERE "OrderId"=%s',
                    (order_id,),
                )
                cur.execute(
                    'UPDATE "Orders" SET "Status"=\'cancelled\',"WalletPaid"=0,'
                    '"PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL '
                    'WHERE "Id"=%s',
                    (order_id,),
                )
                conn.commit()
                return True, refunded, None
        finally:
            lock_cur.execute(
                'SELECT pg_advisory_unlock(%s,%s)',
                (lock_namespace, order_id),
            )


def order_refund_amount(order_id):
    """مبلغی که برای این سفارش قبلاً به کیف پول برگشته (اگر برگشته باشد).

    برای اینکه ادمین وقتی سفارش تحویل‌شده ثبت می‌کند بداند آیا باید موجودی
    کیف پول کاربر را دوباره کم کند (وقتی پول قبلاً برگشت ولی محصول هم تحویل شد).
    """
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                '''SELECT COALESCE(SUM(wt."Amount"),0)
                   FROM "WalletTransactions" wt
                   JOIN "Wallets" wa ON wa."Id"=wt."WalletId"
                   WHERE wa."UserId"=(
                     SELECT "UserId" FROM "Orders" WHERE "Id"=%s
                   )
                     AND wt."Kind"='charge'
                     AND (wt."Description"=%s OR wt."Description"=%s)''',
                (int(order_id),
                 f'برگشت تحویل ناموفق سفارش #{order_id}',
                 f'لغو توسط ادمین سفارش #{order_id}'),
            )
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0
    except Exception:
        return 0


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
    amount = checked_amount(amount, label='مبلغ شارژ کیف پول')
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
    amount = checked_amount(amount, label='مبلغ برداشت کیف پول')
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
    amount = checked_amount(amount, label='مبلغ شارژ کیف پول')
    authority = str(authority or '').strip()
    if not authority or len(authority) > 100:
        raise ValueError('کد تراکنش شارژ نامعتبر است.')
    wallet_id, _ = get_or_create_wallet(user_db_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "WalletTransactions" '
            '("WalletId", "Amount", "Kind", "Description", "Authority", "IsPaid", '
            '"PaymentExpectedAmount", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, %s, false, %s, now()) RETURNING "Id"',
            (
                wallet_id, amount, 'charge',
                f'شارژ کیف پول {amount:,} تومان', authority, amount,
            ),
        )
        tx_id = cur.fetchone()[0]
        conn.commit()
        return tx_id


def complete_wallet_charge_by_authority(authority, verified_amount=None, ref_id=None):
    """شارژ اتمیک کیف پول فقط با مبلغ و کد پیگیری تأییدشده زرین‌پال."""
    authority = str(authority or '').strip()
    ref_id = str(ref_id or '').strip()
    if not authority or authority.startswith('wcard_') or not ref_id:
        return False, None, 0, 0
    try:
        verified_amount = checked_amount(
            verified_amount, label='مبلغ تأییدشده شارژ کیف پول'
        )
    except ValueError:
        return False, None, 0, 0
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","WalletId","Amount","IsPaid","PaymentExpectedAmount",'
            '"PaymentVerifiedAt","PaymentRefId" FROM "WalletTransactions" '
            'WHERE "Authority"=%s AND "Kind"=\'charge\' FOR UPDATE',
            (authority,),
        )
        row = cur.fetchone()
        if not row:
            return False, None, 0, 0
        tx_id, wallet_id, amount, is_paid, expected, verified_at, stored_ref = row
        try:
            amount = checked_amount(amount, label='مبلغ شارژ کیف پول')
        except ValueError:
            return False, None, 0, 0
        if int(expected or 0) != amount or verified_amount != amount:
            return False, None, 0, 0
        cur.execute(
            'SELECT "UserId","Balance" FROM "Wallets" WHERE "Id"=%s FOR UPDATE',
            (wallet_id,),
        )
        wallet = cur.fetchone()
        if not wallet:
            return False, None, 0, 0
        user_id, balance = wallet
        if is_paid:
            if verified_at and str(stored_ref or '') == ref_id:
                return True, user_id, amount, balance
            return False, None, 0, 0
        new_bal = balance + amount
        cur.execute(
            'UPDATE "Wallets" SET "Balance"=%s, "UpdatedAt"=now() WHERE "Id"=%s',
            (new_bal, wallet_id),
        )
        cur.execute(
            'UPDATE "WalletTransactions" SET "IsPaid"=true,'
            '"PaymentVerifiedAt"=now(),"PaymentRefId"=%s '
            'WHERE "Id"=%s AND "IsPaid"=false',
            (ref_id[:100], tx_id),
        )
        if cur.rowcount != 1:
            return False, None, 0, 0
        conn.commit()
        return True, user_id, amount, new_bal


def approve_wallet_card_charge(tx_id):
    """شارژ کارت‌به‌کارت فقط با رسید تصویری pending و به‌صورت اتمیک."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "WalletId","Amount","Authority","IsPaid" '
            'FROM "WalletTransactions" WHERE "Id"=%s AND "Kind"=\'charge\' FOR UPDATE',
            (int(tx_id),),
        )
        tx = cur.fetchone()
        if not tx:
            return False, None, 0, 0, 'تراکنش پیدا نشد.'
        wallet_id, amount, authority, is_paid = tx
        if is_paid:
            cur.execute(
                'SELECT "UserId","Balance" FROM "Wallets" WHERE "Id"=%s',
                (wallet_id,),
            )
            wallet = cur.fetchone()
            return True, wallet[0], int(amount), int(wallet[1]), 'already_paid'
        if not str(authority or '').startswith('wcard_'):
            return False, None, 0, 0, 'تراکنش کارت‌به‌کارت نیست.'
        try:
            amount = checked_amount(amount, label='مبلغ شارژ کیف پول')
        except ValueError as e:
            return False, None, 0, 0, str(e)
        cur.execute(
            'SELECT "Id" FROM "PaymentReceipts" '
            'WHERE "WalletTransactionId"=%s AND "Status"=\'pending\' AND "FileId"<>\'\' '
            'ORDER BY "Id" DESC LIMIT 1 FOR UPDATE',
            (int(tx_id),),
        )
        receipt = cur.fetchone()
        if not receipt:
            return False, None, 0, 0, 'رسید تصویری تأییدنشده‌ای وجود ندارد.'
        cur.execute(
            'SELECT "UserId","Balance" FROM "Wallets" WHERE "Id"=%s FOR UPDATE',
            (wallet_id,),
        )
        wallet = cur.fetchone()
        if not wallet:
            return False, None, 0, 0, 'کیف پول پیدا نشد.'
        user_id, balance = wallet
        new_balance = int(balance) + amount
        cur.execute(
            'UPDATE "Wallets" SET "Balance"=%s,"UpdatedAt"=now() WHERE "Id"=%s',
            (new_balance, wallet_id),
        )
        cur.execute(
            'UPDATE "WalletTransactions" SET "IsPaid"=true WHERE "Id"=%s',
            (int(tx_id),),
        )
        cur.execute(
            'UPDATE "PaymentReceipts" SET "Status"=\'approved\',"ReviewedAt"=now() '
            'WHERE "WalletTransactionId"=%s AND "Status"=\'pending\'',
            (int(tx_id),),
        )
        conn.commit()
        return True, user_id, amount, new_balance, 'approved'


def reject_wallet_card_charge(tx_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "IsPaid","Authority" FROM "WalletTransactions" '
            'WHERE "Id"=%s AND "Kind"=\'charge\' FOR UPDATE',
            (int(tx_id),),
        )
        row = cur.fetchone()
        if not row:
            return False, 'تراکنش پیدا نشد.'
        if row[0]:
            return False, 'شارژ قبلاً اعمال شده و قابل رد نیست.'
        if not str(row[1] or '').startswith('wcard_'):
            return False, 'تراکنش کارت‌به‌کارت نیست.'
        cur.execute(
            'UPDATE "WalletTransactions" SET '
            '"Description"=COALESCE("Description",\'\') || \' [rejected]\','
            '"Authority"=\'rejected_\' || "Id"::text WHERE "Id"=%s',
            (int(tx_id),),
        )
        cur.execute(
            'UPDATE "PaymentReceipts" SET "Status"=\'rejected\',"ReviewedAt"=now() '
            'WHERE "WalletTransactionId"=%s AND "Status"=\'pending\'',
            (int(tx_id),),
        )
        conn.commit()
        return True, None


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
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "IsTelegramPremium" BOOLEAN NOT NULL DEFAULT false',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "WalletPaid" INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "PaymentExpectedAmount" INTEGER',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "PaymentVerifiedAt" TIMESTAMPTZ',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "PaymentRefId" VARCHAR(100)',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "DeliveryUserNotifiedAt" TIMESTAMPTZ',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "DeliveryAdminNotifiedAt" TIMESTAMPTZ',
        'ALTER TABLE "WalletTransactions" ADD COLUMN IF NOT EXISTS "PaymentExpectedAmount" INTEGER',
        'ALTER TABLE "WalletTransactions" ADD COLUMN IF NOT EXISTS "PaymentVerifiedAt" TIMESTAMPTZ',
        'ALTER TABLE "WalletTransactions" ADD COLUMN IF NOT EXISTS "PaymentRefId" VARCHAR(100)',
        '''UPDATE "WalletTransactions" SET "PaymentExpectedAmount"="Amount"
           WHERE "PaymentExpectedAmount" IS NULL AND "Kind"='charge' ''',
        '''ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "PaymentExpiresAt"
           TIMESTAMPTZ NOT NULL DEFAULT (now()+interval '15 minutes')''',
        '''UPDATE "Orders" SET "PaymentExpiresAt"="CreatedAt"+interval '15 minutes'
           WHERE "PaymentExpiresAt" IS NULL''',
        '''CREATE INDEX IF NOT EXISTS idx_orders_payment_expiry
           ON "Orders" ("PaymentExpiresAt")
           WHERE "Status"='pending' AND "PaymentVerifiedAt" IS NULL''',
        '''CREATE INDEX IF NOT EXISTS idx_orders_processing_verified
           ON "Orders" ("PaymentVerifiedAt")
           WHERE "Status"='processing' AND "PaymentVerifiedAt" IS NOT NULL''',
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_payment_authority
           ON "Orders" ("PaymentAuthority")
           WHERE "PaymentAuthority" IS NOT NULL AND "PaymentAuthority" <> ''""",
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_transactions_authority
           ON "WalletTransactions" ("Authority")
           WHERE "Authority" IS NOT NULL AND "Authority" <> ''""",
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_transactions_payment_ref
           ON "WalletTransactions" ("PaymentRefId")
           WHERE "PaymentRefId" IS NOT NULL AND "PaymentRefId" <> ''""",
        '''DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_orders_financials') THEN
                ALTER TABLE "Orders" ADD CONSTRAINT ck_orders_financials
                CHECK ("TotalAmount">0 AND "DiscountAmount">=0
                       AND "DiscountAmount"<"TotalAmount" AND "WalletPaid">=0
                       AND ("PaymentExpectedAmount" IS NULL OR "PaymentExpectedAmount">0))
                NOT VALID;
            END IF;
        END $$''',
        '''DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_order_items_financials') THEN
                ALTER TABLE "OrderItems" ADD CONSTRAINT ck_order_items_financials
                CHECK ("Price">0 AND "Quantity">0) NOT VALID;
            END IF;
        END $$''',
        '''DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_wallet_balance_nonnegative') THEN
                ALTER TABLE "Wallets" ADD CONSTRAINT ck_wallet_balance_nonnegative
                CHECK ("Balance">=0) NOT VALID;
            END IF;
        END $$''',
        '''DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_wallet_tx_amount_positive') THEN
                ALTER TABLE "WalletTransactions" ADD CONSTRAINT ck_wallet_tx_amount_positive
                CHECK ("Amount">0) NOT VALID;
            END IF;
        END $$''',
        '''DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_gem_package_financials') THEN
                ALTER TABLE "GemPackages" ADD CONSTRAINT ck_gem_package_financials
                CHECK ("Amount">0 AND "Price">0 AND "Stock">=0) NOT VALID;
            END IF;
        END $$''',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "ReferredById" INTEGER REFERENCES "Users"("Id") ON DELETE SET NULL',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "CardNumber" VARCHAR(32) NOT NULL DEFAULT \'\'',
        'ALTER TABLE "Users" ADD COLUMN IF NOT EXISTS "CardVerified" BOOLEAN NOT NULL DEFAULT false',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "G2BulkSubmittedAt" TIMESTAMPTZ',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "CredentialCiphertext" TEXT',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "CredentialStatus" VARCHAR(30) NOT NULL DEFAULT \'\'',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "CredentialTwoFactorEnabled" BOOLEAN',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "CredentialAdminNote" VARCHAR(500) NOT NULL DEFAULT \'\'',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "CredentialViewedAt" TIMESTAMPTZ',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "CredentialDeletedAt" TIMESTAMPTZ',
        'ALTER TABLE "GemOrderInfo" ADD COLUMN IF NOT EXISTS "CredentialUpdatedAt" TIMESTAMPTZ',
        '''INSERT INTO "GemPackages"
           ("Title","Amount","BonusAmount","Price","OldPrice","PlanType",
            "PurchaseType","AutoDeliver","G2BulkCatalogueName","Stock",
            "IsAvailable","IsActive","SortOrder")
           SELECT seed.title,seed.amount,0,seed.price,NULL,seed.plan_type,
                  'by_credentials',false,seed.catalogue,9999,true,true,seed.sort_order
           FROM (VALUES
             ('📅 عضویت هفتگی فری‌فایر',60,100000,'weekly','itunes_try:60',10),
             ('📆 عضویت ماهانه فری‌فایر',300,500000,'monthly','itunes_try:300',20)
           ) AS seed(title,amount,price,plan_type,catalogue,sort_order)
           WHERE NOT EXISTS (
             SELECT 1 FROM "GemPackages" p
             WHERE p."PurchaseType"='by_credentials'
               AND p."PlanType"=seed.plan_type
           )''',
        'ALTER TABLE "GemPackages" ADD COLUMN IF NOT EXISTS "SortOrder" INTEGER NOT NULL DEFAULT 0',
        '''CREATE TABLE IF NOT EXISTS "BotAdmins" (
            "TelegramId" VARCHAR(64) PRIMARY KEY,
            "Title" VARCHAR(150) NOT NULL DEFAULT '',
            "Role" VARCHAR(20) NOT NULL DEFAULT 'admin',
            "AddedBy" VARCHAR(64),
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "BotSettings" (
            "Key" VARCHAR(100) PRIMARY KEY,
            "Value" TEXT NOT NULL DEFAULT '',
            "UpdatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           VALUES ('credential_support_id','@lookurback',now())
           ON CONFLICT ("Key") DO NOTHING''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           VALUES ('support_id','@omid_1797',now())
           ON CONFLICT ("Key") DO NOTHING''',
        # اجبار یک‌باره آیدی‌های عمومی پشتیبانی
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           SELECT 'support_id','@omid_1797',now()
           WHERE NOT EXISTS (
             SELECT 1 FROM "BotSettings"
             WHERE "Key"='support_ids_public_v1' AND "Value"='1'
           )
           ON CONFLICT ("Key") DO UPDATE
           SET "Value"='@omid_1797',"UpdatedAt"=now()
           WHERE NOT EXISTS (
             SELECT 1 FROM "BotSettings"
             WHERE "Key"='support_ids_public_v1' AND "Value"='1'
           )''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           SELECT 'credential_support_id','@lookurback',now()
           WHERE NOT EXISTS (
             SELECT 1 FROM "BotSettings"
             WHERE "Key"='support_ids_public_v1' AND "Value"='1'
           )
           ON CONFLICT ("Key") DO UPDATE
           SET "Value"='@lookurback',"UpdatedAt"=now()
           WHERE NOT EXISTS (
             SELECT 1 FROM "BotSettings"
             WHERE "Key"='support_ids_public_v1' AND "Value"='1'
           )''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           VALUES ('support_ids_public_v1','1',now())
           ON CONFLICT ("Key") DO UPDATE
           SET "Value"='1',"UpdatedAt"=now()''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           VALUES ('gem_profit_percent','10',now())
           ON CONFLICT ("Key") DO NOTHING''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           VALUES ('credential_weekly_cost_usd','1.328',now())
           ON CONFLICT ("Key") DO NOTHING''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           VALUES ('credential_monthly_cost_usd','6.64',now())
           ON CONFLICT ("Key") DO NOTHING''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           SELECT 'credential_weekly_profit_percent',
                  COALESCE(
                    (SELECT "Value" FROM "BotSettings"
                     WHERE "Key"='credential_profit_percent' LIMIT 1),
                    '40'
                  ),
                  now()
           WHERE NOT EXISTS (
             SELECT 1 FROM "BotSettings"
             WHERE "Key"='credential_weekly_profit_percent'
           )''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           SELECT 'credential_monthly_profit_percent',
                  COALESCE(
                    (SELECT "Value" FROM "BotSettings"
                     WHERE "Key"='credential_profit_percent' LIMIT 1),
                    '40'
                  ),
                  now()
           WHERE NOT EXISTS (
             SELECT 1 FROM "BotSettings"
             WHERE "Key"='credential_monthly_profit_percent'
           )''',
        '''DO $migration$
           BEGIN
             IF NOT EXISTS (
               SELECT 1 FROM "BotSettings"
               WHERE "Key"='delivery_notification_outbox_v1'
             ) THEN
               UPDATE "Orders"
               SET "DeliveryUserNotifiedAt"=COALESCE("DeliveryUserNotifiedAt",now()),
                   "DeliveryAdminNotifiedAt"=COALESCE("DeliveryAdminNotifiedAt",now())
               WHERE "Status" IN ('delivered','completed');

               INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
               VALUES ('delivery_notification_outbox_v1','1',now())
               ON CONFLICT ("Key") DO NOTHING;
             END IF;
           END;
           $migration$''',
        '''DO $migration$
           BEGIN
             IF NOT EXISTS (
               SELECT 1 FROM "BotSettings"
               WHERE "Key"='welcome_text_atomic_shop_v2_applied'
             ) THEN
               INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
               VALUES (
                 'welcome_text',
                 $welcome$✨ به اتومیک شاپ خوش اومدی! ✨

اینجا جاییه که سرعت، امنیت و قیمت مناسب کنار هم جمع شدن تا خرید راحت‌تری داشته باشی 🚀

💎 خرید جم و محصولات بازی
🎯 پک‌های حرفه‌ای سنسیویتی موبایل و PC
💳 پرداخت امن با درگاه یا کارت‌به‌کارت
🎁 کدهای هدیه و تخفیف‌های ویژه
🧑‍💻 پشتیبانی مستقیم و پیگیری سفارش

تمام سفارش‌ها و پرداخت‌های تو از داخل ربات قابل مشاهده و پیگیری هستند.

👇 برای شروع، یکی از گزینه‌های منوی پایین را انتخاب کن.

⚛️ Atomic Shop$welcome$,
                 now()
               )
               ON CONFLICT ("Key") DO UPDATE
               SET "Value"=EXCLUDED."Value", "UpdatedAt"=now();

               INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
               VALUES ('welcome_text_atomic_shop_v2_applied','1',now())
               ON CONFLICT ("Key") DO NOTHING;
             END IF;
           END;
           $migration$''',
        '''CREATE TABLE IF NOT EXISTS "ForcedJoinChannels" (
            "Id" SERIAL PRIMARY KEY,
            "ChatId" VARCHAR(100) UNIQUE NOT NULL,
            "Title" VARCHAR(150) NOT NULL DEFAULT '',
            "InviteUrl" TEXT NOT NULL,
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''INSERT INTO "ForcedJoinChannels" ("ChatId","Title","InviteUrl")
           SELECT '@Omid_AtomicFF','کانال امید اتومیک',
                  'https://t.me/Omid_AtomicFF'
           WHERE NOT EXISTS (
               SELECT 1 FROM "BotSettings" WHERE "Key"='forced_join_initialized'
           ) AND NOT EXISTS (SELECT 1 FROM "ForcedJoinChannels")''',
        '''UPDATE "ForcedJoinChannels"
           SET "Title"='کانال امید اتومیک'
           WHERE "ChatId"='@Omid_AtomicFF'
             AND "Title"='کانال امید اتمیک' ''',
        '''INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
           VALUES ('forced_join_initialized','1',now())
           ON CONFLICT ("Key") DO NOTHING''',
        '''CREATE TABLE IF NOT EXISTS "SensePackages" (
            "Id" SERIAL PRIMARY KEY,
            "Title" VARCHAR(255) NOT NULL,
            "Platform" VARCHAR(20) NOT NULL DEFAULT 'pc',
            "Price" INTEGER NOT NULL,
            "Description" TEXT NOT NULL DEFAULT '',
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "SortOrder" INTEGER NOT NULL DEFAULT 0,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "SupportDepartments" (
            "Id" SERIAL PRIMARY KEY,
            "Title" VARCHAR(150) NOT NULL,
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "ProductCategories" (
            "Id" SERIAL PRIMARY KEY,
            "Title" VARCHAR(150) NOT NULL,
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "SortOrder" INTEGER NOT NULL DEFAULT 0,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "StoreProducts" (
            "Id" SERIAL PRIMARY KEY,
            "CategoryId" INTEGER REFERENCES "ProductCategories"("Id") ON DELETE SET NULL,
            "Title" VARCHAR(255) NOT NULL,
            "Price" INTEGER NOT NULL DEFAULT 0,
            "Stock" INTEGER NOT NULL DEFAULT 0,
            "Description" TEXT NOT NULL DEFAULT '',
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "SortOrder" INTEGER NOT NULL DEFAULT 0,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "PromoCodes" (
            "Id" SERIAL PRIMARY KEY,
            "Code" VARCHAR(80) UNIQUE NOT NULL,
            "CodeType" VARCHAR(20) NOT NULL,
            "Value" INTEGER NOT NULL DEFAULT 0,
            "MaxUses" INTEGER NOT NULL DEFAULT 1,
            "UsedCount" INTEGER NOT NULL DEFAULT 0,
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "PaymentReceipts" (
            "Id" SERIAL PRIMARY KEY,
            "OrderId" INTEGER REFERENCES "Orders"("Id") ON DELETE CASCADE,
            "WalletTransactionId" INTEGER REFERENCES "WalletTransactions"("Id") ON DELETE CASCADE,
            "TelegramId" VARCHAR(64),
            "ReceiptType" VARCHAR(20) NOT NULL DEFAULT 'order',
            "FileId" TEXT NOT NULL DEFAULT '',
            "Text" TEXT NOT NULL DEFAULT '',
            "Status" VARCHAR(20) NOT NULL DEFAULT 'pending',
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
            "ReviewedAt" TIMESTAMPTZ
        )''',
        '''CREATE TABLE IF NOT EXISTS "PaymentAttempts" (
            "Id" BIGSERIAL PRIMARY KEY,
            "OrderId" INTEGER REFERENCES "Orders"("Id") ON DELETE SET NULL,
            "WalletTransactionId" INTEGER REFERENCES "WalletTransactions"("Id") ON DELETE SET NULL,
            "TelegramId" VARCHAR(64),
            "Provider" VARCHAR(30) NOT NULL,
            "Event" VARCHAR(40) NOT NULL,
            "Status" VARCHAR(20) NOT NULL,
            "Amount" INTEGER,
            "Authority" VARCHAR(100),
            "RefId" VARCHAR(100),
            "Message" VARCHAR(500) NOT NULL DEFAULT '',
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "OrderProfitSnapshots" (
            "Id" BIGSERIAL PRIMARY KEY,
            "OrderId" INTEGER NOT NULL REFERENCES "Orders"("Id") ON DELETE CASCADE,
            "GemOrderInfoId" INTEGER UNIQUE NOT NULL
                REFERENCES "GemOrderInfo"("Id") ON DELETE CASCADE,
            "SaleAmountToman" INTEGER NOT NULL CHECK ("SaleAmountToman">0),
            "SupplierCostUsd" NUMERIC(18,6) NOT NULL CHECK ("SupplierCostUsd">0),
            "UsdTomanRate" INTEGER,
            "SupplierCostToman" INTEGER,
            "GrossProfitToman" INTEGER,
            "FxSource" VARCHAR(80) NOT NULL DEFAULT '',
            "FxObservedMs" BIGINT,
            "CapturedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE INDEX IF NOT EXISTS idx_profit_snapshots_captured
           ON "OrderProfitSnapshots" ("CapturedAt" DESC)''',
        '''CREATE INDEX IF NOT EXISTS idx_payment_attempts_status_created
           ON "PaymentAttempts" ("Status", "CreatedAt" DESC)''',
        '''CREATE INDEX IF NOT EXISTS idx_payment_attempts_order
           ON "PaymentAttempts" ("OrderId", "CreatedAt" DESC)''',
        '''CREATE INDEX IF NOT EXISTS idx_payment_receipts_pending
           ON "PaymentReceipts" ("CreatedAt") WHERE "Status"='pending' ''',
        '''CREATE TABLE IF NOT EXISTS "AdminAuditLogs" (
            "Id" BIGSERIAL PRIMARY KEY,
            "AdminTelegramId" VARCHAR(64) NOT NULL,
            "Action" VARCHAR(80) NOT NULL,
            "TargetType" VARCHAR(40) NOT NULL DEFAULT '',
            "TargetId" VARCHAR(100) NOT NULL DEFAULT '',
            "Details" VARCHAR(500) NOT NULL DEFAULT '',
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        'ALTER TABLE "SensePackages" ADD COLUMN IF NOT EXISTS "SortOrder" INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE "ProductCategories" ADD COLUMN IF NOT EXISTS "SortOrder" INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE "StoreProducts" ADD COLUMN IF NOT EXISTS "SortOrder" INTEGER NOT NULL DEFAULT 0',
        '''CREATE INDEX IF NOT EXISTS idx_gem_packages_sort
           ON "GemPackages" ("SortOrder","Id")''',
        '''CREATE INDEX IF NOT EXISTS idx_sense_packages_sort
           ON "SensePackages" ("Platform","SortOrder","Id")''',
        '''CREATE TABLE IF NOT EXISTS "OrderStatusHistory" (
            "Id" BIGSERIAL PRIMARY KEY,
            "OrderId" INTEGER NOT NULL REFERENCES "Orders"("Id") ON DELETE CASCADE,
            "OldStatus" VARCHAR(30),
            "NewStatus" VARCHAR(30) NOT NULL,
            "ChangedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE OR REPLACE FUNCTION record_atomic_order_status_transition()
           RETURNS trigger AS $$
           BEGIN
             IF TG_OP='INSERT' THEN
               INSERT INTO "OrderStatusHistory" ("OrderId","OldStatus","NewStatus")
               VALUES (NEW."Id",NULL,NEW."Status");
             ELSIF OLD."Status" IS DISTINCT FROM NEW."Status" THEN
               INSERT INTO "OrderStatusHistory" ("OrderId","OldStatus","NewStatus")
               VALUES (NEW."Id",OLD."Status",NEW."Status");
             END IF;
             RETURN NEW;
           END;
           $$ LANGUAGE plpgsql''',
        'DROP TRIGGER IF EXISTS trg_atomic_order_status_transition ON "Orders"',
        '''CREATE TRIGGER trg_atomic_order_status_transition
           AFTER INSERT OR UPDATE OF "Status" ON "Orders"
           FOR EACH ROW EXECUTE FUNCTION record_atomic_order_status_transition()''',
        '''CREATE INDEX IF NOT EXISTS idx_admin_audit_created
           ON "AdminAuditLogs" ("CreatedAt" DESC)''',
        'ALTER TABLE "BotAdmins" ADD COLUMN IF NOT EXISTS "Role" VARCHAR(20) NOT NULL DEFAULT \'admin\'',
        'ALTER TABLE "BotAdmins" ADD COLUMN IF NOT EXISTS "AddedBy" VARCHAR(64)',
    ]
    failures = []
    with get_conn() as conn, conn.cursor() as cur:
        for index, sql in enumerate(stmts):
            savepoint = f'schema_patch_{index}'
            cur.execute(f'SAVEPOINT {savepoint}')
            try:
                cur.execute(sql)
            except Exception as e:
                cur.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                failures.append((index, type(e).__name__))
                _LOG.exception('Schema migration step %s failed', index)
            finally:
                cur.execute(f'RELEASE SAVEPOINT {savepoint}')
        if failures:
            raise RuntimeError(
                f'{len(failures)} database migration step(s) failed: {failures}'
            )
        conn.commit()


# ─── تنظیمات و پنل مدیریت توسعه‌یافته ─────────────────────────────────────────
def get_setting(key, default=''):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT "Value" FROM "BotSettings" WHERE "Key"=%s', (str(key),))
            row = cur.fetchone()
            return row[0] if row else default
    except Exception:
        return default


def get_credential_support_contact():
    """آیدی پشتیبان جم با اطلاعات برای نمایش به مشتری.

    اولویت: تنظیم credential_support_id در پنل (پیش‌فرض @lookurback).
    """
    raw = str(get_setting('credential_support_id', '@lookurback') or '@lookurback').strip()
    if not raw:
        raw = '@lookurback'
    if raw.isdigit():
        return {
            'handle': raw,
            'username': '',
            'url': f'https://t.me/user?id={raw}',
            'display': raw,
            'telegram_id': raw,
        }
    if not raw.startswith('@'):
        raw = '@' + raw.lstrip('@')
    username = raw[1:]
    return {
        'handle': raw,
        'username': username,
        'url': f'https://t.me/{username}',
        'display': raw,
        'telegram_id': '',
    }


def get_support_contact():
    """آیدی پشتیبانی عمومی کل ربات (پیش‌فرض @omid_1797)."""
    raw = str(get_setting('support_id', '@omid_1797') or '@omid_1797').strip()
    if not raw:
        raw = '@omid_1797'
    if raw.isdigit():
        return {
            'handle': raw,
            'username': '',
            'url': f'https://t.me/user?id={raw}',
            'display': raw,
        }
    if not raw.startswith('@'):
        raw = '@' + raw.lstrip('@')
    return {
        'handle': raw,
        'username': raw[1:],
        'url': f'https://t.me/{raw[1:]}',
        'display': raw,
    }


def set_credential_support_from_admin(telegram_id, username=''):
    """با ثبت پشتیبان credential، اگر آیدی عمومی خالی بود پر می‌کند؛ در غیر این صورت دست نمی‌زند."""
    current = str(get_setting('credential_support_id', '') or '').strip()
    if current:
        return current
    tg = str(telegram_id or '').strip()
    uname = str(username or '').lstrip('@').strip()
    if not uname:
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT COALESCE(NULLIF("TelegramUsername", \'\'), \'\') '
                    'FROM "Users" WHERE "TelegramId"=%s',
                    (tg,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    uname = str(row[0]).lstrip('@').strip()
        except Exception:
            uname = ''
    value = f'@{uname}' if uname else tg
    if value:
        set_setting('credential_support_id', value)
    return value


def set_setting(key, value):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "BotSettings" ("Key", "Value", "UpdatedAt") VALUES (%s,%s,now()) '
            'ON CONFLICT ("Key") DO UPDATE SET "Value"=EXCLUDED."Value", "UpdatedAt"=now()',
            (str(key), str(value or '')),
        )
        conn.commit()


def list_forced_join_channels(active_only=True):
    where = 'WHERE "IsActive"=true' if active_only else ''
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","ChatId","Title","InviteUrl","IsActive" '
            f'FROM "ForcedJoinChannels" {where} ORDER BY "Id"'
        )
        return cur.fetchall()


def add_forced_join_channel(chat_id, invite_url, title=''):
    chat_id = str(chat_id or '').strip()
    invite_url = str(invite_url or '').strip()
    title = str(title or '').strip()[:150]
    if not chat_id or len(chat_id) > 100:
        raise ValueError('شناسه کانال نامعتبر است.')
    if not invite_url or len(invite_url) > 500:
        raise ValueError('لینک کانال نامعتبر است.')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "ForcedJoinChannels" '
            '("ChatId","Title","InviteUrl","IsActive") VALUES (%s,%s,%s,true) '
            'ON CONFLICT ("ChatId") DO UPDATE SET '
            '"Title"=EXCLUDED."Title","InviteUrl"=EXCLUDED."InviteUrl",'
            '"IsActive"=true RETURNING "Id"',
            (chat_id, title, invite_url),
        )
        channel_id = cur.fetchone()[0]
        conn.commit()
        return channel_id


def remove_forced_join_channel(channel_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'DELETE FROM "ForcedJoinChannels" WHERE "Id"=%s',
            (int(channel_id),),
        )
        conn.commit()
        return cur.rowcount > 0


def get_bool_setting(key, default=True):
    value = str(get_setting(key, '1' if default else '0')).strip().lower()
    return value in ('1', 'true', 'yes', 'on', 'بله', 'فعال')


def list_bot_admins():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "TelegramId", "Title", "IsActive", "CreatedAt", '
            'COALESCE("Role",\'admin\') '
            'FROM "BotAdmins" ORDER BY "CreatedAt"'
        )
        return cur.fetchall()


def is_bot_admin(telegram_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM "BotAdmins" WHERE "TelegramId"=%s AND "IsActive"=true '
                'AND COALESCE("Role",\'admin\')=\'admin\'',
                (str(telegram_id),),
            )
            return cur.fetchone() is not None
    except Exception:
        _LOG.warning("Admin role lookup failed for telegram_id=%s", telegram_id, exc_info=True)
        return False


def is_premium_editor(telegram_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM "BotAdmins" a JOIN "Users" u '
                'ON u."TelegramId"=a."TelegramId" '
                'WHERE a."TelegramId"=%s AND a."IsActive"=true '
                'AND a."Role"=\'premium\' AND u."IsTelegramPremium"=true',
                (str(telegram_id),),
            )
            return cur.fetchone() is not None
    except Exception:
        _LOG.warning(
            "Premium editor role lookup failed for telegram_id=%s",
            telegram_id,
            exc_info=True,
        )
        return False


def is_credential_staff(telegram_id):
    """پشتیبان فقط بخش جم با اطلاعات (Role=credential)."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM "BotAdmins" WHERE "TelegramId"=%s AND "IsActive"=true '
                'AND COALESCE("Role",\'admin\')=\'credential\'',
                (str(telegram_id),),
            )
            return cur.fetchone() is not None
    except Exception:
        _LOG.warning(
            "Credential staff lookup failed for telegram_id=%s",
            telegram_id,
            exc_info=True,
        )
        return False


def list_credential_admins():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "TelegramId", "Title", "IsActive", "CreatedAt", '
            'COALESCE("Role",\'admin\') '
            'FROM "BotAdmins" '
            'WHERE "IsActive"=true AND COALESCE("Role",\'admin\')=\'credential\' '
            'ORDER BY "CreatedAt"'
        )
        return cur.fetchall()


def add_bot_admin(telegram_id, title='', role='admin', added_by=None):
    telegram_id = str(telegram_id or '').strip()
    if not telegram_id.isdigit() or not 1 <= int(telegram_id) < 2 ** 52:
        raise ValueError('شناسه عددی تلگرام معتبر نیست.')
    role = str(role or '').strip().lower()
    if role not in ('admin', 'premium', 'credential'):
        raise ValueError('نقش مدیر معتبر نیست.')
    with get_conn() as conn, conn.cursor() as cur:
        if role == 'premium':
            cur.execute(
                'SELECT "IsTelegramPremium" FROM "Users" WHERE "TelegramId"=%s',
                (telegram_id,),
            )
            user = cur.fetchone()
            if not user:
                raise ValueError('کاربر باید ابتدا ربات را /start کند.')
            if not user[0]:
                raise ValueError(
                    'این حساب در آخرین مراجعه Telegram Premium نبوده است.'
                )
        cur.execute(
            'INSERT INTO "BotAdmins" ("TelegramId","Title","Role","AddedBy","IsActive") '
            'VALUES (%s,%s,%s,%s,true) ON CONFLICT ("TelegramId") DO UPDATE SET '
            '"Title"=EXCLUDED."Title","Role"=EXCLUDED."Role",'
            '"AddedBy"=EXCLUDED."AddedBy","IsActive"=true',
            (telegram_id, str(title or ''), role, str(added_by or '') or None),
        )
        conn.commit()


def remove_bot_admin(telegram_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM "BotAdmins" WHERE "TelegramId"=%s', (str(telegram_id),))
        conn.commit()
        return cur.rowcount > 0


def list_users_filtered(kind='all', limit=50):
    """لیست فیلترشده کاربران برای پنل ادمین.

    خروجی: (TelegramId, FirstName, TelegramUsername, Balance, RefCount, CardNumber)
    """
    limit = max(1, min(int(limit or 50), 100))
    where = ''
    order = 'ORDER BY u."Id" DESC'
    if kind == 'balance':
        where = 'WHERE COALESCE(w."Balance",0)>0'
        order = 'ORDER BY COALESCE(w."Balance",0) DESC, u."Id" DESC'
    elif kind == 'referral':
        where = 'WHERE EXISTS (SELECT 1 FROM "Users" r WHERE r."ReferredById"=u."Id")'
    elif kind == 'card':
        where = 'WHERE COALESCE(u."CardVerified",false)=true'
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                'SELECT u."TelegramId", u."FirstName", COALESCE(u."TelegramUsername",\'\'), '
                'COALESCE(w."Balance",0), '
                '(SELECT COUNT(*) FROM "Users" r WHERE r."ReferredById"=u."Id"), '
                'COALESCE(u."CardNumber",\'\') FROM "Users" u '
                'LEFT JOIN "Wallets" w ON w."UserId"=u."Id" ' + where + ' '
                + order + ' LIMIT %s',
                (limit,),
            )
            return cur.fetchall()
        except Exception:
            # اسکیمای ناقص / ستون قدیمی — حداقل موجودی را برگردان
            _LOG.warning(
                'list_users_filtered(%s) failed; using balance fallback',
                kind,
                exc_info=True,
            )
            try:
                cur.execute(
                    'SELECT u."TelegramId", COALESCE(u."FirstName",\'\'), '
                    'COALESCE(u."TelegramUsername", \'\'), '
                    'COALESCE(w."Balance",0), 0, \'\' '
                    'FROM "Users" u '
                    'JOIN "Wallets" w ON w."UserId"=u."Id" '
                    'WHERE COALESCE(w."Balance",0)>0 '
                    'ORDER BY w."Balance" DESC LIMIT %s',
                    (limit,),
                )
            except Exception:
                cur.execute(
                    'SELECT u."TelegramId", COALESCE(u."FirstName",\'\'), '
                    '\'\', COALESCE(w."Balance",0), 0, \'\' '
                    'FROM "Users" u '
                    'JOIN "Wallets" w ON w."UserId"=u."Id" '
                    'WHERE COALESCE(w."Balance",0)>0 '
                    'ORDER BY w."Balance" DESC LIMIT %s',
                    (limit,),
                )
            return cur.fetchall()


def list_all_telegram_ids():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "TelegramId" FROM "Users" WHERE "TelegramId" IS NOT NULL '
            'AND COALESCE("IsBlocked",false)=false'
        )
        return [r[0] for r in cur.fetchall() if r[0]]


def mass_charge_wallets(amount, description='شارژ همگانی'):
    amount = int(amount)
    if amount <= 0:
        raise ValueError('مبلغ باید بیشتر از صفر باشد.')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "Wallets" ("UserId","Balance","UpdatedAt") '
            'SELECT u."Id",0,now() FROM "Users" u '
            'ON CONFLICT ("UserId") DO NOTHING'
        )
        cur.execute(
            'UPDATE "Wallets" SET "Balance"="Balance"+%s,"UpdatedAt"=now() RETURNING "Id"',
            (amount,),
        )
        wallet_ids = [r[0] for r in cur.fetchall()]
        cur.executemany(
            'INSERT INTO "WalletTransactions" '
            '("WalletId","Amount","Kind","Description","IsPaid","CreatedAt") '
            'VALUES (%s,%s,\'charge\',%s,true,now())',
            [(wid, amount, f'[admin] {description}') for wid in wallet_ids],
        )
        conn.commit()
        return len(wallet_ids)


def get_order_admin(order_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT o."Id",o."TelegramId",o."TotalAmount",o."DiscountAmount",'
            'o."PaymentMethod",o."Status",o."CreatedAt",u."FirstName",'
            'COALESCE(u."TelegramUsername",\'\') FROM "Orders" o '
            'LEFT JOIN "Users" u ON u."Id"=o."UserId" WHERE o."Id"=%s',
            (int(order_id),),
        )
        return cur.fetchone()


def list_pending_receipts(limit=30):
    """فقط سفارش‌هایی که رسید تصویری pending دارند."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT o."Id",o."TelegramId",o."TotalAmount",o."CreatedAt",'
            'r."FileId",r."Id" '
            'FROM "Orders" o '
            'JOIN "PaymentReceipts" r ON r."OrderId"=o."Id" '
            'WHERE o."PaymentMethod"=\'card_transfer\' '
            'AND o."Status"=\'pending\' '
            'AND r."Status"=\'pending\' '
            'AND COALESCE(r."FileId",\'\')<>\'\' '
            'ORDER BY r."Id" DESC LIMIT %s',
            (int(limit),),
        )
        return cur.fetchall()


def list_pending_wallet_card_charges(limit=20):
    """فقط شارژهای کارت‌به‌کارت که رسید تصویری pending دارند."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id", t."Amount", t."Authority", w."UserId", '
            'u."TelegramId", u."FirstName", r."FileId" '
            'FROM "WalletTransactions" t '
            'JOIN "Wallets" w ON w."Id"=t."WalletId" '
            'LEFT JOIN "Users" u ON u."Id"=w."UserId" '
            'JOIN "PaymentReceipts" r ON r."WalletTransactionId"=t."Id" '
            'WHERE t."Kind"=\'charge\' AND t."IsPaid"=false '
            'AND t."Authority" LIKE %s '
            'AND r."Status"=\'pending\' '
            'AND COALESCE(r."FileId",\'\')<>\'\' '
            'ORDER BY t."Id" DESC LIMIT %s',
            ('wcard_%', int(limit)),
        )
        return cur.fetchall()


def save_payment_receipt(order_id=None, wallet_tx_id=None, telegram_id='', file_id='', text=''):
    if (order_id is None) == (wallet_tx_id is None):
        raise ValueError('رسید باید دقیقاً به یک سفارش یا تراکنش متصل باشد.')
    telegram_id = str(telegram_id or '').strip()
    file_id = str(file_id or '').strip()
    if not telegram_id or not file_id:
        raise ValueError('شناسه کاربر و عکس رسید الزامی است.')
    with get_conn() as conn, conn.cursor() as cur:
        if order_id is not None:
            cur.execute(
                'SELECT "Status","PaymentVerifiedAt","PaymentMethod","TelegramId",'
                'COALESCE("PaymentExpiresAt"<=now(),false) '
                'FROM "Orders" WHERE "Id"=%s FOR UPDATE',
                (int(order_id),),
            )
            order = cur.fetchone()
            if not order:
                raise ValueError('سفارش پیدا نشد.')
            if order[0] != 'pending' or order[1]:
                raise ValueError('سفارش دیگر در انتظار پرداخت نیست.')
            if order[2] != 'card_transfer':
                raise ValueError('روش پرداخت سفارش کارت‌به‌کارت نیست.')
            if str(order[3] or '') != telegram_id:
                raise ValueError('سفارش متعلق به این کاربر نیست.')
            if order[4]:
                raise ValueError('مهلت پرداخت سفارش تمام شده است.')
            cur.execute(
                'SELECT 1 FROM "PaymentReceipts" WHERE "OrderId"=%s '
                'AND "Status"=\'pending\' LIMIT 1',
                (int(order_id),),
            )
            if cur.fetchone():
                raise ValueError('برای این سفارش قبلاً رسید در انتظار بررسی ثبت شده است.')
        else:
            cur.execute(
                'SELECT t."IsPaid",t."Authority",u."TelegramId" '
                'FROM "WalletTransactions" t '
                'JOIN "Wallets" w ON w."Id"=t."WalletId" '
                'JOIN "Users" u ON u."Id"=w."UserId" '
                'WHERE t."Id"=%s AND t."Kind"=\'charge\' FOR UPDATE OF t',
                (int(wallet_tx_id),),
            )
            tx = cur.fetchone()
            if not tx:
                raise ValueError('تراکنش شارژ پیدا نشد.')
            if tx[0]:
                raise ValueError('این تراکنش قبلاً پرداخت شده است.')
            if not str(tx[1] or '').startswith('wcard_'):
                raise ValueError('تراکنش مربوط به کارت‌به‌کارت نیست.')
            if str(tx[2] or '') != telegram_id:
                raise ValueError('تراکنش متعلق به این کاربر نیست.')
            cur.execute(
                'SELECT 1 FROM "PaymentReceipts" WHERE "WalletTransactionId"=%s '
                'AND "Status"=\'pending\' LIMIT 1',
                (int(wallet_tx_id),),
            )
            if cur.fetchone():
                raise ValueError('برای این تراکنش قبلاً رسید در انتظار بررسی ثبت شده است.')
        cur.execute(
            'INSERT INTO "PaymentReceipts" '
            '("OrderId","WalletTransactionId","TelegramId","ReceiptType","FileId","Text") '
            'VALUES (%s,%s,%s,%s,%s,%s) RETURNING "Id"',
            (order_id, wallet_tx_id, telegram_id,
             'wallet' if wallet_tx_id else 'order', file_id, text or ''),
        )
        rid = cur.fetchone()[0]
        conn.commit()
        return rid


def log_payment_attempt(*, provider, event, status, amount=None, order_id=None,
                        wallet_tx_id=None, telegram_id='', authority='', ref_id='',
                        message=''):
    """ثبت best-effort رویداد مالی؛ خرابی لاگ نباید مسیر پرداخت را متوقف کند."""
    allowed_statuses = {'pending', 'success', 'failed', 'canceled', 'rejected'}
    status = str(status or '').strip().lower()
    if status not in allowed_statuses:
        status = 'failed'
    safe_amount = None
    if amount not in (None, ''):
        try:
            safe_amount = checked_amount(amount, label='مبلغ رویداد پرداخت')
        except ValueError:
            safe_amount = None
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "PaymentAttempts" '
                '("OrderId","WalletTransactionId","TelegramId","Provider","Event",'
                '"Status","Amount","Authority","RefId","Message","CreatedAt") '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) RETURNING "Id"',
                (
                    int(order_id) if order_id is not None else None,
                    int(wallet_tx_id) if wallet_tx_id is not None else None,
                    str(telegram_id or '')[:64],
                    str(provider or 'unknown')[:30],
                    str(event or 'unknown')[:40],
                    status,
                    safe_amount,
                    str(authority or '')[:100] or None,
                    str(ref_id or '')[:100] or None,
                    str(message or '')[:500],
                ),
            )
            attempt_id = cur.fetchone()[0]
            conn.commit()
            return attempt_id
    except Exception:
        _LOG.exception(
            "Could not persist payment attempt provider=%s event=%s order=%s wallet_tx=%s",
            provider,
            event,
            order_id,
            wallet_tx_id,
        )
        return None


def list_payment_attempts(status=None, limit=30):
    limit = max(1, min(int(limit), 100))
    where = ''
    args = []
    if status:
        where = 'WHERE "Status"=%s'
        args.append(str(status))
    args.append(limit)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","OrderId","WalletTransactionId","TelegramId","Provider",'
            '"Event","Status","Amount","Authority","RefId","Message","CreatedAt" '
            'FROM "PaymentAttempts" ' + where +
            ' ORDER BY "Id" DESC LIMIT %s',
            args,
        )
        return cur.fetchall()


def payment_attempt_stats():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Status",COUNT(*) FROM "PaymentAttempts" '
            'WHERE "CreatedAt">=now()-interval \'30 days\' GROUP BY "Status"'
        )
        counts = {row[0]: int(row[1]) for row in cur.fetchall()}
        cur.execute(
            'SELECT COALESCE(SUM("Amount"),0) FROM "PaymentAttempts" '
            'WHERE "Status"=\'success\' AND "Event" IN '
            '(\'verified\',\'card_approved\',\'wallet_verified\',\'wallet_card_approved\') '
            'AND "CreatedAt">=now()-interval \'30 days\''
        )
        return counts, int(cur.fetchone()[0] or 0)


def log_admin_action(admin_telegram_id, action, target_type='', target_id='', details=''):
    """Best-effort audit trail; logging failure must never change the admin action."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "AdminAuditLogs" '
                '("AdminTelegramId","Action","TargetType","TargetId","Details","CreatedAt") '
                'VALUES (%s,%s,%s,%s,%s,now()) RETURNING "Id"',
                (
                    str(admin_telegram_id or '')[:64],
                    str(action or 'unknown')[:80],
                    str(target_type or '')[:40],
                    str(target_id or '')[:100],
                    str(details or '')[:500],
                ),
            )
            audit_id = cur.fetchone()[0]
            conn.commit()
            return audit_id
    except Exception:
        _LOG.exception(
            "Could not persist admin audit action=%s target_type=%s target_id=%s",
            action,
            target_type,
            target_id,
        )
        return None


def list_admin_actions(limit=30):
    limit = max(1, min(int(limit), 100))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","AdminTelegramId","Action","TargetType","TargetId",'
            '"Details","CreatedAt" FROM "AdminAuditLogs" '
            'ORDER BY "Id" DESC LIMIT %s',
            (limit,),
        )
        return cur.fetchall()


def claim_gem_submission(info_id):
    """Persist the attempt before network I/O so only one caller can submit it."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "GemOrderInfo" SET '
            '"G2BulkStatus"=\'SUBMITTING\',"G2BulkSubmittedAt"=now() '
            'WHERE "Id"=%s AND "G2BulkOrderId" IS NULL '
            'AND COALESCE("G2BulkStatus",\'\')=\'\' '
            'RETURNING "Id"',
            (int(info_id),),
        )
        claimed = bool(cur.fetchone())
        conn.commit()
        return claimed


def reconcile_completed_g2_order(provider_order_id, expected_player_id):
    """Safely attach a confirmed provider order to exactly one paid local order."""
    provider_order_id = str(provider_order_id or '').strip()
    expected_player_id = str(expected_player_id or '').strip()
    if not provider_order_id or not expected_player_id:
        return False, 'شناسه سفارش و Player ID الزامی است.'
    # If the provider id was persisted from the original create response, that
    # exact link is stronger than a paginated history lookup. Verify its live
    # status, then update only the one paid row carrying both ids.
    live = g2bulk.get_game_order_status(provider_order_id)
    if live.get('ok') and live.get('status') == 'COMPLETED':
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT g."Id",g."OrderId",o."Status" '
                'FROM "GemOrderInfo" g '
                'JOIN "Orders" o ON o."Id"=g."OrderId" '
                'WHERE g."G2BulkOrderId"=%s AND g."GameUID"=%s '
                'AND o."PaymentVerifiedAt" IS NOT NULL '
                'AND COALESCE(o."PaymentRefId",\'\')<>\'\' FOR UPDATE',
                (provider_order_id, expected_player_id),
            )
            linked = cur.fetchall()
            if len(linked) != 1:
                conn.rollback()
                return False, (
                    f'تعداد اتصال‌های دقیق محلی {len(linked)} است؛ '
                    'هیچ تغییری انجام نشد.'
                )
            info_id, order_id, order_status = linked[0]
            if order_status in ('delivered', 'completed'):
                conn.rollback()
                return True, (
                    f'سفارش داخلی #{order_id} قبلاً تحویل‌شده ثبت شده است.'
                )
            cur.execute(
                'UPDATE "GemOrderInfo" SET "G2BulkStatus"=\'COMPLETED\','
                '"PlayerName"=COALESCE(NULLIF(%s,\'\'),"PlayerName") '
                'WHERE "Id"=%s',
                (live.get('player_name') or '', info_id),
            )
            cur.execute(
                'UPDATE "Orders" SET "Status"=\'delivered\' WHERE "Id"=%s',
                (order_id,),
            )
            conn.commit()
            return True, (
                f'سفارش داخلی #{order_id} به G2Bulk #{provider_order_id} '
                'متصل و تحویل‌شده ثبت شد.'
            )
    if live.get('ok'):
        return False, f'وضعیت G2Bulk هنوز {live.get("status") or "نامشخص"} است.'

    # Legacy rows without a persisted provider id need the stricter history +
    # player-id match before they may be linked.
    details = g2bulk.get_game_order_details(provider_order_id)
    if not details.get('ok'):
        return False, (
            live.get('error') or details.get('error') or
            'استعلام G2Bulk ناموفق بود.'
        )
    if details.get('status') != 'COMPLETED':
        return False, f'وضعیت G2Bulk هنوز {details.get("status") or "نامشخص"} است.'
    provider_player_id = str(details.get('player_id') or '').strip()
    if provider_player_id != expected_player_id:
        return False, 'Player ID سفارش G2Bulk با مقدار مورد انتظار تطابق ندارد.'

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT g."Id",g."OrderId",g."G2BulkOrderId",o."Status" '
            'FROM "GemOrderInfo" g '
            'JOIN "Orders" o ON o."Id"=g."OrderId" '
            'WHERE (g."G2BulkOrderId"=%s OR (g."G2BulkOrderId" IS NULL '
            'AND g."GameUID"=%s AND COALESCE(g."G2BulkStatus",\'\') '
            'IN (\'FAILED\',\'SUBMITTING\',\'SUBMIT_UNKNOWN\',\'PENDING\','
            '\'PROCESSING\',\'\'))) '
            'AND o."PaymentVerifiedAt" IS NOT NULL '
            'AND COALESCE(o."PaymentRefId",\'\')<>\'\' '
            'ORDER BY (g."G2BulkOrderId"=%s) DESC,g."Id" DESC FOR UPDATE',
            (provider_order_id, expected_player_id, provider_order_id),
        )
        candidates = cur.fetchall()
        exact = [row for row in candidates if str(row[2] or '') == provider_order_id]
        selected = exact or candidates
        if len(selected) != 1:
            conn.rollback()
            return False, (
                f'تعداد رکوردهای محلی منطبق {len(selected)} است؛ '
                'برای جلوگیری از اتصال اشتباه هیچ تغییری انجام نشد.'
            )
        info_id, order_id, _old_provider_id, order_status = selected[0]
        if order_status in ('delivered', 'completed'):
            conn.rollback()
            return True, f'سفارش داخلی #{order_id} قبلاً تحویل‌شده ثبت شده است.'
        cur.execute(
            'UPDATE "GemOrderInfo" SET "G2BulkOrderId"=%s,'
            '"G2BulkStatus"=\'COMPLETED\',"PlayerName"=COALESCE(NULLIF(%s,\'\'),'
            '"PlayerName") WHERE "Id"=%s',
            (provider_order_id, details.get('player_name') or '', info_id),
        )
        cur.execute(
            'UPDATE "Orders" SET "Status"=\'delivered\' WHERE "Id"=%s',
            (order_id,),
        )
        conn.commit()
        return True, (
            f'سفارش داخلی #{order_id} به G2Bulk #{provider_order_id} '
            'متصل و تحویل‌شده ثبت شد.'
        )


def financial_health_snapshot():
    """Small operational snapshot for spotting stuck or inconsistent money flows."""
    queries = {
        'pending_orders': (
            'SELECT COUNT(*) FROM "Orders" WHERE "Status"=\'pending\''
        ),
        'expired_pending_orders': (
            'SELECT COUNT(*) FROM "Orders" WHERE "Status"=\'pending\' '
            'AND "PaymentExpiresAt" IS NOT NULL AND "PaymentExpiresAt"<now()'
        ),
        'processing_orders': (
            'SELECT COUNT(*) FROM "Orders" WHERE "Status"=\'processing\''
        ),
        'verified_pending_orders': (
            'SELECT COUNT(*) FROM "Orders" WHERE "Status"=\'pending\' '
            'AND "PaymentVerifiedAt" IS NOT NULL'
        ),
        'pending_receipts': (
            'SELECT COUNT(*) FROM "PaymentReceipts" WHERE "Status"=\'pending\''
        ),
        'unpaid_wallet_charges': (
            'SELECT COUNT(*) FROM "WalletTransactions" '
            'WHERE "Kind"=\'charge\' AND COALESCE("IsPaid",FALSE)=FALSE'
        ),
        'failed_payments_24h': (
            'SELECT COUNT(*) FROM "PaymentAttempts" WHERE "Status"=\'failed\' '
            'AND "CreatedAt">=now()-interval \'24 hours\''
        ),
        'wallet_mismatches': (
            'SELECT COUNT(*) FROM "Wallets" w WHERE w."Balance" <> COALESCE(('
            'SELECT SUM(CASE WHEN t."Kind"=\'charge\' THEN t."Amount" '
            'WHEN t."Kind"=\'spend\' THEN -t."Amount" ELSE 0 END) '
            'FROM "WalletTransactions" t WHERE t."WalletId"=w."Id" '
            'AND COALESCE(t."IsPaid",false)=true),0)'
        ),
    }
    result = {}
    with get_conn() as conn, conn.cursor() as cur:
        for key, sql in queries.items():
            cur.execute(sql)
            result[key] = int(cur.fetchone()[0] or 0)
    return result


def get_payment_receipt(order_id=None, wallet_tx_id=None, pending_only=True):
    """آخرین رسید ثبت‌شده برای سفارش یا شارژ کیف پول."""
    field = '"OrderId"' if order_id is not None else '"WalletTransactionId"'
    value = order_id if order_id is not None else wallet_tx_id
    if value is None:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        pending_clause = ' AND "Status"=\'pending\'' if pending_only else ''
        cur.execute(
            f'SELECT "Id","TelegramId","FileId","Text","Status","CreatedAt" '
            f'FROM "PaymentReceipts" WHERE {field}=%s'
            f'{pending_clause} '
            f'AND COALESCE("FileId",\'\')<>\'\' '
            f'ORDER BY "Id" DESC LIMIT 1',
            (value,),
        )
        row = cur.fetchone()
        if row or pending_only:
            return row
        cur.execute(
            f'SELECT "Id","TelegramId","FileId","Text","Status","CreatedAt" '
            f'FROM "PaymentReceipts" WHERE {field}=%s '
            f'ORDER BY "Id" DESC LIMIT 1',
            (value,),
        )
        return cur.fetchone()


def mark_receipt_reviewed(order_id=None, wallet_tx_id=None, status='approved'):
    field = '"OrderId"' if order_id is not None else '"WalletTransactionId"'
    value = order_id if order_id is not None else wallet_tx_id
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE "PaymentReceipts" SET "Status"=%s,"ReviewedAt"=now() '
            f'WHERE {field}=%s AND "Status"=\'pending\'',
            (status, value),
        )
        conn.commit()


def get_gem_admin(pk):
    """دریافت بسته جم برای پنل ادمین — حتی اگر IsActive=false باشد."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_GEM_COLS}, "IsActive" FROM "GemPackages" WHERE "Id"=%s',
            (int(pk),),
        )
        return cur.fetchone()


def admin_stats_full():
    stats = get_admin_stats()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT COUNT(DISTINCT "UserId") FROM "Orders" '
            'WHERE "Status" IN (\'paid\',\'processing\',\'delivered\',\'completed\')'
        )
        stats['buyers'] = cur.fetchone()[0]
        cur.execute(
            'SELECT COUNT(*),COALESCE(SUM("TotalAmount"-"DiscountAmount"),0) '
            'FROM "Orders" WHERE "Status" IN (\'paid\',\'processing\',\'delivered\',\'completed\')'
        )
        stats['sales_count'], sales_sum = cur.fetchone()
        stats['sales_sum'] = int(sales_sum or 0)
    return stats


def admin_operations_snapshot(low_stock_threshold=5):
    """خلاصه عملیاتی امروز و هشدارهایی که نیاز به اقدام مدیر دارند."""
    threshold = max(0, min(int(low_stock_threshold), 10_000))
    successful = "('paid','processing','delivered','completed')"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT '
            '(SELECT COUNT(*) FROM "Users" WHERE "DateJoined">=CURRENT_DATE),'
            '(SELECT COUNT(*) FROM "Orders" WHERE "CreatedAt">=CURRENT_DATE),'
            f'(SELECT COUNT(*) FROM "Orders" WHERE "CreatedAt">=CURRENT_DATE '
            f' AND "Status" IN {successful}),'
            f'(SELECT COALESCE(SUM("TotalAmount"-"DiscountAmount"),0) FROM "Orders" '
            f' WHERE "CreatedAt">=CURRENT_DATE AND "Status" IN {successful}),'
            '(SELECT COUNT(*) FROM "PaymentReceipts" WHERE "Status"=\'pending\'),'
            '(SELECT COUNT(*) FROM "Orders" o WHERE o."Status"=\'processing\' '
            ' AND o."PaymentVerifiedAt" IS NOT NULL '
            ' AND o."PaymentVerifiedAt"<now()-interval \'15 minutes\' '
            ' AND NOT EXISTS ('
            '   SELECT 1 FROM "WalletTransactions" wt '
            '   JOIN "Wallets" wa ON wa."Id"=wt."WalletId" '
            '   WHERE wa."UserId"=o."UserId" AND wt."Kind"=\'charge\' '
            '     AND (wt."Description"=(\'برگشت تحویل ناموفق سفارش #\' || o."Id") '
            '          OR wt."Description"=(\'لغو توسط ادمین سفارش #\' || o."Id"))'
            ' )),'
            '(SELECT COUNT(*) FROM "PaymentAttempts" WHERE "Status"=\'failed\' '
            ' AND "CreatedAt">=now()-interval \'24 hours\'),'
            '(SELECT COUNT(*) FROM "SupportTickets" WHERE "Status"=\'open\'),'
            '(SELECT COUNT(*) FROM "GemPackages" WHERE "IsActive"=true '
            ' AND COALESCE("AutoDeliver",false)=false AND "Stock"<=%s),'
            '(SELECT COUNT(*) FROM "StoreProducts" WHERE "IsActive"=true '
            ' AND "Stock"<=%s),'
            '(SELECT COUNT(DISTINCT o."Id") FROM "Orders" o '
            ' JOIN "WalletTransactions" wt ON wt."Kind"=\'charge\' '
            ' JOIN "Wallets" wa ON wa."Id"=wt."WalletId" AND wa."UserId"=o."UserId" '
            ' WHERE (wt."Description"=(\'برگشت تحویل ناموفق سفارش #\' || o."Id") '
            '     OR wt."Description"=(\'لغو توسط ادمین سفارش #\' || o."Id")) '
            ' AND wt."CreatedAt">=now()-interval \'7 days\')',
            (threshold, threshold),
        )
        row = cur.fetchone()
    keys = (
        'new_users_today', 'orders_today', 'sales_today_count',
        'sales_today_amount', 'pending_receipts', 'stuck_processing',
        'failed_payments_24h', 'open_tickets', 'low_gem_stock',
        'low_store_stock', 'wallet_refunds_7d',
    )
    # Older test doubles and databases mid-rolling-upgrade may not yet
    # expose the final wallet-refund metric. Keep the operational panel
    # available and default only that additive metric to zero.
    if len(row) == len(keys) - 1:
        row = tuple(row) + (0,)
    result = dict(zip(keys, row, strict=True))
    for key in keys:
        result[key] = int(result[key] or 0)
    result['low_stock_threshold'] = threshold
    return result


def list_wallet_refunded_orders(limit=20):
    """سفارش‌هایی که مبلغشان به کیف پول برگشته (G2B fail یا لغو ادمین).

    خروجی هر ردیف:
      id, telegram_id, total_amount, status, payment_method,
      refunded_amount, refund_kind, refunded_at, g2_statuses
    """
    limit = max(1, min(int(limit), 50))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''SELECT o."Id", o."TelegramId", o."TotalAmount", o."Status",
                      o."PaymentMethod",
                      SUM(wt."Amount") AS refunded_amount,
                      CASE
                        WHEN bool_or(wt."Description" LIKE 'لغو توسط ادمین%%')
                          THEN 'admin'
                        ELSE 'g2b'
                      END AS refund_kind,
                      MAX(wt."CreatedAt") AS refunded_at,
                      COALESCE(
                        string_agg(DISTINCT COALESCE(g."G2BulkStatus", '-'), ','),
                        '-'
                      ) AS g2_statuses
               FROM "Orders" o
               JOIN "Wallets" wa ON wa."UserId"=o."UserId"
               JOIN "WalletTransactions" wt ON wt."WalletId"=wa."Id"
                 AND wt."Kind"='charge'
                 AND (wt."Description"=('برگشت تحویل ناموفق سفارش #' || o."Id")
                      OR wt."Description"=('لغو توسط ادمین سفارش #' || o."Id"))
               LEFT JOIN "GemOrderInfo" g ON g."OrderId"=o."Id"
               GROUP BY o."Id"
               ORDER BY MAX(wt."CreatedAt") DESC
               LIMIT %s''',
            (limit,),
        )
        return cur.fetchall()


def count_wallet_refunded_orders(days=7):
    days = max(1, min(int(days), 90))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''SELECT COUNT(DISTINCT o."Id")
               FROM "Orders" o
               JOIN "Wallets" wa ON wa."UserId"=o."UserId"
               JOIN "WalletTransactions" wt ON wt."WalletId"=wa."Id"
                 AND wt."Kind"='charge'
                 AND (wt."Description"=('برگشت تحویل ناموفق سفارش #' || o."Id")
                      OR wt."Description"=('لغو توسط ادمین سفارش #' || o."Id"))
                 AND wt."CreatedAt">=now()-(%s * interval '1 day')''',
            (days,),
        )
        return int(cur.fetchone()[0] or 0)


def list_stuck_processing_orders(limit=30, older_minutes=15):
    """سفارش پرداخت‌شده‌ای که بیش از حد در processing مانده است.

    سفارش‌هایی که پولشان به کیف پول برگشته (لغو/ریفاند) اینجا نمی‌آیند.
    """
    limit = max(1, min(int(limit), 100))
    older_minutes = max(5, min(int(older_minutes), 24 * 60))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT o."Id",o."TelegramId",o."TotalAmount",o."PaymentMethod",'
            'o."PaymentVerifiedAt",'
            'COALESCE(string_agg(DISTINCT COALESCE(g."G2BulkStatus",\'-\'),\',\'),\'-\') '
            'FROM "Orders" o '
            'LEFT JOIN "GemOrderInfo" g ON g."OrderId"=o."Id" '
            'WHERE o."Status"=\'processing\' AND o."PaymentVerifiedAt" IS NOT NULL '
            'AND o."PaymentVerifiedAt"<now()-(%s * interval \'1 minute\') '
            'AND NOT EXISTS ('
            '  SELECT 1 FROM "WalletTransactions" wt '
            '  JOIN "Wallets" wa ON wa."Id"=wt."WalletId" '
            '  WHERE wa."UserId"=o."UserId" AND wt."Kind"=\'charge\' '
            '    AND (wt."Description"=(\'برگشت تحویل ناموفق سفارش #\' || o."Id") '
            '         OR wt."Description"=(\'لغو توسط ادمین سفارش #\' || o."Id"))'
            ') '
            'GROUP BY o."Id" ORDER BY o."PaymentVerifiedAt" LIMIT %s',
            (older_minutes, limit),
        )
        return cur.fetchall()


def close_orders_already_refunded(limit=50):
    """اگر پول برگشته ولی وضعیت هنوز paid/processing است، سفارش را لغو کن.

    خروجی: تعداد سفارش‌هایی که وضعیت‌شان اصلاح شد.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''UPDATE "Orders" o
               SET "Status"='cancelled',"WalletPaid"=0,
                   "PaymentAuthority"=NULL,"PaymentExpectedAmount"=NULL
               WHERE o."Id" IN (
                 SELECT o2."Id" FROM "Orders" o2
                 WHERE o2."Status" IN ('paid','processing')
                   AND EXISTS (
                     SELECT 1 FROM "WalletTransactions" wt
                     JOIN "Wallets" wa ON wa."Id"=wt."WalletId"
                     WHERE wa."UserId"=o2."UserId" AND wt."Kind"='charge'
                       AND (wt."Description"=('برگشت تحویل ناموفق سفارش #' || o2."Id")
                            OR wt."Description"=('لغو توسط ادمین سفارش #' || o2."Id"))
                   )
                 ORDER BY o2."Id"
                 LIMIT %s
               )
               RETURNING o."Id"''',
            (max(1, min(int(limit), 200)),),
        )
        rows = cur.fetchall()
        conn.commit()
        return [int(r[0]) for r in rows]


def list_low_stock_items(threshold=5, limit=50):
    """موجودی‌های کمِ قابل اقدام؛ بسته‌های خودکار از موجودی G2 جدا هستند."""
    threshold = max(0, min(int(threshold), 10_000))
    limit = max(1, min(int(limit), 100))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT \'gem\',"Id","Title","Stock" FROM "GemPackages" '
            'WHERE "IsActive"=true AND COALESCE("AutoDeliver",false)=false '
            'AND "Stock"<=%s ORDER BY "Stock","Id" LIMIT %s',
            (threshold, limit),
        )
        rows = list(cur.fetchall())
        remaining = max(0, limit - len(rows))
        if remaining:
            cur.execute(
                'SELECT \'product\',"Id","Title","Stock" FROM "StoreProducts" '
                'WHERE "IsActive"=true AND "Stock"<=%s '
                'ORDER BY "Stock","Id" LIMIT %s',
                (threshold, remaining),
            )
            rows.extend(cur.fetchall())
        return rows


def list_sense_packages(platform=None, active_only=False):
    where, args = [], []
    if platform:
        where.append('"Platform"=%s')
        args.append(platform)
    if active_only:
        where.append('"IsActive"=true')
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","Title","Platform","Price","Description","IsActive" '
            'FROM "SensePackages"' + clause + ' ORDER BY "Platform","SortOrder","Id"',
            args,
        )
        return cur.fetchall()


def get_sense_package(package_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","Title","Platform","Price","Description","IsActive" '
            'FROM "SensePackages" WHERE "Id"=%s', (int(package_id),)
        )
        return cur.fetchone()


def add_sense_package(title, platform, price, description=''):
    price = checked_amount(price, label='قیمت بسته')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "SensePackages" '
            '("Title","Platform","Price","Description","SortOrder") '
            'VALUES (%s,%s,%s,%s,(SELECT COALESCE(MAX("SortOrder"),0)+10 '
            'FROM "SensePackages" WHERE "Platform"=%s)) RETURNING "Id"',
            (title, platform, price, description or '', platform),
        )
        value = cur.fetchone()[0]
        conn.commit()
        return value


def update_sense_package(package_id, field, value):
    allowed = {'Title', 'Platform', 'Price', 'Description', 'IsActive', 'SortOrder'}
    if field not in allowed:
        raise ValueError('فیلد نامعتبر')
    if field == 'Price':
        value = checked_amount(value, label='قیمت بسته')
    elif field == 'SortOrder':
        value = max(0, int(value))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'UPDATE "SensePackages" SET "{field}"=%s WHERE "Id"=%s',
                    (value, int(package_id)))
        conn.commit()


def add_gem_package(title, amount, price, stock=9999):
    amount = checked_amount(amount, maximum=1_000_000, label='مقدار جم')
    price = checked_amount(price, label='قیمت بسته')
    stock = int(stock)
    if stock < 0:
        raise ValueError('موجودی نمی‌تواند منفی باشد')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "GemPackages" '
            '("Title","Amount","BonusAmount","Price","PlanType","PurchaseType",'
            '"AutoDeliver","G2BulkCatalogueName","Stock","IsAvailable","IsActive","SortOrder") '
            'VALUES (%s,%s,0,%s,\'once\',\'by_id\',true,%s,%s,true,true,'
            '(SELECT COALESCE(MAX("SortOrder"),0)+10 FROM "GemPackages")) RETURNING "Id"',
            (title, amount, price, str(amount), stock),
        )
        value = cur.fetchone()[0]
        conn.commit()
        return value


def update_gem_package(package_id, field, value):
    allowed = {'Title', 'Amount', 'Price', 'Stock', 'IsAvailable', 'IsActive',
               'G2BulkCatalogueName', 'AutoDeliver', 'SortOrder'}
    if field not in allowed:
        raise ValueError('فیلد نامعتبر')
    if field == 'Amount':
        value = checked_amount(value, maximum=1_000_000, label='مقدار جم')
    elif field == 'Price':
        value = checked_amount(value, label='قیمت بسته')
    elif field == 'Stock':
        value = int(value)
        if value < 0:
            raise ValueError('موجودی نمی‌تواند منفی باشد')
    elif field == 'SortOrder':
        value = max(0, int(value))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'UPDATE "GemPackages" SET "{field}"=%s WHERE "Id"=%s',
                    (value, int(package_id)))
        conn.commit()


def admin_list_gems():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_GEM_COLS}, "IsActive" FROM "GemPackages" '
            'WHERE "PurchaseType"=\'by_id\' ORDER BY "SortOrder","Id"'
        )
        return cur.fetchall()


def simple_list(table, columns):
    allowed = {
        'SupportDepartments', 'ProductCategories', 'StoreProducts', 'PromoCodes'
    }
    if table not in allowed:
        raise ValueError('جدول نامعتبر')
    cols = ','.join(f'"{c}"' for c in columns)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT {cols} FROM "{table}" ORDER BY "Id" DESC LIMIT 100')
        return cur.fetchall()


def add_department(title):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO "SupportDepartments" ("Title") VALUES (%s) RETURNING "Id"',
                    (title,))
        value = cur.fetchone()[0]
        conn.commit()
        return value


def add_category(title):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO "ProductCategories" ("Title") VALUES (%s) RETURNING "Id"',
                    (title,))
        value = cur.fetchone()[0]
        conn.commit()
        return value


def add_store_product(title, price, stock=0, category_id=None, description=''):
    price = checked_amount(price, label='قیمت محصول')
    stock = int(stock)
    if stock < 0:
        raise ValueError('موجودی نمی‌تواند منفی باشد')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "StoreProducts" '
            '("Title","Price","Stock","CategoryId","Description") VALUES (%s,%s,%s,%s,%s) '
            'RETURNING "Id"',
            (title, price, stock, category_id, description or ''),
        )
        value = cur.fetchone()[0]
        conn.commit()
        return value


def get_store_product(product_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","Title","Price","Stock","Description","IsActive","CategoryId" '
            'FROM "StoreProducts" WHERE "Id"=%s',
            (int(product_id),),
        )
        return cur.fetchone()


def update_store_product(product_id, field, value):
    allowed = {'Title', 'Price', 'Stock', 'Description', 'IsActive', 'CategoryId'}
    if field not in allowed:
        raise ValueError('فیلد نامعتبر')
    if field == 'Price':
        value = checked_amount(value, label='قیمت محصول')
    elif field == 'Stock':
        value = int(value)
        if value < 0:
            raise ValueError('موجودی نمی‌تواند منفی باشد')
    elif field == 'IsActive':
        value = bool(value)
    elif field == 'CategoryId':
        value = None if value in (None, '', '0', '-') else int(value)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE "StoreProducts" SET "{field}"=%s WHERE "Id"=%s',
            (value, int(product_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def get_promo_code(promo_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","Code","CodeType","Value","MaxUses","UsedCount","IsActive" '
            'FROM "PromoCodes" WHERE "Id"=%s',
            (int(promo_id),),
        )
        return cur.fetchone()


def update_promo_code(promo_id, field, value):
    allowed = {'IsActive', 'MaxUses', 'Value'}
    if field not in allowed:
        raise ValueError('فیلد نامعتبر')
    if field == 'IsActive':
        value = bool(value)
    elif field == 'MaxUses':
        value = checked_amount(value, maximum=1_000_000, label='تعداد استفاده')
    elif field == 'Value':
        value = int(value)
        if value < 0:
            raise ValueError('مقدار نامعتبر است')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE "PromoCodes" SET "{field}"=%s WHERE "Id"=%s',
            (value, int(promo_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def add_promo_code(code, code_type, value, max_uses=1):
    code_type = str(code_type or '').strip().lower()
    max_uses = checked_amount(max_uses, maximum=1_000_000, label='تعداد استفاده')
    if code_type == 'discount':
        value = checked_amount(value, maximum=99, label='درصد تخفیف')
    elif code_type == 'gift':
        value = checked_amount(value, label='مبلغ هدیه')
    else:
        raise ValueError('نوع کد معتبر نیست')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "PromoCodes" ("Code","CodeType","Value","MaxUses") '
            'VALUES (%s,%s,%s,%s) RETURNING "Id"',
            (code.strip().upper(), code_type, value, max_uses),
        )
        result = cur.fetchone()[0]
        conn.commit()
        return result


def delete_simple_record(table, record_id):
    allowed = {'SupportDepartments', 'ProductCategories', 'StoreProducts', 'PromoCodes'}
    if table not in allowed:
        raise ValueError('جدول نامعتبر')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'DELETE FROM "{table}" WHERE "Id"=%s', (int(record_id),))
        conn.commit()
        return cur.rowcount > 0


def sync_gem_prices():
    """کاتالوگ تاییدشده را یک‌بار همگام کن و سپس قیمت‌های ادمین را حفظ کن."""
    catalogue = (
        ('🎯 لول‌آپ سطح 6', 6, 65_000, 'Level Up Package - Level 6'),
        ('🎯 لول‌آپ سطح 10', 10, 110_000, 'Level Up Package - Level 10'),
        ('🎯 لول‌آپ سطح 15', 15, 110_000, 'Level Up Package - Level 15'),
        ('🎯 لول‌آپ سطح 20', 20, 110_000, 'Level Up Package - Level 20'),
        ('🎯 لول‌آپ سطح 25', 25, 110_000, 'Level Up Package - Level 25'),
        ('🎯 لول‌آپ سطح 30', 30, 172_000, 'Level Up Package - Level 30'),
        ('💎 110 جم', 110, 191_000, '110'),
        ('💎 231 جم', 231, 382_000, '231'),
        ('📅 بسته هفتگی', 90_001, 430_000, 'Weekly Membership'),
        ('🏆 بویاه پس', 90_002, 640_000, 'Booyah Pass'),
        ('💎 583 جم', 583, 956_000, '583'),
        ('💎 1188 جم', 1188, 1_913_000, '1188'),
        ('📆 بسته ماهانه', 90_003, 2_106_000, 'Monthly Membership'),
        ('💎 2420 جم', 2420, 3_824_000, '2420'),
    )
    marker = 'g2bulk_catalogue_14_20260727'
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT 1 FROM "BotSettings" WHERE "Key"=%s', (marker,))
        if not cur.fetchone():
            for title, amount, price, supplier_name in catalogue:
                cur.execute(
                    'UPDATE "GemPackages" SET '
                    '"Title"=%s,"Amount"=%s,"Price"=%s,"PlanType"=\'once\','
                    '"PurchaseType"=\'by_id\',"AutoDeliver"=true,'
                    '"Stock"=9999,"IsAvailable"=true,"IsActive"=true '
                    'WHERE "G2BulkCatalogueName"=%s',
                    (title, amount, price, supplier_name),
                )
                if cur.rowcount:
                    continue
                cur.execute(
                    'INSERT INTO "GemPackages" '
                    '("Title","Amount","BonusAmount","Price","PlanType","PurchaseType",'
                    '"AutoDeliver","G2BulkCatalogueName","Stock","IsAvailable","IsActive") '
                    'VALUES (%s,%s,0,%s,\'once\',\'by_id\',true,%s,9999,true,true)',
                    (title, amount, price, supplier_name),
                )
            cur.execute(
                'INSERT INTO "BotSettings" ("Key","Value","UpdatedAt") '
                'VALUES (%s,\'1\',now()) ON CONFLICT ("Key") DO NOTHING',
                (marker,),
            )
        title_marker = 'g2bulk_catalogue_titles_fa_v2_20260727'
        cur.execute(
            'SELECT 1 FROM "BotSettings" WHERE "Key"=%s', (title_marker,)
        )
        if not cur.fetchone():
            for title, _amount, _price, supplier_name in catalogue:
                cur.execute(
                    'UPDATE "GemPackages" SET "Title"=%s '
                    'WHERE "G2BulkCatalogueName"=%s',
                    (title, supplier_name),
                )
            cur.execute(
                'INSERT INTO "BotSettings" ("Key","Value","UpdatedAt") '
                'VALUES (%s,\'1\',now()) ON CONFLICT ("Key") DO NOTHING',
                (title_marker,),
            )
        package_title_marker = 'g2bulk_package_titles_fa_v3_20260728'
        cur.execute(
            'SELECT 1 FROM "BotSettings" WHERE "Key"=%s',
            (package_title_marker,),
        )
        if not cur.fetchone():
            cur.execute(
                'UPDATE "GemPackages" SET "Title"=%s '
                'WHERE "G2BulkCatalogueName"=%s',
                ('📅 بسته هفتگی', 'Weekly Membership'),
            )
            cur.execute(
                'UPDATE "GemPackages" SET "Title"=%s '
                'WHERE "G2BulkCatalogueName"=%s',
                ('📆 بسته ماهانه', 'Monthly Membership'),
            )
            cur.execute(
                'INSERT INTO "BotSettings" ("Key","Value","UpdatedAt") '
                'VALUES (%s,\'1\',now()) ON CONFLICT ("Key") DO NOTHING',
                (package_title_marker,),
            )
        cur.execute('SELECT COUNT(*) FROM "SensePackages"')
        if cur.fetchone()[0] == 0:
            cur.executemany(
                'INSERT INTO "SensePackages" '
                '("Title","Platform","Price","Description","IsActive") VALUES (%s,%s,%s,%s,true)',
                [
                    ('پک سنس PC', 'pc', 1_000_000, 'پک سنس مخصوص سیستم PC'),
                    ('پک سنس PC + خدمات', 'pc', 2_200_000, 'پک سنس PC همراه با خدمات'),
                    ('پک سنس موبایل', 'mobile', 450_000, 'پک سنس مخصوص موبایل'),
                    ('پک سنس موبایل + خدمات', 'mobile', 850_000, 'پک سنس موبایل همراه با خدمات'),
                ],
            )
        # اگر فقط PC وجود دارد، پک موبایل را هم اضافه کن (بدون دست زدن به پک‌های فعلی)
        mobile_seed_marker = 'sense_mobile_seed_v1_20260808'
        cur.execute(
            'SELECT 1 FROM "BotSettings" WHERE "Key"=%s', (mobile_seed_marker,)
        )
        if not cur.fetchone():
            cur.execute(
                'SELECT COUNT(*) FROM "SensePackages" WHERE "Platform"=\'mobile\''
            )
            if cur.fetchone()[0] == 0:
                cur.executemany(
                    'INSERT INTO "SensePackages" '
                    '("Title","Platform","Price","Description","IsActive") '
                    'VALUES (%s,%s,%s,%s,true)',
                    [
                        ('پک سنس موبایل', 'mobile', 450_000, 'پک سنس مخصوص موبایل'),
                        (
                            'پک سنس موبایل + خدمات',
                            'mobile',
                            850_000,
                            'پک سنس موبایل همراه با خدمات',
                        ),
                    ],
                )
            cur.execute(
                'INSERT INTO "BotSettings" ("Key","Value","UpdatedAt") '
                'VALUES (%s,\'1\',now()) ON CONFLICT ("Key") DO NOTHING',
                (mobile_seed_marker,),
            )
        conn.commit()


def compute_gem_sale_price(cost_usd, usd_toman_rate_value, profit_percent=10):
    """قیمت فروش هر بسته جم با سود مشخص — گرد شده به نزدیک‌ترین هزار تومان."""
    from decimal import Decimal, ROUND_HALF_UP

    cost = Decimal(str(cost_usd))
    rate = Decimal(str(usd_toman_rate_value))
    profit = Decimal(1) + (Decimal(profit_percent) / Decimal(100))
    raw = (cost * rate * profit).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return (int(raw) // 1000 + (1 if int(raw) % 1000 else 0)) * 1000


CREDENTIAL_COST_USD = {
    60: Decimal('1.328'),
    300: Decimal('6.64'),
}


def get_credential_pricing_config():
    """بهای دلاری و درصد سود هفتگی/ماهانه جم با اطلاعات (قابل تنظیم از پنل)."""
    from decimal import Decimal, InvalidOperation

    legacy_profit = str(get_setting('credential_profit_percent', '40') or '40')

    def _profit(key):
        raw = get_setting(key, legacy_profit) or legacy_profit
        try:
            return max(1, min(200, int(str(raw).replace('%', '').replace('٪', ''))))
        except (TypeError, ValueError):
            return 40

    def _cost(key, default):
        raw = str(get_setting(key, default) or default).replace(',', '').strip()
        try:
            value = Decimal(raw)
        except (InvalidOperation, TypeError, ValueError):
            value = Decimal(default)
        if value < Decimal('0.01') or value > Decimal('1000'):
            value = Decimal(default)
        return value

    return {
        'weekly_cost': _cost('credential_weekly_cost_usd', '1.328'),
        'monthly_cost': _cost('credential_monthly_cost_usd', '6.64'),
        'weekly_profit': _profit('credential_weekly_profit_percent'),
        'monthly_profit': _profit('credential_monthly_profit_percent'),
    }


def credential_cost_for_package(amount=None, plan_type=None):
    """هزینه دلاری تأمین برای بسته جم با اطلاعات."""
    cfg = get_credential_pricing_config()
    plan = str(plan_type or '').strip().lower()
    try:
        amt = int(amount or 0)
    except (TypeError, ValueError):
        amt = 0
    if plan == 'weekly' or amt == 60:
        return cfg['weekly_cost']
    if plan == 'monthly' or amt == 300:
        return cfg['monthly_cost']
    return CREDENTIAL_COST_USD.get(amt)


def credential_profit_for_package(amount=None, plan_type=None):
    """درصد سود فروش برای بسته جم با اطلاعات."""
    cfg = get_credential_pricing_config()
    plan = str(plan_type or '').strip().lower()
    try:
        amt = int(amount or 0)
    except (TypeError, ValueError):
        amt = 0
    if plan == 'weekly' or amt == 60:
        return cfg['weekly_profit']
    if plan == 'monthly' or amt == 300:
        return cfg['monthly_profit']
    return cfg['monthly_profit']


def sync_gem_prices_daily(_force=False):
    """همگام‌سازی قیمت با نرخ دلار و درصد سود مستقل هر روش خرید.

    اگر کمتر از ۲۴ ساعت از آخرین اجرا گذشته باشد و _force نباشد، چیزی
    انجام نمی‌دهد. تعداد آیتم‌های به‌روزرسانی‌شده را برمی‌گرداند.
    """
    from datetime import datetime, timezone
    try:
        open_db_pool()
    except Exception:
        _LOG.warning("gem price sync skipped: DB unavailable")
        return 0

    # بررسی آخرین اجرا
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "Value" FROM "BotSettings" WHERE "Key"=%s',
                ("gem_price_last_sync",),
            )
            row = cur.fetchone()
    except Exception:
        _LOG.warning("gem price sync: last-sync check failed", exc_info=True)
        return 0
    if not _force and row and row[0]:
        try:
            last = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - last).total_seconds() < 24 * 3600:
                return 0
        except (TypeError, ValueError):
            pass

    # دریافت نرخ دلار
    try:
        from profitability import get_usd_toman_rate

        rate_result = get_usd_toman_rate(force=True)
        if not rate_result.get("ok"):
            _LOG.warning("gem price sync: USD rate fetch failed: %s", rate_result.get("error"))
            return 0
        rate_value = int(rate_result["rate"])
    except Exception:
        _LOG.warning("gem price sync: USD rate error", exc_info=True)
        return 0

    # دریافت کاتالوگ G2Bulk (برای جم با آیدی) — شکست آن مانع به‌روز شدن
    # جم با اطلاعات (بهای ثابت پنل) نمی‌شود.
    prices_by_name = {}
    try:
        import g2bulk as g2

        snapshot = g2.get_inventory_snapshot(force=True)
        if not snapshot.get("ok"):
            _LOG.warning("gem price sync: G2Bulk snapshot failed: %s", snapshot.get("error"))
        else:
            prices_by_name = snapshot.get("prices_by_name") or {}
    except Exception:
        _LOG.warning("gem price sync: G2Bulk error", exc_info=True)

    # سود جم با آیدی و سود/بهای هفتگی و ماهانهٔ جم با اطلاعات
    profit_percent = 10
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "Value" FROM "BotSettings" WHERE "Key"=%s',
                ("gem_profit_percent",),
            )
            row = cur.fetchone()
        if row and row[0]:
            profit_percent = max(1, min(200, int(row[0])))
    except Exception:
        _LOG.warning("gem price sync: profit setting read failed", exc_info=True)

    cred_cfg = get_credential_pricing_config()

    # به‌روزرسانی قیمت در دیتابیس
    updated = 0
    matched = 0
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT "Id","Price","G2BulkCatalogueName","PurchaseType",
                          "Amount","PlanType"
                   FROM "GemPackages"
                   WHERE "IsActive"=true AND "G2BulkCatalogueName" IS NOT NULL
                   AND "G2BulkCatalogueName"<>''"""
            )
            for (gem_id, current_price, catalogue_name,
                 purchase_type, amount, plan_type) in cur.fetchall():
                if purchase_type == 'by_credentials':
                    cost_usd = credential_cost_for_package(amount, plan_type)
                    package_profit_percent = credential_profit_for_package(
                        amount, plan_type
                    )
                else:
                    name_key = g2bulk._normalise_catalogue_name(catalogue_name)
                    cost_usd = prices_by_name.get(name_key)
                    package_profit_percent = profit_percent
                if cost_usd is None:
                    continue
                matched += 1
                new_price = compute_gem_sale_price(
                    cost_usd, rate_value,
                    profit_percent=package_profit_percent,
                )
                if int(new_price) != int(current_price):
                    cur.execute(
                        'UPDATE "GemPackages" SET "Price"=%s WHERE "Id"=%s',
                        (new_price, gem_id),
                    )
                    updated += 1
            cur.execute(
                """INSERT INTO "BotSettings"("Key","Value") VALUES(%s,%s)
                   ON CONFLICT ("Key") DO UPDATE SET "Value"=EXCLUDED.\"Value\"""",
                ("gem_price_last_sync", str(datetime.now(timezone.utc).isoformat())),
            )
            conn.commit()
    except Exception:
        _LOG.warning("gem price sync: update failed", exc_info=True)
        return updated

    _LOG.info(
        "Gem price sync: matched=%d updated=%d rate=%d id_profit=%d%% "
        "weekly_profit=%d%% monthly_profit=%d%% weekly_cost=%s monthly_cost=%s source=%s",
        matched,
        updated,
        rate_value,
        profit_percent,
        cred_cfg['weekly_profit'],
        cred_cfg['monthly_profit'],
        cred_cfg['weekly_cost'],
        cred_cfg['monthly_cost'],
        rate_result.get("source", "unknown"),
    )
    return updated


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
        # Access control must fail closed. An unreadable block state is denied
        # until the database is healthy instead of bypassing an active ban.
        _LOG.warning(
            "Blocked-user lookup failed for telegram_id=%s; denying access",
            tg,
            exc_info=True,
        )
        return True


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
    """(Id, TelegramId, FirstName, TelegramUsername, IsBlocked, Balance) — ساده و پایدار"""
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                'SELECT u."Id", u."TelegramId", u."FirstName", '
                'COALESCE(u."TelegramUsername", \'\'), '
                'COALESCE(u."IsBlocked", false), COALESCE(w."Balance", 0) '
                'FROM "Users" u '
                'LEFT JOIN "Wallets" w ON w."UserId"=u."Id" '
                'ORDER BY u."Id" DESC LIMIT %s',
                (limit,),
            )
        except Exception:
            cur.execute(
                'SELECT u."Id", u."TelegramId", u."FirstName", u."Username", '
                'false, COALESCE(w."Balance", 0) '
                'FROM "Users" u '
                'LEFT JOIN "Wallets" w ON w."UserId"=u."Id" '
                'ORDER BY u."Id" DESC LIMIT %s',
                (limit,),
            )
        return cur.fetchall()


def list_users_with_balance(limit=30):
    """(Id, TelegramId, FirstName, TelegramUsername, IsBlocked, Balance) کاربرانی با موجودی مثبت."""
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                'SELECT u."Id", u."TelegramId", u."FirstName", '
                'COALESCE(u."TelegramUsername", \'\'), '
                'COALESCE(u."IsBlocked", false), COALESCE(w."Balance", 0) '
                'FROM "Users" u '
                'JOIN "Wallets" w ON w."UserId"=u."Id" '
                'WHERE COALESCE(w."Balance", 0) > 0 '
                'ORDER BY w."Balance" DESC LIMIT %s',
                (limit,),
            )
        except Exception:
            cur.execute(
                'SELECT u."Id", u."TelegramId", u."FirstName", u."Username", '
                'false, COALESCE(w."Balance", 0) '
                'FROM "Users" u '
                'JOIN "Wallets" w ON w."UserId"=u."Id" '
                'WHERE COALESCE(w."Balance", 0) > 0 '
                'ORDER BY w."Balance" DESC LIMIT %s',
                (limit,),
            )
        return cur.fetchall()


def admin_set_wallet_balance(user_db_id, new_balance, desc='تنظیم موجودی توسط ادمین'):
    """موجودی را دقیقاً روی عدد مشخص بگذار. خروجی: (ok, old, new, error)"""
    new_balance = int(new_balance)
    if new_balance < 0:
        return False, 0, 0, 'موجودی منفی مجاز نیست.'
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
        wallet_id, old = row
        delta = new_balance - old
        cur.execute(
            'UPDATE "Wallets" SET "Balance"=%s, "UpdatedAt"=now() WHERE "Id"=%s',
            (new_balance, wallet_id),
        )
        if delta != 0:
            kind = 'charge' if delta > 0 else 'spend'
            cur.execute(
                'INSERT INTO "WalletTransactions" '
                '("WalletId", "Amount", "Kind", "Description", "IsPaid", "CreatedAt") '
                'VALUES (%s, %s, %s, %s, true, now())',
                (wallet_id, abs(delta), kind, f'[admin] {desc}'),
            )
        conn.commit()
        return True, old, new_balance, None


def create_wallet_card_charge(user_db_id, amount):
    """شارژ کارت‌به‌کارت در انتظار تایید ادمین. خروجی: tx_id, authority"""
    import uuid
    amount = checked_amount(amount, label='مبلغ شارژ کیف پول')
    authority = f"wcard_{uuid.uuid4().hex}"
    tx_id = create_wallet_charge_tx(user_db_id, amount, authority)
    return tx_id, authority


def get_wallet_tx(tx_id):
    """Id, Amount, Authority, IsPaid, UserId, TelegramId, Balance"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id", t."Amount", t."Authority", t."IsPaid", '
            'w."UserId", u."TelegramId", w."Balance" '
            'FROM "WalletTransactions" t '
            'JOIN "Wallets" w ON w."Id"=t."WalletId" '
            'LEFT JOIN "Users" u ON u."Id"=w."UserId" '
            'WHERE t."Id"=%s',
            (tx_id,),
        )
        return cur.fetchone()


def mark_wallet_tx_rejected(tx_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "WalletTransactions" '
            'SET "Description"=COALESCE("Description", \'\') || \' [rejected]\' '
            'WHERE "Id"=%s AND "IsPaid"=false',
            (tx_id,),
        )
        # Authority را عوض کن تا دوباره complete نشود
        cur.execute(
            'UPDATE "WalletTransactions" '
            'SET "Authority"=\'rejected_\' || "Id"::text '
            'WHERE "Id"=%s AND "IsPaid"=false',
            (tx_id,),
        )
        conn.commit()


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
            'SELECT COUNT(DISTINCT o."Id") FROM "Orders" o '
            'JOIN "GemOrderInfo" g ON g."OrderId"=o."Id" '
            'WHERE o."Status" IN (\'paid\',\'processing\') '
            'AND COALESCE(g."G2BulkStatus",\'\') '
            'IN (\'FAILED\',\'REJECTED\')'
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
    """سفارش‌های پرداخت‌شده که تحویل G2Bulk شکست خورده (نه لغو/تحویل‌شده)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT DISTINCT o."Id", o."TelegramId", o."TotalAmount", o."Status", '
            'o."PaymentMethod", g."GameUID", g."G2BulkStatus" '
            'FROM "Orders" o '
            'JOIN "GemOrderInfo" g ON g."OrderId"=o."Id" '
            'WHERE o."Status" IN (\'paid\',\'processing\') '
            'AND COALESCE(g."G2BulkStatus",\'\') '
            'IN (\'FAILED\',\'REJECTED\',\'SUBMIT_UNKNOWN\') '
            'ORDER BY o."Id" DESC LIMIT %s',
            (limit,),
        )
        return cur.fetchall()


def list_processing_auto_orders(limit=50):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT DISTINCT o."Id" FROM "Orders" o '
            'JOIN "GemOrderInfo" g ON g."OrderId"=o."Id" '
            'JOIN "GemPackages" p ON p."Id"=g."GemPackageId" '
            'WHERE o."Status"=\'processing\' AND o."PaymentVerifiedAt" IS NOT NULL '
            'AND p."AutoDeliver"=true AND (g."G2BulkOrderId" IS NOT NULL '
            'OR COALESCE(g."G2BulkStatus",\'\') '
            'IN (\'SUBMITTING\',\'SUBMIT_UNKNOWN\',\'FAILED\',\'REJECTED\')) '
            'ORDER BY o."Id" LIMIT %s',
            (max(1, min(int(limit), 100)),),
        )
        return [int(row[0]) for row in cur.fetchall()]


def list_unnotified_refunds(limit=50):
    """سفارش‌های لغو+ریفاندشده که هنوز به کاربر/ادمین اعلام نشده‌اند."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''SELECT DISTINCT o."Id", o."TelegramId",
                      (o."DeliveryUserNotifiedAt" IS NOT NULL),
                      (o."DeliveryAdminNotifiedAt" IS NOT NULL),
                      COALESCE((
                          SELECT SUM(wt."Amount") FROM "WalletTransactions" wt
                          JOIN "Wallets" wa ON wa."Id"=wt."WalletId"
                          WHERE wa."UserId"=o."UserId" AND wt."Kind"='charge'
                            AND (wt."Description"=('برگشت تحویل ناموفق سفارش #' || o."Id")
                                 OR wt."Description"=('لغو توسط ادمین سفارش #' || o."Id"))
                      ),0)
               FROM "Orders" o
               WHERE o."Status" IN ('cancelled','canceled')
                 AND (
                      o."PaymentVerifiedAt" IS NOT NULL
                      OR COALESCE(o."WalletPaid",0) > 0
                      OR EXISTS (
                          SELECT 1 FROM "WalletTransactions" wt
                          JOIN "Wallets" wa ON wa."Id"=wt."WalletId"
                          WHERE wa."UserId"=o."UserId" AND wt."Kind"='charge'
                            AND (wt."Description"=('برگشت تحویل ناموفق سفارش #' || o."Id")
                                 OR wt."Description"=('لغو توسط ادمین سفارش #' || o."Id"))
                      )
                 )
                 AND (o."DeliveryUserNotifiedAt" IS NULL
                      OR o."DeliveryAdminNotifiedAt" IS NULL)
               ORDER BY o."Id" DESC LIMIT %s''',
            (max(1, min(int(limit), 100)),),
        )
        return cur.fetchall()


def list_unnotified_auto_deliveries(limit=50):
    """Return completed supplier orders whose durable notification is pending."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT DISTINCT o."Id",o."TelegramId",'
            '(o."DeliveryUserNotifiedAt" IS NOT NULL),'
            '(o."DeliveryAdminNotifiedAt" IS NOT NULL) '
            'FROM "Orders" o '
            'JOIN "GemOrderInfo" g ON g."OrderId"=o."Id" '
            'WHERE o."Status" IN (\'delivered\',\'completed\') '
            'AND o."PaymentVerifiedAt" IS NOT NULL '
            'AND g."G2BulkOrderId" IS NOT NULL '
            'AND g."G2BulkStatus"=\'COMPLETED\' '
            'AND (o."DeliveryUserNotifiedAt" IS NULL '
            'OR o."DeliveryAdminNotifiedAt" IS NULL) '
            'ORDER BY o."Id" LIMIT %s',
            (max(1, min(int(limit), 100)),),
        )
        return cur.fetchall()


def move_catalogue_item(kind, item_id, direction):
    """Move a gem or sensitivity pack and compact stable sort ranks."""
    if kind not in {'gem', 'sense'}:
        raise ValueError('نوع فهرست نامعتبر است.')
    direction = str(direction or '').strip().lower()
    if direction not in {'up', 'down', 'first', 'last'}:
        raise ValueError('جهت مرتب‌سازی نامعتبر است.')
    table = 'GemPackages' if kind == 'gem' else 'SensePackages'
    with get_conn() as conn, conn.cursor() as cur:
        if kind == 'gem':
            cur.execute(
                'SELECT "Id" FROM "GemPackages" '
                'WHERE "PurchaseType"=\'by_id\' '
                'ORDER BY "SortOrder","Id" FOR UPDATE'
            )
        else:
            cur.execute(
                'SELECT "Platform" FROM "SensePackages" WHERE "Id"=%s FOR UPDATE',
                (int(item_id),),
            )
            selected = cur.fetchone()
            if not selected:
                raise ValueError('پک سنس پیدا نشد.')
            cur.execute(
                'SELECT "Id" FROM "SensePackages" WHERE "Platform"=%s '
                'ORDER BY "SortOrder","Id" FOR UPDATE',
                (selected[0],),
            )
        identifiers = [int(row[0]) for row in cur.fetchall()]
        item_id = int(item_id)
        if item_id not in identifiers:
            raise ValueError('آیتم برای مرتب‌سازی پیدا نشد.')
        old_index = identifiers.index(item_id)
        new_index = {
            'up': max(0, old_index - 1),
            'down': min(len(identifiers) - 1, old_index + 1),
            'first': 0,
            'last': len(identifiers) - 1,
        }[direction]
        identifiers.pop(old_index)
        identifiers.insert(new_index, item_id)
        cur.executemany(
            f'UPDATE "{table}" SET "SortOrder"=%s WHERE "Id"=%s',
            [(rank * 10, identifier) for rank, identifier in enumerate(identifiers, 1)],
        )
        conn.commit()
        return old_index != new_index


def mark_delivery_notified(order_id, target):
    """Atomically persist one successful notification delivery.

    برای سفارش‌های delivered/completed (موفق) و cancelled/canceled (ریفاند ناموفق)
    فلگ نوتیف را می‌زند تا حلقه reconcile اسپم نکند.
    """
    column = {
        'user': '"DeliveryUserNotifiedAt"',
        'admin': '"DeliveryAdminNotifiedAt"',
    }.get(str(target or '').strip().lower())
    if not column:
        raise ValueError('notification target is invalid')
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE "Orders" SET {column}=COALESCE({column},now()) '
            'WHERE "Id"=%s AND "Status" IN '
            '(\'delivered\',\'completed\',\'cancelled\',\'canceled\') '
            f'RETURNING {column}',
            (int(order_id),),
        )
        updated = bool(cur.fetchone())
        conn.commit()
        return updated


def silence_refund_notifications(order_id=None):
    """برای توقف فوری اسپم: فلگ نوتیف سفارش‌های لغو‌شده را بزن."""
    with get_conn() as conn, conn.cursor() as cur:
        if order_id is not None:
            cur.execute(
                'UPDATE "Orders" SET '
                '"DeliveryUserNotifiedAt"=COALESCE("DeliveryUserNotifiedAt",now()),'
                '"DeliveryAdminNotifiedAt"=COALESCE("DeliveryAdminNotifiedAt",now()) '
                'WHERE "Id"=%s AND "Status" IN (\'cancelled\',\'canceled\')',
                (int(order_id),),
            )
        else:
            cur.execute(
                'UPDATE "Orders" SET '
                '"DeliveryUserNotifiedAt"=COALESCE("DeliveryUserNotifiedAt",now()),'
                '"DeliveryAdminNotifiedAt"=COALESCE("DeliveryAdminNotifiedAt",now()) '
                'WHERE "Status" IN (\'cancelled\',\'canceled\') '
                'AND ("DeliveryUserNotifiedAt" IS NULL '
                'OR "DeliveryAdminNotifiedAt" IS NULL)'
            )
        count = cur.rowcount
        conn.commit()
        return count


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
            't."TelegramId", u."TelegramId", u."FirstName", '
            'COALESCE(t."Category", \'other\') '
            'FROM "SupportTickets" t '
            'LEFT JOIN "Users" u ON u."Id"=t."UserId" '
            'WHERE t."Id"=%s',
            (ticket_id,),
        )
        return cur.fetchone()


def list_open_tickets(limit=20, category=None):
    with get_conn() as conn, conn.cursor() as cur:
        params = []
        where = 'WHERE t."Status"=\'open\' '
        if category:
            where += 'AND COALESCE(t."Category", \'other\')=%s '
            params.append(str(category))
        params.append(limit)
        cur.execute(
            'SELECT t."Id", t."Subject", t."Status", t."CreatedAt", '
            'COALESCE(t."TelegramId", u."TelegramId"), u."FirstName", '
            'COALESCE(t."Category", \'other\') '
            'FROM "SupportTickets" t '
            'LEFT JOIN "Users" u ON u."Id"=t."UserId" '
            + where +
            'ORDER BY t."UpdatedAt" DESC NULLS LAST, t."Id" DESC LIMIT %s',
            tuple(params),
        )
        return cur.fetchall()


def count_open_tickets(category=None):
    with get_conn() as conn, conn.cursor() as cur:
        if category:
            cur.execute(
                'SELECT COUNT(*) FROM "SupportTickets" '
                'WHERE "Status"=\'open\' AND COALESCE("Category", \'other\')=%s',
                (str(category),),
            )
        else:
            cur.execute(
                'SELECT COUNT(*) FROM "SupportTickets" WHERE "Status"=\'open\''
            )
        return int(cur.fetchone()[0] or 0)


def close_ticket(ticket_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "SupportTickets" SET "Status"=\'closed\', "UpdatedAt"=now() WHERE "Id"=%s',
            (ticket_id,),
        )
        conn.commit()


def get_active_ticket_for_user(user_db_id, category=None):
    with get_conn() as conn, conn.cursor() as cur:
        if category:
            cur.execute(
                'SELECT "Id" FROM "SupportTickets" '
                'WHERE "UserId"=%s AND "Status"=\'open\' '
                'AND COALESCE("Category", \'other\')=%s '
                'ORDER BY "Id" DESC LIMIT 1',
                (user_db_id, str(category)),
            )
        else:
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


# ─── Free Fire orders fulfilled with temporary account access ────────────────
def count_ready_credential_orders():
    """تعداد سفارش‌های اطلاعاتی آماده‌ی بررسی ادمین."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''SELECT COUNT(*) FROM "GemOrderInfo" g
               JOIN "Orders" o ON o."Id"=g."OrderId"
               WHERE g."PurchaseType"='by_credentials'
                 AND g."CredentialStatus"='ready'
                 AND o."Status" IN ('paid','processing')'''
        )
        return int(cur.fetchone()[0] or 0)


def list_credential_orders(limit=30, *, paid_only=False):
    with get_conn() as conn, conn.cursor() as cur:
        paid_filter = ''
        if paid_only:
            paid_filter = (
                " AND o.\"Status\" IN ('paid','processing') "
                " AND COALESCE(g.\"CredentialStatus\",'') "
                " NOT IN ('awaiting_payment','deleted') "
            )
        cur.execute(
            '''SELECT o."Id",o."TelegramId",o."TotalAmount",o."Status",
                      p."Title",g."LoginMethod",g."CredentialStatus",
                      g."CredentialTwoFactorEnabled",u."TelegramUsername",
                      o."CreatedAt",COALESCE(oi."Quantity",1)
               FROM "GemOrderInfo" g
               JOIN "Orders" o ON o."Id"=g."OrderId"
               JOIN "GemPackages" p ON p."Id"=g."GemPackageId"
               LEFT JOIN "OrderItems" oi ON oi."Id"=g."OrderItemId"
               LEFT JOIN "Users" u ON u."Id"=o."UserId"
               WHERE g."PurchaseType"='by_credentials'
               ''' + paid_filter + '''
               ORDER BY CASE g."CredentialStatus"
                          WHEN 'ready' THEN 0 WHEN 'needs_info' THEN 1
                          WHEN 'awaiting_payment' THEN 2 ELSE 3 END,
                        o."Id" DESC LIMIT %s''',
            (max(1, min(int(limit), 100)),),
        )
        return cur.fetchall()


def get_credential_order(order_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''SELECT o."Id",o."TelegramId",o."TotalAmount",o."Status",
                      p."Title",p."PlanType",g."LoginMethod",
                      g."CredentialCiphertext",g."CredentialStatus",
                      g."CredentialTwoFactorEnabled",g."CredentialAdminNote",
                      g."CredentialViewedAt",g."CredentialDeletedAt",
                      u."TelegramUsername",u."FirstName",u."LastName",g."Id",
                      COALESCE(oi."Quantity",1)
               FROM "GemOrderInfo" g
               JOIN "Orders" o ON o."Id"=g."OrderId"
               JOIN "GemPackages" p ON p."Id"=g."GemPackageId"
               LEFT JOIN "OrderItems" oi ON oi."Id"=g."OrderItemId"
               LEFT JOIN "Users" u ON u."Id"=o."UserId"
               WHERE o."Id"=%s AND g."PurchaseType"='by_credentials' ''',
            (int(order_id),),
        )
        return cur.fetchone()


def mark_credential_viewed(order_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "GemOrderInfo" SET "CredentialViewedAt"=now() '
            'WHERE "OrderId"=%s AND "PurchaseType"=\'by_credentials\' '
            'AND "CredentialCiphertext" IS NOT NULL',
            (int(order_id),),
        )
        conn.commit()
        return cur.rowcount == 1


def admin_complete_credential_order(order_id, admin_note=''):
    """Complete a paid manual order and irreversibly erase its login secret."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''SELECT o."Status",o."TelegramId",g."CredentialStatus"
               FROM "Orders" o JOIN "GemOrderInfo" g ON g."OrderId"=o."Id"
               WHERE o."Id"=%s AND g."PurchaseType"='by_credentials'
               FOR UPDATE OF o,g''',
            (int(order_id),),
        )
        row = cur.fetchone()
        if not row:
            return False, None, 'سفارش اطلاعاتی پیدا نشد.'
        if row[0] in ('delivered', 'completed') and row[2] == 'completed':
            return True, row[1], 'already'
        if row[0] not in ('paid', 'processing'):
            return False, row[1], 'فقط سفارش پرداخت‌شده قابل تکمیل است.'
        cur.execute(
            '''UPDATE "GemOrderInfo" SET "CredentialStatus"='completed',
                      "CredentialAdminNote"=%s,"CredentialCiphertext"=NULL,
                      "CredentialDeletedAt"=now(),"CredentialUpdatedAt"=now(),
                      "G2BulkStatus"='COMPLETED'
               WHERE "OrderId"=%s AND "PurchaseType"='by_credentials' ''',
            (str(admin_note or '')[:500], int(order_id)),
        )
        cur.execute(
            'UPDATE "Orders" SET "Status"=\'delivered\' WHERE "Id"=%s',
            (int(order_id),),
        )
        conn.commit()
        return True, row[1], 'completed'


def admin_reject_credential_info(order_id, admin_note=''):
    """Mark access data as incomplete but keep ciphertext until delivery/refund.

    Admin may still need the same login details while coordinating with the buyer.
    Secrets are wiped only on successful delivery or wallet refund/cancel.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            '''SELECT o."Status",o."TelegramId"
               FROM "Orders" o JOIN "GemOrderInfo" g ON g."OrderId"=o."Id"
               WHERE o."Id"=%s AND g."PurchaseType"='by_credentials'
               FOR UPDATE OF o,g''',
            (int(order_id),),
        )
        row = cur.fetchone()
        if not row:
            return False, None, 'سفارش اطلاعاتی پیدا نشد.'
        if row[0] not in ('paid', 'processing'):
            return False, row[1], 'فقط سفارش پرداخت‌شده قابل بررسی است.'
        cur.execute(
            '''UPDATE "GemOrderInfo" SET "CredentialStatus"='needs_info',
                      "CredentialAdminNote"=%s,"CredentialUpdatedAt"=now()
               WHERE "OrderId"=%s AND "PurchaseType"='by_credentials' ''',
            (str(admin_note or 'اطلاعات ورود صحیح یا کامل نیست.')[:500], int(order_id)),
        )
        cur.execute(
            'UPDATE "Orders" SET "Status"=\'processing\' WHERE "Id"=%s',
            (int(order_id),),
        )
        conn.commit()
        return True, row[1], 'needs_info'
