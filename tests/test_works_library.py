import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from core import works_library


@contextmanager
def scratch_dir():
    workspace = Path.cwd().resolve()
    path = workspace / f"test_output_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        path.resolve().relative_to(workspace)
        shutil.rmtree(path)


def save(root, path, data=None):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        file.write_bytes(b"fixture")
    else:
        file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return file


class WorksLibraryTest(unittest.TestCase):
    def test_full_preview_is_visible_and_preferred_over_the_opening_preview(self):
        with scratch_dir() as root:
            day='2026-11-11'
            opening=save(root,f'daily/{day}/douyin/opening.mp4')
            full=save(root,f'daily/{day}/douyin/full.mp4')
            item={'story':{'title':'同一份工作稿'},'edit_revision':'current',
                  'edit':{'scenes':[{'text':'已保存的口播。'}]},
                  'full_preview_revision':'current','full_preview':{'video':str(full),'duration':57},
                  'preview':{'video':str(opening),'duration':12}}
            save(root,f'daily/{day}/edition.json',{'items':{'douyin':item}})
            previews=[w for a in works_library.load_works(root,day=day) for w in a.works if w.kind=='preview']
            self.assertEqual(len(previews),1)
            self.assertEqual(previews[0].package['video'],str(full))
            self.assertEqual(previews[0].state,'全片试剪')
            self.assertFalse(any(w.kind=='production' for a in works_library.load_works(root,day=day) for w in a.works))
            item['full_preview']['video']=str(root/'missing.mp4')
            save(root,f'daily/{day}/edition.json',{'items':{'douyin':item}})
            fallback=[w for a in works_library.load_works(root,day=day) for w in a.works if w.kind=='preview']
            self.assertEqual(fallback[0].package['video'],str(opening))

    def test_same_topic_keeps_three_platforms_and_distinct_xiaohongshu_formats(self):
        with scratch_dir() as root:
            day = "2026-11-08"
            daily = f"daily/{day}"
            video = save(root, f"{daily}/xiaohongshu/final.mp4")
            douyin = save(root, f"{daily}/douyin/final.mp4")
            bilibili = save(root, "other/bilibili/video_bilibili.mp4")
            preview = save(root, f"{daily}/douyin/preview.mp4")
            save(root, f"{daily}/edition.json", {"items": {
                "xiaohongshu": {"story": {"title": "  周末图书馆  "}, "package": {"video": str(video), "duration": 61}},
                "douyin": {"story": {"title": "周末图书馆"}, "package": {"video": str(douyin)},
                           "preview": {"video": str(preview)}},
            }})
            save(root, "other/manifest.json", {"title": "周末图书馆", "platforms": {
                "bilibili": {"video": r"C:\old\output\other\bilibili\video_bilibili.mp4",
                             "reading_pages": [{"title": "报道", "start_ms": 0}]},
            }})
            picture = save(root, "copywriting/20261108-a/小红书-01/01.jpg")
            save(root, "copywriting/20261108-a/batch.json", {"topic": "周末图书馆", "results": {
                "xiaohongshu": {"drafts": [{"title": "图书馆图文", "script": "带图正文",
                                           "pictures": [{"file": "小红书-01/01.jpg"}]},
                                          {"title": "图书馆纯文字", "script": "纯文字正文", "caption": "配文"}]},
            }})

            albums = works_library.load_works(root)

            self.assertEqual([album.title for album in albums], ["周末图书馆"])
            works = {(work.platform, work.kind): work for work in albums[0].works}
            self.assertEqual(set(works), {("xiaohongshu", "video"), ("xiaohongshu", "image"),
                                          ("xiaohongshu", "text"), ("douyin", "video"), ("douyin", "preview"),
                                          ("bilibili", "video")})
            self.assertEqual(works["xiaohongshu", "video"].package["video"], str(video))
            self.assertEqual(works["xiaohongshu", "image"].pictures[0]["file"], str(picture))
            self.assertEqual(works["xiaohongshu", "text"].draft["script"], "纯文字正文")
            self.assertEqual(works["bilibili", "video"].package["video"], str(bilibili))
            self.assertEqual(works["bilibili", "video"].package["reading_pages"][0]["title"], "报道")

    def test_legacy_note_images_and_old_versions_remain_available(self):
        with scratch_dir() as root:
            old = save(root, "daily/2026-11-06/douyin/old.mp4")
            newer = save(root, "daily/2026-11-06/douyin/new.mp4")
            save(root, "daily/2026-11-06/edition.json", {
                "items": {"douyin": {"story": {"title": "另一篇"}, "package": {"video": str(newer)}}},
                "previous_items": {"douyin": [{"story": {"title": "旧选题"}, "package": {"video": str(old)}}]},
            })
            shot = save(root, "copywriting/20261106-a/01-配图/01.png")
            save(root, "copywriting/20261106-a/batch.json", {"topic": "旧选题", "results": {
                "xiaohongshu": {"drafts": [{"title": "旧图文", "script": "正文",
                                           "pages": [{"title": "实际分页", "text": "分页正文"}]}]},
            }})
            save(root, "copywriting/20261107-b/batch.json", {"topic": "旧选题", "text_only": True, "results": {
                "xiaohongshu": {"drafts": [{"title": "纯文字", "script": "正文"}]},
            }})
            save(root, "copywriting/20261107-b/01-配图/01.png")
            save(root, "empty/manifest.json", {"title": "无文件", "platforms": {
                "douyin": {"video": "not-present.mp4"},
            }})
            save(root, "daily/2026-11-07/edition.json").write_text("{", encoding="utf-8")

            albums = {album.title: album.works for album in works_library.load_works(root)}

            self.assertEqual(set(albums), {"旧选题", "另一篇"})
            works = {work.kind: work for work in albums["旧选题"]}
            self.assertEqual(works["video"].package["video"], str(old))
            self.assertEqual(works["image"].pictures[0]["file"], str(shot))
            self.assertEqual(works["text"].pictures, ())


if __name__ == "__main__":
    unittest.main()
