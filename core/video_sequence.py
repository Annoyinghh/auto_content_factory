"""Schedule verified source clips under the measured, continuous narration."""

import math
import re


def align_source_narration(scenes):
    """Make explicit sentence/clip bindings separate speech jobs before measuring audio."""
    output = []
    index = 0
    compact = lambda text: re.sub(r'\s+', '', text)
    while index < len(scenes):
        scene = scenes[index]
        clips = (scene.get('visual') or {}).get('clips') or []
        if not any('for_text' in clip for clip in clips):
            output.append(scene)
            index += 1
            continue
        if not all(isinstance(clip.get('for_text'), str) and compact(clip['for_text']) for clip in clips):
            raise ValueError('部分原片缺少对应口播，请重新逐句选镜。')
        group = scene.get('clip_group', index)
        parts = [scene['text']]
        end = index + 1
        while end < len(scenes) and scenes[end].get('clip_group', end) == group:
            if (scenes[end].get('visual') or {}).get('clips') != clips:
                raise ValueError('同段口播的原片对应已变化，请重新选镜。')
            parts.append(scenes[end]['text'])
            end += 1
        narration = compact(''.join(parts))
        bindings = {}
        for clip in clips:
            text = compact(clip['for_text'])
            if narration.count(text) != 1:
                raise ValueError('原片对应的口播已修改或重复，请重新逐句选镜。')
            bindings.setdefault(text, []).append(clip)
        texts = sorted(bindings, key=narration.index)
        if ''.join(texts) != narration:
            raise ValueError('有口播尚未配到对应画面，请补充原片、原图或通知截图后重新选镜。')
        for number, text in enumerate(texts):
            selected = bindings[text]
            first = selected[0]
            spoken = first['for_text'].strip()
            visual = {**scene['visual'], 'clips': selected, 'start': first['start'], 'end': first['end'],
                      'intent': first['intent'], 'reason': first['reason']}
            shot = {**scene, 'text': spoken, 'card_desc': spoken, 'asset': first['asset'], 'visual': visual,
                    'clip_group': ('bound', group, number), 'context_sequence': False}
            if number:
                shot['motion'] = 'cut'
                for field in ('documentary_title', 'documentary_number', 'chapter_handoff'):
                    shot.pop(field, None)
            output.append(shot)
        index = end
    return output


def enough_footage(span, duration):
    # Permit source-frame/container rounding, never seconds of held or repeated footage.
    return span + min(.1,span*.01) >= duration*(2/3)


def _continue_context(scenes):
    tracks=[]
    groups={}
    for index,scene in enumerate(scenes):
        group=scene.get('clip_group',index)
        clips=(scene.get('visual') or {}).get('clips')
        if not clips:
            # A graphic's render index can equal a later paragraph's clip group.
            tracks.append(None)
            continue
        if group in groups:
            continue
        previous=tracks[-1] if tracks else None
        contiguous=bool(clips and previous and previous['clips'] and scene.get('context_sequence')
                        and previous['context'] and scene.get('chapter_title') == previous['chapter']
                        and clips[0]['media'] == previous['clips'][-1]['media']
                        and math.isclose(clips[0]['start'],previous['clips'][-1]['end'],abs_tol=.001))
        if contiguous:
            previous['clips'].extend(clips)
            groups[group]=previous
        else:
            track={'group':group,'clips':list(clips or []),'context':scene.get('context_sequence',False),
                   'chapter':scene.get('chapter_title')}
            tracks.append(track);groups[group]=track
    return [{**scene,'clip_group':groups[scene.get('clip_group',i)]['group'],
             'visual':{**scene['visual'],'clips':groups[scene.get('clip_group',i)]['clips']}}
            if (scene.get('visual') or {}).get('clips') else scene for i,scene in enumerate(scenes)]


def expand_sequences(scenes, durations):
    scenes=_continue_context(scenes)
    totals = {}
    for index,(scene,duration) in enumerate(zip(scenes,durations,strict=True)):
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('口播时长无效。')
        if (scene.get('visual') or {}).get('clips'):
            group=scene.get('clip_group',index)
            totals[group]=totals.get(group,0)+duration
    output,timing,positions = [],[],{}
    for index,(scene,duration) in enumerate(zip(scenes,durations,strict=True)):
        clips=(scene.get('visual') or {}).get('clips')
        if not clips:
            output.append(scene);timing.append(duration)
            continue
        group=scene.get('clip_group',index)
        span=sum(clip['end']-clip['start'] for clip in clips)
        if not enough_footage(span,totals[group]):
            raise RuntimeError(f'“{scene.get("title", "当前分镜")}”的多镜头时长不足，请补充相关原片或缩短口播。')
        rate=min(1.,span/totals[group])
        cursor=positions.get(group,0.)
        limit=cursor+duration
        boundary=0.
        for clip in clips:
            seconds=(clip['end']-clip['start'])/rate
            left,right=max(cursor,boundary),min(limit,boundary+seconds)
            if right-left > 1e-7:
                visual={key:value for key,value in scene['visual'].items() if key != 'clips'}
                visual.update(start=clip['start']+(left-boundary)*rate,
                              end=min(clip['end'],clip['start']+(right-boundary)*rate),
                              intent=clip['intent'],reason=clip['reason'])
                shot={**scene,'asset':clip['asset'],'visual':visual,'clip_group':(group,len(output))}
                if left > 1e-7:
                    shot['motion']='cut'
                    for field in ('documentary_title','documentary_number','chapter_handoff'):
                        shot.pop(field,None)
                output.append(shot);timing.append(right-left)
            boundary+=seconds
            if boundary >= limit-1e-7:
                break
        positions[group]=limit
    return output,timing
