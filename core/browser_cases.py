"""Read same-topic public notes in the workbench's local browser session."""

import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _work(data, *, photo_notes):
    from core.copywriting import _work_page, _work_identity
    if not isinstance(data, dict) or data.get('type') not in ('normal', 'video'):
        return None
    if photo_notes and data['type'] != 'normal':
        return None
    title, excerpt, url = (data.get(key) for key in ('title', 'desc', 'url'))
    if (not isinstance(title, str) or not title.strip() or not isinstance(excerpt, str)
            or not 20 <= len(excerpt) <= 6000 or not isinstance(url, str) or len(url) > 2000):
        return None
    page = _work_page('xiaohongshu', url)
    if (not page or not re.fullmatch(r'[a-f0-9]{24}', str(data.get('id', '')))
            or _work_identity('xiaohongshu', page).rsplit('/', 1)[1] != data['id']):
        return None
    evidence = ['浏览器读取的公开图文笔记' if data['type'] == 'normal' else '浏览器读取的作品简介']
    published = ''
    if type(data.get('time')) in (int, float):
        try:
            stamp = dt.datetime.fromtimestamp(data['time']/1000, dt.timezone.utc)
            if 2013 <= stamp.year <= dt.datetime.now().year:
                published = stamp.isoformat()
                evidence.append('发布 ' + stamp.strftime('%Y-%m-%d'))
        except (ValueError, OSError, OverflowError):
            pass
    stats = data.get('stats') if isinstance(data.get('stats'), dict) else {}
    for key, label in (('likedCount', '点赞'), ('collectedCount', '收藏')):
        value = str(stats.get(key, ''))
        if re.fullmatch(r'\d+(?:\.\d+)?[万亿wWkK]?\+?', value):
            evidence.append('页面显示' + label + ' ' + value)
    evidence.append('采集 ' + dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M'))
    images = [row for row in data.get('images', [])[:20] if isinstance(row, dict)
              and isinstance(row.get('url'), str)] if isinstance(data.get('images'), list) else []
    author = str(data.get('author', ''))[:120]
    report = {'title': title.strip()[:120], 'url': url, 'summary': excerpt, 'published': published,
              'source': '小红书图文' if data['type'] == 'normal' else '小红书作品简介',
              'author': author, 'source_platform': 'xiaohongshu', 'source_note_id': data['id'],
              'content_type': 'photo_note' if data['type'] == 'normal' else 'video_note',
              'source_type': 'reference', 'headline_only': False,
              'media_candidates': [{**row, 'kind': 'image', 'title': f'原文配图 {index+1}'}
                                   for index,row in enumerate(images)]}
    return {'title': report['title'], 'url': url, 'excerpt': excerpt,
            'evidence': ' · '.join(evidence), 'media_candidates': images, 'source_report': report,
            **({'video_url':data.get('video_url','')} if data['type']=='video' else {})}


def store_browser_reference(output_dir, work, *, topic=None):
    from contextlib import closing
    from core.daily_news import story_key
    from core.intake import _connect, RETENTION_DAYS, has_report
    report = work.get('source_report')
    if not isinstance(report, dict) or not has_report(report):
        return
    report=dict(report)
    now = dt.datetime.now(dt.timezone.utc)
    with closing(_connect(output_dir)) as db, db:
        old=db.execute('SELECT story FROM articles WHERE id=?',(story_key(report),)).fetchone()
        topics=json.loads(old[0]).get('reference_topics',[]) if old else []
        topics=[v for v in topics if isinstance(v,str) and 1<=len(v)<=160] if isinstance(topics,list) else []
        if isinstance(topic,str) and 1<=len(topic.strip())<=160:
            topics.append(topic.strip())
        if topics:
            report['reference_topics']=list(dict.fromkeys(topics))[-16:]
        db.execute('DELETE FROM articles WHERE collected < ?', ((now-dt.timedelta(days=RETENTION_DAYS)).timestamp(),))
        db.execute('INSERT OR REPLACE INTO articles VALUES (?, ?, ?)',
                   (story_key(report), now.timestamp(), json.dumps(report, ensure_ascii=False)))
    return story_key(report)


def collect_browser_cases(output_dir, topic, *, limit=2, photo_notes=False, interactive=False):
    from config import BASE_DIR
    from core.workflow import _lock
    helper = (BASE_DIR if getattr(sys, 'frozen', False) else BASE_DIR / 'build') / 'post_panel' / 'CaseReader.exe'
    if not helper.is_file():
        return {'works': [], 'error': '缺少小红书参考采集组件，请更新完整工作台。'}
    if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 160 or type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('请填写选题并选择一到三篇参考。')
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    profile = root.parent / 'xhs-reference-browser'
    profile.mkdir(parents=True, exist_ok=True)
    with _lock(root.parent / 'xhs-reference-browser.sqlite3', timeout=650), tempfile.TemporaryDirectory(prefix='xhs-case-') as job:
        request, result = Path(job) / 'request.json', Path(job) / 'result.json'
        request.write_text(json.dumps({'topic': topic.strip(), 'limit': limit, 'photo_notes': photo_notes,
                                      'interactive': interactive}, ensure_ascii=False), encoding='utf-8')
        try:
            subprocess.run([str(helper), str(request), str(profile), str(result)],
                           timeout=620 if interactive else 100, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if not result.is_file() or result.stat().st_size > 150000:
                return {'works': [], 'error': '小红书参考读取未完成，可重试采集。'}
            data = json.loads(result.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {'works': [], 'error': '小红书参考读取未完成，可重试采集。'}
    if not isinstance(data, dict) or not isinstance(data.get('works'), list):
        return {'works': [], 'error': '小红书参考读取结果无效，可重试采集。'}
    rows = data['works']
    return {'works': [work for row in rows[:3] if (work := _work(row, photo_notes=photo_notes))],
            'requires_login': data.get('requires_login') is True, 'error': str(data.get('error', ''))[:180]}
