"""Ham istihbarat verilerini JSON formatında dışa aktarır."""

import json
import os


class JSONExporter:
    """Sözlük ya da oturum verisini JSON olarak yazar."""

    def __init__(self, report_data: dict):
        self.report_data = report_data or {}

    def generate(self) -> str:
        return json.dumps(self.report_data, ensure_ascii=False, indent=4)

    def export(self, filepath: str) -> str:
        directory = os.path.dirname(os.path.abspath(filepath))
        os.makedirs(directory, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.generate())
        return filepath
