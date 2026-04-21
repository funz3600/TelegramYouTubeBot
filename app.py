import os
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import CommandHandler, MessageHandler, filters, ApplicationBuilder
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

TELEGRAM_BOT_TOKEN = "HTTP API:8674447276:AAFxI_Wlu-Qxa3CC07rAzQYD0ZMh6Nj0FSo"
GOOGLE_CLIENT_SECRETS_FILE = "61481650487-83cot93su80e39ik9dgakfj3msggj1tc.apps.googleusercontent.com"

app = Flask(__name__)

@app.route("/", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.update_queue.put(update)
    return "OK"

def start(update, context):
    update.message.reply_text("Hello! I am your bot.")

def echo(update, context):
    update.message.reply_text(update.message.text)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

if __name__ == "__main__":
    app.run(port=8080)
