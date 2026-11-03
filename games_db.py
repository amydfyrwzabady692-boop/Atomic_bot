"""شارژ بازی‌ها در ربات: پابجی، کالاف دیوتی، موبایل لجندز و بقیه.

فهرست بازی‌ها، نام فارسی، واحد و سرورها از سایت می‌آید (سایت بازی‌های قفلِ
منطقه را بیرون گذاشته و نام‌ها را درست کرده). بهای دلاریِ هر بسته را ربات
خودش از تامین‌کننده می‌گیرد و مثل جم، گیفت کارت و استارز قیمت می‌گذارد:
بها × (نرخ زندهٔ دلار + ۵٬۰۰۰) × (۱ + سود٪). سود: «game_profit_percent» (۱۰).

سفارش همان مسیرِ استارز را دارد: سفارش در «Orders» و یک قلم در «OrderItems»،
جزئیات در «GameTopupOrders»؛ بعد از پرداخت fulfill_order این‌جا را صدا می‌زند،
سفارش با آیدی بازیکن (و سرور) به تامین‌کننده می‌رود و اگر ردِ صریح آمد پول به
کیف پول برمی‌گردد. هیچ‌جا قبل از پرداخت دوباره موجودی چک نمی‌شود تا قطعیِ
لحظه‌ای به ریفاندِ سفارشِ پرداخت‌شده تبدیل نشود.
"""
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal

import g2bulk
from payment_safety import checked_amount

_LOG = logging.getLogger(__name__)

# بازی‌هایی که اول فهرست می‌آیند — پرجست‌وجوترین‌ها.
FEATURED = ('pubgm', 'codm_sgmy', 'mlbb', 'deltaforce', 'bloodstrikeme', 'hok',
            'genshin', 'honkai_star_rail')
SYNC_KEY = 'games_catalog_last_sync'
SYNC_EVERY_SECONDS = 20 * 3600  # حلقهٔ روزانه هر ۲۴ ساعت صدا می‌زند؛ چند ساعت حاشیه
_PLAYER_RE = re.compile(r'^[A-Za-z0-9_.\-]{3,40}$')
_FA_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def _db():
    import db
    return db


def game_profit_percent():
    raw = str(_db().get_setting('game_profit_percent', '10') or '10')
    try:
        value = int(raw.replace('%', '').replace('٪', '').strip())
    except ValueError:
        value = 10
    return max(1, min(200, value))


def normalize_player_id(raw):
    """آیدی بازیکن: ارقام فارسی به لاتین، بی‌فاصله؛ نامعتبر → ''."""
    text = str(raw or '').translate(_FA_DIGITS).strip().replace(' ', '')
    return text if _PLAYER_RE.match(text) else ''


# ── خواندن ───────────────────────────────────────────────────────────────

def _game_row(row):
    return {
        'id': row[0], 'code': row[1], 'title': row[2], 'unit': row[3] or '',
        'needs_server': bool(row[4]),
        'servers': [s for s in (row[5] or '').splitlines() if s.strip()],
        'warning': row[6] or '', 'live': int(row[7] or 0) if len(row) > 7 else 0,
        'low_price': int(row[8] or 0) if len(row) > 8 else 0,
    }


def list_games():
    """بازی‌های فعالی که دست‌کم یک بستهٔ فعال دارند؛ پرجست‌وجوها اول."""
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT g."Id",g."Code",g."TitleFa",g."Unit",g."NeedsServer",g."Servers",'
            'g."Warning",COUNT(p."Id"),MIN(p."Price") '
            'FROM "BotGames" g JOIN "GameTopupPackages" p ON p."GameId"=g."Id" AND p."IsActive" '
            'WHERE g."IsActive" GROUP BY g."Id" ORDER BY g."SortOrder", g."TitleFa"'
        )
        return [_game_row(row) for row in cur.fetchall()]


def get_game(game_id):
    try:
        game_id = int(game_id)
    except (TypeError, ValueError):
        return None
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","Code","TitleFa","Unit","NeedsServer","Servers","Warning" '
            'FROM "BotGames" WHERE "Id"=%s AND "IsActive"', (game_id,))
        row = cur.fetchone()
    return _game_row(row) if row else None


def _package_row(row):
    return {
        'id': row[0], 'game_id': row[1], 'catalogue': row[2], 'title': row[3],
        'cost_usd': Decimal(str(row[4])), 'price': int(row[5]), 'active': bool(row[6]),
        'available': bool(row[6]) and int(row[5] or 0) > 0,
    }


def list_game_packages(game_id):
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","GameId","CatalogueName","Title","CostUsd","Price","IsActive" '
            'FROM "GameTopupPackages" WHERE "GameId"=%s AND "IsActive" '
            'ORDER BY "SortOrder","Price","Id"', (int(game_id),))
        return [_package_row(row) for row in cur.fetchall()]


def get_game_package(package_id):
    try:
        package_id = int(package_id)
    except (TypeError, ValueError):
        return None
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","GameId","CatalogueName","Title","CostUsd","Price","IsActive" '
            'FROM "GameTopupPackages" WHERE "Id"=%s', (package_id,))
        row = cur.fetchone()
    return _package_row(row) if row else None


# ── ثبت سفارش ────────────────────────────────────────────────────────────

def create_game_order_atomic(user_db_id, package_id, expected_price, *, player_id,
                             server_id='', player_name='', telegram_id='', full_name=''):
    db = _db()
    expected_price = checked_amount(expected_price, label='قیمت مورد تأیید')
    player = normalize_player_id(player_id)
    if not player:
        raise ValueError('آیدی بازیکن معتبر نیست.')
    pkg = get_game_package(package_id)
    if not pkg or not pkg['available']:
        raise ValueError('این بسته دیگر موجود نیست.')
    game = get_game(pkg['game_id'])
    if not game:
        raise ValueError('این بازی فعلاً فعال نیست.')
    server = str(server_id or '').strip()
    if game['needs_server']:
        if game['servers'] and server not in game['servers']:
            raise ValueError('سرور انتخاب‌شده معتبر نیست.')
        if not server:
            raise ValueError('سرور بازی را انتخاب کن.')
    price = checked_amount(pkg['price'], label='قیمت بسته')
    if price != expected_price:
        raise ValueError('قیمت این بسته تغییر کرده است؛ دوباره از فهرست انتخاب کن.')
    available, _cost, _balance, error = g2bulk.can_fulfill_game(
        game['code'], pkg['catalogue'], force=True)
    if not available:
        raise ValueError(error or 'موجودی این بسته کافی نیست.')
    title = f"{pkg['title']} {game['title']}".strip()
    rate = int(db.get_setting('games_usd_toman_rate', '0') or 0) or None
    with db.get_conn() as conn, conn.cursor() as cur:
        try:
            order_id = db._insert_pending_order(
                cur, user_db_id, price, telegram_id=telegram_id,
                full_name=full_name, payment_method='pending')
            cur.execute(
                'INSERT INTO "OrderItems" ("OrderId","ProductId","ProductName","Price","Quantity") '
                'VALUES (%s,NULL,%s,%s,1) RETURNING "Id"', (order_id, title[:250], price))
            item_id = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO "GameTopupOrders" ("OrderId","OrderItemId","PackageId","GameCode",'
                '"GameTitle","CatalogueName","Title","PlayerId","ServerId","PlayerName",'
                '"CostUsd","SaleToman","UsdTomanRate") '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (order_id, item_id, pkg['id'], game['code'], game['title'][:190],
                 pkg['catalogue'], pkg['title'][:190], player, server[:120],
                 str(player_name or '')[:250], str(pkg['cost_usd']), price, rate))
            conn.commit()
            return order_id, title, price
        except Exception:
            conn.rollback()
            raise


def get_game_topup_order(order_id):
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id","OrderId","PackageId","GameCode","GameTitle","CatalogueName","Title",'
            '"PlayerId","ServerId","PlayerName","CostUsd","SaleToman","G2OrderId","G2Status",'
            '"LastError" FROM "GameTopupOrders" WHERE "OrderId"=%s', (int(order_id),))
        row = cur.fetchone()
    if not row:
        return None
    return {
        'id': row[0], 'order_id': row[1], 'package_id': row[2], 'game_code': row[3],
        'game_title': row[4], 'catalogue': row[5], 'title': row[6], 'player_id': row[7],
        'server_id': row[8] or '', 'player_name': row[9] or '', 'cost_usd': row[10],
        'sale_toman': row[11], 'g2_order_id': row[12],
        'g2_status': str(row[13] or '').upper(), 'last_error': row[14] or '',
    }


def game_order_error(order_id):
    try:
        info = get_game_topup_order(order_id)
    except Exception:  # noqa: BLE001
        return ''
    return (info or {}).get('last_error') or ''


def _update(row_id, *, order_id_g2=None, status=None, player_name=None, error=None):
    fields, values = ['"UpdatedAt"=now()'], []
    if order_id_g2 is not None:
        fields.append('"G2OrderId"=%s')
        values.append(str(order_id_g2))
    if status is not None:
        fields.append('"G2Status"=%s')
        values.append(str(status))
    if player_name is not None:
        fields.append('"PlayerName"=%s')
        values.append(str(player_name or '')[:250])
    if error is not None:
        fields.append('"LastError"=%s')
        values.append(str(error)[:4000])
    values.append(int(row_id))
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'UPDATE "GameTopupOrders" SET {", ".join(fields)} WHERE "Id"=%s', values)
        conn.commit()


def _claim_submission(row_id):
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "GameTopupOrders" SET "G2Status"=\'SUBMITTING\',"UpdatedAt"=now() '
            'WHERE "Id"=%s AND "G2OrderId" IS NULL AND COALESCE("G2Status",\'\')=\'\' '
            'RETURNING "Id"', (int(row_id),))
        claimed = bool(cur.fetchone())
        conn.commit()
        return claimed


def _remark(order_id):
    return f'Atomic Bot game #{order_id}'


def _refund(order_id, fail_text):
    ok_refund, refunded = _db()._refund_failed_order(order_id)
    if ok_refund:
        return False, f'refunded:{int(refunded)}'
    return False, fail_text


def fulfill_game_topup(order_id, row):
    """سفارشِ پرداخت‌شده را به تامین‌کننده بفرست یا وضعیتش را پیگیری کن."""
    db = _db()
    status = str(row.get('g2_status') or '').upper()
    if status == 'COMPLETED' and row.get('g2_order_id'):
        db.update_order_status(order_id, 'delivered')
        return True, 'delivered'

    if row.get('g2_order_id') and status in {'PENDING', 'PROCESSING', 'SUBMITTING'}:
        live = g2bulk.get_game_order_status(row['g2_order_id'])
        if live.get('ok'):
            live_status = str(live.get('status') or '').upper()
            _update(row['id'], status=live_status,
                    player_name=live.get('player_name') or row.get('player_name'))
            if live_status == 'COMPLETED':
                db.update_order_status(order_id, 'delivered')
                return True, 'delivered'
            if db._is_terminal_g2_failure(live_status):
                _update(row['id'], status='FAILED',
                        error=f'تامین‌کننده سفارش را {live_status} کرد.')
                return _refund(order_id, 'شارژ بازی ناموفق بود.')
        db.update_order_status(order_id, 'processing')
        return True, 'processing'

    if status in {'SUBMITTING', 'SUBMIT_UNKNOWN'}:
        recovered = g2bulk.find_game_order_by_remark(_remark(order_id))
        if recovered.get('found'):
            recovered_status = str(recovered.get('status') or 'PENDING').upper()
            _update(row['id'], order_id_g2=recovered['order_id'], status=recovered_status,
                    player_name=recovered.get('player_name') or row.get('player_name'))
            if recovered_status == 'COMPLETED':
                db.update_order_status(order_id, 'delivered')
                return True, 'delivered'
            if db._is_terminal_g2_failure(recovered_status):
                return _refund(order_id, 'شارژ بازی ناموفق بود.')
        db.update_order_status(order_id, 'processing')
        return True, 'processing'

    if status in {'REJECTED', 'FAILED'}:
        return _refund(order_id, 'شارژ بازی ناموفق بود.')

    if not row.get('player_id'):
        _update(row['id'], status='REJECTED', error='آیدی بازیکن خالی است.')
        return _refund(order_id, 'آیدی بازیکن سفارش خالی است.')
    if not _claim_submission(row['id']):
        db.update_order_status(order_id, 'processing')
        return True, 'processing'
    result = g2bulk.place_game_order(
        catalogue_name=row['catalogue'], player_id=row['player_id'],
        remark=_remark(order_id),
        idempotency_key=g2bulk.game_idempotency_key(order_id, row['id']),
        game_code=row['game_code'], server_id=row.get('server_id') or None,
    )
    if result.get('ok') and result.get('order_id'):
        api_status = str(result.get('status') or 'PENDING').upper()
        _update(row['id'], order_id_g2=result['order_id'], status=api_status,
                player_name=result.get('player_name') or row.get('player_name'))
        if api_status == 'COMPLETED':
            db.update_order_status(order_id, 'delivered')
            return True, 'delivered'
        if db._is_terminal_g2_failure(api_status):
            _update(row['id'], error=f'تامین‌کننده سفارش را {api_status} کرد.')
            return _refund(order_id, 'شارژ بازی ناموفق بود.')
        db.update_order_status(order_id, 'processing')
        return True, 'processing'
    if result.get('uncertain'):
        _update(row['id'], status='SUBMIT_UNKNOWN')
        db.update_order_status(order_id, 'processing')
        return True, 'processing'
    _update(row['id'], status='REJECTED', error=f'رد تامین‌کننده: {result.get("error") or "—"}')
    return _refund(order_id, result.get('error') or 'ثبت سفارش شارژ بازی ناموفق بود.')


def game_user_delivery_text(order_id):
    info = get_game_topup_order(order_id)
    if not info:
        return None
    who = f"آیدی: {info['player_id']}"
    if info.get('server_id'):
        who += f" · سرور: {info['server_id']}"
    if info.get('player_name'):
        who += f"\nنام بازیکن: {info['player_name']}"
    if info.get('g2_status') == 'COMPLETED':
        return (f"✅ سفارش #{order_id} تحویل شد\n{info['title']} — {info['game_title']}\n{who}\n"
                f"مبلغ پرداختی: {int(info['sale_toman']):,} تومان\n\n"
                "شارژ روی همین آیدی نشسته است؛ اگر نمی‌بینی، یک‌بار از بازی خارج شو و دوباره وارد شو.")
    return (f"✅ سفارش #{order_id} پرداخت شد و شارژ در حال واریز است.\n"
            f"{info['title']} — {info['game_title']}\n{who}\n"
            "نتیجه نهایی به‌صورت خودکار برایت ارسال می‌شود.")


# ── همگام‌سازی فهرست و قیمت ──────────────────────────────────────────────

def _sort_key(code, index):
    return FEATURED.index(code) if code in FEATURED else len(FEATURED) + index


def sync_game_packages(force=False):
    """فهرست بازی‌ها از سایت، بها از تامین‌کننده، قیمت با نرخ زنده + ۵٬۰۰۰ و سود.

    فقط با نرخ زنده (نوبیتکس مستقیم یا از سایت) قیمت عوض می‌شود. خروجی: تعداد
    بسته‌های به‌روزشده. اگر سایت یا تامین‌کننده جواب نداد، چیزی خاموش نمی‌شود.
    """
    db = _db()
    if not force:
        raw = db.get_setting(SYNC_KEY, '')
        if raw:
            try:
                last = datetime.fromisoformat(str(raw))
                if (datetime.now(timezone.utc) - last).total_seconds() < SYNC_EVERY_SECONDS:
                    return 0
            except ValueError:
                pass
    import profitability
    import site_api

    fx = profitability.get_usd_toman_rate(force=True)
    if not fx.get('ok') or fx.get('source') not in ('nobitex_usdtirt_best_ask', 'site_nobitex'):
        _LOG.warning('game sync skipped: dollar rate is not live (%s)', fx.get('source') or fx.get('error'))
        return 0
    rate = int(fx['rate'])
    listing = site_api.fetch_site_games()
    if not isinstance(listing, dict) or not listing.get('ok'):
        _LOG.warning('game sync: site game list unavailable: %s', (listing or {}).get('error'))
        return 0
    margin = game_profit_percent()
    updated = 0
    seen_codes = []
    with db.get_conn() as conn, conn.cursor() as cur:
        for index, game in enumerate(listing.get('games') or []):
            code = str(game.get('code') or '').strip()
            if not code or code.lower() == 'telegram':
                continue
            catalogue = g2bulk.get_game_catalogue(code, force=True)
            if not catalogue.get('ok'):
                seen_codes.append(code)  # قطعیِ لحظه‌ای: بسته‌های قبلی خاموش نشوند
                continue
            names_fa = {str(p.get('catalogue_name') or '').strip(): str(p.get('title_fa') or '').strip()
                        for p in game.get('packages') or []}
            wanted = [item for item in catalogue['items'] if item['name'] in names_fa]
            if not wanted:
                continue
            seen_codes.append(code)
            cur.execute(
                'INSERT INTO "BotGames" ("Code","TitleFa","Unit","NeedsServer","Servers","Warning",'
                '"SortOrder","IsActive","UpdatedAt") VALUES (%s,%s,%s,%s,%s,%s,%s,true,now()) '
                'ON CONFLICT ("Code") DO UPDATE SET "TitleFa"=EXCLUDED."TitleFa",'
                '"Unit"=EXCLUDED."Unit","NeedsServer"=EXCLUDED."NeedsServer",'
                '"Servers"=EXCLUDED."Servers","Warning"=EXCLUDED."Warning",'
                '"SortOrder"=EXCLUDED."SortOrder","IsActive"=true,"UpdatedAt"=now() RETURNING "Id"',
                (code, str(game.get('title_fa') or code)[:190], str(game.get('unit') or '')[:60],
                 bool(game.get('needs_server')), '\n'.join(str(s) for s in game.get('servers') or []),
                 str(game.get('warning') or ''), _sort_key(code, index)))
            game_id = cur.fetchone()[0]
            live_names = []
            for position, item in enumerate(sorted(wanted, key=lambda i: i['cost_usd'])):
                price = db.compute_gem_sale_price(item['cost_usd'], rate, margin)
                if price <= 0:
                    continue
                title = names_fa.get(item['name']) or item['name']
                cur.execute(
                    'INSERT INTO "GameTopupPackages" ("GameId","CatalogueName","Title","CostUsd",'
                    '"Price","IsActive","SortOrder","UpdatedAt") VALUES (%s,%s,%s,%s,%s,true,%s,now()) '
                    'ON CONFLICT ("GameId","CatalogueName") DO UPDATE SET "Title"=EXCLUDED."Title",'
                    '"CostUsd"=EXCLUDED."CostUsd","Price"=EXCLUDED."Price","IsActive"=true,'
                    '"SortOrder"=EXCLUDED."SortOrder","UpdatedAt"=now()',
                    (game_id, item['name'][:160], title[:190], str(item['cost_usd']), price, position))
                live_names.append(item['name'][:160])
                updated += 1
            cur.execute('UPDATE "GameTopupPackages" SET "IsActive"=false,"UpdatedAt"=now() '
                        'WHERE "GameId"=%s AND "CatalogueName" <> ALL(%s)', (game_id, live_names))
        if seen_codes:
            cur.execute('UPDATE "BotGames" SET "IsActive"=false,"UpdatedAt"=now() '
                        'WHERE "Code" <> ALL(%s)', (seen_codes,))
        for key, value in ((SYNC_KEY, datetime.now(timezone.utc).isoformat()),
                           ('games_usd_toman_rate', str(rate))):
            cur.execute('INSERT INTO "BotSettings"("Key","Value","UpdatedAt") VALUES(%s,%s,now()) '
                        'ON CONFLICT ("Key") DO UPDATE SET "Value"=EXCLUDED."Value","UpdatedAt"=now()',
                        (key, value))
        conn.commit()
    return updated
