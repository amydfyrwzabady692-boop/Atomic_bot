from game.models import ButtonEmojiOverride

# key -> (label, default unicode emoji, category). Atomic Bot inline button definitions.
BUTTON_EMOJI_DEFS: dict[str, tuple[str, str, str]] = {
    # ناوبری
    "btn_back":          ("بازگشت",               "◀️", "nav"),
    "btn_home":          ("منوی اصلی",            "🏠", "nav"),
    "btn_next":          ("صفحه بعد",             "▶️", "nav"),
    "btn_prev":          ("صفحه قبل",             "◀️", "nav"),
    "btn_refresh":       ("بروزرسانی",            "🔄", "nav"),

    # منوی اصلی
    "btn_menu_ff":       ("محصولات فری‌فایر",     "🎮", "menu"),
    "btn_menu_wal":      ("کیف پول",              "💰", "menu"),
    "btn_menu_ord":      ("سفارش‌های من",         "📦", "menu"),
    "btn_menu_acc":      ("حساب من",              "👤", "menu"),
    "btn_menu_st":       ("فروشگاه اکانت",         "🛍", "menu"),
    "btn_menu_se":       ("پک سنس",               "🎯", "menu"),
    "btn_menu_stars":    ("خرید استارز",          "⭐", "menu"),
    "btn_menu_gc":       ("خرید گیفت کارت",       "🎁", "menu"),
    "btn_menu_su":       ("پشتیبانی",             "🎧", "menu"),
    "btn_menu_ref":      ("دعوت دوستان و جایزه",  "👥", "menu"),

    # تایید و انصراف
    "btn_confirm":       ("تایید و ادامه",        "✅", "action"),
    "btn_cancel":        ("انصراف",               "❌", "action"),
    "btn_buy":           ("خرید این بسته",        "💎", "action"),

    # روش‌های پرداخت
    "btn_pay_zp":        ("زرین‌پال",             "💳", "payment"),
    "btn_pay_card":      ("کارت‌به‌کارت",         "🏧", "payment"),
    "btn_pay_wal":       ("کیف پول (موجودی)",     "💰", "payment"),
    "btn_custom_amount": ("مبلغ دلخواه",          "✏️", "payment"),

    # دسته‌های محصولات
    "btn_gems_id":       ("جم با آیدی",           "🆔", "shop"),
    "btn_gems_cr":       ("جم با اطلاعات",        "🔐", "shop"),
    "btn_sense_pc":      ("پلتفرم PC",            "🖥", "shop"),
    "btn_sense_mob":     ("پلتفرم موبایل",        "📱", "shop"),
}

BUTTON_CATEGORY_LABELS: dict[str, str] = {
    "menu":    "📱 منوی اصلی",
    "nav":     "🧭 ناوبری",
    "action":  "⚡ عملیات و خرید",
    "payment": "💳 روش‌های پرداخت",
    "shop":    "🛍 دسته‌های فروشگاه",
}

BUTTON_EMOJI_KEYS = {k: f"{e} {lbl}" for k, (lbl, e, _c) in BUTTON_EMOJI_DEFS.items()}
BUTTON_DEFAULT_EMOJI = {k: e for k, (_l, e, _c) in BUTTON_EMOJI_DEFS.items()}
BUTTON_CATEGORY_OF = {k: c for k, (_l, _e, c) in BUTTON_EMOJI_DEFS.items()}

_cache: dict[str, ButtonEmojiOverride] = {}   # starts EMPTY, populated synchronously (async-safe)


def refresh_cache() -> None:
    global _cache
    try:
        _cache = {o.key: o for o in ButtonEmojiOverride.objects.all()}
    except Exception:
        _cache = {}


def get_button_icon(key: str) -> str | None:
    o = _cache.get(key)
    return o.custom_emoji_id if o is not None else None


def get_button_label_emoji(key: str) -> str:
    return BUTTON_DEFAULT_EMOJI.get(key, "")


def set_button_emoji(key: str, custom_emoji_id: str, placeholder: str) -> None:
    ButtonEmojiOverride.objects.update_or_create(
        key=key,
        defaults={"custom_emoji_id": custom_emoji_id, "placeholder": placeholder}
    )
    refresh_cache()


def clear_button_emoji(key: str) -> bool:
    deleted, _ = ButtonEmojiOverride.objects.filter(key=key).delete()
    refresh_cache()
    return deleted > 0


def list_button_overrides() -> list:
    return list(ButtonEmojiOverride.objects.order_by("key"))
