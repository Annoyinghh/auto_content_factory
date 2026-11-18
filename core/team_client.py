"""Authenticated team review transport for the native workstation."""

import hashlib
import json
import re
import time
from os import fstat
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from core.accounts import protect_json, unprotect_json
from core.workflow import _lock, write_json


MAX_VIDEO_BYTES = 2 * 1024**3
MAX_PART_BYTES = 8 * 1024**2
MAX_UPLOAD_PARTS = MAX_VIDEO_BYTES // MAX_PART_BYTES


class TeamError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class TeamClient:
    def __init__(self, address, token):
        self.address = address.strip().rstrip("/")
        parts = urlsplit(self.address)
        if (not parts.hostname or parts.username is not None or parts.query or parts.fragment or
                not (parts.scheme == "https" or parts.scheme == "http" and parts.hostname == "127.0.0.1")):
            raise ValueError("请输入团队服务的 HTTPS 地址。")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token.strip()):
            raise ValueError("请输入管理员发给你的个人连接码。")
        self.token = token.strip()

    @classmethod
    def load(cls, root):
        path = root / "accounts" / "team.json"
        if not path.is_file():
            return None
        record = unprotect_json(json.loads(path.read_text(encoding="utf-8"))["sealed"])
        return cls(record["address"], record["token"])

    def save(self, root):
        path = root / "accounts" / "team.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"sealed": protect_json({"address": self.address, "token": self.token})}
        with _lock(path.with_suffix(".sqlite3"), timeout=5):
            write_json(path, record)

    @staticmethod
    def review_path(review_id):
        if not isinstance(review_id, str) or not re.fullmatch(r"[a-f0-9]{32}", review_id):
            raise TeamError("团队服务返回了无效的成片编号。")
        return f"/v1/reviews/{review_id}"

    def _request(self, method, path, *, headers=None, **options):
        try:
            response = requests.request(
                method, self.address + path,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "AutoContentFactory-Team/1.0",
                    **(headers or {}),
                },
                timeout=(8, 120), allow_redirects=False, **options,
            )
        except requests.RequestException as error:
            raise TeamError("暂时无法连接团队服务，请检查网络或团队地址。") from error
        if 200 <= response.status_code < 300:
            return response
        with response:
            status = response.status_code
            if status in (401, 403):
                raise TeamError("连接码无效、已停用或没有权限。请核对团队连接。", status)
            if status == 409:
                raise TeamError("这条审片记录已变化，请刷新后再操作。", status)
            if status == 413:
                raise TeamError("文件超出团队服务允许的大小。", status)
            if status == 429:
                try:
                    body = response.json()
                    code = body.get("code") if isinstance(body, dict) else None
                except ValueError:
                    code = None
                if code == "r2_storage_limit":
                    raise TeamError("团队云端存储已达上限，暂时无法送审新成片。", status)
                if code == "r2_write_limit":
                    raise TeamError("团队云端写入已达用量上限，暂时无法上传成片。", status)
                if code == "r2_read_limit":
                    raise TeamError("团队云端读取已达用量上限，暂时无法读取成片。", status)
            raise TeamError(f"团队服务未完成操作（HTTP {status}）。", status)

    def _json(self, method, path, **options):
        with self._request(method, path, **options) as response:
            try:
                return response.json()
            except ValueError as error:
                raise TeamError("团队服务的响应无法读取。") from error

    def reviews(self):
        return self._json("GET", "/v1/reviews")["items"]

    def review_page(self, search="", status="", before=""):
        page = self._json("GET", "/v1/reviews", params={
            "limit": 40, "q": search, "status": status, "before": before,
        })
        items = page["items"]
        if "next_cursor" not in page:
            term = search.casefold()
            items = [item for item in items if (not status or item["status"] == status) and (
                term in item["title"].casefold() or term in item["author"].casefold())]
        return items, page.get("next_cursor")

    def review(self, review_id):
        return self._json("GET", self.review_path(review_id))

    def _retry_upload(self, method, path, *, json_reply=False, **options):
        for attempt in range(3):
            try:
                if json_reply:
                    return self._json(method, path, **options)
                with self._request(method, path, **options):
                    return
            except TeamError as error:
                cause = error.__cause__
                retry_status = error.status is not None and 500 <= error.status < 600
                retry_network = (
                    isinstance(cause, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError))
                    and not isinstance(cause, requests.exceptions.SSLError)
                )
                if attempt == 2 or not (retry_status or retry_network):
                    raise
                time.sleep(0.25 * (attempt + 1))

    def _upload_parts(self, path, source, size, part_bytes, *, received=None, checksum=None, progress=None):
        if (type(part_bytes) is not int or not 0 < part_bytes <= MAX_PART_BYTES
                or size > part_bytes * MAX_UPLOAD_PARTS):
            raise TeamError("团队服务返回了无效的分片大小。")

        received = received or {}
        digest = hashlib.sha256() if checksum else None
        headers = {"Content-Type": "video/mp4"}
        changed = "成片文件大小已改变，请重新分享。"
        try:
            for number, offset in enumerate(range(0, size, part_bytes), 1):
                expected = min(part_bytes, size - offset)
                part = source.read(expected)
                if len(part) != expected:
                    raise TeamError(changed)
                if digest:
                    digest.update(part)
                if number not in received:
                    self._retry_upload("PUT", f"{path}/parts/{number}", data=part, headers=headers)
                elif received[number] != hashlib.sha256(part).hexdigest():
                    raise TeamError("成片与云端已收到的分片不一致，请重新送审。", 409)
                if progress:
                    progress(offset + expected, size)
            if source.read(1):
                raise TeamError(changed)
            if digest and digest.hexdigest() != checksum:
                raise TeamError("成片内容在上传期间发生变化，请重新送审。", 409)
        except OSError as error:
            raise TeamError("成片读取中断，请检查文件后重新分享。") from error

        self._retry_upload("POST", path + "/complete", json={})

    def _resume_upload(self, source, metadata, directory, progress):
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
        source.seek(0)
        signature = json.dumps((self.address, self.token, metadata, checksum), sort_keys=True, ensure_ascii=False)
        journal = directory / (hashlib.sha256(signature.encode()).hexdigest() + ".json")
        directory.mkdir(parents=True, exist_ok=True)

        def prepare(review_id):
            return self._retry_upload("PUT", self.review_path(review_id) + "/upload", json=metadata, json_reply=True)

        def new_review():
            review_id = uuid4().hex
            write_json(journal, {"id": review_id})
            return review_id

        with _lock(journal.with_suffix(".sqlite3"), timeout=1):
            review_id = json.loads(journal.read_text(encoding="utf-8"))["id"] if journal.is_file() else new_review()
            try:
                record = prepare(review_id)
            except TeamError as error:
                if error.status == 404:
                    journal.unlink(missing_ok=True)
                    return None
                if error.status != 410:
                    raise
                review_id = new_review()
                record = prepare(review_id)
            if record.get("id") != review_id or record.get("upload_mode") != "multipart":
                raise TeamError("团队服务返回了无效的续传记录。")
            if record.get("ready") is not True:
                try:
                    self._upload_parts(self.review_path(review_id), source, metadata["bytes"], record.get("part_bytes"),
                                       received={part["part_number"]: part["digest"] for part in record["uploaded_parts"]},
                                       checksum=checksum, progress=progress)
                except TeamError as error:
                    if error.status != 409:
                        raise
                    if prepare(review_id).get("ready") is not True:
                        journal.unlink(missing_ok=True)
                        raise
            journal.unlink(missing_ok=True)
            return review_id

    def share(self, platform, package, *, previous_id=None, resume_dir=None, progress=None):
        video = Path(package["video"])
        with video.open("rb") as source:
            size = fstat(source.fileno()).st_size
            if video.suffix.lower() != ".mp4" or not 0 < size <= MAX_VIDEO_BYTES:
                raise ValueError("团队审片需要不超过 2 GiB 的 MP4。")
            metadata = {
                "title": package["title"], "platform": platform, "caption": package.get("caption", ""), "bytes": size,
            }
            if previous_id is not None:
                self.review_path(previous_id)
                metadata["previous_id"] = previous_id
            if resume_dir is not None:
                review_id = self._resume_upload(source, metadata, resume_dir, progress)
                if review_id:
                    return review_id
            record = self._json("POST", "/v1/reviews", json=metadata)
            path = self.review_path(record["id"])
            mode = record.get("upload_mode")
            if mode == "multipart":
                self._upload_parts(path, source, size, record.get("part_bytes"), progress=progress)
            elif mode is None:
                if progress:
                    progress(0, size)
                with self._request("PUT", path + "/video", data=source, headers={"Content-Type": "video/mp4"}):
                    pass
            else:
                raise TeamError("团队服务返回了未知的上传方式。")
        return record["id"]

    def feedback(self, review_id, revision, decision, text):
        return self._json("POST", self.review_path(review_id) + "/feedback", json={
            "revision": revision, "decision": decision, "text": text,
        })

    def download(self, item, cache):
        path = self.review_path(item["id"])
        size = item["bytes"]
        if type(size) is not int or not 0 < size <= MAX_VIDEO_BYTES:
            raise TeamError("团队服务返回了无效的视频大小。")
        directory = cache / hashlib.sha256(self.address.encode()).hexdigest()[:16]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{item['id']}.mp4"
        if target.is_file() and target.stat().st_size == size:
            self.review(item["id"])
            return target
        pending = target.with_suffix(f".{uuid4().hex}.part")
        try:
            with self._request("GET", path + "/video", stream=True) as response, pending.open("wb") as output:
                received = 0
                for chunk in response.iter_content(512 * 1024):
                    received += len(chunk)
                    if received > size:
                        raise TeamError("下载大小与成片记录不一致。")
                    output.write(chunk)
            if received != size:
                raise TeamError("成片下载未完成，可以重试。")
            pending.replace(target)
        except requests.RequestException as error:
            raise TeamError("成片下载中断，可以重试。") from error
        finally:
            pending.unlink(missing_ok=True)
        return target
