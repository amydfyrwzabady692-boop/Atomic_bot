"""Storage helpers for Free Fire account login details.

Credentials are stored as JSON text in the database column historically named
CredentialCiphertext. Encryption is optional: if ACCOUNT_CREDENTIALS_KEY is set,
new writes are Fernet-encrypted for deployments that want it; otherwise plain
JSON is used so the flow works without extra server config.
"""
import json
import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialVaultError(RuntimeError):
    pass


def _fernet_or_none():
    raw = (os.getenv('ACCOUNT_CREDENTIALS_KEY') or '').strip().encode('ascii')
    if not raw:
        return None
    try:
        return Fernet(raw)
    except (ValueError, TypeError):
        return None


def is_configured():
    """Credential intake is always available; encryption key is optional."""
    return True


def encrypt_credentials(identifier, password, note='', backup_code=''):
    payload = {
        'identifier': str(identifier or '').strip(),
        'password': str(password or ''),
        'backup_code': str(backup_code or '').strip(),
        'note': str(note or '').strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    fernet = _fernet_or_none()
    if fernet is not None:
        return fernet.encrypt(raw).decode('ascii')
    return raw.decode('utf-8')


def decrypt_credentials(ciphertext):
    text = str(ciphertext or '').strip()
    if not text:
        raise CredentialVaultError('اطلاعات ورود خالی است.')

    # Plain JSON (current default when no key is configured)
    if text.startswith('{'):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CredentialVaultError('اطلاعات ورود قابل خواندن نیست.') from exc
        return _normalize(data)

    # Legacy Fernet ciphertext
    fernet = _fernet_or_none()
    if fernet is None:
        raise CredentialVaultError(
            'این سفارش با کلید قدیمی رمز شده؛ ACCOUNT_CREDENTIALS_KEY را روی سرور بگذار.'
        )
    try:
        raw = fernet.decrypt(text.encode('ascii'))
        data = json.loads(raw.decode('utf-8'))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CredentialVaultError(
            'اطلاعات رمزگذاری‌شده قابل بازیابی نیست؛ کلید سرور را بررسی کنید.'
        ) from exc
    return _normalize(data)


def _normalize(data):
    if not isinstance(data, dict):
        raise CredentialVaultError('فرمت اطلاعات ورود نامعتبر است.')
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
