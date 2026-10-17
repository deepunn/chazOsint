"""
EXIFAnalyzer - Yüklenen görsellerin meta verilerini (EXIF) çıkaran adli tıp modülü.

`extract_metadata()` düz bir etiket->değer sözlüğü döndürür (geriye dönük
uyumluluk). `analyze()` ise kamera, tarih, yazılım, GPS (ondalık koordinat)
ve boyut bilgilerini yapılandırılmış biçimde sunar.
"""

import os


class EXIFAnalyzer:
    """Görsel meta verilerini (EXIF/GPS) çıkaran adli tıp modülü."""

    def __init__(self, image_path: str):
        self.image_path = image_path

    # -- Düz sözlük (geriye dönük uyumluluk) --------------------------------
    def extract_metadata(self) -> dict:
        if not self.image_path or not os.path.exists(self.image_path):
            return {"status": "Görsel seçilmedi."}
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS
            with Image.open(self.image_path) as image:
                exifdata = image.getexif()
                metadata = {}
                for tag_id in exifdata:
                    tag = TAGS.get(tag_id, tag_id)
                    metadata[str(tag)] = str(exifdata.get(tag_id))
                return metadata or {"info": "EXIF verisi yok."}
        except Exception as e:
            return {"error": str(e)}

    # -- Yapılandırılmış analiz --------------------------------------------
    def analyze(self) -> dict:
        result = {
            "path": self.image_path,
            "exists": False,
            "has_exif": False,
            "raw": {},
            "camera_make": None,
            "camera_model": None,
            "lens": None,
            "software": None,
            "datetime": None,
            "width": None,
            "height": None,
            "format": None,
            "exif_width": None,
            "exif_height": None,
            "gps": None,
            "gps_maps_url": None,
            "error": None,
        }
        if not self.image_path or not os.path.exists(self.image_path):
            result["error"] = "Görsel bulunamadı."
            return result

        try:
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS
            with Image.open(self.image_path) as image:
                result["exists"] = True
                result["width"], result["height"] = image.size
                result["format"] = image.format

                exif = image.getexif()
                tags = {TAGS.get(k, k): v for k, v in exif.items()}
                result["raw"] = {str(k): str(v) for k, v in tags.items()}
                result["has_exif"] = bool(tags)

                result["camera_make"] = _clean(tags.get("Make"))
                result["camera_model"] = _clean(tags.get("Model"))
                result["lens"] = _clean(tags.get("LensModel"))
                result["software"] = _clean(tags.get("Software"))
                result["datetime"] = _clean(tags.get("DateTimeOriginal") or tags.get("DateTime"))
                result["exif_width"] = _to_int(tags.get("ExifImageWidth"))
                result["exif_height"] = _to_int(tags.get("ExifImageHeight"))

                gps_info = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else None
                if gps_info:
                    gps = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}
                    lat = _gps_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
                    lon = _gps_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
                    if lat is not None and lon is not None:
                        result["gps"] = {"lat": lat, "lon": lon}
                        result["gps_maps_url"] = f"https://www.google.com/maps?q={lat},{lon}"
        except Exception as e:
            result["error"] = str(e)
        return result


# --- Yardımcılar ------------------------------------------------------------

def _clean(value):
    if value is None:
        return None
    text = str(value).strip().strip("\x00")
    return text or None


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _gps_to_decimal(coord, ref):
    """EXIF GPS (derece, dakika, saniye) -> ondalık koordinat."""
    if not coord or len(coord) != 3:
        return None
    try:
        def _val(x):
            return float(x) if not isinstance(x, tuple) else x[0] / x[1]
        d, m, s = (_val(coord[0]), _val(coord[1]), _val(coord[2]))
        decimal = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except Exception:
        return None
