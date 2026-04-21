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
# CONFIGURATION - YOUR CREDENTIALS ARE HERE
# ============================================
TELEGRAM_BOT_TOKEN = "8674447276:AAFxI_Wlu-Qxa3CC07rAzQYD0ZMh6Nj0FSo"
GOOGLE_CLIENT_ID = "61481650487-83cot93su80e39ik9dgakfj3msggj1tc.apps.googleusercontent.com"
YOUR_APP_URL = "https://telegramyoutubebot.onrender.com"

# The client_secrets.json file must contain the full OAuth client secret JSON.
# You will upload this as a Secret File on Render (see instructions below).
GOOGLE_CLIENT_SECRETS_FILE = "client_secrets.json"

# ============================================
# LOGGING SETUP
# ============================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK APP INITIALIZATION
# ============================================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-to-a-random-string-in-render-env")

# ============================================
# TELEGRAM BOT INITIALIZATION
# ============================================
telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# ============================================
# DATABASE FUNCTIONS (SQLite)
# ============================================
def init_db():
    """Create the users table if it doesn't exist."""
    conn = sqlite3.connect('users.db')
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
    """Store or update a user's info in the database."""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    creds_json = credentials.to_json()
    c.execute('''
        INSERT OR REPLACE INTO users (telegram_id, youtube_channel_id, youtube_channel_title, access_token, refresh_token)
        VALUES (?, ?, ?, ?, ?)
    ''', (telegram_id, channel_id, channel_title, creds_json, credentials.refresh_token))
    conn.commit()
    conn.close()
    logger.info(f"User {telegram_id} added/updated in DB.")

def get_user_credentials(telegram_id):
    """Retrieve a user's OAuth credentials from the database."""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT access_token, refresh_token FROM users WHERE telegram_id = ?', (telegram_id,))
    row = c.fetchone()
    conn.close()
    if row:
        creds_json, refresh_token = row
        creds_data = json.loads(creds_json)
        return Credentials.from_authorized_user_info(info=creds_data)
    return None

def get_all_users():
    """Retrieve all linked users."""
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT telegram_id, youtube_channel_id, youtube_channel_title FROM users')
    rows = c.fetchall()
    conn.close()
    return rows

# Initialize the database on startup
init_db()

# ============================================
# GOOGLE OAUTH HELPER FUNCTIONS
# ============================================
def get_google_auth_url(telegram_id):
    """Generate the URL that the user must visit to authorize the app."""
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl'],
        redirect_uri=f"{YOUR_APP_URL}/callback"
    )
    auth_url, state = flow.authorization_url(
        prompt='consent',
        access_type='offline',
        include_granted_scopes='true',
        state=str(telegram_id)  # Pass telegram_id in state to identify user on callback
    )
    return auth_url

# ============================================
# FLASK ROUTES
# ============================================
@app.route('/')
def index():
    return "Telegram YouTube Bot is running!"

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handle incoming updates from Telegram."""
    if telegram_app:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        await telegram_app.process_update(update)
    return 'ok'

@app.route('/callback')
def google_callback():
    """Handle the redirect from Google after user authorization."""
    # Retrieve telegram_id from state parameter
    state = request.args.get('state')
    if not state:
        return "<h1>Error</h1><p>No state parameter found.</p>", 400
    try:
        telegram_id = int(state)
    except ValueError:
        return "<h1>Error</h1><p>Invalid state parameter.</p>", 400

    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl'],
        redirect_uri=f"{YOUR_APP_URL}/callback"
    )

    # Exchange the authorization code for credentials
    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials

    # Get the user's YouTube channel info
    try:
        youtube = build('youtube', 'v3', credentials=credentials)
        request_obj = youtube.channels().list(
            part="snippet",
            mine=True
        )
        response = request_obj.execute()

        if response['items']:
            channel_id = response['items'][0]['id']
            channel_title = response['items'][0]['snippet']['title']

            # Save to database
            add_or_update_user(telegram_id, credentials, channel_id, channel_title)

            # Notify the user on Telegram
            async def send_success_message():
                await telegram_app.bot.send_message(
                    chat_id=telegram_id,
                    text=f"✅ YouTube account '{channel_title}' connected successfully! You can now use /channels in the bot."
                )
            asyncio.run(send_success_message())

            return "<h1>Success!</h1><p>Your YouTube account has been connected. You can close this window and return to Telegram.</p>"
        else:
            return "<h1>Error</h1><p>Could not find a YouTube channel associated with this account.</p>", 400
    except Exception as e:
        logger.error(f"Error in OAuth callback: {e}")
        return "<h1>Error</h1><p>Something went wrong. Please try again.</p>", 500

# ============================================
# TELEGRAM BOT COMMAND HANDLERS
# ============================================
async def start(update: Update, context):
    """Send a message when the command /start is issued."""
    user = update.effective_user
    telegram_id = user.id

    credentials = get_user_credentials(telegram_id)
    if credentials:
        await update.message.reply_text("You're already connected! Use /channels to see the list.")
    else:
        auth_url = get_google_auth_url(telegram_id)
        keyboard = [[InlineKeyboardButton("🔗 Connect YouTube", url=auth_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Hi {user.mention_html()}! Click the button below to securely connect your YouTube account.",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

async def channels(update: Update, context):
    """Display the list of all connected channels with Subscribe buttons."""
    user = update.effective_user
    telegram_id = user.id

    if not get_user_credentials(telegram_id):
        await update.message.reply_text("You need to connect your YouTube account first! Use /start.")
        return

    users = get_all_users()
    if not users:
        await update.message.reply_text("No channels have been connected yet.")
        return

    keyboard = []
    for other_telegram_id, channel_id, channel_title in users:
        if other_telegram_id == telegram_id:
            continue  # Don't show the user their own channel
        button = InlineKeyboardButton(
            f"Subscribe to {channel_title}",
            callback_data=f"sub_{channel_id}"
        )
        keyboard.append([button])

    if not keyboard:
        await update.message.reply_text("No other channels to subscribe to yet.")
    else:
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Click a button to subscribe:", reply_markup=reply_markup)

async def button_callback(update: Update, context):
    """Handle the 'Subscribe' button clicks."""
    query = update.callback_query
    await query.answer()

    telegram_id = query.from_user.id
    data = query.data

    if data.startswith("sub_"):
        target_channel_id = data[4:]
        credentials = get_user_credentials(telegram_id)

        if not credentials:
            await query.edit_message_text("Please connect your YouTube account first using /start.")
            return

        try:
            youtube = build('youtube', 'v3', credentials=credentials)
            request_body = {
                "snippet": {
                    "resourceId": {
                        "kind": "youtube#channel",
                        "channelId": target_channel_id
                    }
                }
            }
            response = youtube.subscriptions().insert(
                part="snippet",
                body=request_body
            ).execute()

            await query.edit_message_text(f"✅ Successfully subscribed to the channel!")
            logger.info(f"User {telegram_id} subscribed to channel {target_channel_id}")

        except HttpError as e:
            error_message = f"❌ Failed to subscribe: {e}"
            await query.edit_message_text(error_message)
            logger.error(f"Subscription error for user {telegram_id}: {e}")

# ============================================
# REGISTER TELEGRAM HANDLERS
# ============================================
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("channels", channels))
telegram_app.add_handler(CallbackQueryHandler(button_callback))

# ============================================
# MAIN ENTRY POINT (for local development)
# ============================================
if __name__ == '__main__':
    # This block runs only when executing `python app.py` locally.
    # On Render, Gunicorn uses the `app` object directly.
    logger.info("Starting Flask server locally...")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
