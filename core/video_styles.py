"""Production rules distilled from identifiable public video references."""

import json
import re
from functools import cache

from config import ASSETS_DIR


_DEPTH_DISTANCE = r"\d{1,5}(?:\.\d{1,2})?\s*(?:公里|米)"
_DEPTH_VALUE = rf"(?P<label>{_DEPTH_DISTANCE})(?!\s*(?:至|到|[-–—~～]|级|以[上下深]|多|左右))"
_DEPTH_READING = re.compile(
    rf"(?:地下|水下|钻(?:进)?[到至]|深达|(?:井深|总深度|垂直深度)(?:最终)?(?:为|达[到至]?|定格在)?)\s*{_DEPTH_VALUE}"
)
_LEAK_DEPTH = re.compile(rf"在\s*{_DEPTH_VALUE}(?:深)?处[^。！？；，]{{0,12}}井漏")
_DRILL_INTERVAL = re.compile(
    rf"(?:从|最后)\s*(?P<begin>{_DEPTH_DISTANCE})\s*(?:到|至)\s*(?P<end>{_DEPTH_DISTANCE})"
    r"\s*(?:的?[“‘]?(?:最后一公里|井段)|[^，。；！？]{0,10}(?:钻探|钻进|耗时|用时|用了))"
)


def depth_metres(label):
    value, unit = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(公里|米)", label).groups()
    return float(value) * (1000 if unit == "公里" else 1)


def depth_labels(narration):
    """Use measured locations, with an explicit depth context and unit."""
    matches = [*_DEPTH_READING.finditer(narration), *_LEAK_DEPTH.finditer(narration)]
    readings = []
    for match in matches:
        label = match["label"]
        prefix = narration[max(0, match.start() - 12):match.start()]
        suffix = narration[match.end("label"):match.end("label") + 12]
        if (re.search(r"(?:额定|设计|最大|理论|可|能|计划|预计|拟|目标|未|没(?:有)?|将)\s*$", prefix)
                or re.match(r"\s*的?(?:自动化)?钻机", suffix)):
            continue
        readings.append((match.start("label"), label))
    for sentence in re.finditer(r"[^。！？；]+", narration):
        if not re.search(r"钻到|钻至|井深|井段|钻进|钻探|完钻|地表", sentence[0]):
            continue
        if not any(sentence.start() <= index < sentence.end() for index, _ in readings):
            continue
        for interval in _DRILL_INTERVAL.finditer(sentence[0]):
            for field in ("begin", "end"):
                readings.append((sentence.start() + interval.start(field), interval[field]))
    labels = {}
    for _, label in sorted(readings):
        metres = depth_metres(label)
        if 0 < metres <= 100_000:
            labels.setdefault(metres, label)
    return list(labels.values())[:3]


DRILLING_FLOW_SOURCES = (
    ("SLB · 钻井液循环", "https://www.slb.com/resource-library/oilfield-review/defining-series/defining-drilling-fluids"),
    ("SLB · 井漏和堵漏材料", "https://glossary.slb.com/en/terms/l/lost-circulation_material"),
)


def drilling_flow_cues(narration):
    """Require a mechanism or an observed event, rather than a list of risks."""
    cues = []
    for clause in re.finditer(r"[^。！？；，]+", narration):
        text = clause[0]
        if re.search(r"可能|也许|未发生|没有发生|是否|防止|预防|(?:遭遇|发生)过?哪些", text):
            continue
        if ((match := re.search(r"钻井液", text))
                and re.search(r"循环|返出|返流|(?:返|回)到地面|钻杆内|钻具水眼", text)):
            cues.append((clause.start() + match.start(), "circulate", match[0]))
        if ((match := re.search(r"井漏|(?:钻井液)?漏失", text))
                and re.search(r"遭遇|发生|漏入|流入|漏进|回流减少|返出量下降|无法继续", text)):
            cues.append((clause.start() + match.start(), "loss", match[0]))
        if ((match := re.search(r"堵漏浆|带(?:着)?钻具(?:实施)?堵漏|带钻堵漏", text))
                and re.search(r"用|实施|提出|配制|调配|注入|泵|堵住|坚持|方案", text)):
            cues.append((clause.start() + match.start(), "plug", match[0]))
    seen = set()
    clean = []
    for cue in sorted(cues):
        if cue[1] not in seen:
            seen.add(cue[1])
            clean.append(cue)
    return clean


@cache
def _catalog():
    return json.loads((ASSETS_DIR / "video_styles.json").read_text(encoding="utf-8"))


def video_styles(platform):
    from core.spoken_style import PLATFORMS
    if platform not in PLATFORMS:
        return []
    speech = PLATFORMS[platform]
    styles = _catalog().get(platform, [])
    if platform == 'xiaohongshu':
        styles = [{'id':'explain', 'name':speech['name'], 'keywords':[], 'diagram':'steps', 'pace':46,
                   'opening':speech['opening'], 'body':speech['body'], 'closing':speech['closing'],
                   'materials':'同题原片、原图、官方出处；用图解说明原文已支持的关系。',
                   'reference':{'adapt':'按真实口播的方法组织表达，素材逐句核对，不预设同题参考一定存在。'}}]
    return [{**style, 'speech':speech} for style in styles]


def note_styles():
    return _catalog()["xiaohongshu_notes"]


def select_note_style(topic):
    return next((style for style in note_styles() if any(word in topic.casefold() for word in style["keywords"])), None)


def select_video_style(platform, title, requested=""):
    styles = video_styles(platform)
    if requested:
        for style in styles:
            if style["id"] == requested:
                return style
        raise ValueError("请选择这个平台支持的视频类型。")
    return next((style for style in styles if any(term in title for term in style["keywords"])),
                styles[0] if styles else None)


def original_video_style():
    return {"id": "original", "name": "原创生活与过程记录", "diagram": "steps", "pace": 35,
            "opening": "从创作说明中一个实际动作或具体处境开始，让相关实拍先出现。",
            "body": "围绕作者给出的过程推进，用真实视频承担动作，图片缓慢推拉。不同来源的画面不冒充同一段经历。",
            "closing": "回到开头的小问题，停在已提供的结果或一个具体建议上。",
            "materials": "作者说明、已选实拍和可核对资料。没有出现的动作不编造；不做新闻播报。",
            "reference": {"adapt": "按创作说明组织画面与口播，不声称使用了平台案例。"}}


def style_context(style):
    if not style:
        return None
    return {key: style[key] for key in ("name", "opening", "body", "closing", "materials", "diagram")} | {
        "adapt": style["reference"]["adapt"],
        **({'speech':style['speech']} if style.get('speech') else {}),
    }


def chapter_video_style(platform, episode_style, chapter, *, fixed=False):
    if not episode_style or fixed or episode_style["id"] != "roundup":
        return episode_style
    topic = " ".join([chapter["story"]["title"], chapter.get("question", ""),
                      *(report["title"] for report in chapter.get("reports", ()))])
    return select_video_style(platform, topic)


def diagram_labels(layout, labels, narration):
    """Keep visual labels inside the verified narration and in spoken order."""
    from core.news_diagrams import KINDS, specification
    if layout in KINDS:
        expected = specification(narration)
        return list(labels) if expected and expected == (layout, labels) else []
    if layout == "depth":
        expected = iter(depth_labels(narration))
        return list(labels) if isinstance(labels, (list, tuple)) and labels and all(
            label in expected for label in labels) else []
    if layout == "drilling_flow":
        expected = [label for _, _, label in drilling_flow_cues(narration)]
        return expected if labels == expected else []
    if not isinstance(labels, (list, tuple)) or not 2 <= len(labels) <= 4:
        return []
    if layout == "comparison" and (len(labels) != 2 or not re.search(r"同[一等样]|均在|相同|统一|同场", narration)):
        return []
    if layout == "timeline" and not all(isinstance(label, str) and re.search(
            r"\d[年月日时]|\d{1,2}:\d{2}|当天|随后|此前|之后|此前一天|次日|当晚", label) for label in labels):
        return []
    end = 0
    clean = []
    for label in labels:
        if not isinstance(label, str) or not 2 <= len(label) <= 16 or label in clean:
            return []
        if layout == "steps" and re.fullmatch(r"\s*[+−-]?\d+(?:[.，,]\d+)*\s*(?:米|公里|吨|摄氏度|兆帕|天|%|％|℃)?\s*", label):
            return []
        index = narration.find(label, end)
        if index < 0:
            return []
        end = index + len(label)
        clean.append(label)
    return clean


def timeline_labels(narration):
    dates = r"(?:\d{4}年(?:\d{1,2}月(?:\d{1,2}[日号])?)?|\d{1,2}月\d{1,2}[日号])"
    labels = []
    for clause in re.split(r"[。！？；，]", narration):
        match = re.match(rf"({dates})([^\d]{{2,}})$", clause.strip())
        if match:
            label = match[0][:16].rstrip()
            if len(label) >= len(match[1]) + 2:
                labels.append(label)
    return diagram_labels("timeline", labels[:4], narration)


def sequence_labels(narration):
    clauses = re.split(r"[，。；！？]", narration)
    steps = [clause for clause in clauses if re.match(r"(?:先|再|随后|接着|最终)", clause)
             and 2 <= len(clause) <= 16]
    if len(steps) >= 2:
        prior = clauses[:clauses.index(steps[0])]
        if prior and 4 <= len(prior[-1]) <= 16:
            steps.insert(0, prior[-1])
    return diagram_labels("steps", steps[:4], narration)
