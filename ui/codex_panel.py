"""CodexPanel - Codex tarzı sohbet ışığında AI raporunu sunan koyu tema paneli.

Tasarım:
  • Koyu arka plan + monospace (Consolas) sütunlar
  • Yapay zekanın "cevabı" gibi rapor satırları
  • Kopyalanabilir / dosyaya aktarılabilir
  • Alt kısımda basit bir soru kutusu: rapor hakkında hızlı sorular
    (yerel çalışır — harici API çağrılmaz).

Bu sınıf `tk.Frame`'dir; böylece ana pencerede bir sayfa (page) olarak
`grid()`/`tkraise()` ile kullanılabilir.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from ui import theme as T


class CodexPanel(tk.Frame):
    """Yapay zeka sohbet şeridi için tasarlanmış, koyu temalı rapor paneli."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=T.BG_ROOT, **kw)
        self.parent = parent
        # Soru-cevap için tarama bağlamı (dashboard doldurur).
        self.context = {}
        self._quick_answers = []
        self._build()

    # ------------------------------------------------------------------
    #  Kurulum
    # ------------------------------------------------------------------
    def _build(self):
        # Üst başlık şeridi
        head = tk.Frame(self, bg=T.BG_ROOT)
        head.pack(fill=tk.X, padx=20, pady=(18, 0))
        tk.Label(head, text="Yapay Zeka ile Konuş", bg=T.BG_ROOT, fg=T.TEXT,
                 font=T.FONT_DISPLAY).pack(anchor="w")
        tk.Label(head, text="Son taramanın özetini sorun: \"özet\", \"kanıtlar\", "
                            "\"adaylar\", \"duvar\", \"güven\"…",
                 bg=T.BG_ROOT, fg=T.TEXT_MUTED, font=T.FONT_SMALL,
                 justify="left").pack(anchor="w", pady=(2, 0))

        card = T.Card(self, title="AI Rapor Sohbeti",
                      subtitle="Yerel çalışır; sonuçlar burada yapay zeka "
                               "tarafından konuşulur.")
        card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(12, 16))

        # Araç çubuğu
        bar = tk.Frame(card.body, bg=T.BG_CARD)
        bar.pack(fill=tk.X, pady=(0, 10))
        T.RoundButton(bar, "Kopyala", icon="⧉", command=self.copy_content,
                      width=120, height=34, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT)
        T.RoundButton(bar, "Dosyaya Aktar", icon="💾", command=self.export_to_file,
                      width=160, height=34, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=8)
        T.RoundButton(bar, "Paylaşım İpuçları", icon="❔", command=self.open_chat_help,
                      width=180, height=34, fill=T.BG_CARD_ALT, fg=T.TEXT,
                      parent_bg=T.BG_CARD).pack(side=tk.LEFT)

        self.view = scrolledtext.ScrolledText(
            card.body, bg=T.LOG_BG, fg=T.LOG_FG, font=T.FONT_MONO_SM,
            relief=tk.FLAT, bd=0, highlightthickness=0, wrap="word")
        self.view.pack(fill=tk.BOTH, expand=True)
        self.view.tag_configure("user", foreground=T.INFO)
        self.view.tag_configure("ai", foreground=T.TEXT)
        self.view.tag_configure("muted", foreground=T.TEXT_MUTED)
        self.view.configure(state=tk.DISABLED)
        self.set_report("")

        # Soru kutusu
        ask = tk.Frame(card.body, bg=T.BG_CARD)
        ask.pack(fill=tk.X, pady=(10, 0))
        self.entry = T.PlaceholderEntry(
            ask, placeholder="ör.  özet   |   kanıtlar   |   adaylar   |   duvar", width=48)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.entry.bind("<Return>", lambda _e: self.ask())
        T.RoundButton(ask, "Sor", icon="➤", command=self.ask, width=110, height=34,
                      fill=T.ACCENT, parent_bg=T.BG_CARD).pack(side=tk.LEFT, padx=(8, 0))

    # ------------------------------------------------------------------
    #  İçerik
    # ------------------------------------------------------------------
    def _append(self, text: str, tag: str = "ai"):
        self.view.configure(state=tk.NORMAL)
        self.view.insert(tk.END, text, tag)
        self.view.see(tk.END)
        self.view.configure(state=tk.DISABLED)

    def _replace(self, text: str, tag: str = "ai"):
        self.view.configure(state=tk.NORMAL)
        self.view.delete("1.0", tk.END)
        self.view.insert(tk.END, text, tag)
        self.view.see(tk.END)
        self.view.configure(state=tk.DISABLED)

    def set_report(self, body: str = ""):
        """Raporu panelde yükler; boşsa yer tutucu gösterir."""
        if not body:
            self._replace(
                "Hazır değil — önce bir tarama başlatın.\n\n"
                "Tarama tamamlanınca burada sonuçların yapay zeka özeti görünür;"
                " alttaki kutuya \"özet\" yazarak da sorabilirsiniz.\n", "muted")
            return
        self._replace(body)
        self._append(
            "\n\n— Bu özet yerel AI motoru tarafından üretildi. Ayrıntı için "
            "sonuç listesinde sağ tıklayıp 'AI Karar Açıklaması'nı kullanın.\n",
            "muted")

    def append_report(self, body: str = ""):
        """Mevcut akışın altına yeni bir AI rapor bloğu ekler (sohbet hissi)."""
        if not body:
            return
        self._append("\n" + body + "\n")

    # ------------------------------------------------------------------
    #  Soru-cevap (yerel, kural tabanlı)
    # ------------------------------------------------------------------
    def set_context(self, context: dict):
        """Soruları yanıtlamak için tarama bağlamını kaydeder."""
        self.context = context or {}

    def ask(self):
        question = self.entry.get_value().strip()
        if not question:
            return
        self.entry.set_value("")
        self._append(f"\n> {question}\n", "user")
        self._append(self._answer(question) + "\n", "ai")

    def _answer(self, question: str) -> str:
        """Bağlama göre basit, dürüst ve yerel bir yanıt üretir."""
        q = question.lower()
        c = self.context or {}
        if not c:
            return ("Henüz bir tarama yapılmadı. Bir hedef girip 'Taramayı Başlat' "
                    "dedikten sonra sonuçları buradan sorabilirsiniz.")

        target = c.get("target", "-")
        verified = c.get("verified", []) or []
        tentative = c.get("tentative", []) or []
        confidence = c.get("confidence", 0.0)
        walls = [x for x in tentative if x.get("wall")]

        if any(k in q for k in ("özet", "ozet", "summary", "durum")):
            return (f"Hedef: {target}\n"
                    f"• Doğrulanan hesap: {len(verified)}\n"
                    f"• Aday (elle kontrol): {len(tentative)} "
                    f"(bunlardan {len(walls)} tanesi platform duvarı)\n"
                    f"• Genel güven skoru: %{float(confidence):.1f}")

        if any(k in q for k in ("kanıt", "kanit", "evidence", "neden", "niçin", "nicin")):
            if not verified:
                return "Doğrulanmış hesap yok — kanıt bulunamadı."
            lines = ["Doğrulanan hesapların kanıtları:"]
            for v in verified[:5]:
                lines.append(f"• {v.get('platform')} / {v.get('handle')}: "
                             f"{v.get('ai_reason', '-')} "
                             f"(AI %{float(v.get('ai_prob', 0))*100:.0f})")
            return "\n".join(lines)

        if any(k in q for k in ("aday", "lead", "elle", "kontrol")):
            if not tentative:
                return "Doğrulanamayan aday yok."
            lines = ["Elle kontrol edilmesi gereken adaylar:"]
            for t in tentative[:8]:
                if t.get("wall"):
                    lines.append(f"🔒 {t.get('platform')}: {t.get('url')} "
                                 "(platform duvarı — anonim doğrulanamadı)")
                else:
                    lines.append(f"◌ {t.get('platform')}: {t.get('url')} "
                                 f"({t.get('lead_reason', 'doğrulanamadı')})")
            return "\n".join(lines)

        if any(k in q for k in ("duvar", "wall", "instagram", "tiktok", "linkedin")):
            if not walls:
                return "Platform duvarına takılan adres yok."
            lines = ["Platform içeriği anonim istemciye kapalı olan adresler "
                     "(hesap YOK demek değildir):"]
            for w in walls[:8]:
                lines.append(f"🔒 {w.get('platform')}: {w.get('url')}")
            return "\n".join(lines)

        if any(k in q for k in ("güven", "guven", "skor", "confidence", "%")):
            return (f"Genel güven skoru: %{float(confidence):.1f}. "
                    "Bu skor doğrulanan hesapların AI olasılıklarından, "
                    "platform kapsamından ve kaynak bolluğundan hesaplanır.")

        if any(k in q for k in ("link", "url", "adres", "aç", "ac")):
            if not verified:
                return "Doğrulanmış hesap adresi yok."
            return "\n".join(f"• {v.get('platform')}: {v.get('url')}"
                             for v in verified[:10])

        return ("Bunu anlayamadım. Şunları deneyebilirsiniz: "
                "\"özet\", \"kanıtlar\", \"adaylar\", \"duvar\", \"güven\", \"linkler\".")

    # ------------------------------------------------------------------
    #  Dışa aktarım
    # ------------------------------------------------------------------
    def copy_content(self):
        """Şu an görüntülenen içeriği panoya kopyalar."""
        text = self.view.get("1.0", tk.END)
        if not text.strip() or text.strip().startswith("Hazır değil"):
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text.rstrip())
            self.update_idletasks()
        except Exception as e:
            messagebox.showerror("Hata", f"Panoya kopyalanamadı: {e}")

    def export_to_file(self):
        """İçeriği bir metin dosyasına yazar."""
        text = self.view.get("1.0", tk.END).rstrip()
        if not text or text.startswith("Hazır değil"):
            messagebox.showinfo("İptal", "Dışa aktarılacak bir rapor yok.")
            return
        path = filedialog.asksaveasfilename(
            title="AI raporunu kaydet", defaultextension=".txt",
            filetypes=[("Metin belgeleri", "*.txt"), ("Tüm dosyalar", "*.*")],
            parent=self.winfo_toplevel())
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as e:
            messagebox.showerror("Hata", f"Dosya yazılamadı: {e}")
            return
        messagebox.showinfo("Kaydedildi", f"Rapor kaydedildi:\n{path}")

    def open_chat_help(self):
        """Panelin nasıl kullanılacağını özetler."""
        messagebox.showinfo(
            "AI Rapor Sohbeti",
            "CHAZ OSINT — Yapay Zeka Rapor Sohbeti\n"
            "======================================\n"
            "Tarama tamamlandığında sonuç özeti bu sayfaya yazılır.\n\n"
            "Alt kısımdaki kutuya şunları yazabilirsiniz:\n"
            "  • özet        → doğrulanan/aday sayıları ve güven\n"
            "  • kanıtlar    → her doğrulanan hesabın kanıtı\n"
            "  • adaylar     → elle kontrol edilecek adresler\n"
            "  • duvar       → anonim doğrulanamayan adresler\n"
            "  • linkler     → doğrulanan profillerin bağlantıları\n\n"
            "Tüm yanıtlar yereldir; hiçbir veri sunucuya gönderilmez.",
            parent=self.winfo_toplevel())
