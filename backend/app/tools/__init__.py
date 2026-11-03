"""The commands Mario can run. Each tool is declared once here: the same entry
feeds the Gemini function declarations (locked into the Live token) and the
/api/tool dispatcher, so the model can't call anything the server doesn't know."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from . import claude, gmail, pc, system, web

Result = dict[str, Any]


@dataclass
class Context:
    settings: Settings
    jobs: claude.ClaudeJobs


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

    def declaration(self) -> dict:
        decl: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.properties:
            decl["parameters"] = {"type": "OBJECT", "properties": self.properties, "required": list(self.required)}
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
            {"target": _str("Boş bırakılırsa tüm ekran; yoksa pencere ya da program adı, ör. 'chrome'.")},
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
                "Bilgisayar yeniden başlatılsın mı?" if a.get("mode") == "restart" else "Bilgisayar kapatılsın mı?"
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
        # ---------- PC settings ----------
        Tool(
            "get_pc_status",
            "Ses seviyesi ve çıkış cihazı, parlaklık, tema, bildirimler, Wi-Fi/Bluetooth, ağ, güç planı, "
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
            {"level": _int("0-100 arası hedef seviye."), "change": _str("Artır ya da azalt.", ["up", "down"])},
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
            {"radio": _str("Hangisi.", ["wifi", "bluetooth"]), "on": _bool("true: aç, false: kapat.")},
            ("radio", "on"),
            confirm=lambda a: (
                "Wi-Fi kapatılsın mı? Telefondan bağlıysan bağlantı kopabilir."
                if a.get("radio") == "wifi" and not a.get("on") else None
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
            confirm=lambda a: f"{a.get('name', '')} kapatılsın mı? Kaydedilmemiş işler kaybolabilir.",
        ),
        # ---------- Gmail ----------
        Tool(
            "list_emails",
            "Gmail'de e-postaları listeler. Varsayılan: gelen kutusundaki okunmamışlar.",
            _with_settings(gmail.list_emails),
            {
                "query": _str("Gmail arama ifadesi, ör. 'is:unread in:inbox', 'from:ahmet newer_than:2d'."),
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
            {"to": _str("Alıcının e-posta adresi."), "subject": _str("Konu."), "body": _str("Metin.")},
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
