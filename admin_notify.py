"""ارسال اعلان به ادمین (ADMIN_CHAT_ID)."""
import os
import time
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

ADMIN_CHAT_ID = (os.getenv('ADMIN_CHAT_ID') or '').strip()
_ROLE_CACHE_TTL = 300
_role_cache = {}
_LOG = logging.getLogger(__name__)


def admin_id():
    return int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID.isdigit() else None


def is_admin(user_id) -> bool:
    aid = admin_id()
    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    if aid and numeric_user_id == aid:
        return True
    key = ('admin', str(numeric_user_id))
    cached = _role_cache.get(key)
    if cached and time.monotonic() - cached[0] < _ROLE_CACHE_TTL:
        return cached[1]
    try:
        from db import is_bot_admin
        result = bool(is_bot_admin(numeric_user_id))
    except Exception:
        # A temporary database outage must not be cached as a five-minute
        # permission denial. The configured owner above remains available.
        _LOG.warning("Admin role lookup failed for user_id=%s", numeric_user_id, exc_info=True)
        return False
    _role_cache[key] = (time.monotonic(), result)
    return result


def is_premium_admin(user_id) -> bool:
    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    key = ('premium', str(numeric_user_id))
    cached = _role_cache.get(key)
    if cached and time.monotonic() - cached[0] < _ROLE_CACHE_TTL:
        return cached[1]
    try:
        from db import is_premium_editor
        result = bool(is_premium_editor(numeric_user_id))
    except Exception:
        _LOG.warning(
            "Premium editor role lookup failed for user_id=%s",
            numeric_user_id,
            exc_info=True,
        )
        return False
    _role_cache[key] = (time.monotonic(), result)
    return result


def is_credential_admin(user_id) -> bool:
    """Owner یا پشتیبان نقش credential — فقط بخش جم با اطلاعات."""
    aid = admin_id()
    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    if aid and numeric_user_id == aid:
        return True
    key = ('credential', str(numeric_user_id))
    cached = _role_cache.get(key)
    if cached and time.monotonic() - cached[0] < _ROLE_CACHE_TTL:
        return cached[1]
    try:
        from db import is_credential_staff
        result = bool(is_credential_staff(numeric_user_id))
    except Exception:
        _LOG.warning(
            "Credential staff lookup failed for user_id=%s",
            numeric_user_id,
            exc_info=True,
        )
        return False
    _role_cache[key] = (time.monotonic(), result)
    return result


def invalidate_role_cache(user_id=None):
    """Make admin changes visible immediately after a management action."""
    if user_id is None:
        _role_cache.clear()
        return
    target = str(user_id)
    for key in list(_role_cache):
        if key[1] == target:
            _role_cache.pop(key, None)


def admin_ids():
    ids = []
    if admin_id():
        ids.append(admin_id())
    try:
        from db import list_bot_admins
        ids.extend(
            int(row[0]) for row in list_bot_admins()
            if row[2] and row[4] == 'admin' and str(row[0]).isdigit()
        )
    except Exception:
        _LOG.warning("Could not list delegated admins", exc_info=True)
    return list(dict.fromkeys(ids))


def credential_admin_ids():
    """Owner + پشتیبان‌های جم با اطلاعات (Role=credential)."""
    ids = []
    if admin_id():
        ids.append(admin_id())
    try:
        from db import list_credential_admins
        ids.extend(
            int(row[0]) for row in list_credential_admins()
            if str(row[0]).isdigit()
        )
    except Exception:
        _LOG.warning("Could not list credential admins", exc_info=True)
    return list(dict.fromkeys(ids))


async def _send_to_recipients(bot, recipients, text, reply_markup=None, parse_mode='Markdown'):
    if not recipients:
        return False
    sent = False
    for aid in recipients:
        try:
            await bot.send_message(
                chat_id=aid,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            sent = True
        except Exception as e:
            _LOG.warning('Admin notification failed for chat_id=%s: %s', aid, e)
            if parse_mode:
                try:
                    await bot.send_message(
                        chat_id=aid,
                        text=text,
                        reply_markup=reply_markup,
                    )
                    sent = True
                except Exception as plain_error:
                    _LOG.error(
                        'Plain-text admin notification failed for chat_id=%s: %s',
                        aid,
                        plain_error,
                    )
    return sent


async def notify_admin(bot, text, reply_markup=None, parse_mode='Markdown'):
    recipients = admin_ids()
    if not recipients:
        _LOG.error('ADMIN_CHAT_ID is empty; admin notification skipped')
        return False
    return await _send_to_recipients(
        bot, recipients, text, reply_markup=reply_markup, parse_mode=parse_mode,
    )


async def notify_credential_admins(bot, text, reply_markup=None, parse_mode='Markdown'):
    recipients = credential_admin_ids()
    if not recipients:
        _LOG.error('No credential admin recipients; notification skipped')
        return False
    return await _send_to_recipients(
        bot, recipients, text, reply_markup=reply_markup, parse_mode=parse_mode,
    )
