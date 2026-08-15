"""دکمه‌های رنگی تلگرام (Bot API: style = primary / success / danger)."""
from telegram import InlineKeyboardButton, KeyboardButton

_PATCHED = False

# تلگرام فقط همین سه رنگ را دارد؛ هر دکمه یکی می‌گیرد.
# سبز = خرید/پرداخت/تأیید | قرمز = انصراف/حذف/هشدار | آبی = بقیه

_DANGER_MARKERS = (
    'انصراف', 'لغو', 'رد نهایی', 'برای رد', 'بلاک کاربر', 'کسر از',
    'حذف اطلاعات', 'صفر کردن', 'ناموجود', 'فعال نیست', 'اطلاعات ناقص',
    'توقف', 'بستن تیکت',
)
_SUCCESS_MARKERS = (
    'خرید', 'تأیید', 'تایید', 'پرداخت کردم', 'انجام', 'ادامه',
    'فعال است', 'آنبلاک', 'باز کردن درگاه', 'شارژ', 'کیف پول',
    'زرین‌پال', 'زرین\u200cپال', 'فروشگاه اکانت',
)

_DANGER_CB = (
    'cancel', 'gem_cancel', 'cred_cancel', 'support_cancel',
    'admin_no_', 'admin_review_no_', 'wadmin_no_', 'wadmin_review_no_',
    'adm_cancel_', 'adm_block_1_', 'adm_tclose_', 'adm_wdeduct_',
    'adm_wempty_', 'cred_2fa_no', 'admx_credbad_', 'admx_toggle_',
    'admin_kyc_no',
)
_SUCCESS_CB = (
    'gbuy_', 'gem_confirm', 'cred_confirm', 'pay_zp_', 'pay_wallet_',
    'zp_check_', 'paid_done_', 'wcard_done_', 'wchk_', 'admin_ok_',
    'wadmin_ok_', 'adm_done_', 'cred_2fa_yes', 'admx_creddone_',
    'cbuy_', 'wchg_', 'wpay_zp_', 'wallet', 'sens_buy_', 'adm_wal_',
    'adm_retry_', 'adm_block_0_',
)


def _matches(cd, prefixes):
    return any(cd == p.rstrip('_') or cd.startswith(p) for p in prefixes)


def guess_style(text, callback_data=None):
    t = str(text or '')
    cd = str(callback_data or '')
    if _matches(cd, _DANGER_CB) or any(m in t for m in _DANGER_MARKERS):
        return 'danger'
    if _matches(cd, _SUCCESS_CB) or any(m in t for m in _SUCCESS_MARKERS):
        return 'success'
    return 'primary'


def _inject_style(kwargs, style):
    if not style:
        return kwargs
    extra = dict(kwargs.get('api_kwargs') or {})
    extra['style'] = style
    kwargs['api_kwargs'] = extra
    return kwargs


def _patch_init(cls):
    orig = cls.__init__
    try:
        accepts_style = 'style' in orig.__code__.co_varnames
    except AttributeError:
        accepts_style = False

    def wrapped(self, text, *args, style=None, **kwargs):
        guessed = style or guess_style(text, kwargs.get('callback_data')) or 'primary'
        if accepts_style:
            orig(self, text, *args, style=guessed, **kwargs)
            return
        orig(self, text, *args, **_inject_style(kwargs, guessed))

    cls.__init__ = wrapped


def apply_button_styles():
    global _PATCHED
    if _PATCHED:
        return
    _patch_init(InlineKeyboardButton)
    _patch_init(KeyboardButton)
    _PATCHED = True


apply_button_styles()
