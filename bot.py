import logging
import asyncio
import os
import re
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# Токен и ID разработчика
BOT_TOKEN = os.getenv("BOT_TOKEN", "8839895562:AAELe4fHssbswyDOCg2WPmfJ26lnRPmoRgM")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5876046214"))

SITE_URL = "https://outadevinc-commits.github.io/Dristick-Tester-Bot/" 
DOWNLOAD_URL = "https://drive.google.com"

# Общая статистика
stats = {
    "total_reports": 0,
    "bug_reports": 0,
    "ideas": 0,
    "gameplay": 0
}

# База данных лидеров в памяти: {username/nickname: {"points": int, "bugs": int, "reports": int}}
leaderboard = {}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_report_keyboard(report_id: int):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принято (+5 очков)", callback_data=f"status_accept_{report_id}"),
            InlineKeyboardButton(text="🐛 Баг (+10 очков)", callback_data=f"status_bug_{report_id}"),
        ],
        [
            InlineKeyboardButton(text="👑 Выдать титул (+15 очков)", callback_data=f"grant_title_{report_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"status_reject_{report_id}")
        ]
    ])
    return keyboard

def extract_user_info(text: str):
    """Извлекает Telegram Username или Никнейм из текста отчёта"""
    tg_match = re.search(r'Telegram:\s*@?([a_zA_Z0_9_]+)', text, re.IGNORECASE)
    nick_match = re.search(r'Никнейм:\s*(.+)', text, re.IGNORECASE)
    
    tg_user = tg_match.group(1) if tg_match else None
    nick = nick_match.group(1).strip() if nick_match else "Аноним"
    
    return tg_user, nick

def add_points(user_key: str, points: int, is_bug: bool = False):
    """Начисляет очки тестеру в таблицу лидеров"""
    if user_key not in leaderboard:
        leaderboard[user_key] = {"points": 0, "bugs": 0, "reports": 0}
    
    leaderboard[user_key]["points"] += points
    leaderboard[user_key]["reports"] += 1
    if is_bug:
        leaderboard[user_key]["bugs"] += 1

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🕹️ **Добро пожаловать в Dristick Tester Bot!**\n\n"
        "Здесь ты можешь отправлять отчёты по игре и бороться за место в ТОПе тестеров!\n\n"
        "📌 **Список команд:**\n"
        "🔹 /site — Ссылка на сайт тестирования\n"
        "🔹 /top — Таблица лидеров тестеров 🏆\n"
        "🔹 /download — Скачать актуальную версию игры\n"
        "🔹 /rules — Правила для тестировщиков"
    )

@dp.message(Command("site"))
async def cmd_site(message: types.Message):
    await message.answer(f"🌐 **Сайт тестирования Dristick Tester:**\n{SITE_URL}")

@dp.message(Command("download"))
async def cmd_download(message: types.Message):
    await message.answer(f"📦 **Скачать тестовую сборку игры:**\n{DOWNLOAD_URL}")

@dp.message(Command("rules"))
async def cmd_rules(message: types.Message):
    await message.answer(
        "📜 **Правила тестирования Dristick:**\n\n"
        "1. Записывай геймплей или делай скриншоты багов.\n"
        "2. Указывай свой точный ник и желаемый титул.\n"
        "3. Подробно описывай, где именно лагает или проваливается текстура!"
    )

@dp.message(Command("top", "leaderboard"))
async def cmd_top(message: types.Message):
    if not leaderboard:
        await message.answer("🏆 **Таблица лидеров пока пуста.**\nБудь первым, чей отчёт примет разработчик!")
        return

    sorted_top = sorted(leaderboard.items(), key=lambda x: x[1]["points"], reverse=False if False else True)
    
    text = "🏆 **ТОП ТЕСТИРОВЩИКОВ DRISTICK** 🏆\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    medals = ["🥇", "🥈", "🥉"]
    for idx, (user, data) in enumerate(sorted_top[:10], 1):
        prefix = medals[idx-1] if idx <= 3 else f"`{idx}.`"
        text += f"{prefix} **{user}** — **{data['points']}** очков ({data['bugs']} багов, {data['reports']} отчётов)\n"
        
    text += "\n━━━━━━━━━━━━━━━━━━━━━━\n💡 *Набирай очки за найденные баги и полезный фидбек!*"
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа к этой команде!")
        return

    text = (
        "📊 **СТАТИСТИКА DRISTICK TESTER**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 Разработчик: `{ADMIN_ID}`\n"
        f"📥 Всего отчётов отправлено: **{stats['total_reports']}**\n\n"
        f"🐛 Багрепортов: {stats['bug_reports']}\n"
        f"💡 Идей и предложений: {stats['ideas']}\n"
        f"🎥 Чистого геймплея: {stats['gameplay']}\n"
        f"👥 Участников в рейтинге: {len(leaderboard)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 Статус бота: **Активен / Онлайн**"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.callback_query()
async def process_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Только разработчик может изменять статус!", show_alert=True)
        return

    data = callback.data
    original_text = callback.message.text or callback.message.caption or ""
    tg_user, nick = extract_user_info(original_text)
    user_key = f"@{tg_user}" if tg_user else nick

    async def notify_user(msg_text: str):
        if tg_user:
            try:
                await bot.send_message(chat_id=f"@{tg_user}", text=msg_text)
            except Exception as e:
                logging.warning(f"Не удалось отправить личное сообщение @{tg_user}: {e}")

    if data.startswith("status_accept"):
        add_points(user_key, 5)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"✅ **Отчёт от {user_key} ПРИНЯТ!** (+5 очков)")
        await notify_user(f"🎉 **Привет! Твой отчёт по Dristick Tester был принят разработчиком!**\nТебе начислено +5 очков в /top.")
        await callback.answer("Принято! Очки начислены.")
        
    elif data.startswith("status_bug"):
        add_points(user_key, 10, is_bug=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"🐛 **Баг от {user_key} зафиксирован!** (+10 очков)")
        await notify_user(f"🐛 **Отличная работа! Разработчик зафиксировал найденный тобой баг!**\nТебе начислено +10 очков в /top.")
        await callback.answer("Баг зафиксирован! +10 очков.")

    elif data.startswith("grant_title"):
        add_points(user_key, 15)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"👑 **Титул для {user_key} успешно утверждён!** (+15 очков)")
        await notify_user(f"👑 **Поздравляем! Разработчик одобрил твой уникальный титул в игре!**\nТебе начислено +15 очков в /top.")
        await callback.answer("Титул выдан!")

    elif data.startswith("status_reject"):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"❌ **Отчёт от {user_key} отклонен.**")
        await notify_user(f"ℹ️ Твой отчёт по Dristick Tester был отклонен или отмечен как неактуальный.")
        await callback.answer("Отклонено!")

@dp.message()
async def handle_incoming_reports(message: types.Message):
    global stats
    caption_or_text = message.caption or message.text or ""
    
    if "Dristick Tester" in caption_or_text or "Титул" in caption_or_text or "Никнейм" in caption_or_text:
        stats["total_reports"] += 1
        if "Багрепорт" in caption_or_text:
            stats["bug_reports"] += 1
        elif "Идея" in caption_or_text:
            stats["ideas"] += 1
        else:
            stats["gameplay"] += 1

        await message.reply(
            "⚡ **Новый отчёт получен! Выберите действие:**",
            reply_markup=get_report_keyboard(stats["total_reports"])
        )

# Веб-сервер для поддержки Render в активном состоянии
async def handle_web(request):
    return web.Response(text="Dristick Bot is running!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    print("🚀 Бот Dristick Tester с ТОПом и авто-уведомлениями запущен!")
    await asyncio.gather(web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
