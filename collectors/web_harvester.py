"""
WebHarvester - Açık kaynak veri toplama ve GERÇEK profil doğrulama modülü.

Altyapı notları:
  * Tüm HTTP istekleri dayanıklı `HttpClient` (retry/backoff/önbellek/UA
    rotasyonu) üzerinden yapılır.
  * Platform doğrulamaları ve arama motorları `OSINTThreadPool` ile PARALEL
    çalışır; böylece tarama süresi belirgin biçimde kısalır.
  * Kullanıcı aracısı listesi, zaman aşımları ve regex imzaları `config/`
    altındaki dosyalardan (settings.py üzerinden) okunur.

Doğrulama felsefesi: `HTTP 200 == hesap var` YANLIŞTIR. Instagram/Spotify
var olmayan profil için de 200 döndürür. Bu yüzden sayfa İÇERİĞİ doğrulanır ve
gerçek 404 dönen platformlarda durum koduna güvenilir.
"""

import base64
import re
import urllib.parse

from bs4 import BeautifulSoup

from config.settings import get_settings
from collectors.http_client import HttpClient
from collectors.platform_verifiers import VerifierRegistry
from core.thread_pool import OSINTThreadPool
from intelligence.ai_engine import slugify, name_variants, _handle_from_url
from intelligence.handle_generator import (TIER_INITIAL, generate_handles_with_tiers,
                                           order_for_probing, tokenize)
from utils.cache import DiskCache
from utils.logger import get_logger

log = get_logger("harvester")

# Var olmayan sayfaya ait GERÇEK metin işaretleri.
#
# DİKKAT: Buraya platformun JS paketinde geçen iç tanımlayıcılar (ör.
# Instagram'ın `ox_redesigned_404_page` / `HttpErrorPage` bileşen adları)
# EKLENMEMELİDİR. Bunlar Instagram'ın kabuğunda HER kullanıcı için geçtiği
# için (ör. var olan "natgeo" hesabında bile) gerçek profilleri "yok"
# sayıp eliyordu.
NEGATIVE_MARKERS = [
    "sorry, this page isn't available",
    "the link you followed may be broken",
    "page not found",
    "this page could not be found",
    "couldn't find this account",
    "this account doesn't exist",
    "account doesn't exist",
    "no user found",
    "user not found",
    "doesn't exist",
    "kullanıcı bulunamadı",
    "hesap bulunamadı",
    "sayfa bulunamadı",
    "404 not found",
]

# 200 dışında gerçek 404/410 döndüren, bu yüzden durum koduna GÜVENİLEN platformlar.
RELIABLE_STATUS_PLATFORMS = {"GitHub", "YouTube", "SoundCloud", "Twitter / X"}

# DUYARLI (opaque) PLATFORMLAR: anonim istemciye profili GÖSTERMEZ.
#   * Instagram  -> giriş duvarı + JS kabuğu döner (var olan/olmayan aynı sayfa)
#   * TikTok     -> WAF/anti-bot meydan okuma sayfası döner ("Please wait...")
#   * Facebook   -> giriş ekranına yönlendirir
# Bu platformlarda "içerik doğrulaması" YAPILAMAZ. Bu yüzden içerik kanıtı
# bulunamaması "hesap yok" demek DEĞİLDİR; aday, elle doğrulanacak olarak
# işaretlenir (eskiden yanlışlıkla siliniyordu).
OPAQUE_PLATFORMS = {"Instagram", "TikTok", "Facebook", "Threads"}

# Bu platformlarda içeriğin gizlendiğini gösteren kabuk/meydan okuma imzaları.
WALL_MARKERS = (
    "--fds-",                 # Instagram web CSS değişken öneki (JS kabuğu)
    "polarisprofilepage",     # Instagram React profili (yalnızca JS ile dolar)
    "require_login",          # Instagram API yanıtı
    "slardarwaf",             # TikTok WAF istemcisi
    "wafchallengeid",         # TikTok WAF meydan okuması
    "cf-challenge",           # Cloudflare challenge
    "captcha-delivery",       # DataDome captcha
    "checking your browser",
)

# Kabuk/duvar sayfalarında sık görülen giriş çağrıları (yalnızca duyarlı
# platformlarda, og:title gibi bir içerik kanıtı YOKKEN duvar sayılır).
WALL_TEXT_HINTS = (
    "log in", "log into", "sign up", "create an account", "please wait",
    "giriş yap", "kaydol", "hesap oluştur",
)

# Profil sayfası DEĞİL, dolayısıyla elenecek URL parçaları.
NON_PROFILE_MARKERS = [
    "/p/", "/reel", "/reels", "/tv/", "/status/", "/explore", "/hashtag",
    "/about", "/privacy", "/terms", "/help", "/login", "/signup", "/settings",
    "/search", "/share", "/intent/", "/watch", "/playlist", "/results",
    "/directory", "/policies", "/legal", "/developers", "/features",
    "/video/", "/photo/", "/discover", "/music/", "/feed",
]

PLATFORM_TARGETS = [
    ("Instagram", "https://www.instagram.com/{h}/"),
    ("TikTok", "https://www.tiktok.com/@{h}"),
    ("Twitter / X", "https://x.com/{h}"),
    ("Spotify", "https://open.spotify.com/user/{h}"),
    ("YouTube", "https://www.youtube.com/@{h}"),
    ("SoundCloud", "https://soundcloud.com/{h}"),
    ("GitHub", "https://github.com/{h}"),
    ("LinkedIn", "https://www.linkedin.com/in/{h}"),
    ("Twitch", "https://www.twitch.tv/{h}"),
    ("Reddit", "https://www.reddit.com/user/{h}"),
    ("Pinterest", "https://www.pinterest.com/{h}/"),
    ("Medium", "https://medium.com/@{h}"),
    ("Telegram", "https://t.me/{h}"),
    ("Threads", "https://www.threads.net/@{h}"),
    ("GitLab", "https://gitlab.com/{h}"),
    ("Behance", "https://www.behance.net/{h}"),
]

SEARCH_DOMAIN_MAP = [
    ("Instagram", "instagram.com/"),
    ("Twitter / X", "twitter.com/"),
    ("Twitter / X", "x.com/"),
    ("Spotify", "spotify.com/"),
    ("YouTube", "youtube.com/"),
    ("SoundCloud", "soundcloud.com/"),
    ("GitHub", "github.com/"),
    ("TikTok", "tiktok.com/"),
    ("LinkedIn", "linkedin.com/in/"),
    ("Twitch", "twitch.tv/"),
    ("Reddit", "reddit.com/user/"),
    ("Pinterest", "pinterest.com/"),
    ("Medium", "medium.com/@"),
    ("Telegram", "t.me/"),
    ("Threads", "threads.net/"),
    ("GitLab", "gitlab.com/"),
    ("Behance", "behance.net/"),
    ("Dribbble", "dribbble.com/"),
    ("Tumblr", "tumblr.com/"),
    ("VK", "vk.com/"),
]

# Ek arama motorları: sonuç HTML'indeki tüm bağlantılar taranır ve
# SEARCH_DOMAIN_MAP ile eşleştirilir. Yeni bir motor eklemek tek satırdır.
GENERIC_SEARCH_ENGINES = [
    ("yandex", "https://yandex.com/search/?text={q}"),
    ("mojeek", "https://www.mojeek.com/search?q={q}"),
    ("brave", "https://search.brave.com/search?q={q}"),
    ("startpage", "https://www.startpage.com/sp/search?query={q}"),
]

# regex_signatures.json'daki imzalar için kanonik profil URL'leri.
SIGNATURE_URLS = {
    "twitter": ("Twitter / X", "https://x.com/{h}"),
    "instagram": ("Instagram", "https://www.instagram.com/{h}/"),
    "tiktok": ("TikTok", "https://www.tiktok.com/@{h}"),
    "linkedin": ("LinkedIn", "https://www.linkedin.com/in/{h}"),
}


class WebHarvester:
    """Platform tespiti, CANLI doğrulama ve arşiv bağlantısı üreten modül."""

    def __init__(self, target_name: str, session=None, client: HttpClient = None,
                 settings=None):
        self.settings = settings or get_settings()
        self.target_name = target_name.strip()
        self.slug = slugify(self.target_name) or self.target_name.lower().replace(" ", "")

        # Sıkı varyasyonlar (benzerlik/ilgi kontrolü için)
        self.core_variants = name_variants(self.target_name)
        # Denenecek zengin kullanıcı adı adayları (sıralı: en olası önce)
        tiered = generate_handles_with_tiers(
            self.target_name,
            include_loose=bool(self.settings.get("loose_variants", True)))
        if not tiered:
            tiered = [(self.slug, 0)]
        self.handle_tiers = {handle: tier for handle, tier in tiered}
        self.generated_handles = [handle for handle, _tier in tiered]
        # İlgi kontrolünde hem sıkı hem üretilmiş biçimler kabul edilir.
        self.variants = set(self.core_variants) | set(self.generated_handles) | {self.slug}

        if client is not None:
            self.client = client
            self.session = client.session
        else:
            # Özel (test) oturumu verildiyse önbelleği kapat.
            cache = DiskCache(enabled=False) if session is not None else None
            self.client = HttpClient(settings=self.settings, cache=cache, session=session)
            self.session = self.client.session

        self.rate_limiter = _SimpleDelay(self.settings.rate_min, self.settings.rate_max)
        self.regex_signatures = self.settings.regex_signatures
        # Duvara takılan platformlar için alternatif doğrulama yolları
        # (TikTok oEmbed/embed, Instagram web API'si, kullanıcı oturumu...).
        self.verifiers = VerifierRegistry(self.client, self.settings)

    # -- Ana akış -----------------------------------------------------------

    def harvest(self) -> dict:
        tasks = [
            {"name": "duckduckgo", "func": self._search_duckduckgo},
            {"name": "bing", "func": self._search_bing},
            {"name": "google", "func": self._search_google},
            {"name": "generic-search", "func": self._search_engines},
        ]
        for name, template in PLATFORM_TARGETS:
            tasks.append({"name": f"platform:{name}", "func": self._verify_platform,
                          "args": (name, template)})

        pool = OSINTThreadPool(max_workers=self.settings.max_threads)
        results = pool.execute_tasks(tasks)

        snippets = []
        candidates = {}
        for result in results:
            if result["status"] != "SUCCESS":
                log.warning("toplama görevi başarısız: %s -> %s",
                            result["task"], result.get("error"))
                continue
            if result["task"].startswith("platform:"):
                self._merge_candidates(candidates, result["data"])
            else:
                found_snippets, found_candidates = result["data"]
                snippets.extend(found_snippets)
                self._merge_candidates(candidates, found_candidates)


        # Arama ile bulunan ama henüz doğrulanmamış adayları İÇERİKLE doğrula.
        self._verify_search_candidates(candidates)
        candidates = {k: v for k, v in candidates.items() if not v.get("rejected")}

        clean_snippets = [s for s in snippets if s and len(s) > 10]
        clean_snippets = self._filter_relevant_snippets(clean_snippets)
        log.info("tarama bitti: %s aday, %s özet", len(candidates), len(clean_snippets))
        return {
            "snippets": clean_snippets or [
                "Açık web aramalarında doğrudan metin özetine ulaşılamadı."],
            "candidates": list(candidates.values()),
            "socials": [(c["platform"], c["url"], c["archive"])
                        for c in candidates.values()],
        }

    # -- Yardımcılar --------------------------------------------------------

    def _merge_candidates(self, store: dict, new_candidates: list):
        for cand in new_candidates:
            key = (cand["platform"], cand["url"].rstrip("/").lower())
            if key in store:
                existing = store[key]
                existing["source_count"] = min(existing.get("source_count", 1) + 1, 5)
                existing["platform_confirm"] = max(
                    existing.get("platform_confirm", 0.0), cand.get("platform_confirm", 0.0))
                existing["page_has_handle"] = (existing.get("page_has_handle")
                                               or cand.get("page_has_handle"))
                # Duvar işareti, ancak HER İKİ kaynak da doğrulayamadıysa ve
                # hiçbiri kanıt üretmediyse korunur. Bir kaynak doğruladıysa
                # (platform_confirm >= 0.5) duvar "kalkar".
                both_walled = bool(existing.get("wall")) and bool(cand.get("wall"))
                existing["wall"] = both_walled and existing["platform_confirm"] < 0.5
                existing["verified_by"] = (existing.get("verified_by")
                                            or cand.get("verified_by"))
                existing["display_name"] = (existing.get("display_name")
                                              or cand.get("display_name"))
                if not existing.get("description"):
                    existing["description"] = cand.get("description") or ""
            else:
                store[key] = cand

    def _filter_relevant_snippets(self, snippets: list) -> list:
        """Yalnızca hedefle gerçekten ilgili özet metinleri tutar.

        Arama motoru hedef için sonuç bulamadığında sorguyla alakasız
        reklam/spam sayfaları döndürüyor (ör. "sosyal medya profilleri"
        sorgusuna video indirici tanıtımları); bunlar raporu kirletiyordu.
        Hedefin isim parçalarından hiçbirini içermeyen özet elenir.
        """
        tokens = [t for t in tokenize(self.target_name) if len(t) >= 3]
        if not tokens and self.slug:
            tokens = [self.slug]
        if not tokens:
            return snippets

        kept, seen = [], set()
        for snippet in snippets:
            low = slugify(snippet)
            if not any(token in low for token in tokens):
                continue
            if low in seen:
                continue
            seen.add(low)
            kept.append(snippet)
        return kept

    def _is_target_related(self, url: str) -> bool:
        handle = _handle_from_url(url)
        if not handle:
            return False
        if handle in self.variants:
            return True
        if self.slug and (self.slug in handle or handle in self.slug):
            return True
        for variant in self.variants:
            if variant and len(variant) >= 4 and (variant in handle or handle in variant):
                return True
        return False

    def _make_search_candidate(self, platform: str, url: str, wall: bool = False) -> dict:
        """Arama sonucundan gelen adayı kanonik bir sözlüğe çevirir."""
        handle = _handle_from_url(url)
        return {
            "platform": platform,
            "url": url,
            "handle": handle,
            "target": self.target_name,
            "archive": f"https://web.archive.org/web/*/{url}",
            "platform_confirm": 0.3,
            "page_has_handle": False,
            "source_count": 1,
            "is_profile_url": not any(m in url.lower() for m in NON_PROFILE_MARKERS),
            "source": "search",
            "variant_tier": self.handle_tiers.get(handle, 3),
            "display_name": None,
            "description": "",
            # Duyarlı platformlarda içerik zaten gizli: aday daha
            # doğrulanmadan "anonim doğrulanamaz" olarak işaretlenir.
            "wall": bool(wall or platform in OPAQUE_PLATFORMS),
        }

    # -- Arama motorları ----------------------------------------------------

    @staticmethod
    def _decode_bing_redirects(html: str) -> list:
        """Bing sonuç linklerini `u=a1<base64url>` biçiminden çözer.

        Bing, gerçek hedef URL'leri düz metin olarak vermez; bu yüzden ham
        regex ile hiçbir şey bulunamıyordu.
        """
        urls = []
        for encoded in re.findall(r"u=a1([A-Za-z0-9_\-]+)", html):
            padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
            try:
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8", "ignore")
            except Exception:
                continue
            if decoded.startswith("http"):
                urls.append(decoded.split("&")[0] if "bing.com" in decoded else decoded)
        return urls

    @staticmethod
    def _decode_ddg_redirects(html: str) -> list:
        """DuckDuckGo `uddg=` yönlendirme parametrelerini çözer."""
        urls = []
        for encoded in re.findall(r"uddg=([^&\"']+)", html):
            decoded = urllib.parse.unquote(encoded)
            if decoded.startswith("http"):
                urls.append(decoded)
        return urls

    def _extract_social_candidates(self, html: str, extra_urls=None) -> list:
        candidates, seen = [], set()

        # 1) Ham ve çözülmüş URL'lerden
        all_urls = re.findall(r'https?://(?:www\.)?[\w-]+\.[\w.-]+[^\s<"\']+', html)
        all_urls += list(extra_urls or [])
        for link in all_urls:
            link_lower = link.lower()
            if any(m in link_lower for m in NON_PROFILE_MARKERS):
                continue
            if not self._is_target_related(link):
                continue
            for plat_name, domain in SEARCH_DOMAIN_MAP:
                if domain in link_lower:
                    clean = link.split('&')[0].split('?')[0]
                    if clean not in seen:
                        seen.add(clean)
                        candidates.append(self._make_search_candidate(plat_name, clean))
                    break

        # 2) config/regex_signatures.json imzalarından (daha önce kullanılmıyordu)
        for platform_key, pattern in self.regex_signatures.items():
            if platform_key not in SIGNATURE_URLS:
                continue
            plat_name, url_template = SIGNATURE_URLS[platform_key]
            try:
                for handle in re.findall(pattern, html):
                    handle = handle.lstrip("@")
                    if not self._is_meaningful_handle(handle):
                        continue
                    url = url_template.format(h=handle)
                    if url in seen or not self._is_target_related(url):
                        continue
                    seen.add(url)
                    candidates.append(self._make_search_candidate(plat_name, url))
            except re.error:
                log.warning("geçersiz regex imzası: %s", platform_key)
        return candidates

    @staticmethod
    def _is_meaningful_handle(handle: str) -> bool:
        if not handle or len(handle) < 3 or len(handle) > 40:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9_.\-]+", handle))

    BLOCKED_HINTS = ("anomaly", "unusual traffic", "enable javascript",
                     "erişme konusunda sorun")

    def _looks_blocked(self, resp) -> bool:
        """Arama motorunun bot/consent sayfası döndürüp döndürmediğini anlar."""
        if not resp.ok or resp.status_code in (202, 403, 429):
            return True
        low = (resp.text or "").lower()
        return len(low) < 3000 and any(h in low for h in self.BLOCKED_HINTS)

    def _search_duckduckgo(self) -> tuple:
        """DuckDuckGo HTML; engellenirse lite sürümüne düşer ve uddg çözer."""
        snippets, candidates = [], []
        query = urllib.parse.quote(
            f"{self.target_name} instagram tiktok twitter spotify youtube github linkedin")
        for base in ("https://html.duckduckgo.com/html/?q=",
                     "https://lite.duckduckgo.com/lite/?q="):
            try:
                self.rate_limiter.wait()
                resp = self.client.get(f"{base}{query}")
                if self._looks_blocked(resp):
                    log.info("arama engellendi: %s", base)
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                for node in soup.find_all("a", class_="result__snippet"):
                    snippets.append(node.get_text().strip())
                extra = self._decode_ddg_redirects(resp.text)
                candidates = self._extract_social_candidates(resp.text, extra)
                if snippets or candidates:
                    break
            except Exception as e:
                snippets.append(f"[!] DuckDuckGo Arama Hatası: {e}")
                break
        return snippets, candidates

    def _search_bing(self) -> tuple:
        """Bing; sonuç linkleri `u=a1<base64>` olduğu için önce onlar çözülür."""
        snippets, candidates = [], []
        try:
            self.rate_limiter.wait()
            query = urllib.parse.quote(
                f"{self.target_name} instagram tiktok sosyal medya hesapları profilleri")
            resp = self.client.get(f"https://www.bing.com/search?q={query}")
            if resp.ok and not self._looks_blocked(resp):
                soup = BeautifulSoup(resp.text, "html.parser")
                # Yalnızca gerçek sonuç bloklarındaki açıklamalar alınır; sayfa
                # genelindeki her <p> toplanırsa alakasız reklam/spam metinleri
                # rapora sızıyordu.
                for node in soup.select("li.b_algo p, .b_caption p, .b_snippet p, .b_algoSlug"):
                    text = node.get_text().strip()
                    if len(text) > 15:
                        snippets.append(text)
                extra = self._decode_bing_redirects(resp.text)
                candidates = self._extract_social_candidates(resp.text, extra)
        except Exception as e:
            log.debug("bing arama hatası: %s", e)
        return snippets, candidates

    def _search_google(self) -> tuple:
        """Google'ı dener; consent/JS sayfası dönerse sessizce vazgeçer."""
        snippets, candidates = [], []
        try:
            self.rate_limiter.wait()
            query = urllib.parse.quote(
                f'{self.target_name} (site:twitter.com OR site:x.com OR site:instagram.com'
                f' OR site:tiktok.com)')
            resp = self.client.get(f"https://www.google.com/search?gbv=1&num=20&q={query}")
            if self._looks_blocked(resp):
                log.info("Google otomatik erişimi engelledi (consent/JS sayfası)")
                return snippets, candidates
            soup = BeautifulSoup(resp.text, "html.parser")
            for node in soup.select("div.BNeawe, span.aCOpRe, div.VwiC3b, div.IsZvec"):
                text = node.get_text().strip()
                if len(text) > 15:
                    snippets.append(text)
            extra = [urllib.parse.unquote(u)
                     for u in re.findall(r"/url\?q=([^&\"]+)", resp.text)]
            candidates = self._extract_social_candidates(resp.text, extra)
        except Exception as e:
            log.debug("google arama hatası: %s", e)
        return snippets, candidates

    def _search_engines(self) -> tuple:
        """Ek arama motorları tek bir ortak tarayıcıyla toplanır.

        Yandex / Mojeek / Brave / Startpage sonuç HTML'indeki TÜM bağlantılar
        çıkarılır ve `_extract_social_candidates` (SEARCH_DOMAIN_MAP) ile
        filtrelenir. Böylece motor başına özel ayrıştırma gerekmez ve yeni bir
        kaynak tek satırla eklenir. Engellenen/consent sayfaları sessizce
        atlanır; hiçbiri uygulamayı bozmaz.
        """
        snippets, candidates = [], []
        query = urllib.parse.quote(
            f"{self.target_name} instagram tiktok twitter youtube github profil")
        for name, template in GENERIC_SEARCH_ENGINES:
            try:
                self.rate_limiter.wait()
                resp = self.client.get(template.format(q=query))
                status = getattr(resp, "status_code", 0)
                text = getattr(resp, "text", "") or ""
                low = text.lower()
                if status in (202, 403, 429):
                    log.info("arama engellendi (%s): HTTP %s", name, status)
                    continue
                if len(low) < 3000 and any(h in low for h in self.BLOCKED_HINTS):
                    log.info("arama engellendi (%s): bot/consent sayfası", name)
                    continue
                found = self._extract_social_candidates(text)
                if found:
                    candidates.extend(found)
                    log.info("%s: %d aday", name, len(found))
            except Exception as e:
                log.debug("%s arama hatası: %s", name, e)
        return snippets, candidates

    # -- Arama sonuçlarını kendi doğrulamasından geçirme ---------------------
    def _verify_search_candidates(self, store: dict):
        """Arama ile bulunan profilleri GERÇEKTEN var mı diye doğrular.

        Böylece arama sonucu bir URL, içerik doğrulamasından geçtiğinde
        'doğrulanmış' hale gelir; geçemeyen elenir (kendi eleme süzgeci).
        """
        pending = [c for c in store.values()
                   if c.get("source") == "search" and c.get("platform_confirm", 0) < 0.5]
        if not pending:
            return

        tasks, by_name = [], {}
        for i, cand in enumerate(pending):
            name = f"verify:{i}"
            by_name[name] = cand
            tasks.append({"name": name, "func": self._verify_profile,
                          "args": (cand["platform"], cand["url"], cand.get("handle", ""))})

        results = OSINTThreadPool(max_workers=self.settings.max_threads).execute_tasks(tasks)
        for result in results:
            cand = by_name.get(result["task"])
            if cand is None or result["status"] != "SUCCESS":
                continue
            verification = result["data"]
            if verification.get("wall"):
                # Platform içeriği gizliyor: "yok" denemez. Önce alternatif
                # doğrulama yollarını dene; hiçbiri kanıt vermezse aday
                # "elle kontrol" olarak kalır.
                cand["evidence"] = list(verification["markers"])
                outcome = self._resolve_wall(cand, cand["platform"], cand["url"],
                                             cand.get("handle", ""))
                if outcome == "rejected":
                    cand["rejected"] = True
                    continue
                if outcome == "unknown":
                    cand["wall"] = True
                    cand["platform_confirm"] = max(cand.get("platform_confirm", 0.0), 0.3)
                continue
            if verification["exists"] is False:
                cand["rejected"] = True          # sahte/dead link -> ele
                continue
            cand["platform_confirm"] = max(cand.get("platform_confirm", 0.0),
                                           verification["confirm"])
            cand["page_has_handle"] = (cand.get("page_has_handle")
                                       or verification["page_has_handle"])
            cand["source_count"] = cand.get("source_count", 1) + 1
            cand["source"] = "search+verified"
            cand["evidence"] = verification["markers"]
            if verification.get("display_name"):
                cand["display_name"] = verification["display_name"]
            if verification.get("description"):
                cand["description"] = verification["description"]

    # -- Canlı / içerik tabanlı doğrulama (paralel çalışır) -----------------

    def _verify_platform(self, platform: str, url_template: str) -> list:
        """Tek bir platformdaki aday kullanıcı adlarını sırayla doğrular.

        Duyarlı (opaque) platformlarda içerik doğrulaması mümkün olmadığı için
        yalnızca isme güçlü biçimde bağlı adaylar (tam eşleşme, ayraçlı, baş
        harf) deneye alınır; böylece rapor alakasız adreslerle dolmaz.
        """
        candidates = []
        opaque = platform in OPAQUE_PLATFORMS
        max_wall = int(self.settings.get("max_wall_candidates", 3))
        wall_found = 0

        for handle in self._candidate_handles(strong_only=opaque):
            tier = self.handle_tiers.get(handle, 0)
            if opaque and tier > TIER_INITIAL:
                continue
            # Bir kez duvarla karşılaşıldıysa (ör. LinkedIn bot koruması)
            # gevşek biçimleri hiç denemeyiz: doğrulanamayan adreslerle
            # listeyi doldurmanın anlamı yok, üstelik istek israfı olur.
            if wall_found and tier > TIER_INITIAL:
                continue
            url = url_template.format(h=handle)
            self.rate_limiter.wait()
            verification = self._verify_profile(platform, url, handle)
            if verification["exists"] is False:
                continue
            candidate = {
                "platform": platform,
                "url": url,
                "handle": handle,
                "target": self.target_name,
                "archive": f"https://web.archive.org/web/*/{url}",
                "platform_confirm": verification["confirm"],
                "page_has_handle": verification["page_has_handle"],
                "source_count": 2 if verification["confirm"] >= 0.5 else 1,
                "is_profile_url": True,
                "source": "direct",
                "evidence": verification["markers"],
                "variant_tier": tier,
                "display_name": verification.get("display_name") or "",
                "description": verification.get("description") or "",
            }
            # Platform içeriği gizledi: sayfanın var olup olmadığını bilemeyiz.
            # Adayı silmek yerine "elle doğrula" olarak işaretleyip raporlarız;
            # ama yalnızca isme doğrudan bağlı (güçlü) adresler için ve
            # platform başına sınırlı sayıda.
            if verification.get("wall"):
                if tier > TIER_INITIAL or wall_found >= max_wall:
                    continue
                # Duvara takıldıysa alternatif doğrulama yollarını dene.
                outcome = self._resolve_wall(candidate, platform, url, handle)
                if outcome == "rejected":
                    continue
                if outcome == "unknown":
                    candidate["wall"] = True
                    candidate["platform_confirm"] = max(candidate["platform_confirm"], 0.3)
                    wall_found += 1
            candidates.append(candidate)
        return candidates

    def _resolve_wall(self, candidate: dict, platform: str, url: str,
                      handle: str) -> str:
        """Duvara takılan adayı alternatif yollarla doğrulamayı dener.

        Instagram/TikTok gibi platformlarda doğrudan HTML içeriği gelmez;
        bu yüzden oEmbed, embed sayfası, web API'si veya kullanıcının kendi
        oturumu gibi ek yollar denenir.

        Dönüş değerleri:
          * "confirmed" -> sağlayıcı profili KANITLADI (görünen ad ile)
          * "rejected"  -> sağlayıcı kesin "yok" dedi (aday elenir)
          * "unknown"   -> kanıt yok; aday "elle kontrol" olarak kalır

        Önemli: "unknown" ASLA "hesap yok" demek değildir.
        """
        if not getattr(self, "verifiers", None):
            return "unknown"
        result = self.verifiers.verify(platform, url, handle)
        evidence = candidate.setdefault("evidence", [])
        evidence.append(f"sağlayıcı {result.provider}: {result.status}")

        if result.confirmed:
            candidate["wall"] = False
            candidate["platform_confirm"] = max(candidate.get("platform_confirm", 0.0), 0.8)
            candidate["page_has_handle"] = True
            candidate["source_count"] = candidate.get("source_count", 1) + 1
            candidate["verified_by"] = result.provider
            if result.display_name:
                candidate["display_name"] = result.display_name
            evidence.append(result.describe())
            log.info("duvar aşıldı: %s %s (%s)", platform, url, result.evidence)
            return "confirmed"

        if result.rejected:
            evidence.append(result.describe())
            log.info("duvar: sağlayıcı 'yok' dedi: %s %s", platform, url)
            return "rejected"

        evidence.append(f"otomatik doğrulama başarısız: {result.evidence}")
        return "unknown"

    def _candidate_handles(self, strong_only: bool = False) -> list:
        """Denenecek kullanıcı adları.

        Biçimler tier'lar arasında DÖNÜŞÜMLÜ seçilir; böylece üst sınır darsa
        bile yalnızca güçlü biçimler değil, harf sırası değişimi / ünlü
        düşürme / kısaltma gibi gevşek biçimler de denenir.

        strong_only=True: içerik doğrulaması yapılamayan platformlar için
        yalnızca isme doğrudan bağlı biçimler döndürülür.
        """
        limit = int(self.settings.get("max_handle_variants", 12))
        tiered = [(h, t) for h, t in self.handle_tiers.items()]
        if strong_only:
            tiered = [(h, t) for h, t in tiered if t <= TIER_INITIAL]
        ordered = [self.slug] + [h for h in order_for_probing(tiered, limit=limit)
                                 if h != self.slug]
        seen, result = set(), []
        for handle in ordered:
            if handle and handle not in seen:
                seen.add(handle)
                result.append(handle)
        return result[:limit]

    def _verify_profile(self, platform: str, url: str, handle: str,
                         display_name: str = None) -> dict:
        """Sayfa içeriğini inceleyerek profilin var olup olmadığını belirler.

        display_name -> raporlama ve AI tarafı içindiği sürece harvester tarafından
        dağıtılmaya değerdir; ilk geçen güvenilir gösterim sett edilir.
        """
        result = {"exists": None, "confirm": 0.0, "page_has_handle": False,
                  "markers": [], "display_name": display_name or "",
                  "description": "", "wall": False}
        # Duvarlı platformlarda istek, Chrome taklit eden taşımayla atılır
        # (TLS/JA3 + HTTP/2 parmak izi). ÖLÇÜLDÜ (17.10.2026): Instagram aynı
        # URL'de normal Python istemcisine ~595 KB kabuk, Chrome taklidine
        # ~785 KB GERÇEK profil sayfası (og:title = görünen ad) döndürüyor;
        # bilinen 5 hesapta 5/5 isabet. curl_cffi kurulu değilse sessizce
        # normal oturuma düşer ve davranış eski hâline döner.
        resp = self.client.get(url, timeout=self.settings.timeout,
                               impersonate=platform in OPAQUE_PLATFORMS)

        if resp.status_code == 0:
            result["markers"].append("ağ hatası")
            return result
        if resp.status_code in (404, 410):
            result["exists"] = False
            result["markers"].append(f"HTTP {resp.status_code}")
            return result
        if resp.status_code in (401, 403, 429, 503, 999):
            # Bot/erişim engeli (999 = LinkedIn'in bot koruması): sayfanın var
            # olup olmadığı öğrenilemedi. "Yok" DENEMEZ; aday, elle kontrol
            # edilecek olarak işaretlenir.
            result["wall"] = True
            result["markers"].append(
                f"erişim engeli (HTTP {resp.status_code}): anonim doğrulama yapılamadı")
            return result
        if resp.status_code >= 400:
            result["markers"].append(f"HTTP {resp.status_code}")
            return result

        html = resp.text or ""
        low = html.lower()
        handle_low = (handle or "").lower()

        soup = BeautifulSoup(html, "html.parser")
        og_title = self._meta(soup, "og:title")
        og_desc = self._meta(soup, "og:description")
        page_title = soup.title.get_text().strip() if soup.title else ""
        # Profil açıklaması (bio) yeni AI kanıtıdır: og:title adı vermese bile
        # açıklama hedefin ismini içerebilir.
        result["description"] = og_desc or ""

        # DUVAR TESPİTİ (negatif metin işaretlerinden ÖNCE gelir):
        # duyarlı platformlar içeriği anonim istemciye göstermiyorsa
        # "hesap yok" sonucu ÇIKARILAMAZ. Instagram giriş duvarı ve TikTok
        # WAF meydan okuması, var olan ve var olmayan kullanıcı için AYNI
        # sayfayı döndürür; o sayfanın JS'i içinde "404" geçen metinler
        # bulunur ve bunlar kanıt sayılamaz. Bu yüzden aday silinmez;
        # "anonim doğrulanamadı" olarak işaretlenip elle kontrol için sunulur.
        if platform in OPAQUE_PLATFORMS and not og_title and not og_desc:
            signal = self._wall_signal(low) or self._wall_text_signal(low)
            if signal:
                result["wall"] = True
                result["markers"].append(
                    f"platform duvarı ({signal}): anonim doğrulama mümkün değil")
                return result

        for marker in NEGATIVE_MARKERS:
            if marker in low:
                result["exists"] = False
                result["markers"].append(f"işaret: '{marker}'")
                return result

        # OG title'dan platform kısmını çıkarın. Çoğu platform "Kişi · Platform" biçiminde.
        raw_display = self._best_display_name(og_title, page_title, soup, platform)
        result["display_name"] = raw_display or result["display_name"]

        for label, meta_text in (("og:title", og_title), ("og:description", og_desc),
                                 ("<title>", page_title)):
            if meta_text and handle_low and handle_low in meta_text.lower():
                result["page_has_handle"] = True
                result["confirm"] += 0.40
                result["markers"].append(f"{label} içinde handle")
                break

        # DUVARLI PLATFORMLARDA GÜÇLÜ KANIT: bu platformların duvar/kabuk
        # yanıtı og:title İÇERMEZ; başlığı yalnızca GERÇEK profil sayfası
        # verir ("Görünen Ad (@kullanıcı) • Instagram..."). Yani og:title
        # içinde hedefin geçmesi, duvar yokluğunun ölçülmüş kanıtıdır.
        if (platform in OPAQUE_PLATFORMS and og_title
                and handle_low and handle_low in og_title.lower()):
            result["confirm"] = max(result["confirm"], 0.9)
            result["markers"].append("profil meta verisi (og:title) okundu — duvar yok")

        if platform == "GitHub" and any(k in low for k in
                                        ['itemprop="name"', "octolytics", "js-profile-editable"]):
            result["confirm"] += 0.30
            result["markers"].append("GitHub profil işareti")
        if platform == "YouTube" and ('"channelid"' in low or '"externalid"' in low):
            result["confirm"] += 0.30
            result["markers"].append("YouTube kanal işareti")
        if platform == "Spotify" and ('"type":"user"' in low or "spotify:user:" in low):
            result["confirm"] += 0.40
            result["page_has_handle"] = True
            result["markers"].append("Spotify kullanıcı işareti")

        is_generic_shell = (
            not result["page_has_handle"]
            and any(k in low for k in
                    ["log in", "giriş yap", "sign up", "kaydol", "create an account"])
        )
        if is_generic_shell:
            result["exists"] = False
            result["markers"].append("giriş duvarı / boş kabuk (profil değil)")
            return result

        if platform in RELIABLE_STATUS_PLATFORMS and result["confirm"] < 0.5:
            result["confirm"] = max(result["confirm"], 0.55)
            result["markers"].append("platform 404-doğrulaması (HTTP 200 = var)")

        result["confirm"] = min(result["confirm"], 1.0)
        if result["confirm"] >= 0.5:
            result["exists"] = True
        elif result["confirm"] <= 0.05:
            result["exists"] = False
            result["markers"].append("doğrulama kanıtı yok (anonim doğrulama mümkün değil)")
        else:
            result["exists"] = None
        return result

    @staticmethod
    def _wall_signal(low_html: str) -> str:
        """Giriş/anti-bot duvarını tespit eder; bulunan imzayı döndürür."""
        for marker in WALL_MARKERS:
            if marker in low_html:
                return marker
        return ""

    @staticmethod
    def _wall_text_signal(low_html: str) -> str:
        """Kabuk sayfadaki giriş/kayıt çağrısını duvar işareti olarak döndürür."""
        for marker in WALL_TEXT_HINTS:
            if marker in low_html:
                return marker
        return ""

    @staticmethod
    def _meta(soup, prop: str) -> str:
        tag = (soup.find("meta", attrs={"property": prop})
               or soup.find("meta", attrs={"name": prop}))
        if tag and tag.get("content"):
            return tag["content"]
        return ""

    @staticmethod
    def _best_display_name(og_title: str, page_title: str, soup, platform: str) -> str:
        """Profili temsil eden en güçlü gösterim metnini geri döndürür.

        Çoklu platform örneği:
          og:title "Mehmet Yilmaz (@mehmetyilmaz) · GitHub"
          -> "Mehmet Yilmaz"

        Sayfanın gerçek insan isim gösterimini tergetsi, AI bu kanıtla
        kullanıcı adını doğrulayabilir — veya tersi de: başka bir isim
        görüntüleniyorsa bu bir kalıntı/özel profil gibi tehlikesi ayırır.
        """
        if not og_title and not page_title:
            return ""

        title = og_title or page_title

        # Platform ekleri (" · GitHub") temizle; takma ad parantezlerini temizle.
        cleaned = re.sub(r"\s*[··]\s*\S+$", "", title, flags=re.IGNORECASE)

        # @takma isim parantezlerinden kurtul
        match = re.match(r"^(.*?)\s*\(@[^\)]+\)[\s\u00b7,]*$", cleaned, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # "KullanıcıAdı @handle" biçimine düş -> önceki kısım gösterim
        match = re.match(r"^([^\s@\t\n][^\s@\t\n]*\s[^\s@\t\n][^\s@\t\n]*)", title)
        if match:
            return match.group(1).strip()

        # Basit fallback: sütundaki ilk kelime grubu.
        return cleaned.strip()


class _SimpleDelay:
    """İstekler arası rastgele gecikme (platformları yormamak için)."""

    def __init__(self, min_delay: float, max_delay: float):
        import random
        import time
        self._random = random
        self._time = time
        self.min_delay = float(min_delay)
        self.max_delay = float(max_delay)

    def wait(self):
        self._time.sleep(self._random.uniform(self.min_delay, self.max_delay))
