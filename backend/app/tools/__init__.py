"""The commands Mario can run. Each tool is declared once here: the same entry
feeds the Gemini function declarations (locked into the Live token) and the
/api/tool dispatcher, so the model can't call anything the server doesn't know."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from . import claude, system, web

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
    # Destructive tools only run after the user confirms by voice.
    confirm: Callable[[dict], str] | None = None

    def declaration(self) -> dict:
        decl: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.properties:
            decl["parameters"] = {"type": "OBJECT", "properties": self.properties, "required": list(self.required)}
        return decl


def _str(description: str, enum: list[str] | None = None) -> dict:
    return {"type": "STRING", "description": description, **({"enum": enum} if enum else {})}


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
