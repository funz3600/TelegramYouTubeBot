import os
import logging
import json
import asyncio
import urllib.parse
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ChatMemberHandler
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ============================================
# CONFIGURATION (ALL YOUR DETAILS)
# ============================================
TELEGRAM_BOT_TOKEN = "8674447276:AAFxI_Wlu-Qxa3CC07rAzQYD0ZMh6Nj0FSo"
GOOGLE_CLIENT_ID = "61481650487-83cot93su80e39ik9dgakfj3msggj1tc.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
YOUR_APP_URL = "https://telegramyoutubebot.onrender.com"

# Your YouTube channel ID
AUTO_SUBSCRIBE_CHANNEL_ID = "UCNRXsU3P2cC2x4F5ngF9T0Q"

# Your Telegram group ID
GROUP_CHAT_ID = "-1003988510989"

# Your personal Telegram numeric admin ID
ADMIN_TELEGRAM_ID = 383722109

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "my-super-secret-flask-key-2024"

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

# ---------- DATABASE (PostgreSQL / SQLite) ----------
# Render will provide DATABASE_URL when you link a PostgreSQL service
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    """Returns a DB connection – PostgreSQL if available, else SQLite."""
    if DATABASE_URL:
        result = urllib.parse.urlparse(DATABASE_URL)
        import psycopg2
        conn = psycopg2.connect(
            host=result.hostname,
            port=result.port,
            dbname=result.path[1:],
            user=result.username,
            password=result.password,
            sslmode='require'
        )
        return conn
    else:
        import sqlite3
        conn = sqlite3.connect('users.db', timeout=10)
        return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        # PostgreSQL
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                youtube_channel_id TEXT,
                youtube_channel_title TEXT,
                access_token TEXT,
                refresh_token TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                subscriber_telegram_id BIGINT,
                target_channel_id TEXT,
                target_channel_title TEXT,
                timestamp TEXT
            )
        ''')
    else:
        # SQLite
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                youtube_channel_id TEXT,
                youtube_channel_title TEXT,
                access_token TEXT,
                refresh_token TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_telegram_id INTEGER,
                target_channel_id TEXT,
                target_channel_title TEXT,
                timestamp TEXT
            )
        ''')
    conn.commit()
    conn.close()

def add_or_update_user(telegram_id, credentials, channel_id, channel_title):
    conn = get_db_connection()
    c = conn.cursor()
    creds_json = credentials.to_json()
    if DATABASE_URL:
        c.execute('''
            INSERT INTO users (telegram_id, youtube_channel_id, youtube_channel_title, access_token, refresh_token)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
                youtube_channel_id = EXCLUDED.youtube_channel_id,
                youtube_channel_title = EXCLUDED.youtube_channel_title,
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token
        ''', (telegram_id, channel_id, channel_title, creds_json, credentials.refresh_token))
    else:
        c.execute('''
            INSERT OR REPLACE INTO users (telegram_id, youtube_channel_id, youtube_channel_title, access_token, refresh_token)
            VALUES (?, ?, ?, ?, ?)
        ''', (telegram_id, channel_id, channel_title, creds_json, credentials.refresh_token))
    conn.commit()
    conn.close()

def remove_user(telegram_id):
    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute('DELETE FROM users WHERE telegram_id = %s', (telegram_id,))
    else:
        c.execute('DELETE FROM users WHERE telegram_id = ?', (telegram_id,))
    conn.commit()
    conn.close()

def get_user_credentials(telegram_id):
    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute('SELECT access_token, refresh_token FROM users WHERE telegram_id = %s', (telegram_id,))
    else:
        c.execute('SELECT access_token, refresh_token FROM users WHERE telegram_id = ?', (telegram_id,))
    row = c.fetchone()
    conn.close()
    if row:
        creds_json, _ = row
        creds_data = json.loads(creds_json)
        return Credentials.from_authorized_user_info(info=creds_data)
    return None

def get_all_users():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT telegram_id, youtube_channel_id, youtube_channel_title FROM users')
    rows = c.fetchall()
    conn.close()
    return rows

def log_subscription(subscriber_telegram_id, target_channel_id, target_channel_title):
    conn = get_db_connection()
    c = conn.cursor()
    timestamp = datetime.utcnow().isoformat()
    if DATABASE_URL:
        c.execute('''
            INSERT INTO subscriptions (subscriber_telegram_id, target_channel_id, target_channel_title, timestamp)
            VALUES (%s, %s, %s, %s)
        ''', (subscriber_telegram_id, target_channel_id, target_channel_title, timestamp))
    else:
        c.execute('''
            INSERT INTO subscriptions (subscriber_telegram_id, target_channel_id, target_channel_title, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (subscriber_telegram_id, target_channel_id, target_channel_title, timestamp))
    conn.commit()
    conn.close()

def get_subscriptions_for_channel(channel_id):
    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute('SELECT subscriber_telegram_id FROM subscriptions WHERE target_channel_id = %s', (channel_id,))
    else:
        c.execute('SELECT subscriber_telegram_id FROM subscriptions WHERE target_channel_id = ?', (channel_id,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_total_subscriptions():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM subscriptions')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_most_popular_channel():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT target_channel_title, COUNT(*) as cnt FROM subscriptions GROUP BY target_channel_id ORDER BY cnt DESC LIMIT 1')
    row = c.fetchone()
    conn.close()
    return row if row else None

init_db()

# ---------- OAUTH HELPERS ----------
def get_client_config():
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{YOUR_APP_URL}/callback"]
        }
    }

def get_google_auth_url(telegram_id):
    flow = Flow.from_client_config(
        get_client_config(),
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

# ---------- BOT COMMAND HANDLERS ----------
async def start(update: Update, context):
    user = update.effective_user
    if get_user_credentials(user.id):
        await update.message.reply_text("✅ You're already connected! Use /channels to see the community list, or /help for all commands.")
    else:
        url = get_google_auth_url(user.id)
        btn = InlineKeyboardButton("🔗 Connect YouTube", url=url)
        await update.message.reply_text(
            f"Hi {user.mention_html()}! Click below to connect your YouTube account:",
            reply_markup=InlineKeyboardMarkup([[btn]]),
            parse_mode='HTML'
        )

async def help_command(update: Update, context):
    user = update.effective_user
    is_admin = (user.id == ADMIN_TELEGRAM_ID)
    text = (
        "📋 *Available commands:*\n"
        "/start - Connect your YouTube account\n"
        "/channels - See all connected channels and subscribe with one click\n"
        "/disconnect - Remove your YouTube connection from this bot\n"
        "/help - Show this help message\n"
    )
    if is_admin:
        text += (
            "\n🔧 *Admin commands:*\n"
            "/subscribers - View who subscribed to your channel via the bot\n"
            "/listusers - List all connected users\n"
            "/stats - Subscription statistics\n"
            "/broadcast <message> - Send message to all connected users\n"
            "/invite - Create a group invite link (use in the group)\n"
        )
    await update.message.reply_text(text, parse_mode='Markdown')

async def disconnect(update: Update, context):
    user = update.effective_user
    if get_user_credentials(user.id):
        remove_user(user.id)
        await update.message.reply_text("🔌 Your YouTube account has been disconnected from the bot.")
    else:
        await update.message.reply_text("You haven't connected a YouTube account yet.")

async def subscribers(update: Update, context):
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    subs = get_subscriptions_for_channel(AUTO_SUBSCRIBE_CHANNEL_ID)
    if not subs:
        await update.message.reply_text("No subscriptions recorded for your channel yet.")
        return
    mention_list = [f"• [User](tg://user?id={tid}) (ID: `{tid}`)" for tid in subs]
    await update.message.reply_text(
        f"📈 *Subscribers to your channel via the bot:* ({len(subs)} total)\n\n" + "\n".join(mention_list),
        parse_mode='Markdown'
    )

async def list_users(update: Update, context):
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    users = get_all_users()
    if not users:
        await update.message.reply_text("No connected users.")
        return
    lines = [f"• `{tid}` → {ctitle} (`{cid}`)" for tid, cid, ctitle in users]
    await update.message.reply_text(
        f"👥 *Connected Users ({len(users)}):*\n\n" + "\n".join(lines),
        parse_mode='Markdown'
    )

async def stats(update: Update, context):
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    total_users = len(get_all_users())
    total_subs = get_total_subscriptions()
    popular = get_most_popular_channel()
    popular_text = f"{popular[0]} ({popular[1]} subs)" if popular else "N/A"
    await update.message.reply_text(
        f"📊 *Statistics:*\n"
        f"• Connected users: {total_users}\n"
        f"• Total subscriptions made: {total_subs}\n"
        f"• Most popular channel: {popular_text}",
        parse_mode='Markdown'
    )

async def broadcast(update: Update, context):
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Admin only.")
        return
    msg = update.message.text.partition(' ')[2]
    if not msg:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    users = get_all_users()
    if not users:
        await update.message.reply_text("No connected users.")
        return
    success = 0
    for tid, _, _ in users:
        try:
            await context.bot.send_message(chat_id=tid, text=f"📢 *Message from Fun size:*\n{msg}", parse_mode='Markdown')
            success += 1
        except Exception as e:
            logger.warning(f"Broadcast to {tid} failed: {e}")
    await update.message.reply_text(f"✅ Broadcast sent to {success}/{len(users)} users.")

async def invite(update: Update, context):
    chat_id = update.effective_chat.id
    if chat_id != update.effective_user.id:  # in a group
        target = chat_id
    else:
        target = int(GROUP_CHAT_ID) if GROUP_CHAT_ID else None
    if not target:
        await update.message.reply_text("Please set GROUP_CHAT_ID or use this command inside the group.")
        return
    try:
        invite_link = await context.bot.create_chat_invite_link(chat_id=target, member_limit=0, creates_join_request=False)
        await update.message.reply_text(f"🔗 Invite link:\n{invite_link.invite_link}")
    except Exception as e:
        await update.message.reply_text(f"❌ Could not create invite link: {e}")

async def channels(update: Update, context):
    user = update.effective_user
    if not get_user_credentials(user.id):
        await update.message.reply_text("Connect your YouTube account first with /start.")
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
            await query.edit_message_text("Connect your YouTube account first with /start.")
            return
        try:
            youtube = build('youtube', 'v3', credentials=creds)
            youtube.subscriptions().insert(
                part="snippet",
                body={"snippet": {"resourceId": {"kind": "youtube#channel", "channelId": target}}}
            ).execute()
            target_title = next((ctitle for _, cid, ctitle in get_all_users() if cid == target), target)
            log_subscription(user_id, target, target_title)
            await query.edit_message_text("✅ Subscribed!")
        except HttpError as e:
            await query.edit_message_text(f"❌ Failed: {e}")

async def on_user_join(update: Update, context):
    chat_member = update.chat_member
    if chat_member.new_chat_member.status in ["member", "administrator"]:
        user = chat_member.new_chat_member.user
        tid = user.id
        creds = get_user_credentials(tid)
        if creds:
            try:
                youtube = build('youtube', 'v3', credentials=creds)
                youtube.subscriptions().insert(
                    part="snippet",
                    body={"snippet": {"resourceId": {"kind": "youtube#channel", "channelId": AUTO_SUBSCRIBE_CHANNEL_ID}}}
                ).execute()
                log_subscription(tid, AUTO_SUBSCRIBE_CHANNEL_ID, "Fun size")
                logger.info(f"Auto-subscribed {tid} to admin channel on join.")
            except HttpError as e:
                logger.warning(f"Auto-subscribe failed for {tid}: {e}")
        else:
            auth_url = get_google_auth_url(tid)
            btn = InlineKeyboardButton("🔗 Connect YouTube", url=auth_url)
            try:
                await context.bot.send_message(
                    chat_id=tid,
                    text="Welcome! Please connect your YouTube account to join the subscriber network.",
                    reply_markup=InlineKeyboardMarkup([[btn]])
                )
            except Exception as e:
                logger.error(f"Could not DM user {tid}: {e}")

# Register all handlers
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", help_command))
telegram_app.add_handler(CommandHandler("channels", channels))
telegram_app.add_handler(CommandHandler("disconnect", disconnect))
telegram_app.add_handler(CommandHandler("subscribers", subscribers))
telegram_app.add_handler(CommandHandler("listusers", list_users))
telegram_app.add_handler(CommandHandler("stats", stats))
telegram_app.add_handler(CommandHandler("broadcast", broadcast))
telegram_app.add_handler(CommandHandler("invite", invite))
telegram_app.add_handler(CallbackQueryHandler(button_callback))
telegram_app.add_handler(ChatMemberHandler(on_user_join, ChatMemberHandler.CHAT_MEMBER))

# ---------- FLASK ROUTES ----------
@app.route('/')
def index():
    return "Telegram YouTube Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
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

    flow = Flow.from_client_config(
        get_client_config(),
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

            # Auto-subscribe to admin channel
            try:
                youtube.subscriptions().insert(
                    part="snippet",
                    body={"snippet": {"resourceId": {"kind": "youtube#channel", "channelId": AUTO_SUBSCRIBE_CHANNEL_ID}}}
                ).execute()
                log_subscription(telegram_id, AUTO_SUBSCRIBE_CHANNEL_ID, "Fun size")
                logger.info(f"New user {telegram_id} auto-subscribed to admin channel.")
            except HttpError as e:
                logger.warning(f"Auto-subscribe failed: {e}")

            async def send_msg():
                await telegram_app.bot.send_message(
                    chat_id=telegram_id,
                    text=f"✅ YouTube account '{channel_title}' connected successfully!\n\n"
                         "You are now part of the Fun size subscriber network.\n"
                         "Use /channels to see the community.\n"
                         "Use /help for all commands."
                )
            asyncio.run(send_msg())

            return "<h1>Success!</h1><p>YouTube connected. Return to Telegram.</p>"
        else:
            return "<h1>Error</h1><p>No YouTube channel found.</p>", 400
    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        return "<h1>Error</h1><p>Something went wrong.</p>", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
