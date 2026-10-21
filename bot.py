import asyncio
import hashlib
import html
import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque

from aiohttp import ClientSession, ClientTimeout, web
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
ALLOWED_ORIGINS = {
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "https://outadevinc-commits.github.io").split(",")
    if o.strip()
}

# Хранилище очков. Если заданы обе переменные — очки живут в Upstash Redis и
# переживают любые деплои. Иначе — файл (сбрасывается при деплое на Render).
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
STATE_KEY = "dristick_state"

DEDUP_WINDOW = 24 * 3600  # одинаковый отчёт повторно не принимаем 24 часа

# action -> (очки, это баг?, статус для админа, уведомление тестеру)
ACTIONS = {
    "accept": (5, False, "✅ Отчёт принят (+5)", "🎉 Твой отчёт по Dristick Tester принят разработчиком! (+5 очков)"),
    "bug": (10, True, "🐛 Баг зафиксирован (+10)", "🐛 Разработчик зафиксировал твой баг! (+10 очков)"),
    "title": (15, False, "👑 Титул утверждён (+15)", "👑 Твой титул одобрен! (+15 очков)"),
    "reject": (0, False, "❌ Отчёт отклонён", "ℹ️ Твой отчёт был отклонён разработчиком."),
}
STAT_KEYS = {"accept": "accepted", "bug": "bugs", "title": "titles", "reject": "rejected"}

# Звания по очкам: (минимум очков, название)
RANKS = [
    (0, "🆕 Новичок"),
    (20, "🔍 Тестер"),
    (50, "🐛 Охотник на баги"),
    (100, "⚔️ Ветеран"),
    (200, "👑 Легенда"),
]

# ───────────── Состояние ─────────────
leaderboard: dict[str, dict] = {}   # "@user" или ник -> {"points", "bugs", "reports"}
known_users: dict[str, int] = {}    # username (lower) -> chat_id (кто писал боту)
seen: dict[str, float] = {}         # отпечаток отчёта -> время (защита от дублей)
stats = {"total": 0, "accepted": 0, "bugs": 0, "titles": 0, "rejected": 0}

# Не сохраняется (живёт до рестарта)
reports: dict[str, dict] = {}       # report_id -> {"nick", "tg"}
done: set[str] = set()              # уже обработанные отчёты
reply_state: dict = {}              # кому админ сейчас пишет ответ
hits: dict[str, deque] = defaultdict(deque)  # антиспам по IP
save_lock = asyncio.Lock()

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ───────────── Хранилище ─────────────
def upstash_enabled() -> bool:
    return bool(UPSTASH_URL and UPSTASH_TOKEN)


async def upstash(command: list):
    async with ClientSession(timeout=ClientTimeout(total=15)) as session:
        async with session.post(
            UPSTASH_URL, json=command, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}
        ) as resp:
            data = await resp.json()
            if resp.status != 200 or "error" in data:
                raise RuntimeError(f"Upstash {resp.status}: {data}")
            return data.get("result")


async def load_state():
    raw = None
    if upstash_enabled():
        for attempt in range(1, 4):
            try:
                raw = await upstash(["GET", STATE_KEY])
                break
            except Exception as exc:
                logging.warning("Upstash: не удалось загрузить (%s/3): %s", attempt, exc)
                if attempt == 3:
                    # Лучше не запускаться, чем стартовать пустым и затереть очки.
                    raise SystemExit("Не удалось загрузить данные из Upstash — выхожу, чтобы не затереть очки.")
                await asyncio.sleep(3)
    elif os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            raw = f.read()

    if raw:
        data = json.loads(raw)
        leaderboard.update(data.get("leaderboard", {}))
        known_users.update({k: int(v) for k, v in data.get("users", {}).items()})
        seen.update({k: float(v) for k, v in data.get("seen", {}).items()})
        stats.update(data.get("stats", {}))
    logging.info(
        "Хранилище: %s | тестеров: %d, пользователей: %d",
        "Upstash" if upstash_enabled() else f"файл {DATA_FILE}",
        len(leaderboard),
        len(known_users),
    )


async def save_state():
    async with save_lock:
        payload = json.dumps(
            {"leaderboard": leaderboard, "users": known_users, "seen": seen, "stats": stats},
            ensure_ascii=False,
        )
        try:
            if upstash_enabled():
                await upstash(["SET", STATE_KEY, payload])
            else:
                tmp = DATA_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, DATA_FILE)
        except Exception:
            logging.exception("Не удалось сохранить данные (память в порядке, повторю при следующем изменении)")


# ───────────── Хелперы ─────────────
def clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def norm_tg(value) -> str | None:
    value = clean(value, 64).lstrip("@")
    return value if re.fullmatch(r"[A-Za-z0-9_]{3,32}", value) else None


def extract_user_info(text: str):
    """Запасной вариант (после рестарта): достаём ник и @username из текста сообщения."""
    tg = re.search(r"Telegram:\s*@?([A-Za-z0-9_]+)", text, re.IGNORECASE)
    nick = re.search(r"Никнейм:\s*(.+)", text, re.IGNORECASE)
    return (tg.group(1) if tg else None), (nick.group(1).strip() if nick else "Аноним")


def rank_for(points: int) -> str:
    name = RANKS[0][1]
    for need, title in RANKS:
        if points >= need:
            name = title
    return name


def next_rank(points: int):
    for need, title in RANKS:
        if points < need:
            return need, title
    return None


def add_points(user_key: str, points: int, is_bug: bool = False):
    key = next((k for k in leaderboard if k.lower() == user_key.lower()), user_key)
    row = leaderboard.setdefault(key, {"points": 0, "bugs": 0, "reports": 0})
    old = row["points"]
    row["points"] += points
    row["reports"] += 1
    if is_bug:
        row["bugs"] += 1
    return key, old, row["points"]


def report_keyboard(rid: str) -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"rep:{action}:{rid}")

    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("✅ Принято (+5)", "accept"), btn("🐛 Баг (+10)", "bug")],
        [btn("👑 Титул (+15)", "title"), btn("❌ Отклонить", "reject")],
        [btn("💬 Ответить", "reply")],
    ])


def reply_only_keyboard(rid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"rep:reply:{rid}")]
    ])


async def remember(message: types.Message):
    """Запоминаем chat_id по @username, чтобы потом можно было написать человеку."""
    user = message.from_user
    if user and user.username and message.chat.type == "private":
        key = user.username.lower()
        if known_users.get(key) != message.chat.id:
            known_users[key] = message.chat.id
            await save_state()


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


def fingerprint(nick: str, tg: str | None, category: str, media: str, desc: str) -> str:
    raw = "|".join([nick.lower(), (tg or "").lower(), category.lower(), media.lower(), " ".join(desc.lower().split())])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def is_duplicate(fp: str) -> bool:
    now = time.time()
    for key in [k for k, t in seen.items() if now - t > DEDUP_WINDOW]:
        del seen[key]
    return fp in seen


async def send_report(nick: str, tg: str | None, title: str, category: str, media: str, desc: str):
    rid = uuid.uuid4().hex[:8]
    reports[rid] = {"nick": nick, "tg": tg}
    if len(reports) > 300:
        reports.pop(next(iter(reports)))
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
async def remember_middleware(handler, event, data):
    await remember(event)
    return await handler(event, data)


dp.message.outer_middleware(remember_middleware)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🕹️ <b>Dristick Tester Bot активен!</b>\n"
        "Команды: /me — твои очки и звание, /top — таблица лидеров, /site, /download"
    )


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
        text += f"{idx}. <b>{html.escape(user)}</b> — <b>{data['points']}</b> очков · {rank_for(data['points'])}\n"
    await message.answer(text)


@dp.message(Command("me"))
async def cmd_me(message: types.Message):
    username = message.from_user.username
    if not username:
        await message.answer("У тебя нет @username в Telegram, по нему я ищу твои очки. Задай его в настройках Telegram.")
        return
    key = next((k for k in leaderboard if k.lower() == f"@{username}".lower()), None)
    if not key:
        await message.answer(f"Пока нет начисленных очков. Отправь отчёт на сайте:\n{html.escape(SITE_URL)}")
        return
    row = leaderboard[key]
    ordered = sorted(leaderboard, key=lambda k: leaderboard[k]["points"], reverse=True)
    text = (
        f"👤 <b>{html.escape(key)}</b>\n"
        f"🎖 Звание: {rank_for(row['points'])}\n"
        f"⭐ Очки: <b>{row['points']}</b> (место {ordered.index(key) + 1} из {len(ordered)})\n"
        f"🐛 Баги: {row['bugs']}\n"
        f"📨 Принятых отчётов: {row['reports']}"
    )
    nxt = next_rank(row["points"])
    if nxt:
        text += f"\n⏭ До звания «{nxt[1]}»: {nxt[0] - row['points']} очков"
    await message.answer(text)


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    pending = max(0, stats["total"] - stats["accepted"] - stats["bugs"] - stats["titles"] - stats["rejected"])
    storage = "Upstash (очки переживают деплои)" if upstash_enabled() else "файл (сбросится при деплое!)"
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"📨 Всего отчётов: {stats['total']}\n"
        f"✅ Принято: {stats['accepted']}\n"
        f"🐛 Багов: {stats['bugs']}\n"
        f"👑 Титулов: {stats['titles']}\n"
        f"❌ Отклонено: {stats['rejected']}\n"
        f"⏳ Ждут решения: {pending}\n\n"
        f"🏆 Тестеров в топе: {len(leaderboard)}\n"
        f"👥 Писали боту: {len(known_users)}\n"
        f"💾 Хранилище: {storage}"
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    if message.from_user.id == ADMIN_ID and reply_state:
        reply_state.clear()
        await message.answer("Отменено.")


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

    text = cb.message.text or cb.message.caption or ""
    rep = reports.get(rid)
    if rep:
        tg, nick = rep["tg"], rep["nick"]
    else:  # после рестарта сервера — берём данные из текста сообщения
        tg, nick = extract_user_info(text)
    user_key = f"@{tg}" if tg else nick

    # 💬 Ответить тестеру
    if action == "reply":
        chat_id = known_users.get(tg.lower()) if tg else None
        if not chat_id:
            await cb.answer("Ответить нельзя: нет @username или тестер ещё не писал боту /start", show_alert=True)
            return
        reply_state.clear()
        reply_state.update(chat_id=chat_id, label=user_key)
        await cb.message.answer(f"✍️ Напиши ответ для {html.escape(user_key)} одним сообщением. Отмена — /cancel")
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
    rank_line = ""
    if points:
        user_key, old, new = add_points(user_key, points, is_bug)
        user_msg += f"\n⭐ Всего очков: {new}"
        if rank_for(new) != rank_for(old):
            rank_line = f"🎖 Новое звание: {rank_for(new)}"
            user_msg += f"\n{rank_line}"
    stats[STAT_KEYS[action]] += 1
    await save_state()

    problem = await notify_user(tg, user_msg)

    status_line = f"{status} — {html.escape(user_key)}"
    if rank_line:
        status_line += f"\n{rank_line}"
    if problem:
        status_line += f"\n⚠️ {html.escape(problem)}"
    try:
        await cb.message.edit_text(f"{cb.message.html_text}\n\n{status_line}", reply_markup=reply_only_keyboard(rid))
    except TelegramBadRequest:
        await cb.message.edit_reply_markup(reply_markup=reply_only_keyboard(rid))
        await cb.message.reply(status_line)
    await cb.answer("Готово")


@dp.message(F.from_user.id == ADMIN_ID, F.text, ~F.text.startswith("/"))
async def admin_reply(message: types.Message):
    """Следующее сообщение админа после кнопки «💬 Ответить» уходит тестеру."""
    if not reply_state:
        return
    target = dict(reply_state)
    reply_state.clear()
    try:
        await bot.send_message(target["chat_id"], f"💬 <b>Ответ разработчика:</b>\n{html.escape(message.text)}")
        await message.answer(f"✅ Отправлено: {html.escape(target['label'])}")
    except Exception as exc:
        logging.warning("Не удалось отправить ответ: %s", exc)
        await message.answer("⚠️ Не удалось отправить ответ.")


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

    tg = norm_tg(data.get("telegram"))
    title = clean(data.get("title"), 64) or "Не указан"
    category = clean(data.get("type"), 32) or "Не указана"
    media = clean(data.get("media"), 500) or "нет"
    desc = clean(data.get("description"), 2000) or "Без описания"

    fp = fingerprint(nick, tg, category, media, desc)
    if is_duplicate(fp):
        return web.json_response({"ok": False, "error": "duplicate"}, status=409)
    seen[fp] = time.time()

    try:
        await send_report(nick, tg, title, category, media, desc)
    except Exception:
        seen.pop(fp, None)
        logging.exception("Не удалось отправить отчёт админу")
        return web.json_response({"ok": False, "error": "telegram_error"}, status=502)

    stats["total"] += 1
    await save_state()
    return web.json_response({"ok": True})


def build_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/", handle_root)
    app.router.add_post("/api/report", handle_report)
    app.router.add_route("OPTIONS", "/api/report", handle_preflight)
    return app


async def main():
    await load_state()
    runner = web.AppRunner(build_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000))).start()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
