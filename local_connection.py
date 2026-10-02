"""Validate loopback-only URLs used by local companion applications."""
import ipaddress
from urllib.parse import urlparse


def normalize_loopback_url(value):
    if not isinstance(value, str):
        raise ValueError('接続先はURLで指定してください。')
    try:
        parsed = urlparse(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError('接続先のポート番号が不正です。') from exc
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError('接続先はこのPCのhttp://127.0.0.1:ポート番号などを指定してください。')
    host = '[::1]' if parsed.hostname == '::1' else parsed.hostname
    return f'http://{host}' + (f':{port}' if port is not None else '')


def is_loopback_host_header(value):
    """True only when an HTTP Host header names this PC (DNS rebinding guard)."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse('//' + value.strip())
        hostname = parsed.hostname
        parsed.port  # raises ValueError for a malformed port
    except ValueError:
        return False
    if not hostname:
        return False
    hostname = hostname.lower().rstrip('.')
    if hostname == 'localhost':
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
