"""همگام‌سازی پیام رسید بین چند ادمین بعد از تایید/رد."""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)
_NOTICES = {}


def admin_display_name(user):
    first = (
        getattr(user, 'full_name', None)
        or getattr(user, 'first_name', None)
        or ''
    ).strip()
    username = getattr(user, 'username', None)
    if first and username:
        return f'{first} (@{username})'
    if first:
        return first
    if username:
        return f'@{username}'
    uid = getattr(user, 'id', '') or ''
    return f'ادمین {uid}'.strip()


def remember_notices(kind, pk, notices):
    key = (kind, int(pk))
    merged = list(_NOTICES.get(key) or [])
    seen = {(int(item['chat_id']), int(item['message_id'])) for item in merged}
    for item in notices or []:
        try:
            pair = (int(item['chat_id']), int(item['message_id']))
        except (TypeError, ValueError, KeyError):
            continue
        if pair in seen:
            continue
        seen.add(pair)
        merged.append({'chat_id': pair[0], 'message_id': pair[1]})
    _NOTICES[key] = merged
    return merged


def get_notices(kind, pk):
    return list(_NOTICES.get((kind, int(pk))) or [])


def review_footer(*, approved, reviewer_name):
    name = (reviewer_name or 'ادمین').strip() or 'ادمین'
    if approved:
        return (
            f'✅ تایید شد توسط {name}\n'
            'ادمین دیگر نیاز به بررسی ندارد.'
        )
    return (
        f'❌ رد شد توسط {name}\n'
        'ادمین دیگر نیاز به بررسی ندارد.'
    )


async def _edit_one(bot, chat_id, message_id, text):
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text,
            reply_markup=None,
        )
        return True
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=None,
        )
        return True
    except Exception:
        pass
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None,
        )
    except Exception:
        _LOG.warning('Could not clear markup chat=%s msg=%s', chat_id, message_id)
    return False


async def broadcast_reviewed(
    bot, kind, pk, *, approved, reviewer_name, result_text='', extra_admins=None,
):
    """دکمه‌ها را از همه نسخه‌های رسید بردار و به ادمین دیگر بگو چه کسی تایید کرد."""
    footer = review_footer(approved=approved, reviewer_name=reviewer_name)
    caption = (
        f'{result_text.strip()}\n\n━━━━━━━━━━━━━━━\n{footer}'
        if result_text else footer
    )
    seen = set()
    for item in get_notices(kind, pk):
        chat_id = int(item['chat_id'])
        message_id = int(item['message_id'])
        seen.add(chat_id)
        edited = await _edit_one(bot, chat_id, message_id, caption)
        if not edited:
            try:
                await bot.send_message(chat_id=chat_id, text=footer)
            except Exception:
                _LOG.warning('Review ping failed chat=%s', chat_id)
    try:
        from admin_notify import admin_ids
        others = extra_admins if extra_admins is not None else admin_ids()
    except Exception:
        others = extra_admins or []
    for aid in others:
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        try:
            await bot.send_message(chat_id=aid, text=footer)
        except Exception:
            _LOG.warning('Review ping failed chat=%s', aid)
