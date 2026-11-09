"""Owner-configured OAuth for the Windows workstation."""

import base64
import ctypes
import hashlib
import json
import os
import secrets
import time
from ctypes import wintypes
from urllib.parse import parse_qs, urlencode, urlsplit

from config import DATA_DIR


PLATFORMS = {"douyin": ("DOUYIN", "CLIENT_KEY"), "bilibili": ("BILIBILI", "CLIENT_ID")}


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _dpapi(data, decrypt):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    transform = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    transform.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    transform.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.addressof(buffer))
    result = _Blob()
    if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError("Windows 无法读取或保护当前用户的授权数据。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def protect_json(value):
    return base64.b64encode(_dpapi(json.dumps(value).encode("utf-8"), False)).decode("ascii")


def unprotect_json(value):
    return json.loads(_dpapi(base64.b64decode(value), True))


def _settings(platform):
    local = _read(_path(platform).with_suffix(".application.json"))
    if local is not None:
        return local["client"], local["secret"], local["redirect"], local["partition"]
    prefix, key = PLATFORMS[platform]
    return tuple(os.getenv(f"{prefix}_{name}", "").strip() for name in (key, "CLIENT_SECRET", "REDIRECT_URI", "TID"))


def application_fields(platform):
    client, _, redirect, partition = _settings(platform)
    return client, redirect, partition


def _validate_redirect(redirect):
    parts = urlsplit(redirect)
    loopback = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if not parts.hostname or not (parts.scheme == "https" or loopback):
        raise ValueError("请填写开放平台登记的 HTTPS 回调地址。")
    if parts.username is not None or parts.fragment or parts.query:
        raise ValueError("回调地址不能包含账号、查询参数或片段。")


def save_application(platform, client, secret, redirect, partition=""):
    from core.workflow import _lock

    client, secret, redirect, partition = (value.strip() for value in (client, secret, redirect, partition))
    if not client:
        raise ValueError("请填写应用 ID。")
    _validate_redirect(redirect)
    if platform == "bilibili" and partition and (not partition.isdecimal() or int(partition) <= 0):
        raise ValueError("B站投稿分区 ID 必须是正整数。")
    path = _path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock(path.with_suffix(".sqlite3"), timeout=5):
        previous_client, previous_secret, _, _ = _settings(platform)
        if not secret and client == previous_client:
            secret = previous_secret
        if not secret:
            raise ValueError("请填写此应用的应用密钥。")
        _save(path.with_suffix(".application.json"), {
            "client": client, "secret": secret, "redirect": redirect, "partition": partition,
        })


def bilibili_partition():
    partition = _settings("bilibili")[3]
    return int(partition) if partition.isdecimal() else 0


def begin_login(platform):
    client, secret, redirect, _ = _settings(platform)
    if not client or not secret or not redirect:
        raise RuntimeError("请先配置已审核的开放平台应用、密钥和登记的回调地址。")
    _validate_redirect(redirect)
    state = secrets.token_urlsafe(32)
    if platform == "douyin":
        base = "https://open.douyin.com/platform/oauth/connect/"
        query = {"client_key": client, "response_type": "code", "redirect_uri": redirect,
                 "scope": "user_info,video.create,video.list", "state": state}
    else:
        base = "https://account.bilibili.com/pc/account-pc/auth/oauth"
        query = {"client_id": client, "gourl": redirect, "state": state}
    return {"url": f"{base}?{urlencode(query)}", "client": client, "state": state, "expires_at": time.time() + 600}


def auth_code(platform, login, callback):
    client, _, redirect, _ = _settings(platform)
    if login["client"] != client:
        raise ValueError("应用配置已变更，请重新发起授权。")
    received = urlsplit(callback.strip())
    if received._replace(query="", fragment="") != urlsplit(redirect) or received.fragment:
        raise ValueError("回调地址与开放平台登记的地址不匹配。")
    fields = parse_qs(received.query, max_num_fields=16)
    states = fields.get("state", ())
    if (time.time() >= login["expires_at"] or len(states) != 1 or
            not secrets.compare_digest(states[0].encode(), login["state"].encode())):
        raise ValueError("授权状态无效或已过期，请重新登录。")
    codes = fields.get("code", ())
    if len(codes) != 1 or not 0 < len(codes[0]) < 2048:
        raise ValueError("平台没有返回可用授权码。")
    return codes[0]


def _exchange(platform, *, code=None, refresh_token=None):
    import requests

    client, secret, _, _ = _settings(platform)
    if not client or not secret:
        raise RuntimeError("请配置开放平台应用凭证后重新登录。")
    data = {"grant_type": "authorization_code" if code else "refresh_token"}
    data.update({"code": code, "client_secret": secret} if code else {"refresh_token": refresh_token})
    if platform == "douyin":
        url = f"https://open.douyin.com/oauth/{'access_token' if code else 'refresh_token'}/"
        data["client_key"] = client
        arguments = {"data": data} if code else {"files": {key: (None, value) for key, value in data.items()}}
    else:
        url = f"https://api.bilibili.com/x/account-oauth2/v1/{'token' if code else 'refresh_token'}"
        data.update(client_id=client, client_secret=secret)
        arguments = {"params": data, "headers": {"Content-Type": "application/x-www-form-urlencoded"}}
    try:
        response = requests.post(url, timeout=(6, 15), allow_redirects=False, **arguments)
        if response.status_code != 200:
            raise RuntimeError(f"授权请求失败 · HTTP {response.status_code}")
        raw = response.json()
        token = raw.get("data", {})
        status = token.get("error_code") if platform == "douyin" else raw.get("code")
        if str(status) != "0" or not token.get("access_token"):
            safe_status = str(status) if str(status).isdigit() else "未知"
            raise RuntimeError(f"平台未接受授权 · {safe_status}，请核对应用权限或重新登录。")
        expires = int(token["expires_in"])
    except requests.RequestException as error:
        raise RuntimeError(f"授权服务暂不可用（{type(error).__name__}）。") from error
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError("平台未返回可用的授权凭证。") from error
    return {
        "access_token": token["access_token"], "refresh_token": token.get("refresh_token") or refresh_token,
        "expires_at": expires if platform == "bilibili" else time.time() + expires,
        **({"open_id": token["open_id"]} if token.get("open_id") else {}),
    }


def _path(platform):
    if platform not in PLATFORMS:
        raise ValueError("该平台请通过官方创作者后台管理。")
    return DATA_DIR / "accounts" / f"{platform}.json"


def _read(path):
    try:
        text = path.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    return unprotect_json(json.loads(text)["sealed"])


def _save(path, account):
    from core.workflow import write_json

    write_json(path, {"sealed": protect_json(account)})


def _account_profile(platform, account):
    from core.news_release import _bili_post, _douyin_request

    if platform == "douyin":
        result = _douyin_request("POST", "/oauth/userinfo/", account["access_token"], account["open_id"])
        return result.get("nickname"), account["open_id"]
    client, secret, _, _ = _settings(platform)
    result = _bili_post("https://member.bilibili.com/arcopen/fn/user/account/info", method="GET",
                        credentials=(client, secret, account["access_token"]))
    return result.get("name"), result.get("openid")


def finish_login(platform, login, callback):
    from core.workflow import _lock

    path = _path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock(path.with_suffix(".sqlite3"), timeout=5):
        code = auth_code(platform, login, callback)
        account = _exchange(platform, code=code)
        try:
            name, account_id = _account_profile(platform, account)
        except RuntimeError:
            name, account_id = None, account.get("open_id")
        account.update(label=name or ("账号 " + account_id[-8:] if account_id else "已授权账号"),
                       identity=account_id or secrets.token_hex(16), client=_settings(platform)[0])
        _save(path, account)
    return account["label"]


def account_hint(platform):
    try:
        stored = _read(_path(platform))
        if stored:
            if stored["client"] != _settings(platform)[0]:
                return "应用配置已更换，请重新登录", None
            return stored["label"], stored["identity"]
    except (ValueError, OSError, RuntimeError, KeyError):
        return "本机授权需重新登录", None
    identity = _environment_identity(platform)
    return ("系统已配置账号" if identity else "未登录", identity)


def _environment_identity(platform):
    if _path(platform).with_suffix(".application.json").exists():
        return None
    if platform == "douyin":
        return os.getenv("DOUYIN_OPEN_ID")
    token = os.getenv("BILIBILI_ACCESS_TOKEN")
    return hashlib.sha256(token.encode()).hexdigest() if token else None


def _active_account(platform):
    from core.workflow import _lock

    path = _path(platform)
    if not path.is_file():
        return None
    with _lock(path.with_suffix(".sqlite3"), timeout=5):
        account = _read(path)
        if account is None:
            return None
        if account["client"] != _settings(platform)[0]:
            raise RuntimeError("开放平台应用配置已更换，请重新登录账号。")
        if account["expires_at"] <= time.time() + 120:
            if not account.get("refresh_token"):
                raise RuntimeError("账号授权已过期，请重新登录。")
            account.update(_exchange(platform, refresh_token=account["refresh_token"]))
            _save(path, account)
        return account


def _check_identity(platform, account, expected):
    if expected is not None and (account["identity"] if account else _environment_identity(platform)) != expected:
        raise RuntimeError("账号已变更，请核对当前账号后重试。")


def douyin_credentials(expected=None):
    account = _active_account("douyin")
    _check_identity("douyin", account, expected)
    if account:
        return account["access_token"], account["open_id"]
    return os.getenv("DOUYIN_ACCESS_TOKEN"), _environment_identity("douyin")


def bilibili_credentials(expected=None):
    account = _active_account("bilibili")
    _check_identity("bilibili", account, expected)
    client, secret, _, _ = _settings("bilibili")
    if account and account["client"] != client:
        raise RuntimeError("应用配置已更换，请重新登录账号。")
    token = os.getenv("BILIBILI_ACCESS_TOKEN") if _environment_identity("bilibili") else None
    return client, secret, account["access_token"] if account else token
