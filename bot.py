import os
import logging
import glob
import asyncio
import re
import json
import time
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

# --- СЛОВАРЬ ОПЕЧАТОК И ТРАНСЛИТА ---
def translit(text):
    mapping = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'}
    return ''.join(mapping.get(ch, ch) for ch in text.lower())

def fix_query(query):
    q = query.lower().strip()
    fixes = {
        'sleer': 'slayer', 'металлика': 'metallica', 'ария': 'aria', 
        'мастер оф папетс': 'master of puppets', 'кипелов': 'kipelov',
        'король и шут': 'korol i shut', 'энтер сендмен': 'enter sandman'
    }
    for typo, correct in fixes.items():
        q = q.replace(typo, correct)
    
    # Если запрос на русском, добавляем транслит
    variants = [q]
    if translit(q) != q:
        variants.append(translit(q))
    return list(dict.fromkeys(variants))

def clean_title(title):
    title = re.sub(r'\s*[\(\[][^)\]]*(official|audio|video|lyrics|hq|hd|remaster|clip|клип|официальный|аудио|видео|текст)[^)\]]*[\)\]]', '', title, flags=re.IGNORECASE)
    return title.strip(' -–—,|')

# --- УРОВЕНЬ 1: ПРЯМОЙ ПАРСИНГ HTML (Молниеносный) ---
def direct_parse_search(query, limit=30):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
        }
        url = f'https://www.youtube.com/results?search_query={query}'
        response = requests.get(url, headers=headers, timeout=5)
        html = response.text

        match = re.search(r'var ytInitialData = (\{.*?\});', html)
        if not match: return None

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
        print(f"Парсинг упал: {e}")
        return None

# --- УРОВЕНЬ 2: yt-dlp (Клиенты web и web_music) ---
def yt_dlp_search(query, limit=30):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    # Пробуем разные клиенты
    for client in ['web', 'web_music']:
        try:
            ydl_opts = {
                'quiet': True, 'no_warnings': True, 'extract_flat': True,
                'default_search': f'ytsearch{limit}:',
                'headers': headers,
                'socket_timeout': 10,
                'extractor_args': {'youtube': {'player_client': [client]}}
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                entries = info.get('entries', [])
                if entries:
                    results = []
                    for entry in entries:
                        title = entry.get('title', 'Без названия')
                        duration = int(entry.get('duration') or 0)
                        url = entry.get('url') or f"https://youtube.com/watch?v={entry.get('id')}"
                        if duration < 30 or duration > 600: continue
                        results.append({'title': title, 'url': url, 'duration': duration})
                    if results: return results
        except Exception as e:
            print(f"yt-dlp ({client}) упал: {e}")
            continue
    return []

# --- МЕГА-ПОИСК (Запускаем всё вместе) ---
def mega_search(query, limit=30):
    # Генерируем варианты (русский + английский)
    all_results = []
    for v in fix_query(query):
        # Уровень 1: Прямой парсинг
        direct = direct_parse_search(v, limit)
        if direct:
            all_results.extend(direct)
        
        # Уровень 2: yt-dlp
        dlp = yt_dlp_search(v, limit)
        if dlp:
            all_results.extend(dlp)
            
        time.sleep(0.3) # Небольшая задержка, чтобы не забанили

    # Убираем дубликаты
    final, seen = [], set()
    for r in all_results:
        if r['url'] not in seen:
            seen.add(r['url'])
            final.append(r)
    
    # Если ничего не нашли, возвращаем пустой список
    return final[:limit]

# --- СКАЧИВАНИЕ (Пробуем разные клиенты) ---
def download_youtube(url):
    clients = ['web', 'tv', 'web_music']
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    for client in clients:
        try:
            ydl_opts = {
                'format': '140/bestaudio/best',
                'outtmpl': 'downloads/%(title)s.%(ext)s',
                'quiet': True, 'no_warnings': True,
                'headers': headers,
                'socket_timeout': 30,
                'retries': 5,
                'fragment_retries': 5,
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

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update, context):
    await update.message.reply_text(
        "🎵 *МЕГА-БОТ* \n\n"
        "Включаю тройной поиск (HTML + Web + Music).\n"
        "Понимает русский и английский!\n"
        "_Пример: металлика, ария, enter sandman_\n\n"
        "Если не скачивает - даст ссылку на YouTube!",
        parse_mode='Markdown'
    )

async def handle_message(update, context):
    query = update.message.text.strip()
    
    old_msg = context.user_data.pop('last_search_msg_id', None)
    if old_msg:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_msg)
        except: pass

    msg = await update.message.reply_text(f"🧠 Запускаю МЕГА-поиск на *{query}*...", parse_mode='Markdown')
    
    # ГЛАВНОЕ: Запускаем мега-поиск в отдельном потоке (без await, без ошибок)
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, mega_search, query, 30)
    
    context.user_data['last_search_msg_id'] = msg.message_id

    if not results:
        await msg.edit_text("❌ YouTube жестко блокирует IP Render для этого запроса. Попробуй еще раз через минуту или напиши на английском!", parse_mode='Markdown')
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
                    await status_msg.edit_text(f"❌ Render блокирует скачивание.\n[Открыть оригинал на YouTube]({url})", parse_mode='Markdown', disable_web_page_preview=True)
            except asyncio.TimeoutError:
                await status_msg.edit_text(f"⏱️ Слишком долго.\n[Открыть на YouTube]({url})", parse_mode='Markdown', disable_web_page_preview=True)

        task = asyncio.create_task(download_and_send())
        context.user_data['active_task'] = task

def main():
    print("✅ МЕГА-БОТ запущен!")
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
