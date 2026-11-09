import datetime as dt
import json
import os
import sqlite3
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from requests import Response

from config import get_ffmpeg
from core.accounts import (
    account_hint, application_fields, auth_code, begin_login, bilibili_partition, douyin_credentials,
    finish_login, protect_json, save_application, unprotect_json,
)
from core.daily_news import parse_feed, select_stories
from core.editing import build_news_case
from core.news_media import commons_asset
from core.news_release import _with_receipt, list_videos, submit_bilibili, submit_douyin
from core.workflow import edition_path, run_daily, run_stage, write_json
from core.desktop_jobs import run_worker


NOW = dt.datetime(2026, 11, 6, 12, tzinfo=dt.timezone.utc)
FEED = """<rss version="2.0"><channel><title>测试资讯</title>
<item><title>某平台推出新的订阅方案</title>
<link>https://example.com/subscription?utm_source=rss</link>
<pubDate>Fri, 06 Nov 2026 08:00:00 GMT</pubDate>
<description><![CDATA[<p>某平台发布了新的订阅方案，包含按月和按年两种选项。</p>]]></description></item>
<item><title>某公司发布新款芯片</title><link>https://example.com/chip</link>
<pubDate>Fri, 06 Nov 2026 08:00:00 GMT</pubDate>
<description>新款芯片面向笔记本，厂商公布了部分性能数据。</description></item>
<item><title>旧消息：芯片</title><link>https://example.com/old</link>
<pubDate>Fri, 01 May 2026 08:00:00 GMT</pubDate><description>过期消息。</description></item>
</channel></rss>"""


@contextmanager
def scratch_dir():
    path = Path.cwd() / f"test_output_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


class DailyTest(unittest.TestCase):
    def test_intake_prunes_old_articles_and_queues_a_real_rank_match(self):
        from core.intake import refresh_intake

        stories = parse_feed(FEED, NOW)
        old = {**stories[0], "title": "不再保留的历史报道"}
        ranking = {"published": NOW.timestamp(), "entries": [{"rank": 1, "title": stories[0]["title"],
                    "url": "https://example.com/trend", "heat": "21万"}]}
        with scratch_dir() as root, patch("core.intake.fetch_rank", return_value=ranking), \
                patch("core.intake.collect_news", side_effect=[([old], []), (stories, [])]):
            refresh_intake(root, NOW - dt.timedelta(days=8))
            snapshot = refresh_intake(root, NOW)
            self.assertEqual(len(snapshot["articles"]), 2)
            with closing(sqlite3.connect(root / "collection.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 2)
            edition = run_stage("queue_hotspots", NOW.date(), root, now=NOW)
            self.assertEqual(len(edition["items"]), 1)
            item = next(iter(edition["items"].values()))
            self.assertEqual(item["hotspot"]["rank"], 1)
            self.assertEqual(item["story"]["url"], stories[0]["url"])

    def test_held_render_cannot_be_submitted_until_restored(self):
        with scratch_dir() as output:
            path = edition_path(output, NOW.date())
            path.parent.mkdir(parents=True)
            package = {"video": "current.mp4"}
            write_json(path, {"items": {"douyin": {"package": package, "edit_revision": "x", "package_revision": "x"}}})
            publish = {"video": package["video"], "account": "account-A"}
            run_stage("hold", NOW.date(), output, "douyin", {"video": package["video"], "hold": True})
            with patch("core.news_release.submit_douyin", return_value="receipt") as submit:
                with self.assertRaisesRegex(RuntimeError, "暂缓"):
                    run_stage("publish", NOW.date(), output, "douyin", publish)
                submit.assert_not_called()
                run_stage("hold", NOW.date(), output, "douyin", {"video": package["video"], "hold": False})
                self.assertEqual(run_stage("publish", NOW.date(), output, "douyin", publish), "receipt")

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing semantics")
    def test_manifest_replace_survives_a_reader_that_releases_the_file(self):
        with scratch_dir() as root:
            manifest = root / "edition.json"
            write_json(manifest, {"version": 1})
            replace = Path.replace
            with manifest.open(encoding="utf-8") as reader:
                def replace_after_reader(source, target):
                    try:
                        return replace(source, target)
                    except PermissionError:
                        reader.close()
                        raise

                with patch.object(Path, "replace", replace_after_reader):
                    write_json(manifest, {"version": 2})
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), {"version": 2})

    def test_oauth_rejects_wrong_destination_and_state_before_exchange(self):
        env = {"DOUYIN_CLIENT_KEY": "client", "DOUYIN_CLIENT_SECRET": "configured",
               "DOUYIN_REDIRECT_URI": "https://example.com/callback"}
        with patch.dict(os.environ, env):
            login = begin_login("douyin")
            self.assertIn("open.douyin.com", login["url"])
            self.assertEqual(auth_code("douyin", login, f"https://example.com/callback?code=ok&state={login['state']}"), "ok")
            with self.assertRaisesRegex(ValueError, "回调地址"):
                auth_code("douyin", login, f"https://different.test/callback?code=ok&state={login['state']}")
            with self.assertRaisesRegex(ValueError, "状态"):
                auth_code("douyin", login, "https://example.com/callback?code=ok&state=wrong")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_windows_seals_worker_credentials(self):
        private = {"code": "temporary-secret-code"}
        sealed = protect_json(private)
        self.assertNotIn(private["code"], sealed)
        self.assertEqual(unprotect_json(sealed), private)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_desktop_application_settings_drive_login_without_environment_credentials(self):
        with scratch_dir() as root, patch("core.accounts.DATA_DIR", root), patch.dict(os.environ, {}, clear=True):
            redirect = "https://example.com/creator-callback"
            save_application("bilibili", "studio-app", "private-studio-secret", redirect, "207")
            self.assertEqual(application_fields("bilibili"), ("studio-app", redirect, "207"))
            self.assertEqual(bilibili_partition(), 207)
            self.assertIn("client_id=studio-app", begin_login("bilibili")["url"])
            self.assertNotIn("private-studio-secret", (root / "accounts" / "bilibili.application.json").read_text())
            save_application("bilibili", "studio-app", "", redirect, "124")
            self.assertEqual(bilibili_partition(), 124)
            with self.assertRaisesRegex(ValueError, "应用密钥"):
                save_application("bilibili", "other-app", "", redirect)
            self.assertIn("client_id=studio-app", begin_login("bilibili")["url"])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_login_saves_credentials_and_checks_selected_account_before_posting(self):
        with (
            scratch_dir() as root,
            patch.dict(os.environ, {"DOUYIN_CLIENT_KEY": "client", "DOUYIN_CLIENT_SECRET": "secret",
                                    "DOUYIN_REDIRECT_URI": "https://example.com/callback"}),
            patch("core.accounts.DATA_DIR", root),
            patch("core.accounts._exchange", return_value={
                "access_token": "private-access-token", "open_id": "account-A", "expires_at": 4_000_000_000,
            }),
            patch("core.accounts._account_profile", return_value=("测试账号", "account-A")),
        ):
            login = begin_login("douyin")
            callback = f"https://example.com/callback?code=ok&state={login['state']}"
            self.assertEqual(finish_login("douyin", login, callback), "测试账号")
            self.assertEqual(account_hint("douyin"), ("测试账号", "account-A"))
            self.assertEqual(douyin_credentials("account-A"), ("private-access-token", "account-A"))
            self.assertNotIn("private-access-token", (root / "accounts" / "douyin.json").read_text())
            with self.assertRaisesRegex(RuntimeError, "账号已变更"):
                douyin_credentials("account-B")

    def test_ai_success_persists_suggestion_without_replacing_manual_draft(self):
        with scratch_dir() as root:
            stories = parse_feed(FEED, NOW)
            with patch("core.daily_news.collect_news", return_value=(stories, [])):
                run_stage("collect", NOW.date(), root, now=NOW)
            original = run_stage("draft", NOW.date(), root, "xiaohongshu")
            suggestion = {"scenes": [{**scene, "text": "助手建议，须先审阅。"} for scene in original["scenes"]]}
            with patch.dict(os.environ, {"CONTENT_AI_MODEL": "test-model"}), patch("requests.Session") as session:
                post = session.return_value.__enter__.return_value.post
                post.return_value.status_code = 200
                post.return_value.json.return_value = {"message": {"content": json.dumps(suggestion)}}
                self.assertEqual(run_stage("ai", NOW.date(), root, "xiaohongshu"), suggestion)
                self.assertEqual(post.call_args.args[0], "http://127.0.0.1:11434/api/chat")
                scenes = post.call_args.kwargs["json"]["format"]["properties"]["scenes"]
                self.assertEqual((scenes["minItems"], scenes["maxItems"]), (2, 6))
                self.assertEqual(scenes["items"]["properties"]["media"]["enum"], [""])
            self.assertEqual(run_stage("draft", NOW.date(), root, "xiaohongshu"), original)

    def test_ai_worker_failure_leaves_draft_worker_available(self):
        with scratch_dir() as root:
            stories = parse_feed(FEED, NOW)
            with patch("core.daily_news.collect_news", return_value=(stories, [])):
                run_stage("collect", NOW.date(), root, now=NOW)
            with patch.dict(os.environ, {"CONTENT_AI_MODEL": ""}):
                with self.assertRaisesRegex(RuntimeError, "Ollama"):
                    run_worker("ai", NOW.date(), root, "xiaohongshu")
            crashed = [sys.executable, "-c", "import os; os._exit(47)"]
            with patch("core.desktop_jobs.desktop_command", return_value=crashed):
                with self.assertRaisesRegex(RuntimeError, "意外退出（47）"):
                    run_worker("ai", NOW.date(), root, "xiaohongshu")
            self.assertTrue(run_worker("draft", NOW.date(), root, "xiaohongshu")["scenes"])

    @unittest.skipUnless(os.name == "nt", "Windows process lifetime")
    def test_worker_timeout_stops_its_child_process(self):
        import _winapi

        with scratch_dir() as root:
            marker = root / "child.pid"
            child = (
                "import os, sys, time; from pathlib import Path; "
                "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
            )
            launcher = (
                "import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); time.sleep(60)"
            )
            command = [sys.executable, "-c", launcher, child, str(marker)]
            with patch("core.desktop_jobs.desktop_command", return_value=command), patch.dict(
                "core.desktop_jobs.DEADLINES", {"render": 3},
            ):
                with self.assertRaisesRegex(RuntimeError, "运行超时"):
                    run_worker("render", NOW.date(), root, "douyin")
            self.assertTrue(marker.is_file(), "The timed-out worker never started its child")
            pid = int(marker.read_text())
            try:
                handle = _winapi.OpenProcess(0x00100001, False, pid)  # SYNCHRONIZE | PROCESS_TERMINATE
            except OSError as error:
                if error.winerror == 87:  # The process has already exited.
                    return
                raise
            try:
                status = _winapi.WaitForSingleObject(handle, 1000)
                if status != _winapi.WAIT_OBJECT_0:
                    _winapi.TerminateProcess(handle, 1)
                self.assertEqual(status, _winapi.WAIT_OBJECT_0, "Worker left its child running after timeout")
            finally:
                _winapi.CloseHandle(handle)

    def test_selects_recent_evidence_for_distinct_audiences(self):
        stories = parse_feed(FEED, NOW)
        self.assertEqual(len(stories), 2)
        self.assertEqual(stories[0]["url"], "https://example.com/subscription")
        chosen = select_stories(stories, set())
        self.assertEqual(chosen["xiaohongshu"]["category"], "消费")
        self.assertEqual(chosen["bilibili"]["category"], "科技")
        xhs = build_news_case(stories[0], "xiaohongshu")
        dy = build_news_case(stories[0], "douyin")
        self.assertNotEqual(xhs["scenes"][0]["text"], dy["scenes"][0]["text"])
        self.assertEqual(xhs["news"]["source_url"], stories[0]["url"])
        self.assertIn("按月", " ".join(scene["text"] for scene in xhs["scenes"]))

    def test_report_sentence_preserves_the_price_after_a_long_intro(self):
        story = parse_feed(FEED, NOW)[0]
        story["summary"] = "某平台今天公布了新的会员政策，" + "消费者关心服务范围是否调整，" * 4 + "月费为 39 元。"
        case = build_news_case(story, "xiaohongshu")
        self.assertTrue(case["scenes"][1]["text"].endswith("月费为 39 元。"))

    def test_blocks_restricted_and_unattributed_material(self):
        record = {
            "title": "File:Example.jpg",
            "imageinfo": [{
                "mime": "image/jpeg",
                "url": "https://upload.wikimedia.org/wikipedia/commons/a/a0/Test.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Test.jpg",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                    "Artist": {"value": "<a>Example author</a>"},
                },
            }],
        }
        self.assertEqual(commons_asset(record)["author"], "Example author")
        meta = record["imageinfo"][0]["extmetadata"]
        meta["Restrictions"] = {"value": "Personality rights"}
        self.assertIsNone(commons_asset(record))
        del meta["Restrictions"]
        del meta["Artist"]
        self.assertIsNone(commons_asset(record))

    def test_daily_rerun_reuses_completed_edition(self):
        stories = parse_feed(FEED, NOW)
        with scratch_dir() as root:
            with (
                patch("core.daily_news.collect_news", return_value=(stories, [])) as collect,
                patch("core.news_media.download_story_media", return_value=([], [])),
                patch("core.publisher.package_case_for_all_platforms", side_effect=lambda case, platforms, **kwargs: {
                    "platforms": {platform: {"video": f"{platform}.mp4"} for platform in platforms},
                }) as render,
            ):
                first = run_daily(NOW, root)
                second = run_daily(NOW, root)
            self.assertEqual(first, second)
            self.assertEqual(first["items"]["xiaohongshu"]["status"], "ready")
            self.assertEqual(render.call_count, 3)
            collect.assert_called_once()

    def test_uncertain_publish_is_not_retried(self):
        with scratch_dir() as output:
            video = output / "video.mp4"
            video.write_bytes(b"video")
            package = {"video": str(video), "caption": "一条测试"}
            with (
                patch.dict("os.environ", {"DOUYIN_ACCESS_TOKEN": "token", "DOUYIN_OPEN_ID": "account"}),
                patch("core.news_release._douyin_post", side_effect=[
                    {"video": {"video_id": "id"}}, TimeoutError("unknown"),
                ]) as post,
            ):
                with self.assertRaises(TimeoutError):
                    submit_douyin(package, output)
                with self.assertRaisesRegex(RuntimeError, "核对"):
                    submit_douyin(package, output)
            self.assertEqual(post.call_count, 2)

    def test_publish_can_retry_after_upload_worker_exits(self):
        with scratch_dir() as output:
            upload_worker = (
                "import os, sys; from pathlib import Path; from core.news_release import _with_receipt; "
                "_with_receipt(Path(sys.argv[1]), lambda: os._exit(47), lambda upload: 'unexpected')"
            )
            process = subprocess.run([sys.executable, "-c", upload_worker, str(output)], timeout=10)
            self.assertEqual(process.returncode, 47)

            def prepare():
                with self.assertRaises(RuntimeError):
                    _with_receipt(output, lambda: self.fail("Concurrent upload started"), lambda upload: "")
                return "uploaded-file"

            self.assertEqual(_with_receipt(output, prepare, lambda upload: "item-" + upload), "item-uploaded-file")
            (output / "submitting.txt").write_text("stale pending marker", encoding="utf-8")
            self.assertEqual(_with_receipt(output, lambda: self.fail("Uploaded after receipt"), str), "item-uploaded-file")

    def test_publish_requires_the_confirmed_video_even_after_rerendering_the_same_draft(self):
        with scratch_dir() as output, patch("core.news_release.submit_douyin", return_value="item-id") as submit:
            path = edition_path(output, NOW.date())
            path.parent.mkdir(parents=True)
            package = {"video": str(output / "new.mp4")}
            write_json(path, {"items": {"douyin": {
                "package": package, "edit_revision": "same-draft", "package_revision": "same-draft",
            }}})
            approved = {"account": "account-A", "video": str(output / "old.mp4")}
            with self.assertRaisesRegex(RuntimeError, "成片已更换"):
                run_stage("publish", NOW.date(), output, "douyin", approved)
            submit.assert_not_called()
            approved["video"] = package["video"]
            self.assertEqual(run_stage("publish", NOW.date(), output, "douyin", approved), "item-id")
            self.assertEqual(submit.call_args.kwargs["expected_account"], "account-A")

    def test_bilibili_submits_after_uploading_the_video_and_cover(self):
        with scratch_dir() as output:
            video, cover = output / "video.mp4", output / "cover.png"
            video.write_bytes(b"video")
            cover.write_bytes(b"cover")
            package = {"video": str(video), "cover": str(cover), "title": "测试", "tags": ["科技"],
                       "credit_caption": "资讯来源\nhttps://example.com"}
            responses = iter([{"upload_token": "upload"}, {}, {"url": "https://archive.biliimg.com/cover.png"},
                              {"resource_id": "BV123"}])

            def reply(request, **_):
                if request.url.startswith("https://openupos.bilivideo.com/"):
                    self.assertEqual(request.body.read(), b"video")
                    self.assertEqual(request.headers["Content-Type"], "application/octet-stream")
                    self.assertNotIn("Authorization", request.headers)
                response = Response()
                response.status_code = 200
                response._content = json.dumps({"code": 0, "data": next(responses)}).encode("utf-8")
                return response

            with (
                patch.dict("os.environ", {"BILIBILI_CLIENT_ID": "id", "BILIBILI_CLIENT_SECRET": "secret",
                                         "BILIBILI_ACCESS_TOKEN": "token", "BILIBILI_TID": "124"}),
                patch("requests.Session.send", side_effect=reply) as send,
            ):
                self.assertEqual(submit_bilibili(package, output), "BV123")
                self.assertEqual(submit_bilibili(package, output), "BV123")
            self.assertEqual(send.call_count, 4)
            _, _, cover_request, submit_request = [call.args[0] for call in send.call_args_list]
            self.assertTrue(cover_request.headers["Content-Type"].startswith("multipart/form-data; boundary="))
            self.assertIn(b'name="file"', cover_request.body)
            self.assertEqual(submit_request.headers["Accept"], "application/json")
            self.assertEqual(json.loads(submit_request.body)["desc"], package["credit_caption"])
            self.assertTrue(submit_request.url.endswith("/add-by-utoken?upload_token=upload"))

    def test_bilibili_list_sends_required_signed_headers_and_uses_reported_pagination(self):
        items = [{"title": "视频", "addit_info": {"state_desc": "开放浏览"}}] * 2
        response = Response()
        response.status_code = 200
        data = {"list": items, "page": {"pn": 1, "ps": 2, "total": 3}}
        response._content = json.dumps({"code": 0, "data": data}).encode("utf-8")
        with (
            patch("core.accounts.bilibili_credentials", return_value=("client", "secret", "token")),
            patch("requests.Session.send", return_value=response) as send,
        ):
            first = list_videos("bilibili")
            request = send.call_args.args[0]
            self.assertEqual(request.headers["Accept"], "application/json")
            self.assertEqual(request.headers["Content-Type"], "application/json")
            self.assertEqual(request.headers["x-bili-content-md5"], "d41d8cd98f00b204e9800998ecf8427e")
            self.assertEqual(first["next"], 2)
            self.assertEqual(first["items"][0]["status"], "开放浏览")
            data["page"] = {"pn": 1, "ps": 20, "total": 20}
            data["list"] = items * 10
            response._content = json.dumps({"code": 0, "data": data}).encode("utf-8")
            self.assertIsNone(list_videos("bilibili")["next"])

    def test_douyin_list_distinguishes_reported_and_missing_review_states(self):
        payload = {"list": [
            {"video_status": 2, "is_reviewed": True},
            {"video_status": 5},
            {"is_reviewed": False},
            {},
        ]}
        with (
            patch("core.accounts.douyin_credentials", return_value=("token", "account")),
            patch("core.news_release._douyin_request", return_value=payload),
        ):
            statuses = [item["status"] for item in list_videos("douyin")["items"]]
        self.assertEqual(statuses, ["不适宜公开", "公开视频", "审核中", "平台未提供状态"])

    def test_ai_and_material_failures_do_not_block_editing_or_other_platforms(self):
        stories = parse_feed(FEED, NOW)
        with scratch_dir() as root:
            with patch("core.daily_news.collect_news", return_value=(stories, [])):
                run_stage("collect", NOW.date(), root, now=NOW)
            with patch("core.news_media.download_story_media", side_effect=RuntimeError("素材服务宕机")):
                with self.assertRaisesRegex(RuntimeError, "素材服务宕机"):
                    run_stage("media", NOW.date(), root, "xiaohongshu")
            draft = run_stage("draft", NOW.date(), root, "xiaohongshu")
            draft["scenes"][0]["text"] = "编辑保存的口播。"
            run_stage("save", NOW.date(), root, "xiaohongshu", draft)
            with patch("core.editing.suggest_cut", side_effect=RuntimeError("模型离线")):
                with self.assertRaisesRegex(RuntimeError, "模型离线"):
                    run_stage("ai", NOW.date(), root, "xiaohongshu")
            self.assertEqual(run_stage("draft", NOW.date(), root, "xiaohongshu"), draft)
            with patch("core.publisher.package_case_for_all_platforms") as render:
                render.return_value = {"platforms": {"douyin": {"video": "existing.mp4"}}}
                self.assertEqual(run_stage("render", NOW.date(), root, "douyin")["video"], "existing.mp4")
                self.assertEqual(render.call_args.args[0]["news"]["media"], [])

    def test_generates_all_three_mp4s_with_photo_and_footage(self):
        stories = parse_feed(FEED, NOW)
        with scratch_dir() as root:
            audio = root / "speech.mp3"
            photo, footage = root / "photo.jpg", root / "footage.mp4"
            Image.new("RGB", (500, 340), "#61928b").save(photo)
            subprocess.run([get_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=duration=0.9", str(audio)],
                           check=True, capture_output=True)
            subprocess.run([get_ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:duration=0.7",
                            "-c:v", "libx264", str(footage)], check=True, capture_output=True)
            credit = {"preview": str(photo), "author": "Test author", "license": "CC BY 4.0",
                      "license_url": "https://creativecommons.org/licenses/by/4.0/",
                      "page_url": "https://commons.wikimedia.org/wiki/File:Test.jpg", "title": "Test media"}
            assets = [{**credit, "kind": "image", "path": str(photo)},
                      {**credit, "kind": "video", "path": str(footage)}]

            async def synthesize(_text, _voice, _rate, target):
                shutil.copyfile(audio, target)

            with (
                patch("core.daily_news.collect_news", return_value=(stories, [])),
                patch("core.news_media.download_story_media", return_value=(assets, [])),
                patch("core.audio_engine._synthesize_text", side_effect=synthesize),
            ):
                edition = run_daily(NOW, root)
            for platform, item in edition["items"].items():
                self.assertEqual(item["status"], "ready", item.get("error"))
                package = item["package"]
                info = subprocess.run([get_ffmpeg(), "-i", package["video"]], capture_output=True, text=True)
                self.assertIn("Video: h264", info.stderr, platform)
                self.assertIn("Audio: aac", info.stderr, platform)
                self.assertIn("Test author", package["caption"])
                self.assertTrue(Path(package["video"]).with_name("cut_01.mp4").is_file())

    def test_manual_story_can_be_edited_and_rendered_when_collection_is_offline(self):
        with scratch_dir() as root:
            platform, day = "douyin", NOW.date()
            with patch("core.daily_news.collect_news", side_effect=RuntimeError("资讯源离线")):
                with self.assertRaisesRegex(RuntimeError, "资讯源离线"):
                    run_stage("collect", day, root, now=NOW)
            title = "周末阳台种植"
            draft = {"scenes": [
                {"title": "开场", "text": "今天在阳台种罗勒。", "media": "", "zoom": False},
                {"title": "正文", "text": "用有排水孔的花盆。", "media": "", "zoom": False},
            ]}
            payload = {"title": title, "cut": draft}
            run_worker("create", day, root, platform, payload)
            self.assertEqual(run_worker("draft", day, root, platform), draft)
            with self.assertRaisesRegex(RuntimeError, "已有选题"):
                run_worker("create", day, root, platform, {**payload, "title": "另一个选题"})

            with patch("core.news_media.download_story_media", side_effect=RuntimeError("素材源离线")):
                with self.assertRaisesRegex(RuntimeError, "素材源离线"):
                    run_stage("media", day, root, platform)
            photo, footage = root / "private-photo.png", root / "private-footage.mp4"
            Image.new("RGB", (400, 300), "#50876c").save(photo)
            subprocess.run([get_ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:duration=0.7",
                            "-c:v", "libx264", str(footage)], check=True, capture_output=True)
            def finish_collection(_story, _directory):
                run_worker("import_media", day, root, platform, str(photo))
                return [], []

            with patch("core.news_media.download_story_media", side_effect=finish_collection):
                run_stage("media", day, root, platform)
            assets = run_worker("import_media", day, root, platform, str(footage))
            photo.unlink()
            footage.unlink()
            for scene, asset in zip(draft["scenes"], assets, strict=True):
                scene["media"] = asset["page_url"]
                self.assertTrue(Path(asset["path"]).is_file())
                self.assertTrue(Path(asset["preview"]).is_file())

            draft["scenes"][1]["text"] = "选一个底部有排水孔的花盆。"
            run_worker("save", day, root, platform, draft)
            audio = root / "speech.mp3"
            subprocess.run([get_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=duration=0.9", str(audio)],
                           check=True, capture_output=True)

            async def synthesize(_text, _voice, _rate, target):
                shutil.copyfile(audio, target)

            with patch("core.audio_engine._synthesize_text", side_effect=synthesize):
                package = run_stage("render", day, root, platform)
            self.assertTrue(Path(package["video"]).is_file())
            self.assertIn(title, package["caption"])
            self.assertNotIn("资讯来源", package["credit_caption"])
            self.assertNotIn("private-photo", package["caption"])
            self.assertNotIn("private-footage", package["caption"])
            self.assertTrue(Path(package["video"]).with_name("cut_01.mp4").is_file())
            with patch("core.daily_news.collect_news", return_value=(parse_feed(FEED, NOW), [])):
                edition = run_stage("collect", day, root, now=NOW)
            self.assertEqual(edition["items"][platform]["edit"], draft)
            self.assertIn("bilibili", edition["items"])


if __name__ == "__main__":
    unittest.main()
