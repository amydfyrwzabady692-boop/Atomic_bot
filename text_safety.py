"""Small helpers for safely rendering user/admin supplied Telegram text."""


def markdown_safe(value, limit=None):
    """Escape dynamic text for Telegram's legacy Markdown parser."""
    text = str(value or '')
    if limit is not None:
        text = text[:max(0, int(limit))]
    text = text.replace('\\', '\\\\')
    for char in ('_', '*', '`', '['):
        text = text.replace(char, f'\\{char}')
    return text
