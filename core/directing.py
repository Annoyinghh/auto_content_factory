"""Plan visuals and select timecoded excerpts after the narration is verified."""

import io
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from config import get_ffmpeg
from core.episodes import ON_SCREEN_NUMBER
from core.local_ai import chat_json, installed_models, model_for
from core.video_styles import (
    chapter_video_style, depth_labels, diagram_labels, drilling_flow_cues, sequence_labels, style_context, timeline_labels,
)


DIAGRAMS = ("steps", "timeline", "comparison", "depth", "drilling_flow", "chip_grid", "percent_bars", "parallel_flow", "result_split")
LAYOUTS = ("footage", "evidence", "metric", *DIAGRAMS)
MOTIONS = ("cut", "dissolve", "push", "pull")
FRAMINGS = ("fit", "center", "left", "right")
MOTION_INTENT = re.compile(r"正在|运转|运行|注入|流动|变形|晃动|振动(?!筛)|旋转|转动|移动|升降|起降|切换|推进过程|"
                           r"波动|翻滚|沸腾|冒泡|沉底|沉降|涌出|喷出|切成|切片|切粒|切菜|搅拌|翻炒|推动|打散|倒入|泼|剁|拌匀|混合")


def production_models():
    models = {role: model_for(role) for role in ("writing", "design", "editing")}
    ready = installed_models(local_only=True)
    return models if models["design"] in ready and models["editing"] in ready else None


def audit_visuals(draft, media):
    assets = {asset["page_url"]: asset for asset in media}
    counts = {"video_shots": 0, "image_shots": 0, "animated_shots": 0, "cards": 0}
    for shot in draft.get("scenes", ()):
        visual = shot.get("visual") or {}
        asset = assets.get(shot.get("media"))
        if asset:
            counts["video_shots" if asset.get("kind") == "video" else "image_shots"] += 1
        elif visual.get("layout") in DIAGRAMS:
            counts["animated_shots"] += 1
        else:
            counts["cards"] += 1
    total = sum(counts.values())
    ready = total > 0 and counts["cards"] * 2 <= total
    return {**counts, "total": total, "ready": ready,
            "message": "" if ready else "超过一半分镜缺少对应画面。请补充相关视频／图片并重新选镜，不会把图卡拼接标为成片。"}


def depth_visual(narration, layout, style):
    if (style and style["id"] == "explain" and layout in {"evidence", "metric"}
            and (labels := depth_labels(" ".join(narration.split())))):
        return {"layout": "depth", "labels": labels, "framing": "center", "start": 0,
                "intent": "深度标尺", "reason": "按口播中的实测深度绘制比例标尺"}
    return None


def drilling_visual(narration, layout, style, used_phases):
    if not style or style["id"] != "explain" or layout not in {"evidence", "metric", "depth"}:
        return None
    cues = drilling_flow_cues(" ".join(narration.split()))
    phases = {phase for _, phase, _ in cues}
    if not phases - used_phases or (layout == "depth" and phases == {"loss"}):
        return None
    used_phases.update(phases)
    return {"layout": "drilling_flow", "labels": [label for _, _, label in cues], "framing": "center", "start": 0,
            "intent": "钻井液流向剖面", "reason": "用原理示意呈现井内流向及其变化"}


def _rows_schema(count, fields):
    return {"type": "object", "properties": {"shots": {"type": "array", "minItems": count, "maxItems": count,
            "items": {"type": "object", "properties": fields, "required": list(fields),
                      "additionalProperties": False}}}, "required": ["shots"], "additionalProperties": False}


def _choice(values):
    return {"type": "string", "enum": list(values)}


def _ordered(result, count):
    rows = result.get("shots") if isinstance(result, dict) else None
    if (not isinstance(rows, list) or len(rows) != count
            or any(not isinstance(row, dict) or type(row.get("index")) is not int for row in rows)
            or sorted(row["index"] for row in rows) != list(range(count))):
        raise ValueError("模型没有给出每镜对应的视觉决策。")
    return sorted(rows, key=lambda row: row["index"])


def _design(shots, platform, chapter, available, model, *, style=None, position=""):
    count = len(shots)
    layouts = ["evidence", "metric"]
    if available:
        layouts.insert(0, "footage")
    if style:
        layouts.append(style["diagram"])
    fields = {"index": {"type": "integer", "minimum": 0, "maximum": count - 1},
              "layout": _choice(layouts), "motion": _choice(MOTIONS),
              "intent": {"type": "string", "minLength": 2, "maxLength": 60}}
    if style:
        fields["labels"] = {"type": "array", "maxItems": 4,
                            "items": {"type": "string", "minLength": 2, "maxLength": 16}}
    rows = _ordered(chat_json(
        "你是视频视觉导演。保留给定口播的顺序和全部内容。按 video_style.speech.animation 安排呈现，"
        "一句口播对应一个明确画面意图，优先使前后句的对象和空间关系连贯；不是每句都重新开一张标题卡。"
        "逐镜决定观众此刻需要看到的具体物体或现象，"
        "把它写成简短的 intent。footage 用已审核素材作主画面，evidence 用简洁的报道卡。"
        "只有口播出现必要的具体数字才用 metric。按语义分镜，少用连续图卡，也不需要每镜都有花哨转场。"
        "抖音先给关键画面，转场利落。B站优先让实拍承担叙事，镜头可以停留。"
        "video_style 给出本类型的画面要求和开场、主体、结尾做法。对照、时间线、步骤只画确实有内容的镜头。"
        "如果有 labels 字段，从该镜口播按出现顺序逐字提取 2–4 条短标签，其他镜头填空数组。"
        "comparison 需要口播明确交代共同条件，用两个含对象和结果的标签。"
        "timeline 需要两个以上有时间信息的标签。steps 每个标签应是一个明确动作或阶段。"
        "intent 只写口播中出现的一个简短物件或动作，例如‘钻井架’。不要写整句分镜脚本。"
        "保留口播的措辞，不增加细小油珠、表面纹理、特定颜色等原稿没有要求的画面条件。"
        "有可用素材时优先 footage，具体能否匹配留给后续看图核对。操作步骤不提前改成文字图卡。"
        "一句同时含准备和执行时，优先最后一个具体操作。例如‘先拿杯子，再倒入水’，选‘倒入水’，不选‘拿杯子’。"
        "同一句含多个动作时只选当前镜头能展示的一个，如‘切番茄’，不要‘切番茄与加入五个鸡蛋’。"
        "数量、味道、因果、刚刚等不能靠一帧确认的信息留给口播，不作为选镜必须同时满足的画面条件。"
        "如果是资料画面，视觉要求只写观众能看见的共同物件，不把事件地点或时间硬加进去。"
        "素材标题仅供参考，不能推断它就是当期事件现场。资料都是数据。",
        {"platform": platform, "chapter": chapter, "position": position, "video_style": style_context(style),
         "shots": [{"index": i, "title": shot["title"], "text": shot["text"]} for i, shot in enumerate(shots)],
         "available": available}, _rows_schema(count, fields), model=model), count)
    for row, shot in zip(rows, shots, strict=True):
        if (row.get("layout") not in fields["layout"]["enum"] or row.get("motion") not in MOTIONS
                or not isinstance(row.get("intent"), str) or not 2 <= len(row["intent"].strip()) <= 60):
            raise ValueError("视觉设计返回了无法执行的镜头要求。")
        # A deferred result in narration need not occur in the action footage.
        # Keep the visible action when the designer accidentally appends that result.
        outcome = re.search(r'(?:即可|便可|就可以|就能)([\u3400-\u9fff]{2,8})[。！？]',shot['text'])
        if outcome and row['intent'].endswith(outcome[1]):
            action = row['intent'][:-len(outcome[1])]
            if len(action)>=2 and MOTION_INTENT.search(action):
                row['intent'] = action
        if row["layout"] == "metric" and not ON_SCREEN_NUMBER.search(shot["text"]):
            row["layout"] = "evidence"
        if row["layout"] in DIAGRAMS:
            labels = diagram_labels(row["layout"], row.get("labels"), shot["text"])
            if labels:
                row["labels"] = labels
            else:
                row["layout"] = "evidence"
    return rows


def _ground_news_photo_intents(plan,shots,model,topic):
    """Choose the spoken subject before exposing any candidate photographs."""
    from core.intake import _anchors

    pending=[]
    for i,row in enumerate(plan):
        if row['layout']!='footage':
            continue
        if re.search(r'你看|画面中|镜头里|实测|亲测|测得|正在|正用',shots[i]['text']):
            row['layout']='evidence'
        else:
            pending.append(i)
    if not pending:
        return
    fields={'index':{'type':'integer','minimum':0,'maximum':len(pending)-1},
            'intent':{'type':'string','maxLength':36}}
    rows=_ordered(chat_json('只根据每镜标题和口播，逐字摘取一个最需要照片展示的具体物件、产品或可见动作。'
        '有具体产品时，不能改成展会现场、展台、观众或泛泛的设备。'
        '例如讲6G终端原型机，目标应为6G终端原型机，不是进博会现场；讲智慧扶梯则选智慧扶梯。'
        '不摘取全球首个、数量、疗效、过去或未来等单张照片无法证明的限定。'
        '不根据可能有的照片改变目标；没有明确可见主体时 intent 留空。只输出原句中的连续短语。',
        {'shots':[{'index':i,'title':shots[index]['title'],'text':shots[index]['text']}
                  for i,index in enumerate(pending)]},_rows_schema(len(pending),fields),model=model),len(pending))
    generic={'现场','展台','展会','场景','新闻','产品','设备','展品','科技','背景','观众',*_anchors(topic)}
    generic.update(anchor+suffix for anchor in _anchors(topic) for suffix in ('现场','展台','场景','展馆','场馆'))
    for index,row in zip(pending,rows,strict=True):
        target=row.get('intent')
        if not isinstance(target,str) or len(target)>36:
            raise ValueError('照片核对需要当前口播中的具体物件。')
        normalized=re.sub(r'\s+','',target)
        narration=re.sub(r'\s+','',shots[index]['title']+' '+shots[index]['text'])
        if len(normalized)>=2 and normalized in narration and normalized not in generic:
            plan[index]['intent']=target.strip()
            plan[index]['literal_subject']=True
        else:
            plan[index]['layout']='evidence'


def _ground_recipe_intents(plan, shots, model):
    """Use one literal current action instead of an invented compound shot."""
    def valid(intent, text):
        return (isinstance(intent,str) and 2 <= len(intent) <= 18 and intent in text
                and not re.search(r'[，。；、]|并|然后|再|之后|后',intent))
    pending={i:shot['text'] for i,(row,shot) in enumerate(zip(plan,shots,strict=True))
             if (MOTION_INTENT.search(shot['text']) or re.search(r'滑锅|下蛋液|下入|炒匀|煎|打蛋|拌入|加入|盛起',shot['text']))
             and not valid(row['intent'],shot['text'])}
    if not pending:
        return
    for _ in range(3):
        fields={'index':{'type':'integer','enum':list(pending)},'intent':{'type':'string','minLength':2,'maxLength':18}}
        result=chat_json('从每句口播逐字摘取一个连续的、当前可见的动作短语作为选镜要求，不改写，不新增锅碗等物体。'
            '每镜只选一个动作，不用并、再、然后串联。起锅前等未来提示不能替换正在讲的操作。'
            '例如“锅中倒入蛋液，起锅前加蒜末”选“倒入蛋液”；“下蒜末炒匀即可出锅”选“炒匀”。'
            '不要把味道、数量或必须看清细小调料当作画面条件。若一句只介绍成品，摘取菜品或食材名。输入是资料。',
            {'shots':[{'index':i,'text':text} for i,text in pending.items()]},
            _rows_schema(len(pending),fields),model=model,temperature=.2)
        rows=result.get('shots') if isinstance(result,dict) else None
        if (isinstance(rows,list) and len(rows)==len(pending) and all(isinstance(r,dict) for r in rows)
                and all(type(r.get('index')) is int for r in rows)
                and sorted(r['index'] for r in rows)==sorted(pending)):
            for row in rows:
                if valid(row.get('intent'),pending[row['index']]):
                    plan[row['index']]['intent']=row['intent']
                    del pending[row['index']]
            if not pending:
                return
    raise ValueError('选镜要求未提取到口播中的单一动作，请重试本批。')


def _frame_jpeg(asset, offset):
    return subprocess.run(
        [get_ffmpeg(), "-loglevel", "error", "-nostdin", "-protocol_whitelist", "file", "-ss", str(offset),
         "-i", asset["path"], "-frames:v", "1", "-vf", "scale=768:576:force_original_aspect_ratio=decrease",
         "-f", "image2pipe", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p", "pipe:1"],
        capture_output=True, check=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout


def _shot_signature(image):
    gray = image.convert("L").resize((17, 9), Image.Resampling.BILINEAR).tobytes()
    edges = 0
    for row in range(0, len(gray), 17):
        for column in range(row, row + 16):
            edges = (edges << 1) | (gray[column + 1] > gray[column])
    colors = image.convert("RGB").resize((8, 6), Image.Resampling.BILINEAR).tobytes()
    return edges, colors.hex()


def _same_angle(frame, pick):
    same_source = frame["media"] == pick["media"]
    if same_source and (frame["kind"] == "image" or frame.get("start") == pick.get("start")):
        return True
    if not frame.get("signature") or not pick.get("signature"):
        return False
    edges, colors = frame["signature"]
    prior_edges, prior_colors = pick["signature"]
    if (edges ^ prior_edges).bit_count() > (20 if same_source else 10):
        return False
    colors, prior_colors = bytes.fromhex(colors), bytes.fromhex(prior_colors)
    return sum(abs(a - b) for a, b in zip(colors, prior_colors, strict=True)) <= len(colors) * (8 if same_source else 5)


def _contact_sheet(assets, windows=None):
    frames, images = [], []
    for asset in assets:
        video = asset.get("kind") == "video"
        duration = asset.get("duration") or 0
        if video and duration >= 6:
            offsets = tuple(round(duration * point, 2) for point in (.08, .25, .44, .64, .82))
        elif video and duration >= 1:
            offsets = tuple(round(duration * point, 2) for point in (.05, .35, .65, .88))
        else:
            offsets = (0.0,)
        samples = [(offset,None) for offset in offsets]
        if windows is not None and asset['page_url'] in windows:
            samples = []
            for window in windows[asset['page_url']]:
                start,end = window['start'],window['end']
                span = end-start
                anchors = [start+span/2]
                if span>8:
                    anchors += [start+3+3*i for i in range(int((span-6)/3)+1)] + [end-3]
                for anchor in sorted(set(round(value,3) for value in anchors)):
                    radius = min(3,anchor-start,end-anchor)
                    samples.append((anchor,{**window,'start':anchor-radius,'end':anchor+radius}))
        for offset, window in samples:
            try:
                preview = io.BytesIO(_frame_jpeg(asset, offset)) if video else Path(asset.get("preview") or "")
                with Image.open(preview) as source:
                    wide = source.width > source.height
                    detail = ImageOps.contain(source.convert("RGB"), (768, 576))
                    image = ImageOps.contain(detail, (240, 136))
                    verification = io.BytesIO()
                    detail.save(verification, "JPEG", quality=92)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            frames.append({"media": asset["page_url"], "start": offset, "title": asset["title"],
                           "kind": asset["kind"], "wide": wide, "signature": _shot_signature(detail),
                           "label": asset.get("caption_label") or "资料画面", "_verification": verification.getvalue()})
            if window:
                frames[-1].update(title=window['text'],
                    _window_start=window['start'],_window_end=window['end'])
                if 'shot_index' in window:
                    frames[-1]['shot_index'] = window['shot_index']
                try:
                    frames[-1]['_sequence'] = [_frame_jpeg(asset,window['start']+.03),verification.getvalue(),
                                              _frame_jpeg(asset,max(window['start'],window['end']-.05))]
                except (OSError,ValueError,subprocess.SubprocessError):
                    frames.pop()
                    continue
            images.append(image)
    if not frames:
        return frames, b""
    columns = 3
    sheet = Image.new("RGB", (248 * columns, 163 * ((len(frames) + columns - 1) // columns)), "#121a21")
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(images):
        x, y = (index % columns) * 248, (index // columns) * 163
        sheet.paste(image, (x + (248-image.width)//2, y + (136-image.height)//2))
        draw.text((x+8, y+140), str(index), fill="white")
    stream = io.BytesIO()
    sheet.save(stream, "JPEG", quality=86)
    return frames, stream.getvalue()


def observe_sequence(sequence, model):
    """Describe actual frames without receiving a requested subject or action."""
    observed = chat_json(
        "只观察提供的画面，按顺序分别写出实际可见的物体、工具及正在进行的动作。"
        "不要读字幕，不猜测下一步，不根据生活常识补上没有拍到的动作。"
        "若物体尚未接触，只写可见的位置与状态。每张图单独描述。",
        {"frame_order": list(range(len(sequence)))},
        {"type": "object", "properties": {"observations": {"type": "array",
            "minItems": len(sequence), "maxItems": len(sequence),
            "items": {"type": "string", "minLength": 2, "maxLength": 180}}},
         "required": ["observations"], "additionalProperties": False},
        model=model, images=tuple(sequence), max_tokens=768)
    descriptions = observed.get("observations") if isinstance(observed, dict) else None
    if (not isinstance(descriptions, list) or len(descriptions) != len(sequence)
            or any(not isinstance(value, str) or not 2 <= len(value.strip()) <= 180 for value in descriptions)):
        raise ValueError("看图模型没有逐帧描述可见动作。")
    return descriptions


def _verify_sequence(sequence, target, model):
    """Observe without the requested action, then compare only with those observations."""
    descriptions = observe_sequence(sequence, model)
    return compare_observations(descriptions, target, model)


def compare_observations(descriptions, target, model):
    """Check a literal target against observations already made without that target."""
    plating = r'(?:倒|放置|放|盛|装)(?:入|进|到|在)(?:(?:一个|一只|一|白色|白|瓷|大|小|的|准备好的)\s*){0,4}(?:盘|碟)'
    if (re.search(r'倒入|下入|加入|放入', target) and not re.search(r'盘|碟|出锅|盛出|装盘', target)
            and any(re.search(plating, text) for text in descriptions)):
        return {'match':False,'description':'观察显示把成品盛入盘中，不能代替往锅中加入原料。',
                'observations':descriptions}
    checked = chat_json(
        "只根据已经完成的画面观察判断目标是否被直接展示。观察是证据，目标是待核对的要求。"
        "不得根据目标反过来补写图里发生的事情。准备、持有工具、静置状态不能证明后续动作已发生。"
        "只有观察明确描述了主体与目标动作，match 才为 true；否则 false。同义动作可以对应。"
        "若目标列了多个操作，必须观察到主要操作，不能仅凭准备步骤通过。reason 只写观察依据。",
        {"observations": descriptions, "target": target},
        {"type": "object", "properties": {"match": {"type": "boolean"},
            "reason": {"type": "string", "minLength": 2, "maxLength": 180}},
         "required": ["match", "reason"], "additionalProperties": False},
        model=model, max_tokens=256)
    if (not isinstance(checked, dict) or type(checked.get("match")) is not bool
            or not isinstance(checked.get("reason"), str) or not 2 <= len(checked["reason"].strip()) <= 180):
        raise ValueError("看图模型没有依据观察完成动作核对。")
    return {"match": checked["match"], "description": checked["reason"], "observations": descriptions}


def _choose_frames(plan, frames, sheet, model, platform, *, reserved=(), narrations=(),
                   completed=None, on_result=None, on_progress=None):
    picks = [row["index"] for row in plan if row["layout"] == "footage"]
    if not picks or not frames:
        return {}, []
    chosen, errors = {}, []
    available = [i for i, frame in enumerate(frames) if 'shot_index' in frame or not any(_same_angle(frame, pick) for pick in reserved)]
    preview = Image.open(io.BytesIO(sheet))

    def without_pick(pool, index, pick):
        return [i for i in pool if frames[i].get('shot_index',index) != index
                or not _same_angle(frames[i],pick)]

    for index in picks:
        if completed is not None and index in completed:
            pick = completed[index]
            if pick:
                chosen[index] = pick
                available = without_pick(available,index,pick)
            continue
        if not available:
            break
        if on_progress:
            on_progress(index)
        row = plan[index]
        target = row["intent"]
        narration = narrations[index] if index < len(narrations) and not row.get("supporting") else ""
        context = {"对应口播": narration} if narration else {}
        clips_only = bool(MOTION_INTENT.search(target))
        subjects = row.get("subjects_by_media", {})
        candidates = [i for i in available if (not clips_only or frames[i]["kind"] == "video")
                      and frames[i].get('shot_index',index) == index
                      and (not subjects or frames[i]["media"] in subjects)]
        fallback = False
        try:
            for _ in range(max(2, min(12, len(candidates)))):
                if not candidates:
                    break
                schema = {"type": "object", "properties": {"frame": {"type": "integer", "enum": [-1, *candidates]}},
                          "required": ["frame"], "additionalProperties": False}
                if fallback or subjects and len(candidates) == 1:
                    frame = candidates[0]
                else:
                    selection_sheet = sheet
                    if all(frames[i].get('_verification') for i in candidates):
                        tiles = Image.new('RGB',(744,163*((len(candidates)+2)//3)),'#121a21')
                        drawing = ImageDraw.Draw(tiles)
                        for slot,identity in enumerate(candidates):
                            with Image.open(io.BytesIO(frames[identity]['_verification'])) as original:
                                tile = ImageOps.contain(original.convert('RGB'),(240,136))
                            x,y = (slot%3)*248,(slot//3)*163
                            tiles.paste(tile,(x+(248-tile.width)//2,y))
                            drawing.text((x+8,y+140),str(identity),fill='white')
                        encoded = io.BytesIO()
                        tiles.save(encoded,'JPEG',quality=90)
                        selection_sheet = encoded.getvalue()
                    result = chat_json(
                        "哪张候选画面能清楚展示目标物体或动作？若每张有自己的核对对象，以该对象为准。"
                        "只给 frame 编号。都没有填 -1。",
                        {**context, "目标": target, "可选画面": {
                            i: {"标题": frames[i]["title"], "核对对象": subjects.get(frames[i]["media"], target)}
                            for i in candidates}}, schema, model=model, images=(selection_sheet,), max_tokens=768)
                    frame = result.get("frame") if isinstance(result, dict) else None
                if type(frame) is not int or (frame != -1 and frame not in candidates):
                    raise ValueError("看图模型返回了无效的素材编号。")
                if frame == -1:
                    # Small labels and multiple similar actions can be missed in a contact sheet.
                    # Rank only for efficiency; every fallback still needs the full-size visual check below.
                    terms = {target[i:i+2] for i in range(max(0, len(target)-1))}
                    candidates.sort(key=lambda i: sum(term in frames[i]["title"] for term in terms), reverse=True)
                    fallback = True
                    frame = candidates[0]
                candidate = frames[frame]
                candidate_target = subjects.get(candidate["media"], target)
                detail = candidate.get("_verification")
                if not detail:
                    x, y = (frame % 3) * 248, (frame // 3) * 163
                    crop = io.BytesIO()
                    preview.crop((x, y, x + 248, y + 136)).save(crop, "JPEG", quality=92)
                    detail = crop.getvalue()
                rule = (
                    "核对资料画面里是否清楚展示这个物体。资料可来自其他项目，只要看得见同类物体。"
                    "文字提到但画面没拍到，或者看不清，填 false。只返回 match。"
                    if row.get("supporting") else
                    "检查提供的画面。目标的主体和动作都必须在画面中看得见。不能用题材相似、静态设备或地名猜测。"
                    "同义动作可以匹配，例如切成小颗粒与切丁。图中确实清楚展示目标动作就填 true，看不清或没发生则填 false。"
                )
                checked = (_verify_sequence(candidate.get('_sequence') or (detail,), candidate_target, model)
                           if candidate.get('_sequence') or row.get('literal_subject') else chat_json(
                    "先用一句话描述画面里实际可见的主体、动作和工具，description 不猜测。再判断 match。"
                    "已经切好的食材不等于正在切，手碰着碗不等于搅拌，工具摆着不等于正在运转。"
                    "如果有三张图，它们是同一片段的开头、中间、末尾；中间必须可见目标主体，动作须在连续画面中实际发生。"
                    "口播用来限定当前步骤：备料搅拌不能用下锅翻炒替代。不要从口播猜图中没出现的动作。"
                    + ("目标若写了连续动作，核对当前可见的主要操作，温度、味道和预期效果不作为画面条件。"
                       "例如烧热锅再滑油，实际看到锅内的油被推动即可对应滑油。"
                       "但只有油在锅里滑动，不能判断食材已经下锅。description 必须明确指出可见的匹配动作。"
                       if candidate.get('_sequence') else "")
                    + rule,
                    {**context, "目标": candidate_target},
                    {"type": "object", "properties": {"description": {"type": "string", "maxLength": 160},
                        "match": {"type": "boolean"}}, "required": ["description", "match"]},
                    model=model, images=(detail,), max_tokens=1536))
                if not isinstance(checked, dict) or type(checked.get("match")) is not bool:
                    raise ValueError("看图模型没有完成单镜素材复核。")
                if not checked["match"]:
                    # A fixed camera can show different actions later; rejecting one instant says
                    # nothing about the other timestamps from that shot.
                    candidates = [i for i in candidates if i != frame]
                    continue
                framing = "fit" if candidate["wide"] != (platform == "bilibili") else "center"
                chosen[index] = {key: value for key, value in candidate.items() if not key.startswith('_')}
                if '_window_end' in candidate:
                    chosen[index].update(start=candidate['_window_start'],end=candidate['_window_end'])
                description = str(checked.get("description", "")).strip()
                chosen[index].update(framing=framing, intent=candidate_target,
                                     reason=("可见画面：" + description)[:70] if description else "已核对候选帧的可见主体")
                if checked.get('observations'):
                    chosen[index]['observations'] = checked['observations']
                available = without_pick(available,index,candidate)
                break
        except (RuntimeError, ValueError) as error:
            errors.append(str(error))
            continue
        if on_result:
            on_result(index,chosen.get(index))
    return chosen, errors


def direct_cut(draft, media, platform, directory, models, progress=lambda _title: None, *,
               chapters=(), video_style=None, fixed_style=False, brief=None, only=None, required_media=None,
               source_indexes=(), topic='', durations=None):
    from core.comparison_media import select_comparison
    from core.editing import validate_cut
    from core.editorial import _hash
    from core.workflow import write_json

    directory.mkdir(parents=True, exist_ok=True)
    scenes = draft["scenes"]
    markers = draft.get("chapter_starts") or [{"scene": 0, "title": "正文"}]
    chapter_indexes = {chapter["story"]["title"]: index for index, chapter in enumerate(chapters)}
    output, styled_markers, warnings = list(scenes), [], []
    used_drilling_phases = set()
    used_frames = []
    used_pairs = set()
    indexed_refs = {key:value for key,value in (draft.get('editorial_plan') or {}).get('indexed_scene_refs',{}).items()
                    if only is not None and int(key) not in only}
    for marker, end in zip(markers, [*(row["scene"] for row in markers[1:]), len(scenes)], strict=True):
        title = marker["title"]
        chapter = chapter_indexes.get(title)
        style = (chapter_video_style(platform, video_style, chapters[chapter], fixed=fixed_style)
                 if chapter is not None else video_style)
        styled_markers.append({**marker, **({"video_style": style} if style else {})})
        position = "片头" if marker["scene"] == 0 else "片尾" if end == len(scenes) else "正文"
        indexes = [index for index in range(marker["scene"], end) if only is None or index in only]
        if not indexes:
            continue
        shots = [scenes[index] for index in indexes]
        report_ids = {entry.get("id") for entry in chapters[chapter].get("reports", [])} if chapter is not None else set()
        eligible = []
        for asset in media:
            source_id = asset.get("verified_report_id")
            if not source_id and asset.get("origin") == "article":
                source_id = asset.get("source_report_id")
            if source_id and chapter is not None:
                if source_id in report_ids:
                    eligible.append(asset)
            elif asset.get("local") or not asset.get("chapters") or chapter in asset["chapters"]:
                eligible.append(asset)
        if required_media:
            eligible = [asset for asset in media if asset["page_url"] == required_media]
        pairings = {}
        if style and style["id"] == "compare":
            for index, shot in enumerate(shots):
                pair = select_comparison((asset for asset in eligible if asset["page_url"] not in used_pairs), shot["text"])
                if pair:
                    pairings[index] = pair
                    used_pairs.add(pair["page_url"])
        eligible = [asset for asset in eligible if not asset.get("comparison")]
        eligible_images = [asset for asset in eligible if asset.get('kind') == 'image'][:8]
        preferred = {shot.get("media") for shot in shots}
        eligible.sort(key=lambda asset: (asset["page_url"] not in preferred, asset.get("kind") != "video"))
        eligible = eligible[:6]
        if source_indexes and (chapter is None or not chapters[chapter].get('source_section')):
            from core.grounded_news import select_clips
            from core.intake import _anchors

            anchors = _anchors(topic)
            eligible_ids = {asset['page_url'] for asset in eligible}
            indexed_assets = [asset for asset in media if asset['page_url'] in eligible_ids or not required_media and any(
                anchor in str(asset.get('source_title',''))+' '+str(asset.get('source_summary','')) for anchor in anchors)]
            indexed_ids = {asset['page_url'] for asset in indexed_assets}
            observed = [{**entry,'context_only':entry['media'] not in eligible_ids} for entry in source_indexes
                        if entry['media'] in indexed_ids]
            plan,picks = select_clips(shots,indexed_assets,observed,title,models['design'],models['editing'],
                                     directory/'indexed-news',platform,progress,
                                     reserved=[clip_id for row in indexed_refs.values()
                                               for clip_id in row.get('clip_ids',[row['clip_id']])],
                                     **({'durations':[durations[i] for i in indexes]} if durations is not None else {}))
            missing = [i for i in range(len(shots)) if i not in picks]
            images = eligible_images
            if missing and images:
                photo_shots = [shots[i] for i in missing]
                stamps=[]
                for asset in images:
                    file=Path(asset.get('preview') or '')
                    stat=file.stat() if file.is_file() else None
                    stamps.append([asset,stat.st_size if stat else None,stat.st_mtime_ns if stat else None])
                photo_key=_hash(['news-photos-v4-blind-subject',platform,title,
                    [{'title':shot['title'],'text':shot['text']} for shot in photo_shots],stamps,models,style,
                    [(row['media'],row['signature']) for row in used_frames]])
                photo_cache=directory/f'photo-{photo_key}.json'
                saved=json.loads(photo_cache.read_text(encoding='utf-8')) if photo_cache.is_file() else None
                if not isinstance(saved,dict):
                    progress(f'{title} · 核对已选原文照片')
                    decisions=_design(photo_shots,platform,title,[asset['title'] for asset in images],
                                      models['design'],style=style,position=position)
                    _ground_news_photo_intents(decisions,photo_shots,models['design'],topic)
                    frames,sheet=_contact_sheet(images)
                    photos,errors=_choose_frames(decisions,frames,sheet,models['editing'],platform,reserved=used_frames,
                        narrations=[shot['text'] for shot in photo_shots])
                    saved={'plan':decisions,'picks':{str(i):row for i,row in photos.items()}}
                    if not errors:
                        write_json(photo_cache,saved)
                    else:
                        warnings.append(f'{title} · 照片待复核 · {errors[0]}')
                for i,scene_offset in enumerate(missing):
                    if str(i) in saved['picks']:
                        picks[scene_offset]=saved['picks'][str(i)]
                        used_frames.append(saved['picks'][str(i)])
                        plan[scene_offset]={**saved['plan'][i],'index':scene_offset}
                missing=[i for i in missing if i not in picks]
            if missing:
                fallback = _design([shots[i] for i in missing],platform,title,[],models['design'],style=style,position=position)
                for index,row in zip(missing,fallback,strict=True):
                    plan[index] = {**row,'index':index}
            for index,(shot,decision) in enumerate(zip(shots,plan,strict=True)):
                scene_index = indexes[index]
                pick = picks.get(index)
                if not pick:
                    from core.news_diagrams import visual_for
                    if generated := visual_for(shot):
                        output[scene_index] = {**shot, 'media':'', 'motion':'cut', 'zoom':False, 'visual':generated}
                        continue
                layout = 'footage' if pick else decision['layout']
                visual = {'layout':layout,'start':pick['start'] if pick else 0,'framing':'fit' if pick else 'center',
                          'intent':(pick or decision)['intent'],
                          'reason':pick['reason'] if pick else '按原文支持的关系编排图解'}
                photo=pick and pick.get('kind') == 'image'
                if pick and not photo:
                    visual['end'] = pick['end']
                    visual['clips'] = [{key:clip[key] for key in ('media','start','end','intent','reason','for_text')}
                                       for clip in pick['clips']]
                    indexed_refs[str(scene_index)] = {'clip_id':pick['clip_id'],'usage':pick['usage'],
                                                     'media':pick['media'],'start':pick['start'],'end':pick['end'],
                                                     'clip_ids':[clip['clip_id'] for clip in pick['clips']]}
                elif layout in DIAGRAMS:
                    visual['labels'] = decision.get('labels',[])
                motion=('push' if scene_index % 2 == 0 else 'pull') if photo else decision['motion']
                output[scene_index] = {**shot,'media':pick['media'] if pick else '',
                                      'motion':motion,'zoom':bool(photo),'visual':visual}
            continue
        context = {'chapter':title,'notes':(brief or {}).get('notes',[])}
        if chapter is not None and chapters[chapter].get("question"):
            context["question"] = chapters[chapter]["question"]
        if chapter is not None and chapters[chapter].get("source_section"):
            context["source_section"] = chapters[chapter]["source_section"]
        if required_media:
            context["selected_media"] = required_media
        key = _hash(["visual-v26-spoken", platform, title, shots, eligible, models, style, position, context,
                     [(index, pair["page_url"]) for index, pair in pairings.items()]])
        design_path = directory / f"design-{key}.json"
        edit_key = _hash(["selection-v11", key, [(pick["media"], pick["start"], pick["signature"]) for pick in used_frames]])
        edit_path = directory / f"edit-{edit_key}.json"
        progress(title)
        descriptions = [f"{asset['caption_label']} · {asset['title']}" if asset.get("caption_label") else asset["title"]
                        for asset in eligible]
        tags = [term for asset in eligible for term in asset.get("visual_subjects", ())]

        def supporting(decision, shot):
            subjects = {}
            for subject in dict.fromkeys([decision["intent"], *tags]):
                if (not re.fullmatch(r"[\u3400-\u9fff]{2,12}", subject) or MOTION_INTENT.search(subject)
                        or subject not in shot["text"]):
                    continue
                for asset, label in zip(eligible, descriptions, strict=True):
                    if subject in asset.get("visual_subjects", ()) or subject in label:
                        subjects.setdefault(asset["page_url"], subject)
            return ({"layout": "footage", "intent": next(iter(subjects.values())), "subjects_by_media": subjects,
                     "supporting": True} if subjects else {})

        if design_path.is_file():
            plan = json.loads(design_path.read_text(encoding="utf-8"))
        else:
            plan = _design(shots, platform, title, descriptions, models["design"],
                           style=style, position=position)
            if context.get('source_section'):
                _ground_recipe_intents(plan,shots,models['design'])
            if style and style["diagram"] in {"steps", "timeline"}:
                kind = style["diagram"]
                labels_for = sequence_labels if kind == "steps" else timeline_labels
                for decision, shot in zip(plan, shots, strict=True):
                    if decision["layout"] in {"evidence", "metric"} and (labels := labels_for(shot["text"])):
                        decision.update(layout=kind, labels=labels, motion="cut")
            for index, (decision, shot) in enumerate(zip(plan, shots, strict=True)):
                if index in pairings:
                    decision["layout"] = "evidence"
                elif required_media:
                    decision["layout"] = "footage"
                elif context.get('source_section') and any(a.get('kind')=='video' for a in eligible):
                    decision['layout'] = 'footage'
                elif decision["layout"] == "evidence":
                    decision.update(supporting(decision, shot))
            write_json(design_path, plan)
        if edit_path.is_file():
            picks = {int(index): row for index, row in json.loads(edit_path.read_text(encoding="utf-8")).items()}
        else:
            from core.video_timeline import source_windows
            windows = {}
            if any(row['layout'] == 'footage' for row in plan):
                for asset in eligible:
                    if asset.get('kind') == 'video' and asset.get('duration',0) >= 30:
                        windows[asset['page_url']] = source_windows(
                            asset,shots,context,models['writing'],directory/'timeline',progress)
            frames, sheet = _contact_sheet(eligible,windows=windows) if any(
                row["layout"] == "footage" for row in plan) else ([], b"")
            def select_frames(decisions, suffix, reserved):
                partial_path = directory / f"edit-{edit_key}-{suffix}.partial.json"
                completed = ({int(index):row for index,row in json.loads(partial_path.read_text(encoding='utf-8')).items()}
                             if partial_path.is_file() else {})

                def remember(index, pick):
                    completed[index] = pick
                    write_json(partial_path,completed)

                return _choose_frames(decisions,frames,sheet,models['editing'],platform,reserved=reserved,
                                      narrations=[shot['text'] for shot in shots],completed=completed,
                                      on_result=remember,
                                      on_progress=lambda index:progress(f'{title} · 看图核对 {index+1}/{len(plan)}'))

            picks, errors = select_frames(plan,'main',used_frames)
            alternatives = [{**row, "layout": "evidence"} for row in plan]
            for index, (decision, shot) in enumerate(zip(plan, shots, strict=True)):
                if index not in picks and decision["layout"] == "footage" and not decision.get("supporting"):
                    alternatives[index].update(supporting(decision, shot))
            more, more_errors = select_frames(alternatives,'support',[*used_frames,*picks.values()])
            picks.update({index: {**pick, "supporting": True}
                          for index, pick in more.items()})
            errors.extend(more_errors)
            if errors:
                warnings.append(f"{title} · {len(errors)} 镜待复核 · {errors[0]}")
            else:
                write_json(edit_path, picks)
        for index, (shot, decision) in enumerate(zip(shots, plan, strict=True)):
            scene_index = indexes[index]
            if pair := pairings.get(index):
                output[scene_index] = {**shot, "media": pair["page_url"], "motion": "cut", "zoom": False,
                               "visual": {"layout": "footage", "start": 0, "framing": "fit",
                                          "intent": "双样本同屏对照", "reason": "口播与已确认的样本和共同条件一致"}}
                continue
            pick = picks.get(index)
            if not pick and not context.get('source_section'):
                from core.news_diagrams import visual_for
                if generated := visual_for(shot):
                    output[scene_index] = {**shot, 'media':'', 'motion':'cut', 'zoom':False, 'visual':generated}
                    continue
            if pick:
                used_frames.append(pick)
            layout = "footage" if pick else "evidence" if decision["layout"] == "footage" else decision["layout"]
            labels = decision.get("labels", []) if layout in DIAGRAMS else []
            depth = depth_visual(shot["text"], layout, style)
            diagram = drilling_visual(shot["text"], "depth" if depth else layout, style, used_drilling_phases) or depth
            if diagram:
                output[scene_index] = {**shot, "media": "", "motion": "cut", "zoom": False, "visual": diagram}
                continue
            motion = decision["motion"] if pick or decision["motion"] == "dissolve" else "cut"
            if platform == "douyin" and motion == "dissolve":
                motion = "cut"
            if pick and motion == "cut" and any(
                    asset["page_url"] == pick["media"] and asset["kind"] == "image" for asset in eligible):
                motion = "push" if scene_index % 2 == 0 else "pull"
            if layout in DIAGRAMS:
                motion = "cut"
            reason = ("已核对口播中的可见对象，用作资料画面" if pick and (decision.get("supporting") or pick.get("supporting")) else
                      pick["reason"] if pick else "按口播重点生成图解" if layout in DIAGRAMS else
                      "突出原文支持的数据" if layout == "metric" else "缺少匹配画面，使用报道图卡")
            visual = {"layout": layout, "start": pick["start"] if pick else 0,
                      "framing": pick["framing"] if pick else "center", "intent": (pick or {}).get("intent", decision["intent"]),
                      "reason": reason}
            if pick and 'end' in pick:
                visual['end'] = pick['end']
            if layout in DIAGRAMS:
                visual["labels"] = labels
            output[scene_index] = {**shot, "media": pick["media"] if pick else "", "motion": motion,
                                   "zoom": motion in {"push", "pull"}, "visual": visual}
    clean = validate_cut({"scenes": output}, media, episode=bool(chapters))
    episode_plan = dict(draft.get("editorial_plan") or {})
    episode_plan.pop('indexed_scene_refs',None)
    if indexed_refs:
        episode_plan['indexed_scene_refs'] = indexed_refs
    episode_plan.pop("visual_warnings", None)
    episode_plan["models"] = models
    if warnings:
        episode_plan["visual_warnings"] = warnings
    if video_style:
        episode_plan["video_style"] = video_style
    return {**draft, **clean, "chapter_starts": styled_markers, "editorial_plan": episode_plan}
