"""HTML rapor dışa aktarıcı.

Tek sorumluluk: metin raporu, tarayıcıda açılabilen şık bir HTML panosuna
dönüştürmek. ÖNEMLİ: rapor metni kaçışsız (escape edilmeden) gömülürse
içerikteki `<`, `>`, `&` karakterleri HTML'i bozar ya da istenmeyen etiketler
üretir; bu yüzden tüm dinamik metin `html.escape` ile kaçırılır.
"""

import html
from datetime import datetime


class HTMLDashboardExporter:
    """Etkileşimli web tabanlı HTML rapor panosu üretir."""

    def __init__(self, target_name: str, report_text: str):
        self.target_name = target_name or "-"
        self.report_text = report_text or ""

    def generate_html(self, filepath: str) -> str:
        target = html.escape(str(self.target_name))
        report = html.escape(self.report_text)
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M")

        html_content = f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CHAZ OSINT Raporu · {target}</title>
    <style>
        :root {{
            --bg: #0B0E13; --card: #141922; --border: #232C39;
            --text: #E9EAF0; --soft: #C2C9D3; --muted: #7B8493;
            --accent: #6EE7B7; --info: #60A5FA;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0; padding: 32px 18px; background: var(--bg);
            color: var(--text);
            font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
        }}
        .wrap {{ max-width: 960px; margin: 0 auto; }}
        header {{
            border-bottom: 1px solid var(--border); padding-bottom: 16px;
            margin-bottom: 20px;
        }}
        .brand {{ color: var(--accent); font-weight: 700; letter-spacing: 2px;
                  font-size: 12px; text-transform: uppercase; }}
        h1 {{ font-size: 22px; margin: 6px 0 4px; }}
        .meta {{ color: var(--muted); font-size: 12px; }}
        .card {{
            background: var(--card); border: 1px solid var(--border);
            border-radius: 12px; padding: 20px; overflow-x: auto;
        }}
        pre {{
            margin: 0; white-space: pre-wrap; word-break: break-word;
            font-family: Consolas, "Courier New", monospace;
            font-size: 13px; line-height: 1.5; color: var(--soft);
        }}
        footer {{ margin-top: 18px; color: var(--muted); font-size: 11px; }}
        footer strong {{ color: var(--info); }}
    </style>
</head>
<body>
    <div class="wrap">
        <header>
            <div class="brand">CHAZ OSINT Enterprise</div>
            <h1>İstihbarat Raporu · {target}</h1>
            <div class="meta">Oluşturulma: {stamp} · Yerel çalışma · Veri dışarı gönderilmedi</div>
        </header>
        <div class="card"><pre>{report}</pre></div>
        <footer>Bu rapor <strong>CHAZ OSINT Enterprise</strong> tarafından
        üretildi. Sonuçları ikinci bir kaynakla doğrulayın.</footer>
    </div>
</body>
</html>
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        return filepath
