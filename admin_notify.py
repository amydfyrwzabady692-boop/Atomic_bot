"""ارسال اعلان به ادمین (ADMIN_CHAT_ID)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

ADMIN_CHAT_ID = (os.getenv('ADMIN_CHAT_ID') or '').strip()


def admin_id():
    return int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID.isdigit() else None


def is_admin(user_id) -> bool:
    aid = admin_id()
    if aid and int(user_id) == aid:
        return True
    try:
        from db import is_bot_admin
        return is_bot_admin(user_id)
    except Exception:
        return False


def admin_ids():
    ids = []
    if admin_id():
        ids.append(admin_id())
    try:
        from db import list_bot_admins
        ids.extend(int(row[0]) for row in list_bot_admins() if row[2] and str(row[0]).isdigit())
    except Exception:
        pass
    return list(dict.fromkeys(ids))


async def notify_admin(bot, text, reply_markup=None, parse_mode='Markdown'):
    recipients = admin_ids()
    if not recipients:
        print('[ADMIN] ADMIN_CHAT_ID empty — notify skipped')
        return False
    sent = False
    last_error = None
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
            last_error = e
            print(f'[ADMIN] notify {aid} failed: {e}')
    return sent
