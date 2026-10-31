"""احراز هویت بعد از پرداخت در ربات — همان قاعدهٔ سایت.

پیش از پرداخت فقط «بازگشت وجه نداریم» پذیرفته می‌شود. بعد از پرداخت، اگر جمع
خریدهای پرداخت‌شدهٔ چهار روزِ خریدارِ احرازنشده (با همین سفارش) به یک میلیون
تومان برسد، سفارش «kyc_hold» می‌ماند تا کارت بانکی، کارت ملی و سلفی با کارت ملی
تایید شود؛ با تایید، سفارش‌های منتظر خودکار تحویل می‌شوند.
"""
import asyncio
import pathlib
import random
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import db
from handlers import identity, site_outbox

ROOT = pathlib.Path(__file__).resolve().parents[1]
CARD = '6037990000000006'
OTHER_CARD = '6219860000000001'
# نشانه‌های قاعده (سقف و بازهٔ تست‌ها): هیچ‌کدام نباید به خریدار برسد.
RULE_MARKS = ('۱٬۰۰۰٬۰۰۰', '1,000,000', '۴ روز', 'جمع خریدهایت', 'سقف')


def run(coro):
    return asyncio.run(coro)


def random_card():
    """شمارهٔ ۱۶ رقمیِ معتبر و تازه — دیتابیسِ تست از اجراهای قبل کارت دارد."""
    body = '603799' + ''.join(random.choice('0123456789') for _ in range(9))
    for check in '0123456789':
        number = body + check
        total = 0
        for index, ch in enumerate(reversed(number)):
            digit = int(ch) * (2 if index % 2 else 1)
            total += digit - 9 if digit > 9 else digit
        if total % 10 == 0:
            return number


def _db_available():
    try:
        db.open_db_pool()
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT to_regclass(%s)', ('"IdentityVerifications"',))
            if cur.fetchone()[0] is None:
                db.ensure_admin_schema()
        return True
    except Exception:  # noqa: BLE001 — بدون Postgres فقط تست‌های بدون دیتابیس اجرا می‌شوند
        return False


DB = _db_available()


@unittest.skipUnless(DB, 'Postgres is not available')
class HoldRuleDbTests(unittest.TestCase):
    """تصمیمِ نگه‌داشتن روی دیتابیسِ واقعی."""

    def setUp(self):
        self.tg = str(random.randint(9_000_000_000, 9_999_999_999))
        self.user_id, _ = db.get_or_create_user(self.tg, 'علی', 'رضایی', '')
        self.orders = []
        self.card, self.other_card = random_card(), random_card()

    def tearDown(self):
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM "Orders" WHERE "TelegramId" IN (%s, %s)', (self.tg, '1' + self.tg))
            cur.execute('DELETE FROM "IdentityVerifications" WHERE "TelegramId" IN (%s, %s)',
                        (self.tg, '1' + self.tg))
            cur.execute('DELETE FROM "CardVerifications" WHERE "TelegramId" IN (%s, %s)',
                        (self.tg, '1' + self.tg))
            cur.execute('DELETE FROM "Users" WHERE "TelegramId" IN (%s, %s)', (self.tg, '1' + self.tg))
            conn.commit()

    def order(self, total, *, status='processing', days_ago=0, tg=None):
        tg = tg or self.tg
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "Orders" ("UserId","TelegramId","TotalAmount","PaymentMethod",'
                '"PaymentExpectedAmount","PaymentVerifiedAt","PaymentRefId","Status","CreatedAt") '
                'VALUES (%s,%s,%s,\'card_transfer\',%s,now(),\'REF\',%s,'
                'now()-make_interval(days => %s)) RETURNING "Id"',
                (self.user_id, tg, total, total, status, days_ago))
            order_id = cur.fetchone()[0]
            cur.execute('INSERT INTO "OrderItems" ("OrderId","ProductName","Price","Quantity") '
                        'VALUES (%s,\'جم\',%s,1)', (order_id, total))
            conn.commit()
        self.orders.append(order_id)
        return order_id

    def status(self, order_id):
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT "Status","KycHoldAt","KycReleasedAt" FROM "Orders" WHERE "Id"=%s',
                        (order_id,))
            return cur.fetchone()

    def test_under_the_threshold_nothing_is_held(self):
        order_id = self.order(900_000)
        self.assertFalse(db.claim_identity_hold(order_id))
        self.assertEqual(self.status(order_id)[0], 'processing')

    def test_four_days_of_purchases_add_up(self):
        self.order(600_000, status='delivered', days_ago=2)
        order_id = self.order(400_000)
        self.assertTrue(db.claim_identity_hold(order_id))
        status, held_at, _ = self.status(order_id)
        self.assertEqual(status, 'kyc_hold')
        self.assertIsNotNone(held_at)
        # دوباره پرسیدن همان جواب را می‌دهد و چیزی را خراب نمی‌کند.
        self.assertTrue(db.claim_identity_hold(order_id))

    def test_older_and_cancelled_purchases_do_not_count(self):
        self.order(900_000, status='delivered', days_ago=5)
        self.order(900_000, status='cancelled')
        self.assertFalse(db.claim_identity_hold(self.order(300_000)))

    def test_fulfillment_stops_at_the_hold(self):
        order_id = self.order(1_200_000)
        self.assertEqual(db.fulfill_order(order_id), (True, 'kyc_hold'))
        self.assertEqual(self.status(order_id)[0], 'kyc_hold')

    def test_a_verified_buyer_is_never_held(self):
        row_id, _ = db.submit_identity_verification(self.tg, self.card, '09120000001', 'a', 'b', 'c')
        db.review_identity_verification(row_id, approve=True, reviewer='مدیر')
        self.assertFalse(db.claim_identity_hold(self.order(5_000_000)))

    def test_the_old_gateway_verification_still_counts(self):
        db.set_kyc_status(self.tg, 'approved')
        self.assertEqual(db.identity_state(self.tg), 'approved')
        self.assertFalse(db.claim_identity_hold(self.order(2_000_000)))

    def test_a_released_order_is_not_held_again(self):
        order_id = self.order(1_500_000)
        self.assertTrue(db.claim_identity_hold(order_id))
        self.assertEqual(db.release_identity_holds(self.tg), [order_id])
        status, held_at, released_at = self.status(order_id)
        self.assertEqual((status, held_at), ('processing', None))
        self.assertIsNotNone(released_at)
        self.assertFalse(db.claim_identity_hold(order_id))

    def test_the_admin_can_release_one_order(self):
        order_id = self.order(1_500_000)
        db.claim_identity_hold(order_id)
        self.assertTrue(db.release_identity_hold_order(order_id))
        self.assertFalse(db.release_identity_hold_order(order_id))

    def test_the_hold_notice_is_claimed_once(self):
        order_id = self.order(1_500_000)
        db.claim_identity_hold(order_id)
        self.assertTrue(db.claim_identity_hold_notice(order_id))
        self.assertFalse(db.claim_identity_hold_notice(order_id))
        self.assertEqual([row[0] for row in db.list_identity_held_orders(self.tg)], [order_id])

    def test_approval_registers_the_card_and_is_decided_once(self):
        row_id, status = db.submit_identity_verification(self.tg, self.card, '09120000001', 'a', 'b', 'c')
        self.assertEqual(status, 'pending')
        self.assertEqual(db.identity_state(self.tg), 'pending')
        self.assertEqual(db.review_identity_verification(row_id, approve=True, reviewer='مدیر'),
                         (self.tg, self.card))
        self.assertIsNone(db.review_identity_verification(row_id, approve=False, reviewer='دیگری'))
        self.assertEqual([c[1] for c in db.approved_cards(self.tg)], [self.card])
        self.assertTrue(db.is_kyc_approved(self.tg))
        # ارسالِ دوبارهٔ مدارک تاییدشده را خراب نمی‌کند.
        self.assertEqual(db.submit_identity_verification(self.tg, self.other_card, '', 'x', 'y', 'z'),
                         (row_id, 'approved'))

    def test_a_rejection_can_be_sent_again(self):
        row_id, _ = db.submit_identity_verification(self.tg, self.card, '09120000001', 'a', 'b', 'c')
        db.review_identity_verification(row_id, approve=False, reviewer='مدیر', reason='تار')
        self.assertEqual(db.identity_state(self.tg), 'rejected')
        again, status = db.submit_identity_verification(self.tg, self.card, '09120000001', 'd', 'e', 'f')
        self.assertEqual((again, status), (row_id, 'pending'))
        self.assertEqual(db.get_identity_verification(row_id)[9], '')

    def test_a_card_belongs_to_one_account(self):
        other = '1' + self.tg
        db.get_or_create_user(other, 'x', '', '')
        db.submit_identity_verification(other, self.card, '09120000002', 'a', 'b', 'c')
        self.assertTrue(db.identity_card_owned_by_other(self.tg, self.card))
        self.assertFalse(db.identity_card_owned_by_other(self.tg, self.other_card))
        self.assertFalse(db.import_site_identity(self.tg, self.card, '09120000001'))

    def test_a_site_verification_is_imported(self):
        self.assertTrue(db.import_site_identity(self.tg, self.card, '09120000001'))
        self.assertEqual(db.identity_state(self.tg), 'approved')
        self.assertEqual([c[1] for c in db.approved_cards(self.tg)], [self.card])

    def test_terms_are_accepted_once_per_version(self):
        self.assertFalse(db.terms_accepted(self.tg))
        db.accept_terms(self.tg)
        self.assertTrue(db.terms_accepted(self.tg))


class TermsGateTests(unittest.TestCase):
    def test_no_refund_is_shown_before_paying_and_identity_is_not(self):
        query = SimpleNamespace(edit_message_text=AsyncMock())
        with patch.object(identity, 'terms_accepted', return_value=False), \
                patch.object(identity, 'identity_threshold_toman', return_value=1_000_000), \
                patch.object(identity, 'identity_window_days', return_value=4):
            self.assertFalse(run(identity.terms_gate(query, 5, 12)))
        text = query.edit_message_text.call_args.args[0]
        self.assertIn('بازگشت وجه نداریم', text)
        self.assertNotIn('احراز', text)
        for mark in RULE_MARKS:
            self.assertNotIn(mark, text)
        markup = query.edit_message_text.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'terms_ok_12')

    def test_accepted_terms_pass(self):
        query = SimpleNamespace(edit_message_text=AsyncMock())
        with patch.object(identity, 'terms_accepted', return_value=True):
            self.assertTrue(run(identity.terms_gate(query, 5, 12)))
        query.edit_message_text.assert_not_called()


class HoldNoticeTests(unittest.TestCase):
    def setUp(self):
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.patches = [
            patch.object(identity, 'get_order', return_value=(9, 1, 1_200_000, 'kyc_hold',
                                                              'card_transfer', None, '555', 0)),
            patch.object(identity, 'identity_state', return_value='none'),
            patch.object(identity, 'identity_threshold_toman', return_value=1_000_000),
            patch.object(identity, 'identity_window_days', return_value=4),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_the_buyer_and_admins_hear_it_once(self):
        with patch.object(identity, 'claim_identity_hold_notice', return_value=True), \
                patch.object(identity, 'notify_admin', new=AsyncMock()) as admin:
            run(identity.announce_identity_hold(self.bot, 9))
        kwargs = self.bot.send_message.call_args.kwargs
        self.assertEqual(kwargs['chat_id'], 555)
        self.assertIn('برای تکمیل این سفارش یک‌بار احراز هویت لازم است', kwargs['text'])
        for mark in RULE_MARKS:
            self.assertNotIn(mark, kwargs['text'])
        self.assertEqual(kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'idv_start')
        self.assertIn('منتظر احراز هویت', admin.call_args.args[1])

    def test_a_repeat_from_a_background_loop_is_silent(self):
        with patch.object(identity, 'claim_identity_hold_notice', return_value=False), \
                patch.object(identity, 'notify_admin', new=AsyncMock()) as admin:
            run(identity.announce_identity_hold(self.bot, 9))
        self.bot.send_message.assert_not_called()
        admin.assert_not_called()

    def test_the_payment_result_handler_routes_the_hold(self):
        from handlers import payment

        with patch('handlers.identity.announce_identity_hold', new=AsyncMock()) as announce:
            result = run(payment._handle_fulfill_result(self.bot, 9, True, 'kyc_hold', tg_id=555))
        self.assertEqual(result, 'kyc_hold')
        announce.assert_awaited_once()


def _admin_query(data):
    return SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_reply_markup=AsyncMock(),
        edit_message_text=AsyncMock(), edit_message_caption=AsyncMock(),
        message=SimpleNamespace(photo=None, document=None), from_user=SimpleNamespace(id=1))


def _admin_update(query):
    return SimpleNamespace(callback_query=query,
                           effective_user=SimpleNamespace(id=1, full_name='مدیر', username='boss'))


class AdminReviewTests(unittest.TestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    def review(self, data, **patches):
        query = _admin_query(data)
        with patch.object(identity, 'is_admin', return_value=True), \
                patch.object(identity, 'broadcast_reviewed', new=AsyncMock()):
            run(identity.identity_review(_admin_update(query), self.ctx))
        return query

    def test_reject_first_asks_for_a_reason(self):
        query = self.review('idv_no_5')
        markup = query.edit_message_reply_markup.call_args.kwargs['reply_markup']
        callbacks = [row[0].callback_data for row in markup.inline_keyboard]
        self.assertIn('idv_rj_5_blur', callbacks)
        self.assertIn('idv_back_5', callbacks)

    def test_rejecting_with_a_reason_tells_the_buyer(self):
        with patch.object(identity, 'review_identity_verification',
                          return_value=('555', CARD)) as decide:
            self.review('idv_rj_5_name')
        self.assertEqual(decide.call_args.kwargs['reason'], identity.REJECT_REASONS['name'])
        self.assertFalse(decide.call_args.kwargs['approve'])
        kwargs = self.ctx.bot.send_message.call_args.kwargs
        self.assertEqual(kwargs['chat_id'], 555)
        self.assertIn(identity.REJECT_REASONS['name'], kwargs['text'])
        self.assertEqual(kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'idv_resend')

    def test_approval_releases_the_held_orders(self):
        with patch.object(identity, 'review_identity_verification', return_value=('555', CARD)), \
                patch.object(identity, 'release_and_deliver', new=AsyncMock(return_value=[7, 8])):
            self.review('idv_ok_5')
        self.assertIn('۲ سفارش', self.ctx.bot.send_message.call_args.kwargs['text'])

    def test_a_second_decision_is_refused(self):
        with patch.object(identity, 'review_identity_verification', return_value=None), \
                patch.object(identity, 'get_identity_verification',
                             return_value=(5, '555', CARD, '', '', '', '', 'approved', 'bot', '',
                                           'مدیر اول', None, None)):
            query = self.review('idv_ok_5')
        self.assertIn('مدیر اول', query.answer.call_args.args[0])
        self.ctx.bot.send_message.assert_not_called()

    def test_site_documents_are_decided_on_the_site(self):
        with patch('site_api.call_site_identity_review',
                   return_value={'ok': True, 'status': 'rejected', 'released': 0}) as call:
            self.review('site_idv_rj_7_selfie')
        self.assertEqual(call.call_args.args[:2], (7, 'reject'))
        self.assertEqual(call.call_args.args[4], identity.REJECT_REASONS['selfie'])

    def test_strangers_cannot_review(self):
        query = _admin_query('idv_ok_5')
        with patch.object(identity, 'is_admin', return_value=False), \
                patch.object(identity, 'review_identity_verification') as decide:
            run(identity.identity_review(_admin_update(query), self.ctx))
        decide.assert_not_called()
        self.assertIn('دسترسی', query.answer.call_args.args[0])


class ConversationTests(unittest.TestCase):
    def _update(self, *, contact_user_id=None, text=None, photo=None):
        message = SimpleNamespace(
            contact=(SimpleNamespace(user_id=contact_user_id, phone_number='+989125550000')
                     if contact_user_id else None),
            text=text, photo=photo, document=None,
            reply_text=AsyncMock(), reply_photo=AsyncMock(return_value=SimpleNamespace(photo=None)))
        user = SimpleNamespace(id=42, username='buyer', full_name='علی رضایی')
        return SimpleNamespace(message=message, effective_user=user, effective_message=message,
                               callback_query=None)

    def _start(self, state, held):
        update = self._update()
        update.effective_user = SimpleNamespace(id=42, username='buyer', first_name='علی',
                                                last_name='', full_name='علی')
        ctx = SimpleNamespace(user_data={}, bot=None)
        with patch.object(identity, 'get_or_create_user', return_value=(7, False)), \
                patch.object(identity, 'identity_state', return_value=state), \
                patch.object(identity, 'list_identity_held_orders', return_value=held):
            result = run(identity.idv_start(update, ctx))
        return result, update.message.reply_text

    def test_nothing_is_asked_when_no_order_waits(self):
        for state in ('none', 'rejected'):
            result, reply = self._start(state, [])
            self.assertEqual(result, identity.ConversationHandler.END)
            self.assertIn('نیازی به احراز هویت نداری', reply.call_args.args[0])

    def test_a_waiting_order_opens_the_flow_without_the_rule(self):
        result, reply = self._start('none', [(9, '42', 1_200_000, 0, 'card_transfer', None)])
        self.assertEqual(result, identity.WAIT_PHONE)
        intro = reply.call_args_list[0].args[0]
        self.assertIn('برای تکمیل سفارشت یک‌بار احراز هویت لازم است', intro)
        for mark in RULE_MARKS:
            self.assertNotIn(mark, intro)

    def test_someone_elses_contact_is_refused(self):
        ctx = SimpleNamespace(user_data={'idv': {}}, bot=None)
        state = run(identity.idv_phone(self._update(contact_user_id=99), ctx))
        self.assertEqual(state, identity.WAIT_PHONE)
        self.assertNotIn('phone', ctx.user_data['idv'])

    def test_a_site_verification_finishes_it_here(self):
        ctx = SimpleNamespace(user_data={'idv': {}}, bot=SimpleNamespace())
        site = {'ok': True, 'approved': True, 'card_number': CARD}
        with patch('site_api.fetch_site_identity', return_value=site) as fetch, \
                patch.object(identity, 'import_site_identity', return_value=True) as imp, \
                patch.object(identity, 'release_and_deliver', new=AsyncMock(return_value=[3])):
            state = run(identity.idv_phone(self._update(contact_user_id=42), ctx))
        fetch.assert_called_once_with('09125550000', 42)
        imp.assert_called_once_with(42, CARD, '09125550000')
        self.assertEqual(state, identity.ConversationHandler.END)

    def test_no_site_verification_continues_with_the_card(self):
        ctx = SimpleNamespace(user_data={'idv': {}}, bot=SimpleNamespace())
        with patch('site_api.fetch_site_identity', return_value={'ok': True, 'approved': False}):
            state = run(identity.idv_phone(self._update(contact_user_id=42), ctx))
        self.assertEqual(state, identity.WAIT_NUMBER)
        self.assertEqual(ctx.user_data['idv']['phone'], '09125550000')

    def test_a_site_outage_does_not_block_it(self):
        ctx = SimpleNamespace(user_data={'idv': {}}, bot=SimpleNamespace())
        with patch('site_api.fetch_site_identity', side_effect=OSError('down')):
            state = run(identity.idv_phone(self._update(contact_user_id=42), ctx))
        self.assertEqual(state, identity.WAIT_NUMBER)

    def test_a_card_on_another_account_is_refused(self):
        ctx = SimpleNamespace(user_data={'idv': {'phone': '09125550000'}}, bot=None)
        with patch.object(identity, 'identity_card_owned_by_other', return_value=True):
            state = run(identity.idv_number(self._update(text=CARD), ctx))
        self.assertEqual(state, identity.ConversationHandler.END)
        self.assertNotIn('idv', ctx.user_data)

    def test_the_card_step_sends_the_sample(self):
        ctx = SimpleNamespace(user_data={'idv': {'phone': '09125550000'}}, bot=None)
        update = self._update(text=CARD)
        with patch.object(identity, 'identity_card_owned_by_other', return_value=False):
            state = run(identity.idv_number(update, ctx))
        self.assertEqual(state, identity.WAIT_CARD)
        self.assertIn('CVV2', update.message.reply_photo.call_args.kwargs['caption'])

    def test_a_file_instead_of_a_photo_is_asked_again(self):
        ctx = SimpleNamespace(user_data={'idv': {}}, bot=None)
        update = self._update()
        update.message.document = SimpleNamespace(mime_type='image/jpeg')
        self.assertEqual(run(identity.idv_card_photo(update, ctx)), identity.WAIT_CARD)
        self.assertIn('به‌صورت عکس', update.message.reply_text.call_args.args[0])

    def test_the_last_photo_sends_everything_for_review(self):
        bot = SimpleNamespace(send_media_group=AsyncMock(), send_message=AsyncMock(
            return_value=SimpleNamespace(message_id=77)))
        ctx = SimpleNamespace(bot=bot, user_data={'idv': {
            'phone': '09125550000', 'number': CARD, 'card': 'F1', 'national': 'F2'}})
        update = self._update(photo=[SimpleNamespace(file_id='F3')])
        with patch.object(identity, 'submit_identity_verification', return_value=(5, 'pending')) as sub, \
                patch.object(identity, 'list_identity_held_orders',
                             return_value=[(9, '42', 1_200_000, 0, 'card_transfer', None)]), \
                patch.object(identity, 'identity_card_owned_by_other', return_value=False), \
                patch.object(identity, 'admin_ids', return_value=[1, 2]), \
                patch.object(identity, 'remember_notices') as remember:
            state = run(identity.idv_selfie(update, ctx))
        self.assertEqual(state, identity.ConversationHandler.END)
        sub.assert_called_once_with(42, CARD, '09125550000', 'F1', 'F2', 'F3')
        self.assertEqual(bot.send_media_group.await_count, 2)
        media = bot.send_media_group.call_args.kwargs['media']
        self.assertEqual([m.media for m in media], ['F1', 'F2', 'F3'])
        caption = bot.send_message.call_args.kwargs['text']
        self.assertIn('سفارش منتظر: 1', caption)
        self.assertIn('کارت ملی کنار دست‌نوشته', caption)
        self.assertIn(f'تاریخی از {identity.note_date(identity._tehran_day() - timedelta(days=3))} '
                      f'تا {identity.note_date()}', caption)
        self.assertEqual(remember.call_args.args[:2], ('bot_idv', 5))

    def test_the_card_photo_step_asks_for_the_note(self):
        identity._SAMPLE_IDS.clear()
        ctx = SimpleNamespace(user_data={'idv': {'phone': '09125550000', 'number': CARD}}, bot=None)
        update = self._update(photo=[SimpleNamespace(file_id='F1')])
        self.assertEqual(run(identity.idv_card_photo(update, ctx)), identity.WAIT_NATIONAL)
        self.assertEqual(ctx.user_data['idv']['card'], 'F1')
        kwargs = update.message.reply_photo.call_args.kwargs
        self.assertTrue(kwargs['photo'].name.endswith('national_note.png'))
        self.assertIn('کارت ملی کنار دست‌نوشته', kwargs['caption'])
        self.assertIn(f'تاریخ: {identity.note_date()} — امضا و اثر انگشت', kwargs['caption'])
        self.assertEqual(kwargs['parse_mode'], 'Markdown')

    def test_a_menu_button_leaves_the_flow(self):
        ctx = SimpleNamespace(user_data={'idv': {'phone': '0912'}}, bot=None)
        with patch('appearance.menu_action', return_value='wallet'):
            state = run(identity.idv_number(self._update(text='💰 کیف پول'), ctx))
        self.assertEqual(state, identity.ConversationHandler.END)
        self.assertNotIn('idv', ctx.user_data)


def legacy_markdown_ok(text):
    """همان چیزی که parse_mode='Markdown' تلگرام بی‌خطا می‌خواند (بدون لینک و کد)."""
    bare = text.replace('\\[', '')
    return text.count('*') % 2 == 0 and '_' not in text and '`' not in text and '[' not in bare


class NoteTests(unittest.TestCase):
    """دست‌نوشته کنار کارت ملی — همان متن و همان بازهٔ تاریخِ سایت."""

    def test_the_text_is_the_sites_with_the_shamsi_date(self):
        first, second = identity.note_lines(date(2026, 10, 30))
        self.assertEqual(first, 'اینجانب [نام و نام خانوادگی] این مدارک را فقط برای احراز '
                                'هویت در اتومیک شاپ می‌فرستم.')
        self.assertEqual(second, 'تاریخ: ۱۴۰۵/۰۸/۰۸ — امضا و اثر انگشت')
        self.assertEqual(identity.note_date(date(2026, 3, 21)), '۱۴۰۵/۰۱/۰۱')
        self.assertEqual(identity.note_date(date(2026, 3, 20)), '۱۴۰۴/۱۲/۲۹')

    def test_today_is_tehran_today(self):
        # ۲۲:۰۰ گرینویچ، ساعت ۱:۳۰ بامدادِ روزِ بعد در تهران است.
        late = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(identity._tehran_day(late), date(2026, 10, 31))
        early = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(identity._tehran_day(early), date(2026, 10, 30))

    def test_the_caption_is_safe_markdown_and_fits(self):
        text = identity.national_caption(date(2026, 10, 30))
        self.assertTrue(legacy_markdown_ok(text), text)
        self.assertLessEqual(len(text), 1024)
        self.assertIn('\\[نام و نام خانوادگی]', text)
        self.assertIn('تاریخ: ۱۴۰۵/۰۸/۰۸ — امضا و اثر انگشت', text)
        self.assertIn('امضا کن و اثر انگشتت را بزن', text)

    def test_every_step_text_mentions_the_note_but_never_the_rule(self):
        with patch.object(identity, 'identity_threshold_toman', return_value=1_000_000), \
                patch.object(identity, 'identity_window_days', return_value=4):
            intro = identity._intro_text()
            terms = identity.terms_text()
            hold = identity.hold_user_text(7, 'none')
            waiting = identity.hold_user_text(7, 'pending')
        self.assertIn('۳. کارت ملی کنار یک دست‌نوشتهٔ کوتاه با امضا و اثر انگشت', intro)
        self.assertTrue(legacy_markdown_ok(intro), intro)
        self.assertIn('کارت ملی کنار یک دست‌نوشتهٔ کوتاهِ امضاشده', hold)
        self.assertNotIn('احراز', terms)
        for text in (intro, terms, hold, waiting):
            for mark in RULE_MARKS:
                self.assertNotIn(mark, text)

    def test_the_reviewer_gets_the_accepted_date_range(self):
        sent = datetime(2026, 10, 30, 5, 30, tzinfo=timezone.utc)  # ۹ صبح تهران
        user = SimpleNamespace(id=42, username='buyer', full_name='علی رضایی')
        text = identity.admin_caption(5, user, CARD, '09125550000', [], False, sent_at=sent)
        self.assertIn('عکس‌ها به ترتیب: کارت بانکی، کارت ملی کنار دست‌نوشته، سلفی با کارت ملی.', text)
        self.assertIn('امضا، اثر انگشت و تاریخی از ۱۴۰۵/۰۸/۰۵ تا ۱۴۰۵/۰۸/۰۸', text)

    def test_a_missing_note_is_a_rejection_reason(self):
        self.assertIn('note', identity.REJECT_REASONS)
        markup = identity.reasons_keyboard(5)
        data = [row[0].callback_data for row in markup.inline_keyboard]
        self.assertIn('idv_rj_5_note', data)
        self.assertTrue(identity._REVIEW_RE.match('site_idv_rj_5_note'))


class AccountViewTests(unittest.TestCase):
    """«حساب من»: احراز هویت فقط وقتی دیده می‌شود که لازم است یا انجام شده."""

    def _account(self, state, held):
        from handlers import account

        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42, first_name='علی', last_name='', username='buyer'),
            callback_query=None, message=message, effective_message=message)
        ctx = SimpleNamespace(user_data={'db_id': 7})
        with patch.object(account, 'get_wallet_balance', return_value=0), \
                patch.object(account.appearance, 'user_label', return_value='حساب من'), \
                patch('db.identity_state', return_value=state), \
                patch('db.list_identity_held_orders', return_value=held):
            run(account.my_account(update, ctx))
        return [c.args[0] for c in message.reply_text.call_args_list]

    def test_a_fresh_buyer_sees_nothing_about_it(self):
        texts = self._account('none', [])
        self.assertEqual(len(texts), 1)
        self.assertNotIn('احراز', texts[0])
        self.assertNotIn('سقف', texts[0])

    def test_a_waiting_order_brings_it_up(self):
        texts = self._account('none', [(9, '42', 1_200_000, 0, 'card_transfer', None)])
        self.assertIn('احراز هویت: ⚠️ لازم برای تحویل سفارش منتظر', texts[0])
        self.assertIn('۱ سفارش منتظر احراز هویت توست', texts[1])
        for text in texts:
            self.assertNotIn('سقف', text)

    def test_a_verified_buyer_sees_the_status(self):
        texts = self._account('approved', [])
        self.assertIn('احراز هویت: ✅ تایید شده', texts[0])
        self.assertEqual(len(texts), 1)


class SiteAlbumTests(unittest.TestCase):
    def test_site_documents_arrive_as_an_album_then_the_buttons(self):
        item = {'id': 3, 'audience': 'admins', 'text': 'مدارک', 'ref': 'idv:7',
                'photo_url': 'https://site/1', 'reply_markup': identity.review_keyboard(7, 'site_idv').to_dict(),
                'photo_urls': ['https://site/1', 'https://site/2', 'https://site/3']}
        sent_album = [SimpleNamespace(photo=[SimpleNamespace(file_id=f'id{i}')]) for i in range(3)]
        bot = SimpleNamespace(send_media_group=AsyncMock(return_value=sent_album),
                              send_message=AsyncMock(return_value=SimpleNamespace(message_id=50)),
                              send_photo=AsyncMock())
        with patch.object(site_outbox, 'fetch_site_outbox', return_value={'ok': True, 'items': [item]}), \
                patch.object(site_outbox, 'fetch_site_photo', return_value=b'jpeg') as fetch, \
                patch.object(site_outbox, 'admin_ids', return_value=[1, 2]), \
                patch.object(site_outbox, 'ack_site_outbox', return_value={'ok': True}) as ack, \
                patch.object(site_outbox, 'remember_notices') as remember:
            delivered = run(site_outbox.deliver_site_outbox(bot))
        self.assertEqual(delivered, 1)
        self.assertEqual(fetch.call_count, 3)
        first, second = bot.send_media_group.call_args_list
        # ادمین اول: آپلودِ بایت‌ها (تلگرام آن‌ها را InputFile می‌کند).
        self.assertEqual(len(first.kwargs['media']), 3)
        self.assertFalse(any(isinstance(m.media, str) for m in first.kwargs['media']))
        # ادمین دوم: همان عکس‌ها با file_id، بدون آپلود دوباره.
        self.assertEqual([m.media for m in second.kwargs['media']], ['id0', 'id1', 'id2'])
        bot.send_photo.assert_not_called()
        markup = bot.send_message.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'site_idv_ok_7')
        self.assertEqual(remember.call_args.args[:2], ('site_idv', 7))
        self.assertEqual(len(ack.call_args.args[1]), 2)


class WiringTests(unittest.TestCase):
    def test_bot_registers_the_identity_flow(self):
        src = (ROOT / 'bot.py').read_text(encoding='utf-8')
        self.assertIn('identity_conversation_handler()', src)
        self.assertIn('identity_admin_handlers()', src)
        self.assertLess(src.index('payment_conversation_handler()'),
                        src.index('identity_conversation_handler()'))

    def test_payment_asks_for_the_terms_not_a_card(self):
        payment = (ROOT / 'handlers' / 'payment.py').read_text(encoding='utf-8')
        wallet = (ROOT / 'handlers' / 'wallet.py').read_text(encoding='utf-8')
        self.assertNotIn('card_gate(', payment + wallet)
        self.assertNotIn('prompt_kyc_for_order', payment)
        self.assertEqual(payment.count('terms_gate('), 3)
        self.assertLess(payment.index("terms_gate(query, update.effective_user.id, order_id)"),
                        payment.index("get_setting('card_number'"))

    def test_fulfillment_holds_before_any_delivery(self):
        import inspect

        source = inspect.getsource(db.fulfill_order)
        self.assertLess(source.index('claim_identity_hold(order_id)'),
                        source.index("update_order_status(order_id, 'processing')"))

    def test_held_orders_are_not_picked_up_as_stuck(self):
        import inspect

        source = inspect.getsource(db.list_processing_auto_orders)
        self.assertIn('processing', source)
        self.assertNotIn('kyc_hold', source)

    def test_gem_pages_do_not_mention_identity(self):
        import inspect

        from handlers import gems

        self.assertNotIn('احراز', inspect.getsource(gems))

    def test_the_sample_photos_ship_with_the_bot(self):
        for name in ('bank_card', 'national_note', 'selfie'):
            data = (ROOT / 'assets' / 'identity_samples' / f'{name}.png').read_bytes()
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
            self.assertGreater(int.from_bytes(data[16:20], 'big'), 600)  # پهنا از IHDR


if __name__ == '__main__':
    unittest.main()
