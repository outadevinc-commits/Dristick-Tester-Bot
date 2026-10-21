import asyncio
import html
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)

# ───────────── Настройки (Render → Environment) ─────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
if not BOT_TOKEN or not ADMIN_ID_RAW:
    raise SystemExit("Задай переменные окружения BOT_TOKEN и ADMIN_ID (Render → Environment).")
ADMIN_ID = int(ADMIN_ID_RAW)

SITE_URL = os.getenv("SITE_URL", "https://outadevinc-commits.github.io/Dristick-Tester-Bot/")
DOWNLOAD_URL = os.getenv("DOWNLOAD_URL", "https://drive.google.com")
# Откуда сайту разрешено слать отчёты (через запятую). Только origin, без пути.
ALLOWED_ORIGINS = {
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "https://outadevinc-commits.github.io").split(",")
    if o.strip()
}

# action -> (очки, это баг?, статус для админа, уведомление тестеру)
ACTIONS = {
    "accept": (5, False, "✅ Отчёт принят (+5)", "🎉 Твой отчёт по Dristick Tester принят разработчиком! (+5 очков в /top)"),
    "bug": (10, True, "🐛 Баг зафиксирован (+10)", "🐛 Разработчик зафиксировал твой баг! (+10 очков в /top)"),
    "title": (15, False, "👑 Титул утверждён (+15)", "👑 Твой титул одобрен! (+15 очков в /top)"),
    "reject": (0, False, "❌ Отчёт отклонён", "ℹ️ Твой отчёт был отклонён разработчиком."),
}

# ───────────── Хранилище (в памяти, сбрасывается при рестарте) ─────────────
leaderboard: dict[str, dict] = {}
reports: dict[str, dict] = {}       # report_id -> {"nick", "tg"}
done: set[str] = set()              # уже обработанные отчёты
known_users: dict[str, int] = {}    # username (lower) -> chat_id (кто нажимал /start)
hits: dict[str, deque] = defaultdict(deque)  # антиспам по IP

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ───────────── Хелперы ─────────────
def clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def norm_tg(value) -> str | None:
    value = clean(value, 64).lstrip("@")
    return value if re.fullmatch(r"[A-Za-z0-9_]{3,32}", value) else None


def extract_user_info(text: str):
    """Запасной вариант: достаём ник и @username из текста сообщения."""
    tg = re.search(r"Telegram:\s*@?([A-Za-z0-9_]+)", text, re.IGNORECASE)
    nick = re.search(r"Никнейм:\s*(.+)", text, re.IGNORECASE)
    return (tg.group(1) if tg else None), (nick.group(1).strip() if nick else "Аноним")


def add_points(user_key: str, points: int, is_bug: bool = False):
    row = leaderboard.setdefault(user_key, {"points": 0, "bugs": 0, "reports": 0})
    row["points"] += points
    row["reports"] += 1
    if is_bug:
        row["bugs"] += 1


def report_keyboard(rid: str) -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"rep:{action}:{rid}")

    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("✅ Принято (+5)", "accept"), btn("🐛 Баг (+10)", "bug")],
        [btn("👑 Титул (+15)", "title"), btn("❌ Отклонить", "reject")],
    ])


def remember(message: types.Message):
    """Запоминаем chat_id по @username, чтобы потом можно было написать человеку."""
    user = message.from_user
    if user and user.username:
        known_users[user.username.lower()] = message.chat.id


async def notify_user(tg: str | None, text: str) -> str | None:
    """Возвращает None при успехе, иначе причину, почему не получилось."""
    if not tg:
        return "в отчёте нет @username — тестер не уведомлён"
    chat_id = known_users.get(tg.lower())
    if not chat_id:
        return f"@{tg} не запускал бота (/start) — уведомить нельзя"
    try:
        await bot.send_message(chat_id, text)
    except Exception as exc:
        logging.warning("Не удалось отправить ЛС: %s", exc)
        return f"не удалось написать @{tg}"
    return None


async def send_report(nick: str, tg: str | None, title: str, category: str, media: str, desc: str):
    rid = uuid.uuid4().hex[:8]
    reports[rid] = {"nick": nick, "tg": tg}
    e = html.escape
    text = (
        "📬 <b>НОВЫЙ ОТЧЁТ (Dristick Tester)</b>\n\n"
        f"👤 <b>Никнейм:</b> {e(nick)}\n"
        f"📱 <b>Telegram:</b> {'@' + e(tg) if tg else 'нет'}\n"
        f"👑 <b>Титул:</b> {e(title)}\n"
        f"📌 <b>Категория:</b> {e(category)}\n\n"
        f"🔗 <b>Медиа:</b> {e(media)}\n"
        f"📝 <b>Описание:</b> {e(desc)}"
    )
    await bot.send_message(ADMIN_ID, text, reply_markup=report_keyboard(rid))


# ───────────── Команды ─────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    remember(message)
    await message.answer("🕹️ <b>Dristick Tester Bot активен!</b>\nКоманды: /site, /top, /download")


@dp.message(Command("site"))
async def cmd_site(message: types.Message):
    await message.answer(f"🌐 <b>Сайт:</b>\n{html.escape(SITE_URL)}")


@dp.message(Command("download"))
async def cmd_download(message: types.Message):
    await message.answer(f"⬇️ <b>Скачать:</b>\n{html.escape(DOWNLOAD_URL)}")


@dp.message(Command("top", "leaderboard"))
async def cmd_top(message: types.Message):
    if not leaderboard:
        await message.answer("🏆 <b>Таблица лидеров пока пуста.</b>")
        return
    top = sorted(leaderboard.items(), key=lambda x: x[1]["points"], reverse=True)[:10]
    text = "🏆 <b>ТОП ТЕСТИРОВЩИКОВ</b> 🏆\n\n"
    for idx, (user, data) in enumerate(top, 1):
        text += f"{idx}. <b>{html.escape(user)}</b> — <b>{data['points']}</b> очков\n"
    await message.answer(text)


# ───────────── Кнопки под отчётом ─────────────
@dp.callback_query(F.data.startswith("rep:"))
async def on_report_action(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("⛔ Только для разработчика!", show_alert=True)
        return

    try:
        _, action, rid = cb.data.split(":", 2)
    except ValueError:
        await cb.answer()
        return
    if action not in ACTIONS:
        await cb.answer()
        return
    if rid in done:
        await cb.answer("Уже обработано")
        return
    done.add(rid)

    points, is_bug, status, user_msg = ACTIONS[action]
    rep = reports.pop(rid, None)
    if rep:
        tg, nick = rep["tg"], rep["nick"]
    else:  # после рестарта сервера — берём данные из текста сообщения
        tg, nick = extract_user_info(cb.message.text or cb.message.caption or "")
    user_key = f"@{tg}" if tg else nick

    if points:
        add_points(user_key, points, is_bug)
    problem = await notify_user(tg, user_msg)

    status_line = f"{status} — {html.escape(user_key)}"
    if problem:
        status_line += f"\n⚠️ {html.escape(problem)}"
    try:
        await cb.message.edit_text(f"{cb.message.html_text}\n\n{status_line}", reply_markup=None)
    except TelegramBadRequest:
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply(status_line)
    await cb.answer("Готово")


@dp.message()
async def on_any_message(message: types.Message):
    remember(message)


# ───────────── HTTP: приём отчётов с сайта ─────────────
def rate_limited(ip: str, limit: int = 5, window: int = 60) -> bool:
    now = time.monotonic()
    q = hits[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


@web.middleware
async def cors_middleware(request: web.Request, handler):
    resp = await handler(request)
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Vary"] = "Origin"
    return resp


async def handle_root(request: web.Request):
    return web.Response(text="Dristick Bot is running!")


async def handle_preflight(request: web.Request):
    return web.Response(status=204)


async def handle_report(request: web.Request):
    ip = request.headers.get("X-Forwarded-For", request.remote or "").split(",")[0].strip()
    if rate_limited(ip):
        return web.json_response({"ok": False, "error": "too_many_requests"}, status=429)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad_json"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"ok": False, "error": "bad_json"}, status=400)

    nick = clean(data.get("nickname"), 64)
    if not nick:
        return web.json_response({"ok": False, "error": "nickname_required"}, status=400)

    try:
        await send_report(
            nick=nick,
            tg=norm_tg(data.get("telegram")),
            title=clean(data.get("title"), 64) or "Не указан",
            category=clean(data.get("type"), 32) or "Не указана",
            media=clean(data.get("media"), 500) or "нет",
            desc=clean(data.get("description"), 2000) or "Без описания",
        )
    except Exception:
        logging.exception("Не удалось отправить отчёт админу")
        return web.json_response({"ok": False, "error": "telegram_error"}, status=502)
    return web.json_response({"ok": True})


def build_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/", handle_root)
    app.router.add_post("/api/report", handle_report)
    app.router.add_route("OPTIONS", "/api/report", handle_preflight)
    return app


async def main():
    runner = web.AppRunner(build_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000))).start()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
