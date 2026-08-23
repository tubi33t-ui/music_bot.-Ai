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

# Словарь популярных опечаток и транслита (расширенный ИИ)
def fix_typos(query):
    q = query.lower()
    replacements = {
        'sleer': 'slayer',
        'slayer': 'slayer',
        'mettalica': 'metallica',
        'металлика': 'metallica',
        'металлика мастер оф папетс': 'metallica master of puppets',
        'мастер оф папетс': 'master of puppets',
        'кипелов': 'kipelov',
        'король и шут': 'korol i shut',
        'raining blood': 'raining blood',
        'ентер сендмен': 'enter sandman',
        'numb': 'numb',
    }
    for typo, correct in replacements.items():
        if typo in q:
            q = q.replace(typo, correct)
    return q

def translit(text):
    mapping = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'}
    return ''.join(mapping.get(ch, ch) for ch in text.lower())

def clean_title(title):
    title = re.sub(r'\s*[\(\[][^)\]]*(official|audio|video|lyrics|hq|hd|remaster|clip|клип|официальный|аудио|видео|текст)[^)\]]*[\)\]]', '', title, flags=re.IGNORECASE)
    return title.strip(' -–—,|')

def generate_smart_queries(query):
    q = fix_typos(query).strip()
    variants = [q]
    if translit(q) != q:
        variants.append(translit(q))
    if len(q.split()) >= 2:
        variants.extend([f"{q} cover", f"{q} remix"])
    return list(dict.fromkeys(variants))

def search_soundcloud(query, limit=10):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    all_results = []
    for v in generate_smart_queries(query):
        try:
            ydl_opts = {
                'quiet': True, 'no_warnings': True, 'extract_flat': True,
                'headers': headers, 'socket_timeout': 5,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f'scsearch:{limit}:{v}', download=False)
                for entry in info.get('entries', []):
                    title = entry.get('title', 'Без названия')
                    duration = int(entry.get('duration') or 0)
                    url = entry.get('url') or entry.get('webpage_url')
                    if url:
                        all_results.append({'title': title, 'url': url, 'duration': duration, 'source': 'soundcloud'})
        except:
            continue
    final = []
    seen = set()
    for r in all_results:
        if r['url'] not in seen:
            seen.add(r['url'])
            final.append(r)
    return final[:limit]

def search_youtube_links(query, limit=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    try:
        fixed_query = fix_typos(query)
        ydl_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True, 'default_search': f'ytsearch{limit}:', 'headers': headers}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(fixed_query, download=False)
            entries = info.get('entries', [])
            results = []
            for entry in entries:
                title = entry.get('title', 'Без названия')
                duration = int(entry.get('duration') or 0)
                url = f"https://youtube.com/watch?v={entry.get('id')}"
                results.append({'title': title, 'url': url, 'duration': duration, 'source': 'youtube'})
            return results
    except:
        return []

async def mega_search(query):
    loop = asyncio.get_event_loop()
    sc_task = loop.run_in_executor(None, search_soundcloud, query, 10)
    yt_task = loop.run_in_executor(None, search_youtube_links, query, 3)
    sc_results, yt_results = await asyncio.gather(sc_task, yt_task)
    return sc_results + yt_results

def download_soundcloud(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    try:
        ydl_opts = {'format': 'bestaudio/best', 'outtmpl': 'downloads/%(title)s.%(ext)s', 'quiet': True, 'no_warnings': True, 'headers': headers, 'socket_timeout': 30, 'retries': 5, 'fragment_retries': 5}
        os.makedirs('downloads', exist_ok=True)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        audio_files = glob.glob('downloads/*.mp3') + glob.glob('downloads/*.m4a') + glob.glob('downloads/*.opus') + glob.glob('downloads/*.webm')
        if audio_files:
            return max(audio_files, key=os.path.getmtime)
    except Exception as e:
        logging.error(f"Ошибка скачивания: {e}")
    return None

async def start(update, context):
    await update.message.reply_text("🎵 *МЕГА БОТ*\n\nНапиши исполнителя, исправит опечатки!\n_Если не может скачать - даст ссылку на YouTube._", parse_mode='Markdown')

async def handle_message(update, context):
    query = update.message.text.strip()
    old_msg_id = context.user_data.pop('last_search_msg_id', None)
    if old_msg_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_msg_id)
        except: pass

    msg = await update.message.reply_text(f"🧠 Ищу *{query}*...", parse_mode='Markdown')
    results = await mega_search(query)
    context.user_data['last_search_msg_id'] = msg.message_id

    if not results:
        await msg.edit_text(f"❌ По запросу *{query}* ничего не нашлось. Проверь название!", parse_mode='Markdown')
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
        source_icon = "☁️" if track.get('source') == 'soundcloud' else "▶️"
        label = f"{source_icon} {i}. {clean_t[:30]} - {dur_str}"
        if len(label) > 60: label = label[:57] + '...'
        
        if track.get('source') == 'youtube':
            keyboard.append([InlineKeyboardButton(label, url=track['url'])])
        else:
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
        
        if track.get('source') == 'youtube':
            await query.message.reply_text(f"▶️ [Открыть оригинал на YouTube]({url})", parse_mode='Markdown', disable_web_page_preview=True)
            return

        status_msg = await query.message.reply_text(f"⏳ Качаю *{title}*...", parse_mode='Markdown')
        async def download_and_send():
            try:
                filepath = await asyncio.wait_for(asyncio.to_thread(download_soundcloud, url), timeout=90)
                if filepath and os.path.exists(filepath):
                    with open(filepath, 'rb') as f:
                        await query.message.reply_audio(audio=f, title=os.path.basename(filepath).rsplit('.', 1)[0], caption="🎵 Держи!")
                    os.remove(filepath)
                    await status_msg.delete()
                    await show_page(update, context)
                else:
                    await status_msg.edit_text(f"❌ Не удалось скачать.\n▶️ [Открыть на YouTube]({url})", parse_mode='Markdown', disable_web_page_preview=True)
            except asyncio.TimeoutError:
                await status_msg.edit_text("⏱️ Слишком долго.")
        task = asyncio.create_task(download_and_send())
        context.user_data['active_task'] = task

def main():
    print("✅ МЕГА БОТ запущен!")
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
