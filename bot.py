import os
import logging
import glob
import asyncio
import re
import yt_dlp
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import BadRequest

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    print("❌ ОШИБКА: Не задана переменная TOKEN на Render!")
    raise SystemExit

logging.basicConfig(level=logging.INFO)

def translit(text):
    mapping = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'}
    return ''.join(mapping.get(ch, ch) for ch in text.lower())

def clean_title(title):
    title = re.sub(r'\s*[\(\[][^)\]]*(official|audio|video|lyrics|hq|hd|remaster|clip|клип|официальный|аудио|видео|текст)[^)\]]*[\)\]]', '', title, flags=re.IGNORECASE)
    return title.strip(' -–—,|')

def fix_typos(query):
    q = query.lower()
    replacements = {
        'sleer': 'slayer', 'металлика': 'metallica', 'мастер оф папетс': 'master of puppets',
        'кипелов': 'kipelov', 'энтер сендмен': 'enter sandman', 'король и шут': 'korol i shut'
    }
    for typo, correct in replacements.items():
        q = q.replace(typo, correct)
    return q

def generate_yt_queries(query):
    """Генерирует варианты запросов для YouTube Music"""
    q = fix_typos(query).strip()
    variants = [q]
    if translit(q) != q:
        variants.append(translit(q))
    variants.append(f"{q} song")
    variants.append(f"{q} audio")
    return list(dict.fromkeys(variants))

def search_youtube_music(query, limit=20):
    """Поиск именно через клиент YouTube Music"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    all_results = []

    for v in generate_yt_queries(query):
        try:
            ydl_opts = {
                'quiet': True, 'no_warnings': True, 'extract_flat': True,
                'default_search': f'ytsearch{limit}:',
                'headers': headers,
                'socket_timeout': 10,
                # ВАЖНО: Используем клиент web_music для поиска музыки!
                'extractor_args': {'youtube': {'player_client': ['web_music']}}
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(v, download=False)
                entries = info.get('entries', [])
                for entry in entries:
                    title = entry.get('title', 'Без названия')
                    duration = int(entry.get('duration') or 0)
                    url = f"https://youtube.com/watch?v={entry.get('id')}"
                    if url and (60 < duration < 900):
                        all_results.append({
                            'title': title, 'url': url, 'duration': duration, 'source': 'youtube'
                        })
        except Exception as e:
            logging.error(f"Ошибка поиска YT Music '{v}': {e}")
            continue

    final, seen = [], set()
    for r in all_results:
        if r['url'] not in seen:
            seen.add(r['url'])
            final.append(r)
    return final[:limit]

def download_youtube(url):
    """Попытка скачать через официальный клиент"""
    clients_list = ['web_music', 'web', 'tv']
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    for client in clients_list:
        try:
            ydl_opts = {
                'format': '140/bestaudio/best',
                'outtmpl': 'downloads/%(title)s.%(ext)s',
                'quiet': True, 'no_warnings': True,
                'headers': headers,
                'socket_timeout': 30,
                'retries': 5,
                'fragment_retries': 5,
                # Обязательно куки для работы на Render!
                'cookiefile': 'cookies.txt',
                'extractor_args': {'youtube': {'player_client': [client]}}
            }
            os.makedirs('downloads', exist_ok=True)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            audio_files = glob.glob('downloads/*.m4a') + glob.glob('downloads/*.opus') + glob.glob('downloads/*.webm')
            if audio_files:
                return max(audio_files, key=os.path.getmtime)
        except Exception as e:
            logging.error(f"Ошибка скачивания (клиент {client}): {e}")
            continue
    return None

async def start(update, context):
    await update.message.reply_text(
        "🎵 *Гипер-Бот с ИИ*\n\n"
        "Ищет на YouTube Music! Понимает транслит: *металлика* -> *metallica*\n\n"
        "_Если не скачивает из-за блока Render, выдаст ссылку._", parse_mode='Markdown')

async def handle_message(update, context):
    query = update.message.text.strip()
    old_msg = context.user_data.pop('last_search_msg_id', None)
    if old_msg:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_msg)
        except: pass

    msg = await update.message.reply_text(f"🧠 Ищу *{query}* на YouTube Music...", parse_mode='Markdown')
    results = await search_youtube_music(query, 20)

    context.user_data['last_search_msg_id'] = msg.message_id

    if not results:
        await msg.edit_text(f"❌ Ничего не нашел. Попробуй написать на английском.", parse_mode='Markdown')
        return

    context.user_data['search_results'] = results
    context.user_data['current_page'] = 0
    await show_page(update, context, msg)

async def show_page(update, context, msg=None):
    results = context.user_data.get('search_results', [])
    page = context.user_data.get('current_page', 0)
    per_page = 10
    total_pages = (len(results) + per_page - 1) // per_page
    if page >= total_pages: page = total_pages - 1

    start = page * per_page
    end = min(start + per_page, len(results))
    page_results = results[start:end]

    keyboard = []
    for i, track in enumerate(page_results, start=start + 1):
        clean_t = clean_title(track['title'])
        dur = track.get('duration', 0)
        dur_str = f"{int(dur)//60}:{int(dur)%60:02d}" if dur else "??:??"
        label = f"▶️ {i}. {clean_t[:35]} - {dur_str}"
        if len(label) > 60: label = label[:57] + '...'
        keyboard.append([InlineKeyboardButton(label, callback_data=f"play_{i-1}")])

    nav_buttons = []
    if page > 0: nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data="prev_page"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data="next_page"))
    if nav_buttons: keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("⛔️ Отменить", callback_data="cancel")])

    try:
        if msg: await msg.edit_text("🎶 Нашёл!", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        else: await update.callback_query.message.edit_text("🎶 Нашёл!", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except BadRequest as e:
        if "Message is not modified" not in str(e): raise e

async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        task = context.user_data.get('active_task')
        if task and not task.done(): task.cancel()
        await query.edit_message_text("✅ Поиск остановлен.")
        return

    if data == "prev_page":
        current = context.user_data.get('current_page', 0)
        if current > 0: context.user_data['current_page'] = current - 1; await show_page(update, context)
    elif data == "next_page":
        current = context.user_data.get('current_page', 0)
        results = context.user_data.get('search_results', [])
        per_page = 10
        total_pages = (len(results) + per_page - 1) // per_page
        if current < total_pages - 1: context.user_data['current_page'] = current + 1; await show_page(update, context)
    elif data.startswith("play_"):
        index = int(data.split("_")[1])
        results = context.user_data.get('search_results', [])
        if not results or index >= len(results):
            await query.edit_message_text("❌ Результаты устарели.")
            return

        track = results[index]
        title = track['title']
        url = track['url']
        status_msg = await query.message.reply_text(f"⏳ Качаю *{title}*...", parse_mode='Markdown')

        async def download_and_send():
            try:
                filepath = await asyncio.wait_for(asyncio.to_thread(download_youtube, url), timeout=90)
                if filepath and os.path.exists(filepath):
                    with open(filepath, 'rb') as f:
                        await query.message.reply_audio(audio=f, title=os.path.basename(filepath).rsplit('.', 1)[0], caption="🎵 Держи!")
                    os.remove(filepath)
                    await status_msg.delete()
                else:
                    await status_msg.edit_text(f"❌ Не скачалось.\n[Открыть на YouTube]({url})", parse_mode='Markdown', disable_web_page_preview=True)
            except asyncio.TimeoutError:
                await status_msg.edit_text(f"⏱️ Слишком долго.\n[Открыть на YouTube]({url})", parse_mode='Markdown', disable_web_page_preview=True)

        task = asyncio.create_task(download_and_send())
        context.user_data['active_task'] = task

def main():
    print("✅ Гипер-Бот запущен!")
    app = Application.builder().token(TOKEN).read_timeout(120).write_timeout(120).connect_timeout(120).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://music-bot-ai.onrender.com") + "/webhook"
    app.run_webhook(
        listen="0.0.0.0", port=int(os.environ.get("PORT", 10000)),
        url_path="webhook", webhook_url=webhook_url, secret_token="mysecret123"
    )

if __name__ == "__main__":
    main()
