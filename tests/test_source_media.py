import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from core import source_media
from tests.test_works_library import scratch_dir


class SourceMediaTests(unittest.TestCase):
    def test_jpeg_with_hdr_gain_map_remains_one_static_publisher_photo(self):
        report={'title':'设备实拍','url':'https://www.news.cn/politics/20261108/'+'b'*32+'/c.html',
                'media_candidates':[{'kind':'image','url':'https://www.news.cn/hdr.jpg'}]}
        def download(_url,path,_limit):
            primary=Image.new('RGB',(1280,960),'orange')
            primary.save(path,format='MPO',save_all=True,append_images=[Image.new('RGB',(320,240),'gray')])
            with Image.open(path) as image:self.assertEqual(image.format,'MPO')
        with scratch_dir() as root,patch('core.source_media._get_file',side_effect=download):
            saved=source_media.cache_candidate(report,source_media.candidates(report)[0],root)
            self.assertEqual(saved['kind'],'image')
            with Image.open(saved['path']) as photo:self.assertEqual(photo.size,(1280,960))

    def test_publisher_24mp_jpeg_has_a_thumbnail_and_can_be_used_in_a_film(self):
        report={'title':'原文实拍','url':'https://www.news.cn/politics/20261108/'+'a'*32+'/c.html',
                'media_candidates':[{'kind':'image','url':'https://www.news.cn/photo.jpg'}]}
        def download(_url,path,_limit):
            Image.new('RGB',(5712,4284),'orange').save(path,format='JPEG')
        with scratch_dir() as root,patch('core.source_media._get_file',side_effect=download):
            candidate=source_media.candidates(report)[0]
            listed=source_media._with_thumbnails([candidate],root)[0]
            self.assertTrue(Path(listed.get('preview','')).is_file())
            saved=source_media.cache_candidate(report,candidate,root)
            self.assertEqual((saved['width'],saved['height']),(5712,4284))
            with Image.open(saved['path']) as photo:
                self.assertLessEqual(max(photo.size),1920)
            self.assertEqual(saved['rights'],'unverified')

    def test_news_video_is_bound_to_a_real_article_route_and_publisher_cdn(self):
        page = 'https://www.news.cn/politics/20261111/63a9df62556d4405a9c0f01ee6a88415/c.html'
        report = {'title': '雪景报道', 'url': page, 'media_candidates': [
            {'kind': 'video', 'url': 'https://vodpub6.v.news.cn/original/movie.mp4',
             'poster': 'https://vodpub6.v.news.cn/original/poster.jpg'},
            {'kind': 'video', 'url': 'https://vodpub6.v.news.cn.evil.test/movie.mp4'},
            {'kind': 'video', 'url': 'https://p6.itc.cn/movie.mp4'},
            {'kind': 'image', 'url': 'https://www.news.cn/20261111/photo.jpg'}]}
        assets = source_media.candidates(report)
        self.assertEqual([row['kind'] for row in assets], ['video', 'image'])
        self.assertEqual(assets[0]['thumbnail_url'], 'https://vodpub6.v.news.cn/original/poster.jpg')
        self.assertTrue(all(row['rights'] == 'unverified' for row in assets))
        for invalid in ('https://www.news.cn/', page.replace('www.news.cn','www.news.cn.evil.test'),
                        page.replace('www.news.cn','example.com')):
            self.assertEqual(source_media.candidates({**report, 'url': invalid}), [])

    def test_news_video_preview_fetches_only_the_poster_without_importing_video(self):
        from core.daily_news import story_key
        report = {'title': '雪景报道',
            'url': 'https://www.news.cn/politics/20261111/63a9df62556d4405a9c0f01ee6a88415/c.html',
            'media_candidates': [{'kind': 'video', 'url': 'https://vodpub6.v.news.cn/original/movie.mp4',
                                  'poster': 'https://vodpub6.v.news.cn/original/poster.jpg'}]}
        downloaded = []
        def download(url, target, _limit):
            downloaded.append(url)
            Image.new('RGB', (900, 1200), 'gray').save(target, format='JPEG')
        with scratch_dir() as root, patch('core.intake.read_intake', return_value={'articles': [report]}), \
                patch('core.source_media._get_file', side_effect=download):
            _, assets = source_media.source_options(root, story_key(report))
            self.assertTrue(Path(assets[0]['preview']).is_file())
            self.assertFalse((root/'material-library').exists())
            self.assertEqual(downloaded, ['https://vodpub6.v.news.cn/original/poster.jpg'])

    def test_xhs_webp_preview_is_local_jpeg_without_approving_or_saving_the_source(self):
        from core.daily_news import story_key
        note='6a0c1b78000000000702fe3a'
        report={'title':'番茄炒蛋','url':f'https://www.xiaohongshu.com/explore/{note}',
                'source_platform':'xiaohongshu','source_note_id':note,'content_type':'photo_note',
                'media_candidates':[{'kind':'image','url':'https://sns-webpic-qc.xhscdn.com/image/1040g3sample000000!webp'}]}
        def download(url,path,limit):
            Image.new('RGB',(1080,1440),'orange').save(path,format='WEBP')
        with scratch_dir() as root, patch('core.intake.read_intake',return_value={'articles':[report]}), \
                patch('core.source_media._get_file',side_effect=download) as get:
            _,options=source_media.source_options(root,story_key(report))
            with Image.open(options[0]['preview']) as photo:
                self.assertEqual(photo.format,'JPEG')
            self.assertFalse((root/'material-library').exists())
            self.assertEqual(options[0]['rights'],'unverified')
            source_media.source_options(root,story_key(report))
            self.assertEqual(get.call_count,1)

    def test_xhs_photo_ids_survive_access_parameter_rotation_and_keep_the_source_text(self):
        note='6a0c1b78000000000702fe3a'
        report={'title':'番茄炒蛋','url':f'https://www.xiaohongshu.com/explore/{note}?xsec_token=one',
                'source_platform':'xiaohongshu','source_note_id':note,'content_type':'photo_note',
                'source_type':'reference','summary':'鸡蛋先炒熟，再加入处理好的番茄。'*5,
                'media_candidates':[{'kind':'image','url':'https://sns-webpic-qc.xhscdn.com/sign-one/notes/1040g3sample000000!webp'},
                    {'kind':'image','url':'https://sns-webpic-qc.xhscdn.com.evil.test/notes/1040g3sample000001!webp'}]}
        first=source_media.candidates(report)
        self.assertEqual(len(first),1)
        self.assertEqual(first[0]['source_summary'],report['summary'])
        report['url']=report['url'].replace('one','two')
        report['media_candidates'][0]['url']=report['media_candidates'][0]['url'].replace('sign-one','sign-two')
        self.assertEqual(source_media.candidates(report)[0]['source_media_id'],first[0]['source_media_id'])
        from core.daily_news import story_key
        self.assertNotEqual(story_key(report),story_key({**report,'source_note_id':'6a058bfb000000003501faca'}))

    def test_original_player_video_is_allowed_but_lookalike_domains_are_not(self):
        report = {"title":"原文视频", "url":"https://www.sohu.com/a/434905007_99991059",
                  "media_candidates":[{"kind":"video", "url":"https://1009-39.vod.tv.itc.cn/sohu/v2/original.mp4"},
                    {"kind":"video", "url":"https://1009-39.vod.tv.itc.cn.evil.test/original.mp4"}]}
        found = source_media.candidates(report)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "video")
        self.assertEqual(found[0]["source_page"], report["url"])
        report['media_candidates'][0]['url'] += '?play_session=another-read'
        self.assertEqual(source_media.candidates(report)[0]['source_media_id'],found[0]['source_media_id'])
        report['media_candidates'].append({'kind':'video','url':report['media_candidates'][0]['url']+'&retry=1'})
        self.assertEqual(len(source_media.candidates(report)),1)

    def test_undecodable_player_initialization_file_is_not_saved_as_video(self):
        report = {"title":"原文视频", "url":"https://www.sohu.com/a/434905007_99991059",
                  "media_candidates":[{"kind":"video", "url":"https://1009-39.vod.tv.itc.cn/sohu/v2/init.mp4"}]}
        with scratch_dir() as root, patch('core.source_media._get_file',side_effect=lambda url,target,limit:target.write_bytes(b'not a movie')):
            with self.assertRaisesRegex(ValueError,'无法解码'):
                source_media.cache_candidate(report,source_media.candidates(report)[0],root)
            self.assertFalse(list(root.rglob('material.json')))
            self.assertFalse(list(root.rglob('.original.download')))

    def test_original_mp4_is_decoded_and_keeps_real_duration_and_dimensions(self):
        import shutil
        import subprocess
        from config import get_ffmpeg
        report = {"title":"原文视频", "url":"https://www.sohu.com/a/434905007_99991059",
                  "media_candidates":[{"kind":"video", "url":"https://1009-39.vod.tv.itc.cn/sohu/v2/original.mp4"}]}
        with scratch_dir() as root:
            clip = root / "fixture.mp4"
            subprocess.run([get_ffmpeg(), '-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=25',
                '-t','2','-c:v','libx264','-pix_fmt','yuv420p',str(clip)],check=True,capture_output=True)
            with patch('core.source_media._get_file',side_effect=lambda url,target,limit:shutil.copyfile(clip,target)):
                saved = source_media.cache_candidate(report,source_media.candidates(report)[0],root)
            self.assertEqual((saved['kind'],saved['width'],saved['height']),('video',640,360))
            self.assertAlmostEqual(saved['duration'],2,places=1)
            self.assertTrue(Path(saved['preview']).is_file())
            self.assertEqual(saved['origin_format'],'mp4')

    def setUp(self):
        self.report = {"title": "番茄炒蛋", "url": "https://www.sohu.com/a/434905007_99991059",
                       "source": "美食作家王刚", "media_candidates": [
            {"kind": "image", "url": "http://p6.itc.cn/images01/20201129/food.gif", "title": "切番茄",
             "context": "把番茄切成小颗粒", "width": 436, "height": 230},
            {"kind": "image", "url": "https://127.0.0.1/private.jpg"}]}

    def test_original_media_retains_context_and_rejects_arbitrary_downloads(self):
        found = source_media.candidates(self.report)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "animation")
        self.assertEqual(found[0]["context"], "把番茄切成小颗粒")
        self.assertTrue(found[0]["download_url"].startswith("https://p6.itc.cn/"))
        self.assertEqual(found[0]["source_page"], self.report["url"])
        self.assertEqual(found[0]["rights"], "unverified")

    def test_saved_original_image_preserves_source_and_is_reusable_in_assembly(self):
        from core.material_search import saved_materials
        self.report["media_candidates"][0]["url"] = "https://p6.itc.cn/images01/food.jpg"
        def download(url, path, limit):
            Image.new("RGB", (1280, 960), "orange").save(path, format="JPEG")
        with scratch_dir() as root, patch("core.source_media._get_file", side_effect=download):
            asset = source_media.cache_candidate(self.report, source_media.candidates(self.report)[0], root)
            self.assertEqual(asset["source_page"], self.report["url"])
            self.assertEqual(asset["width"], 1280)
            self.assertEqual(saved_materials(root)[0]["path"], asset["path"])

    def test_original_download_selection_must_exist_in_current_collection(self):
        with scratch_dir() as root, patch("core.intake.read_intake", return_value={"articles": [self.report]}), \
                patch("core.source_media.cache_candidate") as save:
            with self.assertRaisesRegex(ValueError, "已变化|不存在"):
                source_media.save_source_media(root, "missing", "a" * 20)
            save.assert_not_called()

    def test_workbench_lists_only_selected_article_media(self):
        from core.daily_news import story_key
        from core.wpf_workspace import workspace_stage
        with scratch_dir() as root, patch("core.intake.read_intake", return_value={"articles": [self.report]}):
            result = workspace_stage("workspace_source_media", None, root, None,
                                     {"report_id": story_key(self.report)})
            self.assertEqual(result["title"], self.report["title"])
            self.assertEqual(len(result["items"]), 1)
            self.assertEqual(result["items"][0]["source_report_id"], story_key(self.report))

    def test_saved_article_media_can_be_reused_after_collection_expiry(self):
        from core.daily_news import story_key
        self.report["media_candidates"][0]["url"] = "https://p6.itc.cn/images01/food.jpg"
        def download(url, path, limit):
            Image.new("RGB", (1280, 960), "orange").save(path, format="JPEG")
        with scratch_dir() as root, patch("core.source_media._get_file", side_effect=download) as get:
            saved = source_media.cache_candidate(self.report, source_media.candidates(self.report)[0], root)
            with patch("core.intake.read_intake", return_value={"articles": []}):
                again = source_media.save_source_media(root, story_key(self.report), saved["source_media_id"])
                self.assertEqual(again["path"], saved["path"])
                self.assertEqual(get.call_count, 1)
                with self.assertRaises(ValueError):
                    source_media.save_source_media(root, "other-report", saved["source_media_id"])

    def test_supplied_public_article_link_keeps_original_media_in_collection(self):
        import datetime as dt
        from core.editorial_link import read_editorial_link
        from core.intake import read_intake
        from core.daily_news import story_key
        article = {**self.report, "summary": "番茄和鸡蛋的操作说明。" * 40, "published": "2020-11-29 16:05"}
        with scratch_dir() as root, patch("core.browser_source.read_public_article", return_value=article):
            result = read_editorial_link(article["url"], dt.datetime.now().astimezone(), output_dir=root)
            self.assertEqual(result["media_candidates"], article["media_candidates"])
            stored = read_intake(root)["articles"]
            self.assertEqual(len(stored), 1)
            self.assertEqual(story_key(stored[0]), story_key(article))
            _, media = source_media.source_options(root, result["id"])
            self.assertEqual(len(media), 1)


if __name__ == "__main__":
    unittest.main()
