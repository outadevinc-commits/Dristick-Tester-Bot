import logging
import asyncio
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# Токен и ID разработчика
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

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_report_keyboard(report_id: int):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принято", callback_data=f"status_accept_{report_id}"),
            InlineKeyboardButton(text="🐛 Баг зафиксирован", callback_data=f"status_bug_{report_id}"),
        ],
        [
            InlineKeyboardButton(text="👑 Выдать титул", callback_data=f"grant_title_{report_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"status_reject_{report_id}")
        ]
    ])
    return keyboard

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🕹️ **Добро пожаловать в Dristick Tester Bot!**\n\n"
        "Здесь ты можешь получить ссылку на сайт для отправки геймплея и багрепортов.\n\n"
        "📌 **Список команд:**\n"
        "🔹 /site — Ссылка на сайт тестирования\n"
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
    
    if data.startswith("status_accept"):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("✅ **Отчёт отмечен как ПРИНЯТ!**")
        await callback.answer("Статус обновлен!")
        
    elif data.startswith("status_bug"):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("🐛 **Баг внесён в список задач!**")
        await callback.answer("Баг зафиксирован!")

    elif data.startswith("grant_title"):
        await callback.answer("👑 Титул одобрен и записан!", show_alert=True)
        await callback.message.reply("👑 **Титул тестировщика успешно подтверждён!**")

    elif data.startswith("status_reject"):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("❌ **Отчёт отклонен / не актуален.**")
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

# Функция для обмана Render, чтобы он думал, будто запущен сайт (убирает тайм-аут)
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
    print("🚀 Бот Dristick Tester запущен!")
    # Запускаем и веб-сервер для Render, и самого телеграм-бота одновременно
    await asyncio.gather(web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())


