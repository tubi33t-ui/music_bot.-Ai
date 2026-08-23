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

# Транслитерация для поиска на английском
def translit(text):
    mapping = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'}
    return ''.join(mapping.get(ch, ch) for ch in text.lower())

def clean_title(title):
    title = re.sub(r'\s*[\(\[][^)\]]*(official|audio|video|lyrics|hq|hd|remaster|clip|клип|официальный|аудио|видео|текст)[^)\]]*[\)\]]', '', title, flags=re.IGNORECASE)
    return title.strip(' -–—,|')

# --- УЛУЧШЕННЫЙ ПОИСК НА SOUNDCLOUD ---
def search_soundcloud(query, limit=30):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    # Генерируем варианты запроса (русский + английский)
    variants = [query]
    if translit(query) != query:
        variants.append(translit(query))
    variants = list(dict.fromkeys(variants))

    all_results = []
    for q in variants:
        try:
            search_query = f'scsearch:{limit}:{q}'
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'headers': headers,
                'socket_timeout': 10,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                entries = info.get('entries', [])
                
                for entry in entries:
                    title = entry.get('title', 'Без названия')
                    # SoundCloud отдает float, приводим к int, чтобы не падать
                    duration = int(entry.get('duration') or 0)
                    url = entry.get('url') or entry.get('webpage_url')
                    if url:
                        all_results.append({'title': title, 'url': url, 'duration': duration})
        except Exception as e:
            print(f"Ошибка поиска '{q}': {e}")
            continue

    # Убираем дубликаты и возвращаем максимум limit
    final_results = []
    seen = set()
    for r in all_results:
        if r['url'] not in seen:
            seen.add(r['url'])
            final_results.append(r)
    return final_results[:limit]

def download_soundcloud(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'headers': headers,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
        }
        os.makedirs('downloads', exist_ok=True)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        audio_files = glob.glob('downloads/*.mp3') + glob.glob('downloads/*.m4a') + glob.glob('downloads/*.opus') + glob.glob('downloads/*.webm')
        if audio_files:
            return max(audio_files, key=os.path.getmtime)
    except Exception as e:
        logging.error(f"Ошибка скачивания с SoundCloud: {e}")
    return None

async def start(update, context):
    await update.message.reply_text("🎵 *SoundCloud Бот (Расширенный поиск)*\nПросто напиши название песни или исполнителя!\n*Совет:* Ищи больше по-английски, так как западные исполнители чаще всего есть именно там.", parse_mode='Markdown')

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

    # Новый лимит: 2 слова - 10 результатов, всё остальное - 30
    if len(query.split()) == 2: 
        limit = 10
    else: 
        limit = 30

    msg = await update.message.reply_text(f"🧠 Ищу *{query}* на SoundCloud...", parse_mode='Markdown')
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, search_soundcloud, query, limit)
    
    context.user_data['last_search_msg_id'] = msg.message_id

    if not results:
        await msg.edit_text(f"❌ По запросу *{query}* ничего не нашлось.\nПопробуй написать на английском!", parse_mode='Markdown')
        return

    context.user_data['search_results'] = results
    context.user_data['current_page'] = 0
    await show_page(update, context, msg)

async def show_page(update, context, msg=None):
    results = context.user_data.get('search_results', [])
    page = context.user_data.get('current_page', 0)
    per_page = 15  # Больше треков на странице
    total_pages = (len(results) + per_page - 1) // per_page
    if page >= total_pages: page = total_pages - 1; context.user_data['current_page'] = page
    start = page * per_page
    end = min(start + per_page, len(results))
    page_results = results[start:end]
    
    keyboard = []
    for i, track in enumerate(page_results, start=start + 1):
        clean_t = clean_title(track['title'])
        dur = track.get('duration', 0)
        dur_str = f"{int(dur)//60}:{int(dur)%60:02d}" if dur else "??:??"
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
        per_page = 15
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
                filepath = await asyncio.wait_for(asyncio.to_thread(download_soundcloud, url), timeout=90)
                if filepath and os.path.exists(filepath):
                    try:
                        with open(filepath, 'rb') as f:
                            await query.message.reply_audio(audio=f, title=os.path.basename(filepath).rsplit('.', 1)[0], caption="🎵 Держи!")
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
                await status_msg.edit_text("❌ Загрузка заняла слишком много времени.")
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
    print("✅ SoundCloud бот запущен на Render через Webhook!")
    app = Application.builder().token(TOKEN).read_timeout(120).write_timeout(120).connect_timeout(120).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://music-bot-ai.onrender.com") + "/webhook"
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path="webhook",
        webhook_url=webhook_url,
        secret_token="mysecret123"
    )

if __name__ == "__main__":
    main()
