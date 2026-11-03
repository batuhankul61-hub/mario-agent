"""The only module that talks to Gemini. Routers depend on `GeminiService`,
so tests can swap in a fake without touching the network."""

import datetime as dt

from google import genai
from google.genai import types

from .config import Settings
from .tools import declarations

SYSTEM_INSTRUCTION = """Sen Mario'sun: Batuhan'ın Windows bilgisayarında çalışan, sesle komut alan kişisel asistanı.
Türkçe, kısa ve doğal konuş; çoğu cevap bir iki cümle olsun. Kullanıcı İngilizce konuşursa İngilizce cevap ver.

Araçlar:
- Bir iş istenince uygun aracı hemen çağır. Araç sonucu gelmeden işin yapıldığını asla söyleme.
- Araç "status": "error" ya da "ambiguous" dönerse "message" alanını kendi sözlerinle kısaca aktar.
- Araçlarınla yapılamayan bir iş ya da internette araştırma gerekirse Claude'a görev vermeyi öner.

Onay gerektiren işler (bilgisayarı kapatma/yeniden başlatma, Claude'a görev verme):
- Bu araçlar önce "status": "needs_confirmation", bir "confirmation_id" ve "question" döner. İşi henüz yapmadın.
- "question" alanındaki soruyu kullanıcıya sor ve cevabını bekle.
- Kullanıcı açıkça evet derse confirm_action'ı o confirmation_id ile çağır. Hayır ya da vazgeç derse cancel_action çağır.
- Kullanıcı cevap vermeden confirm_action çağırma. Sunucu, onayı kullanıcının kendi sözlerinden ayrıca kontrol eder;
  "not_confirmed" dönerse soruyu bir kez daha sor.

"(Sistem notu: ...)" ile başlayan mesajlar uygulamadan gelir, kullanıcıdan değil: içindeki bilgiyi kullanıcıya kısaca söyle."""


class GeminiService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def live_config(self) -> types.LiveConnectConfig:
        tools = [types.Tool(function_declarations=declarations())]
        instruction = SYSTEM_INSTRUCTION
        if self.settings.enable_google_search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
            instruction += "\n\nGenel bilgi, haber, fiyat gibi güncel sorular için Google aramasını kullan."
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=instruction,
            tools=tools,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.settings.voice)
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

    async def create_live_token(self) -> types.AuthToken:
        now = dt.datetime.now(tz=dt.timezone.utc)
        return await self._client.aio.auth_tokens.create(
            config=types.CreateAuthTokenConfig(
                uses=1,
                expire_time=now + dt.timedelta(minutes=self.settings.max_session_minutes),
                new_session_expire_time=now + dt.timedelta(minutes=1),
                live_connect_constraints=types.LiveConnectConstraints(
                    model=self.settings.live_model, config=self.live_config()
                ),
                # Empty list = lock exactly the fields set above, tools included,
                # so the browser can't add or change commands.
                lock_additional_fields=[],
            )
        )
