import os
import logging
import glob
import asyncio
import re
import time
import json
import subprocess
import requests
import yt_dlp
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

def fix_query(query):
    q = query.lower().strip()
    fixes = {'sleer': 'slayer', 'металлика': 'metallica', 'ария': 'aria', 'мастер оф папетс': 'master of puppets', 'кипелов': 'kipelov', 'энтер сендмен': 'enter sandman'}
    for typo, correct in fixes.items():
        q = q.replace(typo, correct)
    variants = [q]
    if translit(q) != q:
        variants.append(translit(q))
    return list(dict.fromkeys(variants))

# --- УРОВЕНЬ 1: ПРЯМОЙ ПАРСИНГ (для мгновенного поиска) ---
def direct_parse_search(query, limit=30):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'}
        response = requests.get(f'https://www.youtube.com/results?search_query={query}', headers=headers, timeout=5)
        html = response.text
        match = re.search(r'var ytInitialData = (\{.*?\});', html)
        if not match: return None
        data = json.loads(match.group(1))
        sections = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents']
        results = []
        for section in sections:
            if 'itemSectionRenderer' not in section: continue
            for item in section['itemSectionRenderer']['contents']:
                if 'videoRenderer' not in item: continue
                renderer = item['videoRenderer']
                video_id = renderer.get('videoId')
                title = ''.join(run['text'] for run in renderer.get('title', {}).get('runs', []))
                length_text = renderer.get('lengthText', {}).get('simpleText', '0:00')
                try:
                    parts = length_text.split(':')
                    duration = int(parts[0])*60 + int(parts[1]) if len(parts) == 2 else int(parts[0])
                except: duration = 0
                if duration < 30 or duration > 600: continue
                results.append({'title': title, 'url': f'https://youtube.com/watch?v={video_id}', 'duration': duration})
                if len(results) >= limit: return results
        return results if results else None
    except Exception as e:
        logging.error(f"Ошибка прямого парсинга: {e}")
        return None

# --- УРОВЕНЬ 2: УМНЫЙ ПОИСК через yt-dlp (массированный) ---
def yt_dlp_search(query, limit=30):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    for client in ['web', 'tv', 'web_music']:
        try:
            ydl_opts = {
                'quiet': True, 'no_warnings': True, 'extract_flat': True,
                'default_search': f'ytsearch{limit}:', 'headers': headers,
                'socket_timeout': 10,
                # Используем Rust POT Provider через CLI
                'extractor_args': {'youtube': {'player_client': [client], 'player_skip': 'webpage'}}
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
            logging.error(f"Ошибка yt-dlp (клиент {client}): {e}")
            continue
    return []

# --- УРОВЕНЬ 3: МЕНЕДЖЕР ПРОКСИ (для обхода блокировок) ---
def get_free_proxies():
    url = "https://raw.githubusercontent.com/proxygenerator1/ProxyGenerator/main/Stable/http.txt"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return [line.strip() for line in response.text.strip().split("\n") if line.strip()]
    except Exception: pass
    return ["103.152.112.162:80", "45.112.210.45:8080", "51.158.85.40:8811"]

# --- МЕГА-ПОИСК (Несколько уровней параллельно) ---
def mega_search(query, limit=30):
    all_results = []
    for v in fix_query(query):
        direct = direct_parse_search(v, limit)
        if direct: all_results.extend(direct)
        dlp = yt_dlp_search(v, limit)
        if dlp: all_results.extend(dlp)
    final, seen = [], set()
    for r in all_results:
        if r['url'] not in seen:
            seen.add(r['url'])
            final.append(r)
    return final[:limit]

# --- СКАЧИВАНИЕ (Живучее, с перебором прокси и клиентов) ---
def download_youtube(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    # Генерируем список прокси и добавляем "без прокси" (None) в конце
    proxy_list = get_free_proxies() + [None]
    
    for proxy in proxy_list:
        for client in ['web', 'tv']:
            try:
                ydl_opts = {
                    'format': '140/bestaudio/best',
                    'outtmpl': 'downloads/%(title)s.%(ext)s',
                    'quiet': True, 'no_warnings': True,
                    'headers': headers,
                    'geo_bypass': True,
                    'socket_timeout': 30,
                    'retries': 5,
                    'fragment_retries': 5,
                    'cookiefile': 'cookies.txt',
                    # Умная стратегия: отключаем web_safari при ошибке и добавляем pot-токены
                    'extractor_args': {'youtube': {'player_client': [client], 'player_skip': 'webpage'}}
                }
                if proxy:
                    ydl_opts['proxy'] = f'http://{proxy}'
                
                os.makedirs('downloads', exist_ok=True)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                audio_files = glob.glob('downloads/*.m4a') + glob.glob('downloads/*.opus') + glob.glob('downloads/*.webm')
                if audio_files:
                    return max(audio_files, key=os.path.getmtime)
            except Exception as e:
                logging.error(f"Ошибка (клиент {client}, прокси {proxy}): {e}")
                continue
    return None

# --- ОБРАБОТЧИКИ TELEGRAM ---
async def start(update, context):
    await update.message.reply_text("🎵 *МЕГА-БОТ 3.0*\n\nИщу на YouTube Music!\nПонимает транслит и тяжелые запросы.\n_Если не скачивает - даст ссылку._", parse_mode='Markdown')

async def handle_message(update, context):
    query = update.message.text.strip()
    old_msg = context.user_data.pop('last_search_msg_id', None)
    if old_msg:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_msg)
        except: pass
    msg = await update.message.reply_text(f"🧠 Запускаю МЕГА-поиск на *{query}*...", parse_mode='Markdown')
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, mega_search, query, 30)
    context.user_data['last_search_msg_id'] = msg.message_id
    if not results:
        await msg.edit_text("❌ YouTube жестко блокирует IP Render для этого запроса. Попробуй через минуту!", parse_mode='Markdown')
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
                filepath = await asyncio.wait_for(asyncio.to_thread(download_youtube, url), timeout=120)
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
    print("✅ МЕГА-БОТ 3.0 запущен!")
    app = Application.builder().token(TOKEN).read_timeout(120).write_timeout(120).connect_timeout(120).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://music-bot-ai.onrender.com") + "/webhook"
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 10000)), url_path="webhook", webhook_url=webhook_url, secret_token="mysecret123")

if __name__ == "__main__":
    main()
