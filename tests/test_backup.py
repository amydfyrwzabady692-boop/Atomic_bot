"""💾 بک‌آپ از پنلِ مالک: دیتابیسِ ربات (pg_dump) و سایت، تکه‌تکه در تلگرامِ مالک.

- فقط مالک (ADMIN_CHAT_ID) دکمه را می‌بیند و می‌زند؛ ادمینِ کمکی نه.
- فایلِ بزرگ‌تر از سقف چند بخش می‌شود و بخش‌ها کنارِ هم همان gzip سالم‌اند.
- رمزِ دیتابیس فقط در PGPASSWORD است؛ خطای pg_dump بخشِ آخر را نمی‌فرستد.
- بک‌آپِ سایت با خطای شبکه دوباره امتحان می‌شود، با جوابِ خودِ سایت نه.
"""
import asyncio
import gzip
import io
import os
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import site_api
from handlers import backup

SQL = b'-- PostgreSQL database dump\n' + b'INSERT INTO "Users" VALUES (1);\n' * 400


class FakeProcess:
    def __init__(self, out=SQL, code=0, err=b''):
        self.stdout = io.BytesIO(out)
        self.stderr = io.BytesIO(err)
        self._code = code

    def wait(self, timeout=None):
        return self._code

    def kill(self):
        pass


class SplitterTests(unittest.TestCase):
    def collect(self, size, *writes):
        calls = []
        splitter = backup.PartSplitter(lambda i, d, last: calls.append((i, d, last)), size)
        for data in writes:
            splitter.write(data)
        splitter.finish()
        return calls

    def test_a_small_file_is_one_part(self):
        self.assertEqual(self.collect(4, b'abc'), [(1, b'abc', True)])

    def test_exactly_one_part_is_not_split(self):
        self.assertEqual(self.collect(4, b'ab', b'cd'), [(1, b'abcd', True)])

    def test_a_big_file_is_split_in_order(self):
        self.assertEqual(self.collect(4, b'abcdefghij'),
                         [(1, b'abcd', False), (2, b'efgh', False), (3, b'ij', True)])


class DumpTests(unittest.TestCase):
    def test_the_parts_join_into_the_gzipped_dump(self):
        parts, seen = [], {}

        def popen(args, stdout=None, stderr=None, env=None):
            seen.update(args=list(args), env=dict(env))
            return FakeProcess()

        with patch.dict(os.environ, {'DB_HOST': 'db', 'DB_PASSWORD': 'not-a-real-password'}), \
                patch.object(backup.subprocess, 'Popen', popen), \
                patch.object(backup, 'PART_BYTES', 64):
            count, size = backup.dump_bot_database(lambda i, d, last: parts.append((i, d, last)))
        self.assertGreater(count, 1)
        self.assertEqual([p[0] for p in parts], list(range(1, count + 1)))
        self.assertTrue(parts[-1][2])
        self.assertEqual(gzip.decompress(b''.join(p[1] for p in parts)), SQL)
        self.assertEqual(size, sum(len(p[1]) for p in parts))
        self.assertEqual(seen['args'][0], 'pg_dump')
        self.assertNotIn('not-a-real-password', ' '.join(seen['args']))
        self.assertEqual(seen['env']['PGPASSWORD'], 'not-a-real-password')
        self.assertEqual(seen['env']['PGHOST'], 'db')

    def test_a_failed_dump_sends_no_last_part(self):
        parts = []
        with patch.object(backup.subprocess, 'Popen',
                          lambda *a, **k: FakeProcess(b'', 1, b'pg_dump: error: no connection')):
            with self.assertRaisesRegex(RuntimeError, 'no connection'):
                backup.dump_bot_database(lambda i, d, last: parts.append(i))
        self.assertEqual(parts, [])


def fake_bot():
    return SimpleNamespace(send_document=AsyncMock(), send_message=AsyncMock())


class RunBackupTests(unittest.TestCase):
    def run_backup(self, site_result):
        bot = fake_bot()
        with patch.object(backup.subprocess, 'Popen', lambda *a, **k: FakeProcess()), \
                patch.object(backup, 'PART_BYTES', 64), \
                patch.object(site_api, 'fetch_site_backup', return_value=site_result) as fetch:
            ok = asyncio.run(backup.run_backup(bot, 42))
        return ok, bot, fetch

    def test_both_backups_reach_the_owner_with_join_help(self):
        site = gzip.compress(b'-- site dump\n')
        ok, bot, fetch = self.run_backup(
            {'ok': True, 'data': site, 'filename': 'atomicshop-2026-11-24-0900.sql.gz'})
        self.assertTrue(ok)
        fetch.assert_called_once_with(42)
        names = [c.kwargs['filename'] for c in bot.send_document.call_args_list]
        self.assertTrue(names[0].startswith('atomic-bot-') and names[0].endswith('.sql.gz.part1'))
        self.assertEqual(names[-1], 'atomicshop-2026-11-24-0900.sql.gz')
        self.assertEqual(bot.send_document.call_args_list[-1].args[1].getvalue(), site)
        for call in bot.send_document.call_args_list:
            self.assertEqual(call.args[0], 42)
            self.assertEqual(call.kwargs['write_timeout'], 600)
        summary = bot.send_message.call_args.args[1]
        self.assertIn('✅', summary)
        self.assertIn('copy /b', summary)
        self.assertIn('cat ', summary)

    def test_a_site_error_is_reported(self):
        ok, bot, _fetch = self.run_backup({'ok': False, 'error': 'forbidden'})
        self.assertFalse(ok)
        summary = bot.send_message.call_args.args[1]
        self.assertIn('⚠️', summary)
        self.assertIn('❌ سایت: forbidden', summary)


def callback(data, user_id):
    query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                            message=SimpleNamespace(reply_text=AsyncMock()))
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user_id))
    ctx = SimpleNamespace(bot=fake_bot(), application=SimpleNamespace(create_task=MagicMock()))
    return update, ctx, query


class OwnerOnlyTests(unittest.TestCase):
    def setUp(self):
        backup._STATE.update(running=False, last=0.0)
        self.addCleanup(backup._STATE.update, running=False, last=0.0)

    def test_a_helper_admin_cannot_take_a_backup(self):
        update, ctx, query = callback('bkp_go', 777)
        with patch.object(backup, 'admin_id', return_value=1):
            asyncio.run(backup.backup_router(update, ctx))
        self.assertTrue(query.answer.call_args.kwargs.get('show_alert'))
        ctx.application.create_task.assert_not_called()

    def test_the_owner_starts_it_in_the_background_once(self):
        update, ctx, query = callback('bkp_go', 1)
        with patch.object(backup, 'admin_id', return_value=1):
            asyncio.run(backup.backup_router(update, ctx))
            ctx.application.create_task.call_args.args[0].close()  # کوروتینِ ساختگی اجرا نشود
            self.assertEqual(ctx.application.create_task.call_count, 1)
            self.assertTrue(backup._STATE['running'])
            again, ctx2, query2 = callback('bkp_go', 1)
            asyncio.run(backup.backup_router(again, ctx2))
        self.assertTrue(query2.answer.call_args.kwargs.get('show_alert'))
        ctx2.application.create_task.assert_not_called()

    def test_the_button_is_only_on_the_owners_settings_page(self):
        from keyboards import admin_hub_system_keyboard

        def callbacks(markup):
            return [b.callback_data for row in markup.inline_keyboard for b in row]

        self.assertIn('bkp_home', callbacks(admin_hub_system_keyboard({'owner': True})))
        self.assertNotIn('bkp_home', callbacks(admin_hub_system_keyboard()))
        self.assertNotIn('bkp_home', callbacks(admin_hub_system_keyboard({'owner': False})))


class FakeResponse:
    def __init__(self, data, disposition='attachment; filename="atomicshop-2026-11-24-0900.sql.gz"'):
        self._data = data
        self.headers = {'Content-Disposition': disposition}

    def read(self, n=-1):
        return self._data if n < 0 else self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FetchSiteBackupTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {'BOT_INTERNAL_SECRET': 'test-secret'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_network_errors_are_retried(self):
        data = gzip.compress(b'dump')
        opener = MagicMock(side_effect=[urllib.error.URLError('handshake timed out'),
                                        FakeResponse(data)])
        with patch('urllib.request.urlopen', opener):
            result = site_api.fetch_site_backup(42, pause=0)
        self.assertTrue(result['ok'])
        self.assertEqual(result['data'], data)
        self.assertEqual(result['filename'], 'atomicshop-2026-11-24-0900.sql.gz')
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('X-bot-secret'), 'test-secret')

    def test_the_sites_own_answer_is_not_retried(self):
        error = urllib.error.HTTPError('u', 403, 'Forbidden', {}, io.BytesIO(b'{"error": "forbidden"}'))
        opener = MagicMock(side_effect=error)
        with patch('urllib.request.urlopen', opener):
            result = site_api.fetch_site_backup(42, pause=0)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(result, {'ok': False, 'error': 'forbidden'})

    def test_something_that_is_not_gzip_is_refused(self):
        with patch('urllib.request.urlopen', MagicMock(return_value=FakeResponse(b'<html>'))):
            result = site_api.fetch_site_backup(42, pause=0)
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
