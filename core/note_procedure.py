"""Keep a tutorial's cited instructions separate from generated commentary."""

import re
from decimal import Decimal


def _quantities(text):
    result = {}
    pattern = r'(\d+(?:\.\d+)?)\s*(千克|毫升|kg|ml|克|g|勺|碗|个)\s*([\u3400-\u9fff]{1,12}?)(?=[，、；。\s（(]|和|及|$)'
    for value, unit, ingredient in re.findall(pattern, text, re.I):
        result.setdefault(ingredient, set()).add((str(Decimal(value).normalize()), unit.lower()))
    return result


def note_procedure(photos):
    """Extract ordered source instructions without asking a model to rewrite them.

    Numbered body instructions take precedence. Otherwise use the ordered text
    observed on process photographs. Conflicting amounts require another source.
    """
    bodies, images = {}, {}
    for photo in photos:
        body = str(photo.get('source_summary') or '').strip()
        if body:
            bodies.setdefault(body, photo)
        text = str(photo.get('source_image_text') or '').strip()
        if text:
            images.setdefault(text, photo)
    quantities = [_quantities(text) for text in (*bodies, *images)]
    ingredients = set().union(*(set(q) for q in quantities)) if quantities else set()
    conflicts = []
    for ingredient in sorted(ingredients):
        values = [q[ingredient] for q in quantities if ingredient in q]
        if len(values) > 1 and all(len(v) == 1 for v in values) and len(set.union(*values)) > 1:
            conflicts.append(ingredient)
    steps = []
    numbered = r'^\s*(?:\d+(?:\ufe0f?\u20e3|[、.．)）])|[（(]\d+[)）])\s*'
    for text, photo in bodies.items():
        for line in text.splitlines():
            if re.match(numbered, line):
                steps.append({'text': re.sub(numbered, '', line).strip(),
                              'source_report_id': photo.get('source_report_id', ''), 'field': 'summary'})
    if not steps:
        for text, photo in images.items():
            if photo.get('photo_role') != 'process':
                continue
            for line in text.splitlines():
                if line.strip():
                    steps.append({'text': line.strip(), 'source_report_id': photo.get('source_report_id', ''),
                                  'field': 'image_text', 'image': photo.get('path', '')})
    return {'steps': steps, 'conflicts': conflicts}
