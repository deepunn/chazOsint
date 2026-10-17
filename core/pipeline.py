"""
OSINTPipeline - İstihbarat boru hattının tek orkestrasyon noktası.

Arayüz (GUI), CLI ve testler aynı çekirdeği çağırır. Böylece iş mantığı
UI'dan ayrışır, tek yerden test edilir ve otomasyona uygun hale gelir.

Akış:
    1. Toplama (WebHarvester)           -> adaylar + özetler
    2. Fotoğraf analizi (opsiyonel)     -> adli tıp + kimlik izleri + parmak izi
    3. AI doğrulama (HeuristicFilter)   -> doğrulanan / doğrulanamayan
    4. Güven skoru (ConfidenceScorer)
    5. Rapor (IntelligenceSynthesizer)  -> metin
    6. İlişki ağı (GraphBuilder)
    7. Kalıcılık (DatabaseManager)      -> geçmiş + görsel parmak izleri
"""

import os

from collectors.web_harvester import WebHarvester
from collectors.forensics.photo_forensics import PhotoForensicsAI
from debugger.heuristic_filter import HeuristicFilter
from debugger.confidence_scorer import ConfidenceScorer
from debugger.hallucination_guard import HallucinationGuard
from intelligence.ai_engine import get_engine
from intelligence.photo_identity import PhotoIdentityAI
from intelligence.identity_cluster import IdentityClusterer
from intelligence.synthesizer import IntelligenceSynthesizer
from intelligence.graph_builder import GraphBuilder
from database.storage import DatabaseManager
from core.session_manager import SessionManager
from config.settings import get_settings
from utils.logger import get_logger

log = get_logger("pipeline")


class OSINTPipeline:
    """Bir hedef için tüm istihbarat boru hattını yürütür."""

    def __init__(self, ai_engine=None, db: DatabaseManager = None, settings=None,
                 harvester_factory=None):
        self.settings = settings or get_settings()
        self.ai = ai_engine or get_engine()
        self.db = db or DatabaseManager()
        # Test edilebilirlik: harvester enjekte edilebilir.
        self.harvester_factory = harvester_factory or (
            lambda target: WebHarvester(target, settings=self.settings))

    # -- Ana giriş ----------------------------------------------------------

    def run(self, target: str, photo_path: str = None, on_log=None) -> SessionManager:
        session = SessionManager(target)

        def emit(message):
            session.log(message)
            if on_log:
                try:
                    on_log(message)
                except Exception:
                    pass

        try:
            emit(f"[*] '{target}' için istihbarat boru hattı çalıştırılıyor...")

            # 1) Toplama
            harvester = self.harvester_factory(target)
            session.raw_data = harvester.harvest()
            session.snippets = session.raw_data.get("snippets", [])
            session.candidates = session.raw_data.get("candidates", [])
            emit(f"[+] Açık kaynak taraması: {len(session.candidates)} ham aday.")

            # 2) Fotoğraf analizi (opsiyonel)
            if photo_path and os.path.exists(photo_path):
                self._analyze_photo(session, photo_path, emit)

            # 3) Hallucination temizliği
            clean_snippets = HallucinationGuard().sanitize(session.snippets)

            # 4) AI doğrulama
            ai_filter = HeuristicFilter(engine=self.ai)
            session.verified, session.tentative, ai_logs = ai_filter.verify_candidates(
                target, session.candidates)
            session.raw_data["ai_logs"] = ai_logs
            emit(f"[+] AI doğrulaması: {len(session.verified)} doğrulandı, "
                 f"{len(session.tentative)} doğrulanamadı.")

            # 5) Güven skoru
            session.confidence = ConfidenceScorer(
                session.verified, len(clean_snippets)).calculate_score()

            # 6) Çapraz platform kimlik kümeleri: aynı kullanıcı adı birden çok
            #    platformda çıktıysa bunlar büyük olasılıkla aynı kişidir.
            clusterer = IdentityClusterer(session.verified + session.tentative)
            session.clusters = clusterer.build()
            if session.clusters:
                emit(f"[+] Çapraz platform kimlik kümesi: {len(session.clusters)} ad "
                     f"birden çok platformda görünüyor.")

            # 7) Rapor
            synthesizer = IntelligenceSynthesizer(
                target, session.raw_data, session.verified, ai_logs,
                session.confidence, session.tentative,
                clusters=session.clusters,
                merge_suggestions=clusterer.merge_suggestions(session.clusters))
            session.report = synthesizer.generate_report()
            session.report += self._extra_sections(session)

            # 8) İlişki ağı
            session.network = GraphBuilder(target, session.verified).build_node_network()

            # 9) Kalıcılık
            self._persist(session)
            emit(f"[+] Rapor yerel veritabanına kaydedildi. (süre: {session.duration}s)")

        except Exception as e:
            log.exception("pipeline hatası")
            emit(f"[-] Kritik Hata: {e}")
        finally:
            session.finish()

        log.info("pipeline tamamlandı: %s", session.get_summary())
        return session

    # -- Alt adımlar --------------------------------------------------------

    def _analyze_photo(self, session: SessionManager, photo_path: str, emit):
        try:
            session.photo = PhotoForensicsAI(photo_path).analyze()
            emit(f"[+] Fotoğraf adli tıp: risk %{session.photo['risk_score']*100:.0f} "
                 f"({session.photo['verdict']}).")
        except Exception as e:
            emit(f"[-] Fotoğraf analizi başarısız: {e}")

        try:
            session.photo_identity = PhotoIdentityAI(photo_path).analyze()
            targets = session.photo_identity.get("suggested_targets", [])
            emit(f"[+] Fotoğraftan {len(session.photo_identity.get('leads', []))} kimlik izi, "
                 f"{len(targets)} hedef önerisi.")
        except Exception as e:
            emit(f"[-] Kimlik analizi başarısız: {e}")

        # Görsel parmak izi tekrar kullanım kontrolü
        phash = (session.photo or {}).get("phash")
        if phash:
            try:
                matches = self.db.find_fingerprint(phash)
                session.fingerprint_matches = matches
                if matches:
                    emit(f"[!] Bu görsel daha önce {len(matches)} kez görülmüş "
                         f"(parmak izi eşleşmesi).")
            except Exception as e:
                log.debug("parmak izi sorgusu hatası: %s", e)

    def _extra_sections(self, session: SessionManager) -> str:
        blocks = []
        if session.photo:
            blocks.append(_photo_block(session.photo))
        if session.fingerprint_matches:
            blocks.append(_fingerprint_block(session.fingerprint_matches))
        if session.photo_identity and session.photo_identity.get("leads"):
            blocks.append(_identity_block(session.photo_identity))
        return "\n".join(blocks)

    def _persist(self, session: SessionManager):
        try:
            self.db.save_scan(session.target_name, session.report,
                              session.confidence, session.verified_count)
        except Exception as e:
            log.warning("rapor kaydedilemedi: %s", e)

        phash = (session.photo or {}).get("phash")
        if phash:
            try:
                self.db.save_fingerprint(phash, session.target_name,
                                         (session.photo.get("meta") or {}).get("path"))
            except Exception as e:
                log.debug("parmak izi kaydedilemedi: %s", e)


# --- Rapor blokları ---------------------------------------------------------

def _photo_block(photo: dict) -> str:
    meta = photo.get("meta", {})
    lines = ["", "=" * 70, " 📷 FOTOĞRAF ADLİ TIP ANALİZİ", "=" * 70,
             f" Dosya        : {os.path.basename(meta.get('path') or '-')}",
             f" Boyut        : {meta.get('width')} x {meta.get('height')} ({meta.get('format')})",
             f" Kamera       : {(meta.get('camera_make') or '-')} {meta.get('camera_model') or ''}".rstrip(),
             f" Tarih        : {meta.get('datetime') or '-'}",
             f" Yazılım      : {meta.get('software') or '-'}",
             f" GPS          : {meta.get('gps') or 'yok'}",
             f" Algısal Hash : {photo.get('phash') or '-'}",
             f" ► Manipülasyon Riski : %{photo.get('risk_score', 0)*100:.0f}",
             f" ► Değerlendirme      : {photo.get('verdict')}"]
    for _name, detail in photo.get("signals", []):
        lines.append(f"   • {detail}")
    return "\n".join(lines)


def _fingerprint_block(matches: list) -> str:
    lines = ["", "=" * 70, " 🔁 GÖRSEL TEKRAR KULLANIM TESPİTİ", "=" * 70,
             " Bu görsel daha önce şu hedeflerle ilişkilendirilmiş:"]
    for m in matches:
        lines.append(f"   • {m.get('target')} ({m.get('timestamp')})")
    return "\n".join(lines)


def _identity_block(identity: dict) -> str:
    lines = ["", "=" * 70, " 🪪 FOTOĞRAFTAN ÇIKARILAN KİMLİK İZLERİ", "=" * 70]
    for lead in identity.get("leads", [])[:10]:
        lines.append(f"   • {lead['kind']}: {lead['value']} "
                     f"({lead['source']}, güven %{lead['confidence']*100:.0f})")
    targets = identity.get("suggested_targets", [])
    if targets:
        lines.append(f"   Önerilen hedef(ler): {', '.join(targets)}")
    return "\n".join(lines)
