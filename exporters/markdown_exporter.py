"""Tarama sonucunu Markdown raporuna çevirir (insan-okur, sürüm kontrolü dostu)."""

from datetime import datetime


class MarkdownExporter:
    """`SessionManager.to_dict()` çıktısını Markdown'a dönüştürür."""

    def __init__(self, session_data: dict):
        self.data = session_data or {}

    def generate(self) -> str:
        d = self.data
        summary = d.get("summary", {})
        lines = [
            f"# 🛡️ OSINT Raporu — {d.get('target', '-')}",
            "",
            f"*Oluşturulma:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
            f"*Güven skoru:* %{summary.get('confidence', 0):.1f}  ",
            f"*Süre:* {summary.get('duration', 0)} sn",
            "",
            "## Özet",
            "",
            "| Metrik | Değer |",
            "|---|---|",
            f"| Aday | {summary.get('candidates', 0)} |",
            f"| Doğrulanan hesap | {summary.get('verified', 0)} |",
            f"| Doğrulanamayan | {summary.get('tentative', 0)} |",
            f"| Güven skoru | %{summary.get('confidence', 0):.1f} |",
            "",
            "## Doğrulanan Hesaplar",
            "",
        ]
        verified = d.get("verified", [])
        if verified:
            lines += ["| Platform | Kullanıcı Adı | AI | Kanıt | Link |",
                      "|---|---|---|---|---|"]
            for c in verified:
                prob = (c.get("ai_probability") or 0) * 100
                lines.append(
                    f"| {c.get('platform', '-')} | {c.get('handle', '-')} | "
                    f"%{prob:.0f} | {c.get('reason', '-')} | "
                    f"[link]({c.get('url', '#')}) |")
        else:
            lines.append("*Yeterli kanıtla doğrulanmış hesap bulunamadı.*")

        tentative = d.get("tentative", [])
        if tentative:
            lines += ["", "## Doğrulanamayan Adaylar", ""]
            for c in tentative:
                if c.get("wall"):
                    # İçerik anonim erişime kapalı: "yok" denemez, AI
                    # olasılığı da ölçülemez -> elle kontrol gerekiyor.
                    lines.append(f"- 🔒 {c.get('platform', '-')}: {c.get('url', '-')} "
                                 f"(platform duvarı — elle kontrol edin)")
                    continue
                prob = (c.get("ai_probability") or 0) * 100
                lines.append(f"- {c.get('platform', '-')}: {c.get('url', '-')} "
                             f"(AI %{prob:.0f})")

        if d.get("fingerprint_matches"):
            lines += ["", "## 🔁 Görsel Tekrar Kullanımı", ""]
            for m in d["fingerprint_matches"]:
                lines.append(f"- Daha önce **{m.get('target')}** hedefiyle görülmüş "
                             f"({m.get('timestamp')})")

        if d.get("snippets"):
            lines += ["", "## Açık Kaynak Özetleri", ""]
            for s in d["snippets"][:5]:
                lines.append(f"- {s[:160]}")

        return "\n".join(lines) + "\n"

    def export(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.generate())
