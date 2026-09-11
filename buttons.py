import re
from telegram import InlineKeyboardButton

_LEADING_EMOJI = re.compile(
    r"^\s*[\U0001F000-\U0001FAFF☀-➿←-⇿⬀-⯿⌀-⏿]"
    r"[️\U0001F000-\U0001FAFF☀-➿⬀-⯿]*\s*"
)


def btn(label: str, *, emoji_key: str | None = None, style: str | None = None, **kwargs) -> InlineKeyboardButton:
    """Inline button factory that attaches Telegram Premium icon_custom_emoji_id if themed,
    safely strips duplicate leading emoji without creating empty labels, and falls back
    to default unicode emoji prefix if unthemed."""
    if emoji_key is not None:
        from game.button_emoji import get_button_icon, get_button_label_emoji
        icon = get_button_icon(emoji_key)
        label_has_emoji = bool(_LEADING_EMOJI.match(label))
        if icon is not None:
            stripped = _LEADING_EMOJI.sub("", label, count=1) if label_has_emoji else label
            if stripped.strip():                       # real text remains → icon + text
                kwargs["icon_custom_emoji_id"] = icon
                label = stripped
            # else: emoji-only label → keep the emoji as text, skip the icon (never empty!)
        elif not label_has_emoji:                      # unthemed & no emoji → prefix fallback
            fb = get_button_label_emoji(emoji_key)
            if fb:
                label = f"{fb} {label}"
    if style is not None:
        kwargs["style"] = style
    return InlineKeyboardButton(label, **kwargs)


def back_btn(callback_data, label: str = "بازگشت") -> InlineKeyboardButton:
    """Convenience factory for back buttons."""
    return btn(label, emoji_key="btn_back", callback_data=callback_data)
