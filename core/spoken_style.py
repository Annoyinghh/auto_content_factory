"""Evidence-qualified speaking references and platform adaptations for video jobs."""
import copy
import re

VERSION = 'spoken-style-v1'

# Authored production rules, distinct from observations about a reference creator.
PLATFORMS = {
    'xiaohongshu': {
        'name': '小红书 · 一次讲明白',
        'opening': '从观众一个具体的不明白之处切入，立即点出本期要解释的对象。',
        'sentence': '一个句子解释一个概念；尽量用短的主谓句。保留必要的专名与限定，不能为亲切而捏造个人经历。',
        'body': '先说是什么，再说这次发生什么变化；每个术语首次出现时接一句有来源的解释。',
        'closing': '回到开头的问题，用一个已经讲清楚的结论结束，不强加购物建议、关注口号或营销话术。',
        'animation': '概念出现时才显出对应物件或标签；说关系时连接或并列；实拍承担场景，图解承担关系。',
    },
    'douyin': {
        'name': '抖音 · 变化与证据',
        'opening': '先说这条新闻最值得知道的一项具体变化，再用一个短问题带出证据。不要故弄玄虚。',
        'sentence': '短句推进，一句一个信息点。关键数字后给听众停顿，不把三个测试条件塞进一句话。',
        'body': '结果、依据、限制依次展开；删除重复背景，每转一次意思才换镜头。',
        'closing': '用已知结果和一个仍待观察的问题收束，不套点赞关注模板。',
        'animation': '先给结果物件，再揭示证据；重点词入场与口播同步。变化用前后布局表达，避免每句话同一种转场。',
    },
    'bilibili': {
        'name': 'B站 · 问题驱动的专题',
        'opening': '提出本期能回答的具体问题，先交代为什么需要这段背景，再进入第一条证据。',
        'sentence': '完整但不堆叠的口语句；解释术语后再推进，不使用短视频式连续喊话。',
        'body': '问题、必要背景、证据、解释、局限分层推进。周报按事件合章，用上一章留下的问题自然过渡。',
        'closing': '回答开头的问题，区分已证实与仍未知；不强编几个独立新闻的共同因果。',
        'animation': '保留空间关系，按解释顺序逐层揭示；原片用于过程、截图用于出处、动画用于结构。留足读图时间。',
    },
}

_TECH = re.compile(r'(?i)(?<![a-z])AI(?![a-z])|人工智能|大模型|芯片|算力|TPU|GPU|Ironwood|DeepSeek|Qwen|模型训练')
_VERIFIED = {
    'platform': 'xiaohongshu',
    'title': '麦麦学AI · 一句话讲清楚AI的五大名词',
    'url': 'https://www.xiaohongshu.com/explore/6a9d6e5700000000250376f0',
    'evidence': '2026-11-17 读取原视频并转写；13秒，前五段约1.28–1.60秒。专名有转写误差，不作为事实来源。',
    'keywords': 'AI 人工智能 大模型 概念 芯片 算力',
    'notes': '连续使用简短、结构相近的解释句，一次只讲一个概念；屏幕标签跟随当前概念，最后把分散概念合起来。仅借表达结构，不借术语类比或结论。',
    'speech_profile': {
        'basis': 'transcript', 'timing_verified': True,
        'observed_at': '2026-11-17', 'duration_seconds': 12.933,
        'observed_segment_seconds': [1.44, 1.60, 1.40, 1.28, 1.44],
        'methods': ['一概念一句解释', '相近句式形成连续性', '先分解后组合'],
        'limits': '只分析表达和屏幕标签；不复制声音身份；短样本速度不套用于长片，不借类比充当新闻证据。',
    },
}


def qualified(case):
    if not isinstance(case, dict):
        return False
    profile = case.get('speech_profile')
    return (isinstance(profile, dict) and profile.get('basis') in {'transcript', 'user_script'}
            and isinstance(profile.get('methods'), list) and 1 <= len(profile['methods']) <= 8
            and all(isinstance(method, str) and 2 <= len(method) <= 600 for method in profile['methods'])
            and not re.search(r'图文|未读取.*口播|只有简介', case.get('evidence', '')))


def resolve_reference(output_dir, platform, topic, facts='', *, explicit=None):
    from core.copywriting import retrieve_cases

    candidates = ([explicit] if explicit else []) + retrieve_cases(output_dir, platform, topic, facts,
                                                                  actual_works=True, spoken_only=True)
    for case in candidates:
        if qualified(case):
            return {**case, 'match_kind': '用户指定口播写法' if case is explicit else '同平台相关口播',
                    'target_platform': platform, 'application': PLATFORMS[platform]}
    if _TECH.search(topic + ' ' + facts):
        match = '同类口播参考（非同题）' if platform == 'xiaohongshu' else '跨平台口播参考（小红书 · 非同题）'
        return {**copy.deepcopy(_VERIFIED), 'match_kind': match,
                'target_platform': platform, 'application': PLATFORMS[platform]}
    return None


def writing_instruction(context):
    """Used by both initial writing and repairs; reference facts never enter evidence."""
    style = (context.get('video_style') or {}).get('speech')
    if not style:
        return ''
    rules = '按当前平台说话：' + ' '.join(style[key] for key in ('opening', 'sentence', 'body', 'closing'))
    rules += (' 每镜围绕一个意思，尽量一镜一句完整口播，不用“首先其次最后”逐条念摘要。'
              '提问只用于引出当前原文能回答的内容，不增加新的事实判断。'
              '每个数字保留主体、单位、条件。animation只是画面安排，不是新闻事实。')
    ref = context.get('style_reference') or {}
    if qualified(ref):
        rules += ' 可迁移的真实口播特点：' + '；'.join(ref['speech_profile']['methods']) + '。'
        rules += '只迁移句式组织，不复制参考的句子、事实、声音身份或营销结尾；按目标平台调整长度，不照搬参考语速。'
    return rules


def sentences(text):
    """Keep quoted clauses together and never discard punctuation or source words."""
    parts, start, depth = [], 0, 0
    for index, char in enumerate(text):
        if char in '“‘「『':
            depth += 1
        elif char in '”’」』':
            depth = max(0, depth - 1)
        following = text[index+1:index+2]
        if depth == 0 and (char in '。！？!?' or (char in '”’」』' and index and text[index-1] in '。！？!?')) and following not in '”’」』':
            parts.append(text[start:index+1]); start = index+1
    if text[start:]:
        parts.append(text[start:])
    return parts or [text]


def sentence_cut(draft):
    # User-confirmed cuts and directed media windows are never resegmented.
    if draft.get('confirmed_scenes') or any(row.get('visual') for row in draft['scenes']):
        return draft
    rows, positions = [], {}
    for index, scene in enumerate(draft['scenes']):
        positions[index] = len(rows)
        parts = sentences(scene['text'])
        for text in parts:
            if not text.strip():
                continue
            title = scene['title']
            if len(parts) > 1:
                # A parent heading may name a different sentence's number or subject.
                # Use this sentence's own words, without another model rewrite.
                clause = re.split(r'[，。！？；]', text.strip(), maxsplit=1)[0]
                title = clause[:35] + ('…' if len(clause)>35 else '')
            rows.append({**scene, 'title':title or scene['title'], 'text':text})
    if len(rows) > 96:
        return draft
    return {**draft, 'scenes': rows, 'chapter_starts': [
        {**mark, 'scene': positions[mark['scene']]} for mark in draft.get('chapter_starts', [])]}


def analyze_spoken_work(work, platform, *, model=None):
    """Analyze an observed public media URL; raw reference media stays temporary."""
    import tempfile
    from pathlib import Path
    from urllib.parse import urlsplit
    from core.audio_engine import get_audio_duration
    from core.copywriting import analyze_case
    from core.news_media import _get_file
    from core.video_timeline import original_speech

    link = urlsplit(work.get('video_url') or '')
    if (platform != 'xiaohongshu' or link.scheme not in {'http','https'}
            or not re.fullmatch(r'[a-z0-9-]+\.xhscdn\.com', link.hostname or '')
            or link.username or link.password or link.port not in {None,80,443}):
        return None
    with tempfile.TemporaryDirectory(prefix='acf-spoken-reference-') as temporary:
        folder = Path(temporary)
        file = folder/'reference.mp4'
        _get_file(work['video_url'], file, 80_000_000)
        duration = get_audio_duration(str(file), strict=True)
        if not 3 <= duration <= 300:
            return None
        segments = original_speech({'path':str(file), 'kind':'video', 'duration':duration}, folder)
        if len(segments) < 3 or len(''.join(row['text'] for row in segments)) < 20:
            return None
        analysis = analyze_case(platform, '\n'.join(row['text'] for row in segments)[:6000], model=model)
        return {**analysis, 'speech_profile': {
            'basis':'transcript', 'timing_verified':True, 'duration_seconds':round(duration,3),
            'methods':[analysis['notes']],
            'observed_segment_seconds':[round(row['end']-row['start'],2) for row in segments[:20]],
            'limits':'Whisper转写用于提炼表达，可能有专名误差；不作为新闻事实或声音克隆依据。',
        }}
