"""验证 storage_path.py 各函数签名、返回值类型、目录创建、URL 合规。"""

import os
import tempfile
import unittest
from unittest.mock import patch


@patch("utils.storage_path.BASE_URL", "https://test.mzsh.top")
class TestStoragePath(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import utils.storage_path as sp
        self._orig_root = sp.STORAGE_ROOT
        sp.STORAGE_ROOT = self.tmpdir
        sp.PROJECTS_DIR = f"{self.tmpdir}/projects"

    def tearDown(self):
        import utils.storage_path as sp
        sp.STORAGE_ROOT = self._orig_root
        sp.PROJECTS_DIR = f"{self._orig_root}/projects"
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _check(self, func, project_id="test123", filename="test.jpg"):
        local, url = func(project_id, filename)
        self.assertIsInstance(local, str)
        self.assertIsInstance(url, str)
        self.assertTrue(local.startswith(self.tmpdir))
        self.assertTrue(url.startswith("https://test.mzsh.top/storage/"))
        self.assertTrue(os.path.exists(os.path.dirname(local)))
        return local, url

    def test_figure_path(self):
        from utils.storage_path import figure_path
        local, url = self._check(figure_path)
        self.assertIn("/projects/test123/figures/", local)
        self.assertIn("/projects/test123/figures/", url)
        with open(local, "w") as f:
            f.write("test")
        self.assertTrue(os.path.exists(local))

    def test_scene_path(self):
        from utils.storage_path import scene_path
        self._check(scene_path)

    def test_tts_path(self):
        from utils.storage_path import tts_path
        self._check(tts_path, filename="voice.mp3")

    def test_bgm_path(self):
        from utils.storage_path import bgm_path
        self._check(bgm_path, filename="bg.mp3")

    def test_video_path(self):
        from utils.storage_path import video_path
        self._check(video_path, filename="shot.mp4")

    def test_subtitle_path(self):
        from utils.storage_path import subtitle_path
        self._check(subtitle_path, filename="sub.srt")

    def test_final_path(self):
        from utils.storage_path import final_path
        self._check(final_path, filename="final.mp4")

    def test_local_to_url(self):
        from utils.storage_path import local_to_url
        l1 = f"{self.tmpdir}/projects/123/figures/x.jpg"
        u1 = local_to_url(l1)
        self.assertEqual(u1, "https://test.mzsh.top/storage/projects/123/figures/x.jpg")
        l2 = f"{self.tmpdir}/figures/y.jpg"
        u2 = local_to_url(l2)
        self.assertEqual(u2, "https://test.mzsh.top/storage/figures/y.jpg")

    def test_url_to_local(self):
        from utils.storage_path import url_to_local
        u = "https://test.mzsh.top/storage/projects/123/figures/x.jpg"
        l = url_to_local(u)
        self.assertEqual(l, f"{self.tmpdir}/projects/123/figures/x.jpg")

    def test_auto_filename(self):
        from utils.storage_path import figure_path
        local, url = figure_path("test123")
        fname = os.path.basename(local)
        self.assertTrue(fname.startswith("figure_"))
        self.assertTrue(fname.endswith(".jpg"))

    def test_all_type_dirs(self):
        from utils.storage_path import all_type_dirs
        dirs = all_type_dirs("test123")
        self.assertIn("figures", dirs)
        self.assertIn("scenes", dirs)
        self.assertIn("videos", dirs)
        self.assertIn("tts", dirs)
        self.assertIn("bgm", dirs)
        self.assertIn("subtitle", dirs)
        self.assertIn("final", dirs)
        for d in dirs.values():
            self.assertTrue(os.path.exists(d))

    def test_store_content(self):
        from utils.storage_path import store_content
        local, url = store_content("test123", "subtitle", "act1.srt", b"test content")
        self.assertTrue(os.path.exists(local))
        with open(local, "r") as f:
            self.assertEqual(f.read(), "test content")

    def test_local_to_url_empty(self):
        from utils.storage_path import local_to_url
        self.assertEqual(local_to_url(""), "")
        self.assertEqual(local_to_url("https://x.com/y.jpg"), "https://x.com/y.jpg")


if __name__ == "__main__":
    unittest.main()
