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
    return bool(aid and int(user_id) == aid)


async def notify_admin(bot, text, reply_markup=None, parse_mode='Markdown'):
    aid = admin_id()
    if not aid:
        print('[ADMIN] ADMIN_CHAT_ID empty — notify skipped')
        return False
    try:
        await bot.send_message(
            chat_id=aid,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    except Exception as e:
        print(f'[ADMIN] notify failed: {e}')
        return False
