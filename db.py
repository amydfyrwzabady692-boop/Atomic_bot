"""دسترسی به دیتابیس PostgreSQL مشترک با سایت Atomic Shop (accshop).

جدول‌ها و ستون‌ها PascalCase هستند و داخل گیومه قرار می‌گیرند.
"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import g2bulk
from payment_safety import checked_amount, order_amounts, valid_owner

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
    """همه بسته‌های فعال جم با آیدی که مدیر ساخته است."""
    sql = (
        f'SELECT {_GEM_COLS} FROM "GemPackages" '
        'WHERE "IsActive"=true '
        'AND "PurchaseType"=\'by_id\' '
        'AND "PlanType"=\'once\' '
        'ORDER BY "Price"'
    )
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
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
        '"PaymentExpectedAmount","PaymentVerifiedAt" '
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


def get_order_payment_expected(order_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "PaymentExpectedAmount" FROM "Orders" WHERE "Id"=%s',
            (int(order_id),),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


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


def set_order_payment_method(order_id, payment_method):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "Orders" SET "PaymentMethod"=%s WHERE "Id"=%s',
            (payment_method, order_id),
        )
        conn.commit()


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


def set_order_wallet_paid(order_id, amount):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "Orders" SET "WalletPaid"=%s WHERE "Id"=%s',
            (int(amount), order_id),
        )
        conn.commit()


def apply_wallet_to_order(user_db_id, order_id):
    """کسر موجودی به اندازه ممکن از مبلغ باقی‌مانده.
    خروجی: (ok, used, remaining, new_balance, error)"""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            row, net_total, remaining = _locked_order_financials(cur, order_id)
            if row[6] != 'pending' or row[10]:
                return False, 0, 0, 0, 'سفارش قابل پرداخت نیست.'
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
    order = get_order(order_id)
    if not order:
        return 0
    paid = int(order[7] or 0)
    if paid <= 0:
        return 0
    user_db_id = order[1]
    wallet_charge(user_db_id, paid, desc=f'برگشت کیف پول سفارش #{order_id}')
    set_order_wallet_paid(order_id, 0)
    return paid


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
    """رد رسید و بازگشت اتمیک سهم کیف پول."""
    ok, refunded, error = cancel_order_and_refund(order_id)
    if not ok:
        return False, 0, error
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "PaymentReceipts" SET "Status"=\'rejected\',"ReviewedAt"=now() '
            'WHERE "OrderId"=%s AND "Status"=\'pending\'',
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
        cur.execute(
            'UPDATE "GemPackages" SET "Stock"="Stock"-1 '
            'WHERE "Id"=%s AND COALESCE("AutoDeliver",false)=false AND "Stock">0',
            (int(package_id),),
        )
        if cur.rowcount != 1:
            return False
        cur.execute(
            'UPDATE "GemOrderInfo" SET "G2BulkStatus"=\'MANUAL_PENDING\' WHERE "Id"=%s',
            (int(info_id),),
        )
        conn.commit()
        return True


def fulfill_order(order_id):
    """تحویل فقط پس از اثبات پرداخت و با قفل سراسری idempotent برای هر سفارش."""
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
            total_manual = 0
            manual_ok = True
            for info in infos:
                (info_id, pkg_id, game_uid, player_name, auto_deliver,
                 catalogue, g2_id, amount) = info
                if not auto_deliver:
                    total_manual += 1
                    manual_ok = _reserve_manual_gem(info_id, pkg_id) and manual_ok
                    continue
                total_auto += 1
                if g2_id:
                    delivered += 1
                    continue
                if not game_uid or not g2bulk.is_supported_amount(amount):
                    update_gem_g2bulk(info_id, status='FAILED')
                    continue
                result = g2bulk.place_game_order(
                    catalogue_name=catalogue or str(amount),
                    player_id=game_uid,
                    remark=f'Atomic Bot order #{order_id}',
                    idempotency_key=g2bulk.idempotency_key(order_id, info_id),
                )
                if result.get('ok') and result.get('order_id'):
                    update_gem_g2bulk(
                        info_id,
                        order_id_g2=result['order_id'],
                        status=result.get('status', 'PENDING'),
                        player_name=result.get('player_name') or player_name,
                    )
                    delivered += 1
                else:
                    update_gem_g2bulk(info_id, status='FAILED')

            if total_auto and delivered == total_auto and manual_ok:
                update_order_status(order_id, 'delivered')
                return True, 'delivered'
            if total_auto == 0 and manual_ok:
                update_order_status(order_id, 'paid')
                return True, 'paid'
            if delivered or (total_manual and manual_ok):
                update_order_status(order_id, 'processing')
                return True, 'processing'
            return False, 'تحویل خودکار ناموفق بود. پشتیبانی بررسی می‌کند.'
        finally:
            lock_cur.execute(
                'SELECT pg_advisory_unlock(%s,%s)',
                (lock_namespace, order_id),
            )


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
            '("WalletId", "Amount", "Kind", "Description", "Authority", "IsPaid", "CreatedAt") '
            'VALUES (%s, %s, %s, %s, %s, false, now()) RETURNING "Id"',
            (wallet_id, amount, 'charge', f'شارژ کیف پول {amount:,} تومان', authority),
        )
        tx_id = cur.fetchone()[0]
        conn.commit()
        return tx_id


def complete_wallet_charge_by_authority(authority):
    """پس از verify زرین‌پال، موجودی را شارژ کن. خروجی: (ok, user_id, amount, new_balance)"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","WalletId","Amount","IsPaid" FROM "WalletTransactions" '
            'WHERE "Authority"=%s AND "Kind"=\'charge\' FOR UPDATE',
            (authority,),
        )
        row = cur.fetchone()
        if not row:
            return False, None, 0, 0
        tx_id, wallet_id, amount, is_paid = row
        try:
            amount = checked_amount(amount, label='مبلغ شارژ کیف پول')
        except ValueError:
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
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "WalletPaid" INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "PaymentExpectedAmount" INTEGER',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "PaymentVerifiedAt" TIMESTAMPTZ',
        'ALTER TABLE "Orders" ADD COLUMN IF NOT EXISTS "PaymentRefId" VARCHAR(100)',
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_payment_authority
           ON "Orders" ("PaymentAuthority")
           WHERE "PaymentAuthority" IS NOT NULL AND "PaymentAuthority" <> ''""",
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_transactions_authority
           ON "WalletTransactions" ("Authority")
           WHERE "Authority" IS NOT NULL AND "Authority" <> ''""",
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
        '''CREATE TABLE IF NOT EXISTS "BotAdmins" (
            "TelegramId" VARCHAR(64) PRIMARY KEY,
            "Title" VARCHAR(150) NOT NULL DEFAULT '',
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
            "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "BotSettings" (
            "Key" VARCHAR(100) PRIMARY KEY,
            "Value" TEXT NOT NULL DEFAULT '',
            "UpdatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
        )''',
        '''CREATE TABLE IF NOT EXISTS "SensePackages" (
            "Id" SERIAL PRIMARY KEY,
            "Title" VARCHAR(255) NOT NULL,
            "Platform" VARCHAR(20) NOT NULL DEFAULT 'pc',
            "Price" INTEGER NOT NULL,
            "Description" TEXT NOT NULL DEFAULT '',
            "IsActive" BOOLEAN NOT NULL DEFAULT true,
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
    ]
    with get_conn() as conn, conn.cursor() as cur:
        for index, sql in enumerate(stmts):
            savepoint = f'schema_patch_{index}'
            cur.execute(f'SAVEPOINT {savepoint}')
            try:
                cur.execute(sql)
            except Exception as e:
                cur.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                print(f'[DB] schema patch skipped: {sql[:40]}… ({e})')
            finally:
                cur.execute(f'RELEASE SAVEPOINT {savepoint}')
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


def set_setting(key, value):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "BotSettings" ("Key", "Value", "UpdatedAt") VALUES (%s,%s,now()) '
            'ON CONFLICT ("Key") DO UPDATE SET "Value"=EXCLUDED."Value", "UpdatedAt"=now()',
            (str(key), str(value or '')),
        )
        conn.commit()


def get_bool_setting(key, default=True):
    value = str(get_setting(key, '1' if default else '0')).strip().lower()
    return value in ('1', 'true', 'yes', 'on', 'بله', 'فعال')


def list_bot_admins():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "TelegramId", "Title", "IsActive", "CreatedAt" '
            'FROM "BotAdmins" ORDER BY "CreatedAt"'
        )
        return cur.fetchall()


def is_bot_admin(telegram_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM "BotAdmins" WHERE "TelegramId"=%s AND "IsActive"=true',
                (str(telegram_id),),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def add_bot_admin(telegram_id, title=''):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "BotAdmins" ("TelegramId","Title","IsActive") VALUES (%s,%s,true) '
            'ON CONFLICT ("TelegramId") DO UPDATE SET "Title"=EXCLUDED."Title","IsActive"=true',
            (str(telegram_id), str(title or '')),
        )
        conn.commit()


def remove_bot_admin(telegram_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM "BotAdmins" WHERE "TelegramId"=%s', (str(telegram_id),))
        conn.commit()
        return cur.rowcount > 0


def list_users_filtered(kind='all', limit=50):
    where = ''
    if kind == 'balance':
        where = 'WHERE COALESCE(w."Balance",0)>0'
    elif kind == 'referral':
        where = 'WHERE EXISTS (SELECT 1 FROM "Users" r WHERE r."ReferredById"=u."Id")'
    elif kind == 'card':
        where = 'WHERE COALESCE(u."CardVerified",false)=true'
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT u."TelegramId", u."FirstName", COALESCE(u."TelegramUsername",\'\'), '
            'COALESCE(w."Balance",0), '
            '(SELECT COUNT(*) FROM "Users" r WHERE r."ReferredById"=u."Id"), '
            'COALESCE(u."CardNumber",\'\') FROM "Users" u '
            'LEFT JOIN "Wallets" w ON w."UserId"=u."Id" ' + where +
            ' ORDER BY u."Id" DESC LIMIT %s',
            (int(limit),),
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
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT o."Id",o."TelegramId",o."TotalAmount",o."CreatedAt" '
            'FROM "Orders" o WHERE o."PaymentMethod"=\'card_transfer\' '
            'AND o."Status"=\'pending\' ORDER BY o."Id" DESC LIMIT %s',
            (int(limit),),
        )
        return cur.fetchall()


def save_payment_receipt(order_id=None, wallet_tx_id=None, telegram_id='', file_id='', text=''):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "PaymentReceipts" '
            '("OrderId","WalletTransactionId","TelegramId","ReceiptType","FileId","Text") '
            'VALUES (%s,%s,%s,%s,%s,%s) RETURNING "Id"',
            (order_id, wallet_tx_id, str(telegram_id or ''),
             'wallet' if wallet_tx_id else 'order', file_id or '', text or ''),
        )
        rid = cur.fetchone()[0]
        conn.commit()
        return rid


def get_payment_receipt(order_id=None, wallet_tx_id=None):
    """آخرین رسید ثبت‌شده برای سفارش یا شارژ کیف پول."""
    field = '"OrderId"' if order_id is not None else '"WalletTransactionId"'
    value = order_id if order_id is not None else wallet_tx_id
    if value is None:
        return None
    with get_conn() as conn, conn.cursor() as cur:
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
            'FROM "SensePackages"' + clause + ' ORDER BY "Platform","Price"',
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
            'INSERT INTO "SensePackages" ("Title","Platform","Price","Description") '
            'VALUES (%s,%s,%s,%s) RETURNING "Id"',
            (title, platform, price, description or ''),
        )
        value = cur.fetchone()[0]
        conn.commit()
        return value


def update_sense_package(package_id, field, value):
    allowed = {'Title', 'Platform', 'Price', 'Description', 'IsActive'}
    if field not in allowed:
        raise ValueError('فیلد نامعتبر')
    if field == 'Price':
        value = checked_amount(value, label='قیمت بسته')
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
            '"AutoDeliver","G2BulkCatalogueName","Stock","IsAvailable","IsActive") '
            'VALUES (%s,%s,0,%s,\'once\',\'by_id\',true,%s,%s,true,true) RETURNING "Id"',
            (title, amount, price, str(amount), stock),
        )
        value = cur.fetchone()[0]
        conn.commit()
        return value


def update_gem_package(package_id, field, value):
    allowed = {'Title', 'Amount', 'Price', 'Stock', 'IsAvailable', 'IsActive',
               'G2BulkCatalogueName', 'AutoDeliver'}
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
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'UPDATE "GemPackages" SET "{field}"=%s WHERE "Id"=%s',
                    (value, int(package_id)))
        conn.commit()


def admin_list_gems():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_GEM_COLS}, "IsActive" FROM "GemPackages" '
            'WHERE "PurchaseType"=\'by_id\' ORDER BY "Id"'
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


def add_promo_code(code, code_type, value, max_uses=1):
    code_type = str(code_type or '').strip().lower()
    max_uses = checked_amount(max_uses, maximum=1_000_000, label='تعداد استفاده')
    if code_type == 'discount':
        value = checked_amount(value, maximum=100, label='درصد تخفیف')
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
    """فقط بسته‌های اولیه را ایجاد کن؛ قیمت تنظیم‌شده ادمین هرگز بازنویسی نمی‌شود."""
    prices = {
        110: 200_000,
        231: 400_000,
        583: 1_000_000,
        1188: 2_000_000,
        2420: 4_000_000,
    }
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM "GemPackages"')
        if cur.fetchone()[0] == 0:
            for amount, price in prices.items():
                cur.execute(
                    'INSERT INTO "GemPackages" '
                    '("Title","Amount","BonusAmount","Price","PlanType","PurchaseType",'
                    '"AutoDeliver","G2BulkCatalogueName","Stock","IsAvailable","IsActive") '
                    'VALUES (%s,%s,0,%s,\'once\',\'by_id\',true,%s,9999,true,true)',
                    (f'بسته {amount} جمی', amount, price, str(amount)),
                )
        cur.execute('SELECT COUNT(*) FROM "SensePackages"')
        if cur.fetchone()[0] == 0:
            cur.executemany(
                'INSERT INTO "SensePackages" '
                '("Title","Platform","Price","Description","IsActive") VALUES (%s,%s,%s,%s,true)',
                [
                    ('پک سنس PC', 'pc', 1_000_000, 'پک سنس مخصوص سیستم PC'),
                    ('پک سنس PC + خدمات', 'pc', 2_200_000, 'پک سنس PC همراه با خدمات'),
                ],
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


def list_pending_wallet_card_charges(limit=20):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id", t."Amount", t."Authority", w."UserId", u."TelegramId", u."FirstName" '
            'FROM "WalletTransactions" t '
            'JOIN "Wallets" w ON w."Id"=t."WalletId" '
            'LEFT JOIN "Users" u ON u."Id"=w."UserId" '
            'WHERE t."Kind"=\'charge\' AND t."IsPaid"=false '
            'AND t."Authority" LIKE %s '
            'ORDER BY t."Id" DESC LIMIT %s',
            ('wcard_%', limit),
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
