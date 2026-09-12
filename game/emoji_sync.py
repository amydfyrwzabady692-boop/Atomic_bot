"""
Automatic Telegram Premium custom emoji pack discovery and synchronization engine.

1. Discovers all Premium custom emoji sticker packs used by the owner across:
   - EmojiOverride
   - ButtonEmojiOverride
   - Appearance table
   - Known seed packs (CenterOfEmoji686499, IconsInTg, MeowieQ, etc.)
2. Pulls all stickers from discovered packs via Bot API (getCustomEmojiStickers + getStickerSet).
3. Auto-populates ButtonEmojiOverride for all buttons that lack an icon (or all if force=True).
4. Auto-populates EmojiOverride for all semantic text keys that lack one (or all if force=True).
5. Bulk registers all literal glyphs (g:<glyph>) so premiumize_html auto-themes all message bodies.
6. Syncs overrides to the legacy Appearance table for backward compatibility.
7. Refreshes all in-memory caches.
"""

import json
import logging
import os
import urllib.request
from typing import Any

from django.conf import settings

from game.models import EmojiOverride, ButtonEmojiOverride
from game.emoji import (
    EMOJI_DEFS,
    GLYPH_SKIP,
    _GLYPH_PREFIX,
    _norm_glyph,
    refresh_cache as refresh_emoji_cache,
    set_emoji,
    set_glyphs_bulk,
)
from game.button_emoji import (
    BUTTON_EMOJI_DEFS,
    BUTTON_ALIASES,
    refresh_cache as refresh_button_cache,
    set_button_emoji,
)

logger = logging.getLogger(__name__)

PACK_PRIORITY = [
    "GamingIcons_by_TgEmodziBot",# Modern fiery sunset gamepads & gaming icons
    "UserEmoji",                 # Apple 3D high-aesthetic skeuomorphic icons (Profile, Folder, Basket, Agent, PC, Phone, Lock)
    "TgStars_by_TgEmodziBot",    # 3D radiant golden Telegram Star coins
    "MeowieQ",                   # 3D Gold Trophy, 3D Bullseye Target
    "Emoji_fan37_by_TgEmodziBot",# 3D colorful Double Gift Boxes with ribbons
    "Proxy_PJ2",                 # 3D Golden burlap money bag with $
    "Emoji004_1912R",            # 3D Cyan Shimmering Diamond
    "FlameEmoji",                # 3D Flames
    "mamali01_by_TgEmojis_bot",
    "pack_90fb6_by_TgEmojis_bot",
    "randomRedpack",
    "IconsInTg",
    "tgiosicons",
]

KNOWN_PACK_SEEDS = PACK_PRIORITY

# Curated high-priority pins for core main menu and action buttons to guarantee visual neatness, vibrant 3D colors & eye-catching prominence
CURATED_BUTTON_PINS: dict[str, tuple[str, str]] = {
    # 1. محصولات فری‌فایر (Free Fire) - Fiery Sunset Wireless Gamepad from GamingIcons_by_TgEmodziBot
    "btn_menu_ff": ("5334529375121481680", "🎮"),
    "b.menu.ff": ("5334529375121481680", "🎮"),

    # 2. کیف پول (Wallet) - 3D Golden Burlap Money Sack with $ from Proxy_PJ2
    "btn_menu_wal": ("5908958028073275934", "💰"),
    "b.menu.wal": ("5908958028073275934", "💰"),

    # 3. حساب من (Account) - 3D Glossy Royal Blue User Profile Sphere from UserEmoji
    "btn_menu_acc": ("5307797528735920953", "👤"),
    "b.menu.acc": ("5307797528735920953", "👤"),

    # 4. پک سنس (Sense Pack) - 3D Red Bullseye Target with Gold Dart in Center from MeowieQ
    "btn_menu_se": ("5832335747088130009", "🎯"),
    "b.menu.se": ("5832335747088130009", "🎯"),

    # 5. دعوت دوستان و جایزه (Referral & Prize) - 3D Golden Championship Trophy Cup from MeowieQ
    "btn_menu_ref": ("5818913299978263825", "🏆"),
    "b.menu.ref": ("5818913299978263825", "🏆"),

    # 6. سفارش‌های من (Orders) - 3D Glossy Yellow Folder with Documents from UserEmoji
    "btn_menu_ord": ("5307994908252975336", "📂"),
    "b.menu.ord": ("5307994908252975336", "📂"),

    # 7. خرید گیفت کارت (Gift Cards) - 3D Double Gift Boxes with Ribbons from Emoji_fan37
    "btn_menu_gc": ("4958699241137505132", "🎁"),
    "b.menu.gc": ("4958699241137505132", "🎁"),

    # 8. خرید استارز (Stars) - 3D Radiant Golden Telegram Star Coin from TgStars_by_TgEmodziBot
    "btn_menu_stars": ("5425107576809349359", "⭐"),
    "b.menu.stars": ("5425107576809349359", "⭐"),

    # 9. پشتیبانی (Support) - 3D Support Agent with Headset from UserEmoji
    "btn_menu_su": ("5307861601058042068", "🎧"),
    "b.menu.su": ("5307861601058042068", "🎧"),

    # 10. فروشگاه اکانت (Store) - 3D Glossy Emerald Green Shopping Basket from UserEmoji
    "btn_menu_st": ("5309909110752293449", "🛍"),
    "b.menu.st": ("5309909110752293449", "🛍"),

    # Sub-menu action buttons
    "btn_gems_id": ("5940725397195853882", "💎"),
    "b.gems.id": ("5940725397195853882", "💎"),
    "btn_gems_cr": ("5307946864748803590", "🔒"),
    "b.gems.cr": ("5307946864748803590", "🔒"),
    "b.nav.home": ("5307731102771719870", "🏠"),
    "btn_home": ("5307731102771719870", "🏠"),
    "b.gem.ok": ("5307875864644432289", "✅"),
    "b.stars.ok": ("5307875864644432289", "✅"),
    "btn_confirm": ("5307875864644432289", "✅"),
    "btn_sense_pc": ("5307910396181491682", "🖥"),
    "b.se.pc": ("5307910396181491682", "🖥"),
    "btn_sense_mob": ("5307754269825315648", "📱"),
    "b.se.mob": ("5307754269825315648", "📱"),
}

CURATED_TEXT_PINS: dict[str, tuple[str, str]] = {
    "game": ("5334529375121481680", "🎮"),
    "wallet": ("5908958028073275934", "💰"),
    "user": ("5307797528735920953", "👤"),
    "sense": ("5832335747088130009", "🎯"),
    "referral": ("5818913299978263825", "🏆"),
    "order": ("5307994908252975336", "📂"),
    "giftcard": ("4958699241137505132", "🎁"),
    "star": ("5425107576809349359", "⭐"),
    "gem": ("5940725397195853882", "💎"),
    "support": ("5307861601058042068", "🎧"),
}

CURATED_APPEARANCE_HEADERS: dict[str, tuple[str, str]] = {
    "t.welcome": ("5334529375121481680", "🎮"),
    "t.sense.hdr": ("5832335747088130009", "🎯"),
    "t.sense.pc": ("5307910396181491682", "🖥"),
    "t.sense.mob": ("5307754269825315648", "📱"),
    "t.account.hdr": ("5307797528735920953", "👤"),
    "t.orders.hdr": ("5307994908252975336", "📂"),
    "t.orders.empty": ("5307994908252975336", "📂"),
    "t.gc.hdr": ("4958699241137505132", "🎁"),
    "t.ff.hdr": ("5334529375121481680", "🎮"),
    "t.wallet.hdr": ("5908958028073275934", "💰"),
    "t.gems.hdr": ("5940725397195853882", "💎"),
    "t.creds.hdr": ("5307946864748803590", "🔒"),
    "t.stars.hdr": ("5425107576809349359", "⭐"),
    "t.support": ("5307861601058042068", "🎧"),
}

_VS16 = "\ufe0f"

# Curated fallback glyphs for buttons when exact unicode glyph is not in packs
BUTTON_FALLBACKS: dict[str, list[str]] = {
    "btn_menu_ff": ["🎮", "🕹️", "🎯", "🔥", "💎"],
    "b.menu.ff": ["🎮", "🕹️", "🎯", "🔥", "💎"],
    "btn_menu_wal": ["💰", "💳", "💵", "🪙", "👛"],
    "b.menu.wal": ["💰", "💳", "💵", "🪙", "👛"],
    "btn_menu_ord": ["📦", "📋", "🛍️", "📑"],
    "b.menu.ord": ["📦", "📋", "🛍️", "📑"],
    "btn_menu_acc": ["👤", "🪪", "👑", "🧑", "⚙️"],
    "b.menu.acc": ["👤", "🪪", "👑", "🧑", "⚙️"],
    "btn_menu_st": ["🛍️", "🛒", "🏪", "🏬", "📦"],
    "b.menu.st": ["🛍️", "🛒", "🏪", "🏬", "📦"],
    "btn_menu_se": ["🎯", "🏹", "⚡", "🎮"],
    "b.menu.se": ["🎯", "🏹", "⚡", "🎮"],
    "btn_menu_stars": ["⭐", "🌟", "✨", "👑"],
    "b.menu.stars": ["⭐", "🌟", "✨", "👑"],
    "btn_menu_gc": ["🎁", "🎀", "🎟️", "💳"],
    "b.menu.gc": ["🎁", "🎀", "🎟️", "💳"],
    "btn_menu_su": ["🎧", "💬", "🆘", "📞", "❓"],
    "b.menu.su": ["🎧", "💬", "🆘", "📞", "❓"],
    "btn_menu_ref": ["👥", "🎁", "🏆", "🤝", "👤"],
    "b.menu.ref": ["👥", "🎁", "🏆", "🤝", "👤"],

    "btn_confirm": ["✅", "✔️", "👍", "🟢"],
    "btn_cancel": ["❌", "✖️", "🚫", "🛑", "🔴"],
    "btn_buy": ["💎", "🛒", "💳", "✅", "💰"],
    "b.gem.buy": ["💎", "🛒", "💳", "✅", "💰"],
    "b.gem.ok": ["✅", "✔️", "👍", "🟢"],
    "b.gem.no": ["❌", "✖️", "🚫", "🛑", "🔴"],
    "b.stars.buy": ["⭐", "🌟", "🛒", "✅"],
    "b.stars.ok": ["✅", "✔️", "👍", "🟢"],
    "b.stars.no": ["❌", "✖️", "🚫", "🛑", "🔴"],

    "btn_pay_zp": ["💳", "🏦", "🪙", "💰"],
    "b.pay.zp": ["💳", "🏦", "🪙", "💰"],
    "btn_pay_card": ["🏧", "💳", "🏦", "💵"],
    "b.pay.card": ["🏧", "💳", "🏦", "💵"],
    "btn_pay_wal": ["💰", "💳", "🪙", "💵"],
    "b.pay.wal": ["💰", "💳", "🪙", "💵"],
    "btn_custom_amount": ["✏️", "📝", "🔢", "💰"],
    "b.wal.custom": ["✏️", "📝", "🔢", "💰"],

    "btn_gems_id": ["🆔", "💎", "⚡"],
    "b.gems.id": ["🆔", "💎", "⚡"],
    "btn_gems_cr": ["🔐", "🔒", "🔑", "💎"],
    "b.gems.cr": ["🔐", "🔒", "🔑", "💎"],
    "btn_sense_pc": ["🖥️", "💻", "🎮"],
    "b.se.pc": ["🖥️", "💻", "🎮"],
    "btn_sense_mob": ["📱", "📲", "🎮"],
    "b.se.mob": ["📱", "📲", "🎮"],

    "btn_back": ["🔙", "◀️", "⬅️", "⏪"],
    "b.nav.home": ["🔙", "🏠", "◀️", "⬅️"],
    "btn_home": ["🏠", "🏡", "🔙"],
    "btn_next": ["▶️", "➡️", "⏩"],
    "btn_prev": ["◀️", "⬅️", "⏪"],
    "btn_refresh": ["🔄", "🔃", "🔁"],

    "b.wal.50": ["💵", "💰", "🪙", "💳"],
    "b.wal.100": ["💵", "💰", "🪙", "💳"],
    "b.wal.200": ["💵", "💰", "🪙", "💳"],
    "b.wal.500": ["💵", "💰", "🪙", "💳"],
    "b.gc.gplay_us": ["🎁", "🎮", "💳"],
    "b.gc.itunes_us": ["🎁", "🍏", "💳"],
    "b.gc.itunes_tr": ["🎁", "🍏", "💳"],
    "b.gc.gplay_tr": ["🎁", "🎮", "💳"],
}

# Curated fallback glyphs for semantic message text keys
TEXT_FALLBACKS: dict[str, list[str]] = {
    "gem": ["💎", "💠", "🔷", "✨"],
    "coin": ["💰", "🪙", "💵", "💳"],
    "star": ["⭐", "🌟", "✨", "💫"],
    "dollar": ["💵", "💲", "💰", "💳"],
    "wallet": ["💳", "💰", "👛", "🪙"],
    "game": ["🎮", "🕹️", "🎯", "🔥"],
    "fire": ["🔥", "⚡", "💥", "✨"],
    "giftcard": ["🎁", "🎀", "🎟️"],
    "membership": ["📅", "📆", "🗓️", "👑"],
    "sense": ["🎯", "🏹", "⚡"],
    "store": ["🛍️", "🛒", "🏪", "🏬"],
    "crown": ["👑", "⭐", "🏆"],
    "confirm": ["✅", "✔️", "👍"],
    "cancel": ["❌", "✖️", "🚫"],
    "back": ["🔙", "◀️", "⬅️"],
    "home": ["🏠", "🏡"],
    "cart": ["🛒", "🛍️"],
    "support": ["🎧", "💬", "🆘", "📞"],
    "user": ["👤", "🪪", "🧑"],
    "order": ["📦", "📋", "📑"],
    "lock": ["🔐", "🔒", "🔑"],
    "info": ["ℹ️", "❓", "📢"],
    "sparkles": ["✨", "🌟", "⭐"],
    "rocket": ["🚀", "⚡", "🏎️"],
    "zap": ["⚡", "🚀", "💥"],
    "card": ["💳", "🏧", "💰"],
    "bank": ["🏧", "🏦", "💳"],
    "referral": ["👥", "🤝", "🎁"],
    "trophy": ["🏆", "🥇", "⭐"],
}


def _get_bot_token(token: str | None = None) -> str:
    if token:
        return token
    t = os.getenv("BOT_TOKEN")
    if t:
        return t
    return getattr(settings, "BOT_TOKEN", "")


def _call_telegram_api(method: str, payload: dict, token: str) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def discover_sticker_set_names(token: str) -> list[str]:
    """Collect all custom_emoji_ids and discover their sticker set names."""
    ids = set()

    for o in EmojiOverride.objects.all():
        if o.custom_emoji_id:
            ids.add(o.custom_emoji_id)

    for b in ButtonEmojiOverride.objects.all():
        if b.custom_emoji_id:
            ids.add(b.custom_emoji_id)

    if getattr(settings, "DATABASES", {}).get("default", {}).get("ENGINE") == "django.db.backends.postgresql" or os.getenv("DB_HOST"):
        try:
            from db import get_conn
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute('SELECT "EmojiId" FROM "Appearance" WHERE length("EmojiId") > 0')
                for row in cur.fetchall():
                    if row[0]:
                        ids.add(str(row[0]))
        except Exception as e:
            logger.warning("Could not fetch Appearance EmojiIds: %s", e)

    set_names = set(KNOWN_PACK_SEEDS)

    if ids:
        id_list = list(ids)
        for i in range(0, len(id_list), 50):
            chunk = id_list[i : i + 50]
            try:
                res = _call_telegram_api("getCustomEmojiStickers", {"custom_emoji_ids": chunk}, token)
                for s in res.get("result", []):
                    sn = s.get("set_name")
                    if sn:
                        set_names.add(sn)
            except Exception as e:
                logger.warning("getCustomEmojiStickers error for chunk: %s", e)

    def _pack_sort_key(name: str) -> tuple[int, str]:
        if name in PACK_PRIORITY:
            return (PACK_PRIORITY.index(name), name)
        return (len(PACK_PRIORITY), name)

    return sorted(set_names, key=_pack_sort_key)


def fetch_pack_emoji_map(set_names: list[str], token: str) -> dict[str, str]:
    """Fetch all stickers across sets and return mapping: normalized_glyph -> custom_emoji_id."""
    emap: dict[str, str] = {}
    for sn in set_names:
        try:
            res = _call_telegram_api("getStickerSet", {"name": sn}, token)
            if not res.get("ok"):
                continue
            stickers = res.get("result", {}).get("stickers", [])
            for st in stickers:
                raw_e = st.get("emoji")
                cid = st.get("custom_emoji_id")
                if raw_e and cid:
                    norm = _norm_glyph(raw_e)
                    emap.setdefault(raw_e, str(cid))
                    if norm:
                        emap.setdefault(norm, str(cid))
        except Exception as e:
            logger.warning("getStickerSet error for %s: %s", sn, e)
    return emap


def sync_all_emojis_from_packs(force: bool = False, bot_token: str | None = None) -> dict[str, Any]:
    """
    Main entrypoint:
    Discovers packs, fills missing buttons & text keys, sets literal glyphs,
    syncs to Appearance, and refreshes caches.
    """
    token = _get_bot_token(bot_token)
    if not token:
        raise ValueError("BOT_TOKEN is not configured.")

    # 1. Discover sets
    set_names = discover_sticker_set_names(token)
    logger.info("Discovered %d sticker pack(s): %s", len(set_names), set_names)

    # 2. Build emoji map
    emap = fetch_pack_emoji_map(set_names, token)
    logger.info("Fetched %d emojis across %d sticker pack(s)", len(emap), len(set_names))

    if not emap:
        return {
            "success": False,
            "error": "No stickers retrieved from discovered packs.",
            "sets": len(set_names),
        }

    # 2.5 Apply curated high-priority pins
    existing_btn = {o.key: o for o in ButtonEmojiOverride.objects.all()}
    existing_txt = {o.key: o for o in EmojiOverride.objects.exclude(key__startswith=_GLYPH_PREFIX)}
    btn_done = 0
    btn_skipped = 0
    btn_unmatched = []

    for pin_k, (pin_cid, pin_ph) in CURATED_BUTTON_PINS.items():
        set_button_emoji(pin_k, pin_cid, pin_ph)
        existing_btn[pin_k] = None

    for pin_k, (pin_cid, pin_ph) in CURATED_TEXT_PINS.items():
        set_emoji(pin_k, pin_cid, pin_ph)
        existing_txt[pin_k] = None

    # 3. Auto-populate buttons
    for key, (lbl, default_e, _cat) in BUTTON_EMOJI_DEFS.items():
        if key in CURATED_BUTTON_PINS:
            continue
        if key in existing_btn and not force:
            btn_skipped += 1
            continue

        cands = [default_e] + BUTTON_FALLBACKS.get(key, [])
        alias = BUTTON_ALIASES.get(key)
        if alias and alias in BUTTON_FALLBACKS:
            cands.extend(BUTTON_FALLBACKS[alias])

        cid = None
        ph = default_e
        for cand in cands:
            cid = emap.get(cand) or emap.get(_norm_glyph(cand))
            if cid:
                ph = cand
                break

        if cid:
            set_button_emoji(key, cid, ph)
            btn_done += 1
        else:
            btn_unmatched.append(key)

    # 4. Auto-populate text / message emojis
    txt_done = 0
    txt_skipped = 0
    txt_unmatched = []

    for key, (_lbl, default_e, _cat) in EMOJI_DEFS.items():
        if key in CURATED_TEXT_PINS:
            continue
        if key in existing_txt and not force:
            txt_skipped += 1
            continue

        cands = [default_e] + TEXT_FALLBACKS.get(key, [])
        cid = None
        ph = default_e
        for cand in cands:
            cid = emap.get(cand) or emap.get(_norm_glyph(cand))
            if cid:
                ph = cand
                break

        if cid:
            set_emoji(key, cid, ph)
            txt_done += 1
        else:
            txt_unmatched.append(key)

    # 4.5 Ensure curated pins are firmly enforced
    for pin_k, (pin_cid, pin_ph) in CURATED_BUTTON_PINS.items():
        set_button_emoji(pin_k, pin_cid, pin_ph)

    for pin_k, (pin_cid, pin_ph) in CURATED_TEXT_PINS.items():
        set_emoji(pin_k, pin_cid, pin_ph)

    # 5. Bulk register literal glyphs
    glyphs_count = set_glyphs_bulk(emap)

    # 6. Sync to Appearance table
    app_synced = 0
    if getattr(settings, "DATABASES", {}).get("default", {}).get("ENGINE") == "django.db.backends.postgresql" or os.getenv("DB_HOST"):
        try:
            from db import get_conn
            with get_conn() as conn, conn.cursor() as cur:
                for key, (lbl, default_e, _cat) in BUTTON_EMOJI_DEFS.items():
                    override = ButtonEmojiOverride.objects.filter(key=key).first()
                    if override and override.custom_emoji_id:
                        cur.execute(
                            'INSERT INTO "Appearance" ("Key", "EmojiId", "EmojiChar") '
                            'VALUES (%s, %s, %s) '
                            'ON CONFLICT ("Key") DO UPDATE SET "EmojiId"=EXCLUDED."EmojiId", "EmojiChar"=EXCLUDED."EmojiChar"',
                            (key, override.custom_emoji_id, override.placeholder or default_e)
                        )
                        app_synced += 1

                for pin_k, (pin_cid, pin_ph) in CURATED_BUTTON_PINS.items():
                    cur.execute(
                        'INSERT INTO "Appearance" ("Key", "EmojiId", "EmojiChar") '
                        'VALUES (%s, %s, %s) '
                        'ON CONFLICT ("Key") DO UPDATE SET "EmojiId"=EXCLUDED."EmojiId", "EmojiChar"=EXCLUDED."EmojiChar"',
                        (pin_k, pin_cid, pin_ph)
                    )
                    app_synced += 1

                for hkey, (hcid, hchar) in CURATED_APPEARANCE_HEADERS.items():
                    cur.execute(
                        'INSERT INTO "Appearance" ("Key", "EmojiId", "EmojiChar") '
                        'VALUES (%s, %s, %s) '
                        'ON CONFLICT ("Key") DO UPDATE SET "EmojiId"=EXCLUDED."EmojiId", "EmojiChar"=EXCLUDED."EmojiChar"',
                        (hkey, hcid, hchar)
                    )
                    app_synced += 1
                conn.commit()
        except Exception as e:
            logger.warning("Appearance sync error: %s", e)

    # 7. Refresh caches
    refresh_emoji_cache()
    refresh_button_cache()
    try:
        import appearance
        appearance.invalidate_cache()
    except Exception:
        pass

    return {
        "success": True,
        "sets": len(set_names),
        "total_emojis_in_packs": len(emap),
        "buttons_assigned": btn_done,
        "buttons_skipped": btn_skipped,
        "buttons_unmatched": btn_unmatched,
        "text_assigned": txt_done,
        "text_skipped": txt_skipped,
        "text_unmatched": txt_unmatched,
        "glyphs_assigned": glyphs_count,
        "appearance_synced": app_synced,
    }
