"""CHAZ OSINT - Fotoğraf adli-tıp AI testleri.

Çalıştırma (proje kökünden):
    python -m unittest tests.test_photo_forensics -v
"""

import os
import tempfile
import unittest

from PIL import Image

from collectors.forensics.exif_analyzer import EXIFAnalyzer
from collectors.forensics.photo_forensics import PhotoForensicsAI


def _make_image(path, exif=None, size=(120, 90), color=(120, 180, 240)):
    img = Image.new("RGB", size, color)
    if exif is not None:
        img.save(path, exif=exif)
    else:
        img.save(path)
    return path


class TestPhotoForensics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def test_no_exif_flags_stripped_metadata(self):
        p = _make_image(self._path("no_exif.jpg"))
        result = PhotoForensicsAI(p).analyze()
        self.assertIn("metadata_stripped", result["active"])
        self.assertGreater(result["risk_score"], 0.3)
        self.assertIsNotNone(result["phash"])

    def test_editing_software_raises_risk(self):
        exif = Image.Exif()
        exif[0x010F] = "Canon"            # Make
        exif[0x0110] = "EOS 5D"           # Model
        exif[0x0131] = "Adobe Photoshop 24.0"  # Software
        exif[0x9003] = "2024:05:01 12:00:00"   # DateTimeOriginal
        p = _make_image(self._path("edited.jpg"), exif=exif)

        result = PhotoForensicsAI(p).analyze()
        self.assertIn("editing_software", result["active"])
        self.assertIn("has_camera", result["active"])
        self.assertGreaterEqual(result["risk_score"], 0.5)

    def test_clean_camera_photo_low_risk(self):
        exif = Image.Exif()
        exif[0x010F] = "Apple"
        exif[0x0110] = "iPhone 15"
        exif[0x0131] = "iOS 17"
        exif[0x9003] = "2024:05:01 12:00:00"
        p = _make_image(self._path("clean.jpg"), exif=exif)

        result = PhotoForensicsAI(p).analyze()
        self.assertIn("has_camera", result["active"])
        self.assertNotIn("editing_software", result["active"])
        self.assertLess(result["risk_score"], 0.3)

    def test_gps_is_parsed_to_decimal(self):
        p = _make_image(self._path("gps.jpg"))
        analyzer = EXIFAnalyzer(p)
        meta = analyzer.analyze()
        self.assertTrue(meta["exists"])
        self.assertEqual(meta["width"], 120)
        self.assertEqual(meta["height"], 90)

    def test_phash_is_stable(self):
        p = _make_image(self._path("hash.jpg"))
        h1 = PhotoForensicsAI(p).analyze()["phash"]
        h2 = PhotoForensicsAI(p).analyze()["phash"]
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_missing_file_handled(self):
        result = PhotoForensicsAI(self._path("yok.jpg")).analyze()
        self.assertFalse(result["meta"]["exists"])
        self.assertEqual(result["risk_score"], 0.0)

    def test_legacy_extract_metadata_still_works(self):
        p = _make_image(self._path("legacy.jpg"))
        meta = EXIFAnalyzer(p).extract_metadata()
        self.assertIsInstance(meta, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
