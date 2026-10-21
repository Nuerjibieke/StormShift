import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AssetTests(unittest.TestCase):
    def test_phaser_is_vendored_on_server(self):
        phaser = ROOT / "web" / "vendor" / "phaser.min.js"
        self.assertTrue(phaser.is_file())
        self.assertGreater(phaser.stat().st_size, 1_000_000)
        self.assertTrue((ROOT / "web" / "vendor" / "PHASER-LICENSE.md").is_file())

    def test_frontend_entrypoints_exist(self):
        for relative in ["web/index.html", "web/styles.css", "web/app.js", "web/phaser-world.js"]:
            self.assertGreater((ROOT / relative).stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
