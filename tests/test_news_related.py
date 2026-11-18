import datetime as dt
import unittest
from unittest.mock import patch

from core import news_related


NOW = dt.datetime.fromisoformat('2026-11-16T23:00:00+08:00')
ANCHOR = '10月国民经济运行总体平稳向新向优'
INDEX = 'https://www.news.cn/fortune/index.htm'


def article(number, *, month=10, year=2026, day='20261116', body=None, media='clip'):
    page = f'https://www.news.cn/fortune/{day}/{number:032x}/c.html'
    summary = body or (f'11月16日，国务院新闻办公室举行新闻发布会，介绍{year}年{month}月份国民经济运行情况。'
                       '国家统计局新闻发言人介绍了服务零售、市场需求和产业发展情况，并回答记者提问。')
    return {'title': '市场需求向新拓展', 'url': page, 'summary': summary,
            'published': f'{day[:4]}-{day[4:6]}-{day[6:]}T12:00:00+08:00',
            'headline_only': False, 'content_origin': 'article', 'content_type': 'video_article',
            'media_candidates': [{'kind': 'video', 'url': f'https://vodpub6.v.news.cn/original/{media}.mp4'}]}


def links(*reports):
    return ''.join(f'<a href="{r["url"]}">{r["title"]}</a>' for r in reports)


class NewsRelatedTests(unittest.TestCase):
    def setUp(self):
        self.seed = article(1)
        self.seed['title'] = ANCHOR
        self.seed['media_candidates'] = []

    def discover(self, pages, reports, **kwargs):
        with patch.object(news_related, '_fetch_html', side_effect=lambda u: pages.get(u, '')), \
                patch.object(news_related, '_read_report', side_effect=lambda r, *_a, **_k: reports.get(r['url'], r)):
            return news_related.discover_related_reports([self.seed], ANCHOR, NOW, **kwargs)

    def test_one_hop_uses_full_body_when_title_does_not_name_anchor(self):
        related = article(2)
        result = self.discover({self.seed['url']: links(related)}, {related['url']: related})
        self.assertEqual([r['url'] for r in result], [related['url']])
        self.assertEqual(result[0]['related_seed_url'], self.seed['url'])
        self.assertEqual(result[0]['discovery_url'], self.seed['url'])
        self.assertEqual(result[0]['summary'], related['summary'])

    def test_index_fallback_requires_same_release_event_not_generic_economic_words(self):
        good = article(2, body='11月16日，国家统计局国民经济综合统计司负责人在国务院新闻办新闻发布会上介绍，今年前10个月网上服务零售额增长。消费市场需求逐步释放，服务业平稳增长。')
        wrong_month, old_year = article(3, month=9), article(4, year=2025)
        unrelated = article(5, body='11月16日，记者探访当地工厂，发现今年前10个月工业生产稳步增长。国家统计局经济数据公布后，企业负责人介绍了消费市场需求和新产品研发计划。')
        wrong_stamp = article(6)
        wrong_stamp['published'] = '2026-11-15T12:00:00+08:00'
        historical = article(7, body='资料回顾：去年10月份国民经济运行呈现新的特点。国家统计局曾介绍服务零售和消费市场变化，相关研究说明了当时产业发展的基本情况。历史数据仅作为今年分析的参考。')
        rows = [good, wrong_month, old_year, unrelated, wrong_stamp, historical]
        for index, row in enumerate(rows):
            row['media_candidates'][0]['url'] = f'https://vodpub6.v.news.cn/original/{index}.mp4'
        result = self.discover({INDEX: links(*rows)}, {r['url']: r for r in rows})
        self.assertEqual([r['url'] for r in result], [good['url']])
        self.assertEqual(result[0]['discovery_url'], INDEX)

    def test_old_or_unrelated_seed_does_not_open_category(self):
        for changes in ({'published': '2026-11-08T12:00:00+08:00'},
                        {'summary': '产业经济发展前景广阔。' * 30},
                        {'url': self.seed['url'].replace('www.news.cn', 'example.com')}):
            with self.subTest(changes=changes), patch.object(news_related, '_fetch_html') as fetch:
                self.assertEqual(news_related.discover_related_reports([{**self.seed, **changes}], ANCHOR, NOW), [])
                fetch.assert_not_called()

    def test_only_current_official_article_links_are_read_and_budget_is_bounded(self):
        rows = [article(n, media=str(n)) for n in range(2, 20)]
        invalid = [article(20, day='20251116'), article(21, day='20261117')]
        evil = article(22)
        evil['url'] = evil['url'].replace('www.news.cn', 'www.news.cn.evil.test')
        with patch.object(news_related, '_fetch_html', return_value=links(*invalid, evil, *rows)), \
                patch.object(news_related, '_read_report', side_effect=lambda r, *_a, **_k: next(x for x in rows if x['url'] == r['url'])) as read:
            result = news_related.discover_related_reports([self.seed], ANCHOR, NOW, limit=100)
        self.assertEqual(len(result), 8)
        self.assertEqual(read.call_count, 8)

    def test_aliases_and_rotated_queries_do_not_repeat_original_video(self):
        a, b, c = article(2), article(3), article(4)
        base = 'https://vodpub6.v.news.cn/yqfbzx-original/20261116/'
        a['media_candidates'][0]['url'] = base + '20261116' + 'a'*32 + '_XxjfceC000049_20261116_CBVFN0A001.mp4?session=one'
        b['media_candidates'][0]['url'] = base + '20261116' + 'b'*32 + '_XxjfceC000049_20261116_CBVFN0A001.mp4'
        c['media_candidates'][0]['url'] = a['media_candidates'][0]['url'].replace('one', 'two')
        result = self.discover({self.seed['url']: links(a, b, c)}, {r['url']: r for r in (a, b, c)})
        self.assertEqual([r['url'] for r in result], [a['url']])

    def test_missing_video_and_failed_discovery_are_skipped(self):
        row = article(2)
        row['media_candidates'] = []
        self.assertEqual(self.discover({self.seed['url']: links(row)}, {row['url']: row}), [])
        with patch.object(news_related, '_fetch_html', side_effect=ValueError('unavailable')):
            self.assertEqual(news_related.discover_related_reports([self.seed], ANCHOR, NOW), [])


if __name__ == '__main__':
    unittest.main()
