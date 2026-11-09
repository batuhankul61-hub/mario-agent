# Kuzey Lojistik: sevkiyat paneli projesi

Müşteri Kuzey Lojistik A.Ş. Proje, depo ve sevkiyat durumlarını tek ekranda gösteren bir web paneli.
Müşteri tarafındaki iletişim kişisi Mert Demir (operasyon müdürü).

## Kapsam ve teknik seçimler

Arka uç FastAPI, ön yüz React. Veriler müşterinin mevcut PostgreSQL veritabanından salt okunur bir kullanıcıyla okunacak.
Kimlik doğrulama müşterinin Microsoft hesabıyla (Entra ID) yapılacak. Mobil uygulama kapsam dışı.

## Takvim ve bütçe

Başlangıç toplantısı 4 Kasım 2026'da yapıldı. Teslim tarihi 15 Ocak 2027. Bütçe 180.000 TL + KDV;
%40'ı başlangıçta, kalanı teslimde ödenecek.

Haftalık durum toplantısı her perşembe saat 14:00'te, Teams üzerinden.

## Riskler

En büyük risk, müşterinin veritabanında sevkiyat durumlarının tutarsız olması. Mert Bey, veri temizliğinin
Aralık ortasına kadar biteceğini söyledi. İkinci risk, Entra ID erişiminin geç açılması.
