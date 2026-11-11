"""The commands Mario can run. Each tool is declared once here: the same entry
feeds the Gemini function declarations (locked into the Live token) and the
/api/tool dispatcher, so the model can't call anything the server doesn't know."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import Settings
from . import claude, gcalendar, gmail, knowledge, pc, remind, screen, skills, system, web, wol

if TYPE_CHECKING:
    from ..agents import MemoryResearcher
    from ..memory import Memory
    from ..reminders import Reminders

Result = dict[str, Any]


@dataclass
class Context:
    settings: Settings
    jobs: claude.ClaudeJobs
    # Built on first use by tools/knowledge.py; tests pass ones with fake models.
    memory: "Memory | None" = None
    researcher: "MemoryResearcher | None" = None
    reminders: "Reminders | None" = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    run: Callable[[dict, Context], Awaitable[Result]] | None
    properties: dict = field(default_factory=dict)
    required: tuple[str, ...] = ()
    # Destructive tools only run after the user confirms by voice. Returns the
    # question to ask, or None when these particular args are harmless.
    confirm: Callable[[dict], str | None] | None = None
    # Same, for questions that depend on server state (a learned skill's manifest).
    confirm_ctx: Callable[[dict, "Context"], str | None] | None = None

    def question(self, args: dict, ctx: "Context") -> str | None:
        if self.confirm_ctx:
            return self.confirm_ctx(args, ctx)
        return self.confirm(args) if self.confirm else None

    def declaration(self) -> dict:
        decl: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.properties:
            decl["parameters"] = {
                "type": "OBJECT",
                "properties": self.properties,
                "required": list(self.required),
            }
        return decl


def _str(description: str, enum: list[str] | None = None) -> dict:
    return {"type": "STRING", "description": description, **({"enum": enum} if enum else {})}


def _int(description: str) -> dict:
    return {"type": "INTEGER", "description": description}


def _bool(description: str) -> dict:
    return {"type": "BOOLEAN", "description": description}


def _with_settings(fn):
    return lambda a, c: fn(a, c.settings)


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "open_program",
            "Bilgisayarda bir programı ya da uygulamayı adıyla açar (Chrome, Not Defteri, Flo Desk...).",
            lambda a, c: system.open_program(a.get("name", ""), c.settings),
            {"name": _str("Programın kullanıcının söylediği adı.")},
            ("name",),
        ),
        Tool(
            "take_screenshot",
            "Ekranın ya da adı verilen bir pencerenin ekran görüntüsünü alır ve kaydeder.",
            lambda a, c: system.take_screenshot(a.get("target", ""), c.settings),
            {
                "target": _str(
                    "Boş bırakılırsa tüm ekran; yoksa pencere ya da program adı, ör. 'chrome'."
                )
            },
        ),
        Tool(
            "lock_computer",
            "Bilgisayarın ekranını kilitler (oturum açık kalır).",
            lambda a, c: system.lock_computer(c.settings),
        ),
        Tool(
            "shutdown_computer",
            "Bilgisayarı 30 saniye sonra kapatır ya da yeniden başlatır. Sesli onay gerekir.",
            lambda a, c: system.shutdown(a.get("mode", "shutdown"), c.settings),
            {"mode": _str("Kapatma mı yeniden başlatma mı.", ["shutdown", "restart"])},
            ("mode",),
            confirm=lambda a: (
                "Bilgisayar yeniden başlatılsın mı?"
                if a.get("mode") == "restart"
                else "Bilgisayar kapatılsın mı?"
            ),
        ),
        Tool(
            "cancel_shutdown",
            "Zamanlanmış bir kapatma ya da yeniden başlatmayı iptal eder.",
            lambda a, c: system.cancel_shutdown(c.settings),
        ),
        Tool(
            "get_weather",
            "Bir şehrin şu anki hava durumunu ve bugünün en düşük/en yüksek sıcaklığını verir.",
            lambda a, c: web.get_weather(a.get("city", "")),
            {"city": _str("Şehir adı, ör. 'İstanbul'.")},
            ("city",),
        ),
        Tool(
            "get_time",
            "Bu bilgisayardaki şu anki tarih ve saati verir.",
            lambda a, c: web.get_time(),
        ),
        # ---------- reminders, calendar ----------
        Tool(
            "set_reminder",
            "Hatırlatma ya da zamanlayıcı kurar ('10 dakika sonra çayı hatırlat', 'yarın 9'da kirayı hatırlat', "
            "'5 dakikalık zamanlayıcı kur'). Zamanı geldiğinde Mario söyler ve bildirim çıkar. in_minutes ya da "
            "at'ten birini ver.",
            remind.set_reminder,
            {
                "text": _str("Hatırlatılacak şey, kısa ve anlaşılır."),
                "in_minutes": _int("Şimdiden kaç dakika sonra."),
                "at": _str(
                    "Yerel saat: bugün/yarın için 'SS:DD' (geçmişte kalırsa yarın), başka gün için "
                    "'YYYY-AA-GGTSS:DD'. Tarihi bilmiyorsan önce get_time çağır."
                ),
                "kind": _str("Hatırlatma mı zamanlayıcı mı.", ["reminder", "timer"]),
            },
        ),
        Tool(
            "list_reminders",
            "Bekleyen hatırlatmaları ve zamanlayıcıları numaralarıyla listeler.",
            remind.list_reminders,
        ),
        Tool(
            "cancel_reminder",
            "Bekleyen bir hatırlatmayı ya da zamanlayıcıyı iptal eder.",
            remind.cancel_reminder,
            {"id": _int("Hatırlatmanın numarası (list_reminders'tan).")},
            ("id",),
        ),
        Tool(
            "list_calendar_events",
            "Google Takvim'deki etkinlikleri okur ('bugün neyim var?', 'bu hafta toplantım var mı?').",
            lambda a, c: gcalendar.list_events(a, c.settings),
            {"days": _int("Bugünden başlayarak kaç gün (1-14). Varsayılan 1, yani bugün.")},
        ),
        Tool(
            "delegate_to_claude",
            "Araçlarınla yapılamayan bir bilgisayar ya da kod işini Claude Code'a arka planda verir. "
            "Görev bitince sonuç bir sistem notuyla gelir. Sesli onay gerekir.",
            lambda a, c: c.jobs.start(a.get("task", "")),
            {"task": _str("Kullanıcının isteğinden yazılmış eksiksiz Türkçe görev metni.")},
            ("task",),
            confirm=lambda a: f"Bu görevi Claude'a vereyim mi: {a.get('task', '')}",
        ),
        Tool(
            "check_claude_task",
            "Claude'a verilen görevin durumunu ve sonucunu okur. job_id verilmezse son görev.",
            lambda a, c: c.jobs.status(a.get("job_id") or None),
            {"job_id": _str("İsteğe bağlı görev numarası.")},
        ),
        # ---------- screen ----------
        Tool(
            "read_screen",
            "Öndeki (ya da adı verilen) penceredeki düğmeleri, metin alanlarını ve bağlantıları numaralı listeler. "
            "Ekranda bir şeye tıklamadan ya da yazmadan önce çağır.",
            _with_settings(screen.read_screen),
            {"window": _str("İsteğe bağlı pencere ya da program adı; boşsa en öndeki pencere.")},
        ),
        Tool(
            "click_element",
            "read_screen listesindeki bir öğeye tıklar. Gönder/sil/öde gibi düğmeler sesli onay ister.",
            _with_settings(screen.click_element),
            {"element": _str("Öğenin listedeki numarası ya da adı.")},
            ("element",),
            confirm_ctx=lambda a, c: screen.click_question(a, c.settings),
        ),
        Tool(
            "type_text",
            "Öndeki pencereye ya da listedeki bir metin alanına yazı yazar.",
            _with_settings(screen.type_text),
            {
                "text": _str("Yazılacak metin."),
                "element": _str("İsteğe bağlı: yazılacak alanın numarası ya da adı."),
                "enter": _bool("true: yazdıktan sonra Enter'a bas."),
            },
            ("text",),
        ),
        Tool(
            "press_keys",
            "Öndeki pencerede tuşlara basar, ör. 'ctrl+s', 'enter', 'alt+tab', 'ctrl+l'; birkaçı boşlukla ayrılır. "
            "Pencere kapatan kısayollar sesli onay ister.",
            _with_settings(screen.press_keys),
            {"keys": _str("Tuşlar."), "window": _str("İsteğe bağlı pencere adı.")},
            ("keys",),
            confirm=screen.keys_question,
        ),
        Tool(
            "screen_task",
            "Birkaç adım süren ya da ekranı görerek karar vermeyi gerektiren bir işi Claude'a verir; Claude ekrana "
            "bakıp tıklayarak yapar. Sesli onay gerekir.",
            lambda a, c: c.jobs.screen(a.get("task", "")),
            {"task": _str("Kullanıcının isteğinden yazılmış eksiksiz Türkçe görev metni.")},
            ("task",),
            confirm=lambda a: f"Bu ekran görevini Claude'a vereyim mi: {a.get('task', '')}",
        ),
        # ---------- learning ----------
        Tool(
            "learn_skill",
            "Hiçbir aracın ya da öğrenilmiş yeteneğin karşılamadığı bir istek için çağır: Claude Code'un bu işi "
            "kalıcı bir yetenek olarak öğretmesini başlatır. Önce kullanıcıdan izin ister.",
            lambda a, c: c.jobs.learn(a.get("request", "")),
            {"request": _str("Kullanıcının isteği, kendi sözlerine yakın ve eksiksiz.")},
            ("request",),
            confirm=lambda a: (
                f"Bunu henüz bilmiyorum. İzin verirsen Claude sayesinde öğreneyim: "
                f"«{a.get('request', '').strip().rstrip('.!?')}». "
                "Öğreneyim mi?"
            ),
        ),
        Tool(
            "run_skill",
            "Öğrenilmiş bir yeteneği çalıştırır. Yetenekler ve parametreleri sistem talimatında ya da "
            "list_skills sonucunda yazar.",
            _with_settings(skills.run_skill),
            {
                "name": _str("Yeteneğin adı."),
                "args_json": _str(
                    'Parametreler, JSON nesnesi olarak, ör. {"kisi": "Ahmet", "mesaj": "Geliyorum"}.'
                ),
            },
            ("name",),
            confirm_ctx=lambda a, c: skills.skill_question(a, c.settings),
        ),
        Tool(
            "list_skills",
            "Claude sayesinde öğrenilmiş yetenekleri listeler.",
            _with_settings(skills.list_skills),
        ),
        Tool(
            "forget_skill",
            "Öğrenilmiş bir yeteneği unutur (klasörü skills/_unutulan altına taşınır). Sesli onay gerekir.",
            _with_settings(skills.forget_skill),
            {"name": _str("Yeteneğin adı.")},
            ("name",),
            confirm=lambda a: f"{a.get('name', '')} yeteneğini unutayım mı?",
        ),
        # ---------- PC settings ----------
        Tool(
            "get_pc_status",
            "Ses seviyesi ve çıkış cihazı, mikrofon, parlaklık, tema, bildirimler, Wi-Fi/Bluetooth, ağ, güç planı, "
            "işlemci/bellek ve pil durumunu tek seferde okur.",
            _with_settings(pc.get_pc_status),
        ),
        Tool(
            "set_volume",
            "Ses seviyesini ayarlar, artırır/azaltır ya da sesi kapatır/açar.",
            _with_settings(pc.set_volume),
            {
                "level": _int("0-100 arası hedef seviye."),
                "change": _str("10 puan artır ya da azalt.", ["up", "down"]),
                "mute": _bool("true: sesi kapat, false: sesi aç."),
            },
        ),
        Tool(
            "set_microphone",
            "Bilgisayarın varsayılan mikrofonunu tüm uygulamalar için kapatır (sessize alır) ya da açar. "
            "Kapatmak sesli onay ister.",
            _with_settings(pc.set_microphone),
            {"mute": _bool("true: mikrofonu kapat, false: mikrofonu aç.")},
            ("mute",),
            confirm=lambda a: (
                "Mikrofon kapatılsın mı? Bu bilgisayardan konuşuyorsan seni duyamam, açmak için yazman gerekir."
                if a.get("mute", True)
                else None
            ),
        ),
        Tool(
            "set_audio_output",
            "Sesin çıktığı cihazı değiştirir (ör. kulaklık, hoparlör, monitör).",
            _with_settings(pc.set_audio_output),
            {"device": _str("Cihaz adı ya da bir parçası, ör. 'kulaklık', 'VG259'.")},
            ("device",),
        ),
        Tool(
            "set_brightness",
            "Ekran parlaklığını ayarlar ya da 10 puan artırır/azaltır (tüm monitörler).",
            _with_settings(pc.set_brightness),
            {
                "level": _int("0-100 arası hedef seviye."),
                "change": _str("Artır ya da azalt.", ["up", "down"]),
            },
        ),
        Tool(
            "set_theme",
            "Windows'u koyu ya da açık temaya geçirir.",
            _with_settings(pc.set_theme),
            {"mode": _str("Tema.", ["dark", "light"])},
            ("mode",),
        ),
        Tool(
            "set_do_not_disturb",
            "Rahatsız etme: bildirim balonlarını ve seslerini kapatır ya da yeniden açar.",
            _with_settings(pc.set_do_not_disturb),
            {"on": _bool("true: rahatsız etme açık (bildirimler sessiz), false: kapalı.")},
            ("on",),
        ),
        Tool(
            "open_settings",
            "Windows Ayarlar'da bir sayfayı açar. Gece ışığı gibi doğrudan değiştirilemeyen ayarlar için kullan.",
            _with_settings(pc.open_settings),
            {"page": _str("Sayfa.", list(pc.SETTINGS_PAGES))},
            ("page",),
        ),
        Tool(
            "set_radio",
            "Wi-Fi ya da Bluetooth'u açar veya kapatır. Wi-Fi'yi kapatmak sesli onay ister.",
            _with_settings(pc.set_radio),
            {
                "radio": _str("Hangisi.", ["wifi", "bluetooth"]),
                "on": _bool("true: aç, false: kapat."),
            },
            ("radio", "on"),
            confirm=lambda a: (
                "Wi-Fi kapatılsın mı? Telefondan bağlıysan bağlantı kopabilir."
                if a.get("radio") == "wifi" and not a.get("on")
                else None
            ),
        ),
        Tool(
            "set_power_plan",
            "Güç planını değiştirir (Dengeli, Yüksek performans, Güç tasarrufu...).",
            _with_settings(pc.set_power_plan),
            {"plan": _str("Plan adı.")},
            ("plan",),
        ),
        Tool(
            "sleep_computer",
            "Bilgisayarı uyku moduna alır. Sesli onay gerekir.",
            _with_settings(pc.sleep_computer),
            confirm=lambda a: "Bilgisayar uyku moduna alınsın mı?",
        ),
        Tool(
            "list_programs",
            "Açık pencereli programları listeler.",
            _with_settings(pc.list_programs),
        ),
        Tool(
            "close_program",
            "Açık bir programı kapatır (kaydetmeyi sorabilmesi için normal kapatma isteğiyle). Sesli onay gerekir.",
            _with_settings(pc.close_program),
            {"name": _str("Program ya da pencere adı.")},
            ("name",),
            confirm=lambda a: (
                f"{a.get('name', '')} kapatılsın mı? Kaydedilmemiş işler kaybolabilir."
            ),
        ),
        Tool(
            "scan_network",
            "Yerel ağı tarayıp açık bilgisayar ve cihazları numaralı listeler (ad, IP). Kullanıcı ağdaki "
            "cihazları sorduğunda ya da uyandırmak istediği cihaz kayıtlı değilse çağır; listeyi okuyup "
            "hangisi olduğunu sor. Kapalı cihazlar listede çıkmaz.",
            _with_settings(wol.scan_network),
        ),
        Tool(
            "save_wake_device",
            "scan_network listesindeki bir cihazı bir adla kaydeder; böylece kapalıyken de wake_device ile "
            "uyandırılabilir.",
            _with_settings(wol.save_wake_device),
            {
                "number": _int("Cihazın scan_network listesindeki numarası."),
                "name": _str("Kullanıcının verdiği ad, ör. 'masaüstü'. Boşsa cihazın ağdaki adı."),
            },
            ("number",),
        ),
        Tool(
            "wake_device",
            "Yerel ağdaki bir bilgisayarı ya da cihazı Wake-on-LAN ile uyandırır/açar ('masaüstünü aç', "
            "'sunucuyu uyandır'). Kayıtlı bir ad ya da son scan_network listesinden bir numara ver; ikisi de "
            "boşsa kayıtlı cihazları sayar.",
            _with_settings(wol.wake_device),
            {
                "name": _str("Kayıtlı cihazın adı, ör. 'masaüstü'."),
                "number": _int("Son scan_network listesindeki numara."),
            },
        ),
        # ---------- Gmail ----------
        Tool(
            "list_emails",
            "Gmail'de e-postaları listeler. Varsayılan: gelen kutusundaki okunmamışlar.",
            _with_settings(gmail.list_emails),
            {
                "query": _str(
                    "Gmail arama ifadesi, ör. 'is:unread in:inbox', 'from:ahmet newer_than:2d'."
                ),
                "max_results": _int("En fazla kaç e-posta (1-10)."),
            },
        ),
        Tool(
            "read_email",
            "Bir e-postanın tamamını okur.",
            _with_settings(gmail.read_email),
            {"id": _str("list_emails'ten gelen e-posta id'si.")},
            ("id",),
        ),
        Tool(
            "find_email_address",
            "Bir kişinin e-posta adresini önceki yazışmalardan bulur.",
            _with_settings(gmail.find_email_address),
            {"name": _str("Kişinin adı.")},
            ("name",),
        ),
        Tool(
            "send_email",
            "Yeni bir e-posta gönderir. Sesli onay gerekir.",
            _with_settings(gmail.send_email),
            {
                "to": _str("Alıcının e-posta adresi."),
                "subject": _str("Konu."),
                "body": _str("Metin."),
            },
            ("to", "subject", "body"),
            confirm=lambda a: (
                f"{a.get('to', '')} adresine '{a.get('subject', '')}' konulu şu e-posta gönderilsin mi: "
                f"{_short(a.get('body', ''))}"
            ),
        ),
        Tool(
            "reply_email",
            "Bir e-postaya, gönderene yanıt verir. Sesli onay gerekir.",
            _with_settings(gmail.reply_email),
            {"id": _str("Yanıtlanacak e-postanın id'si."), "body": _str("Yanıt metni.")},
            ("id", "body"),
            confirm=lambda a: f"Bu e-postaya şu yanıt gönderilsin mi: {_short(a.get('body', ''))}",
        ),
        # ---------- memory (RAG) ----------
        Tool(
            "remember",
            "Kullanıcı 'bunu hatırla', 'not al', 'unutma' dediğinde bilgiyi uzun süreli hafızaya not eder.",
            knowledge.remember,
            {"text": _str("Hatırlanacak bilgi, tek başına anlaşılır eksiksiz bir cümle.")},
            ("text",),
        ),
        Tool(
            "ask_memory",
            "Kullanıcının kendi notları, belgeleri ya da hafızaya alınmış e-postalarıyla ilgili bir soruyu "
            "cevaplar (ör. 'kira ne zaman ödeniyor?', 'arabanın bakımı ne zaman?'). Hafıza araştırmacısı ajan "
            "arar ve kaynaklı kısa bir cevap döner.",
            knowledge.ask_memory,
            {"question": _str("Kullanıcının sorusu, eksiksiz.")},
            ("question",),
        ),
        Tool(
            "recall",
            "Hafızadaki notlarda, belgelerde ve e-postalarda anlamsal arama yapar, en yakın parçaları kaynak "
            "id'leriyle döner. Hızlı bir bakış ya da 'ne not etmiştim?' için.",
            knowledge.recall,
            {"query": _str("Aranacak konu.")},
            ("query",),
        ),
        Tool(
            "search_documents",
            "Yalnızca belge klasöründeki dosyalarda (PDF, Markdown, metin) anlamsal arama yapar.",
            knowledge.search_documents,
            {"query": _str("Aranacak konu.")},
            ("query",),
        ),
        Tool(
            "index_documents",
            "Belge klasöründeki yeni ve değişen dosyaları hafızaya ekler, silinenleri çıkarır.",
            knowledge.index_documents,
        ),
        Tool(
            "index_emails",
            "Gmail'deki e-postaları hafızaya ekler, sonra ask_memory ile sorulabilir. Varsayılan: son 7 günün "
            "gelen kutusu.",
            knowledge.index_emails,
            {
                "query": _str("Gmail arama ifadesi, ör. 'in:inbox newer_than:7d'."),
                "max_results": _int("En fazla kaç e-posta (1-50)."),
            },
        ),
        Tool(
            "forget_memory",
            "Hafızadaki bir notu siler (not-3 gibi bir id). Sesli onay gerekir.",
            knowledge.forget_memory,
            {"id": _str("Notun id'si, ör. 'not-3'.")},
            ("id",),
            confirm_ctx=knowledge.forget_question,
        ),
        Tool(
            "confirm_action",
            "Kullanıcı sorulan onay sorusuna sesli olarak açıkça evet dediğinde, bekleyen işi çalıştırır. "
            "Kullanıcı cevap vermeden çağırma.",
            None,
            {"confirmation_id": _str("Onay isteyen aracın döndürdüğü confirmation_id.")},
            ("confirmation_id",),
        ),
        Tool(
            "cancel_action",
            "Kullanıcı hayır ya da vazgeç dediğinde bekleyen işi iptal eder.",
            None,
            {"confirmation_id": _str("İptal edilecek işin confirmation_id'si.")},
            ("confirmation_id",),
        ),
    ]
}


def declarations() -> list[dict]:
    return [t.declaration() for t in TOOLS.values()]
