# 🛡️ CHAZ OSINT Enterprise 

Yerel çalışan, **harici API bağımlılığı olmayan**, kendi kendine öğrenen yapay
zeka ile doğrulama yapan modüler siber istihbarat ve tehdit analiz platformu.

Bir isim (veya bir fotoğraf) verirsiniz; sistem ilgili hesapları toplar,
**AI Core** her sonucu "gerçekten bu kişiye mi ait?" diye puanlar ve yalnızca
güvenilir sonuçları raporlar.

## 🚀 Kurulum ve Çalıştırma

```bash
pip install -r requirements.txt
python main.py                 # Grafik arayüz
python cli.py scan "Ahmet Yılmaz"   # Komut satırı
```

---

## ⚡ Sürüm 4.1 — düzeltilenler

**"hamigumi yazdım, Instagram ve TikTok gelmiyor" hatasının kökü ve çözümü:**

1. **Yanlış "hesap yok" işareti (kök neden).** Instagram, profili anonim
   istemciye hiç göstermez: giriş duvarı + ~590 KB'lık JS kabuğu döndürür —
   var olan (`natgeo`) ve var olmayan kullanıcı için **aynı** sayfa. O kabuğun
   JS'i içinde geçen `ox_redesigned_404_page` metni "sayfa bulunamadı"
   sanılıyordu; bu yüzden **gerçek IG profilleri eleniyordu**. Metin artık
   negatif işaret değil (test: `test_instagram_js_404_component_is_not_evidence`).
2. **TikTok hiç yoklanmıyordu.** Platform listesine **TikTok**, **LinkedIn** ve
   **Twitch** eklendi. TikTok bir WAF meydan okuma sayfası döndürüyor
   (`slardarWAF`), LinkedIn bot koruması (HTTP 999) — ikisi de doğrulama
   engeli olarak algılanır.
3. **"Duvar" kavramı eklendi.** Instagram / TikTok / Facebook / LinkedIn
   içeriği gizlediği için "doğrulama yok" bilgisi artık **"hesap yok"
   sayılmaz**. Bu adresler silinmez; raporun **“1c. Anonim olarak
   doğrulanamayan profiller”** bölümünde ve arayüzde 🔒 ile, *doğrulanmış
   sayılmadan* sunulur. Gerçek HTTP 404/410 yanıtları eskisi gibi elenir.
4. **Kullanıcı adı sorgusu desteği.** Hedef tek kelime (ör. `hamigumi`) ise
   o adla var olan sayfa *aranan şeyin kendisidir*; sayfadaki görünen adın
   farklı olması eşleşmeyi çürütmez. Tam ad sorgusunda ("Ahmet Yılmaz")
   sayfa başka birini gösteriyorsa eşleşme reddedilir.
5. **Arama motoru sorgularına** `tiktok` ve `linkedin` eklendi; duvarlı
   adaylar arama doğrulamasında artık elenmiyor.
6. **Arayüz:** gerçek menü çubuğu (Dosya/Tarama/Görünüm/Yardım) + klavye
   kısayolları, tıklanabilir (eskiden yalnızca çerçeve tıklanıyordu) ve hover
   vurgulu kenar menüsü, hızlı işlem düğmeleri, durum çubuğu, "Profili Aç"
   (çift tıklama), JSON/Markdown dışa aktarma.
7. **CLI artık çökmüyor:** Windows konsolu (cp1254) raporda emoji görünce
   `UnicodeEncodeError` veriyordu; yazılamayan karakterler `?` ile değişiyor.

## 🔓 Sürüm 4.2 — Instagram/TikTok'a bakılabiliyor (ölçülmüş yollar)

Bu ağda **17.10.2026** tarihinde ölçülen sonuçlar:

| İstemci | Instagram profili | TikTok |
|---|---|---|
| `requests` (OpenSSL, HTTP/1.1) | ~595 KB kabuk/duvar, `og:title` **YOK** | WAF sayfası (`Please wait...`), oEmbed **429** |
| **Chrome taklidi** (`curl_cffi`; TLS/JA3 + HTTP/2) | **~785 KB GERÇEK profil sayfası**, `og:title` = `Görünen Ad (@kullanıcı)` — bilinen 5 hesapta **5/5** | embed sayfası: `nickname` + `uniqueId` ile **200**; olmayan hesapta **503** (2/2) |

Yani fark yaratan şey **programlama dili değil, TLS/HTTP2 parmak izi.** Bu yüzden:

* `HttpClient` artık `impersonate=True` ile Chrome taklidi yapabilir
  (`curl_cffi`). Kurulu değilse **sessizce normal oturuma düşer** — hiçbir şey bozulmaz.
* Duvarlı platformlarda (Instagram / TikTok / Facebook) doğrudan yoklama taklitli
  taşımayla yapılır ve bu istekler **ayrı bir önbellek anahtarı** kullanır
  (taklitli ve taklitsiz yanıtlar birbirine karışmaz).
* Yeni sağlayıcı **`instagram-html`**: `og:title` içinde hedef kullanıcı adı
  geçiyorsa profil **kanıtlanmış** sayılır ve görünen ad rapora yazılır.
  Duvar/kabuk yanıtı `og:title` **içermediği** için bu, sağlam bir pozitif kanıttır.
* **`tiktok-embed`** güçlendirildi: pozitif kanıt, WAF işaretinden **ÖNCE**
  aranır (embed sayfası `slardar` betiği taşısa bile içindeki gerçek veri kanıttır).
* Sağlayıcı sırası ölçüme göre: `tiktok-embed` → `instagram-html` → `tiktok-oembed`
  → `instagram-web-api`. Son yol oturumsuz çağrıda artık **401 `require_login`**
  döndürüyor; yalnızca kullanıcının kendi oturum çereziyle çalışır.

**Canlı örnek** (`python cli.py scan "hamigumi"`, 18 sn):

```
[DOĞRULANDI]  [TikTok]    https://www.tiktok.com/@hamigumi     (görünen ad: ⒸⒽ Ⓧ ⒽⒶⓂⓏⒶ)
[DOĞRULANDI]  [YouTube]   https://www.youtube.com/@hamigumi
[ADAY(duvar)] [Instagram] https://www.instagram.com/hamigumi/   (elle kontrol)
```

Ek ölçüm: `instagram.com/natgeo` → **"National Geographic"** olarak doğrulandı
(`instagram-html`).

**Değişmeyen sınır:** Instagram bazı adlarda (ör. `hamigumi`) hâlâ kabuk/duvar
veriyor; bu "hesap yok" **değildir**, aday "elle kontrol" olarak kalır. TikTok'un
profil HTML'i ve oEmbed ucu bu ağdan hâlâ 429/WAF döndürüyor (IP seviyesi kota) —
kanıt yolu embed sayfasıdır.

## 🧠 Sürüm 4.3 — AI daha güçlü, daha DÜRÜST

**1) Metrikler artık eğitimde görülmeyen veriden geliyor (holdout).**
Eskiden model, eğitildiği veriyle ölçülüyordu → "F1 %100" gibi şişmiş bir
rakam. Artık sınıf dengeli (stratified) bir doğrulama kümesi ayrılır ve
raporlanan metrik ondan gelir; eğitim metrikleri ayrıca gösterilir ki
ezberleme (overfit) farkı görünsün. Ölçüm (bu makine):

```
DOĞRULAMA (holdout, 13 örnek): doğruluk %92, kesinlik %88, hatırlama %100,
                               F1 %93, log-loss 0.1617, Brier 0.0564
EĞİTİM (39 örnek)            : doğruluk %100, F1 %100  ← fark ezberleme göstergesi
```

**2) Olasılık KALİBRASYONU (Platt/sıcaklık ölçekleme).** Küçük veriyle
eğitilen lojistik regresyon aşırı özgüvenlidir (%0/%100). Sıcaklık T,
doğrulama kümesinde log-loss'u en aza indirecek şekilde seçilir (ölçüm:
0.1649 → 0.1617, T=0.79) ve güvenilirlik tablosu üretilir:

```
güven aralığı   adet    tahmin   gerçek    fark
%0-%20           13    %  4.2   %  0.0   +0.042
%80-%100         13    % 97.1   %100.0   -0.029
```

**3) Çevrimiçi öğrenme güvenli hale geldi.** Her geri bildirim öncesi anlık
görüntü alınır (`Çevrimiçi Güncellemeleri Geri Al`), ağırlıklar ± 6 ile
sınırlıdır; tek bir yanlış geri bildirim modeli bozamaz.

**4) Gereksiz yeniden eğitim kaldırıldı.** Geri bildirim dosyası değişmediyse
açılışta yeniden eğitim YAPILMAZ (her açılışta aynı veriyle eğitip modelin
kademeli iyileşmesini gizlemek yerine yalnızca YENİ kayıtlarda eğitilir).

**5) Yeni yetenek: çapraz platform kimlik kümeleme.** Aynı kullanıcı adı
birden çok platformda bulunduysa bunlar büyük olasılıkla aynı kişidir;
kanıtlar sönümlemeli log-odds ile birleştirilir:
`z = logit(en güçlü) + 0.5 × Σ logit(diğerleri)`. Duvar üyeleri BİLİNMEYEN
sayılır ve puan KATMAZ; gevşek biçimlerden (harf sırası/ünlü düşmesi) oluşan
kümeler "kimlik iddiası değildir" uyarısıyla en sonda listelenir. Raporda
**1d. ÇAPRAZ PLATFORM KİMLİK KÜMELERİ** bölümü olarak çıkar.

**6) Şeffaflık arayüzü:** `python cli.py model` ve GUI'de
**Yapay Zeka > Model Durumu ve Kalibrasyon** aynı raporu verir.

```bash
python cli.py model              # holdout metrikleri + kalibrasyon + ağırlıklar
python cli.py model --retrain    # geri bildirimle yeniden eğit
python cli.py model --revert     # çevrimiçi güncellemeleri geri al
```

## 🖥️ Sürüm 4.3 — Menüler

Menü çubuğu **Dosya / Tarama / Yapay Zeka / Görünüm / Yardım** olarak yenilendi:

| Menü | Yenilik |
|---|---|
| Dosya | `Dışa Aktar` alt menüsü (HTML/JSON/Markdown/**Panoya Kopyala**), **Son Taramalar** (veritabanından son 8 tarama), `Ctrl+T` ile hedef alanına git |
| Tarama | Seçili sonucun **AI açıklaması** (ağırlık × değer = katkı tablosu) |
| Yapay Zeka | Model durumu ve kalibrasyon, karar ağırlıkları, geri bildirim özeti, yeniden eğit (`F9`), geri al, **karar eşiğini ayarla**, "Nasıl karar veriyor?" katman açıklaması, AI kartını gizle/göster |
| Görünüm | `F6` ile verileri yenile (model + geçmiş) |
| Yardım | **Doğrulama Sağlayıcıları (tanılama)**: Chrome taklidi açık mı, hangi sağlayıcılar etkin, kendi oturumun açık mı |

Ek olarak sonuç listesinde **sağ tık menüsü**: profili aç, bağlantıyı kopyala,
arşiv bağlantısını aç, "bu sonuç doğru/yanlış" (AI eğitimi) ve AI karar
açıklaması. Kısayollar menülerde görünür ve `F1` listesinde özetlenir.

## 🏗️ Mimari

```
main.py / cli.py          -> giriş noktaları (GUI / headless)
core/pipeline.py          -> OSINTPipeline: tüm boru hattının orkestrasyonu
core/session_manager.py   -> SessionManager: bir taramanın durum nesnesi
core/thread_pool.py       -> paralel görev yürütücüsü
config/settings.py        -> merkezi yapılandırma (json + txt dosyalarından)
ui/                       -> Tk arayüz: menü çubuğu, kenar menüsü, tema, widget'lar
collectors/
  http_client.py          -> retry/backoff + önbellek + UA rotasyonu
  web_harvester.py        -> paralel toplama + içerik tabanlı doğrulama
  forensics/              -> EXIF, fotoğraf adli tıp AI
intelligence/
  ai_engine.py            -> lojistik regresyon + kalibrasyon + holdout ölçümü
  identity_cluster.py     -> çapraz platform kimlik kümeleme (sönümlemeli log-odds)
  photo_identity.py       -> fotoğraftan kimlik izleri
  synthesizer.py, graph_builder.py
debugger/                 -> filtre, güven skoru, halüsinasyon temizliği
database/storage.py       -> SQLite: geçmiş + görsel parmak izleri
utils/                    -> cache, logger, rate limiting
exporters/                -> HTML / JSON / Markdown rapor
tests/                    -> 161 test (AI, kalibrasyon, kümeleme, varyasyon, duvar,
                             doğrulayıcılar, menüler, fotoğraf, altyapı)
```

Boru hattı **tek yerden** (`OSINTPipeline`) yönetilir; GUI ve CLI aynı çekirdeği
kullanır. Bu sayede iş mantığı arayüzden ayrışmış ve test edilebilir hale gelmiştir.

## ⚡ Altyapı iyileştirmeleri

- **Merkezi konfigürasyon:** `config/settings.json`, `user_agents.txt` ve
  `regex_signatures.json` artık gerçekten okunuyor (önceden ölü dosyalardı).
- **Dayanıklı HTTP katmanı:** üstel geri çekilmeli yeniden deneme, bağlantı
  havuzu, 512 KB sınırlı TTL önbelleği (`database/http_cache.json`).
- **Paralel toplama:** platform doğrulamaları ve arama motorları `OSINTThreadPool`
  ile eşzamanlı çalışır. Gerçek ölçüm: 6 platform + 2 arama motoru **~3 saniye**
  (eskiden sıralıydı ve ~30 sn sürüyordu).
- **Şema migrasyonu:** eski veritabanlarına eksik sütunlar otomatik eklenir.
- **Yapılandırılmış loglama:** `logs/chaz.log` (dönen dosya).
- **Headless CLI:** otomasyon için `scan` / `history` / `stats` / `model`.

## 🔎 Arama katmanı ve kendi eleme süzgeci

Hesap keşfi iki koldan yürür:

1. **Doğrudan platform yoklaması (birincil, en güvenilir).** İsimden üretilen
   kullanıcı adı varyasyonları ilgili platformlarda gerçekten sorgulanır ve
   sayfa **içeriği** doğrulanır. "abugat" için `x.com/abugat` böyle bulunur.
   Yoklanan platformlar: Instagram, TikTok, Twitter/X, Spotify, YouTube,
   SoundCloud, GitHub, LinkedIn, Twitch. İçerik doğrulanamayan platformlarda
   (Instagram/TikTok/Facebook) yalnızca isme doğrudan bağlı biçimler denenir
   ve platform başına en fazla `max_wall_candidates` (3) aday sunulur.
2. **Arama motorları (ikincil).** Bing sonuç linkleri `u=a1<base64>` biçiminde
   kodlandığı için önce **çözülür** (eskiden bu yüzden hiç aday çıkmıyordu);
   DuckDuckGo `uddg=` yönlendirmeleri de çözülür.

**Kendi elemesi:** arama ile bulunan her profil, raporlanmadan önce uygulamanın
kendi içerik doğrulamasından geçirilir — doğrulananlar güçlendirilir, ölü/sahte
linkler elenir.

> **Dürüst sınırlama:** Bazı ağlardan Google bir consent/JS sayfası döndürür ve
> DuckDuckGo 202 (bot engeli) verir; bu durumlar algılanıp sessizce atlanır.
> Bu yüzden keşif esas olarak doğrudan yoklamaya dayanır ve arama motoru
> sonuçları ağa göre değişkenlik gösterir.

> **Dürüst sınırlama 2:** LinkedIn profilleri anonim istemciye HTTP 999 (bot
> koruması) döndürür; içerik doğrulanamaz, aday "elle kontrol" olarak sunulur.
> Instagram ve TikTok için durum **Sürüm 4.2 ile değişti**: Chrome taklidi +
> platforma özel sağlayıcılar (embed sayfası, `og:title`) anonim kanıt
> üretebiliyor — bkz. yukarıdaki tablo. Kanıt üretilemeyen adresler eskisi gibi
> silinmez, "1c. Anonim olarak doğrulanamayan profiller" bölümünde 🔒 ile listelenir.

## 🧠 AI Core (kendi kendine öğrenen)

| Bileşen | Görev |
|---|---|
| `intelligence/ai_engine.py` | Özellik çıkarımı + karar kuralları + lojistik regresyon + çevrimiçi öğrenme |
| `collectors/forensics/photo_forensics.py` | Fotoğraf manipülasyon riski + dHash |
| `debugger/heuristic_filter.py` | Adayları doğrular / eler, duvar adaylarını ayırır |
| `debugger/confidence_scorer.py` | AI olasılıklarından anlamlı güven skoru |

Karar iki katmanlıdır ve her adım rapora yazılır (`Karar dayanağı`):

1. **İlgi kapısı** — aday biçim olarak isme bağlı değilse elenir (model ne
   derse desin). Gevşek biçimler için daha yüksek benzerlik şartı aranır.
2. **Çelişki kapısı** — tam ad sorgusunda sayfa başka birini gösteriyorsa elenir.
3. **Kanıt kuralları** — sayfadaki görünen ad hedefle uyuşuyorsa, sayfa
   başlığı/meta handle'ı doğruladıysa veya platform "bu sayfa var" diyorsa
   eşleşme kesinleşir (bkz. `EVIDENCE_CONFIDENCE`).
4. **Lojistik model** — kanıtın zayıf olduğu gri bölgede kararı model verir.

**14 özellik** kullanılır: `handle_similarity`, `slug_match`,
`display_name_similarity`, `token_coverage`, `handle_shape`,
`platform_confirm`, `page_has_handle`, `source_count`, `domain_trust`,
`is_profile_url`, `display_name_present`, `wall`, `single_token_target` ve
`display_name_conflict` (etkileşim özelliği: sayfa bir ad gösteriyor AMA hedefle
uyuşmuyor → güçlü karşı kanıt).

Model; dengeli bir sentetik küme + sizin gerçek geri bildirimlerinizle
(L2 cezalı gradyan inişi, F1 maksimize eden eşik kalibrasyonu) eğitilir ve
`config/ai_model.json`'a yazılır. Geri bildirimler `database/ai_feedback.jsonl`.
Arayüzdeki bir sonucu **Doğru/Yanlış** işaretlediğinizde model anında güncellenir;
**Tarama → AI Modelini Yeniden Eğit** ile birikmiş geri bildirimden baştan eğitilir.
Model şeması sürümlenmiştir (`MODEL_VERSION`); şema değişirse eski ağırlıklar
geçersiz sayılıp model otomatik yeniden eğitilir.

## 📸 Fotoğraf analizi ve fotoğraftan hesap bulma

- **Adli tıp:** kamera, lens, tarih, yazılım, GPS (ondalık + harita), EXIF
  bütünlüğü, **manipülasyon riski** ve **algısal parmak izi (dHash)**.
- **Görsel tekrar kullanım tespiti:** aynı görsel daha önce başka bir hedefle
  görüldüyse raporda uyarılır (parmak izi veritabanı).
- **Kimlik izleri:** Artist/Copyright/XPAuthor/UserComment vb. alanlardan
  `@handle`, URL, e-posta ve düz kullanıcı adı çıkarımı + dosya adı analizi.
- **Tek tık:** "Önerilen Hedefle Tara" çıkarılan hedefle taramayı başlatır.
- **Ters görsel arama:** Google Lens / Yandex / Bing / TinEye köprüsü.

> **Dürüst sınırlama:** Yerel yüz tanıma YAPILMAZ (yüz veritabanı + harici
> servis gerekir). Fotoğraftan hesap çıkarımı, görselin yukarıdaki izlerden
> birini taşımasına bağlıdır. Aksi halde Ters Görsel Arama kullanılır.

## 🐞 Kritik düzeltme: yanlış sonuçlar (false positives)

1. **`HTTP 200 == hesap var` varsayımı.** Instagram/Spotify var olmayan
   kullanıcı için de 200 döndürür; artık sayfa **içeriği** doğrulanır ve
   güvenilir platformlarda gerçek `404` durum koduna bakılır.
2. **Sahte filtre** yerine AI olasılık eşiği.
3. **Anlamsız güven skoru** yerine AI + kapsam tabanlı skor.
4. **İlgisiz URL'ler** yerine yalnızca hedef slug'ıyla eşleşen adaylar.

## 🖥️ Arayüz

- **Menü çubuğu:** Dosya (yeni tarama, HTML/JSON/Markdown kaydet, çıkış) ·
  Tarama (AI eğitimi, profili aç, ilişki ağı, modeli yeniden eğit) ·
  Görünüm (bölümler, konsolu temizle) · Yardım (kısayollar, hakkında).
- **Kısayollar:** `Ctrl+N` yeni tarama, `F5` tarama, `Ctrl+1..4` bölümler,
  `Ctrl+G` ilişki ağı, `Ctrl+S` HTML rapor, `Ctrl+L` konsolu temizle,
  çift tıklama profili tarayıcıda açar, `F1` yardım.
- **Sonuç listesi:** `✔` doğrulanan, `◌` aday (AI olasılığı ile),
  `🔒` platform duvarı (anonim doğrulanamadı) olarak gösterilir.
- **AI Karar Analizi** paneli: karar dayanağı, özellik katkıları, kalibrasyon
  ve duvar uyarısı.

## 🖥️ CLI

```bash
python cli.py scan "Ahmet Yılmaz" --photo portre.jpg \
       --json out.json --md out.md --html out.html
python cli.py history --limit 10
python cli.py stats
```

## ✅ Testler

```bash
python -m unittest discover -s tests -t . -v     # 97 test
```

Kapsam notları: arama motoru link çözme, kullanıcı adı varyasyonları ve
sınıflandırma, platform duvarı davranışı (`tests/test_wall_and_platforms.py`),
fotoğraf adli tıp ve altyapı.
