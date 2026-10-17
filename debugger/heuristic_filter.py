"""
HeuristicFilter - Adayları AI motoru ile GERÇEKTEN doğrulayan/süzgeçten geçiren katman.

Eski sürümdeki hata: bu sınıf hiçbir şeyi filtrelemiyordu; her aday "doğrulandı"
diye işaretleniyor, `threshold` parametresi kullanılmıyordu.

Bu sürüm, her adayı intelligence.ai_engine.AIEngine üzerinden puanlar ve
karar eşiğinin altındaki yanlış eşleşmeleri eler.

Ek olarak EŞLEŞME DERECESİ ayrımı yapar (handle_generator.grade_for_tier):

  * "strong" (tam eşleşme, ayraçlı, baş harf biçimleri)
      -> isme doğrudan bağlıdır; AI eşiği geçilirse DOĞRULANDI sayılır.

  * "lead" (kelime kısaltması, ünlü düşürme, harf sırası değişimi)
      -> isme gevşek bağlıdır. "abugat" için üretilen "bugat" ya da "baugat"
         başka bir kişiye ait olabilir. Bu yüzden bu biçimler asla
         "doğrulandı" diye raporlanmaz; ayrı bir ADAY listesinde, neden
         şüpheli olduğu açıkça yazılarak sunulur.

Bu ayrım, "daha fazla varyasyon dene" isteğini karşılarken yanlış-pozitif
üretmeyi engeller.

ÜÇÜNCÜ DURUM — platform duvarı (wall):
  Instagram giriş duvarı ve TikTok WAF meydan okuması, var olan ve var
  olmayan kullanıcı için AYNI sayfayı döndürür. Bu platformlarda "içerik
  doğrulaması yok" bilgisi "hesap yok" anlamına GELMEZ. Bu yüzden isme
  birebir uyan bir adres (ör. instagram.com/hamigumi), doğrulanmış
  sayılmaz ama aday listesinde gösterilir ve raporda "elle kontrol"
  başlığı altında sunulur.
"""

from intelligence.ai_engine import get_engine
from intelligence.handle_generator import TIER_LOOSE, grade_for_tier, tier_label

# Bir adayın "manuel kontrol edilecek aday" sayılabilmesi için AI'nın en az
# bu kadar olasılık vermesi gerekir. İsim benzerliği yüksek olsa bile AI'nın
# neredeyse sıfır dediği (ör. var olmayan GitHub sayfası) sonuçlar elenir.
TENTATIVE_MIN_PROB = 0.20


class HeuristicFilter:
    """Bulunan aday profilleri AI olasılığına göre doğrular/eler."""

    def __init__(self, threshold: float = None, engine=None):
        self.engine = engine or get_engine()
        self.threshold = self.engine.threshold if threshold is None else threshold
        self.debug_logs = []

    def verify_candidates(self, target_name: str, candidates: list) -> tuple:
        """Adayları puanlar.

        Dönüş: (verified, tentative, logs)
          verified  : AI etiketi 1 VE eşleşme derecesi "strong" olan adaylar.
          tentative : İsmi tutan ama tek başına kanıt sayılmayan adaylar:
                        - gevşek biçimler (harf sırası / ünlü düşürme / kısaltma)
                        - platform duvarı olan adresler (IG/TikTok: içerik
                          anonim istemciye hiç gösterilmiyor)
                        - platform anonim doğrulamaya izin vermeyen diğer adaylar
                      Hepsi manuel kontrol için ayrı listelenir.
          logs      : Ayrıntılı karar günlüğü.
        """
        self.debug_logs = []
        verified = []
        tentative = []

        for raw in candidates:
            cand = dict(raw)
            cand.setdefault("target", target_name)
            label, prob, feats = self.engine.classify(cand)
            cand["ai_prob"] = round(prob, 4)
            cand["ai_label"] = label
            cand["ai_features"] = feats
            cand["ai_reason"] = self.engine.explain(feats)

            tier = feats.get("variant_tier", 0)
            cand["variant_tier"] = tier
            cand["match_grade"] = grade_for_tier(tier)

            name_strong = feats["handle_similarity"] >= 0.8 or feats["slug_match"] >= 0.5

            if cand.get("wall"):
                # Platform içeriği anonim istemciye göstermiyor (Instagram giriş
                # duvarı, TikTok WAF). Bu yüzden SAYFANIN VAR OLUP OLMADIĞI
                # bilinemez: doğrulanmış SAYILAMAZ, ama isme birebir uyan bir
                # adres olduğu için de silinemez -> elle kontrol edilecek aday.
                if name_strong:
                    cand.setdefault(
                        "lead_reason",
                        "platform anonim doğrulamayı engelliyor (giriş/anti-bot "
                        "duvarı) — bağlantıyı elle kontrol edin")
                    tentative.append(cand)
                    status = "ADAY (platform duvarı)"
                else:
                    status = "ELENDİ (duvar + isim bağı zayıf)"
            elif label == 1 and prob >= self.threshold and cand["match_grade"] == "strong":
                verified.append(cand)
                status = "DOĞRULANDI"
            elif label == 1 and prob >= self.threshold:
                cand["lead_reason"] = (f"gevşek biçim ({tier_label(tier)}) — "
                                       "bu biçim başka bir kişiye de ait olabilir")
                tentative.append(cand)
                status = "ADAY (gevşek biçim)"
            elif name_strong and prob >= TENTATIVE_MIN_PROB:
                cand.setdefault(
                    "lead_reason",
                    "platform anonim doğrulamaya izin vermedi (içerik kanıtı yok)")
                tentative.append(cand)
                status = "ADAY (doğrulanamadı)"
            else:
                status = "ELENDİ"

            self.debug_logs.append(
                f"[{status}] [{cand.get('platform','?')}] {cand.get('url','?')} "
                f"-> AI olasılık %{prob*100:.1f} ({cand['ai_reason']})"
            )

        # En güvenilir sonuç en üstte.
        verified.sort(key=lambda c: c["ai_prob"], reverse=True)
        # Adaylar önce isme bağlılık derecesine (tam eşleşme ilk), sonra AI
        # olasılığına göre sıralanır: "hamigumi" arayan kullanıcı en üstte
        # instagram.com/hamigumi adresini görmelidir.
        tentative.sort(key=lambda c: (int(c.get("variant_tier", TIER_LOOSE)),
                                      -float(c.get("ai_prob", 0.0))))
        self.debug_logs.append(
            f"[i] Toplam {len(candidates)} aday incelendi: {len(verified)} doğrulandı, "
            f"{len(tentative)} aday (manuel kontrol), "
            f"{len(candidates)-len(verified)-len(tentative)} elendi."
        )
        return verified, tentative, self.debug_logs

    # Geriye dönük uyumluluk.
    def verify_handles(self, target_name: str, handles: list) -> list:
        candidates = []
        for item in handles:
            if isinstance(item, dict):
                candidates.append(item)
            elif isinstance(item, tuple) and len(item) == 3:
                plat, url, arc = item
                candidates.append({
                    "platform": plat, "url": url, "archive": arc,
                    "handle": url.rstrip("/").split("/")[-1],
                    "target": target_name, "platform_confirm": 0.3,
                    "page_has_handle": False, "source_count": 1, "is_profile_url": True,
                })
        verified, _tentative, logs = self.verify_candidates(target_name, candidates)
        return logs
