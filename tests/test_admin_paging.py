"""فهرستِ سفارش‌ها و کاربرانِ پنل صفحه‌بندی دارد؛ قبلاً فقط ۲۰/۱۵ ردیفِ آخر دیده می‌شد."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import handlers.admin as admin
import handlers.admin_extended as ext


def _query(data):
    return SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                           message=SimpleNamespace(text='x'), from_user=SimpleNamespace(id=1))


def _update(query):
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1),
                           effective_message=None)


def _callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class UsersPagingTests(unittest.TestCase):
    def rows(self, n, start=0):
        return [(i, 1000 + i, f'user{i}', f'u{i}', False, 0) for i in range(start, start + n)]

    def run_page(self, data, rows):
        query = _query(data)
        with patch.object(admin, '_require_admin', AsyncMock(return_value=True)), \
                patch.object(admin, 'list_recent_users', return_value=rows) as fetch:
            asyncio.run(admin.admin_users(_update(query), SimpleNamespace()))
        return fetch, query.edit_message_text.call_args.kwargs['reply_markup']

    def test_first_page_has_next_only(self):
        fetch, markup = self.run_page('adm_users', self.rows(16))
        fetch.assert_called_once_with(16, 0)
        callbacks = _callbacks(markup)
        self.assertIn('adm_users_p1', callbacks)
        self.assertNotIn('adm_users', callbacks)

    def test_last_page_goes_back_without_next(self):
        fetch, markup = self.run_page('adm_users_p2', self.rows(3, 30))
        fetch.assert_called_once_with(16, 30)
        callbacks = _callbacks(markup)
        self.assertIn('adm_users_p1', callbacks)
        self.assertNotIn('adm_users_p3', callbacks)


class OrdersPagingTests(unittest.TestCase):
    def rows(self, n, start=0):
        return [(i, 2000 + i, 100_000, 'completed', 'wallet', None, 'نام', 'u', '')
                for i in range(start, start + n)]

    def run_page(self, data, rows):
        query = _query(data)
        with patch.object(ext, 'is_admin', return_value=True), \
                patch.object(ext, 'list_recent_orders_admin', return_value=rows) as fetch, \
                patch.object(ext, '_edit', AsyncMock()) as edit:
            asyncio.run(ext.admin_ext_router(_update(query), SimpleNamespace(bot=None)))
        buttons = edit.call_args.args[2]
        return fetch, [b.callback_data for row in buttons for b in row]

    def test_first_page_offers_the_next(self):
        fetch, callbacks = self.run_page('admx_allorders', self.rows(16))
        fetch.assert_called_once_with(16, 0)
        self.assertIn('admx_allorders_p1', callbacks)

    def test_second_page_goes_back_to_the_first(self):
        fetch, callbacks = self.run_page('admx_allorders_p1', self.rows(5, 15))
        fetch.assert_called_once_with(16, 15)
        self.assertIn('admx_allorders', callbacks)
        self.assertNotIn('admx_allorders_p2', callbacks)


class SiteLinkLineTests(unittest.TestCase):
    def test_states(self):
        from handlers import site_outbox as so

        with patch.dict(so.LINK, {'last_ok': None, 'first_fail': None}):
            self.assertEqual(so.link_status_line(1000.0), '')
        with patch.dict(so.LINK, {'last_ok': 1000.0, 'first_fail': None}):
            self.assertEqual(so.link_status_line(1030.0), '🌐 ارتباط با سایت: برقرار')
        with patch.dict(so.LINK, {'last_ok': 1000.0, 'first_fail': 1000.0}):
            self.assertIn('قطع از حدود 10 دقیقه', so.link_status_line(1600.0))
