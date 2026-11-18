"""Narration-bound explanatory motion; never presented as camera footage."""
import math
import re
from PIL import Image, ImageDraw, ImageFont
from config import FONT_BOLD, FONT_REGULAR

KINDS = ('chip_grid', 'percent_bars', 'parallel_flow', 'result_split')


def specification(text):
    if re.search(r'同步|并行|同时', text) and '代码生成' in text and '学习' in text:
        return 'parallel_flow', ['代码生成', '学习']
    results = re.findall(r'(\d+)\s*项\s*(?:使用的是|使用了?|用了?|则采用了?|采用了?)\s*'
                         r'(?:谷歌)?\s*(自研芯片|Ironwood(?:\s*TPU)?|英伟达\s*GPU)', text)
    if len(results) == 2 and all(0 < int(count) <= 20 for count,_ in results):
        return 'result_split', [f'{count} 项 {name}' for count,name in results]
    rates = [m[0] for m in re.finditer(r'(?<![\d.])\d+(?:\.\d+)?\s*[%％]', text)
             if 0 <= float(re.search(r'\d+(?:\.\d+)?', m[0])[0]) <= 100]
    # Rates are separate indicators, not assumed comparable causes or trends.
    if rates:
        return 'percent_bars', list(dict.fromkeys(rates))[:3]
    chips = re.findall(r'(\d[\d,，]*\s*(?:颗|块))\s*(?:用于[^，。；]{1,18}?的\s*)?'
                       r'((?:(?:NVIDIA|英伟达)\s*)?(?:(?:Ironwood|GB300)\s*(?:芯片|TPU|GPU)?|芯片|TPU|GPU))', text, re.I)
    if chips:
        return 'chip_grid', list(dict.fromkeys(f'{count} {kind}' for count,kind in chips))[:3]
    return None


def visual_for(shot):
    # Never animate a number that lacks an actual source excerpt.
    evidence = ' '.join(c.get('quote', '') for c in shot.get('evidence', []))
    found = specification(shot['text'])
    if not found or not evidence:
        return None
    kind, labels = found
    numbers = re.findall(r'\d+(?:\.\d+)?', ''.join(labels).replace(',', '').replace('，', ''))
    source_numbers = re.findall(r'\d+(?:\.\d+)?', evidence.replace(',', '').replace('，', ''))
    if any(value not in source_numbers for value in numbers):
        return None
    if kind == 'parallel_flow' and not all(word in evidence for word in labels):
        return None
    if kind == 'result_split':
        source = specification(evidence)
        if not source or source[0] != kind or [re.sub(r'\s+', '', x) for x in source[1]] != [re.sub(r'\s+', '', x) for x in labels]:
            return None
    return {'layout': kind, 'labels': labels, 'start': 0, 'framing': 'fit',
            'intent': {'chip_grid': '芯片规模示意', 'percent_bars': '原文比例数据',
                       'parallel_flow': '并行任务示意', 'result_split': '测试结果组成'}[kind],
            'reason': '依据口播及其原文引用绘制的解释动画，非实拍'}


def narration_parts(shot):
    """Switch diagrams at complete spoken sentences, keeping every source word."""
    parts, prefix = [], ''
    for text in re.findall(r'[^。！？!?]+[。！？!?]*', shot['text']):
        visual = visual_for({**shot, 'text':text})
        if visual:
            parts.append({'text':prefix+text, 'visual':visual})
            prefix = ''
        elif parts:
            parts[-1]['text'] += text
        else:
            prefix += text
    return parts if len(parts)>1 else [{'text':shot['text'],'visual':shot['visual']}]


def rate_name(narration, label):
    at = narration.find(label)
    phrase = re.split(r'[，。；！？]', narration[:at])[-1].strip()[-18:]
    phrase = re.sub(r'(?:达到了?|达到|达|为|是)$', '', phrase).strip()
    if re.fullmatch(r'(?:比|高于|低于|超过)', phrase) and '目标' in narration[at:at+18]:
        return '目标准确率' if '准确率' in narration else '目标值'
    return phrase or '原文比例'


def label_cues(labels, narration, duration):
    """Sentence-level narration is timed by TTS; stagger its labels in spoken order."""
    compact = re.sub(r'\s+', '', narration)
    cues = []
    for index, label in enumerate(labels):
        at = compact.find(re.sub(r'\s+', '', label))
        # First object establishes the scene; subsequent labels enter with their clause.
        cues.append(0. if not index else min(duration-.5, max(cues[-1]+.35,
                    duration*at/max(1,len(compact)) if at >= 0 else duration*index/len(labels))))
    return cues


def frames(kind, labels, narration, duration, fps, width, height):
    """Render at video resolution with continuous restrained motion."""
    wide = width > height
    unit = width / (1280 if wide else 720)
    pad = int(width * .07)
    font = ImageFont.truetype(FONT_BOLD, int(34 * unit))
    small = ImageFont.truetype(FONT_REGULAR, int(22 * unit))
    large = ImageFont.truetype(FONT_BOLD, int(60 * unit))
    ink, muted, amber, mint = '#F4EFE5', '#A4A8A7', '#E7B96B', '#85BBA8'
    base = Image.new('RGB', (width, height), '#182127')
    pen = ImageDraw.Draw(base)
    pen.text((pad, int(height * .09)), {'chip_grid':'计算规模', 'percent_bars':'数据解读',
             'parallel_flow':'两项任务同步进行', 'result_split':'成绩来自哪些芯片'}[kind], font=font, fill=ink)
    pen.text((pad, int(height * .88)), '依据原文的结构示意 · 非实拍', font=small, fill=muted)
    pen.line((pad, int(height*.16), width-pad, int(height*.16)), fill='#3A464B', width=2)
    cues = label_cues(labels, narration, duration)
    for frame in range(math.ceil(duration * fps)):
        t = frame / fps
        image = base.copy(); draw = ImageDraw.Draw(image)
        reveal = min(1., t / 1.3); ease = 1-(1-reveal)**3
        if kind == 'chip_grid':
            # Symbolic tiles: the annotation explicitly rules out one tile per chip.
            current = max(index for index, cue in enumerate(cues) if t >= cue)
            label = labels[current]
            local = max(0., t-cues[current])
            ease = 1-(1-min(1.,local/1.3))**3
            draw.text((pad, int(height*.22)), label, font=large if len(label)<15 else font, fill=amber)
            # Isometric symbolic packages; light traverses the array continuously.
            side = 7
            hx = min(width*.052, height*.045); hy = hx*.48
            top = height*.38
            depth = max(4,int(10*unit))
            cells=sorted(((r,c) for r in range(side) for c in range(side)),key=lambda rc:sum(rc))
            for n,(r,c) in enumerate(cells):
                x,y=width*.5+(c-r)*hx,top+(c+r)*hy
                lit=n<round(side*side*ease)
                active=lit and (n+int(local*5))%13<2
                color=amber if active else mint if lit else '#29353B'
                draw.polygon([(x-hx*.82,y),(x,y+hy*.82),(x,y+hy*.82+depth),(x-hx*.82,y+depth)],fill='#365653')
                draw.polygon([(x+hx*.82,y),(x,y+hy*.82),(x,y+hy*.82+depth),(x+hx*.82,y+depth)],fill='#263E40')
                draw.polygon([(x,y-hy*.82),(x+hx*.82,y),(x,y+hy*.82),(x-hx*.82,y)],fill=color)
            draw.text((pad, int(height*.81)), '图块表示规模概念，不表示实际拓扑或逐颗数量', font=small, fill=muted)
        elif kind == 'percent_bars':
            if len(labels)==1:
                value=float(re.search(r'\d+(?:\.\d+)?',labels[0])[0])/100
                radius=min(width*.30,height*.25);cx,cy=width*.5,height*.50
                circle=(cx-radius,cy-radius,cx+radius,cy+radius)
                stroke=max(10,int(22*unit))
                draw.ellipse(circle,outline='#303F46',width=stroke)
                draw.arc(circle,start=-90,end=-90+360*value*ease,fill=mint,width=stroke)
                angle=math.radians(-90+360*value*ease)
                mx,my=cx+radius*math.cos(angle),cy+radius*math.sin(angle)
                draw.ellipse((mx-stroke*.3,my-stroke*.3,mx+stroke*.3,my+stroke*.3),fill=ink)
                draw.text((cx,cy),labels[0],font=large,fill=amber,anchor='mm')
                draw.text((cx,height*.23),rate_name(narration,labels[0]),font=small,fill=muted,anchor='mm')
                draw.text((cx,height*.80),'完整圆环表示 100%',font=small,fill=muted,anchor='mm')
                yield image.tobytes()
                continue
            gap = height*.54/max(2,len(labels))
            for i,label in enumerate(labels):
                y = height*.24+i*gap
                if t < cues[i]:
                    continue
                ease = 1-(1-min(1.,(t-cues[i])/1.1))**3
                draw.text((pad,y),rate_name(narration,label),font=small,fill=muted)
                draw.text((width-pad,y), label,font=font,fill=amber,anchor='ra')
                yy=y+int(49*unit); right=width-pad
                draw.rounded_rectangle((pad,yy,right,yy+int(22*unit)),radius=5,fill='#303F46')
                value=float(re.search(r'\d+(?:\.\d+)?',label)[0])/100
                length=(right-pad)*value*ease
                if length>1:draw.rounded_rectangle((pad,yy,pad+length,yy+int(22*unit)),radius=5,fill=mint)
                if length>8:
                    # A moving locator traces the filled range without changing the measured value.
                    marker = pad+length
                    radius = max(3,int(5*unit))
                    draw.ellipse((marker-radius,yy+int(11*unit)-radius,marker+radius,yy+int(11*unit)+radius),fill=ink)
                draw.text((pad,yy+int(30*unit)),'0%',font=small,fill=muted)
                draw.text((right,yy+int(30*unit)),'100%',font=small,fill=muted,anchor='ra')
        elif kind == 'result_split':
            counts = [int(re.match(r'\d+', label)[0]) for label in labels]
            total = sum(counts)
            cols = min(total, 10 if wide else 5)
            rows = math.ceil(total/cols)
            cell = min((width-2*pad)/cols, height*.36/rows)
            for n in range(total):
                x, y = pad+(n%cols)*cell, height*.30+(n//cols)*cell
                fill = (mint if n<counts[0] else amber) if n<round(total*ease) else '#29353B'
                draw.rounded_rectangle((x,y,x+cell*.78,y+cell*.78),radius=8,fill=fill)
                if n == int(t*1.6)%total:
                    draw.rounded_rectangle((x-3,y-3,x+cell*.78+3,y+cell*.78+3),radius=10,outline=ink,width=max(2,int(2*unit)))
            for i,label in enumerate(labels):
                draw.text((pad,height*(.66+i*.07)),label,font=font,fill=mint if i==0 else amber)
            draw.text((pad,int(height*.81)),'每格表示一项测试结果',font=small,fill=muted)
        else:
            for i,label in enumerate(labels):
                y=height*(.33+i*.25)
                draw.text((pad,y-int(45*unit)),label,font=font,fill=ink)
                left,right=pad,width-pad
                draw.line((left,y,right,y),fill='#3A5159',width=int(6*unit))
                for j in range(6):
                    x=left+((t*.22+j/6)%1)*(right-left)
                    size=int(10*unit)
                    draw.ellipse((x-size,y-size,x+size,y+size),fill=amber if i==0 else mint)
            draw.text((pad,int(height*.77)),'并行流程示意 · 不表示实测吞吐速度',font=small,fill=muted)
        yield image.tobytes()
