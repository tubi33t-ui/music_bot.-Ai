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

def direct_parse_search(query, limit=40):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7', 'Connection': 'keep-alive'}
        url = f'https://www.youtube.com/results?search_query={query}'
        response = requests.get(url, headers=headers, timeout=5)
        html = response.text
        match = re.search(r'var ytInitialData = (\{.*?\});', html)
        if not match: return None
        import json
        data = json.loads(match.group(1))
        sections = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents']
        results = []
        for section in sections:
            if 'itemSectionRenderer' not in section: continue
            items = section['itemSectionRenderer']['contents']
            for item in items:
                if 'videoRenderer' not in item: continue
                renderer = item['videoRenderer']
                video_id = renderer.get('videoId')
                title = ''.join(run['text'] for run in renderer.get('title', {}).get('runs', []))
                length_text = renderer.get('lengthText', {}).get('simpleText', '0:00')
                try:
                    parts = length_text.split(':')
                    duration = int(parts[0])*60 + int(parts[1]) if len(parts) == 2 else int(parts[0])
                except:
                    duration = 0
                if duration < 30 or duration > 600: continue
                results.append({'title': title, 'url': f'https://youtube.com/watch?v={video_id}', 'duration': duration})
                if len(results) >= limit: return results
        return results if results else None
    except Exception as e:
        print(f"❌ Способ 1 (Прямой парсинг) не сработал: {e}")
        return None

def search_youtube(query, limit=40):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    cleaned = query.strip()
    direct_results = direct_parse_search(cleaned, limit)
    if direct_results:
        print("⚡ Найдено через прямой парсинг!")
        return direct_results
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True, 'default_search': f'ytsearch{limit}:', 'headers': headers, 'geo_bypass': True, 'socket_timeout': 5}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(cleaned, download=False)
            entries = info.get('entries', [])
            if entries:
                results = []
                for entry in entries:
                    title = entry.get('title', 'Без названия')
                    duration = entry.get('duration')
                    if duration and (duration < 30 or duration > 600): continue
                    url = entry.get('url') or f"https://youtube.com/watch?v={entry.get('id')}"
                    results.append({'title': title, 'url': url, 'duration': duration})
                if results: return results
    except Exception as e:
        print(f"❌ Способ 2 (yt-dlp быстрый) не сработал: {e}")
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': False, 'default_search': f'ytsearch{limit}:', 'headers': headers, 'geo_bypass': True, 'socket_timeout': 15}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(cleaned, download=False)
            entries = info.get('entries', [])
            if entries:
                results = []
                for entry in entries:
                    title = entry.get('title', 'Без названия')
                    duration = entry.get('duration', 0)
                    if duration < 30 or duration > 600: continue
                    url = entry.get('url') or f"https://youtube.com/watch?v={entry.get('id')}"
                    results.append({'title': title, 'url': url, 'duration': duration})
                if results: return results
    except Exception as e:
        print(f"❌ Способ 3 (yt-dlp медленный) не сработал: {e}")
    return []

def download_youtube(url):
    clients_list = ['android_vr', 'web_safari', 'web', 'tv']
    for client in clients_list:
        try:
            ydl_opts = {
                'format': 'bestaudio/best', 'outtmpl': 'downloads/%(title)s.%(ext)s', 'quiet': True, 'no_warnings': True,
                'headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'},
                'geo_bypass': True, 'socket_timeout': 30, 'retries': 3, 'fragment_retries': 3,
                'extractor_args': {'youtube': {'player_client': [client]}}
            }
            os.makedirs('downloads', exist_ok=True)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            audio_files = glob.glob('downloads/*.m4a') + glob.glob('downloads/*.opus') + glob.glob('downloads/*.webm')
            if audio_files: return max(audio_files, key=os.path.getmtime)
        except Exception as e:
            logging.error(f"Ошибка скачивания (клиент {client}): {e}")
            continue
    return None

async def start(update, context):
    await update.message.reply_text("🎵 *Музыкальный бот*\nНапиши группу или песню. Нажми *⛔️ Отменить*, чтобы остановить!", parse_mode='Markdown')

async def handle_message(update, context):
    query = update.message.text.strip()
    old_msg_id = context.user_data.pop('last_search_msg_id', None)
    old_status_id = context.user_data.pop('last_status_msg_id', None)
    if old_msg_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_msg_id)
        except: pass
    if old_status_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_status_id)
        except: pass
    if len(query.split()) == 2: limit = 5
    else: limit = 40
    msg = await update.message.reply_text(f"🧠 Ищу *{query}*...", parse_mode='Markdown')
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, search_youtube, query, limit)
    context.user_data['last_search_msg_id'] = msg.message_id
    if not results:
        await msg.edit_text(f"❌ Ничего не найдено по запросу *{query}*.", parse_mode='Markdown')
        return
    context.user_data['search_results'] = results
    context.user_data['current_page'] = 0
    await show_page(update, context, msg)

async def show_page(update, context, msg=None):
    results = context.user_data.get('search_results', [])
    page = context.user_data.get('current_page', 0)
    per_page = 10
    total_pages = (len(results) + per_page - 1) // per_page
    if page >= total_pages: page = total_pages - 1; context.user_data['current_page'] = page
    start = page * per_page
    end = min(start + per_page, len(results))
    page_results = results[start:end]
    keyboard = []
    for i, track in enumerate(page_results, start=start + 1):
        clean_t = clean_title(track['title'])
        dur = track.get('duration', 0)
        dur_str = f"{dur//60}:{dur%60:02d}" if dur else "??:??"
        label = f"{i}. {clean_t[:35]} - {dur_str}"
        if len(label) > 60: label = label[:57] + '...'
        keyboard.append([InlineKeyboardButton(label, callback_data=f"play_{i-1}")])
    nav_buttons = []
    if page > 0: nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data="prev_page"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data="next_page"))
    if nav_buttons: keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("⛔️ Отменить", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🎶 Нашёл!"
    try:
        if msg: await msg.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        else: await update.callback_query.message.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e): pass
        else: raise e

async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "cancel":
        task = context.user_data.get('active_task')
        if task and not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        status_id = context.user_data.pop('last_status_msg_id', None)
        if status_id:
            try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_id)
            except: pass
        await query.edit_message_text("✅ Поиск остановлен. Напиши новый запрос.")
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
            await query.edit_message_text("❌ Результаты устарели. Повтори запрос.")
            return
        selected = results[index]
        title = selected['title']
        url = selected['url']
        status_msg = await query.message.reply_text(f"⏳ Качаю *{title}*...", parse_mode='Markdown')
        context.user_data['last_status_msg_id'] = status_msg.message_id
        async def download_and_send():
            try:
                filepath = await asyncio.wait_for(asyncio.to_thread(download_youtube, url), timeout=90)
                if filepath and os.path.exists(filepath):
                    try:
                        with open(filepath, 'rb') as f:
                            await query.message.reply_audio(audio=f, title=os.path.basename(filepath).rsplit('.', 1)[0], performer="YouTube", caption="🎵 Держи!")
                        os.remove(filepath)
                        await status_msg.delete()
                        await show_page(update, context)
                    except Exception as e:
                        await status_msg.edit_text(f"❌ Ошибка отправки: {e}")
                        await show_page(update, context)
                else:
                    await status_msg.edit_text(f"❌ Не скачалось *{title}*.", parse_mode='Markdown')
                    await show_page(update, context)
            except asyncio.CancelledError:
                await status_msg.edit_text("⛔️ Отменено.")
            except asyncio.TimeoutError:
                await status_msg.edit_text("❌ Скачивание заняло слишком много времени.")
                await show_page(update, context)
            except Exception as e:
                await status_msg.edit_text(f"❌ Ошибка: {e}")
                await show_page(update, context)
            finally:
                context.user_data.pop('active_task', None)
        task = asyncio.create_task(download_and_send())
        context.user_data['active_task'] = task
        await status_msg.edit_text(f"⏳ Качаю *{title}*...", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⛔️ Отменить", callback_data="cancel")]]))

def main():
    print("✅ Бот запущен на Render через Webhook!")
    app = Application.builder().token(TOKEN).read_timeout(120).write_timeout(120).connect_timeout(120).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Запуск через WEBHOOK (Render дает порт через переменную PORT)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path="webhook",
        webhook_url="https://music-bot-ai.onrender.com/webhook",
        secret_token=TOKEN
    )

if __name__ == "__main__":
    main()
