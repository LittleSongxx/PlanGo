"""Run: PYTHONPATH=backend python -m unittest discover -s backend/tests -p test_skills_loader.py"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plango.skills import MAX_ADVERT_BYTES, MAX_SKILL_BYTES, list_skill_adverts, read_skill


class SkillLoaderTest(unittest.TestCase):
    def test_bounded_read_and_path_permissions(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "skills"
            root.mkdir()
            for name in ("citywalk", "disabled", "huge", "file-link"):
                (root / name).mkdir()
            content = "---\nname: 城市漫步\ndescription: >-\n  真实门店\n  规划路线\n---\nRead the real page."
            (root / "citywalk" / "SKILL.md").write_text(content)
            (root / "disabled" / "SKILL.md").write_text(content)
            (root / "huge" / "SKILL.md").write_text("街" * MAX_SKILL_BYTES)
            outside = Path(folder) / "outside"
            outside.mkdir()
            (outside / "SKILL.md").write_text("private secret")
            (root / "directory-link").symlink_to(outside, target_is_directory=True)
            (root / "file-link" / "SKILL.md").symlink_to(outside / "SKILL.md")
            with patch.dict(os.environ, {"PLANGO_SKILLS_DIR": str(root)}):
                adverts = json.loads(list_skill_adverts(["citywalk"]))
                self.assertEqual(
                    adverts,
                    [{"id": "citywalk", "name": "城市漫步", "description": "真实门店 规划路线"}],
                )
                self.assertEqual(read_skill("citywalk", ["citywalk"]), content)
                self.assertEqual(list_skill_adverts([]), "[]")
                for name, enabled in [
                    ("../outside", None),
                    (str(outside), None),
                    ("directory-link", None),
                    ("file-link", None),
                    ("disabled", ["citywalk"]),
                ]:
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        read_skill(name, enabled)
                huge = read_skill("huge")
                self.assertLessEqual(len(huge.encode()), MAX_SKILL_BYTES)
                self.assertIn("truncated", huge)
                for index in range(30):
                    directory = root / f"large-{index}"
                    directory.mkdir()
                    (directory / "SKILL.md").write_text(
                        "---\nname: " + "城" * 120 + "\ndescription: " + "街" * 600 + "\n---\nBody"
                    )
                all_adverts = list_skill_adverts()
                self.assertLessEqual(len(all_adverts.encode()), MAX_ADVERT_BYTES)
                self.assertIsInstance(json.loads(all_adverts), list)
                self.assertNotIn("private secret", all_adverts)


if __name__ == "__main__":
    unittest.main()
