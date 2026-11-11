---
name: mario-yetenek
description: Mario sesli asistanına yeni bir yetenek öğretir — skills/<ad>/ altına skill.json ve run.ps1 ya da run.py yazar. Mario'nun learn_skill aracı bir istek için "bunu öğren" dediğinde ya da kullanıcı Mario'ya kalıcı yeni bir komut eklemek istediğinde kullan.
---

# Mario'ya yetenek öğretme

Mario, kullanıcının Windows bilgisayarında çalışan sesli asistanı. Hazır araçları (program açma, ekran
görüntüsü, ses/ekran/ağ/güç ayarları, Gmail, hava durumu, Claude'a görev...) bir isteği karşılamadığında
Mario senden o işi **kalıcı bir yetenek** olarak öğretmeni ister. Mario yeteneği `run_skill` aracıyla
çalıştırır; senin işin yeteneği yazmak ve güvenle denemek, kullanıcının isteğini yerine getirmek değil.

## 1. Önce var olana bak

```powershell
backend\.venv\Scripts\python backend\scripts\check_skill.py --list
```

Bu komut Mario'nun hazır araçlarını ve öğrenilmiş yeteneklerini listeler.
- İsteği zaten bir hazır araç ya da yetenek karşılıyorsa yenisini yazma. O yeteneğin adını ver; hazır araçsa
  `YETENEK: yok` de ve SONUÇ'ta hangi aracın kullanılacağını söyle.
- Benzer bir yetenek varsa yenisini açma, onu genişlet (parametre ekle).

## 2. Klasör ve dosyalar

`skills/<ad>/` — ad küçük harf, rakam ve alt çizgi, 3-41 karakter, Türkçe ve anlaşılır (`whatsapp_mesaj`,
`doviz_kuru`, `geri_donusum_bosalt`). Yalnızca bu klasöre yaz; `backend/`, `frontend/` ve `.claude/`
klasörlerine dokunma.

**skill.json**

```json
{
  "name": "whatsapp_mesaj",
  "description": "WhatsApp Desktop'tan bir kişiye mesaj gönderir.",
  "parameters": {
    "kisi": "Mesajın gideceği kişinin WhatsApp'taki adı ya da telefon numarası",
    "mesaj": "Gönderilecek metin"
  },
  "required": ["kisi", "mesaj"],
  "confirm": "{kisi} kişisine WhatsApp'tan şu mesaj gönderilsin mi: {mesaj}",
  "entry": "run.ps1",
  "example": "WhatsApp'tan Ahmet'e akşam geliyorum yaz"
}
```

- `description`: Mario'nun ne zaman kullanacağını anlayacağı tek cümle.
- `parameters`: değişen her şey bir parametre olsun ki yetenek yalnızca bu isteği değil benzerlerini de
  yapsın (başka kişi, başka şehir, başka miktar).
- `confirm`: iş **geri alınamaz ya da dışa dönükse** zorunlu: mesaj/e-posta gönderme, paylaşma, silme,
  satın alma, ödeme, ayar değiştirme, toplu değişiklik, bir hesapta işlem. `{parametre}` yer tutucuları
  kullanıcıya ne olacağını tam söylesin. Zararsız okuma işlerinde `null`.
- `entry`: `run.ps1` ya da `run.py`.

## 3. Betik kuralları

- Argümanlar **stdin'den JSON nesnesi** olarak gelir. Komut satırı argümanı yok.
  - PowerShell: `$a = [Console]::In.ReadToEnd() | ConvertFrom-Json` ve dosyanın başına
    `[Console]::OutputEncoding = [Text.Encoding]::UTF8`.
  - Python: `args = json.load(sys.stdin)`; yalnızca standart kütüphane (`-I` ile çalışır), çıktıdan önce
    `sys.stdout.reconfigure(encoding="utf-8")`.
- `.ps1` dosyasını **UTF-8 BOM ile** kaydet: Windows PowerShell 5.1 BOM'suz dosyadaki Türkçe harfleri bozar.
- Başına ne yaptığını anlatan iki yorum satırı koy.
- Çıktının **son satırı** Mario'nun sesli okuyacağı tek kısa Türkçe cümle olsun. Başarısızlıkta son satır
  `Hata: ...` ile başlasın ve çıkış kodu 1 olsun.
- Mario en fazla 30 saniye bekler. Uzun işleri arka plana at (`Start-Process`) ve hemen dön.
- Kullanıcının verisini kullanıcı adına kendiliğinden göndermesi gereken arayüz işlerinde (ör. WhatsApp
  Desktop) önce resmi yolu dene: URI şemaları (`whatsapp://send?phone=...&text=...`), uygulamanın kendi
  komut satırı, açık API'ler. Tuş/fare otomasyonu son çare olsun ve odaklanmayı doğrula.
- Bir pencereyi öne getirmen gerekiyorsa kendi `SetForegroundWindow` kodunu yazma: Windows, Mario'nun
  sunucusundan gelen bu isteği çoğu zaman reddeder ("pencere öne getirilemedi"). Ortak yardımcıyı kullan:
  `. (Join-Path $env:MARIO_LIB 'window.ps1')`, sonra `Set-MarioForeground -ProcessName 'WhatsApp'` (ya da
  `-Title '...'`). `$true` dönmezse tuşa basma, `Hata:` ile çık. `MARIO_LIB`, yeteneği Mario çalıştırırken
  tanımlıdır; kendin denerken `$env:MARIO_LIB = '<proje>\backend\skill_lib'` ver.
- `.ps1` betiğinde başarı ya da başarısızlığı yalnızca son satır belirler: çıkış kodu, betiğin içinde en son
  çalışan programdan (ör. bulamayan bir `winget list`) kalabilir. Bu yüzden her başarısızlıkta son satır
  mutlaka `Hata:` ile başlasın.
- Şifre, API anahtarı ya da kişisel veri dosyaya yazma. Anahtar gereken bir servis varsa ve bu bilgisayarda
  yoksa yeteneği yazma (bkz. 5).
- Kullanıcının bilgisayarındaki yolları sabit yazma; `$env:APPDATA`, `[Environment]::GetFolderPath(...)`
  gibi yollarla bul.

## 4. Dene, ama zararsızca

```powershell
backend\.venv\Scripts\python backend\scripts\check_skill.py <ad>
```

Bu komut manifesti Mario'nun kullandığı yükleyiciyle doğrular ve betiğin sözdizimini kontrol eder.
Hata verirse düzelt ve yeniden çalıştır.

Ardından betiği yalnızca **zararsız** bir girdiyle dene, ör.
`'{"sehir":"İzmir"}' | powershell -NoProfile -File skills\<ad>\run.ps1`.
- Geri alınamaz ya da dışa dönük hiçbir şeyi gerçekten yapma: mesaj gönderme, silme, satın alma, ayar
  değiştirme. Böyle yeteneklerde yalnızca sözdizimini ve hata yollarını dene (ör. eksik parametre).
- Kullanıcının isteğini kendin yerine getirme; onu Mario, kullanıcı onay verdikten sonra yapacak.

## 5. Öğretilemiyorsa

İş bu bilgisayarda gerçekten yapılamıyorsa (program kurulu değil, giriş/anahtar gerekiyor, donanım yok)
yetenek yazma. `YETENEK: yok` de ve SONUÇ'ta nedenini ve kullanıcının ne yapması gerektiğini tek cümleyle söyle.

## 6. Son mesaj

Yalnızca iki satır:

```
SONUÇ: <Mario'nun sesli söyleyeceği tek kısa Türkçe cümle>
YETENEK: <yeteneğin adı ya da yok>
```
