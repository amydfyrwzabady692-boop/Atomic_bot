"""دکمه‌های رنگی تلگرام (Bot API: style = primary / success / danger)."""
from telegram import InlineKeyboardButton, KeyboardButton

_PATCHED = False

_DANGER_MARKERS = (
    'انصراف', 'لغو', 'رد نهایی', 'برای رد', 'بلاک کاربر', 'کسر از',
    'حذف اطلاعات', 'صفر کردن', 'ناموجود', 'فعال نیست', 'اطلاعات ناقص',
)
_SUCCESS_MARKERS = (
    'خرید', 'تأیید', 'تایید', 'پرداخت کردم', 'انجام', 'ادامه',
    'فعال است', 'آنبلاک', 'باز کردن درگاه', 'شارژ کیف',
)
_PRIMARY_MARKERS = (
    'جم با آیدی', 'جم با ایدی', 'زرین‌پال', 'زرین\u200cپال', 'محصولات فری',
    'مرکز عملیات', 'کیف پول', 'پشتیبانی',
)
_NEUTRAL_MARKERS = (
    'بازگشت', 'منوی اصلی', 'بروزرسانی', 'اصلاح آیدی', 'دسته‌بندی',
)

_DANGER_CB = (
    'cancel', 'gem_cancel', 'cred_cancel', 'support_cancel',
    'admin_no_', 'admin_review_no_', 'wadmin_no_', 'wadmin_review_no_',
    'adm_cancel_', 'adm_block_1_', 'adm_tclose_', 'adm_wdeduct_',
    'adm_wempty_', 'cred_2fa_no', 'admx_credbad_',
)
_SUCCESS_CB = (
    'gbuy_', 'gem_confirm', 'cred_confirm', 'pay_zp_', 'pay_wallet_',
    'zp_check_', 'paid_done_', 'wcard_done_', 'wchk_', 'admin_ok_',
    'wadmin_ok_', 'adm_done_', 'cred_2fa_yes', 'admx_creddone_',
    'cbuy_',
)
_PRIMARY_CB = (
    'gems_by_id', 'gems_credentials', 'pay_card_', 'wchg_', 'wpay_',
    'sens_buy_', 'sens_pc', 'sens_mobile', 'storecat_', 'gems_page_',
)


def guess_style(text, callback_data=None):
    t = str(text or '')
    cd = str(callback_data or '')
    if cd == 'noop':
        return None
    if any(m in t for m in _NEUTRAL_MARKERS):
        return None
    if any(cd.startswith(p) or cd == p.rstrip('_') for p in _DANGER_CB) or any(
        m in t for m in _DANGER_MARKERS
    ):
        return 'danger'
    if any(cd.startswith(p) or cd == p.rstrip('_') for p in _SUCCESS_CB) or any(
        m in t for m in _SUCCESS_MARKERS
    ):
        return 'success'
    if cd.startswith('gem_') and cd[4:].isdigit():
        return 'primary'
    if any(cd.startswith(p) or cd == p.rstrip('_') for p in _PRIMARY_CB) or any(
        m in t for m in _PRIMARY_MARKERS
    ):
        return 'primary'
    return None


def _inject_style(kwargs, style):
    if not style:
        return kwargs
    extra = dict(kwargs.get('api_kwargs') or {})
    extra.setdefault('style', style)
    kwargs['api_kwargs'] = extra
    return kwargs


def _patch_init(cls):
    orig = cls.__init__
    try:
        accepts_style = 'style' in orig.__code__.co_varnames
    except AttributeError:
        accepts_style = False

    def wrapped(self, text, *args, style=None, **kwargs):
        guessed = style or guess_style(text, kwargs.get('callback_data'))
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
