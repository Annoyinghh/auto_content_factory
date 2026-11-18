import datetime as dt
import socket
import unittest
from unittest.mock import patch

from requests import Response

from core.daily_news import parse_feed, story_key
from core.editorial import confirm_sources, source_options
from core.intake import _read_report, has_report
from test_daily import scratch_dir


NOW = dt.datetime(2026, 11, 11, 10, tzinfo=dt.timezone.utc)
URL = 'https://www.ithome.com/1/025/569.htm'
OPENING = '这篇报道核对创作者提交的材料，并逐项说明内容发布的具体流程和所需记录。' * 12
FOOTER = '其他文章和下载推广属于站点页脚，与所选新闻的正文没有关联。' * 15


def response(html):
    result = Response()
    result.status_code = 200
    result.headers['Content-Type'] = 'text/html; charset=utf-8'
    result.encoding = 'utf-8'
    result._content = html.encode('utf-8')
    result._content_consumed = True
    return result


def feed_story():
    return {'title': '创作者资料更新', 'url': URL, 'source': 'IT之家',
            'published': NOW.isoformat(), 'summary': OPENING[:350], 'content_origin': 'feed'}


def article_html():
    # Real publisher container; a short qualification at the end must not disappear.
    return ('<html><title>创作者资料更新</title><p>' + FOOTER + '</p>'
            '<div id="paragraph" class="post_content "><p>' + OPENING + '</p>'
            '<p>审核通过后仍需核对使用范围。<div class="adEditor">责任编辑</div></div>'
            '<p>' + FOOTER + '</p></html>')


class ArticleInputTests(unittest.TestCase):
    def fetch(self, story, html):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch('socket.getaddrinfo', return_value=addresses), patch('requests.get', return_value=response(html)):
            return _read_report(story, NOW)

    def test_rss_excerpt_is_discovery_metadata_even_when_it_looks_long(self):
        xml = ('<rss><channel><title>IT之家</title><item><title>创作者资料更新</title>'
               '<link>' + URL + '</link><pubDate>Wed, 11 Nov 2026 09:00:00 GMT</pubDate>'
               '<description>' + OPENING * 3 + '</description></item></channel></rss>')
        report = parse_feed(xml, NOW)[0]
        self.assertFalse(has_report(report), 'RSS length does not prove the original article was read')
        self.assertEqual(report.get('content_origin'), 'feed')

    def test_article_body_preserves_short_final_fact_and_excludes_footer(self):
        report = self.fetch(feed_story(), article_html())
        self.assertEqual(report['summary'], OPENING + '\n审核通过后仍需核对使用范围。')
        self.assertEqual(report.get('content_origin'), 'article')
        self.assertTrue(has_report(report))

    def test_known_publisher_missing_body_does_not_promote_sidebar_to_article(self):
        story = feed_story()
        report = self.fetch(story, '<html><p>' + FOOTER + '</p></html>')
        self.assertFalse(has_report(report))
        self.assertEqual(report['summary'], story['summary'])

    def test_selected_feed_candidate_resolves_to_actual_body_before_confirmation(self):
        story = feed_story()
        key = story_key(story)
        anchor = {'kind': 'article', 'id': key}
        snapshot = {'articles': [story], 'ranks': {}}
        candidate = source_options(snapshot, anchor, NOW, recommend=False)['reports'][0]
        self.assertTrue(candidate['available'], 'A feed candidate should still be selectable')
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch('socket.getaddrinfo', return_value=addresses), patch('requests.get', return_value=response(article_html())):
            result = confirm_sources(snapshot, {'anchor': anchor, 'source_ids': [key], 'brief': {}}, NOW)
        self.assertEqual(result['reports'][0]['summary'], OPENING + '\n审核通过后仍需核对使用范围。')

    def test_original_unavailable_does_not_fall_back_to_feed_as_writing_evidence(self):
        story = feed_story()
        key = story_key(story)
        with patch('socket.getaddrinfo', side_effect=OSError('offline')):
            with self.assertRaisesRegex(ValueError, '原文'):
                confirm_sources({'articles': [story], 'ranks': {}},
                                {'anchor': {'kind': 'article', 'id': key}, 'source_ids': [key], 'brief': {}}, NOW)

    def test_hotspot_copywriting_also_resolves_its_matched_feed_before_writing(self):
        from core.copywriting import _hotspot_material
        story = feed_story()
        snapshot = {'articles': [story], 'ranks': {'bilibili': {'published': NOW.timestamp(),
                    'entries': [{'title': story['title'], 'url': 'https://www.bilibili.com/video/BV1234567890'}]}}}
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        with patch('socket.getaddrinfo', return_value=addresses), patch('requests.get', return_value=response(article_html())):
            _, report = _hotspot_material('.', 'bilibili', story['title'], now=NOW, snapshot=snapshot)
        self.assertIsNotNone(report)
        self.assertIn('审核通过后仍需核对使用范围。', report['summary'])

    def test_refresh_marks_identical_legacy_rss_without_overwriting_read_originals(self):
        import json
        from contextlib import closing
        from core.intake import _connect, read_intake, refresh_intake
        legacy = {key: value for key, value in feed_story().items() if key != 'content_origin'}
        original = {**feed_story(), 'title': '已经读过的原文', 'content_origin': 'article',
                    'summary': OPENING + '\n完整原文中的关键补充。'}
        with scratch_dir() as root:
            with closing(_connect(root)) as db, db:
                db.executemany('INSERT INTO articles VALUES (?, ?, ?)', [
                    (story_key(row), NOW.timestamp()-100, json.dumps(row, ensure_ascii=False))
                    for row in (legacy, original)])
            with patch('core.intake.collect_news', return_value=([feed_story(),
                       {**original, 'content_origin': 'feed', 'summary': original['summary'][:350]}], [])), \
                    patch('core.intake.fetch_rank', return_value={'published': NOW.timestamp(), 'entries': []}):
                refresh_intake(root, NOW)
            rows = {row['title']: row for row in read_intake(root, NOW)['articles']}
            self.assertEqual(rows[legacy['title']].get('content_origin'), 'feed')
            self.assertFalse(has_report(rows[legacy['title']]))
            self.assertEqual(rows[original['title']]['summary'], original['summary'])
            self.assertEqual(rows[original['title']]['content_origin'], 'article')

    def test_chinanews_container_keeps_report_headings_and_final_paragraph(self):
        story = {**feed_story(), 'url': 'https://www.chinanews.com.cn/fz/2026/11-11/10730253.shtml'}
        html = ('<p>' + FOOTER + '</p><div class="left_zw"><p>调查经过</p><p>' + OPENING + '</p>'
                '<p>应保留原始材料。<div class="adEditor">编辑姓名</div></div><p>' + FOOTER + '</p>')
        report = self.fetch(story, html)
        self.assertEqual(report['summary'], '调查经过\n' + OPENING + '\n应保留原始材料。')

    def test_short_original_video_article_keeps_its_body_media_and_actual_publication(self):
        url = 'https://www.news.cn/politics/20261111/63a9df62556d4405a9c0f01ee6a88415/c.html'
        video = 'https://vodpub6.v.news.cn/yqfbzx-original/20261111/original.mp4'
        poster = 'https://vodpub6.v.news.cn/yqfbzx-original/20261111/image/poster.jpg'
        text = '立冬以后，高海拔地区出现降雪，记者记录了山林与村落的雪景。' * 3
        html = ('<meta name="publishdate" content="2026-11-11"><meta name="source" content="新华社">'
                '<span class="time">10:59:24</span><h1><span class="title">雪景报道</span></h1>'
                '<div id="detail"><span id="detailContent"><p><span class="pageVideo" video_src="' + video +
                '" poster="' + poster + '" video_width="1080" video_height="1440"></span></p>'
                '<p>' + text + '</p><p>摄制：记者甲</p></span>'
                '<div id="articleEdit">其他编辑文字</div></div>'
                '<span class="pageVideo" video_src="https://vodpub6.v.news.cn/unrelated.mp4"></span>')
        report = self.fetch({'url': url, 'title': '', 'source': '', 'published': '', 'headline_only': True}, html)
        self.assertTrue(has_report(report))
        self.assertEqual(report['summary'], text + '\n摄制：记者甲')
        self.assertEqual(report['title'], '雪景报道')
        self.assertEqual(report['source'], '新华社')
        self.assertEqual(dt.datetime.fromisoformat(report['published']).astimezone(dt.timezone.utc).isoformat(),
                         '2026-11-11T02:59:24+00:00')
        from core.source_media import candidates
        assets = candidates(report)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['download_url'], video)
        self.assertEqual(assets[0]['context'], text)
        self.assertEqual(assets[0]['thumbnail_url'], poster)

    def test_short_article_with_untrusted_video_url_is_not_promoted_to_readable_report(self):
        story = {'url': 'https://www.news.cn/politics/20261111/63a9df62556d4405a9c0f01ee6a88415/c.html',
                 'title': '雪景报道', 'source': '新华社', 'published': NOW.isoformat(), 'headline_only': True}
        text = '报道说明已观察到的天气现象，并保留原始摄制资料。' * 3
        for link in ('https://vodpub6.v.news.cn.evil.test/movie.mp4', 'https://127.0.0.1/movie.mp4'):
            html = '<div id="detail"><p>' + text + '</p><span class="pageVideo" video_src="' + link + '"></span></div>'
            self.assertFalse(has_report(self.fetch(story, html)))


if __name__ == '__main__':
    unittest.main()
