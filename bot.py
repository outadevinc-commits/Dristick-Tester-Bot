import logging
import asyncio
import os
import re
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

BOT_TOKEN = os.getenv("BOT_TOKEN", "8839895562:AAELe4fHssbswyDOCg2WPmfJ26lnRPmoRgM")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5876046214"))

SITE_URL = "https://outadevinc-commits.github.io/Dristick-Tester-Bot/" 
DOWNLOAD_URL = "https://drive.google.com"

stats = {
    "total_reports": 0,
    "bug_reports": 0,
    "ideas": 0,
    "gameplay": 0
}

leaderboard = {}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_report_keyboard(report_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принято (+5)", callback_data=f"status_accept_{report_id}"),
            InlineKeyboardButton(text="🐛 Баг (+10)", callback_data=f"status_bug_{report_id}"),
        ],
        [
            InlineKeyboardButton(text="👑 Титул (+15)", callback_data=f"grant_title_{report_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"status_reject_{report_id}")
        ]
    ])

def extract_user_info(text: str):
    tg_match = re.search(r'Telegram:\s*@?([a-zA-Z0-9_]+)', text, re.IGNORECASE)
    nick_match = re.search(r'Никнейм:\s*(.+)', text, re.IGNORECASE)
    
    tg_user = tg_match.group(1).strip() if tg_match else None
    nick = nick_match.group(1).strip() if nick_match else "Аноним"
    
    if tg_user and tg_user.lower() == "нет":
        tg_user = None

    return tg_user, nick

def add_points(user_key: str, points: int, is_bug: bool = False):
    if user_key not in leaderboard:
        leaderboard[user_key] = {"points": 0, "bugs": 0, "reports": 0}
    
    leaderboard[user_key]["points"] += points
    leaderboard[user_key]["reports"] += 1
    if is_bug:
        leaderboard[user_key]["bugs"] += 1

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("🕹️ **Dristick Tester Bot активен!**\nКоманды: /site, /top, /download")

@dp.message(Command("site"))
async def cmd_site(message: types.Message):
    await message.answer(f"🌐 **Сайт:**\n{SITE_URL}")

@dp.message(Command("top", "leaderboard"))
async def cmd_top(message: types.Message):
    if not leaderboard:
        await message.answer("🏆 **Таблица лидеров пока пуста.**")
        return
    sorted_top = sorted(leaderboard.items(), key=lambda x: x[1]["points"], reverse=True)
    text = "🏆 **ТОП ТЕСТИРОВЩИКОВ** 🏆\n\n"
    for idx, (user, data) in enumerate(sorted_top[:10], 1):
        text += f"{idx}. **{user}** — **{data['points']}** очков\n"
    await message.answer(text, parse_mode="Markdown")

@dp.callback_query()
async def process_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Только для разработчика!", show_alert=True)
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
                logging.warning(f"Не удалось отправить ЛС: {e}")

    if data.startswith("status_accept"):
        add_points(user_key, 5)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"✅ **Отчёт от {user_key} ПРИНЯТ!** (+5 очков)")
        await notify_user("🎉 Твой отчёт по Dristick Tester принят разработчиком! (+5 очков в /top)")
        await callback.answer("Принято!")
        
    elif data.startswith("status_bug"):
        add_points(user_key, 10, is_bug=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"🐛 **Баг от {user_key} зафиксирован!** (+10 очков)")
        await notify_user("🐛 Разработчик зафиксировал твой баг! (+10 очков в /top)")
        await callback.answer("Баг зафиксирован!")

    elif data.startswith("grant_title"):
        add_points(user_key, 15)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"👑 **Титул для {user_key} утверждён!** (+15 очков)")
        await notify_user("👑 Твой титул одобрен! (+15 очков в /top)")
        await callback.answer("Титул выдан!")

    elif data.startswith("status_reject"):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply(f"❌ **Отчёт от {user_key} отклонен.**")
        await notify_user("ℹ️ Твой отчёт был отклонен разработчиком.")
        await callback.answer("Отклонено!")

@dp.message()
async def handle_incoming_reports(message: types.Message):
    global stats
    text = message.text or message.caption or ""
    
    # Ловим отчёт с сайта
    if "Никнейм" in text or "Dristick" in text or "Категория" in text:
        stats["total_reports"] += 1
        
        # Удаляем старое чистое сообщение от сайта, чтобы не засорять чат
        try:
            await message.delete()
        except:
            pass
        
        # Отправляем красивое сообщение С КНОПКАМИ от имени бота
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚡ **НОВЫЙ ОТЧЁТ С САЙТА:**\n\n{text}",
            reply_markup=get_report_keyboard(stats["total_reports"]),
            parse_mode="Markdown"
        )

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
    print("🚀 Бот запущен!")
    await asyncio.gather(web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
