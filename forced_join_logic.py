import re
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


def normalize_telegram_invite_url(value: str) -> str:
    """Ensures a telegram link has https:// and is a valid t.me or telegram.me link."""
    v = str(value or '').strip()
    if not v:
        return ''
    if v.startswith('http://'):
        v = 'https://' + v[7:]
    elif not v.startswith('https://'):
        v = 'https://' + v
    if valid_telegram_invite_url(v):
        return v
    return ''


def extract_channel_identifier(raw: str) -> tuple[str, str]:
    """Given a user text like '@channel', 'https://t.me/channel', 'channel', '-1001234567890',
    returns (chat_id, invite_url_or_empty)."""
    text = str(raw or '').strip()
    if not text:
        return '', ''
    if 't.me/' in text or 'telegram.me/' in text:
        path = text.split('t.me/')[-1].split('telegram.me/')[-1].split('?')[0].strip('/')
        if path.startswith('+') or path.startswith('joinchat/'):
            inv = normalize_telegram_invite_url(text)
            return '', inv
        else:
            uname = path.lstrip('@')
            if uname:
                return f"@{uname}", f"https://t.me/{uname}"
    if text.startswith('@'):
        uname = text[1:].strip()
        if uname:
            return f"@{uname}", f"https://t.me/{uname}"
    if (text.startswith('-100') or text.startswith('-')) and text[1:].isdigit():
        return text, ''
    if re.match(r'^[a-zA-Z0-9_]{4,32}$', text):
        return f"@{text}", f"https://t.me/{text}"
    return '', ''


def member_is_joined(status, is_member=False):
    return status in ('member', 'administrator', 'creator') or (
        status == 'restricted' and bool(is_member)
    )
