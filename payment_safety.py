"""قواعد مشترک و بدون وابستگی برای ایمنی مبالغ پرداخت."""
import os


MIN_GATEWAY_AMOUNT = 1_000
MIN_WALLET_CHARGE = 10_000
MAX_PAYMENT_AMOUNT = int(os.getenv('MAX_PAYMENT_AMOUNT', '100000000'))


def checked_amount(value, *, minimum=1, maximum=MAX_PAYMENT_AMOUNT, label='مبلغ'):
    """مبلغ را به عدد صحیح مثبت و محدود تبدیل می‌کند."""
    if isinstance(value, bool):
        raise ValueError(f'{label} نامعتبر است.')
    try:
        amount = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} باید عدد صحیح باشد.') from None
    if amount < int(minimum):
        raise ValueError(f'{label} باید حداقل {int(minimum):,} تومان باشد.')
    if amount > int(maximum):
        raise ValueError(f'{label} از سقف مجاز {int(maximum):,} تومان بیشتر است.')
    return amount


def order_amounts(total, discount=0, wallet_paid=0, item_total=None):
    """اعتبارسنجی جمع مالی سفارش و محاسبه مبلغ خالص و باقی‌مانده."""
    total = checked_amount(total, label='مبلغ کل سفارش')
    try:
        discount = int(discount or 0)
        wallet_paid = int(wallet_paid or 0)
    except (TypeError, ValueError):
        raise ValueError('اطلاعات مالی سفارش نامعتبر است.') from None
    if discount < 0 or discount >= total:
        raise ValueError('تخفیف سفارش نامعتبر است.')
    net_total = total - discount
    if wallet_paid < 0 or wallet_paid > net_total:
        raise ValueError('مبلغ کسرشده از کیف پول نامعتبر است.')
    if item_total is not None and int(item_total) != total:
        raise ValueError('جمع اقلام سفارش با مبلغ کل تطابق ندارد.')
    return net_total, net_total - wallet_paid


def valid_owner(order_user_id, order_telegram_id, *, user_db_id=None, telegram_id=None):
    """حداقل یکی از شناسه‌ها باید داده شود و همه شناسه‌های داده‌شده باید تطابق داشته باشند."""
    if user_db_id is None and telegram_id is None:
        return False
    if user_db_id is not None and int(order_user_id) != int(user_db_id):
        return False
    if telegram_id is not None and str(order_telegram_id or '') != str(telegram_id):
        return False
    return True
