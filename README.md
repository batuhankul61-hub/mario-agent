# Mario: sesle komut alan kişisel asistan

Mario, Windows bilgisayarında çalışan, Jarvis tarzı bir sesli ajandır. Tarayıcıda (masaüstü ya da telefon PWA'sı) konuşursun, **Gemini Live** (`gemini-3.8-live`) dinler ve function calling ile bir araç seçer. Aracı bilgisayardaki **FastAPI** sunucusu gerçekten çalıştırır, Mario da sonucu sesli söyler.

| Komut | Örnek | Ne yapar |
|---|---|---|
| `open_program` | "Not defterini aç", "Flo Desk'i aç" | Önce kısayol klasöründe, sonra Başlat menüsünde arar ve açar |
| `take_screenshot` | "Chrome'un ekran görüntüsünü al" | Ekranı ya da bir pencereyi `Resimler\Screenshots` klasörüne kaydeder; görüntü sohbette (telefonda da) görünür |
| `lock_computer` | "Ekranı kilitle" | Oturumu kilitler |
| `shutdown_computer` 🔒 | "Bilgisayarı kapat / yeniden başlat" | **Sesli onaydan sonra** 30 sn gecikmeyle kapatır |
| `cancel_shutdown` | "Kapatmayı iptal et" | `shutdown /a` |
| `get_weather` | "İzmir'de hava nasıl?" | Open-Meteo API'si (anahtar gerekmez) |
| `get_time` | "Saat kaç?" | Bilgisayarın saati |
| Google Search | "Dolar kaç TL?" | Gemini'nin yerleşik arama aracı |
| `delegate_to_claude` 🔒 | "Claude'a README'yi güncellet" | **Sesli onaydan sonra** görevi arka planda `claude -p`'ye verir; bitince Mario haber verir |
| `check_claude_task` | "Claude'un işi bitti mi?" | Görevin durumunu ve sonucunu okur |

🔒 işaretli araçlar onay ister.

## Mimari

```
 Telefon / Chrome (PWA)                      Bu bilgisayar                         Google
┌────────────────────────┐  POST /api/token  ┌─────────────────────────┐  ephemeral   ┌──────────────┐
│ mic 16 kHz ─┐          │ ────────────────▶ │ FastAPI                 │ ───token───▶ │ Gemini API   │
│ hoparlör 24 kHz ◀┐     │                   │  • token (prompt+araçlar│              └──────────────┘
│             │    │     │                   │    token'a kilitli)     │
│     Gemini Live WebSocket (ham)  ◀────────────────────────────────────────────────▶ gemini-3.8-live
│             toolCall ──┼─ POST /api/tool ─▶│  • araç kaydı + onay    │
│      toolResponse ◀────┼───────────────────│  • audit.jsonl          │──▶ Windows / Open-Meteo / claude -p
└────────────────────────┘                   └─────────────────────────┘
```

* **API anahtarı tarayıcıya hiç gitmez.** Sunucu tek kullanımlık, kısa ömürlü bir ephemeral token verir. Sistem talimatı ve **araç listesi** bu token'a `lock_additional_fields=[]` ile kilitlenir, yani tarayıcı yeni bir komut ekleyemez.
* Ses altyapısı (ham WebSocket istemcisi, 16 kHz mikrofon / 24 kHz oynatma AudioWorklet'leri, araya girince kuyruğu boşaltma, kendi sesini duyma filtresi) **Lucia** projesinden kopyalanıp uyarlandı. `live.js`'e `toolCall` / `toolResponse` desteği eklendi.
* Araçlar tek bir kayıtta (`backend/app/tools/__init__.py`) tanımlı. Aynı tanım hem Gemini function declaration'larını hem de `/api/tool` dağıtıcısını besler.
* Windows komutları kullanıcının JARVIS eklentilerinden uyarlandı: `program-ac.ps1` (ad sadeleştirme ve eşleştirme), `ekran-goruntusu.ps1` (aynen, `backend/scripts/screenshot.ps1`), `bilgisayar-kapat.ps1` (30 sn gecikme) ve `claude_isci.py` (Claude Code'a iş devretme, yasaklı komut listesi, giriş hatası mesajı).

## Model kendi işini onaylayamaz

Yıkıcı bir araç çağrıldığında hiçbir şey çalışmaz. Sunucu işi bekletir ve `needs_confirmation` ile bir `confirmation_id` döner. Mario soruyu sesli sorar. İş, ancak iki koşul birlikte sağlanınca çalışır:

1. Model `confirm_action(confirmation_id)` çağırır, **ve**
2. Tarayıcının gönderdiği, soru sorulduktan **sonra kullanıcının söylediği** sözlerin (Gemini'nin input transkripsiyonu, yankı filtresinden geçmiş hali) içinde açık bir "evet" vardır ve "hayır / vazgeç / yapma / değil" yoktur.

Böylece model kullanıcıdan duymadığı bir "evet"i varsayarsa `not_confirmed` alır ve iş yapılmaz. Onay tek kullanımlıktır ve 90 sn sonra düşer. Ekranda ayrıca **Onayla / İptal** düğmeleri çıkar; düğme modeli hiç devreye sokmadan doğrudan çalışır. Her çağrı, kimin onayladığıyla birlikte `backend/data/audit.jsonl` dosyasına yazılır (`GET /api/audit`). Böylece "gerçekten çalıştı mı?" sorusunun cevabı modelin söylediğine bağlı kalmaz.

## Kurulum

Gereken: Windows, Python 3.12, Chrome/Edge. Claude'a iş devretmek için Claude Code kurulu ve giriş yapılmış olmalı.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env      # GEMINI_API_KEY'i doldur
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

http://localhost:8100 adresini aç, **Başlat**'a bas ve konuş. Mikrofon yoksa komutları yazarak da verebilirsin.

Kendi programlarının kısayollarını `programs/` klasörüne koy ya da `.env` içinde `PROGRAMS_DIR` ile başka bir klasörü göster (ör. JARVIS'in `Programlar` klasörü).

### Telefondan kullanım

Mikrofon için HTTPS gerekir. En kolay yolu Tailscale:

```powershell
tailscale serve --bg 8100
```

1. `.env` içinde uzun bir `ACCESS_CODE` belirle. Bu kod yoksa bilgisayar dışından gelen her istek reddedilir.
2. Telefonda `https://<bilgisayar-adı>.<tailnet>.ts.net/#code=<ACCESS_CODE>` adresini aç (kod bir kez kaydedilir), sonra "Ana ekrana ekle"ye bas.

`tailscale serve` istekleri 127.0.0.1'den iletse de eklediği `X-Forwarded-For` ve `Tailscale-User-Login` başlıkları sayesinde bu istekler "yerel" sayılmaz; erişim kodu yine istenir.

## Test

```powershell
cd backend
.venv\Scripts\python -m pytest
```

Testler Windows'a ve ağa dokunmaz: `subprocess`, `os.startfile`, Open-Meteo ve Claude Code sahte nesnelerle değiştirilir. Kapsanan konular: onay akışı (model kendi başına onaylayamaz, onay tek kullanımlıktır, süresi dolar, düğmeyle onay), erişim kodu ve Tailscale başlıkları, token'a kilitlenen araç listesi, program bulma, ekran görüntüsü çıktısını okuma, kapatma, hava durumu, Claude işleri (görev metni argv yerine stdin'den gider, zaman aşımı, giriş yapılmamış hesap).

Tarayıcı olmadan gerçek Gemini Live ile uçtan uca deneme (araçlar **gerçekten** çalışır):

```powershell
.venv\Scripts\python scripts\live_smoke.py "İstanbul'da hava nasıl?" "Not defterini aç."
.venv\Scripts\python scripts\live_smoke.py "Bilgisayarı kapat" "hayır"
```

Yıkıcı araçları gerçekten çalıştırmadan denemek için `.env` içinde `DRY_RUN=true` yap.

## Yapı

```
backend/
  app/
    gemini.py         ephemeral token: sistem talimatı + araçlar kilitli
    tools/            araç kaydı, Windows komutları, web/API, Claude Code işleri
    confirm.py        bekleyen işler + "evet" kontrolü
    audit.py          audit.jsonl
    routers/          /api/token, /api/tool, /api/confirm, /api/jobs, /api/audit, /api/screenshots
  scripts/            screenshot.ps1, live_smoke.py
  tests/
frontend/             PWA: live.js, audio/ (Lucia'dan), transcript.js, session.js, app.js
programs/             open_program'ın önce baktığı kısayollar
```
