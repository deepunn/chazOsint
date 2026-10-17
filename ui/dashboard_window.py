"""
CHAZ OSINT Enterprise - Ana Operasyon Kontrol Paneli.

Rafine, kart tabanlı koyu tema; üst menü çubuğu + kenar çubuğu:
  • Tarama      : hedef isim/hesap taraması + AI doğrulama + konsol
  • Fotoğraf    : görsel önizleme + adli tıp AI analizi
  • Rapor       : son istihbarat raporu + dışa aktarma
  • Geçmiş      : yerel veritabanındaki taramalar

Kısayollar (Yardım > Kısayollar):
  Ctrl+N yeni tarama · F5 taramayı başlat · Ctrl+1..4 bölümler
  Ctrl+S HTML rapor · Ctrl+G ilişki ağı · Ctrl+L konsolu temizle · F1 yardım
"""

import json
import os
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext

from collectors.forensics.photo_forensics import PhotoForensicsAI
from intelligence.photo_identity import PhotoIdentityAI, REVERSE_SEARCH_PAGES
from intelligence.ai_engine import get_engine
from core.pipeline import OSINTPipeline
from database.storage import DatabaseManager
from config.settings import get_settings
from ui.network_graph_view import NetworkGraphView
from ui import theme as T
from ui.widgets import ModelStatusCard
from ui.codex_panel import CodexPanel
from exporters.html_dashboard_exporter import HTMLDashboardExporter
from exporters.json_exporter import JSONExporter
from exporters.markdown_exporter import MarkdownExporter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_PATH = os.path.join(_ROOT, "database", "ai_feedback.jsonl")


class DashboardWindow:
    """CHAZ HESAP BULMA ARACI"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CHAZ OSINT Enterprise · Hesap Bulma Paneli")
        self.root.geometry("1120x780")
        self.root.minsize(980, 660)
        self.root.configure(bg=T.BG_ROOT)

        self.settings = get_settings()
        self.db = DatabaseManager()
        self.ai = get_engine()
        self.pipeline = OSINTPipeline(ai_engine=self.ai, db=self.db, settings=self.settings)
        self.history_rows = []
        self.last_network_data = {}
        self.last_report = ""
        self.last_session = None
        self.last_candidates = []
        self._verified_view = []
        self._lead_view = []
        self.photo_path = None
        self.photo_analysis = None
        self.identity = None
        self.photo_thumb = None          # PhotoImage referansı (GC'yi önler)
        self._last_batch_targets = []
        self.active_page = "scan"
        self._nav = {}
        self._scanning = False
        self._scan_shortcut = ""
        self._ai_card_var = tk.BooleanVar(master=self.root, value=True)

        self._build_layout()
        self._build_menu()
        self.show_page("scan")
        self._refresh_model_status()
        self._load_history()

    # ==================================================================
    #  Üst menü çubuğu ve kısayollar
    # ==================================================================
    def _build_menu(self):
        """Uygulama menüsünü kurar.

        Menüler: Dosya / Tarama / Yapay Zeka / Görünüm / Yardım.
        "Yapay Zeka" menüsü modeli ŞEFFAF kılar: dürüst (holdout) metrikler,
        kalibrasyon tablosu, karar ağırlıkları, geri bildirim özeti ve karar
        eşiği tek yerden yönetilir.
        """
        menubar = tk.Menu(self.root)

        # --- Dosya -----------------------------------------------------
        dosya = tk.Menu(menubar, tearoff=0)
        dosya.add_command(label="🆕  Yeni Tarama", accelerator="Ctrl+N",
                          command=self._new_scan)
        dosya.add_command(label="🚀  Taramayı Başlat", accelerator="F5",
                          command=self.start_pipeline_thread)
        dosya.add_command(label="🎯  Hedef Alanına Git", accelerator="Ctrl+T",
                          command=self._focus_target)
        dosya.add_separator()
        aktar = tk.Menu(dosya, tearoff=0)
        aktar.add_command(label="🌐  HTML Rapor…", accelerator="Ctrl+S",
                          command=self.save_html_report)
        aktar.add_command(label="{ }  JSON…", command=self.save_json_report)
        aktar.add_command(label="📝  Markdown…", command=self.save_markdown_report)
        aktar.add_separator()
        aktar.add_command(label="📋  Raporu Panoya Kopyala", accelerator="Ctrl+Shift+C",
                          command=self.copy_report_to_clipboard)
        dosya.add_cascade(label="Dışa Aktar", menu=aktar)
        self._recent_scans_menu = tk.Menu(dosya, tearoff=0)
        dosya.add_cascade(label="Son Taramalar", menu=self._recent_scans_menu)
        dosya.add_separator()
        dosya.add_command(label="🎯  Toplu Tarama (çoklu hedef)…", accelerator="Ctrl+B",
                          command=self.show_batch_scan)
        dosya.add_command(label="⚙  Ayarlar…", command=self.show_settings)
        dosya.add_command(label="🗄  Veritabanı Araçları…", command=self.show_db_tools)
        dosya.add_separator()
        dosya.add_command(label="🚪  Çıkış", accelerator="Ctrl+Q", command=self._quit)
        menubar.add_cascade(label="Dosya", menu=dosya)

        # --- Tarama ----------------------------------------------------
        tarama = tk.Menu(menubar, tearoff=0)
        tarama.add_command(label="✔  Sonucu Doğru İşaretle (AI eğitimi)",
                           command=lambda: self.submit_feedback(1))
        tarama.add_command(label="✘  Sonucu Yanlış İşaretle (AI eğitimi)",
                           command=lambda: self.submit_feedback(0))
        tarama.add_separator()
        tarama.add_command(label="↗  Seçili Profili Tarayıcıda Aç",
                           accelerator="Çift tık", command=self.open_selected_profile)
        tarama.add_command(label="🔎  Seçili Sonucun AI Açıklaması…",
                           command=self.show_selected_explanation)
        tarama.add_command(label="🌐  İlişki Ağı", accelerator="Ctrl+G",
                           command=self.open_graph_view)
        menubar.add_cascade(label="Tarama", menu=tarama)

        # --- Yapay Zeka -------------------------------------------------
        ai = tk.Menu(menubar, tearoff=0)
        ai.add_command(label="📊  Model Durumu ve Kalibrasyon…", accelerator="Ctrl+R",
                       command=self.show_ai_report)
        ai.add_command(label="⚖  Karar Ağırlıkları…",
                       command=lambda: self.show_ai_report(only="weights"))
        ai.add_separator()
        ai.add_command(label="🗒  Geri Bildirim Özeti…", command=self.show_feedback_summary)
        ai.add_command(label="🔁  Geri Bildirimle Yeniden Eğit", accelerator="F9",
                       command=self.retrain_model)
        ai.add_command(label="↩  Çevrimiçi Güncellemeleri Geri Al…",
                       command=self.revert_ai_updates)
        ai.add_separator()
        ai.add_command(label="🎚  Karar Eşiğini Ayarla…", command=self.set_ai_threshold)
        ai.add_command(label="🧩  Nasıl Karar Veriyor? (katmanlar)",
                       command=self.show_decision_layers)
        ai.add_separator()
        ai.add_checkbutton(label="🧠  Kenar Menüsünde AI Kartını Göster",
                           variable=self._ai_card_var, command=self.toggle_ai_card)
        menubar.add_cascade(label="Yapay Zeka", menu=ai)

        # --- Görünüm ----------------------------------------------------
        gorunum = tk.Menu(menubar, tearoff=0)
        for key, label, accel in (("scan", "🔍  Tarama", "Ctrl+1"),
                                  ("photo", "🖼  Fotoğraf Analizi", "Ctrl+2"),
                                  ("report", "📄  Rapor", "Ctrl+3"),
                                  ("history", "🗂  Geçmiş", "Ctrl+4"),
                                  ("codex", "🧠  AI Sohbet", "F7")):
            gorunum.add_command(label=label, accelerator=accel,
                                command=lambda k=key: self.show_page(k))
        gorunum.add_separator()
        gorunum.add_command(label="🧹  Konsolu Temizle", accelerator="Ctrl+L",
                            command=self.clear_console)
        gorunum.add_command(label="⟳  Verileri Yenile (model + geçmiş)", accelerator="F6",
                            command=self._refresh_all)
        gorunum.add_command(label="💬  Yapay Zeka ile Konuş…", command=self.show_interactive_report)
        menubar.add_cascade(label="Görünüm", menu=gorunum)

        # --- Yardım -----------------------------------------------------
        yardim = tk.Menu(menubar, tearoff=0)
        yardim.add_command(label="⌨  Kısayollar", accelerator="F1",
                           command=self.show_shortcuts)
        yardim.add_command(label="🛡  Doğrulama Sağlayıcıları (tanılama)…",
                           command=self.show_providers_diagnostic)
        yardim.add_separator()
        yardim.add_command(label="✉  CHAZ OSINT Takımına Geribildirimi Gönder",
                           command=self.send_feedback_to_taim)
        yardim.add_command(label="ℹ  Hakkında", command=self.show_about)
        menubar.add_cascade(label="Yardım", menu=yardim)

        self.root.configure(menu=menubar)
        self.menubar = menubar
        self._refresh_recent_scans_menu()

        # Kısayollar (menüdeki akselere ile eşleşir)
        self.root.bind_all("<Control-n>", lambda _e: self._new_scan())
        self.root.bind_all("<Control-s>", lambda _e: self.save_html_report())
        self.root.bind_all("<Control-t>", lambda _e: self._focus_target())
        self.root.bind_all("<Control-r>", lambda _e: self.show_ai_report())
        self.root.bind_all("<Control-g>", lambda _e: self.open_graph_view())
        self.root.bind_all("<Control-l>", lambda _e: self.clear_console())
        self.root.bind_all("<Control-q>", lambda _e: self._quit())
        self.root.bind_all("<F1>", lambda _e: self.show_shortcuts())
        self.root.bind_all("<F5>", lambda _e: self.start_pipeline_thread())
        self.root.bind_all("<F6>", lambda _e: self._refresh_all())
        self.root.bind_all("<F7>", lambda _e: self.show_interactive_report())
        self.root.bind_all("<F9>", lambda _e: self.retrain_model())
        self.root.bind_all("<Control-b>", lambda _e: self.show_batch_scan())
        self.root.bind_all("<Control-Shift-C>", lambda _e: self.copy_report_to_clipboard())
        self.root.bind_all("<Control-Shift-c>", lambda _e: self.copy_report_to_clipboard())
        for index, key in enumerate(("scan", "photo", "report", "history"), start=1):
            self.root.bind_all(f"<Control-Key-{index}>",
                               lambda _e, k=key: self.show_page(k))

    def _new_scan(self):
        """Yeni tarama için hedef alanını sıfırlar ve odaklar."""
        self.show_page("scan")
        self.entry_target.set_value("")
        self.entry_target.focus_set()
        self.lb_candidates.delete(0, tk.END)
        self.last_candidates = []
        self._verified_view = []
        self._lead_view = []
        self._set_status("Yeni hedef bekleniyor…")

    def clear_console(self):
        self.console.delete("1.0", tk.END)

    def _quit(self):
        if self._scanning and not messagebox.askyesno(
                "Çıkış", "Tarama sürüyor. Yine de çıkmak istiyor musunuz?"):
            return
        self.root.destroy()

    def show_shortcuts(self):
        """Kısayol listesini gösterir."""
        kisa = [
            ("Ctrl+N", "Yeni tarama (hedef alanını sıfırlar)"),
            ("Ctrl+T", "Hedef alanına odaklan"),
            ("F5", "Taramayı başlat"),
            ("Ctrl+1 … Ctrl+4", "Tarama / Fotoğraf / Rapor / Geçmiş / Codex"),
            ("Ctrl+G", "İlişki ağı grafiği"),
            ("Ctrl+S", "HTML rapor kaydet"),
            ("Ctrl+Shift+C", "Raporu panoya kopyala"),
            ("Ctrl+R", "AI model durumu ve kalibrasyon"),
            ("F7", "Yapay Zeka ile konuş (Codex görünümü)"),
            ("F9", "AI modelini geri bildirimle yeniden eğit"),
            ("F6", "Verileri yenile (model + geçmiş)"),
            ("Ctrl+B", "Toplu tarama (çoklu hedef)"),
            ("Ctrl+L", "Konsolu temizle"),
            ("Çift tık", "Seçili profili tarayıcıda aç"),
            ("Sağ tık", "Sonuç menüsü (aç / kopyala / arşiv / geri bildirim)"),
            ("F1", "Bu pencere"),
            ("Ctrl+Q", "Çıkış"),
        ]
        self._info_dialog("Klavye Kısayolları",
                          "\n".join(f"  {k:<16} {v}" for k, v in kisa))

    def show_about(self):
        self._info_dialog(
            "Hakkında",
            f"CHAZ OSINT Enterprise v{self.settings.get('version')}\n"
            f"Yerel çalışır; harici API kullanmaz.\n\n"
            f"AI Core   : {self.ai.describe()}\n"
            f"Ayarlar   : {self.settings.describe()}\n\n"
            "Bazı platformlar (Instagram, TikTok, LinkedIn) profili anonim\n"
            "istemciye göstermediği için bu adresler 'aday' olarak sunulur.")

    def _info_dialog(self, title: str, body: str):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=T.BG_ROOT)
        win.transient(self.root)
        win.resizable(False, False)
        tk.Label(win, text=title, bg=T.BG_ROOT, fg=T.TEXT,
                 font=T.FONT_TITLE).pack(anchor="w", padx=18, pady=(16, 6))
        tk.Label(win, text=body, bg=T.BG_ROOT, fg=T.TEXT_SOFT, font=T.FONT_MONO_SM,
                 justify="left").pack(anchor="w", padx=18)
        T.RoundButton(win, "Kapat", command=win.destroy, width=110, height=34,
                      fill=T.ACCENT, parent_bg=T.BG_ROOT).pack(pady=16)
        win.bind("<Escape>", lambda _e: win.destroy())
        win.focus_set()

    def _text_dialog(self, title: str, body: str, width: int = 100, height: int = 26):
        """Uzun metinleri (model raporu, açıklamalar) kaydırılabilir pencerede gösterir."""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=T.BG_ROOT)
        win.transient(self.root)
        tk.Label(win, text=title, bg=T.BG_ROOT, fg=T.TEXT,
                 font=T.FONT_TITLE).pack(anchor="w", padx=18, pady=(16, 8))
        view = scrolledtext.ScrolledText(
            win, bg=T.LOG_BG, fg=T.LOG_FG, font=T.FONT_MONO_SM, relief=tk.FLAT,
            bd=0, highlightthickness=0, wrap="none", width=width, height=height)
        view.pack(fill=tk.BOTH, expand=True, padx=16)
        view.insert(tk.END, body)
        view.configure(state=tk.DISABLED)
        T.RoundButton(win, "Kapat", command=win.destroy, width=110, height=34,
                      fill=T.ACCENT, parent_bg=T.BG_ROOT).pack(pady=14)
        win.bind("<Escape>", lambda _e: win.destroy())
        win.focus_set()

    # ==================================================================
    #  Yapay Zeka menüsü
    # ==================================================================
    def _focus_target(self):
        """Tarama sayfasındaki hedef alanına odaklanır."""
        self.show_page("scan")
        self.entry_target.focus_set()

    def _refresh_all(self):
        """Model kartı ve geçmiş listesini yeniler."""
        self._refresh_model_status()
        self._load_history()
        self._set_status("Veriler yenilendi (model + geçmiş).");

    def _refresh_recent_scans_menu(self):
        """'Dosya > Son Taramalar' menüsünü veritabanından doldurur."""
        menu = getattr(self, "_recent_scans_menu", None)
        if menu is None:
            return
        menu.delete(0, tk.END)
        try:
            rows = self.db.recent_scans(limit=8)
        except Exception:
            rows = []
        if not rows:
            menu.add_command(label="(kayıt yok)", state=tk.DISABLED)
            return
        for row in rows:
            label = (f"#{row['id']} · {str(row['target'])[:22]} · "
                     f"%{float(row['confidence'] or 0):.0f} · "
                     f"{int(row['verified_count'] or 0)} hesap")
            menu.add_command(label=label,
                             command=lambda i=row["id"]: self.load_history_record(i))

    def load_history_record(self, scan_id: int):
        """Geçmiş bir taramanın raporunu açar."""
        record = self.db.get_scan(scan_id)
        if not record:
            messagebox.showwarning("Uyarı", f"#{scan_id} numaralı kayıt bulunamadı.")
            return
        self.history_view.delete("1.0", tk.END)
        self.history_view.insert(tk.END, record.get("report") or "")
        self.show_page("history")
        self._set_status(f"Geçmiş tarama #{scan_id} açıldı.")
        self._log(f"[i] Geçmiş kaydı açıldı: #{scan_id} ({record.get('target')})")

    def copy_report_to_clipboard(self):
        """Son raporu panoya kopyalar."""
        if not self._require_report():
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.last_report)
        except Exception as e:
            messagebox.showerror("Hata", f"Panoya kopyalanamadı: {e}")
            return
        self._set_status("Rapor panoya kopyalandı.")
        self._log("[+] Rapor panoya kopyalandı.")

    def show_ai_report(self, only: str = None):
        """Model durumu + kalibrasyon tablosu + karar ağırlıkları.

        Metrikler eğitimde GÖRÜLMEYEN (holdout) veriden gelir; böylece
        "AI %98 doğru" iddiası değil, ölçülmüş ve dürüst rakamlar gösterilir.
        """
        engine = self.ai
        parts = []
        if only != "weights":
            parts.append(engine.status_report())
            try:
                report = engine.calibration_report()
            except Exception as e:
                report = None
                parts.append(f"\nKalibrasyon okunamadı: {e}")
            if report:
                parts.append("\nGÜVENİLİRLİK (kalibrasyon) — \"%70 dedi, gerçekten %70 mi?\"")
                parts.append(f"  Brier {report['brier']:.4f} · log-loss "
                             f"{report['log_loss']:.4f} · sıcaklık T={report['temperature']:.2f}")
                parts.append("  güven aralığı   adet    tahmin   gerçek    fark")
                for row in report["bins"]:
                    parts.append(f"  {row['range']:<14} {row['count']:>4}    "
                                 f"%{row['predicted']*100:>5.1f}   %{row['observed']*100:>5.1f}   "
                                 f"{row['gap']:>+6.3f}")
                parts.append("  (fark = tahmin - gerçek; 0'a yakınlık kalibrasyonun "
                             "iyi olduğunu gösterir)")
        parts.append("\nKARAR AĞIRLIKLARI (|ağırlık| büyükten küçüğe):")
        for row in engine.feature_weights():
            parts.append(f"  {row['weight']:>+8.3f}  {row['label']}")
        parts.append("\nNOT: Pozitif ağırlık eşleşmeye, negatif ağırlık elemeye iter.\n"
                     "EĞİTİM ile DOĞRULAMA metrikleri arasındaki fark ezberleme (overfit)\n"
                     "göstergesidir; model kararı ayrıca kanıt kurallarıyla sınırlanır.")
        self._text_dialog("AI Model Durumu", "\n".join(parts))

    def show_feedback_summary(self):
        """database/ai_feedback.jsonl içindeki gerçek geri bildirimlerin özeti."""
        records = []
        try:
            with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            records = []
        positives = sum(1 for r in records if r.get("label") == 1)
        lines = [
            f"Geri bildirim dosyası : {FEEDBACK_PATH}",
            f"Toplam kayıt          : {len(records)}  "
            f"(doğru: {positives}, yanlış: {len(records) - positives})",
            f"Modelde kullanılan    : {self.ai.real_feedback_samples}",
            f"Çevrimiçi güncelleme  : {getattr(self.ai, 'online_updates', 0)}",
            "",
        ]
        if records:
            lines.append("Son kayıtlar (model bunlardan öğrenir):")
            for rec in records[-8:]:
                mark = "DOĞRU " if rec.get("label") else "YANLIŞ"
                lines.append(f"  [{mark}] {rec.get('platform', '?')} / "
                             f"{rec.get('handle', '-')} — {rec.get('url', '-')}")
        else:
            lines.append("Henüz geri bildirim yok. Sonuç listesinde bir adres seçip")
            lines.append("'Tarama > Sonucu Doğru/Yanlış İşaretle' ile modeli eğitebilirsiniz.")
        lines += ["", "Yeniden eğitim için en az 5 gerçek kayıt gerekir:",
                  "  Yapay Zeka > Geri Bildirimle Yeniden Eğit  (F9)"]
        self._text_dialog("AI Geri Bildirim Özeti", "\n".join(lines))

    def revert_ai_updates(self):
        """Çevrimiçi (geri bildirim) güncellemelerini geri alır."""
        updates = int(getattr(self.ai, "online_updates", 0) or 0)
        if not updates:
            messagebox.showinfo("AI Core", "Geri alınacak çevrimiçi güncelleme yok.")
            return
        if not messagebox.askyesno(
                "AI Core",
                f"{updates} çevrimiçi güncelleme geri alınacak ve son toplu "
                "eğitime dönülecek. Onaylıyor musunuz?"):
            return
        if self.ai.revert_online_updates():
            self.ai.save()
            self._refresh_model_status()
            self._log("[🧠] Çevrimiçi güncellemeler geri alındı (son toplu eğitime dönüldü).")
            messagebox.showinfo("AI Core", "Çevrimiçi güncellemeler geri alındı.")

    def set_ai_threshold(self):
        """Karar eşiğini ayarlar: yüksek eşik = daha az yanlış pozitif."""
        from intelligence.ai_engine import DECISION_THRESHOLD

        win = tk.Toplevel(self.root)
        win.title("Karar Eşiği")
        win.configure(bg=T.BG_ROOT)
        win.transient(self.root)
        tk.Label(win, text="Karar Eşiği", bg=T.BG_ROOT, fg=T.TEXT,
                 font=T.FONT_TITLE).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Label(win, text="Model olasılığı bu eşiğin altında kalan adaylar elenir.\n"
                           "Yükseltmek yanlış-pozitifi azaltır, hatırlamayı düşürür.",
                 bg=T.BG_ROOT, fg=T.TEXT_SOFT, font=T.FONT_TINY,
                 justify="left").pack(anchor="w", padx=18)
        value = tk.DoubleVar(value=float(self.ai.threshold))
        readout = tk.Label(win, text=f"{self.ai.threshold:.2f}", bg=T.BG_ROOT,
                           fg=T.ACCENT, font=(T.FONT_FAMILY, 13, "bold"))
        scale = tk.Scale(win, from_=0.30, to=0.90, resolution=0.01,
                         orient=tk.HORIZONTAL, variable=value, length=330,
                         bg=T.BG_ROOT, fg=T.TEXT, highlightthickness=0,
                         troughcolor=T.BG_CARD_ALT, showvalue=False,
                         command=lambda v: readout.configure(text=f"{float(v):.2f}"))
        scale.pack(padx=18, pady=(8, 0))
        readout.pack()
        row = tk.Frame(win, bg=T.BG_ROOT)
        row.pack(pady=14)

        def apply():
            self.ai.threshold = round(float(value.get()), 3)
            self.ai.save()
            self._refresh_model_status()
            self._log(f"[🧠] Karar eşiği {self.ai.threshold:.2f} olarak güncellendi.")
            win.destroy()

        def reset():
            self.ai.threshold = float(DECISION_THRESHOLD)
            value.set(self.ai.threshold)
            readout.configure(text=f"{self.ai.threshold:.2f}")

        T.RoundButton(row, "Kaydet", command=apply, width=110, height=34,
                      fill=T.ACCENT, parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        T.RoundButton(row, "Varsayılan", command=reset, width=120, height=34,
                      fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        T.RoundButton(row, "İptal", command=win.destroy, width=90, height=34,
                      fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        win.bind("<Escape>", lambda _e: win.destroy())
        win.focus_set()

    def toggle_ai_card(self):
        """Kenar menüsündeki AI kartını gösterir/gizler."""
        card = getattr(self, "side_model_card", None)
        if card is None:
            return
        if self._ai_card_var.get():
            card.pack(fill=tk.X)
        else:
            card.pack_forget()

    def show_decision_layers(self):
        """AI'nın karar katmanlarını açıklar (şeffaflık)."""
        body = """AI kararı DÖRT katmanda verilir ve her adım rapora yazılır:

  1) İLGİ KAPISI — aday biçim olarak isme bağlı değilse elenir (model ne
     derse desin). Gevşek biçimlerde (harf sırası, ünlü düşmesi, kısaltma)
     daha yüksek benzerlik şartı aranır.

  2) ÇELİŞKİ KAPISI — TAM AD sorgusunda sayfa açıkça BAŞKA birini gösteriyorsa
     eşleşme reddedilir. Kullanıcı adı sorgusunda (tek kelime, ör. "hamigumi")
     bu beklenen durumdur ve çelişki sayılmaz.

  3) KANIT KURALI — sayfa kanıtı aranır:
       (A) sayfada hedefin görünen adı okundu,
       (B) sayfa başlığı/meta hedef handle'ını doğruladı,
       (C) platform içerik doğrulaması yüksek (sayfa gerçekten profil).
     Kanıt varsa eşleşme kesinleşir (yapay zeka olasılığı ≥ %85).

  4) MODEL + EŞİK — kanıt yoksa kararı kalibre edilmiş olasılık verir; eşiğin
     altındaki adaylar elenir. Eşik 'Yapay Zeka > Karar Eşiğini Ayarla' ile
     değiştirilebilir.

DUVAR (wall): Instagram/TikTok/LinkedIn anonim istemciye profili göstermez.
  Bu durumda "kanıt yok" bilgisi "hesap yok" DEĞİLDİR: aday silinmez,
  "elle kontrol" olarak sunulur ve rapora açıkça yazılır.

Not: Mor olarak işaretlenen duvarlı adreslerde olasılık ÖLÇÜLEMEZ; arayüz
  bu yüzden "—" gösterir, sahte bir yüzde üretmez."""
        self._text_dialog("AI Nasıl Karar Veriyor?", body)

    def show_providers_diagnostic(self):
        """Duvarlı platformlarda hangi doğrulama yollarının açık olduğunu gösterir."""
        providers, impersonation, session_on, error = [], False, False, ""
        try:
            from collectors.http_client import HttpClient
            from collectors.platform_verifiers import VerifierRegistry

            client = HttpClient(settings=self.settings)
            registry = VerifierRegistry(client, self.settings)
            impersonation = client.impersonation_available
            session_on = registry.session_enabled
            for provider in registry.providers:
                platforms = ", ".join(provider.supports) or "—"
                providers.append(f"  - {provider.name:<20} platformlar: {platforms}")
        except Exception as e:      # tanılama asla uygulamayı bozmaz
            error = str(e)

        lines = [
            "Duvarlı platformlarda (Instagram / TikTok / LinkedIn) profil",
            "doğrulaması şu yollarla yapılır:",
            "",
            f"  Chrome taklidi (TLS/JA3 + HTTP2) : "
            f"{'AÇIK' if impersonation else 'KAPALI'}",
            "      Kapalıysa Instagram profil meta verisi (og:title = görünen ad)",
            "      ve TikTok embed verisi alınamaz. Açmak için:  pip install curl_cffi",
            "",
            f"  Kendi oturumun (çerez)            : "
            f"{'AÇIK' if session_on else 'kapalı'}",
            "      Açmak için config/session_cookies.txt dosyasına kendi",
            "      çerezlerinizi koyun (gizli bilgidir; sürüm kontrolüne eklemeyin).",
            "",
            "Etkin doğrulama sağlayıcıları (sırayla denenir):",
        ]
        if error:
            lines.append(f"  tanılama hatası: {error}")
        else:
            lines.extend(providers or ["  (sağlayıcı yok)"])
        lines += ["",
                  "Sıra ölçüme dayanır: kanıt ürettiği ÖLÇÜLMÜŞ yollar önce denenir",
                  "(TikTok embed → Instagram HTML → TikTok oEmbed → Instagram API).",
                  "Sağlayıcı 'kanıt yok' derse aday silinmez; 'elle kontrol' olarak kalır."]
        self._text_dialog("Doğrulama Sağlayıcıları (tanılama)", "\n".join(lines))

    # ==================================================================
    #  Codex / AI sohbet görünümü
    # ==================================================================
    def show_interactive_report(self):
        """Yapay Zeka ile Konuş sayfasını açar (F7).

        Tarama varsa özet yüklenir; yoksa panel nazik bir yönlendirme gösterir.
        """
        self._sync_codex_context()
        self.codex_panel.set_report(self._codex_report())
        self.show_page("codex")

    def _sync_codex_context(self):
        """Sohbet panelinin soruları yanıtlaması için bağlamı tazeler."""
        if self.last_session is not None:
            session = self.last_session
            target = (getattr(session, "target_name", "") 
                      or self.entry_target.get_value())
            verified = list(getattr(session, "verified", []) or [])
            tentative = list(getattr(session, "tentative", []) or [])
            confidence = float(getattr(session, "confidence", 0.0) or 0.0)
        else:
            target = self.entry_target.get_value()
            verified = list(self._verified_view)
            tentative = list(self._lead_view)
            confidence = 0.0
        if hasattr(self, "codex_panel"):
            self.codex_panel.set_context({
                "target": target, "verified": verified,
                "tentative": tentative, "confidence": confidence,
            })

    def _codex_report(self) -> str:
        """Son taramanın sohbet görünümü için okunabilir özeti."""
        session = self.last_session
        if session is None:
            return ""
        target = getattr(session, "target_name", "-")
        verified = list(getattr(session, "verified", []) or [])
        tentative = list(getattr(session, "tentative", []) or [])
        confidence = float(getattr(session, "confidence", 0.0) or 0.0)
        walls = [c for c in tentative if c.get("wall")]

        lines = [
            "══════════ CHAZ OSINT · AI ÖZET ══════════",
            f"Hedef             : {target}",
            f"Doğrulanan hesap  : {len(verified)}",
            f"Elle kontrol aday : {len(tentative)}  (platform duvarı: {len(walls)})",
            f"Genel güven       : %{confidence:.1f}",
            "",
        ]
        if verified:
            lines.append("✔ Doğrulanan hesaplar:")
            for c in verified[:10]:
                lines.append(f"   {c.get('platform')} · {c.get('handle')} · "
                             f"AI %{float(c.get('ai_prob', 0) or 0)*100:.0f} · "
                             f"{c.get('url', '-')}")
        else:
            lines.append("✔ Doğrulanan hesap yok — kanıtsız/tesadüfi eşleşmeler elendi.")
        if walls:
            lines += ["", "🔒 Platform duvarı (anonim doğrulanamadı — 'hesap yok' değildir):"]
            for w in walls[:8]:
                lines.append(f"   {w.get('platform')} · {w.get('url', '-')}")
        lines += ["",
                  "Sorularınız için alttaki kutuya 'özet', 'kanıtlar', 'adaylar',",
                  "'duvar' veya 'güven' yazıp 'Sor' düğmesine basın."]
        return "\n".join(lines)

    def _update_codex_panel(self, session):
        """Tarama bitince sohbet sayfasını tazeler (thread'den UI'ya)."""
        self.last_session = session
        self._sync_codex_context()
        self.codex_panel.set_report(self._codex_report())

    def send_feedback_to_taim(self):
        """Geribildirim/öneri için bilgilendirme penceresi (yerel çalışır)."""
        self._info_dialog(
            "Geribildirim Gönder",
            "CHAZ OSINT — Geribildirim\n"
            "=========================\n"
            "Bu sürüm tamamen YEREL çalışır; hiçbir veriniz sunucuya\n"
            "gönderilmez. Bir hata/öneri bildirmek için elinizdeki kanıtla:\n"
            "  • Konsoldaki son satırlar,\n"
            "  • logs/chaz.log dosyası,\n"
            "  • Adım adım tekrar üretme notu\n"
            "yeterlidir.\n\n"
            "Not: AI'yı yerinde iyileştirmek için sonuç listesinden bir adres\n"
            "seçip 'Doğru'/'Yanlış' işaretleyin — model anında öğrenir.")

    # ==================================================================
    #  Toplu tarama (çoklu hedef)
    # ==================================================================
    def show_batch_scan(self):
        """Birden çok hedefi sırayla taramak için pencere açar (Ctrl+B)."""
        win = tk.Toplevel(self.root)
        win.title("Toplu Tarama")
        win.configure(bg=T.BG_ROOT)
        win.transient(self.root)
        win.geometry("560x460")

        tk.Label(win, text="Toplu Tarama", bg=T.BG_ROOT, fg=T.TEXT,
                 font=T.FONT_TITLE).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Label(win, text="Her satıra bir hedef yazın (ad veya kullanıcı adı).\n"
                           "Hedefler sırayla taranır; sonuçlar konsola ve rapora yazılır.",
                 bg=T.BG_ROOT, fg=T.TEXT_SOFT, font=T.FONT_TINY,
                 justify="left").pack(anchor="w", padx=18)

        box = tk.Text(win, height=12, bg=T.BG_CARD_ALT, fg=T.TEXT,
                      insertbackground=T.ACCENT, font=T.FONT_BODY, relief=tk.FLAT,
                      bd=0, highlightthickness=0, wrap="word")
        box.pack(fill=tk.BOTH, expand=True, padx=18, pady=(10, 0))
        box.insert(tk.END, "\n".join(self._last_batch_targets))
        box.focus_set()

        row = tk.Frame(win, bg=T.BG_ROOT)
        row.pack(pady=14)

        def start():
            raw = box.get("1.0", tk.END)
            targets = []
            for line in raw.splitlines():
                value = line.strip()
                if value and value not in targets:
                    targets.append(value)
            if not targets:
                messagebox.showwarning("Toplu Tarama", "En az bir hedef girin.")
                return
            if self._scanning:
                messagebox.showinfo("Tarama sürüyor",
                                    "Önce mevcut taramanın bitmesini bekleyin.")
                return
            self._last_batch_targets = targets[:50]
            win.destroy()
            self._scanning = True
            self.btn_run.set_enabled(False)
            self.console.delete("1.0", tk.END)
            self.lb_candidates.delete(0, tk.END)
            self.last_candidates = []
            self._verified_view = []
            self._lead_view = []
            self.show_page("scan")
            threading.Thread(target=self._run_batch, args=(targets,),
                             daemon=True).start()

        T.RoundButton(row, f"Taramayı Başlat", icon="🚀", command=start,
                      width=200, height=38, fill=T.ACCENT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        T.RoundButton(row, "İptal", command=win.destroy, width=100, height=38,
                      fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        win.bind("<Escape>", lambda _e: win.destroy())

    def _run_batch(self, targets):
        """Hedefleri sırayla tarar (arka plan iş parçacığı)."""
        total = len(targets)
        results = []
        on_log = lambda message: self.root.after(0, self._log, message)
        for index, target in enumerate(targets, start=1):
            self.root.after(0, self._log,
                            f"\n═════ [{index}/{total}] Hedef: {target} ═════")
            self.root.after(0, self._set_status,
                            f"[{index}/{total}] '{target}' taranıyor…", T.ACCENT)
            try:
                session = self.pipeline.run(target, photo_path=None, on_log=on_log)
                self.last_session = session
                self.last_report = session.report
                self.last_network_data = session.network
                self.root.after(0, self._update_report_view, session.report)
                self.root.after(0, self._populate_candidates,
                                session.verified, session.tentative)
                self.root.after(0, self._refresh_ai_panel,
                                session.verified, session.tentative)
                self.root.after(0, self._update_codex_panel, session)
                self.root.after(0, self._refresh_model_status)
                results.append((target, len(session.verified),
                                len(session.tentative), session.confidence))
            except Exception as e:              # bir hedef çökerse tarama devam eder
                self.root.after(0, self._log, f"[-] {target}: {e}")
                results.append((target, 0, 0, 0.0))
        self.root.after(0, self._finish_scan)
        self.root.after(0, self._load_history)
        self.root.after(0, self._batch_summary, results)

    def _batch_summary(self, results):
        lines = ["\n═════ TOPLU TARAMA ÖZETİ ═════",
                 f"{'Hedef':<28}{'Doğrulanan':>11}{'Aday':>7}{'Güven':>8}"]
        for target, verified, tentative, confidence in results:
            lines.append(f"{target[:26]:<28}{verified:>11}{tentative:>7}"
                         f"{'%' + format(confidence, '.0f'):>8}")
        summary = "\n".join(lines)
        for line in summary.splitlines():
            self._log(line)
        total_verified = sum(r[1] for r in results)
        best = max(results, key=lambda r: (r[1], r[3]), default=None)
        tail = f" · en iyi: {best[0]} (%{best[3]:.0f})" if best and best[1] else ""
        self._set_status(f"Toplu tarama bitti: {len(results)} hedef · "
                         f"{total_verified} doğrulanan hesap{tail}", T.SUCCESS)

    # ==================================================================
    #  Ayarlar
    # ==================================================================
    def show_settings(self):
        """Zaman aşımı, iş parçacığı, önbellek ve eşik ayarlarını düzenler."""
        fields = [
            ("timeout", "HTTP zaman aşımı (sn)", int, "Ağ istekleri için üst sınır."),
            ("retries", "Yeniden deneme", int, "Başarısız istekte kaç kez tekrar denenecek."),
            ("backoff", "Geri çekilme (sn)", float, "Yeniden denemeler arası bekleme."),
            ("max_threads", "Paralel iş parçacığı", int, "Aynı anda kaç istek."),
            ("max_handle_variants", "Maks. kullanıcı adı", int, "Denenecek biçim sayısı."),
            ("cache_ttl", "Önbellek ömrü (sn)", int, "0 önbelleği kapatır."),
            ("rate_min", "Min gecikme (sn)", float, "İstekler arası alt sınır."),
            ("rate_max", "Maks gecikme (sn)", float, "İstekler arası üst sınır."),
        ]

        win = tk.Toplevel(self.root)
        win.title("Ayarlar")
        win.configure(bg=T.BG_ROOT)
        win.transient(self.root)
        win.resizable(False, False)

        tk.Label(win, text="Ayarlar", bg=T.BG_ROOT, fg=T.TEXT,
                 font=T.FONT_TITLE).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Label(win, text="Değişiklikler config/settings.json dosyasına yazılır.",
                 bg=T.BG_ROOT, fg=T.TEXT_MUTED,
                 font=T.FONT_TINY).pack(anchor="w", padx=18)

        form = tk.Frame(win, bg=T.BG_ROOT)
        form.pack(fill=tk.X, padx=18, pady=(12, 0))
        form.grid_columnconfigure(1, weight=1)

        entries = {}
        for row_index, (key, label, caster, hint) in enumerate(fields):
            tk.Label(form, text=label, bg=T.BG_ROOT, fg=T.TEXT_SOFT,
                     font=T.FONT_SMALL, anchor="w").grid(
                row=row_index, column=0, sticky="w", pady=4)
            current = self.settings.get(key) if hasattr(self.settings, "get") \
                else getattr(self.settings, key, "")
            var = tk.StringVar(value=str(current))
            tk.Entry(form, textvariable=var, width=12, bg=T.BG_CARD_ALT,
                     fg=T.TEXT, insertbackground=T.ACCENT, relief=tk.FLAT,
                     bd=6).grid(row=row_index, column=1, sticky="w", padx=(10, 0))
            tk.Label(form, text=hint, bg=T.BG_ROOT, fg=T.TEXT_MUTED,
                     font=T.FONT_TINY, anchor="w").grid(
                row=row_index, column=2, sticky="w", padx=(10, 0))
            entries[key] = (var, caster)

        # AI karar eşiği (modelde saklanır)
        tk.Label(form, text="AI karar eşiği", bg=T.BG_ROOT, fg=T.TEXT_SOFT,
                 font=T.FONT_SMALL, anchor="w").grid(
            row=len(fields), column=0, sticky="w", pady=4)
        threshold_var = tk.StringVar(value=f"{float(self.ai.threshold):.2f}")
        tk.Entry(form, textvariable=threshold_var, width=12, bg=T.BG_CARD_ALT,
                 fg=T.TEXT, insertbackground=T.ACCENT, relief=tk.FLAT,
                 bd=6).grid(row=len(fields), column=1, sticky="w", padx=(10, 0))
        tk.Label(form, text="0.30 – 0.90 · yüksek = az yanlış pozitif",
                 bg=T.BG_ROOT, fg=T.TEXT_MUTED, font=T.FONT_TINY).grid(
            row=len(fields), column=2, sticky="w", padx=(10, 0))

        status = tk.Label(win, text="", bg=T.BG_ROOT, fg=T.TEXT_MUTED,
                          font=T.FONT_TINY)
        status.pack(anchor="w", padx=18, pady=(8, 0))

        def apply():
            errors = []
            for key, (var, caster) in entries.items():
                raw = var.get().strip()
                try:
                    self.settings.set(key, caster(raw))
                except (TypeError, ValueError):
                    errors.append(key)
            try:
                threshold = float(threshold_var.get().strip())
                if not 0.30 <= threshold <= 0.90:
                    raise ValueError
                self.ai.threshold = round(threshold, 3)
                self.ai.save()
            except (TypeError, ValueError):
                errors.append("ai karar eşiği")
            if errors:
                status.configure(text="Geçersiz değer: " + ", ".join(errors),
                                 fg=T.DANGER)
                return
            self.settings.save()
            self._refresh_model_status()
            self._log("[⚙] Ayarlar güncellendi ve config/settings.json'a yazıldı.")
            win.destroy()

        row = tk.Frame(win, bg=T.BG_ROOT)
        row.pack(pady=14)
        T.RoundButton(row, "Kaydet", icon="💾", command=apply, width=140, height=36,
                      fill=T.ACCENT, parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        T.RoundButton(row, "İptal", command=win.destroy, width=100, height=36,
                      fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        win.bind("<Escape>", lambda _e: win.destroy())

    # ==================================================================
    #  Veritabanı araçları
    # ==================================================================
    def show_db_tools(self):
        """Yerel veritabanı istatistikleri + bakım işlemleri."""
        try:
            stats = self.db.stats()
        except Exception as e:
            stats = {"scans": 0, "fingerprints": 0, "path": str(e)}
        size = ""
        try:
            size = f"{os.path.getsize(stats['path']) / 1024:.1f} KB"
        except OSError:
            size = "-"

        win = tk.Toplevel(self.root)
        win.title("Veritabanı Araçları")
        win.configure(bg=T.BG_ROOT)
        win.transient(self.root)
        win.resizable(False, False)

        tk.Label(win, text="Veritabanı Araçları", bg=T.BG_ROOT, fg=T.TEXT,
                 font=T.FONT_TITLE).pack(anchor="w", padx=18, pady=(16, 2))

        info = tk.Label(
            win,
            text=(f"Konum           : {stats.get('path', '-')}\n"
                  f"Dosya boyutu    : {size}\n"
                  f"Tarama kaydı    : {stats.get('scans', 0)}\n"
                  f"Görsel parmak izi: {stats.get('fingerprints', 0)}"),
            bg=T.BG_CARD_ALT, fg=T.TEXT_SOFT, font=T.FONT_MONO_SM,
            justify="left", anchor="w", padx=14, pady=12)
        info.pack(fill=tk.X, padx=18, pady=(10, 0))

        status = tk.Label(win, text="", bg=T.BG_ROOT, fg=T.TEXT_MUTED,
                          font=T.FONT_TINY)
        status.pack(anchor="w", padx=18, pady=(8, 0))

        def refresh():
            try:
                st = self.db.stats()
                new_size = f"{os.path.getsize(st['path']) / 1024:.1f} KB"
            except Exception:
                st, new_size = {"scans": 0, "path": "-"}, "-"
            info.configure(
                text=(f"Konum           : {st.get('path', '-')}\n"
                      f"Dosya boyutu    : {new_size}\n"
                      f"Tarama kaydı    : {st.get('scans', 0)}\n"
                      f"Görsel parmak izi: {st.get('fingerprints', 0)}"))
            self._load_history()

        def clear_history():
            if not messagebox.askyesno("Veritabanı", "Tüm tarama geçmişi silinsin mi?"):
                return
            removed = self.db.clear_history()
            status.configure(text=f"{removed} kayıt silindi.", fg=T.SUCCESS)
            refresh()

        def clear_fingerprints():
            if not messagebox.askyesno("Veritabanı",
                                       "Görsel parmak izleri silinsin mi?"):
                return
            removed = self.db.clear_fingerprints()
            status.configure(text=f"{removed} parmak izi silindi.", fg=T.SUCCESS)
            refresh()

        row = tk.Frame(win, bg=T.BG_ROOT)
        row.pack(pady=14)
        T.RoundButton(row, "Yenile", icon="⟳", command=refresh, width=120, height=36,
                      fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        T.RoundButton(row, "Geçmişi Temizle", icon="🧹", command=clear_history,
                      width=180, height=36, fill=T.DANGER, fg="#3A0B0C",
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        T.RoundButton(row, "İzleri Temizle", icon="🧽", command=clear_fingerprints,
                      width=170, height=36, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        T.RoundButton(row, "Kapat", command=win.destroy, width=100, height=36,
                      fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_ROOT).pack(side=tk.LEFT, padx=4)
        win.bind("<Escape>", lambda _e: win.destroy())

    def show_selected_explanation(self):
        """Seçili sonucun AI kararını ve özellik katkılarını gösterir."""
        cand = self._selected_candidate()
        if not cand:
            messagebox.showwarning("Uyarı", "Önce sonuç listesinden bir adres seçin.")
            return
        self._show_candidate_explanation(cand)

    def _show_candidate_explanation(self, cand: dict):
        """Bir adayın karar açıklaması: ağırlık x değer = katkı tablosu."""
        feats = cand.get("ai_features") or {}
        lines = [
            f"{cand.get('platform', '?')} / {cand.get('handle', '-')}",
            f"URL          : {cand.get('url', '-')}",
            f"Görünen ad   : {cand.get('display_name') or '(okunamadı)'}",
            f"AI olasılık  : %{float(cand.get('ai_prob', 0.0))*100:.1f}",
            f"Karar        : {cand.get('ai_reason', '-')}",
            f"Karar dayanağı: {feats.get('decision_basis', '-')}",
        ]
        if cand.get("wall"):
            lines.append("Durum        : platform duvarı — olasılık ÖLÇÜLEMEDİ "
                         "(hesap yok demek değildir)")
        rows = []
        try:
            rows = self.ai.explain_rows(feats)
        except Exception:
            rows = []
        if rows:
            lines += ["", "Özellik katkıları (katkı = ağırlık x değer):"]
            for row in rows:
                lines.append(f"  {row['contribution']:>+8.3f} = {row['weight']:>+7.3f} x "
                             f"{row['value']:>5.3f}   {row['label']}")
            lines += ["", "Pozitif katkı eşleşmeye, negatif katkı elemeye iter."]
        self._text_dialog("AI Karar Açıklaması", "\n".join(lines))

    def _candidate_menu(self, event):
        """Sonuç listesinde sağ tık menüsü (gerçek iş akışı kısayolları)."""
        index = self.lb_candidates.nearest(event.y)
        if index < 0 or index >= len(self.last_candidates):
            return
        self.lb_candidates.selection_clear(0, tk.END)
        self.lb_candidates.selection_set(index)
        self.lb_candidates.activate(index)
        cand = self._selected_candidate()
        if not cand:
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Profili Tarayıcıda Aç",
                         command=self.open_selected_profile)
        menu.add_command(label="Bağlantıyı Kopyala",
                         command=lambda u=cand.get("url", ""): self._copy_text(u))
        menu.add_command(label="Arşiv Bağlantısını Aç",
                         command=lambda u=cand.get("archive", ""): self._open_url(u))
        menu.add_separator()
        menu.add_command(label="Söyledi: Bu Sonuç DOĞRU (AI eğitimi)",
                         command=lambda: self.submit_feedback(1))
        menu.add_command(label="Söyledi: Bu Sonuç YANLIŞ (AI eğitimi)",
                         command=lambda: self.submit_feedback(0))
        menu.add_separator()
        menu.add_command(label="AI Karar Açıklaması…",
                         command=lambda c=cand: self._show_candidate_explanation(c))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_text(self, text: str):
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status("Bağlantı panoya kopyalandı.")
        except Exception as e:
            self._log(f"[-] Panoya kopyalanamadı: {e}")

    def _open_url(self, url: str):
        if url:
            webbrowser.open(url)

    # ==================================================================
    #  Yerleşim
    # ==================================================================
    def _build_layout(self):
        self._build_header()

        body = tk.Frame(self.root, bg=T.BG_ROOT)
        body.pack(fill=tk.BOTH, expand=True)

        self._build_sidebar(body)

        self.content = tk.Frame(body, bg=T.BG_ROOT)
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        # Codex/AI sohbet katmanı: content oluştuktan SONRA kurulmalı
        # (eskiden __init__ içinde self.content yokken oluşturuluyordu -> çökme).
        self.codex_panel = CodexPanel(self.content)

        self.pages = {
            "scan": self._build_scan_page(self._sidebar),
            "photo": self._build_photo_page(),
            "report": self._build_report_page(),
            "history": self._build_history_page(),
            "codex": self.codex_panel,
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self._build_status_bar()

    def _build_status_bar(self):
        """Alt durum çubuğu: hedef, ilerleme ve son işlem bilgisi."""
        tk.Frame(self.root, bg=T.BORDER, height=1).pack(fill=tk.X, side=tk.BOTTOM)
        bar = tk.Frame(self.root, bg=T.BG_HEADER, height=28)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self.lbl_status = tk.Label(bar, text="Hazır", bg=T.BG_HEADER, fg=T.TEXT_SOFT,
                                   font=T.FONT_SMALL, anchor="w")
        self.lbl_status.pack(side=tk.LEFT, padx=14)
        self.lbl_status_right = tk.Label(bar, text="", bg=T.BG_HEADER, fg=T.TEXT_MUTED,
                                         font=T.FONT_SMALL, anchor="e")
        self.lbl_status_right.pack(side=tk.RIGHT, padx=14)

    def _set_status(self, text: str, color: str = None):
        try:
            self.lbl_status.configure(text=text, fg=color or T.TEXT_SOFT)
        except tk.TclError:
            pass

    def _build_header(self):
        header = tk.Frame(self.root, bg=T.BG_HEADER, height=66)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        # İnce vurgu şeridi: accent -> info gradyanı üst çubuğu belirginleştirir.
        strip = tk.Canvas(self.root, height=2, bg=T.BG_HEADER,
                          highlightthickness=0, bd=0)
        strip.pack(fill=tk.X)
        strip.bind("<Configure>", lambda _e: self._paint_gradient(strip))

        left = tk.Frame(header, bg=T.BG_HEADER)
        left.pack(side=tk.LEFT, padx=20)

        logo = tk.Canvas(left, width=34, height=34, bg=T.BG_HEADER, highlightthickness=0)
        logo.pack(side=tk.LEFT, pady=16)
        T.round_rect(logo, 2, 2, 32, 32, 9, fill=T.ACCENT, outline=T.ACCENT)
        logo.create_text(17, 17, text="C", fill="#FFFFFF", font=(T.FONT_FAMILY, 15, "bold"))

        titles = tk.Frame(left, bg=T.BG_HEADER)
        titles.pack(side=tk.LEFT, padx=(12, 0))
        tk.Label(titles, text="CHAZ OSINT", bg=T.BG_HEADER, fg=T.TEXT,
                 font=T.FONT_DISPLAY).pack(anchor="w")
        tk.Label(titles, text="Enterprise Intelligence Suite", bg=T.BG_HEADER,
                 fg=T.TEXT_MUTED, font=T.FONT_SMALL).pack(anchor="w")

        right = tk.Frame(header, bg=T.BG_HEADER)
        right.pack(side=tk.RIGHT, padx=20)
        self.lbl_status_dot = tk.Label(right, text="●", bg=T.BG_HEADER, fg=T.SUCCESS,
                                       font=(T.FONT_FAMILY, 12))
        self.lbl_status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self.lbl_model = tk.Label(right, text="", bg=T.BG_HEADER, fg=T.TEXT_SOFT,
                                  font=T.FONT_SMALL)
        self.lbl_model.pack(side=tk.LEFT)

    def _paint_gradient(self, canvas):
        """Canvas'a yatay accent->info gradyanı çizer (üst vurgu şeridi)."""
        try:
            canvas.delete("grad")
            width = canvas.winfo_width()
            if width <= 2:
                return
            steps = max(1, width // 3)
            for i in range(steps):
                t = i / max(1, steps - 1)
                color = T.blend(T.ACCENT, T.INFO, t)
                x1 = int(i * width / steps)
                x2 = int((i + 1) * width / steps) + 1
                canvas.create_rectangle(x1, 0, x2, 2, fill=color, outline=color,
                                        tags="grad")
        except tk.TclError:
            pass

    def _build_sidebar(self, parent):
        sidebar = tk.Frame(parent, bg=T.BG_SIDEBAR, width=236)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        tk.Frame(parent, bg=T.BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        # Üst logo
        top = tk.Frame(sidebar, bg=T.BG_SIDEBAR)
        top.pack(fill=tk.X, padx=18, pady=(16, 2))
        tk.Label(top, text="CHAZ", bg=T.BG_SIDEBAR, fg=T.ACCENT,
                 font=(T.FONT_FAMILY, 15, "bold")).pack(side=tk.LEFT)
        tk.Label(top, text="OSINT", bg=T.BG_SIDEBAR, fg=T.TEXT_SOFT,
                 font=(T.FONT_FAMILY, 15, "bold")).pack(side=tk.LEFT, padx=(2, 0))

        tk.Frame(sidebar, bg=T.BORDER_SOFT, height=1).pack(fill=tk.X, padx=16, pady=(12, 0))
        tk.Label(sidebar, text="MENÜ", bg=T.BG_SIDEBAR, fg=T.TEXT_MUTED,
                 font=T.FONT_TINY).pack(anchor="w", padx=20, pady=(12, 6))

        # Kısayol etiketleri menüyle aynıdır (Yardım > Kısayollar).
        for key, label, icon, accel in (("scan", "Tarama", "🔍", "Ctrl+1"),
                                        ("photo", "Fotoğraf Analizi", "🖼", "Ctrl+2"),
                                        ("report", "Rapor", "📄", "Ctrl+3"),
                                        ("history", "Geçmiş", "🗂", "Ctrl+4"),
                                        ("codex", "AI Sohbet", "🧠", "F7")):
            self._nav_item(sidebar, key, label, icon, accel)

        tk.Frame(sidebar, bg=T.BORDER_SOFT, height=1).pack(fill=tk.X, padx=16, pady=14)

        # Hızlı işlemler
        tk.Label(sidebar, text="HIZLI İŞLEM", bg=T.BG_SIDEBAR, fg=T.TEXT_MUTED,
                 font=T.FONT_TINY).pack(anchor="w", padx=20, pady=(0, 6))
        quick = tk.Frame(sidebar, bg=T.BG_SIDEBAR)
        quick.pack(fill=tk.X, padx=18)
        T.RoundButton(quick, "Tarama Başlat", icon="▶", command=self.start_pipeline_thread,
                      width=200, height=36, radius=9, fill=T.ACCENT,
                      parent_bg=T.BG_SIDEBAR).pack(pady=(0, 8))
        T.RoundButton(quick, "Profili Aç", icon="↗", command=self.open_selected_profile,
                      width=200, height=36, radius=9, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_SIDEBAR).pack(pady=(0, 8))
        T.RoundButton(quick, "İlişki Ağı", icon="🌐", command=self.open_graph_view,
                      width=200, height=36, radius=9, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_SIDEBAR).pack(pady=(0, 8))
        T.RoundButton(quick, "AI ile Konuş", icon="🧠", command=self.show_interactive_report,
                      width=200, height=36, radius=9, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_SIDEBAR).pack()

        # Alt: AI model durumu (tek yerden yönetilir)
        footer = tk.Frame(sidebar, bg=T.BG_SIDEBAR)
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)
        self._sidebar_footer = footer
        self.lbl_side_model = tk.Label(footer, text="", bg=T.BG_SIDEBAR, fg=T.TEXT_MUTED,
                                       font=T.FONT_TINY, justify="left", anchor="w",
                                       wraplength=200)
        self.lbl_side_model.pack(fill=tk.X, pady=(0, 8))
        self.side_model_card = ModelStatusCard(footer)
        self.side_model_card.pack(fill=tk.X)
        T.Tooltip(self.side_model_card,
                  "AI motorunun canlı durumu: örnek sayısı, geri bildirim, karar\n"
                  "eşiği ve doğrulama F1 skoru. Geri bildirim verdikçe güçlenir.")
        self._sidebar = sidebar

    def _nav_item(self, parent, key, label, icon, accel=""):
        frame = tk.Frame(parent, bg=T.BG_SIDEBAR, cursor="hand2")
        frame.pack(fill=tk.X, padx=10, pady=2)
        bar = tk.Frame(frame, bg=T.BG_SIDEBAR, width=3)
        bar.pack(side=tk.LEFT, fill=tk.Y)

        inner = tk.Frame(frame, bg=T.BG_SIDEBAR, cursor="hand2")
        inner.pack(side=tk.LEFT, fill=tk.X, expand=True)

        icon_lbl = tk.Label(inner, text=icon + "   ", bg=T.BG_SIDEBAR,
                            fg=T.TEXT_SOFT, font=(T.FONT_FAMILY, 12), anchor="w")
        icon_lbl.pack(side=tk.LEFT, padx=(6, 0))
        lbl = tk.Label(inner, text=label, bg=T.BG_SIDEBAR,
                       fg=T.TEXT_SOFT, font=T.FONT_BODY, anchor="w")
        lbl.pack(side=tk.LEFT)
        accel_lbl = tk.Label(inner, text=accel, bg=T.BG_SIDEBAR, fg=T.TEXT_MUTED,
                             font=T.FONT_TINY, anchor="e")
        accel_lbl.pack(side=tk.RIGHT, padx=(0, 8))

        # Tıklama/hover TÜM alt bileşenlerde çalışmalı: eskiden yalnızca
        # dış çerçeveler bağlıydı, etiketlerin üzerine tıklamak işe yaramıyordu.
        widgets = (frame, inner, icon_lbl, lbl, accel_lbl, bar)

        def enter(_e):
            if self.active_page != key:
                for w in widgets:
                    w.configure(bg=T.BG_HOVER)
                bar.configure(bg=T.ACCENT)
                lbl.configure(fg=T.TEXT)
                icon_lbl.configure(fg=T.TEXT)
                accel_lbl.configure(bg=T.BG_HOVER)

        def leave(_e):
            active = self.active_page == key
            for w in widgets:
                w.configure(bg=T.BG_SIDEBAR)
            bar.configure(bg=T.ACCENT if active else T.BG_SIDEBAR)
            lbl.configure(fg=T.TEXT if active else T.TEXT_SOFT)
            icon_lbl.configure(fg=(T.ACCENT if active else T.TEXT_SOFT))
            accel_lbl.configure(bg=T.BG_SIDEBAR)

        for widget in widgets:
            widget.bind("<Button-1>", lambda _e, k=key: self.show_page(k))
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)
        self._nav[key] = (frame, lbl, bar, icon_lbl, accel_lbl)

    def show_page(self, key):
        if key not in self.pages:
            return
        self.active_page = key
        self.pages[key].tkraise()
        for k, (frame, lbl, bar, icon_lbl, accel_lbl) in self._nav.items():
            active = k == key
            lbl.configure(fg=T.TEXT if active else T.TEXT_SOFT, bg=T.BG_SIDEBAR)
            icon_lbl.configure(fg=(T.ACCENT if active else T.TEXT_SOFT),
                               bg=T.BG_SIDEBAR)
            accel_lbl.configure(bg=T.BG_SIDEBAR)
            bar.configure(bg=T.ACCENT if active else T.BG_SIDEBAR)

    # ==================================================================
    #  Sekme 1: Tarama
    # ==================================================================
    def _build_scan_page(self, sidebar):
        page = tk.Frame(self.content, bg=T.BG_ROOT, padx=20, pady=18)

        # Hedef giriş kartı
        target_card = T.Card(page, title="Hedef Varlık",
                             subtitle="Analiz etmek istediğiniz kişi adı veya kullanıcı adı.")
        target_card.pack(fill=tk.X)

        row = tk.Frame(target_card.body, bg=T.BG_CARD)
        row.pack(fill=tk.X)

        self.entry_target = T.PlaceholderEntry(
            row, placeholder="örn.  Ahmet Yılmaz   veya   ahmet_yilmaz", width=48)
        self.entry_target.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self.entry_target.bind("<Return>", lambda _e: self.start_pipeline_thread())

        btns = tk.Frame(target_card.body, bg=T.BG_CARD)
        btns.pack(fill=tk.X, pady=(12, 0))

        self.btn_run = T.RoundButton(btns, "Taramayı Başlat", icon="🚀",
                                     command=self.start_pipeline_thread,
                                     width=200, height=44, fill=T.ACCENT)
        self.btn_run.pack(side=tk.LEFT)
        T.RoundButton(btns, "İlişki Ağı", icon="🌐", command=self.open_graph_view,
                      width=150, height=44, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=10)
        T.RoundButton(btns, "HTML Rapor", icon="💾", command=self.save_html_report,
                      width=150, height=44, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT)
        T.RoundButton(btns, "Toplu Tarama", icon="🎯", command=self.show_batch_scan,
                      width=150, height=44, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=10)
        tk.Label(target_card.body, text="İpucu: bir kullanıcı adı yazarsanız (ör. hamigumi) "
                                       "o adla var olan profiller; tam ad yazarsanız "
                                       "isme bağlı hesaplar aranır.",
                 bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.FONT_TINY,
                 justify="left").pack(anchor="w", pady=(10, 0))

        # AI doğrulama sonuçları
        results_card = T.Card(page, title="AI Doğrulama Sonuçları",
                              subtitle="✔ doğrulanan hesaplar · ◌ aday (elle kontrol) · "
                                       "🔒 platform duvarı. Satıra çift tıklayın → profil açılır.")
        results_card.pack(fill=tk.X, pady=(16, 0))

        list_wrap = tk.Frame(results_card.body, bg=T.BORDER)
        list_wrap.pack(fill=tk.X)
        self.lb_candidates = tk.Listbox(
            list_wrap, height=6, bg=T.BG_CARD_ALT, fg=T.TEXT,
            selectbackground=T.ACCENT, selectforeground="#FFFFFF",
            font=T.FONT_BODY, relief=tk.FLAT, bd=0, highlightthickness=0,
            activestyle="none", selectborderwidth=0)
        self.lb_candidates.pack(fill=tk.X, ipady=4)
        self.lb_candidates.bind("<Double-Button-1>", lambda _e: self.open_selected_profile())
        # Sağ tık (Linux/Windows) ve Control+tık (macOS) bağlam menüsü.
        self.lb_candidates.bind("<Button-3>", self._candidate_menu)
        self.lb_candidates.bind("<Control-Button-1>", self._candidate_menu)

        fb = tk.Frame(results_card.body, bg=T.BG_CARD)
        fb.pack(fill=tk.X, pady=(10, 0))
        T.RoundButton(fb, "Profili Aç", icon="↗", command=self.open_selected_profile,
                      width=140, height=38, fill=T.ACCENT, parent_bg=T.BG_CARD
                      ).pack(side=tk.LEFT)
        T.RoundButton(fb, "Doğru", icon="✔", command=lambda: self.submit_feedback(1),
                      width=120, height=38, fill=T.SUCCESS, fg="#06251A",
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=(10, 0))
        T.RoundButton(fb, "Yanlış", icon="✘", command=lambda: self.submit_feedback(0),
                      width=120, height=38, fill=T.DANGER, fg="#3A0B0C",
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=10)
        self.lbl_feedback_hint = tk.Label(
            fb, text="Doğru/Yanlış ile modeli eğitin — her tıklama AI'yı geliştirir.",
            bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.FONT_TINY)
        self.lbl_feedback_hint.pack(side=tk.LEFT, padx=10)

        # AI karar analizi paneli
        self.ai_panel_frame = T.Card(page, title="AI Karar Analizi",
                                     subtitle="En güçlü sonuç için AI'nın hareket ettiği 10 sinyal.")
        self.ai_panel_frame.pack(fill=tk.X, pady=(6, 10))
        self.ai_panel = scrolledtext.ScrolledText(
            self.ai_panel_frame.body, height=5, bg=T.LOG_BG, fg=T.LOG_FG,
            font=T.FONT_MONO, relief=tk.FLAT, bd=0, highlightthickness=0, wrap="word")
        self.ai_panel.pack(fill=tk.X, expand=True)
        self.ai_panel.insert(
            tk.END,
            "Tarama başlattığınızda burada AI'nın karar dayanağı, özellik katkıları "
            "ve kalibrasyon bilgisi görünür.\n")

        # Konsol
        console_card = T.Card(page, title="Operasyon Konsolu")
        console_card.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.console = scrolledtext.ScrolledText(
            console_card.body, height=8, bg=T.LOG_BG, fg=T.LOG_FG,
            insertbackground=T.ACCENT, font=T.FONT_MONO, relief=tk.FLAT,
            bd=0, highlightthickness=0, wrap="word")
        self.console.pack(fill=tk.BOTH, expand=True)
        self.console.tag_configure("ok", foreground=T.SUCCESS)
        self.console.tag_configure("err", foreground=T.DANGER)
        self.console.tag_configure("info", foreground=T.INFO)
        self.console.tag_configure("step", foreground=T.ACCENT)
        self._log("[*] CHAZ OSINT hazır. Bir hedef girin ve taramayı başlatın.")

        return page

    # ==================================================================
    #  Sekme 2: Fotoğraf Analizi
    # ==================================================================
    def _build_photo_page(self):
        page = tk.Frame(self.content, bg=T.BG_ROOT, padx=20, pady=18)

        columns = tk.Frame(page, bg=T.BG_ROOT)
        columns.pack(fill=tk.BOTH, expand=True)
        columns.grid_columnconfigure(0, weight=0)
        columns.grid_columnconfigure(1, weight=1)
        columns.grid_rowconfigure(0, weight=1)

        # Sol: görsel önizleme
        left = T.Card(columns, title="Görsel",
                      subtitle="Analiz için bir fotoğraf seçin.")
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 16))

        self.preview = tk.Canvas(left.body, width=300, height=300, bg=T.BG_CARD_ALT,
                                 highlightthickness=1, highlightbackground=T.BORDER)
        self.preview.pack()
        self._draw_preview_placeholder()

        self.lbl_photo_info = tk.Label(left.body, text="Görsel seçilmedi",
                                       bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.FONT_SMALL,
                                       wraplength=300, justify="left")
        self.lbl_photo_info.pack(anchor="w", pady=(10, 0))

        pbtns = tk.Frame(left.body, bg=T.BG_CARD)
        pbtns.pack(fill=tk.X, pady=(12, 0))
        T.RoundButton(pbtns, "Görsel Seç", icon="📁", command=self.select_photo,
                      width=140, height=40, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT)
        T.RoundButton(pbtns, "Analiz Et", icon="🔎", command=self.run_photo_analysis,
                      width=140, height=40, fill=T.ACCENT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=10)

        # Sağ: adli tıp raporu
        right = T.Card(columns, title="Adli Tıp Analizi (AI)",
                       subtitle="Meta veri, manipülasyon riski ve algısal parmak izi.")
        right.grid(row=0, column=1, sticky="nsew")

        self.verdict_chip = tk.Label(right.body, text="Analiz bekleniyor", bg=T.BG_CARD_ALT,
                                     fg=T.TEXT_MUTED, font=T.FONT_BODY_B, padx=12, pady=8)
        self.verdict_chip.pack(fill=tk.X, pady=(0, 10))

        self.photo_output = scrolledtext.ScrolledText(
            right.body, bg=T.LOG_BG, fg=T.LOG_FG, font=T.FONT_MONO,
            relief=tk.FLAT, bd=0, highlightthickness=0, wrap="word")
        self.photo_output.pack(fill=tk.BOTH, expand=True)
        self.photo_output.insert(tk.END, "Görsel seçip 'Analiz Et' düğmesine basın.\n")

        self._build_identity_card(columns)
        return page

    def _build_identity_card(self, parent):
        card = T.Card(parent, title="Fotoğraftan Hesap Bulma",
                      subtitle="Görselin kimlik izlerini çıkarır ve OSINT motoruna hedef olarak önerir.")
        card.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 0))

        row = tk.Frame(card.body, bg=T.BG_CARD)
        row.pack(fill=tk.X)
        T.RoundButton(row, "Hesapları Bul", icon="🔎", command=self.run_photo_identity,
                      width=170, height=40, fill=T.ACCENT).pack(side=tk.LEFT)
        self.btn_scan_lead = T.RoundButton(
            row, "Önerilen Hedefle Tara", icon="🎯", command=self.scan_suggested_target,
            width=215, height=40, fill=T.BG_CARD_ALT, fg=T.TEXT, parent_bg=T.BG_CARD)
        self.btn_scan_lead.pack(side=tk.LEFT, padx=10)
        self.btn_scan_lead.set_enabled(False)

        rs = tk.Frame(card.body, bg=T.BG_CARD)
        rs.pack(fill=tk.X, pady=(12, 0))
        tk.Label(rs, text="Ters görsel arama:", bg=T.BG_CARD, fg=T.TEXT_MUTED,
                 font=T.FONT_SMALL).pack(side=tk.LEFT, padx=(0, 8))
        for label, url in REVERSE_SEARCH_PAGES:
            T.RoundButton(rs, label, command=lambda u=url: webbrowser.open(u),
                          width=112, height=32, radius=9, fill=T.BG_CARD_ALT,
                          fg=T.TEXT, font=T.FONT_SMALL, parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=4)

        self.lbl_leads = tk.Label(
            card.body, text="Görsel analizinden sonra bulunan kimlik izleri burada listelenir.",
            bg=T.BG_CARD, fg=T.TEXT_MUTED, font=T.FONT_SMALL, justify="left", wraplength=830)
        self.lbl_leads.pack(anchor="w", pady=(10, 0))
        return card

    def _draw_preview_placeholder(self):
        self.preview.delete("all")
        T.round_rect(self.preview, 8, 8, 292, 292, 12, outline=T.BORDER,
                     fill=T.BG_CARD_ALT, width=1, dash=(4, 4))
        self.preview.create_text(150, 140, text="🖼", fill=T.TEXT_MUTED,
                                 font=(T.FONT_FAMILY, 34))
        self.preview.create_text(150, 182, text="Görsel seçilmedi", fill=T.TEXT_MUTED,
                                 font=T.FONT_SMALL)

    # ==================================================================
    #  Sekme 3: Rapor
    # ==================================================================
    def _build_report_page(self):
        page = tk.Frame(self.content, bg=T.BG_ROOT, padx=20, pady=18)
        card = T.Card(page, title="İstihbarat Raporu",
                      subtitle="Son taramanın ayrıntılı çıktısı.")
        card.pack(fill=tk.BOTH, expand=True)

        bar = tk.Frame(card.body, bg=T.BG_CARD)
        bar.pack(fill=tk.X, pady=(0, 10))
        T.RoundButton(bar, "HTML Olarak Kaydet", icon="💾", command=self.save_html_report,
                      width=200, height=38, fill=T.ACCENT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT)

        self.report_view = scrolledtext.ScrolledText(
            card.body, bg=T.LOG_BG, fg=T.LOG_FG, font=T.FONT_MONO,
            relief=tk.FLAT, bd=0, highlightthickness=0, wrap="word")
        self.report_view.pack(fill=tk.BOTH, expand=True)
        self.report_view.insert(tk.END, "Henüz rapor üretilmedi. 'Tarama' sekmesinden başlatın.\n")
        return page

    # ==================================================================
    #  Sekme 4: Geçmiş
    # ==================================================================
    def _build_history_page(self):
        page = tk.Frame(self.content, bg=T.BG_ROOT, padx=20, pady=18)
        card = T.Card(page, title="Tarama Geçmişi",
                      subtitle="Yerel veritabanındaki geçmiş taramalar. Rapor için bir kayda çift tıklayın.")
        card.pack(fill=tk.BOTH, expand=True)

        bar = tk.Frame(card.body, bg=T.BG_CARD)
        bar.pack(fill=tk.X, pady=(0, 10))
        T.RoundButton(bar, "Yenile", icon="⟳", command=self._load_history,
                      width=120, height=36, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT)
        self.lbl_hist_stats = tk.Label(bar, text="", bg=T.BG_CARD, fg=T.TEXT_MUTED,
                                       font=T.FONT_SMALL)
        self.lbl_hist_stats.pack(side=tk.LEFT, padx=10)

        self.lb_history = tk.Listbox(
            card.body, bg=T.BG_CARD_ALT, fg=T.TEXT, font=T.FONT_BODY, relief=tk.FLAT,
            bd=0, highlightthickness=0, activestyle="none", height=8,
            selectbackground=T.ACCENT, selectforeground="#FFFFFF")
        self.lb_history.pack(fill=tk.X, ipady=4)
        self.lb_history.bind("<Double-Button-1>", lambda _e: self._open_history_item())

        self.history_view = scrolledtext.ScrolledText(
            card.body, bg=T.LOG_BG, fg=T.LOG_FG, font=T.FONT_MONO, relief=tk.FLAT,
            bd=0, highlightthickness=0, wrap="word")
        self.history_view.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.history_view.insert(tk.END, "Geçmiş kaydı seçin.\n")
        return page

    def _load_history(self):
        try:
            rows = self.db.recent_scans(limit=100)
        except Exception as e:
            self._log(f"[-] Geçmiş okunamadı: {e}")
            return

        self.history_rows = rows
        self.lb_history.delete(0, tk.END)
        for r in rows:
            self.lb_history.insert(
                tk.END, f"  #{r['id']}   {str(r['target'])[:26]:<26}  "
                        f"%{float(r['confidence'] or 0):>5.1f}   "
                        f"{int(r['verified_count'] or 0)} hesap   ·   {r['timestamp']}")
        st = self.db.stats()
        self.lbl_hist_stats.configure(
            text=f"{st['scans']} tarama  ·  {st['fingerprints']} görsel parmak izi")
        self._refresh_recent_scans_menu()

    def _open_history_item(self):
        sel = self.lb_history.curselection()
        if not sel or sel[0] >= len(self.history_rows):
            return
        record = self.db.get_scan(self.history_rows[sel[0]]["id"])
        if not record:
            return
        self.history_view.delete("1.0", tk.END)
        self.history_view.insert(tk.END, record.get("report") or "")

    # ==================================================================
    #  Yardımcılar
    # ==================================================================
    def _log(self, text: str):
        tag = ""
        if text.startswith("[+]"):
            tag = "ok"
        elif text.startswith("[-]"):
            tag = "err"
        elif text.startswith("[🧠]"):
            tag = "info"
        elif text.startswith("[*]"):
            tag = "step"
        self.console.insert(tk.END, text + "\n", tag)
        self.console.see(tk.END)

    def _refresh_model_status(self):
        text = f"{self.ai.trained_samples} örnek · {self.ai.feedback_count} geri bildirim"
        self.lbl_model.configure(text=f"AI Core aktif  ·  {text}")
        self.lbl_side_model.configure(
            text=f"🧠  AI Core\n{self.ai.describe()}")
        self.lbl_status_right.configure(
            text=f"eşik {self.ai.threshold:.2f} · {self.ai.real_feedback_samples} gerçek geri bildirim")

        # AI motorundaki anahtar isimleri farklı olabilir -> güvenli okuma:
        weights = getattr(self.ai, "weights", {}) or {}
        w_hs = (weights.get("handle_similarity")
                or weights.get("handle_sim")
                or weights.get("weights_hs") or 0)
        w_dns = (weights.get("display_name_similarity")
                 or weights.get("dns_similarity")
                 or weights.get("weights_dns") or 0)
        w_shape = (weights.get("handle_shape")
                   or weights.get("shape")
                   or weights.get("weights_shape") or 0)

        self.side_model_card.update_stats(
            bias=getattr(self.ai, "bias", 0.0),
            weights_hs=w_hs,
            weights_dns=w_dns,
            weights_shape=w_shape,
            threshold=getattr(self.ai, "threshold", 0.55),
            trained=getattr(self.ai, "trained_samples", 0),
            feedback=getattr(self.ai, "feedback_count", 0),
            real_feedback=getattr(self.ai, "real_feedback_samples", 0),
            metrics=getattr(self.ai, "metrics", {}) or {},
        )

    # ==================================================================
    #  Fotoğraf işlemleri
    # ==================================================================
    def select_photo(self):
        path = filedialog.askopenfilename(
            title="Analiz edilecek görseli seçin",
            filetypes=[("Resim Dosyaları", "*.jpg *.jpeg *.png *.webp *.tiff *.bmp")])
        if not path:
            return
        self.photo_path = path
        self.photo_analysis = None
        self.identity = None
        self.btn_scan_lead.set_enabled(False)
        self.lbl_leads.configure(
            text="Görsel analizinden sonra bulunan kimlik izleri burada listelenir.",
            fg=T.TEXT_MUTED)
        self.lbl_photo_info.configure(
            text=f"{os.path.basename(path)}\n{self._human_size(path)}")
        self._render_preview(path)
        self.verdict_chip.configure(text="Analiz bekleniyor", bg=T.BG_CARD_ALT, fg=T.TEXT_MUTED)
        self.photo_output.delete("1.0", tk.END)
        self.photo_output.insert(tk.END, f"Görsel yüklendi:\n{path}\n\n'Analiz Et' düğmesine basın.\n")

    def _render_preview(self, path):
        try:
            from PIL import Image, ImageTk
            resample = getattr(Image, "Resampling", Image).LANCZOS
            img = Image.open(path)
            img.thumbnail((280, 280), resample)
            self.photo_thumb = ImageTk.PhotoImage(img)
            self.preview.delete("all")
            self.preview.create_image(150, 150, image=self.photo_thumb)
        except Exception as e:
            self._draw_preview_placeholder()
            self.preview.create_text(150, 220, text=f"Önizleme yok: {e}",
                                     fill=T.TEXT_MUTED, font=T.FONT_TINY, width=280)

    def run_photo_analysis(self):
        if not self.photo_path:
            messagebox.showwarning("Uyarı", "Lütfen önce bir görsel seçin!")
            return
        self.verdict_chip.configure(text="Analiz ediliyor...", bg=T.BG_CARD_ALT, fg=T.TEXT_SOFT)
        self.root.update_idletasks()
        try:
            self.photo_analysis = PhotoForensicsAI(self.photo_path).analyze()
        except Exception as e:
            messagebox.showerror("Hata", f"Analiz başarısız: {e}")
            return
        self._show_photo_analysis(self.photo_analysis)

    def _show_photo_analysis(self, analysis):
        risk = analysis["risk_score"]
        colors = {"success": T.SUCCESS, "warning": T.WARNING,
                  "danger": T.DANGER, "muted": T.TEXT_MUTED}
        fg = colors.get(analysis["verdict_color"], T.TEXT_MUTED)
        self.verdict_chip.configure(
            text=f"{analysis['verdict']}   ·   Risk: %{risk*100:.0f}",
            bg=T.blend(fg, T.BG_ROOT, 0.84), fg=fg)

        self.photo_output.delete("1.0", tk.END)
        self.photo_output.insert(tk.END, self._photo_report_text(analysis))

    def _photo_report_text(self, analysis) -> str:
        meta = analysis["meta"]
        lines = []
        lines.append("═" * 52)
        lines.append(" FOTOĞRAF ADLİ TIP ANALİZİ (AI)")
        lines.append("═" * 52)
        lines.append(f" Dosya        : {os.path.basename(meta.get('path') or '-')}")
        lines.append(f" Boyut        : {meta.get('width')} x {meta.get('height')}  ({meta.get('format')})")
        lines.append(f" Kamera       : {meta.get('camera_make') or '-'} {meta.get('camera_model') or ''}".rstrip())
        lines.append(f" Lens         : {meta.get('lens') or '-'}")
        lines.append(f" Tarih        : {meta.get('datetime') or '-'}")
        lines.append(f" Yazılım      : {meta.get('software') or '-'}")
        if meta.get("gps"):
            lines.append(f" GPS          : {meta['gps']['lat']}, {meta['gps']['lon']}")
            lines.append(f" Harita       : {meta['gps_maps_url']}")
        else:
            lines.append(" GPS          : yok")
        lines.append(f" EXIF         : {'var' if meta.get('has_exif') else 'yok'}")
        lines.append(f" Algısal Hash : {analysis.get('phash') or '-'}")
        lines.append("")
        lines.append(f" ► Manipülasyon Riski : %{analysis['risk_score']*100:.0f}")
        lines.append(f" ► Değerlendirme      : {analysis['verdict']}")
        lines.append("")
        lines.append(" Sinyaller:")
        for name, detail in analysis["signals"]:
            lines.append(f"   • {detail}")
        if not analysis["signals"]:
            lines.append("   • Kayda değer sinyal yok.")
        lines.append("═" * 52)
        return "\n".join(lines)

    def run_photo_identity(self):
        """Görselden kimlik izlerini çıkarır ve aranabilir hedefler önerir."""
        if not self.photo_path:
            messagebox.showwarning("Uyarı", "Lütfen önce bir görsel seçin!")
            return
        try:
            self.identity = PhotoIdentityAI(self.photo_path).analyze()
        except Exception as e:
            messagebox.showerror("Hata", f"Kimlik analizi başarısız: {e}")
            return

        leads = self.identity["leads"]
        targets = self.identity["suggested_targets"]

        lines = []
        if leads:
            lines.append("Bulunan kimlik izleri:")
            for lead in leads[:8]:
                lines.append(f"   • {lead['kind']}: {lead['value']}"
                             f"   ({lead['source']}, güven %{lead['confidence']*100:.0f})")
        else:
            lines.append("Doğrudan kimlik izi bulunamadı.")
        for note in self.identity["notes"]:
            lines.append(f"   – {note}")
        if self.identity.get("gps_maps_url"):
            lines.append(f"   – GPS konumu: {self.identity['gps_maps_url']}")
        if targets:
            lines.append("")
            lines.append("Önerilen arama hedefi:  " + ",  ".join(targets))

        self.lbl_leads.configure(text="\n".join(lines), fg=T.TEXT_SOFT)
        self.btn_scan_lead.set_enabled(bool(targets))
        self._log(f"[+] Fotoğraf kimlik analizi: {len(leads)} iz, {len(targets)} hedef önerisi.")
        if self.identity.get("gps_maps_url"):
            self._log(f"[+] Görselde GPS konumu bulundu: {self.identity['gps_maps_url']}")

    def scan_suggested_target(self):
        """Fotoğraftan çıkarılan en güçlü hedefle mevcut tarama boru hattını başlatır."""
        targets = (self.identity or {}).get("suggested_targets") if self.identity else None
        if not targets:
            messagebox.showwarning("Uyarı", "Önce 'Hesapları Bul' ile görselden iz çıkarın!")
            return
        self.entry_target.set_value(targets[0])
        self._log(f"[*] Fotoğraftan çıkarılan hedefle tarama: '{targets[0]}'")
        self.start_pipeline_thread()

    @staticmethod
    def _human_size(path) -> str:
        try:
            size = os.path.getsize(path)
        except OSError:
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    # ==================================================================
    #  Tarama boru hattı
    # ==================================================================
    def start_pipeline_thread(self):
        target = self.entry_target.get_value()
        if not target:
            messagebox.showerror("Hata", "Lütfen geçerli bir hedef isim girin!")
            return

        if self._scanning:
            messagebox.showinfo("Tarama sürüyor", "Önce mevcut taramanın bitmesini bekleyin.")
            return
        self._scanning = True
        self.btn_run.set_enabled(False)
        self.console.delete("1.0", tk.END)
        self.lb_candidates.delete(0, tk.END)
        self.last_candidates = []
        self._verified_view = []
        self._lead_view = []
        self.show_page("scan")
        self._set_status(f"'{target}' taranıyor…", T.ACCENT)
        threading.Thread(target=self.run_pipeline, args=(target,), daemon=True).start()

    def run_pipeline(self, target: str):
        """UI'dan bağımsız pipeline çekirdeğini çalıştırır ve sonucu gösterir."""
        def on_log(message):
            self.root.after(0, self._log, message)

        try:
            session = self.pipeline.run(target, photo_path=self.photo_path, on_log=on_log)
            self.last_report = session.report
            self.last_network_data = session.network
            self.last_session = session
            self.root.after(0, self._log, session.report)
            self.root.after(0, self._update_report_view, session.report)
            self.root.after(0, self._populate_candidates,
                            session.verified, session.tentative)
            self.root.after(0, self._refresh_ai_panel, session.verified,
                            session.tentative)
            self.root.after(0, self._update_codex_panel, session)
            self.root.after(0, self._refresh_model_status)
            self.root.after(0, self._load_history)
            self.root.after(0, self._set_status,
                            f"Tarama tamamlandı: {len(session.verified)} doğrulanan, "
                            f"{len(session.tentative)} aday · %{session.confidence:.1f} güven")
        except Exception as e:
            self.root.after(0, self._log, f"[-] Kritik Hata: {e}")
            self.root.after(0, self._set_status, f"Tarama hatası: {e}", T.DANGER)
        finally:
            self.root.after(0, self._finish_scan)

    def _finish_scan(self):
        self._scanning = False
        self.btn_run.set_enabled(True)
        try:
            self.root.configure(cursor="")
        except tk.TclError:
            pass

    def _update_report_view(self, report: str):
        self.report_view.delete("1.0", tk.END)
        self.report_view.insert(tk.END, report)

    # ==================================================================
    #  AI karar analizi paneli
    # ==================================================================
    def _refresh_ai_panel(self, verified: list, tentative: list = None):
        """Tarama sonucu ulaşınca AI karar analizi kartını doldurur."""
        self.ai_panel.delete("1.0", tk.END)
        walls = [c for c in (tentative or []) if c.get("wall")]

        if not verified:
            self.ai_panel.insert(tk.END, "✔ Doğrulanmış sonuç yok.\n")
            self.ai_panel.insert(
                tk.END,
                "   Kanıtsız / tesadüfi eşleşmeler AI tarafından elendi.\n")
            if walls:
                self.ai_panel.insert(
                    tk.END,
                    f"   🔒 {len(walls)} adres platform duvarı nedeniyle anonim olarak "
                    "doğrulanamadı (aday listesinde gösterilir).\n")
            self._append_calibration()
            return

        top = verified[0]
        feats = top.get("ai_features") or {}
        rows = self.ai.explain_rows(feats) if self.ai else []

        self.ai_panel.insert(tk.END, "=== AI Karar Analizi (en güçlü sonuç) ===\n")
        self.ai_panel.insert(tk.END, f"Platform      : {top.get('platform', '?')}\n")
        self.ai_panel.insert(tk.END,
                             f"Kullanıcı adı : {top.get('handle', '-')}  ({top.get('url', '-')})\n")
        self.ai_panel.insert(tk.END,
                             f"AI olasılık   : %{round(top.get('ai_prob', 0)*100, 1):.1f}\n")
        self.ai_panel.insert(tk.END,
                             f"Karar dayanağı: {feats.get('decision_basis', 'model olasılığı')}\n")
        self.ai_panel.insert(tk.END, "\nÖZELLİKLER (katkı = ağırlık × değer):\n")
        for row in rows[:10]:
            sign = "+" if row["contribution"] > 0 else ("-" if row["contribution"] < 0 else "~")
            self.ai_panel.insert(
                tk.END,
                f"  {row['label']:34} = {row['value']:.2f}  (katkı {sign}{abs(round(row['contribution'], 2))})\n")
        self.ai_panel.insert(tk.END, "\n=== Karar Şeridi ===\n")
        disp = feats.get("display_name_similarity", 0)
        self.ai_panel.insert(tk.END, f"Görünen ad eşleşmesi  : %{disp*100:.0f}\n")
        self.ai_panel.insert(tk.END,
                             f"Sayfada görünen ad    : "
                             f"{'okunamadı' if feats.get('display_name_present', 0) < 0.5 else 'okundu'}\n")
        self.ai_panel.insert(tk.END,
                             f"İsim parçası kapsama  : {feats.get('token_coverage', 0)}\n")
        self.ai_panel.insert(tk.END,
                             f"Handle şekli          : "
                             f"{'kişi adı gibi' if feats.get('handle_shape', 1) > 0.6 else 'rastgele/jenerik'}\n")
        self.ai_panel.insert(tk.END,
                             f"Platform doğrulaması  : %{feats.get('platform_confirm', 0)*100:.0f}\n")
        self.ai_panel.insert(tk.END,
                             f"Sayfada handle kanıtı : {feats.get('page_has_handle', 0)}\n")
        self.ai_panel.insert(tk.END,
                             f"Bağımsız kaynak      : {feats.get('source_count', 0)}\n")
        self.ai_panel.insert(tk.END,
                             f"Güvenilir alan adı    : "
                             f"{'evet' if feats.get('domain_trust', 0) > 0.5 else 'hayır'}\n")
        self.ai_panel.insert(tk.END,
                             f"Profil URL (gönderi değil) : "
                             f"{'evet' if feats.get('is_profile_url', 0) > 0.5 else 'hayır'}\n")
        self.ai_panel.insert(tk.END,
                             f"Biyografi (bio) ismi  : "
                             f"{'içeriyor' if feats.get('bio_similarity', 0) >= 0.99 else 'yok'}\n")
        self.ai_panel.insert(tk.END,
                             f"\nKısa gerekçe: {top.get('ai_reason', '-')}\n")
        if walls:
            self.ai_panel.insert(
                tk.END,
                f"\n🔒 {len(walls)} adres platform duvarı (Instagram/TikTok/LinkedIn) "
                "nedeniyle anonim olarak doğrulanamadı.\n")
            self.ai_panel.insert(
                tk.END, "   Bu adresler 'aday' sayılır, doğrulanmış SAYILMAZ; "
                        "bağlantıdan elle kontrol edin.\n")
        self._append_calibration()

    def _append_calibration(self):
        """Modelin kalibrasyon bilgisini panelin sonuna ekler."""
        self.ai_panel.insert(tk.END, "\n=== Karar Eşiği (kalibrasyon) ===\n")
        self.ai_panel.insert(tk.END, f"Eşik değeri  : {self.ai.threshold:.2f}\n")
        self.ai_panel.insert(
            tk.END, "Sentetik doğrulama kümesindeki model başarısı:\n")
        for k in ("accuracy", "f1", "precision", "recall"):
            v = (self.ai.metrics or {}).get(k, 0)
            if v:
                self.ai_panel.insert(tk.END, f"  {k.title():<10}: %{v*100:.0f}\n")
        self.ai_panel.insert(
            tk.END,
            "\nNot: Kurallı kanıtlar (görünen ad, sayfada handle, platform\n"
            "doğrulaması) modelden bağımsız çalışır; model belirsiz durumlarda\n"
            "karar verir. 'Doğru/Yanlış' geri bildirimi modeli günceller.\n")

    def _populate_candidates(self, verified: list, tentative: list = None):
        """Doğrulanan sonuçları ve gevşek varyasyon adaylarını listeler.

        İkisi aynı listede gösterilir ama ayırt edilir: gevşek biçimler
        ("abugat" icin "bugat", "baugat"...) asla doğrulanmış sayılmaz.
        """
        self._verified_view = list(verified)
        self._lead_view = list(tentative or [])
        self.last_candidates = self._verified_view + self._lead_view
        self.lb_candidates.delete(0, tk.END)
        if not self.last_candidates:
            self.lb_candidates.insert(tk.END, "  Doğrulanmış sonuç yok — yanlış eşleşmeler elendi.")
            return
        for c in self._verified_view:
            self.lb_candidates.insert(
                tk.END, f"  ✔ {c['platform']}  ·  {c.get('handle', '-')}  ·  "
                        f"AI %{c['ai_prob']*100:.0f}")
        for c in self._lead_view:
            reason = c.get("lead_reason", "doğrulanamadı")
            if c.get("wall"):
                # Duvarlı adreslerde AI olasılığı ölçülemez (sayfa içeriği
                # hiç gelmiyor); yanıltmamak için "—" gösterilir.
                self.lb_candidates.insert(
                    tk.END, f"  🔒 {c['platform']}  ·  {c.get('handle', '-')}  ·  "
                            f"anonim doğrulanamadı (giriş/anti-bot duvarı)")
            else:
                self.lb_candidates.insert(
                    tk.END, f"  ◌ {c['platform']}  ·  {c.get('handle', '-')}  ·  "
                            f"AI %{c['ai_prob']*100:.0f}  ·  aday ({reason[:34]})")
        wall_count = sum(1 for c in self._lead_view if c.get("wall"))
        self._set_status(
            f"{len(self._verified_view)} doğrulanan · {len(self._lead_view)} aday "
            f"({wall_count} platform duvarı)")

    # ==================================================================
    #  Geri bildirim (self-learning)
    # ==================================================================
    def submit_feedback(self, label: int):
        sel = self.lb_candidates.curselection()
        if not sel or not self.last_candidates:
            messagebox.showwarning("Uyarı", "Önce bir tarama yapın ve bir sonuç seçin!")
            return
        idx = sel[0]
        if idx >= len(self.last_candidates):
            return
        cand = self.last_candidates[idx]

        self.ai.online_update(cand.get("ai_features", {}), label)
        self.ai.save()
        self._save_feedback(cand, label)

        yeni = self.ai.predict_proba(cand.get("ai_features", {}))
        self._log(f"[🧠] Geri bildirim ({'DOĞRU' if label else 'YANLIŞ'}): "
                  f"{cand.get('platform')} / {cand.get('handle')} -> yeni AI olasılık %{yeni*100:.0f}")
        cand["ai_prob"] = round(yeni, 4)
        cand["ai_label"] = 1 if yeni >= self.ai.threshold else 0
        self._populate_candidates(self._verified_view, self._lead_view)
        self._refresh_model_status()

    def _save_feedback(self, cand: dict, label: int):
        try:
            os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
            with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "url": cand.get("url"), "platform": cand.get("platform"),
                    "handle": cand.get("handle"), "label": label,
                    "features": cand.get("ai_features", {}),
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            self._log(f"[-] Geri bildirim kaydedilemedi: {e}")

    # ==================================================================
    #  Diğer
    # ==================================================================
    def open_graph_view(self):
        if not self.last_network_data:
            messagebox.showwarning("Uyarı", "Ağ grafiği için önce bir tarama gerçekleştirin!")
            return
        NetworkGraphView(self.root, self.last_network_data)

    def _selected_candidate(self):
        sel = self.lb_candidates.curselection()
        if not sel or sel[0] >= len(self.last_candidates):
            return None
        return self.last_candidates[sel[0]]

    def open_selected_profile(self):
        """Seçili sonucun profilini varsayılan tarayıcıda açar."""
        cand = self._selected_candidate()
        if not cand:
            messagebox.showwarning("Uyarı",
                                   "Önce listeden bir sonuç seçin (çift tıklama da açabilir).")
            return
        url = cand.get("url")
        if not url:
            return
        webbrowser.open(url)
        self._log(f"[+] Profil tarayıcıda açıldı: {url}")

    def retrain_model(self):
        """Birikmiş gerçek geri bildirimle modeli yeniden eğitir."""
        try:
            used = self.ai.retrain_from_feedback()
        except Exception as e:
            messagebox.showerror("Hata", f"Yeniden eğitim başarısız: {e}")
            return
        if used:
            self._log(f"[🧠] AI modeli {used} gerçek geri bildirimle yeniden eğitildi.")
            self._refresh_model_status()
            messagebox.showinfo("AI Core", f"Model {used} gerçek geri bildirimle "
                                          f"yeniden eğitildi.")
        else:
            messagebox.showinfo("AI Core", "Yeniden eğitim için yeterli gerçek geri "
                                           "bildirim yok (en az 5 kayıt gerekir).")

    def save_json_report(self):
        """Son taramayı JSON olarak kaydeder."""
        if not self._require_report():
            return
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON Dosyası", "*.json")])
        if not path:
            return
        try:
            JSONExporter(self._last_session_data()).export(path)
        except Exception as e:
            messagebox.showerror("Hata", f"JSON kaydedilemedi: {e}")
            return
        self._log(f"[+] JSON rapor yazıldı: {path}")

    def save_markdown_report(self):
        """Son taramayı Markdown olarak kaydeder."""
        if not self._require_report():
            return
        path = filedialog.asksaveasfilename(defaultextension=".md",
                                            filetypes=[("Markdown Dosyası", "*.md")])
        if not path:
            return
        try:
            MarkdownExporter(self._last_session_data()).export(path)
        except Exception as e:
            messagebox.showerror("Hata", f"Markdown kaydedilemedi: {e}")
            return
        self._log(f"[+] Markdown rapor yazıldı: {path}")

    def _require_report(self) -> bool:
        if not self.last_report:
            messagebox.showwarning("Uyarı", "Dışa aktarılacak rapor bulunamadı!")
            return False
        return True

    def _last_session_data(self) -> dict:
        """Dışa aktarıcılar için son taramanın verisini hazırlar.

        Oturum nesnesi duruyorsa onun kanonik `to_dict()` çıktısı kullanılır;
        yoksa eldeki listelerden eşdeğerini kurar.
        """
        if self.last_session is not None:
            data = self.last_session.to_dict()
            data["report"] = self.last_report
            return data
        return {
            "target": self.entry_target.get_value(),
            "report": self.last_report,
            "summary": {},
            "verified": [],
            "tentative": [],
            "snippets": [],
        }

    def save_html_report(self):
        if not self._require_report():
            return
        path = filedialog.asksaveasfilename(defaultextension=".html",
                                            filetypes=[("HTML Dosyası", "*.html")])
        if not path:
            return
        try:
            HTMLDashboardExporter(self.entry_target.get_value(),
                                  self.last_report).generate_html(path)
        except Exception as e:
            messagebox.showerror("Hata", f"HTML kaydedilemedi: {e}")
            return
        self._log(f"[+] HTML rapor yazıldı: {path}")
        messagebox.showinfo("Başarılı", f"Rapor kaydedildi:\n{path}")