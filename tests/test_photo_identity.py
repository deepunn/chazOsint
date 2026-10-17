"""CHAZ OSINT - Fotoğraftan hesap/kimlik izi çıkarma testleri.

Çalıştırma (proje kökünden):
    python -m unittest tests.test_photo_identity -v
"""

import os
import tempfile
import unittest

from PIL import Image

from intelligence.photo_identity import PhotoIdentityAI, REVERSE_SEARCH_PAGES


class TestPhotoIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def _make(self, name, exif=None):
        path = self._path(name)
        img = Image.new("RGB", (60, 40), (40, 90, 140))
        if exif is not None:
            img.save(path, exif=exif)
        else:
            img.save(path)
        return path

    def test_handle_from_metadata(self):
        exif = Image.Exif()
        exif[0x013B] = "Ahmet Yılmaz"                  # Artist
        exif[0x8298] = "© 2024 @ahmet_yz"              # Copyright -> @handle
        p = self._make("foto.jpg", exif=exif)

        result = PhotoIdentityAI(p).analyze()
        values = {l["value"].lower() for l in result["leads"]}
        self.assertIn("ahmet_yz", values)
        self.assertIn("ahmet_yz", [t.lower() for t in result["suggested_targets"]])

    def test_url_in_metadata(self):
        exif = Image.Exif()
        exif[0x010E] = "instagram.com/ahmet_yz/profile"   # ImageDescription
        p = self._make("foto.jpg", exif=exif)

        result = PhotoIdentityAI(p).analyze()
        self.assertIn("ahmet_yz", [t.lower() for t in result["suggested_targets"]])

    def test_filename_lead(self):
        p = self._make("foto_ahmet123.jpg")   # "foto" gürültü, "ahmet123" iz
        result = PhotoIdentityAI(p).analyze()
        self.assertIn("ahmet123", result["suggested_targets"])

    def test_generic_image_has_no_targets(self):
        p = self._make("img_2024.jpg")        # hem "img" hem "2024" elenir
        result = PhotoIdentityAI(p).analyze()
        self.assertEqual(result["suggested_targets"], [])
        self.assertTrue(result["notes"])

    def test_metadata_beats_filename(self):
        exif = Image.Exif()
        exif[0x9C9D] = "gercek_hesap".encode("utf-16-le")   # XPAuthor
        p = self._make("foto_rastgele99.jpg", exif=exif)
        result = PhotoIdentityAI(p).analyze()
        # Meta veri izi dosya adından önce gelmeli
        if result["suggested_targets"]:
            self.assertEqual(result["suggested_targets"][0].lower(), "gercek_hesap")

    def test_reverse_search_links_present(self):
        p = self._make("x.jpg")
        result = PhotoIdentityAI(p).analyze()
        self.assertEqual(len(result["reverse_search"]), len(REVERSE_SEARCH_PAGES))
        self.assertTrue(all(u.startswith("http") for _, u in result["reverse_search"]))

    def test_missing_file(self):
        result = PhotoIdentityAI(self._path("yok.jpg")).analyze()
        self.assertEqual(result["suggested_targets"], [])
        self.assertIn("Görsel bulunamadı.", result["notes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
