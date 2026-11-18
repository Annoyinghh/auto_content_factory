"""The workbench's source-led, non-publishing full-programme operation."""

import datetime as dt
import json
import re
from contextlib import closing
from pathlib import Path
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from core.daily_news import story_key
from core.intake import has_report, read_intake, _read_report, _search_reports, _connect
from core.source_media import candidates, cache_candidate


SUBJECTS = {
    'week_tech': ('本周科技', ('人工智能','AI','模型','芯片','半导体','机器人','航天','太空','卫星','量子','计算','科技','电池','通信','6G','5G')),
    'week_economy': ('本周经济', ('经济','金融','贸易','消费','投资','货币','就业','产业','出口','进口','财政',
                                  '资本市场','市场规模','股市','证券','债券','利率','汇率','央行','销量','零售','制造','能源','进博会')),
}

# Public genre examples, used only if live discovery has no readable work.
# Their descriptions are fetched and labelled at use time, never treated as transcripts.
REFERENCE_SEEDS = {
    'week_tech':'https://www.bilibili.com/video/BV1UJVM6BE7h',
    'week_economy':'https://www.bilibili.com/video/BV111BeBPEaD',
}


def _published(row):
    try:
        value=dt.datetime.fromisoformat(row.get('published','').replace('Z','+00:00'))
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    except (ValueError,TypeError):
        return None


def select_weekly_reports(rows, scope, now, *, limit=8):
    """Keep dated relevant developments, spread across dates rather than one feed burst."""
    words=SUBJECTS[scope][1]
    first_day=now.date()-dt.timedelta(days=6)
    recent=[row for row in rows if (stamp:=_published(row)) and first_day<=stamp.astimezone(now.tzinfo).date() and stamp<=now
            and any(word.casefold() in row.get('title','').casefold() for word in words)]
    recent.sort(key=lambda row:(bool(candidates(row)),len(row.get('summary',''))),reverse=True)
    chosen=[];days={};titles=set()
    while recent and len(chosen)<limit:
        row=min(recent,key=lambda r:days.get(_published(r).date(),0))
        recent.remove(row)
        identity=row['title'][:18]
        if identity in titles or any(SequenceMatcher(None,row.get('summary',''),old.get('summary','')).ratio()>.9
                                     for old in chosen):continue
        chosen.append(row);titles.add(identity)
        date=_published(row).date();days[date]=days.get(date,0)+1
    return sorted(chosen,key=lambda row:_published(row))


def collect_original_assets(reports, output_dir, progress=lambda _message:None):
    from core.official_capture import capture_official_notice

    assets=[];warnings=[];seen=set()
    for report in reports:
        progress('读取原文媒体 · '+report['title'])
        try:
            notice=capture_official_notice(report,output_dir)
        except (ValueError,OSError) as error:
            warnings.append(report['title']+' · '+str(error));notice=None
        found=[notice] if notice else []
        options=sorted(candidates(report),key=lambda asset:asset.get('kind')!='video')
        for option in options[:5]:
            try:found.append(cache_candidate(report,option,output_dir))
            except (ValueError,RuntimeError,OSError) as error:warnings.append(report['title']+' · '+str(error))
        if not found:
            try:
                page = capture_official_notice(report, output_dir, article_page=True)
                if page:
                    found.append(page)
            except (ValueError,OSError) as error:
                warnings.append(report['title']+' · '+str(error))
        for asset in found:
            if asset['page_url'] in seen:continue
            seen.add(asset['page_url'])
            assets.append({**asset,'source_id':report['id'],'verified_report_id':report['id'],
                           'source_publisher':report.get('source',''),
                           'automatic_review':'原文媒体已读取并解码；逐句选镜时继续核对画面。'})
    return assets,warnings


def _store_reports(output_dir, reports, now):
    with closing(_connect(output_dir)) as db,db:
        db.executemany('INSERT OR REPLACE INTO articles VALUES (?, ?, ?)',
            [(story_key(row),now.timestamp(),json.dumps(row,ensure_ascii=False)) for row in reports])


def _job_update(path, platform, identity, **changes):
    from core.workflow import _edit_edition
    def update(edition):
        item=edition['items'].get(platform,{})
        if item.get('id')!=identity:raise RuntimeError('选题已变化，已停止旧任务。')
        item.update(changes)
    return _edit_edition(path,update)['items'][platform]


def _reference_case(output_dir, platform, topic, facts, url='', *, weekly=False):
    import requests
    from core.copywriting import list_cases, retrieve_cases, _share_link, _work_page, _work_identity, _bilibili_work

    if url:
        page = _work_page(platform, _share_link(platform, url))
        identity = _work_identity(platform, page)
        cases=[case for case in list_cases(output_dir, platform)
               if (own := _work_page(platform, case['url'])) and _work_identity(platform, own) == identity]
    else:
        cases=retrieve_cases(output_dir, platform, topic, facts, actual_works=True)
    for case in cases:
        if weekly and platform=='bilibili':
            if re.search(r'未来一周|下周|财经日历|经济日历|经济事件预告',case['title']):continue
            try:
                if _bilibili_work(case['url'])['duration']<600:continue
            except (KeyError,ValueError,RuntimeError,requests.RequestException):continue
        return case
    return None


def validate_topic_sources(reports, scope, seconds):
    if not reports:
        raise RuntimeError('尚未取得可核对的完整原文，请补充原文或换一个资料更完整的热点。')
    if scope != 'topic' and len(reports) < 3:
        raise RuntimeError('本周可核对报道不足三篇，暂不能组成周报长片。')
    if len(reports) == 1:
        text = reports[0].get('summary', '')
        if seconds > 180 or len(text) < 300 or len(re.findall(r'[^。！？]+[。！？]', text)) < 3:
            raise RuntimeError('现有原文内容不足以支撑所选时长，请补充资料；不会重复事实凑长度。')
        return ['本专题依据一篇已读取的完整原文制作，未声称多源交叉验证；按有效信息收束，不强凑两分钟。']
    return []


def prepare_topic(day, output_dir, platform, payload, now):
    from core.editorial import source_options
    from core.copywriting import analyze_case, collect_topic_cases
    from core.workflow import edition_path, run_stage

    scope=payload.get('scope','topic')
    if scope not in {'topic',*SUBJECTS}:raise ValueError('请选择热点专题、本周科技或本周经济。')
    snapshot=read_intake(output_dir,now)
    anchor=source_options(snapshot,payload.get('anchor',{}),now,recommend=False)['anchor']
    if scope!='topic' and platform!='bilibili':raise ValueError('每周专题长片请选择 B站；其他平台请选择当前热点专题。')
    seconds=payload.get('target_seconds',720 if platform=='bilibili' else 120)
    if seconds != (720 if platform=='bilibili' else 120):raise ValueError('当前入口支持 B站十二分钟，其他平台两分钟。')
    topic=anchor['title']
    warnings=[]
    # This is the same live retrieval path used by manual topic collection.
    searched=_search_reports({'title':topic},now,reference=True,output_dir=output_dir)
    searched.extend(_search_reports({'title':topic,'search_query':topic+' site:gov.cn'},
                                   now,reference=True,output_dir=output_dir))
    _store_reports(output_dir,searched,now)
    snapshot=read_intake(output_dir,now)
    if scope=='topic':
        options=source_options(snapshot,anchor,now,recommend=True,local_rank=True)
        ids=set(options['recommended'])
        if anchor['kind']=='article':ids.add(anchor['id'])
        rows=[row for row in options['reports'] if row['id'] in ids][:8]
    else:
        rows=select_weekly_reports(snapshot['articles'],scope,now)
        matching=source_options(snapshot,anchor,now,recommend=False,local_rank=True)
        closest=next((row for key in matching['recommended'][:1] for row in matching['reports'] if row['id']==key),None)
        if closest and (stamp:=_published(closest)) and now.date()-dt.timedelta(days=6)<=stamp.astimezone(now.tzinfo).date() and stamp<=now:
            rows=list({row.get('id') or story_key(row):row for row in [closest,*rows]}.values())[:8]
        topic=SUBJECTS[scope][0]+' · '+(now-dt.timedelta(days=6)).strftime('%m.%d')+'—'+now.strftime('%m.%d')
    reports=[]
    for row in rows:
        refresh={**row,'content_origin':'feed'} if urlsplit(row['url']).hostname=='www.ithome.com' and not candidates(row) else row
        original=_read_report(refresh,now,allow_older=True)
        if has_report(original) and original.get('content_origin')!='feed':
            stamp=_published(original)
            if scope!='topic' and (not stamp or stamp.astimezone(now.tzinfo).date()<now.date()-dt.timedelta(days=6) or stamp>now):continue
            reports.append({**original,'id':story_key(original),
                            'source_type':'background' if stamp and stamp<now-dt.timedelta(days=7) else 'report'})
    from core.news_related import discover_related_reports
    related=discover_related_reports(reports,anchor['title'],now)
    if related:
        seed=next(row for row in reports if row['url']==related[0]['related_seed_url'])
        supplements=[{**row,'id':story_key(row),'source_type':'report'} for row in related[:4]]
        preferred=[seed,*supplements,*reports]
        reports=list({row['id']:row for row in preferred}.values())[:8]
    warnings.extend(validate_topic_sources(reports, scope, seconds))
    _store_reports(output_dir,reports,now)
    reference=None
    if text:=(payload.get('reference_text') or '').strip():
        reference={'title':'用户提供的参考稿','url':payload.get('reference_url','').strip(),
                   'evidence':'用户提供的参考文字，只提炼写法。',**analyze_case(platform,text)}
        reference['speech_profile'] = {'basis':'user_script', 'timing_verified':False,
                                       'methods':[reference['notes']]}
    else:
        reference_topic={'week_tech':'科技 周报','week_economy':'经济 周报'}.get(scope,anchor['title'])
        try:
            collect_topic_cases(output_dir,platform,{'topic':anchor['title'],'search_query':reference_topic,
                                'url':payload.get('reference_url','').strip()},limit=2)
        except (RuntimeError,ValueError,OSError) as error:warnings.append('参考作品读取：'+str(error))
        selected=_reference_case(output_dir,platform,anchor['title']+' '+reference_topic,' '.join(r['title'] for r in reports),
                                 payload.get('reference_url','').strip(),
                                 weekly=scope in SUBJECTS and not payload.get('reference_url','').strip())
        if not selected and scope in REFERENCE_SEEDS and not payload.get('reference_url','').strip():
            try:
                link=REFERENCE_SEEDS[scope]
                collect_topic_cases(output_dir,platform,{'topic':reference_topic,'url':link},limit=1)
                selected=_reference_case(output_dir,platform,reference_topic,'',link,weekly=True)
                if selected:warnings.append('实时参考搜索未取得可用作品，本次采用内置的同类长片参考；不是本周热榜作品。')
            except (RuntimeError,ValueError,OSError) as error:warnings.append('备用参考读取：'+str(error))
        if selected:reference=dict(selected)
    from core.spoken_style import resolve_reference
    reference = resolve_reference(output_dir, platform, anchor['title'], ' '.join(r['title'] for r in reports), explicit=reference)
    if not reference:warnings.append('未取得相关口播转写，采用本平台表达规则；图文或视频简介不冒充口播参考。')
    brief={'notes':['把本周发展组织成几个有证据的小专题；同一事件合并讲，独立事件自然转场，不强编共同原因，也不逐条念新闻标题。'
                    if scope!='topic' else '围绕当前热点解释发生了什么、此前背景和已知影响。',
                    '按已读参考提炼表达结构：'+reference['notes'] if reference else '语言自然具体，避免空泛总结。'],
           'asset_paths':[],'sequence':[],'focus_id':reports[0]['id']}
    job_anchor={'kind':'custom','title':topic,'url':''} if scope!='topic' else anchor
    item=run_stage('editorial_create',day,output_dir,platform,{'anchor':job_anchor,
        'source_ids':[r['id'] for r in reports],'target_seconds':seconds,
        'video_style':'roundup' if platform in {'bilibili','douyin'} else '', 'brief':brief},now=now)
    path=edition_path(output_dir,day)
    editorial={**item['editorial'],'production_mode':'weekly' if scope!='topic' else 'feature',
               'period':{'start':(now-dt.timedelta(days=6)).date().isoformat(),'end':now.date().isoformat()},
               'style_reference':reference}
    job={'scope':scope,'seed':anchor,'publish':False,'stage':'sources','reference':reference,'warnings':warnings}
    _job_update(path,platform,item['id'],editorial=editorial,topic_job=job,
                editorial_progress='原文已确认，准备读取原片、原图和官方通知')
    return {'id':item['id'],'day':day.isoformat(),'platform':platform,'topic':topic}


def produce_topic(day, output_dir, platform, payload, now):
    from core.workflow import edition_path, read_edition, run_stage, _lock

    path=edition_path(output_dir,day)
    with _lock(path.parent/f'{platform}-topic-production.sqlite3'):
        item=read_edition(path)['items'].get(platform,{})
        if item.get('id')!=payload.get('id') or not item.get('topic_job'):
            raise ValueError('请从当前热点的「制作整片」入口启动。')
        job=dict(item['topic_job']);identity=item['id']
        def progress(message):_job_update(path,platform,identity,editorial_progress=message)
        try:
            if job.get('stage')=='sources':
                assets,warnings=collect_original_assets(item['editorial']['reports'],output_dir,progress)
                if not assets:raise RuntimeError('没有取得与原文对应的可用视频、图片或通知截图；本次没有用通用素材替代。')
                decisions=dict(item.get('media_review',{}))
                for asset in assets:
                    decisions.setdefault(asset['page_url'],'approved')
                job.update(stage='production',warnings=[*job.get('warnings',[]),*warnings])
                _job_update(path,platform,identity,media=assets,media_review=decisions,topic_job=job,
                            media_checked=True,opening_media_checked=True)
            current=read_edition(path)['items'].get(platform,{})
            if current.get('id')!=identity:raise RuntimeError('选题已变化，已停止旧任务。')
            result=run_stage('auto_cut',day,output_dir,platform,{'id':identity,'export':True,
                'revision':current.get('edit_revision'),
                'suggestion_revision':current.get('suggestion_revision')},now=now)
            current=read_edition(path)['items'].get(platform,{})
            audit=current.get('production_audit') or {}
            if audit.get('ready') is not True:
                raise RuntimeError(audit.get('message') or '成片尚未通过检查，已保留当前制作进度。')
            job.pop('error',None)
            job.update(stage='complete',completed_at=now.isoformat())
            _job_update(path,platform,identity,topic_job=job,editorial_progress='整片已生成，尚未发布')
            return result
        except Exception as error:
            job['error']=str(error)
            _job_update(path,platform,identity,topic_job=job,editorial_progress=str(error))
            raise


def topic_stage(stage, day, output_dir, platform, payload, *, now=None):
    from config import PROFILES
    if platform not in PROFILES or not isinstance(payload,dict):raise ValueError('请选择制作平台和热点。')
    now=now or dt.datetime.now().astimezone()
    day=dt.date.fromisoformat(str(day))
    return (prepare_topic if stage=='topic_prepare' else produce_topic)(day,Path(output_dir),platform,payload,now)
