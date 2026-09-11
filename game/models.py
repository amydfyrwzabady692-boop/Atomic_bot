from django.db import models


class EmojiOverride(models.Model):
    """Maps a semantic key (e.g. "coin") OR a literal glyph (key "g:💎") to a Telegram
    Premium custom emoji. Rendered as <tg-emoji emoji-id="..."> in HTML message bodies."""
    key = models.CharField(max_length=32, unique=True)
    custom_emoji_id = models.CharField(max_length=64)
    placeholder = models.CharField(max_length=16)   # the unicode glyph shown to non-Premium
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'emoji_overrides'
        verbose_name = 'Emoji Override'
        verbose_name_plural = 'Emoji Overrides'

    def __str__(self):
        return f"{self.key} -> {self.custom_emoji_id} ({self.placeholder})"


class ButtonEmojiOverride(models.Model):
    """Maps a button key (e.g. "btn_back") to a Premium custom emoji used as an inline
    button's icon_custom_emoji_id."""
    key = models.CharField(max_length=64, unique=True)
    custom_emoji_id = models.CharField(max_length=64)
    placeholder = models.CharField(max_length=16)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'button_emoji_overrides'
        verbose_name = 'Button Emoji Override'
        verbose_name_plural = 'Button Emoji Overrides'

    def __str__(self):
        return f"{self.key} -> {self.custom_emoji_id} ({self.placeholder})"
