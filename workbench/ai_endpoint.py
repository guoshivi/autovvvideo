"""Validate provider URLs without echoing credentials or query strings."""
from urllib.parse import urlsplit, urlunsplit
import ipaddress


def normalize_endpoint(value, has_key=False):
    try:
        raw = str(value).strip()
        if any(ord(c) < 33 for c in raw) or '\\' in raw:
            raise ValueError()
        u = urlsplit(raw)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError()
        _ = u.port
        local = u.hostname.lower() == 'localhost'
        try:
            local = local or ipaddress.ip_address(u.hostname).is_loopback
        except ValueError:
            pass
        if has_key and u.scheme != 'https' and not local:
            raise ValueError()
        path = u.path.rstrip('/')
        if not path.endswith('/chat/completions'):
            path += '/chat/completions' if path else '/v1/chat/completions'
        return urlunsplit((u.scheme, u.netloc, path, '', ''))
    except (ValueError, TypeError):
        raise ValueError('AI 接口地址无效：远程密钥需要 HTTPS；禁止 URL 用户信息、查询参数及片段') from None
