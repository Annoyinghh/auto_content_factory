"""Time-bounded observations of selected original videos, independent of narration."""

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from config import get_ffmpeg


def segment_bounds(cuts, duration):
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 7200:
        raise ValueError('原片时长无效，无法建立镜头索引。')
    boundaries = [0.]
    for cut in sorted({float(x) for x in cuts if type(x) in (int, float) and math.isfinite(x) and 0 < x < duration}):
        if cut-boundaries[-1] >= 2 and duration-cut >= 1:
            boundaries.append(cut)
    boundaries.append(float(duration))
    spans = []
    for start, end in zip(boundaries, boundaries[1:]):
        count = math.ceil((end-start)/12)
        points = [round(start+(end-start)*i/count, 6) for i in range(count)] + [end]
        spans.extend({'start': a, 'end': b} for a,b in zip(points,points[1:]))
    return spans


def detect_segments(asset):
    result = subprocess.run([get_ffmpeg(), '-nostdin', '-hide_banner', '-loglevel', 'info',
        '-protocol_whitelist', 'file', '-i', str(asset['path']), '-an',
        '-vf', r'scale=320:-2,select=gt(scene\,0.32),showinfo', '-f', 'null', '-'],
        capture_output=True, timeout=180, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('原片场景切分失败，请先核对视频是否可以播放。')
    cuts = [float(x) for x in re.findall(r'pts_time:\s*([0-9]+(?:\.[0-9]+)?)',result.stderr.decode('utf-8','replace'))]
    return segment_bounds(cuts, asset['duration'])


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return None


def writing_catalog(media, indices, query='', *, limit=3):
    """Bounded source locators for writing; observations and ASR stay separate."""
    from core.grounded_news import _terms

    assets={asset['page_url']:asset for asset in media}
    terms=_terms(query)
    pools=[]
    for index in indices:
        asset=assets.get(index.get('media'))
        if not asset or asset.get('kind')!='video':
            continue
        rows=[]
        for row in index.get('segments',[]):
            start,end=row.get('start'),row.get('end')
            visible=row.get('observations',[])
            if (not isinstance(row.get('id'),str) or not row['id']
                    or any(type(t) not in (int,float) or not math.isfinite(t) for t in (start,end))
                    or not 0<=start<end<=asset.get('duration',0) or end-start>12.000001
                    or not isinstance(visible,list) or not visible or any(not isinstance(s,str) for s in visible)):
                continue
            speech=[part['text'] for part in row.get('speech',[]) if isinstance(part,dict)
                    and isinstance(part.get('text'),str)]
            score=lambda text:len(terms&_terms(text))
            observations=list(dict.fromkeys(text[:80] for text in visible))[:3]
            recognized=[max(speech,key=score)[:80]] if speech else []
            record={'kind':'video','clip_id':row['id'],'media':asset['page_url'],
                    'source_id':asset.get('verified_report_id') or asset.get('source_report_id',''),
                    'start':start,'end':end,'observations':observations,'recognized_speech':recognized}
            rows.append((score(' '.join([*visible,*speech])),record))
        rows.sort(key=lambda pair:pair[0],reverse=True)
        if rows:
            pools.append(rows)
    pools.sort(key=lambda rows:rows[0][0],reverse=True)
    selected=[]
    while pools and len(selected)<limit:
        for rows in pools:
            selected.append(rows.pop(0)[1])
            if len(selected)==limit:
                break
        pools=[rows for rows in pools if rows]
    photos=[{'kind':'image','media':asset['page_url'],
             'source_id':asset.get('verified_report_id') or asset.get('source_report_id',''),
             'publisher_caption':str(asset.get('context') or asset.get('title',''))[:120]}
            for asset in media if asset.get('kind')=='image']
    photos.sort(key=lambda row:len(terms&_terms(row['publisher_caption'])),reverse=True)
    return [*selected,*photos[:2]]


def index_video(asset, directory, model, progress=lambda _message: None, *, with_speech=False):
    from core.directing import _frame_jpeg, observe_sequence
    from core.local_ai import LocalAIUnavailable
    from core.workflow import _lock, write_json

    source = Path(asset['path']).resolve()
    if asset.get('kind') != 'video' or not source.is_file():
        raise ValueError('只能识别当前已选、可读取的视频素材。')
    segment_bounds([], asset['duration'])
    stat = source.stat()
    identity = hashlib.sha256(json.dumps(['scene-observations-v1',str(source),stat.st_size,stat.st_mtime_ns,
        asset['duration'],model],ensure_ascii=False).encode()).hexdigest()[:24]
    folder = Path(directory)/identity
    folder.mkdir(parents=True, exist_ok=True)
    with _lock(folder/'writes.sqlite3',timeout=5400):
        spans = _read(folder/'bounds.json')
        valid = (isinstance(spans,list) and spans and all(isinstance(row,dict)
                 and type(row.get('start')) in (int,float) and type(row.get('end')) in (int,float)
                 and 0 <= row['start'] < row['end'] <= asset['duration'] and row['end']-row['start'] <= 12.000001
                 for row in spans))
        if not valid:
            progress('检测原片的场景切换')
            spans = detect_segments(asset)
            write_json(folder/'bounds.json',spans)
        segments, errors, unavailable = [], [], None
        for index, bounds in enumerate(spans):
            key = hashlib.sha256((identity+json.dumps(bounds,sort_keys=True)).encode()).hexdigest()[:20]
            cache = folder/(key+'.json')
            saved = _read(cache)
            if (isinstance(saved,dict) and saved.get('id') == key and saved.get('start') == bounds['start']
                    and saved.get('end') == bounds['end'] and isinstance(saved.get('observations'),list)
                    and len(saved['observations']) == 3
                    and all(isinstance(x,str) and 2<=len(x.strip())<=180 for x in saved['observations'])):
                segments.append(saved)
                continue
            progress(f'识别原片镜头 {index+1}/{len(spans)} · {asset.get("source_title") or asset.get("title", "视频")}')
            start,end = bounds['start'],bounds['end']
            # Container audio can outlast its last video frame; sample within the scene.
            offsets = tuple(start+(end-start)*fraction for fraction in (.15,.5,.85))
            try:
                frames = tuple(_frame_jpeg(asset,offset) for offset in offsets)
                observations = observe_sequence(frames,model)
                row = {'id':key, **bounds, 'sample_times':list(offsets), 'observations':observations}
                write_json(cache,row)
                segments.append(row)
            except LocalAIUnavailable as error:
                errors.append({**bounds,'error':str(error)[:240]})
                unavailable=error
                break
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
                errors.append({**bounds,'error':str(error)[:240]})
        if with_speech and not unavailable:
            from core.video_timeline import original_speech
            speech=original_speech(asset,directory,progress)
            # Keep ASR out of the blind frame cache and physical-action observations.
            segments=[{**row,'speech':[part for part in speech
                       if part['start'] < row['end'] and part['end'] > row['start']]} for row in segments]
        result = {'identity':identity,'model':model,'media':asset['page_url'],
                  'source_report_id':asset.get('source_report_id'),'source_page':asset.get('source_page'),
                  'source_published':asset.get('source_published'),'segments':segments,'errors':errors,
                  'complete':not errors and len(segments)==len(spans)}
        write_json(folder/'index.json',result)
        if unavailable:
            raise LocalAIUnavailable(f'{unavailable} 已保留 {len(segments)} 个已识别镜头，下次从未完成处继续。') from unavailable
        return result
