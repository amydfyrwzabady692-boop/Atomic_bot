import unittest

from forced_join_logic import (
    member_is_joined,
    valid_forced_join_chat_id,
    valid_telegram_invite_url,
)


class ForcedJoinValidationTests(unittest.TestCase):
    def test_accepts_public_and_private_channel_ids(self):
        self.assertTrue(valid_forced_join_chat_id('@Omid_AtomicFF'))
        self.assertTrue(valid_forced_join_chat_id('-1001234567890'))

    def test_rejects_unsafe_or_unusable_channel_ids(self):
        for value in ('Omid_AtomicFF', '@abc', '-1234', '', None):
            with self.subTest(value=value):
                self.assertFalse(valid_forced_join_chat_id(value))

    def test_accepts_only_https_telegram_links_without_credentials(self):
        self.assertTrue(
            valid_telegram_invite_url('https://t.me/Omid_AtomicFF')
        )
        self.assertTrue(valid_telegram_invite_url('https://t.me/+invite'))
        for value in (
            'http://t.me/Omid_AtomicFF',
            'https://example.com/Omid_AtomicFF',
            'https://user:pass@t.me/Omid_AtomicFF',
        ):
            with self.subTest(value=value):
                self.assertFalse(valid_telegram_invite_url(value))

    def test_membership_statuses_are_fail_closed(self):
        for status in ('member', 'administrator', 'creator'):
            self.assertTrue(member_is_joined(status))
        self.assertTrue(member_is_joined('restricted', is_member=True))
        self.assertFalse(member_is_joined('restricted', is_member=False))
        self.assertFalse(member_is_joined('left'))
        self.assertFalse(member_is_joined('kicked'))


if __name__ == '__main__':
    unittest.main()
