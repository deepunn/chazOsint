"""
CHAZ OSINT - Ortak arayüz teması.

Rafine, koyu bir "slate + indigo" paleti; neon 'hacker' görünümü yerine
profesyonel ve dengeli bir tasarım. Tüm arayüz bileşenleri bu modülden
renk/font alır, böylece görünüm tutarlı kalır.
"""

import tkinter as tk

# ---------------------------------------------------------------------------
# Palet
# ---------------------------------------------------------------------------
BG_ROOT      = "#0B0E13"   # ana arka plan
BG_SIDEBAR   = "#0E1218"   # kenar çubuğu
BG_HEADER    = "#0E1218"
BG_CARD      = "#141922"   # kart yüzeyi
BG_CARD_ALT  = "#1A212C"   # ikincil yüzey / input
BG_HOVER     = "#1F2836"

BORDER       = "#232C39"
BORDER_SOFT  = "#1B222C"

TEXT         = "#E9EAF0"   # birincil metin
TEXT_SOFT    = "#C2C9D3"   # ikincil metin
TEXT_MUTED   = "#7B8493"   # soluk metin

ACCENT       = "#6EE7B7"   # emerald-AI vurgu
ACCENT_HOVER = "#A7F3D0"
ACCENT_SOFT  = "#0E2A21"

SUCCESS      = "#22C55E"
WARNING      = "#FBBF24"
DANGER       = "#EF4444"
INFO         = "#60A5FA"

LOG_BG       = "#0C1016"
LOG_FG       = "#C2C9D3"

CHAT_BUBBLE  = "#1B2430"
CHAT_QUOTE   = "#121822"


# ---------------------------------------------------------------------------
# Fontlar
# ---------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"

FONT_DISPLAY = (FONT_FAMILY, 19, "bold")
FONT_TITLE   = (FONT_FAMILY, 13, "bold")
FONT_SUB     = (FONT_FAMILY, 10)
FONT_BODY    = (FONT_FAMILY, 10)
FONT_BODY_B  = (FONT_FAMILY, 10, "bold")
FONT_SMALL   = (FONT_FAMILY, 9)
FONT_TINY    = (FONT_FAMILY, 8)
FONT_MONO    = ("Consolas", 9)
FONT_MONO_SM = ("Consolas", 8)


# ---------------------------------------------------------------------------
# Renk yardımcıları
# ---------------------------------------------------------------------------
def _to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def blend(color_a: str, color_b: str, t: float) -> str:
    """İki rengi t (0..1) oranında karıştırır."""
    ra, ga, ba = _to_rgb(color_a)
    rb, gb, bb = _to_rgb(color_b)
    r = round(ra + (rb - ra) * t)
    g = round(ga + (gb - ga) * t)
    b = round(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# Yuvarlak köşeli dikdörtgen
# ---------------------------------------------------------------------------
def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    """Canvas üzerine yuvarlak köşeli dikdörtgen çizer."""
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ---------------------------------------------------------------------------
# Bileşenler
# ---------------------------------------------------------------------------
class RoundButton(tk.Canvas):
    """Yuvarlak köşeli, hover animasyonlu buton (tk.Button'dan daha şık)."""

    def __init__(self, parent, text, command=None, width=150, height=40, radius=11,
                 fill=ACCENT, fg="#FFFFFF", font=None, parent_bg=None, icon="",
                 outline=False):
        pbg = parent_bg or parent.cget("bg")
        super().__init__(parent, width=width, height=height, bg=pbg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._fill = fill
        self._fg = fg
        self._radius = radius
        self._command = command
        self._enabled = True
        self._outline = outline
        # NOT: self._w tkinter'in iç widget yoludur; bu adı ASLA kullanmayın.
        self._btn_width, self._btn_height = width, height

        if outline:
            # Çerçeveli (ikincil) buton: iç yüzey arka planla aynı, kenar vurgulu.
            self._shape = round_rect(self, 1, 1, width - 1, height - 1, radius,
                                     fill=pbg, outline=fill, width=1)
        else:
            self._shape = round_rect(self, 1, 1, width - 1, height - 1, radius,
                                     fill=fill, outline=fill)
        label = f"{icon}  {text}".strip() if icon else text
        text_fg = fill if outline else fg
        self._text = self.create_text(width / 2, height / 2, text=label,
                                      fill=text_fg, font=font or FONT_BODY_B)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_color(self, color):
        if self._outline:
            self.itemconfig(self._shape, fill=self.cget("bg"), outline=color)
        else:
            self.itemconfig(self._shape, fill=color, outline=color)

    def _on_enter(self, _):
        if self._enabled:
            self._set_color(blend(self._fill, "#FFFFFF", 0.16))

    def _on_leave(self, _):
        self._set_color(self._fill if self._enabled else blend(self._fill, BG_CARD, 0.6))

    def _on_press(self, _):
        if self._enabled:
            self._set_color(blend(self._fill, "#000000", 0.18))

    def _on_release(self, _):
        if not self._enabled:
            return
        self._set_color(blend(self._fill, "#FFFFFF", 0.16))
        if self._command:
            self._command()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        if enabled:
            self._set_color(self._fill)
            self.itemconfig(self._text, fill=self._fg)
        else:
            self._set_color(blend(self._fill, BG_CARD, 0.62))
            self.itemconfig(self._text, fill=TEXT_MUTED)


class Card(tk.Frame):
    """İnce çerçeveli kart yüzeyi."""

    def __init__(self, parent, title=None, subtitle=None, pad=16, bg=BG_CARD, **kw):
        super().__init__(parent, bg=bg, highlightbackground=BORDER,
                         highlightthickness=1, bd=0, **kw)
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill=tk.BOTH, expand=True, padx=pad, pady=pad)

        if title:
            head = tk.Frame(self.body, bg=bg)
            head.pack(fill=tk.X, pady=(0, 10))
            tk.Label(head, text=title, bg=bg, fg=TEXT, font=FONT_TITLE).pack(anchor="w")
            if subtitle:
                tk.Label(head, text=subtitle, bg=bg, fg=TEXT_MUTED,
                         font=FONT_SMALL, justify="left").pack(anchor="w", pady=(2, 0))
            tk.Frame(self.body, bg=BORDER_SOFT, height=1).pack(fill=tk.X, pady=(0, 12))


def chip(parent, text, fg=INFO, bg=None, font=None):
    """Küçük etiket/rozet."""
    bg = bg or blend(fg, BG_ROOT, 0.82)
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font or FONT_TINY,
                    padx=8, pady=3)


class PlaceholderEntry(tk.Entry):
    """Placeholder (ipucu) metni destekleyen giriş alanı."""

    def __init__(self, parent, placeholder="", **kw):
        kw.setdefault("bg", BG_CARD_ALT)
        kw.setdefault("fg", TEXT)
        kw.setdefault("insertbackground", ACCENT)
        kw.setdefault("relief", tk.FLAT)
        kw.setdefault("font", FONT_BODY)
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", BORDER)
        kw.setdefault("highlightcolor", ACCENT)
        super().__init__(parent, **kw)
        self._placeholder = placeholder
        self._showing = False
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self._show_placeholder()

    def _show_placeholder(self):
        if not super().get():
            self._showing = True
            self.configure(fg=TEXT_MUTED)
            self.insert(0, self._placeholder)

    def _focus_in(self, _):
        if self._showing:
            self.delete(0, tk.END)
            self.configure(fg=TEXT)
            self._showing = False

    def _focus_out(self, _):
        if not super().get():
            self._show_placeholder()

    def get_value(self) -> str:
        return "" if self._showing else super().get().strip()

    def set_value(self, value: str):
        self.delete(0, tk.END)
        self._showing = False
        self.configure(fg=TEXT)
        if value:
            self.insert(0, value)
        else:
            self._show_placeholder()


class Tooltip:
    """Bir widget'ın üzerine gelince kısa açıklama gösteren hafif ipucu.

    Kullanım:
        Tooltip(buton, "Taramayı başlatır (F5)")
    """

    def __init__(self, widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except tk.TclError:
            self._after_id = None

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except tk.TclError:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self.text, bg=BG_CARD_ALT, fg=TEXT_SOFT,
                 font=FONT_TINY, justify="left", padx=8, pady=4,
                 highlightbackground=BORDER, highlightthickness=1).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
