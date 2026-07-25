"""Simple 4-language i18n with a JSON-backed per-user language store.

Languages: uz (default), en, ru, tr. Each user's choice is persisted so it
survives restarts (best-effort; the file is ephemeral on Render free tier).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

DEFAULT_LANG = "uz"
LANGS = ("uz", "en", "ru", "tr")
LANG_NAMES = {"uz": "🇺🇿 O'zbekcha", "en": "🇬🇧 English", "ru": "🇷🇺 Русский", "tr": "🇹🇷 Türkçe"}

_STORE_FILE = Path("userlang.json")
_lock = threading.Lock()
_data: dict[str, str] = {}
_loaded = False


def _load() -> None:
    global _data, _loaded
    if _loaded:
        return
    try:
        _data = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
        if not isinstance(_data, dict):
            _data = {}
    except Exception:
        _data = {}
    _loaded = True


def _save() -> None:
    try:
        _STORE_FILE.write_text(json.dumps(_data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def get_lang(user_id: int) -> str:
    with _lock:
        _load()
        return _data.get(str(user_id), DEFAULT_LANG)


def set_lang(user_id: int, lang: str) -> None:
    if lang not in LANGS:
        return
    with _lock:
        _load()
        _data[str(user_id)] = lang
        _save()


def has_lang(user_id: int) -> bool:
    with _lock:
        _load()
        return str(user_id) in _data


def t(user_id: int, key: str, **fmt) -> str:
    lang = get_lang(user_id)
    text = TEXTS.get(lang, TEXTS[DEFAULT_LANG]).get(key)
    if text is None:
        text = TEXTS[DEFAULT_LANG].get(key, key)
    return text.format(**fmt) if fmt else text


TEXTS: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------------- Uzbek
    "uz": {
        "welcome": (
            "👋 <b>Instagram Yuklovchi Bot</b>\n\n"
            "Menga Instagram havolasini yuboring — men mediani yuklab beraman.\n\n"
            "<b>Qo'llab-quvvatlanadi:</b>\n"
            "• Reels va videolar\n"
            "• Postlar (rasm / video / karusel)\n"
            "• IGTV\n"
            "• Stories — <code>@username stories</code>\n"
            "• Highlights — <code>@username highlights</code>\n"
            "• HD profil rasmi — <code>@username pfp</code>\n\n"
            "Boshlash uchun havola yuboring. Tilni o'zgartirish: /language"
        ),
        "help": (
            "<b>Qanday foydalanish</b>\n\n"
            "1️⃣ Reel/video:\n<code>https://instagram.com/reel/XXXX/</code>\n\n"
            "2️⃣ Post (rasm/karusel):\n<code>https://instagram.com/p/XXXX/</code>\n\n"
            "3️⃣ Stories:\n<code>@username stories</code>\n\n"
            "4️⃣ HD profil rasmi:\n<code>@username pfp</code>\n\n"
            "5️⃣ Highlights:\n<code>@username highlights</code>\n\n"
            "Tilni o'zgartirish: /language"
        ),
        "unknown": (
            "🤔 Tushunmadim. Instagram havolasini yuboring yoki "
            "<code>@username stories</code> / <code>@username pfp</code>.\nYordam: /help"
        ),
        "needs_login": (
            "🔒 Stories, highlights va profil rasmi uchun botning Instagram login'i kerak, "
            "u hali sozlanmagan."
        ),
        "downloading": "⏳ Yuklanmoqda...",
        "retrying": "⏳ Instagram band — {sec}s dan keyin qayta urinaman...",
        "compressing": "📦 Video katta — Telegram limitiga siqilmoqda...",
        "too_big": "⚠️ {n} ta fayl siqilgandan keyin ham {mb:.0f} MB dan katta — o'tkazib yuborildi.",
        "too_large": (
            "⚠️ Media siqilgandan keyin ham {mb:.0f} MB dan katta — bot yuklay olmaydi."
        ),
        "auth_error": "🔑 Instagram login muammosi. Egasi ma'lumotlarni tekshirishi kerak.",
        "download_error": "❌ Buni yuklab bo'lmadi.\n<i>{err}</i>",
        "unexpected": "💥 Nimadir xato ketdi. Birozdan keyin qayta urinib ko'ring.",
        "highlight_no_user": (
            "Highlight havolasida username yo'q. "
            "<code>@username highlights</code> deb yuboring."
        ),
        "lang_choose": "🌐 Tilni tanlang:",
        "lang_set": "✅ Til o'zbekchaga o'zgartirildi.",
    },
    # ---------------------------------------------------------------- English
    "en": {
        "welcome": (
            "👋 <b>Instagram Downloader Bot</b>\n\n"
            "Send me an Instagram link and I'll fetch the media for you.\n\n"
            "<b>Supported:</b>\n"
            "• Reels & videos\n"
            "• Posts (photo / video / carousel)\n"
            "• IGTV\n"
            "• Stories — <code>@username stories</code>\n"
            "• Highlights — <code>@username highlights</code>\n"
            "• HD profile picture — <code>@username pfp</code>\n\n"
            "Just paste a link to begin. Change language: /language"
        ),
        "help": (
            "<b>How to use</b>\n\n"
            "1️⃣ Reel/video:\n<code>https://instagram.com/reel/XXXX/</code>\n\n"
            "2️⃣ Post (photo/carousel):\n<code>https://instagram.com/p/XXXX/</code>\n\n"
            "3️⃣ Stories:\n<code>@username stories</code>\n\n"
            "4️⃣ HD profile picture:\n<code>@username pfp</code>\n\n"
            "5️⃣ Highlights:\n<code>@username highlights</code>\n\n"
            "Change language: /language"
        ),
        "unknown": (
            "🤔 I couldn't recognize that. Send an Instagram link or "
            "<code>@username stories</code> / <code>@username pfp</code>.\nHelp: /help"
        ),
        "needs_login": (
            "🔒 Stories, highlights and profile pictures need the bot's Instagram login, "
            "which isn't configured yet."
        ),
        "downloading": "⏳ Downloading...",
        "retrying": "⏳ Instagram is busy — retrying in {sec}s...",
        "compressing": "📦 Video is large — compressing to fit Telegram's limit...",
        "too_big": "⚠️ Skipped {n} file(s) still over {mb:.0f} MB after compression.",
        "too_large": (
            "⚠️ The media is larger than {mb:.0f} MB even after compression — can't upload it."
        ),
        "auth_error": "🔑 Instagram login problem. The owner should re-check credentials.",
        "download_error": "❌ Couldn't download that.\n<i>{err}</i>",
        "unexpected": "💥 Something went wrong. Please try again later.",
        "highlight_no_user": (
            "Highlight links don't include the username. "
            "Send <code>@username highlights</code> instead."
        ),
        "lang_choose": "🌐 Choose your language:",
        "lang_set": "✅ Language changed to English.",
    },
    # ---------------------------------------------------------------- Russian
    "ru": {
        "welcome": (
            "👋 <b>Бот для скачивания из Instagram</b>\n\n"
            "Отправьте ссылку Instagram — я скачаю медиа.\n\n"
            "<b>Поддерживается:</b>\n"
            "• Reels и видео\n"
            "• Посты (фото / видео / карусель)\n"
            "• IGTV\n"
            "• Истории — <code>@username stories</code>\n"
            "• Highlights — <code>@username highlights</code>\n"
            "• Фото профиля HD — <code>@username pfp</code>\n\n"
            "Просто вставьте ссылку. Сменить язык: /language"
        ),
        "help": (
            "<b>Как пользоваться</b>\n\n"
            "1️⃣ Reel/видео:\n<code>https://instagram.com/reel/XXXX/</code>\n\n"
            "2️⃣ Пост (фото/карусель):\n<code>https://instagram.com/p/XXXX/</code>\n\n"
            "3️⃣ Истории:\n<code>@username stories</code>\n\n"
            "4️⃣ Фото профиля HD:\n<code>@username pfp</code>\n\n"
            "5️⃣ Highlights:\n<code>@username highlights</code>\n\n"
            "Сменить язык: /language"
        ),
        "unknown": (
            "🤔 Не понял. Отправьте ссылку Instagram или "
            "<code>@username stories</code> / <code>@username pfp</code>.\nПомощь: /help"
        ),
        "needs_login": (
            "🔒 Для историй, highlights и фото профиля нужен вход бота в Instagram, "
            "который ещё не настроен."
        ),
        "downloading": "⏳ Загрузка...",
        "retrying": "⏳ Instagram занят — повтор через {sec}с...",
        "compressing": "📦 Видео большое — сжимаю под лимит Telegram...",
        "too_big": "⚠️ Пропущено файлов: {n} — больше {mb:.0f} МБ даже после сжатия.",
        "too_large": (
            "⚠️ Медиа больше {mb:.0f} МБ даже после сжатия — бот не может загрузить."
        ),
        "auth_error": "🔑 Проблема входа в Instagram. Владельцу нужно проверить данные.",
        "download_error": "❌ Не удалось скачать.\n<i>{err}</i>",
        "unexpected": "💥 Что-то пошло не так. Попробуйте позже.",
        "highlight_no_user": (
            "В ссылке на highlight нет имени пользователя. "
            "Отправьте <code>@username highlights</code>."
        ),
        "lang_choose": "🌐 Выберите язык:",
        "lang_set": "✅ Язык изменён на русский.",
    },
    # ---------------------------------------------------------------- Turkish
    "tr": {
        "welcome": (
            "👋 <b>Instagram İndirme Botu</b>\n\n"
            "Bana bir Instagram bağlantısı gönder, medyayı indireyim.\n\n"
            "<b>Desteklenenler:</b>\n"
            "• Reels ve videolar\n"
            "• Gönderiler (fotoğraf / video / karusel)\n"
            "• IGTV\n"
            "• Hikayeler — <code>@username stories</code>\n"
            "• Öne çıkanlar — <code>@username highlights</code>\n"
            "• HD profil fotoğrafı — <code>@username pfp</code>\n\n"
            "Başlamak için bağlantı gönder. Dili değiştir: /language"
        ),
        "help": (
            "<b>Nasıl kullanılır</b>\n\n"
            "1️⃣ Reel/video:\n<code>https://instagram.com/reel/XXXX/</code>\n\n"
            "2️⃣ Gönderi (fotoğraf/karusel):\n<code>https://instagram.com/p/XXXX/</code>\n\n"
            "3️⃣ Hikayeler:\n<code>@username stories</code>\n\n"
            "4️⃣ HD profil fotoğrafı:\n<code>@username pfp</code>\n\n"
            "5️⃣ Öne çıkanlar:\n<code>@username highlights</code>\n\n"
            "Dili değiştir: /language"
        ),
        "unknown": (
            "🤔 Anlayamadım. Bir Instagram bağlantısı gönder ya da "
            "<code>@username stories</code> / <code>@username pfp</code>.\nYardım: /help"
        ),
        "needs_login": (
            "🔒 Hikayeler, öne çıkanlar ve profil fotoğrafı için botun Instagram girişi gerekir, "
            "henüz yapılandırılmadı."
        ),
        "downloading": "⏳ İndiriliyor...",
        "retrying": "⏳ Instagram meşgul — {sec}s sonra tekrar denenecek...",
        "compressing": "📦 Video büyük — Telegram limitine sığdırmak için sıkıştırılıyor...",
        "too_big": "⚠️ {n} dosya sıkıştırmadan sonra bile {mb:.0f} MB üstünde — atlandı.",
        "too_large": (
            "⚠️ Medya sıkıştırmadan sonra bile {mb:.0f} MB'den büyük — yüklenemiyor."
        ),
        "auth_error": "🔑 Instagram giriş sorunu. Sahibinin bilgileri kontrol etmesi gerekir.",
        "download_error": "❌ Bu indirilemedi.\n<i>{err}</i>",
        "unexpected": "💥 Bir şeyler ters gitti. Lütfen sonra tekrar deneyin.",
        "highlight_no_user": (
            "Highlight bağlantısında kullanıcı adı yok. "
            "<code>@username highlights</code> gönder."
        ),
        "lang_choose": "🌐 Dilini seç:",
        "lang_set": "✅ Dil Türkçe olarak değiştirildi.",
    },
}
