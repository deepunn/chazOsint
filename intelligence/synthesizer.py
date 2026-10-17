"""IntelligenceSynthesizer - Doğrulanmış adaylardan okunabilir rapor üretir.

Rapor üç seviyeye ayrılır:

  1. AI TARAFINDAN DOĞRULANAN HESAPLAR
     Sayfa kanıtı bulunan, isme bağlı profiller.

  1b. ADAYLAR - MANUEL KONTROL
     Gevşek biçimler (harf sırası değişimi, ünlü düşmesi...) ve doğrulanamayan
     adaylar. Asla "doğrulandı" diye sunulmaz.

  1c. PLATFORM DUVARI NEDENİYLE OTOMATİK DOĞRULANAMAYAN PROFİLLER
     Instagram / TikTok / LinkedIn gibi platformlar anonim istemciye profili
     göstermez. Bu adresler silinmez, ama doğrulanmış da sayılmaz: kullanıcı
     bağlantıya tıklayarak kendi gözüyle kontrol eder. Bu ayrım, "hesap
     bulunamadı" ile "anonim doğrulanamadı" durumunu karıştırmamak içindir.

  1d. ÇAPRAZ PLATFORM KİMLİK KÜMELERİ
     Aynı kullanıcı adı birden çok platformda bulunduysa bunlar büyük
     olasılıkla aynı kişidir; kanıtlar sönümlemeli log-odds ile birleştirilir
     (bkz. intelligence/identity_cluster.py). Benzer ama FARKLI adlar
     otomatik birleştirilmez; yalnızca "olası bağlantı (doğrulanmadı)"
     olarak önerilir.
"""

from intelligence.handle_generator import tier_label

# Platform duvarı (giriş/anti-bot) olan adaylarda AI olasılığı ÖLÇÜLEMEZ:
# sayfa içeriği hiç gelmediği için model değerlendirecek kanıt bulamaz.
UNMEASURABLE = "ölçülemedi (sayfa içeriği anonim erişime kapalı)"


def match_grade(grade) -> str:
    """Eşleşme derecesini insan-okur metne çevirir."""
    return "DOĞRULANDI" if grade == "strong" else "ADAY (gevşek biçim)"


def _probability_text(item: dict) -> str:
    if item.get("wall"):
        return UNMEASURABLE
    return f"%{float(item.get('ai_prob', 0.0)) * 100:.1f}"


class IntelligenceSynthesizer:
    """Platform, canlı link, arşiv ve AI güven olasılığını gösteren raporlama motoru."""

    def __init__(self, target_name: str, raw_data: dict, verified_candidates: list,
                 debug_logs: list, confidence_score: float, tentative_candidates: list = None,
                 clusters: list = None, merge_suggestions: list = None):
        self.target_name = target_name
        self.raw_data = raw_data
        self.verified_candidates = verified_candidates or []
        self.debug_logs = debug_logs or []
        self.confidence_score = confidence_score
        self.tentative_candidates = tentative_candidates or []
        self.clusters = clusters or []
        self.merge_suggestions = merge_suggestions or []

    # -- Bölümler -----------------------------------------------------------

    def _header(self) -> str:
        return f"""
======================================================================
 🛡️ CHAZ HESAP BULMA ARACI - DETAYLI RAPOR
======================================================================
 🎯 Hedef isim        : {self.target_name.upper()}
 🧠 AI güven skoru    : %{self.confidence_score:.1f}
 ✅ Doğrulanan profil : {len(self.verified_candidates)}
 ◌  Aday (elle)       : {len(self.tentative_candidates)}
 🧩 Kimlik kümesi     : {len(self.clusters)}
 ⚙️  Durum            : AI doğrulaması tamamlandı (kanıtsız sonuçlar elendi)
======================================================================
"""

    def _verified_section(self) -> str:
        lines = ["", "🔗 1. AI TARAFINDAN DOĞRULANAN HESAPLAR:"]
        if not self.verified_candidates:
            lines.append("   [-] Yeterli kanıtla doğrulanmış profil bulunamadı.")
            lines.append("       (Düşük olasılıklı / tesadüfi eşleşmeler AI tarafından elendi.)")
            return "\n".join(lines)
        for item in self.verified_candidates:
            lines.append(f"   • Platform     : {item.get('platform', '?')}")
            lines.append(f"     Kullanıcı adı : {item.get('handle', '-')}")
            lines.append(f"     Canlı link    : {item.get('url', '-')}")
            lines.append(f"     Arşiv (Web)   : {item.get('archive', '-')}")
            lines.append(f"     AI olasılık   : {_probability_text(item)}")
            lines.append(f"     Eşleşme       : {match_grade(item.get('match_grade'))}"
                         f" ({tier_label(item.get('variant_tier', 0))})")
            lines.append(f"     Karar         : {item.get('ai_reason', '-')}")
            lines.append("     " + "-" * 60)
        return "\n".join(lines)

    def _tentative_section(self) -> str:
        lines = ["", "❓ 1b. ADAYLAR - MANUEL KONTROL GEREKİYOR:",
                 "    (gevşek biçim veya doğrulanamayan adres; 'doğrulandı' sayılmaz)"]
        if not self.tentative_candidates:
            lines.append("   [-] Ek aday yok.")
            return "\n".join(lines)
        for item in self.tentative_candidates:
            if item.get("wall"):
                continue
            lines.append(f"   • {item.get('platform', '?')}: {item.get('url', '-')}")
            lines.append(f"     Neden aday : {item.get('lead_reason', 'doğrulanamadı')} "
                         f"| AI olasılık {_probability_text(item)}")
        return "\n".join(lines)

    def _wall_section(self) -> str:
        """Platform duvarı olan adresler: ne 'var' ne 'yok' denebilir."""
        walls = [c for c in self.tentative_candidates if c.get("wall")]
        if not walls:
            return ""
        lines = ["", "🔒 1c. ANONİM OLARAK DOĞRULANAMAYAN PROFİLLER (platform duvarı):",
                 "    Bu platformlar profili giriş yapmadan göstermez; sistem sayfanın",
                 "    var olup olmadığını kanıtlayamaz. Doğrulanmış SAYILMAZ, ama",
                 "    bağlantıdan elle kontrol edebilirsiniz:"]
        for item in walls:
            lines.append(f"   • {item.get('platform', '?')}: {item.get('url', '-')}")
            evidence = item.get("evidence") or []
            marker = evidence[0] if evidence else "içerik anonim erişime kapalı"
            lines.append(f"     Engel        : {marker}")
            lines.append(f"     Eşleşme biçimi: {tier_label(item.get('variant_tier', 0))} "
                         f"| AI olasılık {_probability_text(item)}")
        return "\n".join(lines)

    def _cluster_section(self) -> str:
        """Aynı kullanıcı adının birden çok platformdaki görünümü."""
        if not self.clusters:
            return ""
        lines = ["", "🧩 1d. ÇAPRAZ PLATFORM KİMLİK KÜMELERİ:",
                 "    Aynı kullanıcı adı birden çok platformda bulundu; kanıtlar",
                 "    sönümlemeli olarak birleştirildi. Önce isimden birebir türetilmiş",
                 "    adlar; gevşek biçimler en sonda ve UYARILI listelenir:"]
        for cluster in self.clusters:
            probability = cluster.combined_probability
            probability_text = (f"%{probability*100:.1f}" if probability is not None
                                else "ölçülemedi")
            lines.append(f"   • \"{cluster.handle}\" — {len(cluster.platforms)} platform"
                         f" | birleşik olasılık {probability_text}")
            if getattr(cluster, "is_loose", False):
                lines.append("     UYARI: gevşek biçim (harf sırası/ünlü düşmesi/kısaltma) — "
                             "başka bir kişiye ait olabilir, kimlik iddiası değildir")
            names = cluster.display_names
            if names:
                lines.append(f"     Görünen ad(lar): {', '.join(names[:3])}")
            for member in cluster.members:
                cand = member["candidate"]
                tag = "duvar (doğrulanamadı)" if cand.get("wall") else \
                    f"doğrulandı, %{float(cand.get('ai_prob', 0.0))*100:.1f}"
                lines.append(f"     - {cand.get('platform', '?')}: {cand.get('url', '-')} "
                             f"({tag})")
        if self.merge_suggestions:
            lines.append("")
            lines.append("   ⚠ OLASI BAĞLANTILAR (DOĞRULANMADI — elle kontrol edin):")
            for s in self.merge_suggestions[:5]:
                lines.append(f"     - \"{s['a']}\" ~ \"{s['b']}\" "
                             f"(benzerlik %{s['similarity']*100:.0f})")
        return "\n".join(lines)

    def _decision_panel(self) -> str:
        lines = ["", "🧠 3. AI KARAR PANELİ (doğrulanan sonuçların kanıtları):"]
        if not self.verified_candidates:
            lines.append("   [-] Doğrulanmış sonuç olmadığı için kanıt satırı yok.")
            return "\n".join(lines)
        for i, item in enumerate(self.verified_candidates, 1):
            feats = item.get("ai_features") or {}
            disp = feats.get("display_name_similarity", 0.0)
            lines.append(f"   [{i}] {item.get('platform', '?')}  /  {item.get('handle', '-')}")
            lines.append(f"       Karar dayanağı              : "
                         f"{feats.get('decision_basis', 'model olasılığı')}")
            lines.append(f"       Kullanıcı adı benzerliği    : %{feats.get('handle_similarity', 0)*100:.0f}")
            lines.append(f"       İsimden türetilmiş          : {feats.get('slug_match', 0)}")
            lines.append(f"       Profil görünen adı uyumlu   : %{disp*100:.0f}")
            lines.append(f"       İsim parçaları kapsandı     : {feats.get('token_coverage', 0)}")
            lines.append(f"       Handle şekli               : "
                         f"{'kişi-adı gibi' if feats.get('handle_shape', 1) > 0.6 else 'rastgele/jenerik'}")
            lines.append(f"       Platform doğrulaması       : %{feats.get('platform_confirm', 0)*100:.0f}")
            lines.append(f"       Sayfada handle kanıtı       : {feats.get('page_has_handle', 0)}")
            lines.append(f"       Bağımsız kaynak             : {feats.get('source_count', 0)}")
            lines.append(f"       Güvenilir alan adı          : "
                         f"{'evet' if feats.get('domain_trust', 0) > 0.5 else 'hayır'}")
            lines.append(f"       Profil sayfası (link değil) : "
                         f"{'evet' if feats.get('is_profile_url', 0) > 0.5 else 'hayır'}")
            lines.append("       " + "-" * 60)
        return "\n".join(lines)

    def _snippets_section(self) -> str:
        lines = ["", "🌐 2. AÇIK KAYNAK ARAMA ÖZETİ:"]
        snippets = self.raw_data.get("snippets", [])
        if snippets:
            for s in snippets[:4]:
                text = s if len(s) <= 110 else s[:110] + "..."
                lines.append(f"   • {text}")
        else:
            lines.append("   [-] Özet metin bilgisi bulunamadı.")
        return "\n".join(lines)

    def _logs_section(self) -> str:
        lines = ["", "🛠️ 4. AI & DEBUG LOGLARI:"]
        lines.extend(f"   {log}" for log in self.debug_logs)
        return "\n".join(lines)

    # -- Ana çıktı ----------------------------------------------------------

    def generate_report(self) -> str:
        report = (self._header() + self._verified_section() + self._tentative_section()
                  + self._wall_section() + self._cluster_section()
                  + self._decision_panel() + self._snippets_section()
                  + self._logs_section())
        report += """
======================================================================
 [Rapor Sonu - Yerel SQLite Veritabanına Kaydedildi]
======================================================================
"""
        return report
