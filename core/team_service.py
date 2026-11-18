"""Private workspace API for reviewing immutable exports from the desktop app."""

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import uuid
from contextlib import closing
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.util import FileWrapper


MAX_VIDEO_BYTES = 2 * 1024**3
MAX_JSON_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024
REVIEW_LIMIT = 100
PLATFORMS = {"bilibili", "douyin", "xiaohongshu"}
DECISIONS = {"comment", "approved", "changes_requested"}
REVIEW_PATH = re.compile(r"/v1/reviews(?:/([0-9a-f]{32})(?:/(video|feedback))?)?")
REVIEW_COLUMNS = "id, title, platform, caption, created_at, author, status, revision, bytes"


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = HTTPStatus(status)


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _text(value, field, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or "\x00" in value or any("\ud800" <= character <= "\udfff" for character in value)):
        raise RequestError(400, f"Invalid {field}.")
    return value


def _length(environ, maximum, content_type):
    if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != content_type:
        raise RequestError(415, f"Content-Type must be {content_type}.")
    raw = environ.get("CONTENT_LENGTH", "")
    if not raw:
        raise RequestError(411, "Content-Length is required.")
    if not re.fullmatch(r"[0-9]{1,12}", raw):
        raise RequestError(400, "Invalid Content-Length.")
    length = int(raw)
    if length > maximum:
        raise RequestError(413, "Request exceeds the size limit.")
    return length


def _read(stream, length):
    try:
        return stream.read(length)
    except (OSError, ValueError) as error:
        raise RequestError(400, "Request body ended early.") from error


def _json_body(environ, fields, optional=frozenset()):
    length = _length(environ, MAX_JSON_BYTES, "application/json")
    body = _read(environ["wsgi.input"], length)
    if len(body) != length:
        raise RequestError(400, "Request body ended early.")
    try:
        value = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError) as error:
        raise RequestError(400, "Invalid JSON body.") from error
    if not isinstance(value, dict) or not fields <= value.keys() or value.keys() - fields - optional:
        raise RequestError(400, "Unexpected JSON fields.")
    return value


def _mp4_header(header, length):
    return len(header) == 16 and header[4:8] == b"ftyp" and 16 <= int.from_bytes(header[:4], "big") <= length


def _json_reply(start_response, status, value):
    status = HTTPStatus(status)
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    headers = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body))),
               ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")]
    if status == HTTPStatus.UNAUTHORIZED:
        headers.append(("WWW-Authenticate", "Bearer"))
    start_response(f"{status.value} {status.phrase}", headers)
    return [body]


class TeamService:
    def __init__(self, directory):
        directory = Path(directory)
        self.videos = directory / "videos"
        self.videos.mkdir(parents=True, exist_ok=True)
        self.database = directory / "workspace.sqlite3"
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS members (
                    name TEXT PRIMARY KEY,
                    token_hash TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    caption TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    author TEXT NOT NULL REFERENCES members(name),
                    status TEXT NOT NULL DEFAULT 'pending',
                    revision INTEGER NOT NULL DEFAULT 0,
                    bytes INTEGER NOT NULL,
                    ready INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    review_id TEXT NOT NULL REFERENCES reviews(id),
                    revision INTEGER NOT NULL,
                    author TEXT NOT NULL REFERENCES members(name),
                    created_at TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    text TEXT NOT NULL,
                    PRIMARY KEY (review_id, revision)
                );
                CREATE INDEX IF NOT EXISTS ready_reviews ON reviews(ready, created_at DESC);
            """)
            db.execute("BEGIN IMMEDIATE")
            with db:
                columns = {column["name"] for column in db.execute("PRAGMA table_info(reviews)")}
                if "previous_id" not in columns:
                    db.execute("ALTER TABLE reviews ADD COLUMN previous_id TEXT REFERENCES reviews(id)")
                db.execute("CREATE INDEX IF NOT EXISTS review_followups ON reviews(previous_id, ready, created_at DESC)")

    def _connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def create_member(self, name):
        name = _text(name, "member name", 80).strip()
        if not name.isprintable():
            raise RequestError(400, "Invalid member name.")
        token = secrets.token_urlsafe(32)
        with closing(self._connect()) as db, db:
            change = db.execute("""
                INSERT INTO members(name, token_hash) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET token_hash = excluded.token_hash WHERE members.token_hash IS NULL
            """, (name, _digest(token)))
            if not change.rowcount:
                raise RequestError(409, "That member already has a token.")
        return token

    def revoke_member(self, name):
        name = _text(name, "member name", 80).strip()
        with closing(self._connect()) as db, db:
            change = db.execute("UPDATE members SET token_hash = NULL WHERE name = ? AND token_hash IS NOT NULL", (name,))
            if not change.rowcount:
                raise RequestError(404, "No active token for that member.")

    def _authenticate(self, db, environ):
        scheme, _, token = environ.get("HTTP_AUTHORIZATION", "").partition(" ")
        if scheme.lower() == "bearer" and re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            member = db.execute("SELECT name FROM members WHERE token_hash = ?", (_digest(token),)).fetchone()
            if member:
                return member["name"]
        raise RequestError(401, "A valid workspace token is required.")

    def __call__(self, environ, start_response):
        try:
            with closing(self._connect()) as db:
                author = self._authenticate(db, environ)
                match = REVIEW_PATH.fullmatch(environ["PATH_INFO"])
                if not match:
                    raise RequestError(404, "Endpoint not found.")
                review_id, resource = match.groups()
                method = environ["REQUEST_METHOD"]
                if method == "GET" and not review_id:
                    return _json_reply(start_response, 200, self._list_reviews(db, environ.get("QUERY_STRING", "")))
                if method == "POST" and not review_id:
                    return _json_reply(start_response, 201, {"id": self._create_review(db, author, environ)})
                if method == "GET" and not resource:
                    db.execute("BEGIN")
                    return _json_reply(start_response, 200, self._detail(db, review_id))
                if resource == "video" and method == "PUT":
                    self._upload(db, author, review_id, environ)
                    return _json_reply(start_response, 200, {"id": review_id})
                if resource == "video" and method == "GET":
                    return self._download(db, review_id, environ, start_response)
                if resource == "feedback" and method == "POST":
                    return _json_reply(start_response, 200, self._feedback(db, author, review_id, environ))
                raise RequestError(404, "Endpoint not found.")
        except RequestError as error:
            return _json_reply(start_response, error.status, {"error": str(error)})
        except (OSError, sqlite3.Error):
            logging.getLogger(__name__).exception("Workspace storage error")
            return _json_reply(start_response, 500, {"error": "Workspace storage is unavailable."})

    @staticmethod
    def _list_reviews(db, query):
        try:
            parameters = parse_qs(query, keep_blank_values=True, max_num_fields=8)
        except ValueError as error:
            raise RequestError(400, "Invalid review filters.") from error
        status = parameters.get("status", [""])[0]
        before = parameters.get("before", [""])[0]
        raw_limit = parameters.get("limit", [str(REVIEW_LIMIT)])[0]
        search = _text(parameters.get("q", [""])[0], "review search", 120, empty=True).strip()
        if status and status not in {"pending", "approved", "changes_requested"}:
            raise RequestError(400, "Invalid review status.")
        if before and not re.fullmatch(r"[0-9a-f]{32}", before):
            raise RequestError(400, "Invalid review cursor.")
        if not re.fullmatch(r"[1-9][0-9]{0,2}", raw_limit) or int(raw_limit) > REVIEW_LIMIT:
            raise RequestError(400, "Invalid review page size.")
        limit = int(raw_limit)
        where = ["ready = 1"]
        arguments = []
        if status:
            where.append("status = ?")
            arguments.append(status)
        if search:
            where.append("(instr(lower(title), lower(?)) > 0 OR instr(lower(author), lower(?)) > 0)")
            arguments.extend((search, search))
        if before:
            where.append("(created_at, id) < (SELECT created_at, id FROM reviews WHERE id = ? AND ready = 1)")
            arguments.append(before)
        rows = db.execute(f"""SELECT {REVIEW_COLUMNS} FROM reviews WHERE {' AND '.join(where)}
                              ORDER BY created_at DESC, id DESC LIMIT ?""", (*arguments, limit + 1)).fetchall()
        page = {"items": [dict(row) for row in rows[:limit]]}
        if query:
            page["next_cursor"] = rows[limit - 1]["id"] if len(rows) > limit else None
        return page

    def _create_review(self, db, author, environ):
        data = _json_body(environ, {"title", "platform", "caption", "bytes"}, {"previous_id"})
        title = _text(data["title"], "title", 500).strip()
        platform = _text(data["platform"], "platform", 30)
        caption = _text(data["caption"], "caption", 12000, empty=True)
        length = data["bytes"]
        if platform not in PLATFORMS:
            raise RequestError(400, "Unknown platform.")
        if type(length) is not int or not 16 <= length <= MAX_VIDEO_BYTES:
            raise RequestError(400, "Invalid MP4 size.")
        previous_id = data.get("previous_id")
        if previous_id is not None:
            if not isinstance(previous_id, str) or not re.fullmatch(r"[0-9a-f]{32}", previous_id):
                raise RequestError(400, "Invalid previous review.")
            if self._ready_review(db, previous_id)["platform"] != platform:
                raise RequestError(400, "A new version must use the same platform.")
        review_id = uuid.uuid4().hex
        with db:
            db.execute("""INSERT INTO reviews(id, title, platform, caption, created_at, author, bytes, previous_id)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (review_id, title, platform, caption, _now(), author, length, previous_id))
        return review_id

    def _ready_review(self, db, review_id):
        review = db.execute(f"SELECT {REVIEW_COLUMNS} FROM reviews WHERE id = ? AND ready = 1", (review_id,)).fetchone()
        if review is None:
            raise RequestError(404, "Review not found.")
        return dict(review)

    def _detail(self, db, review_id):
        review = self._ready_review(db, review_id)
        messages = db.execute("""SELECT author, created_at, decision, text FROM feedback
                                 WHERE review_id = ? ORDER BY revision""", (review_id,))
        review["feedback"] = [dict(message) for message in messages]
        versions = db.execute("""SELECT id, title, author, status, created_at, 'previous' AS relation
            FROM reviews WHERE ready = 1 AND id = (SELECT previous_id FROM reviews WHERE id = ?)
            UNION ALL SELECT id, title, author, status, created_at, 'next' AS relation
            FROM reviews WHERE ready = 1 AND previous_id = ?
            ORDER BY relation DESC, created_at DESC, id DESC""", (review_id, review_id))
        review["versions"] = [dict(version) for version in versions]
        return review

    def _upload_slot(self, db, author, review_id):
        slot = db.execute("SELECT author, bytes, ready FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if slot is None:
            raise RequestError(404, "Review not found.")
        if slot["author"] != author:
            raise RequestError(403, "Only the submitter can upload this video.")
        if slot["ready"]:
            raise RequestError(409, "This review already has an immutable video.")
        return slot["bytes"]

    def _upload(self, db, author, review_id, environ):
        expected = self._upload_slot(db, author, review_id)
        length = _length(environ, MAX_VIDEO_BYTES, "video/mp4")
        if length != expected:
            raise RequestError(400, "Video length does not match the review.")
        stream = environ["wsgi.input"]
        header = _read(stream, 16)
        if not _mp4_header(header, length):
            raise RequestError(400, "Invalid MP4 header.")
        file = tempfile.NamedTemporaryFile(dir=self.videos, prefix=".upload-", delete=False)
        staged = Path(file.name)
        try:
            with file:
                file.write(header)
                remaining = length - len(header)
                while remaining:
                    chunk = _read(stream, min(remaining, CHUNK_BYTES))
                    if not chunk:
                        raise RequestError(400, "Video upload ended early.")
                    file.write(chunk)
                    remaining -= len(chunk)
                file.flush()
                os.fsync(file.fileno())
            # Lock only the publication step. A pending file left by a crash can be replaced on retry.
            with db:
                db.execute("BEGIN IMMEDIATE")
                self._upload_slot(db, author, review_id)
                target = self.videos / f"{review_id}.mp4"
                staged.replace(target)
                try:
                    db.execute("UPDATE reviews SET ready = 1 WHERE id = ?", (review_id,))
                    db.commit()
                except sqlite3.Error:
                    target.unlink(missing_ok=True)
                    raise
        finally:
            staged.unlink(missing_ok=True)

    def _download(self, db, review_id, environ, start_response):
        review = self._ready_review(db, review_id)
        length = review["bytes"]
        try:
            file = (self.videos / f"{review_id}.mp4").open("rb")
        except FileNotFoundError as error:
            raise RequestError(404, "Video is unavailable.") from error
        try:
            if os.fstat(file.fileno()).st_size != length or not _mp4_header(file.read(16), length):
                raise RequestError(404, "Video is unavailable.")
            file.seek(0)
            start_response("200 OK", [("Content-Type", "video/mp4"), ("Content-Length", str(length)),
                                      ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")])
            return environ.get("wsgi.file_wrapper", FileWrapper)(file, CHUNK_BYTES)
        except Exception:
            file.close()
            raise

    def _feedback(self, db, author, review_id, environ):
        data = _json_body(environ, {"text", "decision", "revision"})
        decision = _text(data["decision"], "decision", 30)
        if decision not in DECISIONS:
            raise RequestError(400, "Unknown decision.")
        text = _text(data["text"], "feedback", 3000, empty=decision != "comment").strip()
        revision = data["revision"]
        if type(revision) is not int or not 0 <= revision < 2**63 - 1:
            raise RequestError(400, "Invalid review revision.")
        with db:
            change = db.execute("""UPDATE reviews SET revision = revision + 1,
                                    status = CASE WHEN ? = 'comment' THEN status ELSE ? END
                                    WHERE id = ? AND revision = ? AND ready = 1""",
                                (decision, decision, review_id, revision))
            if not change.rowcount:
                self._ready_review(db, review_id)
                raise RequestError(409, "Review changed. Reload it before sending feedback.")
            db.execute("""INSERT INTO feedback(review_id, revision, author, created_at, decision, text)
                          VALUES (?, ?, ?, ?, ?, ?)""", (review_id, revision + 1, author, _now(), decision, text))
            return self._detail(db, review_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path, help="Server-local workspace data directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-member").add_argument("name")
    commands.add_parser("revoke-member").add_argument("name")
    server = commands.add_parser("serve")
    server.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.command == "serve" and not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        service = TeamService(args.data)
        if args.command == "create-member":
            print(service.create_member(args.name))
        elif args.command == "revoke-member":
            service.revoke_member(args.name)
        else:
            try:
                from waitress import serve
            except ImportError:
                parser.exit(1, "Install Waitress to serve the workspace.\n")
            serve(service, host="127.0.0.1", port=args.port, max_request_body_size=MAX_VIDEO_BYTES)
    except (OSError, RequestError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
