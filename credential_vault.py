"""Encryption boundary for temporary Free Fire account credentials.

Only ciphertext is persisted.  The key must be supplied by the deployment and
must never be stored in the database or committed to git.
"""
import json
import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialVaultError(RuntimeError):
    pass


def _fernet():
    raw = (os.getenv('ACCOUNT_CREDENTIALS_KEY') or '').strip().encode('ascii')
    if not raw:
        raise CredentialVaultError(
            'کلید رمزنگاری اطلاعات اکانت روی سرور تنظیم نشده است.'
        )
    try:
        return Fernet(raw)
    except (ValueError, TypeError) as exc:
        raise CredentialVaultError(
            'کلید رمزنگاری اطلاعات اکانت نامعتبر است.'
        ) from exc


def is_configured():
    try:
        _fernet()
        return True
    except CredentialVaultError:
        return False


def encrypt_credentials(identifier, password, note='', backup_code=''):
    payload = json.dumps(
        {
            'identifier': str(identifier or '').strip(),
            'password': str(password or ''),
            'backup_code': str(backup_code or '').strip(),
            'note': str(note or '').strip(),
        },
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    return _fernet().encrypt(payload).decode('ascii')


def decrypt_credentials(ciphertext):
    try:
        raw = _fernet().decrypt(str(ciphertext or '').encode('ascii'))
        data = json.loads(raw.decode('utf-8'))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CredentialVaultError(
            'اطلاعات رمزگذاری‌شده قابل بازیابی نیست؛ کلید سرور را بررسی کنید.'
        ) from exc
    return {
        'identifier': str(data.get('identifier') or ''),
        'password': str(data.get('password') or ''),
        'backup_code': str(data.get('backup_code') or ''),
        'note': str(data.get('note') or ''),
    }


def mask_identifier(value):
    value = str(value or '').strip()
    if '@' in value:
        local, domain = value.split('@', 1)
        return f'{local[:2]}***@{domain}'
    if len(value) <= 4:
        return '••••'
    return f'{value[:2]}•••{value[-2:]}'
