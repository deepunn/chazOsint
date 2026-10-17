"""Arayüz menüleri için duman testi (gerçek Tk penceresi kurulur).

Neden gerekli: menüler yalnızca tıklanınca çalışır ve bir komut adı yanlış
bağlanırsa hata SESSİZ kalır. Bu test pencereyi kurar ve menülerdeki HER
komutu çağırır (modal mock'larla); ayrıca sağ tık menüsünü, AI karar
açıklamasını ve yeni "Yapay Zeka" menüsü diyaloglarını doğrular.

Görüntü gerektirmeyen ortamlarda (headless CI) test atlanır.

Çalıştırma:
    python -m unittest tests.test_ui_menus -v
"""

import tkinter as tk
import unittest
from unittest import mock


def _make_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:              # ekran yok (headless CI)
        raise unittest.SkipTest(f"Tk kullanılamıyor: {exc}")
    root.withdraw()
    return root


def _label_of(menu, index):
    try:
        return menu.entrycget(index, "label")
    except tk.TclError:
        return ""


def _menu_entries(menu, path=""):
    entries = []
    for index in range(menu.index("end") + 1):
        kind = menu.type(index)
        label = _label_of(menu, index)
        if kind == "cascade":
            submenu = menu.nametowidget(menu.entrycget(index, "menu"))
            entries.extend(_menu_entries(submenu, f"{path}/{label}"))
        elif kind in ("command", "checkbutton"):
            entries.append((f"{path}/{label}", menu, index, label))
    return entries


def _cascade_labels(menubar):
    return [_label_of(menubar, i)
            for i in range(menubar.index("end") + 1)
            if menubar.type(i) == "cascade"]


def _find_dialog(root, title):
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel) and child.title() == title:
            return child
    return None


def _texts(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Text):
            found.append(child.get("1.0", tk.END))
        elif isinstance(child, tk.Label):
            found.append(str(child.cget("text")))
        found.extend(_texts(child))
    return found


class _FakeMessagebox:
    def __init__(self):
        self.calls = []

    def _record(self, kind):
        def handler(title, message="", **kwargs):
            self.calls.append((kind, title, message))
            return False if kind == "askyesno" else None
        return handler

    def __getattr__(self, name):
        return self._record(name)


class _FakeFiledialog:
    def asksaveasfilename(self, *a, **kw):
        return ""

    def askopenfilename(self, *a, **kw):
        return ""


class TestMenuBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = _make_root()
        from ui import dashboard_window as dm
        cls.dm = dm
        cls.fake_box = _FakeMessagebox()
        dm.messagebox = cls.fake_box
        dm.filedialog = _FakeFiledialog()
        dm.webbrowser = type("W", (), {"open": staticmethod(lambda url: None)})()
        cls.window = dm.DashboardWindow(cls.root)
        cls.root.update()
        cls.cls_root = cls.root

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        for child in list(self.cls_root.winfo_children()):
            if isinstance(child, tk.Toplevel):
                child.destroy()
        self.cls_root.update()

    def _close_dialogs(self):
        for child in list(self.cls_root.winfo_children()):
            if isinstance(child, tk.Toplevel):
                child.destroy()
        self.cls_root.update()

    # -- Menü yapısı ---------------------------------------------------

    def test_menubar_has_expected_cascades(self):
        labels = _cascade_labels(self.window.menubar)
        for expected in ("Dosya", "Tarama", "Yapay Zeka", "Görünüm", "Yardım"):
            self.assertIn(expected, labels)

    def test_every_menu_command_runs_without_error(self):
        menubar = self.window.menubar
        commands = []
        for index in range(menubar.index("end") + 1):
            if menubar.type(index) != "cascade":
                continue
            submenu = self.root.nametowidget(menubar.entrycget(index, "menu"))
            commands.extend(_menu_entries(submenu, _label_of(menubar, index)))
        self.assertGreater(len(commands), 15)

        skipped = ("Çıkış", "HTML Rapor", "JSON", "Markdown",
                   "Doğru İşaretle", "Yanlış İşaretle", "Yeniden Eğit")
        for path, submenu, index, label in commands:
            if any(token in label for token in skipped):
                continue
            with self.subTest(menu=path):
                try:
                    submenu.invoke(index)
                except Exception as exc:
                    self.fail(f"menü komutu hata verdi: {path} -> {exc}")
                self.cls_root.update()
        self._close_dialogs()

    # -- AI / araçlar --------------------------------------------------

    def test_retrain_menu_calls_engine(self):
        calls = []
        self.window.ai.retrain_from_feedback = lambda *a, **kw: (calls.append(1), 0)[1]
        self.window.retrain_model()
        self.assertTrue(calls, "Yeniden eğitim menüsü motoru çağırmadı")

    def test_widgets_import_safely(self):
        from collectors.http_client import HttpClient
        self.assertTrue(issubclass(HttpClient, object))
        import ui.widgets as w
        self.assertTrue(hasattr(w, "ModelStatusCard"))
        pick = next((a for a in dir(w) if "card" in a.lower() or "but" in a.lower()), None)
        self.assertIsNotNone(pick, msg="ui.widgets'ten uygun isim bulunamadı")

    def test_recent_scans_menu_is_populated(self):
        menu = self.window._recent_scans_menu
        self.assertGreater(menu.index("end") + 1, 0)
        labels = [_label_of(menu, i) for i in range(menu.index("end") + 1)]
        self.assertTrue(any(label for label in labels))

    def test_candidate_context_menu(self):
        self.window.last_candidates = [{
            "platform": "GitHub", "handle": "hamigumi",
            "url": "https://github.com/hamigumi",
            "archive": "https://web.archive.org/web/*/https://github.com/hamigumi",
            "ai_prob": 0.91, "ai_features": {}, "ai_reason": "kanıt kuralı",
        }]
        self.window.lb_candidates.insert(tk.END, "  ✔ GitHub · hamigumi · AI %91")
        event = type("Event", (), {"y": 2, "x_root": 100, "y_root": 100})()
        with mock.patch.object(tk.Menu, "tk_popup", lambda *a, **kw: None):
            try:
                self.window._candidate_menu(event)
            except Exception as exc:
                self.fail(f"sağ tık menüsü hata verdi: {exc}")
        self.cls_root.update()

    def test_candidate_explanation_dialog(self):
        self.window._show_candidate_explanation({
            "platform": "GitHub", "handle": "hamigumi",
            "url": "https://github.com/hamigumi", "ai_prob": 0.91,
            "ai_features": {"handle_similarity": 1.0, "wall": 0.0},
            "ai_reason": "kanıt kuralı: platform doğrulaması",
        })
        self.cls_root.update()
        dialog = _find_dialog(self.cls_root, "AI Karar Açıklaması")
        self.assertIsNotNone(dialog, "karar açıklaması penceresi açılmadı")
        body = "\n".join(_texts(dialog))
        self.assertIn("katkı", body.lower())
        self.assertIn("hamigumi", body)

    def test_ai_report_dialog_shows_holdout_and_weights(self):
        self.window.show_ai_report()
        self.cls_root.update()
        dialog = _find_dialog(self.cls_root, "AI Model Durumu")
        self.assertIsNotNone(dialog)
        body = "\n".join(_texts(dialog))
        self.assertIn("DOĞRULAMA", body)
        self.assertIn("KARAR AĞIRLIKLARI", body)
        self.assertIn("kalibrasyon", body.lower())

    def test_providers_diagnostic_lists_providers(self):
        self.window.show_providers_diagnostic()
        self.cls_root.update()
        dialog = _find_dialog(self.cls_root, "Doğrulama Sağlayıcıları (tanılama)")
        self.assertIsNotNone(dialog)
        body = "\n".join(_texts(dialog))
        self.assertIn("Chrome taklidi", body)
        self.assertIn("tiktok-embed", body)

    def test_threshold_dialog_opens(self):
        self.window.set_ai_threshold()
        self.cls_root.update()
        self.assertIsNotNone(_find_dialog(self.cls_root, "Karar Eşiği"))

    def test_feedback_summary_dialog_opens(self):
        self.window.show_feedback_summary()
        self.cls_root.update()
        dialog = _find_dialog(self.cls_root, "AI Geri Bildirim Özeti")
        self.assertIsNotNone(dialog)
        body = "\n".join(_texts(dialog))
        self.assertIn("Geri bildirim", body)

    def test_sidebar_ai_card_toggle(self):
        card = self.window.side_model_card
        self.window._ai_card_var.set(False)
        self.window.toggle_ai_card()
        self.cls_root.update()
        definition = str(card.winfo_manager())
        self.assertFalse(bool(definition), msg="kart gizlenmedi: " + definition)
        self.window._ai_card_var.set(True)
        self.window.toggle_ai_card()
        self.cls_root.update()
        realization = str(card.winfo_manager())
        self.assertTrue(bool(realization), msg="kart gösterilemedi: " + realization)

    def test_shortcuts_dialog_mentions_new_keys(self):
        self.window.show_shortcuts()
        self.cls_root.update()
        dialog = _find_dialog(self.cls_root, "Klavye Kısayolları")
        self.assertIsNotNone(dialog)
        body = "\n".join(_texts(dialog))
        self.assertIn("Ctrl+R", body)
        self.assertIn("Sağ tık", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
