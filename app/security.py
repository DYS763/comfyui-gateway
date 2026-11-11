from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit


QUERY_TOKEN_PREFIX = "getpic_"
ACCESS_KEY_PREFIX = "key_"
SUPER_KEY_PREFIX = "sk_"
RECHARGE_KEY_PREFIX = "recharge_"
INVITE_CODE_PREFIX = "inv_"

_INVITE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_INVITE_BODY_LENGTH = 20

CREDENTIAL_KINDS = {
    ACCESS_KEY_PREFIX: "access",
    SUPER_KEY_PREFIX: "super",
    QUERY_TOKEN_PREFIX: "query",
    RECHARGE_KEY_PREFIX: "recharge",
    INVITE_CODE_PREFIX: "invite",
}


def new_query_token() -> str:
    return f"{QUERY_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def new_recharge_key() -> str:
    return f"{RECHARGE_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def new_invite_code() -> str:
    body = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(_INVITE_BODY_LENGTH))
    return f"{INVITE_CODE_PREFIX}{body}"


def classify_credential(value: str | None) -> str:
    if not value:
        return "empty"
    for prefix, kind in CREDENTIAL_KINDS.items():
        if value.startswith(prefix):
            return kind
    return "legacy"


def _is_trusted_proxy(value: str, allow_ips: str) -> bool:
    for item in allow_ips.split(","):
        item = item.strip()
        if item == "*":
            return True
        if not item:
            continue
        try:
            if ipaddress.ip_address(value) in ipaddress.ip_network(
                item, strict=False
            ):
                return True
        except ValueError:
            continue
    return False


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, allow_ips: str
) -> str | None:
    if not peer:
        return None
    if not _is_trusted_proxy(peer, allow_ips):
        return peer
    hops = [hop.strip() for hop in (forwarded_for or "").split(",") if hop.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            continue
        if not _is_trusted_proxy(hop, allow_ips):
            return hop
    return peer


def normalize_ip_or_network(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("IP 或 CIDR 不能为空")
    try:
        if "/" in candidate:
            return str(ipaddress.ip_network(candidate, strict=False))
        return str(ipaddress.ip_address(candidate))
    except ValueError as exc:
        raise ValueError(
            "IP 或 CIDR 格式不正确，示例：203.0.113.5 或 10.0.0.0/8"
        ) from exc


def is_ip_exempt(value: str | None, exemptions: Iterable[str]) -> bool:
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    for item in exemptions:
        try:
            if "/" in item:
                if address in ipaddress.ip_network(item):
                    return True
            elif address == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def normalize_comfy_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("节点地址必须是字符串")
    candidate = value.strip()
    if not candidate:
        raise ValueError("节点地址不能为空")
    if len(candidate) > 512:
        raise ValueError("节点地址不能超过 512 个字符")
    if any(unicodedata.category(char).startswith("C") for char in candidate):
        raise ValueError("节点地址包含不支持的控制字符")
    if any(char.isspace() for char in candidate):
        raise ValueError("节点地址不能包含空白字符")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("节点地址必须以 http:// 或 https:// 开头")
    if not parsed.netloc:
        raise ValueError("节点地址缺少主机名")
    if "@" in parsed.netloc:
        raise ValueError("节点地址不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ValueError("节点地址不能包含查询参数或片段")
    path = parsed.path.rstrip("/")
    for part in path.split("/"):
        if part == "..":
            raise ValueError("节点地址不能包含 ..")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def new_access_key() -> str:
    return f"{ACCESS_KEY_PREFIX}{secrets.token_urlsafe(24)}"


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_PASSWORD_SCHEME = "scrypt"
_PASSWORD_N = 2 ** 14
_PASSWORD_R = 8
_PASSWORD_P = 1
_PASSWORD_SALT_BYTES = 16
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,24}$")
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_PASSWORD_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        dklen=32,
    )
    return "$".join(
        [
            _PASSWORD_SCHEME,
            str(_PASSWORD_N),
            str(_PASSWORD_R),
            str(_PASSWORD_P),
            salt.hex(),
            digest.hex(),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n_raw, r_raw, p_raw, salt_raw, digest_raw = encoded.split("$")
        if scheme != _PASSWORD_SCHEME:
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_raw),
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=len(bytes.fromhex(digest_raw)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_raw)


def validate_username(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("用户名必须是字符串")
    username = value.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("用户名需为 3~24 位字母、数字、下划线或短横线")
    return username


def validate_password(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("密码必须是字符串")
    if not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"密码长度必须是 {PASSWORD_MIN_LENGTH}~{PASSWORD_MAX_LENGTH} 个字符"
        )
    for char in value:
        if char.isspace() or unicodedata.category(char).startswith("C"):
            raise ValueError("密码不能包含空白或控制字符")
    return value


def normalize_registration_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def validate_prompt(value: str, max_length: int) -> str:
    prompt = _clean_prompt(value, max_length)
    if not prompt:
        raise ValueError("prompt cannot be empty")
    return prompt


def validate_negative_prompt(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    prompt = _clean_prompt(value, max_length)
    return prompt or None


def validate_label(value: str | None, max_length: int = 80) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("label must be a string")
    label = value.strip()
    if len(label) > max_length:
        raise ValueError(f"label must be at most {max_length} characters")
    for char in label:
        if unicodedata.category(char) == "Cc":
            raise ValueError("label contains an unsupported control character")
    return label or None


def validate_comment(value: str, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("评论内容不能为空")
    comment = value.strip()
    if len(comment) > max_length:
        raise ValueError(f"评论内容不能超过 {max_length} 字")
    for char in comment:
        category = unicodedata.category(char)
        if category == "Cc" and char not in "\r\n\t":
            raise ValueError("评论内容包含不支持的控制字符")
    return comment


SUPER_KEY_MIN_LENGTH = 8
SUPER_KEY_MAX_LENGTH = 128


def validate_super_key(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("超级密钥必须是字符串")
    key = value.strip()
    if not key.startswith(SUPER_KEY_PREFIX):
        raise ValueError(f"超级密钥必须以 {SUPER_KEY_PREFIX} 开头")
    if not SUPER_KEY_MIN_LENGTH <= len(key) <= SUPER_KEY_MAX_LENGTH:
        raise ValueError(
            f"超级密钥长度必须是 {SUPER_KEY_MIN_LENGTH}~{SUPER_KEY_MAX_LENGTH} 个字符"
        )
    for char in key:
        if char.isspace() or unicodedata.category(char).startswith("C"):
            raise ValueError("超级密钥不能包含空白或控制字符")
    return key


def _clean_prompt(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("prompt must be a string")
    prompt = value.strip()
    if len(prompt) > max_length:
        raise ValueError(f"prompt must be at most {max_length} characters")
    for char in prompt:
        category = unicodedata.category(char)
        if category == "Cc" and char not in "\r\n\t":
            raise ValueError("prompt contains an unsupported control character")
    return prompt

