"""Submit reviewed packages through official creator APIs."""

import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path

import requests


class Rejected(RuntimeError):
    """The platform explicitly rejected the request."""


def _douyin_post(operation, token, account, **payload):
    return _douyin_request("POST", f"/video/{operation}/", token, account, **payload)


def _douyin_request(method, path, token, account, *, params=None, **payload):
    try:
        response = requests.request(
            method, f"https://open.douyin.com{path}",
            params={"open_id": account, **(params or {})}, headers={"access-token": token},
            timeout=(8, 150 if "upload" in path else 20), allow_redirects=False, **payload,
        )
    except requests.RequestException as error:
        raise RuntimeError(f"抖音接口连接失败（{type(error).__name__}）") from error
    if response.status_code != 200:
        raise RuntimeError(f"抖音接口 HTTP {response.status_code}")
    data = response.json().get("data", {})
    if str(data.get("error_code")) != "0":
        if str(data.get("error_code")).isdigit():
            raise Rejected(f"抖音接口错误码 {data['error_code']}")
        raise RuntimeError("抖音接口返回未知状态。")
    return data


def _with_receipt(directory, prepare, create):
    from core.workflow import _lock

    pending, receipt = directory / "submitting.txt", directory / "submitted.txt"
    with _lock(directory / "publish.sqlite3"):
        if receipt.is_file():
            return receipt.read_text(encoding="utf-8")
        if pending.exists():
            raise RuntimeError("提交结果待核对，请先查看平台创作后台。")
        uploaded = prepare()
        # Only the create request can leave an unconfirmed platform submission.
        pending.write_text("请核对平台创作后台。", encoding="utf-8")
        try:
            item_id = create(uploaded)
        except Rejected:
            pending.unlink()
            raise
        if not item_id:
            raise RuntimeError("平台未返回稿件编号，请核对创作后台。")
        temp = receipt.with_suffix(".tmp")
        temp.write_text(item_id, encoding="utf-8")
        temp.replace(receipt)
        pending.unlink()
        return item_id


def submit_douyin(package, directory, *, expected_account=None):
    from core.accounts import douyin_credentials

    token, account = douyin_credentials(expected_account)
    if not token or not account:
        raise RuntimeError("请先配置有 video.create 权限的 DOUYIN_ACCESS_TOKEN 和 DOUYIN_OPEN_ID。")
    video = Path(package["video"])
    if video.stat().st_size > 128_000_000:
        raise ValueError("该视频超过抖音单文件上传上限。")

    def prepare():
        with video.open("rb") as stream:
            uploaded = _douyin_post("upload", token, account, files={"video": (video.name, stream, "video/mp4")})
        return uploaded["video"]["video_id"]

    def create(video_id):
        result = _douyin_post("create", token, account, json={"video_id": video_id, "text": package["caption"]})
        return result["item_id"]

    return _with_receipt(directory, prepare, create)


BILI_MEMBER = "https://member.bilibili.com/arcopen/fn/archive"
BILI_UPLOAD = "https://openupos.bilivideo.com/video/v2/upload"


def _bili_post(url, *, credentials=None, json_body=None, method="POST", **payload):
    headers = {"Accept": "application/json"}
    body = b""
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode()
        payload["data"] = body
    if url == BILI_UPLOAD:
        headers["Content-Type"] = "application/octet-stream"
    elif "files" not in payload:
        headers["Content-Type"] = "application/json"
    if credentials:
        client, secret, token = credentials
        signed = {
            "x-bili-accesskeyid": client, "x-bili-content-md5": hashlib.md5(body, usedforsecurity=False).hexdigest(),
            "x-bili-signature-method": "HMAC-SHA256", "x-bili-signature-nonce": str(uuid.uuid4()),
            "x-bili-signature-version": "2.0", "x-bili-timestamp": str(int(time.time())),
        }
        message = "\n".join(f"{key}:{signed[key]}" for key in sorted(signed))
        headers.update(signed, Authorization=hmac.new(secret.encode(), message.encode(), "sha256").hexdigest())
        headers["Access-Token"] = token
    try:
        response = requests.request(method, url, headers=headers, timeout=(8, 150 if "upload" in url else 20),
                                    allow_redirects=False, **payload)
    except requests.RequestException as error:
        raise RuntimeError(f"B站接口连接失败（{type(error).__name__}）") from error
    if response.status_code != 200:
        raise RuntimeError(f"B站接口 HTTP {response.status_code}")
    result = response.json()
    if result.get("code") != 0:
        if isinstance(result.get("code"), int):
            raise Rejected(f"B站接口错误码 {result['code']}")
        raise RuntimeError("B站接口返回未知状态。")
    return result.get("data") or {}


def submit_bilibili(package, directory, *, expected_account=None):
    from core.accounts import bilibili_credentials, bilibili_partition

    credentials = bilibili_credentials(expected_account)
    tid = bilibili_partition()
    if not all(credentials) or tid <= 0:
        raise RuntimeError("请配置 B站应用、投稿分区 ID，并以具有 ARC_BASE 权限的账号登录。")
    video, cover = Path(package["video"]), Path(package["cover"])
    description = package["credit_caption"]
    if video.stat().st_size > 100_000_000 or cover.stat().st_size > 5_000_000 or len(description) >= 250:
        raise ValueError("视频、封面或完整署名超过接口上限，请打开 B站创作者后台投稿。")

    def prepare():
        initialized = _bili_post(f"{BILI_MEMBER}/video/init", credentials=credentials,
                                 json_body={"name": video.name, "utype": "1"})
        upload_token = initialized["upload_token"]
        with video.open("rb") as stream:
            _bili_post(BILI_UPLOAD, params={"upload_token": upload_token}, data=stream)
        with cover.open("rb") as stream:
            uploaded_cover = _bili_post(f"{BILI_MEMBER}/cover/upload", credentials=credentials,
                                        files={"file": (cover.name, stream, "image/png")})
        return upload_token, uploaded_cover["url"]

    def create(uploaded):
        upload_token, cover_url = uploaded
        result = _bili_post(
            f"{BILI_MEMBER}/add-by-utoken", credentials=credentials, params={"upload_token": upload_token},
            json_body={"title": package["title"][:79], "cover": cover_url, "tid": tid,
                       "desc": description, "tag": ",".join(package["tags"]), "copyright": 1},
        )
        return result["resource_id"]

    return _with_receipt(directory, prepare, create)


def _douyin_review_state(video):
    states = {1: "已发布", 2: "不适宜公开", 4: "审核中", 5: "公开视频", 6: "好友可见", 7: "私密视频"}
    state = states.get(video.get("video_status"))
    if state is not None:
        return state
    reviewed = video.get("is_reviewed")
    if isinstance(reviewed, bool):
        return "审核已结束" if reviewed else "审核中"
    return "平台未提供状态"


def list_videos(platform, cursor=0, *, expected_account=None):
    from core.accounts import bilibili_credentials, douyin_credentials

    if platform == "douyin":
        token, account = douyin_credentials(expected_account)
        if not token or not account:
            raise RuntimeError("请先登录抖音账号并授予 video.list 权限。")
        result = _douyin_request("GET", "/video/list/", token, account, params={"cursor": cursor, "count": 20})
        return {
            "items": [{"title": video.get("title", ""),
                       "status": _douyin_review_state(video),
                       "url": video.get("share_url", "")} for video in result.get("list", ())],
            "next": result.get("cursor") if result.get("has_more") else None,
        }
    if platform == "bilibili":
        credentials = bilibili_credentials(expected_account)
        if not all(credentials):
            raise RuntimeError("请先登录 B站账号并授予 ARC_BASE 权限。")
        page = int(cursor) or 1
        result = _bili_post(f"{BILI_MEMBER}/viewlist", method="GET", credentials=credentials,
                            params={"pn": page, "ps": 20, "status": "all"})
        items = result.get("list", [])
        pagination = result.get("page") or {}
        page = pagination.get("pn") or page
        size = pagination.get("ps") or 20
        total = pagination.get("total")
        has_more = len(items) == size if total is None else page * size < total
        return {
            "items": [{"title": video.get("title", ""), "status": (video.get("addit_info") or {}).get("state_desc", ""),
                       "url": (video.get("video_info") or {}).get("share_url", "")} for video in items],
            "next": page + 1 if items and has_more else None,
        }
    raise ValueError("小红书作品请通过官方创作者后台管理。")
