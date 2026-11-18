import io
import unittest
from unittest.mock import patch

from PIL import Image

from core.directing import _choose_frames, direct_cut
from core.editing import render_case
from core.video_styles import select_video_style
from test_daily import scratch_dir


class DirectingTest(unittest.TestCase):
    def test_an_explicit_live_action_cannot_be_replaced_with_a_still_object_photo(self):
        from core.directing import _ground_news_photo_intents
        plan=[{'index':0,'layout':'footage','intent':'机械手'}]
        with patch('core.directing.chat_json',side_effect=AssertionError('A still cannot establish live motion')):
            _ground_news_photo_intents(plan,[{'title':'机械手抓取零件','text':'画面中机械手正在抓取零件。'}],'writer','展会')
        self.assertEqual(plan[0]['layout'],'evidence')

    def test_a_generic_event_target_cannot_approve_a_specific_device_photo(self):
        from core.directing import _ground_news_photo_intents
        plan=[{'index':0,'layout':'footage','intent':'进博会现场'}]
        with patch('core.directing.chat_json',return_value={'shots':[{'index':0,'intent':'进博会现场'}]}):
            _ground_news_photo_intents(plan,[{'title':'6G终端原型机','text':'进博会现场展示6G终端原型机。'}],
                                       'writer','进博会激活消费新活力')
        self.assertEqual(plan[0]['layout'],'evidence')

    def test_literal_news_photo_observation_does_not_see_the_requested_product(self):
        import json
        encoded=io.BytesIO();Image.new('RGB',(744,163),'gray').save(encoded,'JPEG')
        frame={'title':'展区资料','kind':'image','media':'photo','wide':True,'start':0,
               '_verification':encoded.getvalue()}
        replies=[{'frame':0},{'observations':['照片里有自动扶梯和参观者。']},
                 {'match':False,'reason':'观察中没有通信终端。'}]
        with patch('core.directing.chat_json',side_effect=replies) as model:
            picks,_=_choose_frames([{'index':0,'layout':'footage','intent':'6G终端原型机','literal_subject':True}],
                                   [frame],encoded.getvalue(),'vision','bilibili')
        self.assertNotIn('6G',json.dumps(model.call_args_list[1].args[1],ensure_ascii=False))
        self.assertFalse(picks)
        self.assertEqual(model.call_count,3)

    def test_news_photo_target_is_the_literal_product_not_a_generic_event(self):
        from core.directing import _ground_news_photo_intents
        shots=[{'title':'高通6G终端原型机','text':'展台展示6G终端原型机。'}]
        plan=[{'index':0,'layout':'footage','intent':'进博会现场'}]
        with patch('core.directing.chat_json',return_value={'shots':[{'index':0,'intent':'6G终端原型机'}]}) as model:
            _ground_news_photo_intents(plan,shots,'writer','进博会激活消费新活力')
        self.assertEqual(plan[0]['intent'],'6G终端原型机')
        self.assertEqual(set(model.call_args.args[1]),{'shots'})

    def test_indexed_news_tries_the_chapters_real_photos_before_an_explanatory_card(self):
        shots=[{'title':'展台机器人','text':'展台上摆放着一台机器人。','media':'','zoom':False},
               {'title':'资料来源','text':'后续变化仍需核对原文。','media':'','zoom':False}]
        media=[{'page_url':f'video{i}','kind':'video','duration':12,'source_report_id':'r','origin':'article'} for i in range(6)]
        media.extend([{'page_url':'photo','kind':'image','title':'本章机器人实景','source_report_id':'r','origin':'article'},
                      {'page_url':'other','kind':'image','title':'另一事件','source_report_id':'other','origin':'article'}])
        evidence=[{'index':i,'layout':'evidence','motion':'cut','intent':'展台机器人'} for i in range(2)]
        photo_plan=[{**row,'layout':'footage'} for row in evidence]
        pick={'media':'photo','kind':'image','start':0,'framing':'fit','signature':'seen-photo',
              'intent':'展台机器人','reason':'已核对实际照片'}
        chapters=[{'story':{'title':'现场'},'reports':[{'id':'r'}]}]
        with scratch_dir() as root,patch('core.grounded_news.select_clips',return_value=(evidence,{})), \
                patch('core.directing._ground_news_photo_intents'), \
                patch('core.directing._design',side_effect=[photo_plan,[evidence[1]]]), \
                patch('core.directing._contact_sheet',return_value=([],b'')) as sheet, \
                patch('core.directing._choose_frames',return_value=({0:pick},[])):
            result=direct_cut({'scenes':shots,'chapter_starts':[{'scene':0,'title':'现场'}]},media,
                'bilibili',root,{'design':'writer','editing':'vision'},chapters=chapters,
                source_indexes=[{'media':'video0','segments':[]}])
        self.assertEqual([row['page_url'] for row in sheet.call_args.args[0]],['photo'])
        self.assertEqual(result['scenes'][0]['media'],'photo')
        self.assertIn(result['scenes'][0]['motion'],('push','pull'))
        self.assertNotIn('end',result['scenes'][0]['visual'])
        self.assertNotIn('clips',result['scenes'][0]['visual'])
        self.assertEqual(result['scenes'][1]['visual']['layout'],'evidence')

    def test_recipe_intent_retry_keeps_valid_fields_and_skips_abstract_intro(self):
        from core.directing import _ground_recipe_intents
        shots=[{'text':'锅中倒入蛋液。'},{'text':'下蒜末炒匀即可出锅。'},
               {'text':'其特点是简单方便无需后续调味。'}]
        plan=[{'index':0,'intent':'平底锅倒蛋'},{'index':1,'intent':'放入锅内炒熟'},
              {'index':2,'intent':'成品菜肴'}]
        with patch('core.directing.chat_json',side_effect=[{'shots':[
                {'index':0,'intent':'倒入蛋液'},{'index':1,'intent':'锅内炒熟'}]},
                {'shots':[{'index':1,'intent':'炒匀'}]}]) as model:
            _ground_recipe_intents(plan,shots,'writer')
        self.assertEqual([row['intent'] for row in plan],['倒入蛋液','炒匀','成品菜肴'])
        self.assertEqual(model.call_args.args[1]['shots'],[{'index':1,'text':shots[1]['text']}])

    def test_putting_ingredients_in_cannot_use_a_plating_out_sequence(self):
        from core.directing import _verify_sequence
        observations=['锅内有炒熟的西红柿鸡蛋。','厨师舀起锅中的菜。','厨师把食物倒入白色盘子。']
        with patch('core.directing.chat_json',side_effect=[{'observations':observations},
                {'match':True,'reason':'出现西红柿和倒入动作。'}]):
            result=_verify_sequence((b'a',b'b',b'c'),'倒入切好的西红柿颗粒','vision')
        self.assertFalse(result['match'])
        adding=['一只手端着装有西红柿颗粒的盘子。','把盘里的西红柿倒入锅中。','西红柿在锅中翻动。']
        with patch('core.directing.chat_json',side_effect=[{'observations':adding},
                {'match':True,'reason':'从盘中往锅里倒入西红柿。'}]):
            self.assertTrue(_verify_sequence((b'a',b'b',b'c'),'倒入切好的西红柿颗粒','vision')['match'])

    def test_wide_action_video_keeps_its_full_frame_in_a_portrait_export(self):
        encoded=io.BytesIO()
        Image.new('RGB',(1000,540),'orange').save(encoded,'JPEG')
        frame={'title':'完整操作画面','kind':'video','media':'clip','wide':True,'start':10,
               '_verification':encoded.getvalue(),'_sequence':[encoded.getvalue()]*3}
        with patch('core.directing._verify_sequence',return_value={'match':True,'description':'手与锅均可见。'}), \
                patch('core.directing.chat_json',side_effect=[{'frame':0},{'match':True}]) as model:
            picks,_=_choose_frames([{'index':0,'layout':'footage','intent':'倒入蛋液'}],
                                   [frame],encoded.getvalue(),'vision','douyin')
        self.assertEqual(picks[0]['framing'],'fit')
        self.assertEqual(model.call_count,1)

    def test_recipe_targets_keep_one_literal_action_and_do_not_invent_a_container(self):
        from core.directing import _ground_recipe_intents
        shots=[{'text':'锅中快速倒入蛋液，起锅前加蒜末提香。'},
               {'text':'翻炒均匀后加盐，再下蒜末炒匀即可出锅。'}]
        plan=[{'index':0,'intent':'平底锅倒蛋并加蒜','layout':'footage'},
              {'index':1,'intent':'加盐蒜末炒匀出锅','layout':'footage'}]
        with patch('core.directing.chat_json',return_value={'shots':[
                {'index':0,'intent':'倒入蛋液'},{'index':1,'intent':'炒匀'}]}):
            _ground_recipe_intents(plan,shots,'writer')
        self.assertEqual([row['intent'] for row in plan],['倒入蛋液','炒匀'])
        with patch('core.directing.chat_json',return_value={'shots':[{'index':0,'intent':'平底锅'}]}):
            with self.assertRaisesRegex(ValueError,'口播中的单一动作'):
                _ground_recipe_intents([{'index':0,'intent':'平底锅'}],shots[:1],'writer')

    def test_video_observation_does_not_receive_the_expected_action(self):
        from core.directing import _verify_sequence
        observations = ['勺内有油。', '碗放在锅旁，勺内有油。', '碗还在锅旁，勺内有油。']
        with patch('core.directing.chat_json', side_effect=[
                {'observations':observations}, {'match':False,'reason':'没有观察到泼油。'}]) as model:
            checked = _verify_sequence((b'first',b'middle',b'last'),'热油泼在蛋液上','editor')
        self.assertFalse(checked['match'])
        self.assertEqual(checked['observations'],observations)
        self.assertNotIn('热油泼在蛋液上',str(model.call_args_list[0]))
        self.assertNotIn('images',model.call_args_list[1].kwargs)

    def test_preparing_an_action_is_not_accepted_as_performing_it(self):
        encoded = io.BytesIO()
        Image.new('RGB',(744,163),'orange').save(encoded,'JPEG')
        frame = {'title':'泼油步骤','media':'clip','kind':'video','start':98.,'wide':True,
                 '_verification':encoded.getvalue(),'_sequence':[encoded.getvalue()]*3}
        observed = ['勺内有油。','装蛋液的碗在锅旁。','勺内仍有油，碗在锅旁。']
        with patch('core.directing.chat_json', side_effect=[
                {'frame':0},{'observations':observed},{'match':False,'reason':'准备动作，未见泼油。'}]):
            picks,errors = _choose_frames([{'index':0,'layout':'footage','intent':'热油泼在蛋液上'}],
                                         [frame],encoded.getvalue(),'editor','douyin')
        self.assertEqual((picks,errors),({},[]))

    def test_retry_reuses_verified_shot_and_retries_only_the_failed_shot(self):
        encoded = io.BytesIO()
        Image.new('RGB',(744,163),'orange').save(encoded,'JPEG')
        frames = [{'media':url,'kind':'video','start':0,'wide':True,'title':url,
                   '_verification':encoded.getvalue()} for url in ('clip-a','clip-b')]
        plan = [{'index':i,'layout':'footage','intent':intent} for i,intent in enumerate(('番茄','鸡蛋'))]
        completed = {}
        def save(index,pick):
            completed[index] = pick
        with patch('core.directing.chat_json',side_effect=[{'frame':0},{'match':True},RuntimeError('暂时离线')]):
            first, errors = _choose_frames(plan,frames,encoded.getvalue(),'editor','douyin',
                                           completed=completed,on_result=save)
        self.assertEqual(list(first),[0])
        self.assertEqual(list(completed),[0])
        self.assertTrue(errors)
        with patch('core.directing.chat_json',side_effect=[{'frame':1},{'match':True}]) as model:
            retried, errors = _choose_frames(plan,frames,encoded.getvalue(),'editor','douyin',
                                             completed=completed,on_result=save)
        self.assertEqual(model.call_count,2)
        self.assertEqual([retried[index]['media'] for index in range(2)],['clip-a','clip-b'])
        self.assertEqual(errors,[])

    def test_article_video_is_available_to_both_chapters_using_its_source(self):
        asset = {'kind':'video', 'page_url':'clip', 'title':'两种做法', 'duration':400,
                 'origin':'article', 'source_report_id':'r', 'chapters':[0]}
        names = ['混炒','蒜香','其他报道']
        chapters = [{'story':{'title':title}, 'question':f'{title}怎么做？', 'source_section':title, 'reports':[{'id':source}]}
                    for title,source in zip(names, ['r','r','other'])]
        draft = {'scenes':[{'title':title,'text':f'讲述{title}的操作。','media':'','zoom':False} for title in names],
                 'chapter_starts':[{'scene':index,'title':title} for index,title in enumerate(names)]}
        def design(_shots, _platform, _title, available, _model, **_kwargs):
            return [{'index':0, 'intent':'操作', 'layout':'evidence', 'motion':'cut'}]
        with scratch_dir() as root, patch('core.directing._design', side_effect=design), \
                patch('core.directing._contact_sheet', return_value=([],b'')), \
                patch('core.video_timeline.source_windows',return_value=[]) as locate:
            direct_cut(draft,[asset],'douyin',root,{'writing':'writer','design':'d','editing':'e'},chapters=chapters)
        self.assertEqual([call.args[2]['chapter'] for call in locate.call_args_list], names[:2])
        self.assertEqual(locate.call_args.args[2]['question'], '蒜香怎么做？')

    def test_deferred_result_does_not_replace_the_visible_action(self):
        from core.directing import _design
        scenes = [{'title':'推炒','text':'推动蛋液炒至适合口感即可出锅。'},
                  {'title':'装盘','text':'将食物倒入盘中。'}]
        reply = {'shots':[{'index':0,'intent':'推动蛋液出锅','layout':'footage','motion':'cut'},
                          {'index':1,'intent':'倒入盘中','layout':'footage','motion':'cut'}]}
        with patch('core.directing.chat_json',return_value=reply):
            plan = _design(scenes,'douyin','操作',['原片'],'director')
        self.assertEqual([row['intent'] for row in plan],['推动蛋液','倒入盘中'])

    def test_long_source_uses_brief_scoped_windows_and_retains_out_point(self):
        encoded = io.BytesIO()
        Image.new('RGB',(320,180),'orange').save(encoded,'JPEG')
        draft = {'scenes':[{'title':'切配','text':'把番茄切成小颗粒。','media':'','zoom':False},
                           {'title':'提醒','text':'按原文步骤操作。','media':'','zoom':False}]}
        design = [{'index':0,'layout':'footage','motion':'cut','intent':'切番茄'},
                  {'index':1,'layout':'evidence','motion':'cut','intent':'步骤提示'}]
        models = {'writing':'writer','design':'director','editing':'editor'}
        with scratch_dir() as root:
            asset = {'kind':'video','page_url':'https://example.com/full','title':'六种做法',
                     'path':str(root/'full.mp4'),'duration':502.5}
            windows = [{'shot_index':0,'start':44.,'end':52.,'text':'第一种切配'}]
            with patch('core.video_timeline.source_windows',return_value=windows) as locate, \
                    patch('core.directing._design',return_value=design), \
                    patch('core.directing._frame_jpeg',return_value=encoded.getvalue()), \
                    patch('core.directing.chat_json',side_effect=[{'frame':0},
                        {'observations':['刀刃在切番茄。']*3},
                        {'match':True,'reason':'能看到刀刃切番茄。'},{'match':True}]) as model:
                cut = direct_cut(draft,[asset],'douyin',root,models,brief={'notes':['只讲第一种']})
            self.assertEqual(locate.call_args.args[2]['notes'],['只讲第一种'])
            self.assertEqual(locate.call_args.args[3],'writer')
            visual = cut['scenes'][0]['visual']
            self.assertGreaterEqual(visual['start'],44)
            self.assertLessEqual(visual['end'],52)
            self.assertGreater(visual['end'],visual['start'])
            self.assertEqual(visual['framing'],'fit')
            for frame in model.call_args_list[1].kwargs['images']:
                with Image.open(io.BytesIO(frame)) as crop:
                    self.assertAlmostEqual(crop.width / crop.height,16/9,places=2)

    def test_frame_verification_uses_visible_action_not_adjacent_article_steps(self):
        encoded = io.BytesIO()
        Image.new('RGB',(744,163),'orange').save(encoded,'JPEG')
        frame = {'title':'先切片再切粒，随后打鸡蛋','kind':'video','media':'clip','start':3,'wide':True,
                 '_verification':encoded.getvalue()}
        def model(instruction, context, schema, **kwargs):
            if 'frame' in schema['properties']: return {'frame':0}
            return {'description':'正在把番茄切成小颗粒', 'match':'素材标题' not in context}
        with patch('core.directing.chat_json', side_effect=model):
            picks, _ = _choose_frames([{'index':0,'layout':'footage','intent':'西红柿切成小颗粒'}],
                                      [frame],encoded.getvalue(),'editor','douyin')
        self.assertEqual(picks[0]['start'],3)

    def test_rejected_early_frame_does_not_discard_later_action_at_same_camera_angle(self):
        encoded = io.BytesIO()
        Image.new('RGB',(744,163),'orange').save(encoded,'JPEG')
        frames = [{'title':'同一机位操作','kind':'video','media':'clip','start':offset,'wide':True,
                   'signature':(0,'101010'),'_verification':encoded.getvalue()} for offset in (0,3)]
        with patch('core.directing.chat_json', side_effect=[{'frame':0},{'match':False},{'frame':1},{'match':True}]):
            picks, errors = _choose_frames([{'index':0,'layout':'footage','intent':'切成小颗粒'}],
                                           frames,encoded.getvalue(),'editor','douyin')
        self.assertEqual(picks[0]['start'],3)
        self.assertEqual(errors,[])

    def test_single_frame_fallback_recovers_a_contact_sheet_miss(self):
        encoded = io.BytesIO()
        Image.new('RGB',(744,163),'orange').save(encoded,'JPEG')
        frame = {'title':'切番茄','kind':'video','media':'clip','start':0,'wide':True,
                 '_verification':encoded.getvalue()}
        with patch('core.directing.chat_json', side_effect=[{'frame':-1},{'match':True}]):
            picks, errors = _choose_frames([{'index':0,'layout':'footage','intent':'切番茄'}],
                                           [frame],encoded.getvalue(),'editor','douyin')
        self.assertEqual(picks[0]['media'],'clip')
        self.assertEqual(errors,[])

    def test_short_clip_sampling_includes_action_near_the_end(self):
        import subprocess
        from config import get_ffmpeg
        from core.directing import _contact_sheet
        with scratch_dir() as root:
            file = root/'two-actions.mp4'
            subprocess.run([get_ffmpeg(), '-v','error','-f','lavfi','-i','color=blue:160x120:r=24:d=1.5',
                '-f','lavfi','-i','color=red:160x120:r=24:d=0.5','-filter_complex','concat=n=2:v=1:a=0',
                '-c:v','libx264','-pix_fmt','yuv420p',str(file)],check=True,capture_output=True)
            frames, _ = _contact_sheet([{'kind':'video','title':'动作变化','page_url':'clip', 'path':str(file),'duration':2}])
            colors = []
            for frame in frames:
                with Image.open(io.BytesIO(frame['_verification'])) as photo:
                    colors.append(photo.convert('RGB').getpixel((80,60)))
            self.assertTrue(any(red > blue * 2 for red, green, blue in colors), colors)

    def test_real_action_footage_is_not_replaced_by_a_steps_diagram(self):
        shots = [{'title':'备菜', 'text':'先切番茄，再打散鸡蛋。', 'media':'', 'zoom':False},
                 {'title':'完成', 'text':'食材准备好了。', 'media':'', 'zoom':False}]
        plan = [{'index':0, 'layout':'footage', 'motion':'cut', 'intent':'切番茄'},
                {'index':1, 'layout':'evidence', 'motion':'cut', 'intent':'准备完成'}]
        asset = {'kind':'video', 'title':'切番茄', 'page_url':'clip', 'duration':10}
        def choose(decisions, *args, **kwargs):
            if decisions[0]['layout'] != 'footage': return {}, []
            return {0:{'media':'clip','start':0,'signature':'cutting','framing':'fit','reason':'动作匹配'}}, []
        with scratch_dir() as root, patch('core.directing._design', return_value=plan), \
                patch('core.directing.sequence_labels', side_effect=lambda text:['切番茄','打散鸡蛋'] if '先' in text else []), \
                patch('core.directing._contact_sheet', return_value=([], b'')), \
                patch('core.directing._choose_frames', side_effect=choose):
            result = direct_cut({'scenes':shots}, [asset], 'douyin', root, {'design':'d','editing':'e'},
                                video_style=select_video_style('douyin','美食教程'))
        self.assertEqual(result['scenes'][0]['media'], 'clip')
        self.assertEqual(result['scenes'][0]['visual']['layout'], 'footage')

    def test_automatic_photo_shots_move_but_video_shots_keep_native_motion(self):
        shots = [{"title": "备菜", "text": "切好的蔬菜放在盘中。", "media": "", "zoom": False},
                 {"title": "入锅", "text": "蔬菜正在锅里翻动。", "media": "", "zoom": False}]
        media = [{"kind": kind, "title": "备菜画面", "page_url": kind, "local": True}
                 for kind in ("image", "video")]
        plan = [{"index": i, "layout": "footage", "motion": "cut", "intent": shot["text"]}
                for i, shot in enumerate(shots)]
        picks = {i: {"media": kind, "start": 0, "signature": kind, "framing": "fit", "reason": "动作匹配"}
                 for i, kind in enumerate(("image", "video"))}
        with scratch_dir() as root, patch("core.directing._design", return_value=plan), \
                patch("core.directing._contact_sheet", return_value=([], b"")), \
                patch("core.directing._choose_frames", side_effect=[(picks, []), ({}, [])]):
            cut = direct_cut({"scenes": shots}, media, "douyin", root, {"design": "director", "editing": "editor"})
        self.assertIn(cut["scenes"][0]["motion"], ("push", "pull"))
        self.assertEqual(cut["scenes"][1]["motion"], "cut")

    def test_comparison_can_show_the_same_magnified_region_in_both_samples(self):
        from PIL import ImageDraw

        from core.comparison_media import build_comparison

        with scratch_dir() as root:
            sources = []
            for i, color in enumerate(("red", "lime")):
                file = root / f"source-{i}.jpg"
                image = Image.new("RGB", (300, 300), "blue")
                ImageDraw.Draw(image).rectangle((100, 100, 199, 199), fill=color)
                image.save(file, quality=100)
                sources.append({"kind": "image", "page_url": f"local:{i}", "preview": str(file)})
            payload = {"sources": [a["page_url"] for a in sources], "labels": ["低档", "高档"], "condition": "同一张图"}
            full = build_comparison(payload, sources, root / "full", "bilibili")
            detail = build_comparison({**payload, "region": "center"}, sources, root / "detail", "bilibili")
            self.assertEqual(detail["comparison"]["region"], "center")
            with Image.open(full["preview"]) as wide, Image.open(detail["preview"]) as crop:
                self.assertGreater(wide.getpixel((720, 550))[2], 180)
                self.assertGreater(crop.getpixel((720, 550))[0], 180)
                self.assertGreater(crop.getpixel((1640, 550))[1], 180)
            with self.assertRaisesRegex(ValueError, "区域"):
                build_comparison({**payload, "region": "random"}, sources, root / "wrong", "bilibili")

    def test_comparison_director_uses_the_named_region(self):
        from core.comparison_media import select_comparison

        pairs = [{"comparison": {"condition": "同一张图", "labels": ["样本甲", "样本乙"], "region": region}}
                 for region in ("center", "top-right", "")]
        shared = "同一张图，样本甲和样本乙。"
        self.assertIs(select_comparison(pairs, shared), pairs[2])
        self.assertIs(select_comparison(pairs, shared + "放大右上角。"), pairs[1])
        self.assertIsNone(select_comparison(pairs, shared + "放大左下角。"))

    def test_comparison_video_respects_each_source_start(self):
        from subprocess import run

        from config import get_ffmpeg
        from core.comparison_media import build_comparison

        with scratch_dir() as root:
            assets = []
            for index, (intro, sample) in enumerate((("red", "blue"), ("green", "yellow"))):
                path = root / f"input-{index}.mp4"
                run([get_ffmpeg(), "-v", "error", "-f", "lavfi", "-i", f"color={intro}:80x60:r=24:d=1",
                     "-f", "lavfi", "-i", f"color={sample}:80x60:r=24:d=2", "-filter_complex", "concat=n=2:v=1:a=0",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
                assets.append({"page_url": f"local:test-{index}", "kind": "video", "local": True, "path": str(path),
                               "duration": 3, "video_format": "mov"})
            pair = build_comparison({"sources": [asset["page_url"] for asset in assets], "labels": ["样本甲", "样本乙"],
                                     "condition": "同步验收", "starts": [1., 1.5]}, assets, root / "pair", "bilibili")
            self.assertEqual(pair["duration"], 1.5)
            with Image.open(pair["preview"]) as frame:
                left = frame.getpixel((int(frame.width * .27), frame.height // 2))
                right = frame.getpixel((int(frame.width * .72), frame.height // 2))
            self.assertGreater(left[2], 200)
            self.assertGreater(right[0], 200)
            self.assertGreater(right[1], 200)
            self.assertLess(right[2], 50)

    def test_comparison_pair_requires_two_named_samples_and_common_conditions(self):
        import datetime as dt

        from core.workflow import edition_path, read_edition, run_stage, write_json

        now = dt.date(2026, 11, 9)
        style = select_video_style("bilibili", "同条件相机对比")
        report = {"title": "光线实测", "source": "测试记录", "url": "https://example.com/test"}
        chapter = {"story": report, "reports": [report]}
        drafts = [{"title": "对照样张", "text": text, "media": "", "zoom": False} for text in (
            "同一机位，设备甲和设备甲专业版各拍了一张。", "只有设备甲专业版在同一机位的照片。")]
        draft = {"scenes": drafts, "chapter_starts": [{"scene": 0, "title": report["title"]}]}
        decisions = {"shots": [{"index": i, "layout": "evidence", "motion": "cut", "intent": "对比样张"}
                               for i in range(2)]}
        with scratch_dir() as root:
            media = []
            for i, color in enumerate(("#4e8d85", "#d09270")):
                path = root / f"source-{i}.jpg"
                Image.new("RGB", (200, 120), color).save(path)
                media.append({"page_url": f"local:sample-{i}", "kind": "image", "local": True,
                              "path": str(path), "preview": str(path), "title": f"样本 {i}"})
            path = edition_path(root, now)
            path.parent.mkdir(parents=True)
            item = {"id": "compare", "story": report, "chapters": [chapter], "editorial": {"reports": [report]},
                    "media": media, "edit": draft}
            write_json(path, {"items": {"bilibili": item}})
            payload = {"id": "compare", "comparison": {"sources": [a["page_url"] for a in media],
                       "labels": ["设备甲", "设备甲专业版"], "condition": "同一机位"}}
            approved = run_stage("import_media", now, root, "bilibili", payload)
            pair = approved[-1]
            with patch("core.directing.chat_json", return_value=decisions):
                cut = direct_cut(draft, [pair], "bilibili", root / "work",
                                 {"writing": "writer", "design": "director", "editing": "editor"}, video_style=style)
            self.assertEqual([shot["media"] for shot in cut["scenes"]], [pair["page_url"], ""])
            run_stage("save", now, root, "bilibili", {**cut, "episode_id": "compare"})
            before = read_edition(path)["items"]["bilibili"]
            self.assertEqual(render_case(before, "bilibili")["scenes"][0]["asset"]["page_url"], pair["page_url"])
            run_stage("review_media", now, root, "bilibili", {"id": "compare", "page_url": media[0]["page_url"],
                                                              "state": "removed"})
            after = read_edition(path)["items"]["bilibili"]
            self.assertEqual(after["media_review"][pair["page_url"]], "removed")
            self.assertNotEqual(before["edit_revision"], after["edit_revision"])
            self.assertEqual(after["edit"]["scenes"][0]["media"], "")

    def test_near_identical_video_frames_are_reserved_across_chapters_and_cache_reuse(self):
        from core.directing import _shot_signature

        plate = io.BytesIO()
        Image.new("RGB", (744, 163), "#05090C").save(plate, "JPEG")
        archive = "https://example.com/video"
        image = "https://example.com/core"
        frames = [{"media": url, "kind": kind, "start": start, "wide": True, "title": title,
                   "signature": _shot_signature(Image.new("RGB", (64, 48), color))}
                  for url, kind, start, title, color in (
                      (archive, "video", 7., "钻机操作", "#3C4751"),
                      (archive, "video", 20., "同机位钻机操作", "#3E4754"),
                      (image, "image", 0., "岩芯展示", "#9F7E57"))]
        media = [{"page_url": url, "kind": kind, "title": title, "visual_subjects": [subject], "duration": 25}
                 for url, kind, title, subject in ((archive, "video", "钻机资料", "钻机"), (image, "image", "岩芯资料", "岩芯"))]
        scenes = [{"title": title, "text": narration, "media": "", "zoom": False}
                  for title, narration in (("设备", "钻机有独立的动力。"), ("样品", "钻机作业后取得岩芯。"))]
        draft = {"scenes": scenes, "chapter_starts": [{"scene": i, "title": shot["title"]}
                                                       for i, shot in enumerate(scenes)]}
        models = {"writing": "writer", "design": "director", "editing": "editor"}
        decision = {"index": 0, "layout": "evidence", "motion": "cut", "intent": "钻机"}
        replies = [{"shots": [decision.copy()]}, {"frame": 0}, {"match": True},
                   {"shots": [decision.copy()]}, {"match": True}]
        with scratch_dir() as root, patch("core.directing._contact_sheet", return_value=(frames, plate.getvalue())), \
                patch("core.directing.chat_json", side_effect=replies) as chat:
            cut = direct_cut(draft, media, "bilibili", root, models)
            self.assertEqual([shot["media"] for shot in cut["scenes"]], [archive, image])
            self.assertEqual(chat.call_args.args[1]["目标"], "岩芯")
            self.assertEqual(direct_cut(draft, media, "bilibili", root, models), cut)
            self.assertEqual(chat.call_count, len(replies))

    def test_explainer_uses_a_cutaway_for_an_observed_leak_only_once(self):
        from core.directing import drilling_visual
        from core.video_styles import diagram_labels

        title = "钻井原理"
        style = select_video_style("bilibili", title)
        mention = "常见风险包括井漏、井壁失稳和钻具疲劳。"
        event = "钻至10289米时遭遇严重井漏，工程师提出用堵漏浆处理。"
        used = set()
        self.assertIsNone(drilling_visual(mention, "evidence", style, used))
        visual = drilling_visual(event, "depth", style, used)
        self.assertEqual(visual["layout"], "drilling_flow")
        self.assertEqual(diagram_labels("drilling_flow", visual["labels"], event), ["井漏", "堵漏浆"])
        self.assertIsNone(drilling_visual(event, "evidence", style, used))
        self.assertIsNone(drilling_visual(event, "footage", style, set()))

    def test_measured_event_value_is_not_overlaid_on_archive_footage(self):
        report = {"title": "万米深井", "source": "新华社", "url": "https://example.com/drilling"}
        chapter = {"story": report, "reports": [report]}
        visual = {"layout": "footage", "start": 0, "framing": "center", "intent": "岩芯", "reason": "资料画面"}
        with scratch_dir() as root:
            image = root / "core.jpg"
            Image.new("RGB", (200, 160)).save(image)
            asset = {"kind": "image", "page_url": "https://example.com/core", "title": "其他项目的岩芯",
                     "preview": str(image), "caption_label": "岩芯资料", "chapters": [0]}
            shots = [{"title": "开场", "text": "报道回顾了钻探经过。", "media": "", "zoom": False},
                     {"title": "岩芯", "text": "该井达到10910 米，现场带回了岩芯。", "media": asset["page_url"],
                      "zoom": False, "visual": visual}]
            item = {"story": report, "chapters": [chapter], "media": [asset],
                    "media_review": {asset["page_url"]: "approved"}, "editorial": {"reports": [report]},
                    "edit": {"scenes": shots, "chapter_starts": [{"scene": 0, "title": report["title"]}]}}
            footage = next(scene for scene in render_case(item, "bilibili")["scenes"] if scene["layout"] == "footage")
        self.assertNotIn("documentary_number", footage)
        self.assertIn("10910 米", footage["text"])

    def test_measured_depth_becomes_a_scale_but_equipment_rating_does_not(self):
        from core.video_styles import depth_labels, diagram_labels

        text = "先钻到 5500 米，在10289米处遭遇严重井漏，最后在地下 10910 米完钻。"
        self.assertEqual(depth_labels(text), ["5500 米", "10289米", "10910 米"])
        self.assertEqual(depth_labels("水下 1.2 公里发现异常。"), ["1.2 公里"])
        self.assertFalse(depth_labels("国产12000米自动化钻机。电子屏幕显示10910.00。"))
        self.assertFalse(depth_labels("该型号最大设计井深为12000米，计划钻到5000米。"))
        self.assertFalse(depth_labels("这次没有钻到5000米。"))
        intervals = "从地表钻到 5500 米用了 50 多天，而最后 10000 米到 10910 米的“最后一公里”耗时 300 多天。"
        self.assertEqual(depth_labels(intervals), ["5500 米", "10000 米", "10910 米"])
        self.assertEqual(diagram_labels("depth", ["5500 米"], intervals), ["5500 米"])
        self.assertFalse(depth_labels("地下5500米到6000米之间。海拔5000米处有人。"))
        self.assertFalse(diagram_labels("depth", ["12000米"], text))
        rating = "现场使用了国产12000米自动化钻机。"
        shots = [{"title": title, "text": line, "media": "", "zoom": False}
                 for title, line in (("钻探进度", text), ("钻机规格", rating))]
        model_rows = {"shots": [{"index": i, "layout": "metric", "intent": "钻井数据", "motion": "push"}
                                for i in range(2)]}
        models = {"writing": "writer", "design": "director", "editing": "editor"}
        style = select_video_style("douyin", "钻井原理")
        with scratch_dir() as root, patch("core.directing.chat_json", return_value=model_rows):
            cut = direct_cut({"scenes": shots}, [], "douyin", root, models, video_style=style)
        self.assertEqual([scene["visual"]["layout"] for scene in cut["scenes"]], ["depth", "metric"])
        self.assertEqual(cut["scenes"][0]["visual"]["labels"], depth_labels(text))

    def test_matching_archival_subject_can_illustrate_a_narrated_fact(self):
        report = {"title": "钻探观察", "source": "通讯社", "url": "https://example.com/news"}
        chapter = {"story": report, "reports": [report]}
        shots = [{"title": "岩芯", "text": "从井中取得的岩芯用来观察地层。", "media": "", "zoom": False},
                 {"title": "用途", "text": "报道还说明了后续安排。", "media": "", "zoom": False}]
        decisions = {"shots": [{"index": 0, "layout": "evidence", "motion": "cut", "intent": "岩芯"},
                               {"index": 1, "layout": "evidence", "motion": "cut", "intent": "后续安排"}]}
        models = {"writing": "writer", "design": "director", "editing": "editor"}
        with scratch_dir() as root:
            photo = root / "core.jpg"
            Image.new("RGB", (200, 160)).save(photo)
            asset = {"kind": "image", "title": "别处项目的岩芯", "caption_label": "岩芯资料", "preview": str(photo),
                     "page_url": "https://example.com/photo"}
            with patch("core.directing.chat_json", side_effect=[decisions, {"match": True}]):
                cut = direct_cut({"scenes": shots}, [asset], "douyin", root, models, chapters=[chapter])
        self.assertEqual(cut["scenes"][0]["media"], asset["page_url"])
        self.assertFalse(cut["scenes"][1]["media"])

    def test_unavailable_exact_action_can_use_only_a_verified_illustrative_subject(self):
        report = {"title": "钻探观察", "source": "通讯社", "url": "https://example.com/news"}
        shots = [{"title": "岩芯", "text": "现场取出了首份岩芯。", "media": "", "zoom": False},
                 {"title": "用途", "text": "报道还说明了后续安排。", "media": "", "zoom": False}]
        decisions = {"shots": [{"index": 0, "layout": "footage", "motion": "cut", "intent": "现场人员拿起首份岩芯"},
                               {"index": 1, "layout": "evidence", "motion": "cut", "intent": "后续安排"}]}
        models = {"writing": "writer", "design": "director", "editing": "editor"}
        with scratch_dir() as root:
            photo = root / "core.jpg"
            Image.new("RGB", (200, 160)).save(photo)
            asset = {"kind": "image", "title": "别处项目的岩芯", "caption_label": "岩芯资料", "preview": str(photo),
                     "page_url": "https://example.com/photo", "visual_subjects": ["岩芯"]}
            with patch("core.directing.chat_json", side_effect=[decisions, {"frame": -1}, {"match": False}, {"match": True}]):
                cut = direct_cut({"scenes": shots}, [asset], "douyin", root, models,
                                 chapters=[{"story": report, "reports": [report]}])
        self.assertEqual(cut["scenes"][0]["media"], asset["page_url"])
        self.assertEqual(cut["scenes"][0]["visual"]["intent"], "岩芯")
        self.assertIn("资料", cut["scenes"][0]["visual"]["reason"])

    def test_a_bare_number_is_not_a_process_step(self):
        from core.video_styles import diagram_labels

        text = "读数停在10910.00。完钻后取出岩芯。"
        self.assertFalse(diagram_labels("steps", ["10910.00", "取出岩芯"], text))

    def test_unmatched_measurement_uses_a_sourced_metric_without_a_shot_number(self):
        report = {"title": "深井钻探设备", "source": "新华社", "url": "https://example.com/drilling"}
        chapter = {"story": report, "reports": [report]}
        visual = {"layout": "evidence", "start": 0, "framing": "center", "intent": "载荷", "reason": "缺少实拍"}
        shots = [{"title": "镜头二：入井载荷", "text": "入井载荷高达 665 吨。", "media": "", "zoom": False,
                  "visual": visual},
                 {"title": "Scene 03", "text": "地面设备继续运转。", "media": "", "zoom": False,
                  "visual": visual}]
        item = {"story": report, "chapters": [chapter], "editorial": {"reports": [report]},
                "edit": {"scenes": shots, "chapter_starts": [{"scene": 0, "title": report["title"]}]}}
        metric, evidence = render_case(item, "douyin")["scenes"]
        self.assertEqual((metric["layout"], metric.get("metric"), metric["title"]), ("metric", "665 吨", "入井载荷"))
        self.assertEqual((evidence["layout"], evidence["title"]), ("brief", report["title"]))

    def test_style_filters_diagrams_and_drives_the_rendered_timeline(self):
        narration = "光线穿过镜头。镜头把光线聚到传感器上，再转为电信号。"
        shots = [{"title": "光怎样变成信号", "text": narration, "media": "", "zoom": False},
                 {"title": "限度", "text": "暗处光线变少。", "media": "", "zoom": False}]
        report = {"title": "成像原理", "source": "报告", "url": "https://example.com/optics"}
        chapter = {"story": report, "reports": [report]}
        draft = {"chapter_starts": [{"scene": 0, "title": report["title"]}], "scenes": shots}
        style = select_video_style("bilibili", report["title"])
        decision = {"shots": [{"index": 0, "layout": "steps", "motion": "cut", "intent": "成像过程",
                               "labels": ["穿过镜头", "聚到传感器", "转为电信号"]},
                              {"index": 1, "layout": "steps", "motion": "cut", "intent": "光线影响",
                               "labels": ["光线变少", "伪造图解"]}]}
        models = {"writing": "writer", "design": "director", "editing": "editor"}
        with scratch_dir() as root, patch("core.directing.chat_json", return_value=decision) as chat:
            cut = direct_cut(draft, [], "bilibili", root, models, chapters=[chapter], video_style=style)
            self.assertEqual(chat.call_args.args[1]["video_style"]["name"], "原理拆解与知识科普")
            self.assertNotIn("comparison", chat.call_args.args[2]["properties"]["shots"]["items"]["properties"]["layout"]["enum"])
            item = {"story": report, "chapters": [chapter], "edit": cut, "editorial": {"reports": [report]}}
            first, second = render_case(item, "bilibili")["scenes"]
            self.assertEqual(first["layout"], "graphic")
            self.assertEqual(first["labels"], ["穿过镜头", "聚到传感器", "转为电信号"])
            self.assertEqual(second["layout"], "brief")

    def test_design_and_visual_editor_keep_verified_words_and_select_a_real_excerpt(self):
        with scratch_dir() as root:
            asset = {"title": "除雪实录", "page_url": "https://example.com/snow", "kind": "video",
                     "path": str(root / "snow.mp4"), "duration": 20, "author": "作者", "local": True}
            report = {"id": "report", "source": "通讯社", "title": "雪后出行", "url": "https://example.com/news"}
            chapter = {"story": report, "reports": [report]}
            draft = {"chapter_starts": [{"scene": 0, "title": report["title"]}],
                     "scenes": [{"title": "观察", "text": "除雪车辆从主路推开积雪。", "media": "", "zoom": False},
                                {"title": "提醒", "text": "列车运行安排以官方公告为准。", "media": "", "zoom": False}]}
            design = {"shots": [{"index": 0, "layout": "footage", "motion": "cut", "intent": "除雪车辆"},
                                {"index": 1, "layout": "evidence", "motion": "cut", "intent": "铁路公告"}]}
            selection = {"frame": 1}

            models = {"writing": "writer", "design": "director", "editing": "editor"}
            sample = io.BytesIO()
            Image.new("RGB", (120, 90), "#577989").save(sample, "JPEG")
            with patch("core.directing._frame_jpeg", return_value=sample.getvalue()), \
                    patch("core.directing.chat_json", side_effect=[design, selection, {"match": True}]) as chat:
                cut = direct_cut(draft, [asset], "douyin", root, models, chapters=[chapter])
                self.assertEqual([call.kwargs["model"] for call in chat.call_args_list], ["director", "editor", "editor"])
                self.assertTrue(chat.call_args.kwargs["images"][0].startswith(b"\xff\xd8"))
                self.assertEqual(chat.call_args.args[1].get("对应口播"), draft["scenes"][0]["text"])
            item = {"story": {**report, "category": "天气"}, "chapters": [chapter], "edit": cut,
                    "media": [asset], "editorial": {"reports": [report]}, "target_seconds": 60}
            timeline = render_case(item, "douyin")["scenes"]
            self.assertEqual(timeline[0]["visual"]["start"], 5.0)
            self.assertEqual(timeline[0]["visual"]["framing"], "fit")
            self.assertEqual(timeline[0]["asset"]["page_url"], asset["page_url"])
            self.assertTrue(all(not scene.get("asset") for scene in timeline if scene["layout"] == "brief"))
            self.assertEqual([s["text"] for s in cut["scenes"]], [s["text"] for s in draft["scenes"]])

    def test_picked_frame_without_the_requested_action_is_rejected(self):
        encoded = io.BytesIO()
        Image.new("RGB", (744, 163), "#ABCDEF").save(encoded, "JPEG")
        frame = {"title": "地质学生观察岩芯", "kind": "video", "media": "https://example.com/core", "wide": False}
        plan = [{"index": 0, "layout": "footage", "intent": "堵漏浆注入井口"}]
        with patch("core.directing.chat_json", side_effect=[{"frame": 0}, {"match": False}]) as chat:
            picks, errors = _choose_frames(plan, [frame], encoded.getvalue(), "editor", "douyin")
        self.assertEqual((picks, errors), ({}, []))
        self.assertEqual(chat.call_args.args[1]["目标"], "堵漏浆注入井口")

    def test_a_still_cannot_be_used_as_proof_of_running_equipment(self):
        encoded = io.BytesIO()
        Image.new("RGB", (744, 163)).save(encoded, "JPEG")
        frame = {"title": "钻井设备静态照片", "kind": "image", "media": "https://example.com/rig", "wide": False}
        for intent in ("自动化钻机运行", "泥浆槽内液体表面波动，岩屑沉底"):
            with self.subTest(intent=intent), patch("core.directing.chat_json") as chat:
                plan = [{"index": 0, "layout": "footage", "intent": intent}]
                picks, errors = _choose_frames(plan, [frame], encoded.getvalue(), "editor", "douyin")
                self.assertEqual((picks, errors), ({}, []))
                chat.assert_not_called()
