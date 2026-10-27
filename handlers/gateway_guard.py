"""خبرِ پرداختِ درگاه از کارتِ احرازنشده — به ادمین‌ها و خریدار.

خودِ تشخیص و برگشت در payments.verify_payment_detailed انجام می‌شود (کدی
همگام که ربات را ندارد)؛ این‌جا فقط ردیف‌های اعلام‌نشده فرستاده می‌شوند.
"""
from __future__ import annotations

import asyncio
import logging

from admin_notify import admin_ids
from db import list_unnotified_gateway_mismatches, mark_gateway_mismatch_notified

log = logging.getLogger(__name__)


def _mask(number):
    number = str(number or '')
    return f'{number[:4]}-{number[4:6]}**-****-{number[12:]}' if len(number) == 16 else number


async def notify_gateway_mismatches(bot):
    rows = await asyncio.to_thread(list_unnotified_gateway_mismatches, 20)
    for authority, tg, card, paid_pan, ref_id, amount, reversed_ok in rows:
        tail = ('✅ تراکنش خودکار به همان کارت برگشت داده شد.' if reversed_ok else
                '⚠️ برگشت خودکار انجام نشد — از پنل زرین‌پال دستی برگردان.')
        admin_text = (
            '🚫 پرداخت درگاه ربات از کارتِ احرازنشده\n'
            f'کاربر تلگرام: {tg} · مبلغ: {int(amount or 0):,} ت\n'
            f'کارت احرازشده: {_mask(card)} · کارت پرداخت: {paid_pan or "—"}\n'
            f'مرجع: {ref_id or "—"} · authority: {authority}\n\n'
            f'سفارش/شارژ انجام نشد. {tail}')
        for aid in admin_ids():
            try:
                await bot.send_message(chat_id=aid, text=admin_text)
            except Exception as exc:  # noqa: BLE001
                log.warning('mismatch admin notice failed admin=%s: %s', aid, exc)
        try:
            await bot.send_message(chat_id=int(tg), text=(
                '⚠️ پرداختت از کارتی انجام شد که احراز نشده است، پس پذیرفته نشد.\n'
                + ('مبلغ به همان کارت برگشت داده شد (معمولاً تا ۷۲ ساعت).' if reversed_ok
                   else 'مبلغ برگشت داده می‌شود؛ با پشتیبانی در تماس باش.')
                + f'\nفقط با کارت {_mask(card)} پرداخت کن.'))
        except Exception as exc:  # noqa: BLE001 — خریدار ربات را بسته
            log.warning('mismatch buyer notice failed tg=%s: %s', tg, exc)
        await asyncio.to_thread(mark_gateway_mismatch_notified, authority)
