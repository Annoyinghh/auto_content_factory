import datetime
import unittest
from pathlib import Path
from unittest.mock import patch
from core.workflow import edition_path, read_edition, run_stage, write_json
from test_daily import scratch_dir

class ProductionModeAudit(unittest.TestCase):
    platform = "xiaohongshu"
    day = datetime.date(2099, 1, 1)
    scenes = [{"title": f"Shot {i}", "text": f"Narration {i}", "media": "", "zoom": False} for i in range(3)]

    def create(self, root):
        run_stage("create", self.day, root, self.platform, {"title": "Production QA", "cut": {
            "scenes": self.scenes, "confirmed_scenes": [0, 2]}})
        return self.read(root)

    def read(self, root):
        return read_edition(edition_path(root, self.day))["items"][self.platform]

    def request(self, item, **extras):
        return {"id": item.get("id"), "revision": item.get("edit_revision"),
                "suggestion_revision": item.get("suggestion_revision"), **extras}

    @staticmethod
    def direction(draft, *args, only=None, **kwargs):
        assert only == {1}, only
        scenes = [dict(shot) for shot in draft["scenes"]]
        scenes[1]["motion"] = "cut"
        return {**draft, "scenes": scenes}

    def test_one_scene_returns_trial_without_saving(self):
        with scratch_dir() as root, \
             patch("core.directing.production_models", return_value={"design": "d", "editing": "e"}), \
             patch("core.directing.direct_cut", side_effect=self.direction), \
             patch("core.workflow._render_trial", return_value={"video": "shot.mp4"}) as trial:
            item = self.create(root)
            response = run_stage("direct_scene", self.day, root, self.platform,
                                 self.request(item, index=1, scene=self.scenes[1], source="edit"))
            self.assertEqual(response["scene"]["motion"], "cut")
            self.assertEqual(response["preview"]["video"], "shot.mp4")
            self.assertEqual(len(trial.call_args.args[0]["scenes"]), 1)
            self.assertEqual(self.read(root), item)

    def test_auto_preserves_confirmed_and_only_exports_on_request(self):
        with scratch_dir() as root, \
             patch("core.directing.production_models", return_value={"design": "d", "editing": "e"}), \
             patch("core.directing.direct_cut", side_effect=self.direction) as direct, \
             patch("core.directing.audit_visuals", return_value={"ready": True}), \
             patch("core.workflow._render_preview", return_value={"video": "trial.mp4"}) as preview, \
             patch("core.workflow._render", return_value={"video": "final.mp4"}) as export:
            item = self.create(root)
            self.assertEqual(item["edit"]["confirmed_scenes"], [0, 2])
            payload = self.request(item)
            self.assertEqual(run_stage("auto_cut", self.day, root, self.platform, payload)["video"], "trial.mp4")
            edited = self.read(root)["edit"]
            self.assertEqual(edited["confirmed_scenes"], [0, 2])
            self.assertEqual([edited["scenes"][i] for i in (0, 2)], [self.scenes[i] for i in (0, 2)])
            self.assertEqual(edited["scenes"][1]["motion"], "cut")
            direct.assert_called_once()
            self.assertTrue(preview.call_args.kwargs["whole"])
            export.assert_not_called()
            with self.assertRaises(RuntimeError):
                run_stage("auto_cut", self.day, root, self.platform, payload)
            payload = self.request(self.read(root), export=True)
            self.assertEqual(run_stage("auto_cut", self.day, root, self.platform, payload)["video"], "final.mp4")
            export.assert_called_once()
            self.assertEqual(self.read(root)["edit"]["confirmed_scenes"], [0, 1, 2])

    def test_fill_missing_preserves_already_matched_unconfirmed_shots(self):
        with scratch_dir() as root, patch('core.workflow._render_preview',return_value={'video':'trial.mp4'}), \
                patch('core.directing.audit_visuals',return_value={'ready':True}), \
                patch('core.workflow._direct_scenes',side_effect=lambda item,base,*args,**kwargs:base) as direct:
            item=self.create(root)
            item['media']=[{'page_url':'clip','local':True}]
            item['edit']['confirmed_scenes']=[]
            item['edit']['scenes'][0]['media']='clip'
            item['edit']['scenes'][2]['media']='clip'
            write_json(edition_path(root,self.day),{'items':{self.platform:item}})
            run_stage('auto_cut',self.day,root,self.platform,self.request(item,only_missing=True))
            self.assertEqual(direct.call_args.args[5],{1})
            self.assertEqual(self.read(root)['edit']['scenes'][0],item['edit']['scenes'][0])
            self.assertEqual(self.read(root)['edit']['scenes'][2],item['edit']['scenes'][2])

    def test_failed_coverage_retains_the_reviewable_draft_and_matching_audit(self):
        audit={'ready':False,'message':'缺少对应画面'}
        with scratch_dir() as root, \
                patch('core.directing.production_models',return_value={'design':'d','editing':'e'}), \
                patch('core.directing.direct_cut',side_effect=self.direction), \
                patch('core.directing.audit_visuals',return_value=audit), \
                patch('core.workflow._render_preview') as preview,patch('core.workflow._render') as export:
            item=self.create(root)
            with self.assertRaisesRegex(RuntimeError,'缺少对应画面'):
                run_stage('auto_cut',self.day,root,self.platform,self.request(item,export=True))
            saved=self.read(root)
            self.assertEqual(saved['edit']['scenes'][1]['motion'],'cut')
            self.assertEqual(saved['edit']['confirmed_scenes'],[0,2])
            self.assertEqual(saved['production_audit'],audit)
            self.assertNotEqual(saved['edit_revision'],item['edit_revision'])
            self.assertEqual([saved['edit']['scenes'][i] for i in (0,2)],[self.scenes[i] for i in (0,2)])
            self.assertIn('缺少对应画面',saved['editorial_progress'])
            preview.assert_not_called();export.assert_not_called()

    def test_new_editorial_auto_builds_all_chapters_before_first_trial(self):
        chapters = [{"story": {"title": shot["title"]}} for shot in self.scenes]
        draft = {"scenes": self.scenes, "chapter_starts": [
            {"scene": index, "title": shot["title"]} for index, shot in enumerate(self.scenes)
        ]}
        item = {"id": "editorial", "story": {"title": "News QA"}, "editorial": {"anchor": {"title": "News QA"}},
                "media": [{"page_url": "local:reference", "local": True}],
                "media_checked": True, "opening_media_checked": True}
        with (
            scratch_dir() as root,
            patch("core.editorial.plan_chapters", return_value=(chapters, {})),
            patch('core.editorial.retrieve_chapter_evidence', side_effect=lambda _editorial, chapters, *_args: chapters),
            patch("core.editorial.write_batches", return_value=draft),
            patch("core.directing.production_models", return_value={"design": "d", "editing": "e"}),
            patch("core.directing.direct_cut", return_value=draft) as direct,
            patch("core.directing.audit_visuals", return_value={"ready": True}),
            patch("core.workflow._render_preview", return_value={"video": "trial.mp4"}) as preview,
        ):
            path = edition_path(root, self.day)
            path.parent.mkdir(parents=True)
            write_json(path, {"items": {self.platform: item}})
            run_stage("auto_cut", self.day, root, self.platform, self.request(item))
            preview.assert_called_once()
            self.assertTrue(preview.call_args.kwargs["whole"])
            self.assertEqual(self.read(root)["edit"]["chapter_starts"], draft["chapter_starts"])
            self.assertEqual(self.read(root)["edit"]["confirmed_scenes"], [])
            # A retry starts with a partially prepared suggestion. It must use the
            # same production cache as the initial preparation.
            prepared_path = direct.call_args.args[3]
            partial = {**item, "suggestion": draft, "suggestion_revision": "partial"}
            write_json(path, {"items": {self.platform: partial}})
            run_stage("auto_cut", self.day, root, self.platform, self.request(partial))
            self.assertEqual(direct.call_args.args[3], prepared_path)
            self.assertEqual(direct.call_count, 2)

if __name__ == "__main__":
    unittest.main()
