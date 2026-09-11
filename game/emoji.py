import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.models import EmojiOverride

GLYPH_SKIP = {"⚪", "🔵", "🟣", "🟡", "🔴", "▓", "░", "•", "·", "━"}
_GLYPH_PREFIX = "g:"

# key -> (label, default unicode emoji, category). Customized for Atomic Bot domain.
EMOJI_DEFS: dict[str, tuple[str, str, str]] = {
    # منابع و مالی
    "gem":        ("جم فری‌فایر",       "💎", "resources"),
    "coin":       ("تومان / سکه",       "💰", "resources"),
    "star":       ("استارز تلگرام",     "⭐", "resources"),
    "dollar":     ("دلار",              "💵", "resources"),
    "wallet":     ("کیف پول",           "💳", "resources"),

    # بازی و محصولات
    "game":       ("بازی فری‌فایر",      "🎮", "game"),
    "fire":       ("بویاه / آتش",       "🔥", "game"),
    "giftcard":   ("گیفت کارت",         "🎁", "game"),
    "membership": ("عضویت هفتگی/ماهانه", "📅", "game"),
    "sense":      ("پک سنس",            "🎯", "game"),
    "store":      ("فروشگاه اکانت",      "🛍", "game"),
    "crown":      ("ویژه / VIP",        "👑", "game"),

    # رابط کاربری و عملیات
    "confirm":    ("تایید",             "✅", "ui"),
    "cancel":     ("انصراف / لغو",      "❌", "ui"),
    "back":       ("بازگشت",            "🔙", "ui"),
    "home":       ("منوی اصلی",         "🏠", "ui"),
    "cart":       ("سبد خرید",          "🛒", "ui"),
    "support":    ("پشتیبانی",          "🎧", "ui"),
    "user":       ("حساب کاربری",       "👤", "ui"),
    "order":      ("سفارش‌ها",          "📦", "ui"),
    "lock":       ("امنیت / اطلاعات ورود", "🔐", "ui"),
    "info":       ("راهنما / اطلاعات",   "ℹ️", "ui"),
    "sparkles":   ("درخشش / جدید",      "✨", "ui"),
    "rocket":     ("تحویل آنی",         "🚀", "ui"),
    "zap":        ("سرعت بالا",         "⚡", "ui"),
    "card":       ("کارت بانکی",        "💳", "ui"),
    "bank":       ("درگاه پرداخت",      "🏧", "ui"),
    "referral":   ("دعوت دوستان",       "👥", "ui"),
    "trophy":     ("جایزه و رتبه",      "🏆", "ui"),
}

CATEGORY_LABELS: dict[str, str] = {
    "resources": "💰 ارز و منابع",
    "game":      "🎮 بازی و محصولات",
    "ui":        "🖥 رابط کاربری و منو",
}

EMOJI_KEYS = {k: f"{e} {lbl}" for k, (lbl, e, _c) in EMOJI_DEFS.items()}
DEFAULT_EMOJI = {k: e for k, (_l, e, _c) in EMOJI_DEFS.items()}
CATEGORY_OF = {k: c for k, (_l, _e, c) in EMOJI_DEFS.items()}

_cache: dict[str, EmojiOverride] = {}
_glyph_map: dict[str, str] = {}
_glyph_re: "re.Pattern | None" = None
_TG_EMOJI_BLOCK = re.compile(r"<tg-emoji\b[^>]*>.*?</tg-emoji>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _norm_glyph(g: str) -> str:
    return g.replace("️", "")   # drop U+FE0F so ⚡️ == ⚡


def _get_model():
    from game.models import EmojiOverride
    return EmojiOverride


def _load_cache() -> dict:
    global _cache
    try:
        model = _get_model()
        _cache = {o.key: o for o in model.objects.all()}
    except Exception:
        _cache = {}
    return _cache


def _load_glyph_map() -> dict:
    global _glyph_map, _glyph_re
    try:
        model = _get_model()
        gm = {
            _norm_glyph(o.key[len(_GLYPH_PREFIX):]): o.custom_emoji_id
            for o in model.objects.filter(key__startswith=_GLYPH_PREFIX)
            if o.key[len(_GLYPH_PREFIX):] not in GLYPH_SKIP
        }
    except Exception:
        gm = {}
    _glyph_map = gm
    _glyph_re = (
        re.compile("|".join(re.escape(g) + "️?" for g in sorted(gm, key=len, reverse=True)))
        if gm else None
    )
    return gm


def refresh_cache() -> None:
    _load_cache()
    _load_glyph_map()


def get_emoji(key: str, fallback: str | None = None) -> str:
    """HTML for a semantic key. MESSAGE BODIES ONLY (parse_mode='HTML') — never button text."""
    o = _cache.get(key)
    if o is not None:
        return f'<tg-emoji emoji-id="{o.custom_emoji_id}">{o.placeholder}</tg-emoji>'
    return fallback if fallback is not None else DEFAULT_EMOJI.get(key, "❓")


def premiumize_html(text: str) -> str:
    """Wrap every themed literal glyph in `text` with its <tg-emoji>. Skips glyphs already
    inside a <tg-emoji> block or any HTML tag, and the fixed GLYPH_SKIP glyphs."""
    if not text:
        return text
    gm = _glyph_map
    if not gm or _glyph_re is None:
        return text

    def _wrap(g: str) -> str:
        cid = gm.get(_norm_glyph(g))
        return f'<tg-emoji emoji-id="{cid}">{g}</tg-emoji>' if cid else g

    def _wrap_segment(seg: str) -> str:
        pieces, last = [], 0
        for tag in _TAG.finditer(seg):
            pieces.append(_glyph_re.sub(lambda m: _wrap(m.group()), seg[last:tag.start()]))
            pieces.append(tag.group())
            last = tag.end()
        pieces.append(_glyph_re.sub(lambda m: _wrap(m.group()), seg[last:]))
        return "".join(pieces)

    out, last = [], 0
    for block in _TG_EMOJI_BLOCK.finditer(text):
        out.append(_wrap_segment(text[last:block.start()]))
        out.append(block.group())
        last = block.end()
    out.append(_wrap_segment(text[last:]))
    return "".join(out)


def _key_glyphs(key: str, placeholder: str) -> set[str]:
    out = set()
    for g in (DEFAULT_EMOJI.get(key, ""), placeholder or ""):
        g = _norm_glyph(g)
        if g and g not in GLYPH_SKIP:
            out.add(g)
    return out


def set_emoji(key: str, custom_emoji_id: str, placeholder: str) -> None:
    model = _get_model()
    model.objects.update_or_create(
        key=key,
        defaults={"custom_emoji_id": custom_emoji_id, "placeholder": placeholder}
    )
    for g in _key_glyphs(key, placeholder):   # couple the literal glyph(s) too
        model.objects.update_or_create(
            key=f"{_GLYPH_PREFIX}{g}",
            defaults={"custom_emoji_id": custom_emoji_id, "placeholder": g}
        )
    refresh_cache()


def clear_emoji(key: str) -> bool:
    model = _get_model()
    existing = model.objects.filter(key=key).first()
    ph = existing.placeholder if existing else ""
    deleted, _ = model.objects.filter(key=key).delete()
    gk = [f"{_GLYPH_PREFIX}{g}" for g in _key_glyphs(key, ph)]
    if gk:
        model.objects.filter(key__in=gk).delete()
    refresh_cache()
    return deleted > 0


def get_emoji_id(key: str) -> str | None:
    """Return custom_emoji_id for key or glyph (or g:<glyph>), or None."""
    o = _cache.get(key)
    if o is not None:
        return o.custom_emoji_id
    norm = _norm_glyph(key)
    return _glyph_map.get(norm)


def set_glyphs_bulk(glyph_map: dict[str, str]) -> int:
    """Bulk register literal glyphs into EmojiOverride (g:<glyph> -> custom_emoji_id)."""
    model = _get_model()
    to_create = []
    to_update = []
    existing = {o.key: o for o in model.objects.filter(key__startswith=_GLYPH_PREFIX)}
    for g, cid in glyph_map.items():
        norm = _norm_glyph(g)
        if not norm or norm in GLYPH_SKIP:
            continue
        key = f"{_GLYPH_PREFIX}{norm}"
        if key in existing:
            o = existing[key]
            if o.custom_emoji_id != cid:
                o.custom_emoji_id = cid
                o.placeholder = norm
                to_update.append(o)
        else:
            to_create.append(model(key=key, custom_emoji_id=cid, placeholder=norm))
    if to_create:
        model.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        model.objects.bulk_update(to_update, ["custom_emoji_id", "placeholder"])
    refresh_cache()
    return len(existing) + len(to_create)


def list_overrides() -> list:
    model = _get_model()
    return list(model.objects.order_by("key"))
