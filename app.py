import os
import logging
import json
import sqlite3
import asyncio
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ============================================
# CONFIGURATION
# ============================================
TELEGRAM_BOT_TOKEN = "8674447276:AAFxI_Wlu-Qxa3CC07rAzQYD0ZMh6Nj0FSo"
GOOGLE_CLIENT_ID = "61481650487-83cot93su80e39ik9dgakfj3msggj1tc.apps.googleusercontent.com"
YOUR_APP_URL = "https://telegramyoutubebot.onrender.com"
GOOGLE_CLIENT_SECRETS_FILE = "client_secrets.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# FLASK APP INITIALIZATION
# ============================================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret")

# ============================================
# TELEGRAM APPLICATION (with initialization)
# ============================================
telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

_app_initialized = False
_init_lock = asyncio.Lock()

async def ensure_initialized():
    global _app_initialized
    async with _init_lock:
        if not _app_initialized:
            await telegram_app.initialize()
            _app_initialized = True
            logger.info("Telegram application initialized")

# ============================================
# DATABASE FUNCTIONS
# ============================================
def init_db():
    conn = sqlite3.connect('users.db', timeout=10)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            youtube_channel_id TEXT,
            youtube_channel_title TEXT,
            access_token TEXT,
            refresh_token TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_or_update_user(telegram_id, credentials, channel_id, channel_title):
    conn = sqlite3.connect('users.db', timeout=10)
    c = conn.cursor()
    creds_json = credentials.to_json()
    c.execute('''
        INSERT OR REPLACE INTO users (telegram_id, youtube_channel_id, youtube_channel_title, access_token, refresh_token)
        VALUES (?, ?, ?, ?, ?)
    ''', (telegram_id, channel_id, channel_title, creds_json, credentials.refresh_token))
    conn.commit()
    conn.close()

def get_user_credentials(telegram_id):
    conn = sqlite3.connect('users.db', timeout=10)
    c = conn.cursor()
    c.execute('SELECT access_token, refresh_token FROM users WHERE telegram_id = ?', (telegram_id,))
    row = c.fetchone()
    conn.close()
    if row:
        creds_json, _ = row
        creds_data = json.loads(creds_json)
        return Credentials.from_authorized_user_info(info=creds_data)
    return None

def get_all_users():
    conn = sqlite3.connect('users.db', timeout=10)
    c = conn.cursor()
    c.execute('SELECT telegram_id, youtube_channel_id, youtube_channel_title FROM users')
    rows = c.fetchall()
    conn.close()
    return rows

init_db()

# ============================================
# GOOGLE OAUTH HELPERS
# ============================================
def get_google_auth_url(telegram_id):
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl'],
        redirect_uri=f"{YOUR_APP_URL}/callback"
    )
    auth_url, state = flow.authorization_url(
        prompt='consent',
        access_type='offline',
        include_granted_scopes='true',
        state=str(telegram_id)
    )
    return auth_url

# ============================================
# TELEGRAM BOT HANDLERS
# ============================================
async def start(update: Update, context):
    user = update.effective_user
    creds = get_user_credentials(user.id)
    if creds:
        await update.message.reply_text("You're already connected! Use /channels.")
    else:
        url = get_google_auth_url(user.id)
        btn = InlineKeyboardButton("🔗 Connect YouTube", url=url)
        await update.message.reply_text(
            f"Hi {user.mention_html()}! Click to connect:",
            reply_markup=InlineKeyboardMarkup([[btn]]),
            parse_mode='HTML'
        )

async def channels(update: Update, context):
    user = update.effective_user
    if not get_user_credentials(user.id):
        await update.message.reply_text("Connect first with /start.")
        return
    users = get_all_users()
    if not users:
        await update.message.reply_text("No channels connected yet.")
        return
    keyboard = []
    for tid, cid, ctitle in users:
        if tid == user.id:
            continue
        keyboard.append([InlineKeyboardButton(f"Subscribe to {ctitle}", callback_data=f"sub_{cid}")])
    if not keyboard:
        await update.message.reply_text("No other channels yet.")
    else:
        await update.message.reply_text("Click to subscribe:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    if data.startswith("sub_"):
        target = data[4:]
        creds = get_user_credentials(user_id)
        if not creds:
            await query.edit_message_text("Connect your account first with /start.")
            return
        try:
            youtube = build('youtube', 'v3', credentials=creds)
            youtube.subscriptions().insert(
                part="snippet",
                body={"snippet": {"resourceId": {"kind": "youtube#channel", "channelId": target}}}
            ).execute()
            await query.edit_message_text("✅ Subscribed!")
        except HttpError as e:
            await query.edit_message_text(f"❌ Failed: {e}")

# Register handlers
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("channels", channels))
telegram_app.add_handler(CallbackQueryHandler(button_callback))

# ============================================
# FLASK ROUTES
# ============================================
@app.route('/')
def index():
    return "Telegram YouTube Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Synchronous webhook that processes Telegram updates."""
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    
    async def process():
        await ensure_initialized()
        await telegram_app.process_update(update)
    
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run(process())
        else:
            loop.run_until_complete(process())
    except RuntimeError:
        asyncio.run(process())
    
    return 'ok'

@app.route('/callback')
def google_callback():
    state = request.args.get('state')
    if not state:
        return "<h1>Error</h1><p>No state parameter.</p>", 400
    try:
        telegram_id = int(state)
    except ValueError:
        return "<h1>Error</h1><p>Invalid state.</p>", 400

    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl'],
        redirect_uri=f"{YOUR_APP_URL}/callback"
    )
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    try:
        youtube = build('youtube', 'v3', credentials=credentials)
        resp = youtube.channels().list(part="snippet", mine=True).execute()
        if resp['items']:
            channel_id = resp['items'][0]['id']
            channel_title = resp['items'][0]['snippet']['title']
            add_or_update_user(telegram_id, credentials, channel_id, channel_title)

            # Send confirmation to Telegram
            async def send_msg():
                await telegram_app.bot.send_message(
                    chat_id=telegram_id,
                    text=f"✅ YouTube account '{channel_title}' connected successfully!"
                )
            asyncio.run(send_msg())

            return "<h1>Success!</h1><p>YouTube connected. Return to Telegram.</p>"
        else:
            return "<h1>Error</h1><p>No YouTube channel found.</p>", 400
    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        return "<h1>Error</h1><p>Something went wrong.</p>", 500

# ============================================
# MAIN (for local development)
# ============================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
