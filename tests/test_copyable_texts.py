"""شماره‌هایی که ادمین یا خریدار برمی‌دارد با یک لمس کپی می‌شوند، و ویدیوی احراز
با دست‌نوشته و کارت ملی در دستِ خریدار گرفته می‌شود (همان سایت).
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from handlers import identity, site_panel

CARD = '6037990000000006'


class ReviewCaptionTests(unittest.TestCase):
    def test_card_phone_and_id_are_copyable_and_the_name_is_escaped(self):
        user = SimpleNamespace(id=42, username='buyer', full_name='<b>علی</b> & رضا')
        text = identity.admin_caption(5, user, CARD, '09125550000', [], False)
        self.assertIn(f'<code>{CARD}</code>', text)
        self.assertIn('<code>09125550000</code>', text)
        self.assertIn('<code>42</code>', text)
        self.assertIn('&lt;b&gt;علی&lt;/b&gt; &amp; رضا', text)
        self.assertNotIn('<b>', text)

    def test_the_review_message_is_sent_as_html(self):
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=9)),
                              send_media_group=AsyncMock(), send_photo=AsyncMock())
        with patch.object(identity, 'remember_notices'):
            asyncio.run(identity.send_for_review(bot, 5, 'متن', ['p1'], recipients=[1]))
        self.assertEqual(bot.send_message.call_args.kwargs['parse_mode'], 'HTML')


class VideoStepTests(unittest.TestCase):
    def test_the_note_and_the_national_card_are_held_in_hand(self):
        for only in (False, True):
            text = identity._video_intro_text(only=only)
            self.assertIn('دست‌نوشته', text)
            self.assertIn('کارت ملی', text)
            self.assertIn('در دستت بگیر', text)
            self.assertNotIn('کارت بانکی', text)
        # فقط ویدیو مانده: عکس‌ها قبلاً رفته‌اند، پس متنِ دست‌نوشته دوباره داده می‌شود.
        self.assertIn('روی کاغذ بنویس', identity._video_intro_text(only=True))
        self.assertIn('در اتومیک شاپ می‌فرستم', identity._video_intro_text(only=True))

    def test_the_reviewer_checks_the_same(self):
        user = SimpleNamespace(id=42, username='', full_name='علی')
        text = identity.admin_caption(5, user, CARD, '', [], False, level=identity.LEVEL_VIDEO)
        self.assertIn('دست‌نوشته و کارت ملی در دستِ خریدار', text)
        self.assertNotIn('هر دو کارت', text)


class SiteReceiptCaptionTests(unittest.TestCase):
    def test_tracking_phone_and_source_card_are_copyable(self):
        item = {'ok': True, 'kind': 'سفارش', 'tracking_code': 'CT00000091', 'amount': 500000,
                'order_id': 7, 'phone': '09120000002', 'status': 'در انتظار بررسی',
                'paid_from': CARD, 'photo_url': 'https://example.test/r.png'}
        message = SimpleNamespace(reply_photo=AsyncMock(), reply_document=AsyncMock())
        query = SimpleNamespace(message=message, edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1))
        with patch.object(site_panel, 'fetch_site_receipt', MagicMock(return_value=item)), \
                patch.object(site_panel, '_nav_back', MagicMock(return_value=[])):
            asyncio.run(site_panel._show_site_receipt(update, 91))
        kwargs = message.reply_photo.call_args.kwargs
        self.assertEqual(kwargs['parse_mode'], 'HTML')
        for value in ('CT00000091', '09120000002', CARD):
            self.assertIn(f'<code>{value}</code>', kwargs['caption'])


if __name__ == '__main__':
    unittest.main()
