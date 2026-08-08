"""اعلان فوری ریفاند شکست G2Bulk به کاربر و ادمین."""
from __future__ import annotations

import asyncio
import logging

from admin_notify import notify_admin
from db import get_order, mark_delivery_notified

_LOG = logging.getLogger(__name__)


async def notify_g2_refund(bot, order_id, telegram_id=None, amount=0):
    """خبر ریفاند را به کاربر و ادمین می‌فرستد و فلگ نوتیف را می‌زند (ضداسپم)."""
    amount = int(amount or 0)
    order_id = int(order_id)
    tg = str(telegram_id or '').strip()
    if not tg:
        try:
            order = await asyncio.to_thread(get_order, order_id)
            tg = str((order[6] if order else '') or '').strip()
        except Exception:
            _LOG.exception('Could not load order %s for refund notify', order_id)
            tg = ''

    if tg:
        try:
            text = (
                f"❌ سفارش #{order_id} انجام نشد.\n"
                "سرویس تأمین (G2Bulk) تحویل را رد کرد یا ناموفق بود.\n"
            )
            if amount > 0:
                text += f"💰 مبلغ {amount:,} تومان به کیف پولت واریز شد."
            else:
                text += "پشتیبانی وضعیت را بررسی می‌کند."
            await bot.send_message(chat_id=int(tg), text=text)
        except Exception:
            _LOG.exception('Could not notify user for refunded order %s', order_id)
    await asyncio.to_thread(mark_delivery_notified, order_id, 'user')

    try:
        admin_text = f"❌ سفارش #{order_id} در G2Bulk ناموفق بود و لغو شد."
        if amount > 0:
            admin_text += (
                f"\n💰 {amount:,} ت به کیف پول کاربر {tg or '—'} واریز شد."
            )
        await notify_admin(bot, admin_text, parse_mode=None)
    except Exception:
        _LOG.exception('Could not notify admin for refunded order %s', order_id)
    await asyncio.to_thread(mark_delivery_notified, order_id, 'admin')
