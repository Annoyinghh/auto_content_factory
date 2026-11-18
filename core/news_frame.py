"""Source-first broadcast frame: real media inside, deterministic motion outside."""

from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageOps


def frame_box(width, height, asset=None):
    portrait = asset and asset.get('kind')=='video' and asset.get('height',0)>asset.get('width',0)>0
    ratios = ((.035,.15,.43,.87) if portrait else (.03,.13,.97,.88)) if width>height else (.055,.21,.945,.65)
    if width < height and asset and asset.get('diagram'):
        ratios = (.055,.21,.945,.83)
    return tuple(round(size * ratio / 2) * 2 for size, ratio in zip((width, height, width, height), ratios))


def frame_slide(case, index, width, height, platform, *, video_overlay=False):
    from core.visual_engine import get_font, wrap_text

    scene = case['scenes'][index]
    asset = scene.get('asset') or {}
    wide = width > height
    unit = height / (1080 if wide else 1920)
    ink, muted, accent = '#F2EEE6', '#A9A8A1', '#D8C99F'
    image = Image.new('RGBA', (width, height), '#191A19')
    draw = ImageDraw.Draw(image)
    margin = round(width * .03)
    x0,y0,x1,y1 = frame_box(width,height,{**asset, 'diagram':bool(scene.get('diagram'))})

    def block(text, x, y, size, max_width, rows=1, color=ink, bold=False):
        font = get_font(max(10,round(size*unit)),bold)
        lines = wrap_text(' '.join(str(text).split()),font,max_width,draw)
        for n,line in enumerate(lines[:rows]):
            if n == rows-1 and len(lines)>rows:
                while line and draw.textlength(line+'…',font=font)>max_width:line=line[:-1]
                line += '…'
            draw.text((x,y),line,font=font,fill=color)
            y += round(max(10,size*unit)*1.35)
        return y

    chapter=scene.get('chapter',1)
    title=scene.get('title') or scene.get('chapter_title') or case['title']
    block(case['title'],margin,round(height*.028),25 if wide else 29,width-2*margin,color=muted)
    block(f'{chapter:02d}  '+title,margin,round(height*.065),41 if wide else 47,
          width-2*margin,1 if wide else 3,bold=True)
    draw.rectangle((x0,y0,x1-1,y1-1),fill='#111211')
    if video_overlay:
        image.paste((0,0,0,0),(x0,y0,x1,y1))
    elif asset.get('preview'):
        with Image.open(asset['preview']) as original:
            photo=ImageOps.contain(original.convert('RGBA'),(x1-x0,y1-y0))
            image.alpha_composite(photo,(x0+(x1-x0-photo.width)//2,y0+(y1-y0-photo.height)//2))
    else:
        evidence=scene.get('evidence') or []
        quote=next((row.get('quote','') for row in evidence if row.get('quote')), '')
        pad=round((x1-x0)*.09)
        draw.rectangle((x0,y0,x1-1,y1-1),fill='#E8E4D9')
        block('报道摘录' if quote else '解说',x0+pad,y0+pad,26,x1-x0-2*pad,color='#686458')
        block(quote or scene['text'],x0+pad,y0+pad+round(72*unit),46 if wide else 43,
              round((x1-x0-2*pad)*.9),6 if wide else 9,color='#262823')
    if wide and x1 < width*.5:
        # Keep the whole portrait source beside its actual cited passage.
        left=round(width*.485);column=round(width*.45)
        draw.line((left,round(height*.24),left+round(65*unit),round(height*.24)),fill=accent,width=3)
        quote=next((row.get('quote','') for row in scene.get('evidence',[]) if row.get('quote')), '')
        block('原文依据' if quote else '本段解说',left,round(height*.27),25,column,color=muted)
        block(quote or scene['text'],left,round(height*.34),43,column,7)
    evidences=scene.get('evidence') or []
    evidence=evidences[0] if evidences else {}
    page=asset.get('source_page') or asset.get('page_url') or evidence.get('url','')
    domain=urlsplit(page).hostname or ''
    label=asset.get('caption_label') or ('官方原网页' if asset.get('capture_kind')=='official_notice' else
          '资料图解' if scene.get('diagram') else {'video':'原片节选','image':'原图'}.get(asset.get('kind'),'报道摘录' if evidence else '解说'))
    author=asset.get('author','')
    source=(asset.get('source_publisher') or (author if author!='原文作者未标注' else '')
            or evidence.get('source') or scene.get('chapter_source') or domain)
    if wide:
        block(label+'  /  '+(source or domain),x0,round(height*.958),21,x1-x0,color=muted)
    else:
        block(label+' · '+source,x0,round(height*.93),26,x1-x0,color=muted)
    return image


def motion_filter(width, height, fps, duration, offset, platform, asset=None):
    """FFmpeg suffix for a moving marker outside the source viewport."""
    x0,y0,x1,y1=frame_box(width,height,asset)
    unit=height/(1080 if width>height else 1920)
    length=max(12,round(68*unit));thickness=max(2,round(2*unit))
    accent='D8C99F'
    span=max(1,x1-x0-length)
    return (f'[framed];color=c=0x{accent}:s={length}x{thickness}:r={fps}:d={duration:.6f}[marker];'
            f'[framed][marker]overlay=x=\'{x0}+mod((t+{offset:.6f})*{round(100*unit)},{span})\':'
            f'y={y0-round(15*unit)}:shortest=1')
