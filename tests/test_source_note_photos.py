import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from test_daily import scratch_dir


class SourceNotePhotosTests(unittest.TestCase):
    def test_manual_selection_keeps_each_original_recipe_together(self):
        from core.note_images import selected_note_photos
        with scratch_dir() as root:
            assets=[]
            for i in range(4):
                p=root/f'{i}.jpg'
                Image.new('RGB',(1080,1440),(i*50,80,120)).save(p)
                assets.append({'path':str(p),'source_report_id':'recipe-a' if i<2 else 'recipe-b'})
            with patch('core.material_search.saved_materials',return_value=assets), \
                    patch('core.note_images.describe_photo',return_value='真实步骤'):
                bundles=selected_note_photos(root,[a['path'] for a in assets],'美食教程',2)
            self.assertEqual([{c['source_report_id'] for _,c in b} for b in bundles],[{'recipe-a'},{'recipe-b'}])

    def test_auto_notes_use_separate_originals_and_require_a_visible_process(self):
        from core.browser_cases import _work, store_browser_reference
        from core.note_images import collect_source_note_photos
        from core.material_search import saved_materials

        def work(identity):
            return _work({'id':identity,'type':'normal','title':'家常饭教程',
                'desc':'先备好食材，炒熟鸡蛋，再加入番茄。'*4,
                'url':'https://www.xiaohongshu.com/explore/'+identity,
                'images':[{'url':f'https://sns-webpic-qc.xhscdn.com/notes/{identity}{i}!webp'} for i in range(4)]},photo_notes=True)

        downloads=[]
        def download(url,path,limit):
            downloads.append(url)
            colour=int(url.split('!')[0][-1])*45 + (5 if '6a0c' in url else 15)
            Image.new('RGB',(1080,1440),(colour,70,140)).save(path,format='WEBP')

        roles=['overview','detail','process','process','overview','detail','process','other']
        views=[{'visible':f'实拍画面{i}','relevant':True,'role':role,'text':f'步骤图原字{i}\n下一步实际操作'} for i,role in enumerate(roles)]
        with scratch_dir() as root, patch('core.source_media._get_file',side_effect=download), \
                patch('core.note_images.inspect_photo',side_effect=views) as inspect:
            ids=[store_browser_reference(root,work(n)) for n in (
                '6a0c1b78000000000702fe3a','6a058bfb000000003501faca')]
            bundles=collect_source_note_photos(root,'家常饭教程',2,ids)
            self.assertEqual([len(b) for b in bundles],[3,3])
            self.assertEqual([{c['source_report_id'] for _,c in b} for b in bundles],[{n} for n in ids])
            self.assertTrue(all(any(c['photo_role']=='process' for _,c in b) for b in bundles))
            self.assertEqual(len({c['source_media_id'] for b in bundles for _,c in b}),6)
            self.assertEqual(len(saved_materials(root)),6)
            self.assertTrue(all(c['rights']=='unverified' for b in bundles for _,c in b))
            self.assertFalse(list(root.rglob('*.download')))
            self.assertTrue(all(Path(c['path']).is_file() for b in bundles for _,c in b))
            self.assertTrue(all(c['source_image_text'].startswith('步骤图原字') for b in bundles for _,c in b))
            calls=inspect.call_count
            repeated=collect_source_note_photos(root,'家常饭教程',2,ids)
            self.assertEqual(inspect.call_count,calls)
            self.assertEqual([[c['source_media_id'] for _,c in b] for b in repeated],
                             [[c['source_media_id'] for _,c in b] for b in bundles])

    def test_auto_notes_do_not_substitute_a_generic_library_when_originals_are_missing(self):
        from core.note_images import collect_source_note_photos
        with scratch_dir() as root:
            with self.assertRaisesRegex(ValueError,'同题原文'):
                collect_source_note_photos(root,'做饭教程',2,[])
