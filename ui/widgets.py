"""
CHAZ OSINT - Arayüz widget'ları.

`ModelStatusCard`: kenar çubuğunun altında AI motorunun canlı durumunu
gösteren küçük kart. Eskiden bir "yer tutucu" (stub) idi ve hiçbir şey
göstermiyordu; artık örnek sayısı, geri bildirim, karar eşiği ve doğrulama
F1 skorunu tek bakışta gösterir.
"""

import tkinter as tk

from ui import theme as T


def strength_color(contribution: float) -> str:
    """Katkı işaretine göre renk döndürür (pozitif/negatif/nötr)."""
    if contribution > 0:
        return "#3DD68C"
    if contribution < 0:
        return "#F2696B"
    return "#5A6478"


def _fmt(value, digits=2):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


class ModelStatusCard(tk.Frame):
    """Yapay zeka motorunun durumu kartı (sidebar footer).

    `update_stats(**kw)` ile canlı güncellenir. Arayüz yalnızca bu metodu
    çağırır; kart kendi iç düzenini yönetir. Eksik/bozuk veri gelirse çökmez.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=T.BG_CARD, highlightbackground=T.BORDER,
                         highlightthickness=1, bd=0)
        self._stats = {}
        self._build()

    # ------------------------------------------------------------------
    #  Kurulum
    # ------------------------------------------------------------------
    def _build(self):
        head = tk.Frame(self, bg=T.BG_CARD)
        head.pack(fill=tk.X, padx=10, pady=(9, 8))
        self._dot = tk.Label(head, text="●", bg=T.BG_CARD, fg=T.SUCCESS,
                             font=(T.FONT_FAMILY, 9))
        self._dot.pack(side=tk.LEFT)
        tk.Label(head, text="AI CORE", bg=T.BG_CARD, fg=T.TEXT,
                 font=T.FONT_TINY).pack(side=tk.LEFT, padx=(5, 0))
        self._state = tk.Label(head, text="aktif", bg=T.BG_CARD, fg=T.SUCCESS,
                               font=T.FONT_TINY)
        self._state.pack(side=tk.RIGHT)

        grid = tk.Frame(self, bg=T.BG_CARD)
        grid.pack(fill=tk.X, padx=10)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        self._v_samples = self._mini(grid, 0, 0, "Örnek")
        self._v_feedback = self._mini(grid, 0, 1, "Geri bildirim")
        self._v_threshold = self._mini(grid, 1, 0, "Eşik")
        self._v_f1 = self._mini(grid, 1, 1, "Doğrulama F1")

        # İnce güven çubuğu (F1 / doğruluk).
        self._bar = tk.Canvas(self, height=6, bg=T.BG_CARD_ALT,
                              highlightthickness=0, bd=0)
        self._bar.pack(fill=tk.X, padx=10, pady=(8, 4))
        self._bar_fill = self._bar.create_rectangle(0, 0, 0, 6, width=0,
                                                    fill=T.ACCENT)

        self._signal = tk.Label(self, text="", bg=T.BG_CARD, fg=T.TEXT_MUTED,
                                font=T.FONT_TINY, anchor="w", justify="left",
                                wraplength=190)
        self._signal.pack(fill=tk.X, padx=10, pady=(0, 9))

        self._bar.bind("<Configure>", lambda _e: self._draw_bar())

    def _mini(self, parent, row, col, caption):
        cell = tk.Frame(parent, bg=T.BG_CARD, padx=6, pady=3)
        cell.grid(row=row, column=col, sticky="ew")
        value = tk.Label(cell, text="-", bg=T.BG_CARD, fg=T.TEXT,
                         font=(T.FONT_FAMILY, 12, "bold"), anchor="w")
        value.pack(anchor="w")
        tk.Label(cell, text=caption, bg=T.BG_CARD, fg=T.TEXT_MUTED,
                 font=T.FONT_TINY, anchor="w").pack(anchor="w")
        return value

    # ------------------------------------------------------------------
    #  Güncelleme
    # ------------------------------------------------------------------
    def update_stats(self, **kw):
        """Motor durumunu karta yazar. Eksik alanlar yok sayılır."""
        self._stats.update(kw)
        stats = self._stats

        trained = stats.get("trained", 0)
        feedback = stats.get("feedback", 0)
        real_feedback = stats.get("real_feedback", 0)
        threshold = stats.get("threshold", 0.55)
        metrics = stats.get("metrics", {}) or {}

        self._v_samples.configure(text=str(int(trained or 0)))
        fb_text = str(int(feedback or 0))
        if real_feedback:
            fb_text += f" ({int(real_feedback)} gerçek)"
        self._v_feedback.configure(text=fb_text)
        self._v_threshold.configure(text=_fmt(threshold))

        f1 = metrics.get("f1")
        if f1 is None:
            f1 = metrics.get("accuracy")
        self._v_f1.configure(text=("%" + str(round(float(f1) * 100))) if f1 is not None else "-")

        # En güçlü model sinyalini kısa bir satırda özetle.
        weights = {
            "Kullanıcı adı benzerliği": stats.get("weights_hs", 0),
            "Görünen ad eşleşmesi": stats.get("weights_dns", 0),
            "Kullanıcı adı şekli": stats.get("weights_shape", 0),
        }
        top = max(weights.items(), key=lambda kv: abs(float(kv[1] or 0)), default=None)
        real = int(real_feedback or 0)
        if real >= 5:
            self._state.configure(text="öğreniyor", fg=T.INFO)
        else:
            self._state.configure(text="hazır", fg=T.SUCCESS)
        if top and abs(float(top[1] or 0)) > 0.01:
            self._signal.configure(text=f"En güçlü sinyal: {top[0]} ({float(top[1]):+.2f})")
        else:
            self._signal.configure(text="Geri bildirim verdikçe güçlenir.")

        self._draw_bar()

    def _draw_bar(self):
        try:
            width = max(self._bar.winfo_width(), 10)
        except tk.TclError:
            return
        metrics = self._stats.get("metrics", {}) or {}
        value = metrics.get("f1")
        if value is None:
            value = metrics.get("accuracy", 0.0)
        try:
            value = min(max(float(value or 0.0), 0.0), 1.0)
        except (TypeError, ValueError):
            value = 0.0
        color = T.SUCCESS if value >= 0.75 else (T.WARNING if value >= 0.55 else T.DANGER)
        self._bar.coords(self._bar_fill, 0, 0, int(width * value), 6)
        self._bar.itemconfig(self._bar_fill, fill=color)
