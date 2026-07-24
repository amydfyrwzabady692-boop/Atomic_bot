"""توابع خالص و قابل تست برای جوین اجباری."""
from urllib.parse import urlparse


def valid_forced_join_chat_id(value):
    value = str(value or '').strip()
    public = (
        value.startswith('@')
        and 5 <= len(value[1:]) <= 32
        and value[1:].replace('_', '').isalnum()
    )
    private = (
        value.startswith('-100')
        and len(value) >= 10
        and value[1:].isdigit()
    )
    return public or private


def valid_telegram_invite_url(value):
    parsed = urlparse(str(value or '').strip())
    return bool(
        parsed.scheme == 'https'
        and parsed.hostname in ('t.me', 'www.t.me', 'telegram.me')
        and parsed.path not in ('', '/')
        and not parsed.username
        and not parsed.password
    )


def member_is_joined(status, is_member=False):
    return status in ('member', 'administrator', 'creator') or (
        status == 'restricted' and bool(is_member)
    )
