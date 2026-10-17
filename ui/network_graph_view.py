"""Dijital ilişki ağı görünümü - hedef varlık merkezli, temaya uygun graf."""

import math
import tkinter as tk

from ui import theme as T


class NetworkGraphView(tk.Toplevel):
    """Hedef varlık ile doğrulanmış hesaplar arasındaki ilişki ağını çizer."""

    RADIUS = 150

    def __init__(self, master, network_data: dict):
        super().__init__(master)
        self.title("İlişki Ağı · CHAZ Hesap Bulma Aracı")
        self.geometry("760x600")
        self.minsize(620, 480)
        self.configure(bg=T.BG_ROOT)
        self.data = network_data or {}

        self._build()

    def _build(self):
        header = tk.Frame(self, bg=T.BG_HEADER)
        header.pack(fill=tk.X)
        tk.Frame(self, bg=T.BORDER, height=1).pack(fill=tk.X)

        tk.Label(header, text="🌐  Hedef Varlık İlişki Haritası", bg=T.BG_HEADER,
                 fg=T.TEXT, font=T.FONT_TITLE).pack(side=tk.LEFT, padx=20, pady=14)
        count = len([n for n in self.data.get("nodes", []) if n.get("group") == "social_account"])
        tk.Label(header, text=f"{count} doğrulanmış hesap", bg=T.BG_HEADER,
                 fg=T.TEXT_MUTED, font=T.FONT_SMALL).pack(side=tk.RIGHT, padx=20)

        self.canvas = tk.Canvas(self, bg=T.BG_CARD, highlightthickness=1,
                                highlightbackground=T.BORDER)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)
        self.canvas.bind("<Configure>", lambda _e: self._draw())

    def _draw(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 720
        h = self.canvas.winfo_height() or 500
        cx, cy = w / 2, h / 2

        nodes = self.data.get("nodes", [])
        target = next((n for n in nodes if n.get("group") == "target"), None)
        accounts = [n for n in nodes if n.get("group") == "social_account"]

        target_label = (target or {}).get("label", "HEDEF")

        # Kenar çizgileri (güç -> renk ve kalınlık)
        positions = self._layout(accounts, cx, cy)
        for node, (x, y) in positions:
            prob = float(node.get("probability", 0.5))
            color = T.SUCCESS if prob >= 0.8 else T.ACCENT if prob >= 0.6 else T.WARNING
            self.canvas.create_line(cx, cy, x, y, fill=T.blend(color, T.BG_CARD, 0.45),
                                    width=1 + prob * 3)

        # Hesap düğümleri
        for node, (x, y) in positions:
            prob = float(node.get("probability", 0.5))
            color = T.SUCCESS if prob >= 0.8 else T.ACCENT if prob >= 0.6 else T.WARNING
            r = 30
            self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=T.BG_CARD_ALT,
                                    outline=color, width=2)
            self.canvas.create_text(x, y - 4, text=str(node.get("label", "?"))[:12],
                                    fill=T.TEXT, font=T.FONT_TINY)
            self.canvas.create_text(x, y + 11, text=f"%{prob*100:.0f}",
                                    fill=color, font=(T.FONT_FAMILY, 7, "bold"))

        # Hedef düğümü (merkez)
        r = 46
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=T.ACCENT,
                                outline=T.ACCENT_HOVER, width=2)
        self.canvas.create_text(cx, cy, text=str(target_label)[:14], fill="#FFFFFF",
                                font=T.FONT_BODY_B, width=90, justify="center")

        if not accounts:
            self.canvas.create_text(cx, cy + 90, text="Doğrulanmış hesap bulunamadı.",
                                    fill=T.TEXT_MUTED, font=T.FONT_SMALL)

    def _layout(self, accounts, cx, cy):
        positions = []
        n = len(accounts)
        if n == 1:
            return [(accounts[0], (cx + self.RADIUS, cy))]
        for i, node in enumerate(accounts):
            angle = -math.pi / 2 + (2 * math.pi * i / n)
            x = cx + self.RADIUS * math.cos(angle)
            y = cy + self.RADIUS * math.sin(angle)
            positions.append((node, (x, y)))
        return positions
