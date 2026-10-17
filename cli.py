#!/usr/bin/env python3
"""
CHAZ OSINT - Headless komut satırı arayüzü.

Arayüz (GUI) olmadan tarama yapmak, otomasyona bağlamak ve raporu dışa
aktarmak için. Aynı `OSINTPipeline` çekirdeğini kullanır.

Kullanım:
    python cli.py scan "Ahmet Yılmaz"
    python cli.py scan ahmet_yilmaz --photo portre.jpg --json out.json --md out.md
    python cli.py scan "Ahmet Yılmaz" --html report.html
    python cli.py history --limit 10
    python cli.py stats
    python cli.py model                 # AI model raporu (kalibrasyon + ağırlıklar)
    python cli.py model --retrain       # geri bildirimle yeniden eğit
    python cli.py model --revert        # çevrimiçi güncellemeleri geri al
"""

import argparse
import sys

def _make_console_robust():
    """Konsolun kodlaması emoji/Türkçe karakterleri basamazsa çökmesin.

    Windows'ta varsayılan konsol kodlaması (ör. cp1254) raporda geçen
    emoji yüzünden `UnicodeEncodeError` verip taramayı çöpe atıyordu.
    Yazılamayan karakterler burada '?' ile değiştirilir; Türkçe metin
    doğru kalır ve komut başarıyla tamamlanır.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_make_console_robust()

from core.pipeline import OSINTPipeline
from database.storage import DatabaseManager
from exporters.html_dashboard_exporter import HTMLDashboardExporter
from exporters.json_exporter import JSONExporter
from exporters.markdown_exporter import MarkdownExporter
from config.settings import get_settings
from utils.logger import get_logger

log = get_logger("cli")


def _cmd_scan(args) -> int:
    settings = get_settings()
    pipeline = OSINTPipeline(settings=settings)

    print(f"[*] Hedef: {args.target}")
    if args.photo:
        print(f"[*] Fotoğraf: {args.photo}")

    def on_log(message):
        if not args.quiet:
            print(f"   {message}")

    session = pipeline.run(args.target, photo_path=args.photo, on_log=on_log)

    if not args.quiet:
        print()
    print(session.report)

    # Dışa aktarımlar
    data = session.to_dict()
    if args.json:
        JSONExporter(data).export(args.json)
        print(f"[+] JSON yazıldı: {args.json}")
    if args.md:
        MarkdownExporter(data).export(args.md)
        print(f"[+] Markdown yazıldı: {args.md}")
    if args.html:
        HTMLDashboardExporter(session.target_name, session.report).generate_html(args.html)
        print(f"[+] HTML yazıldı: {args.html}")

    print(f"[i] {session.get_summary()}")
    return 0 if session.verified or session.candidates else 1


def _cmd_history(args) -> int:
    db = DatabaseManager()
    rows = db.recent_scans(limit=args.limit)
    if not rows:
        print("[-] Kayıtlı tarama yok.")
        return 0
    print(f"{'ID':>4}  {'HEDEF':<24} {'GÜVEN':>7} {'HESAP':>6}  ZAMAN")
    print("-" * 72)
    for r in rows:
        print(f"{r['id']:>4}  {str(r['target'])[:24]:<24} "
              f"%{float(r['confidence'] or 0):>6.1f} {int(r['verified_count'] or 0):>6}  {r['timestamp']}")
    return 0


def _cmd_stats(args) -> int:
    settings = get_settings()
    db = DatabaseManager()
    print(settings.describe())
    print(f"Veritabanı: {db.stats()}")
    return 0


def _cmd_model(args) -> int:
    """AI modelinin durumunu, kalibrasyonunu ve karar ağırlıklarını yazdırır.

    "AI gerçekten iyi mi?" sorusunun sayısal cevabı: dürüst (holdout) metrikler,
    log-loss / Brier ve güvenilirlik tablosu.
    """
    from intelligence.ai_engine import get_engine

    engine = get_engine()

    if args.retrain:
        used = engine.retrain_from_feedback()
        print(f"[+] Yeniden eğitim tamam: {used} gerçek geri bildirim örneği." if used
              else "[-] Yeniden eğitim için yeterli gerçek geri bildirim yok.")
    if args.revert:
        print("[+] Çevrimiçi güncellemeler geri alındı." if engine.revert_online_updates()
              else "[-] Geri alınacak çevrimiçi güncelleme yok.")

    print("=" * 70)
    print(" 🧠 AI MODEL RAPORU")
    print("=" * 70)
    print(engine.status_report())

    print("\nKarar ağırlıkları (|ağırlık| büyükten küçüğe):")
    for row in engine.feature_weights(limit=10):
        print(f"   {row['weight']:>+8.3f}  {row['label']}")

    report = engine.calibration_report()
    print(f"\nKalibrasyon: Brier {report['brier']:.4f} · log-loss {report['log_loss']:.4f}"
          f" · T={report['temperature']:.2f}  ({report['count']} örnek)")
    print("   Güven aralığı    adet    tahmin   gerçek    fark")
    for row in report["bins"]:
        print(f"   {row['range']:<14} {row['count']:>4}    %{row['predicted']*100:>5.1f}   "
              f"%{row['observed']*100:>5.1f}   {row['gap']:>+6.3f}")
    print("   (fark = tahmin - gerçek; 0'a yakın olması kalibrasyonun iyi olduğunu gösterir)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chaz",
        description="CHAZ OSINT Enterprise - komut satırı arayüzü")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Yeni tarama başlat")
    scan.add_argument("target", help="Hedef isim veya kullanıcı adı")
    scan.add_argument("--photo", help="Analiz edilecek fotoğrafın yolu")
    scan.add_argument("--json", help="JSON çıktı dosyası")
    scan.add_argument("--md", help="Markdown çıktı dosyası")
    scan.add_argument("--html", help="HTML çıktı dosyası")
    scan.add_argument("--quiet", action="store_true", help="Adım günlüklerini gizle")
    scan.set_defaults(func=_cmd_scan)

    hist = sub.add_parser("history", help="Geçmiş taramaları listele")
    hist.add_argument("--limit", type=int, default=20)
    hist.set_defaults(func=_cmd_history)

    stats = sub.add_parser("stats", help="Sistem ve veritabanı özeti")
    stats.set_defaults(func=_cmd_stats)

    model = sub.add_parser("model", help="AI model durumu, kalibrasyon ve ağırlıklar")
    model.add_argument("--retrain", action="store_true",
                       help="Biriken geri bildirimle modeli yeniden eğit")
    model.add_argument("--revert", action="store_true",
                       help="Çevrimiçi (geri bildirim) güncellemelerini geri al")
    model.set_defaults(func=_cmd_model)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
