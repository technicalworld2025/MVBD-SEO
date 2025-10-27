import logging
import os
import re
import asyncio
import json
import signal
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.error import TelegramError
from flask import Flask, request
import threading

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
REQUEST_GROUP_ID = -1002686709725  # Request group
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://mvbd-seo.onrender.com')
ADMIN_IDS = [6643046428]  # Replace with actual admin IDs
MOVIES_DB_FILE = 'movies.json'

WAITING_FOR_MOVIE_NAME = 1
WAITING_FOR_MOVIE_LINK = 2

app = Flask(__name__)
application = None
app_loop = None

def load_movies_db():
    """Load movies from JSON file"""
    if os.path.exists(MOVIES_DB_FILE):
        try:
            with open(MOVIES_DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading movies: {e}")
            return {}
    return {}

def save_movies_db(movies_db):
    """Save movies to JSON file"""
    try:
        with open(MOVIES_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(movies_db, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(movies_db)} movies to database")
    except Exception as e:
        logger.error(f"Error saving movies: {e}")

MOVIES_DB = load_movies_db()

@app.route('/')
def health_check():
    return 'Bot is running', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), app_loop)
        return 'ok', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'error', 500

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler"""
    await update.message.reply_text(
        "🎬 Movie Search Bot Started!\n\n"
        "📝 Send movie names in the request group and I'll search for them.\n"
        "👨‍💼 Admin: Use /sync to add movies to database."
    )

async def sync_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to add movies manually"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।\n❌ You don't have permission to use this command.")
        return
    
    await update.message.reply_text(
        "🎬 মুভি যোগ করুন / Add Movie\n\n"
        "মুভির নাম লিখুন / Enter movie name:"
    )
    
    return WAITING_FOR_MOVIE_NAME

async def receive_movie_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive movie name from admin"""
    movie_name = update.message.text.strip().lower()
    
    if len(movie_name) < 2:
        await update.message.reply_text("❌ মুভির নাম খুব ছোট / Movie name too short")
        return WAITING_FOR_MOVIE_NAME
    
    context.user_data['movie_name'] = movie_name
    
    await update.message.reply_text(
        f"✅ মুভি নাম: {movie_name}\n\n"
        f"এখন লিংক পাঠান / Now send the movie link:"
    )
    
    return WAITING_FOR_MOVIE_LINK

async def receive_movie_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive movie link from admin"""
    movie_link = update.message.text.strip()
    movie_name = context.user_data.get('movie_name')
    
    if not (movie_link.startswith('http://') or movie_link.startswith('https://')):
        await update.message.reply_text(
            "❌ অবৈধ লিংক / Invalid link\n"
            "লিংক http:// বা https:// দিয়ে শুরু হতে হবে / Link must start with http:// or https://"
        )
        return WAITING_FOR_MOVIE_LINK
    
    MOVIES_DB[movie_name] = {
        'link': movie_link,
        'added_by': update.effective_user.username or update.effective_user.first_name,
        'date': datetime.now().isoformat()
    }
    
    save_movies_db(MOVIES_DB)
    
    await update.message.reply_text(
        f"✅ মুভি যোগ করা হয়েছে / Movie added!\n\n"
        f"📽️ নাম / Name: {movie_name}\n"
        f"🔗 লিংক / Link: {movie_link}\n\n"
        f"📊 মোট মুভি / Total movies: {len(MOVIES_DB)}"
    )
    
    return ConversationHandler.END

async def cancel_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel sync operation"""
    await update.message.reply_text("❌ বাতিল করা হয়েছে / Cancelled")
    return ConversationHandler.END

async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Search for movie in request group"""
    
    # Only respond in request group
    if update.effective_chat.id != REQUEST_GROUP_ID:
        return
    
    movie_name = update.message.text.strip().lower()
    
    # Ignore commands
    if movie_name.startswith('/'):
        return
    
    # Ignore very short queries
    if len(movie_name) < 2:
        return
    
    searching_msg = await update.message.reply_text(
        f"🔍 Searching for '{movie_name}'...\n⏳ Searching... 5 seconds",
        reply_to_message_id=update.message.message_id
    )
    
    try:
        await asyncio.sleep(5)
        
        results = []
        
        # First pass: Exact and partial matches
        for db_title, movie_info in MOVIES_DB.items():
            if movie_name in db_title:
                results.append((db_title, movie_info, 1.0))
            elif db_title in movie_name:
                results.append((db_title, movie_info, 0.9))
        
        # Second pass: Fuzzy search if no exact matches
        if not results:
            for db_title, movie_info in MOVIES_DB.items():
                similarity = calculate_similarity(movie_name, db_title)
                if similarity > 0.6:
                    results.append((db_title, movie_info, similarity))
        
        # Sort by similarity score
        results.sort(key=lambda x: x[2], reverse=True)
        
        if results:
            response_text = f"✅ Found {len(results)} result(s) for '{movie_name}':\n\n"
            
            keyboard = []
            for idx, (title, info, score) in enumerate(results[:5]):
                response_text += f"{idx + 1}. {title}\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"📥 Get Movie {idx + 1}",
                        url=info['link']
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await searching_msg.edit_text(
                response_text + f"\n👤 Requested by: {update.effective_user.mention_html()}",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            
        else:
            await searching_msg.edit_text(
                f"❌ '{movie_name}' আমাদের ডাটাবেসে পাওয়া যায়নি।\n"
                f"❌ '{movie_name}' not found in our database.\n\n"
                f"📝 অ্যাডমিনের অপেক্ষা করুন / Please wait for admin.\n"
                f"💾 আমাদের কাছে {len(MOVIES_DB)} টি মুভি আছে / We have {len(MOVIES_DB)} movies.\n"
                f"👤 অনুরোধকারী / Requested by: {update.effective_user.mention_html()}",
                parse_mode='HTML'
            )
            
    except Exception as e:
        logger.error(f"Error searching movie: {e}")
        await searching_msg.edit_text(f"❌ Error: {str(e)}")

def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate similarity between two strings"""
    s1 = s1.lower().strip()
    s2 = s2.lower().strip()
    
    if s1 == s2:
        return 1.0
    
    if s1 in s2 or s2 in s1:
        return 0.8
    
    matches = sum(1 for c in s1 if c in s2)
    return matches / max(len(s1), len(s2))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Help command"""
    help_text = """
🎬 Movie Search Bot Help

📋 Commands:
/start - Start the bot
/help - Show this help message
/sync - Add movies to database (Admin only)

📝 How to use:
1. Send a movie name in the request group
2. Bot will search in database
3. Click the button to get the movie

👨‍💼 Admin:
Use /sync to add new movies with links
    """
    await update.message.reply_text(help_text)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

async def setup_webhook(app_instance):
    """Setup webhook for Telegram"""
    try:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        await app_instance.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")

async def initialize_bot():
    """Initialize the bot application"""
    try:
        await application.initialize()
        await application.start()
        logger.info("Bot application initialized and started")
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        raise

async def shutdown_bot():
    """Gracefully shutdown the bot"""
    try:
        logger.info("Shutting down bot...")
        await application.stop()
        logger.info("Bot stopped successfully")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    asyncio.run_coroutine_threadsafe(shutdown_bot(), app_loop)

def main() -> None:
    """Start the bot"""
    global application, app_loop
    
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    sync_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("sync", sync_movies)],
        states={
            WAITING_FOR_MOVIE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_movie_name)],
            WAITING_FOR_MOVIE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_movie_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel_sync)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(sync_conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    logger.info("Setting up webhook...")
    
    app_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(app_loop)
    
    # Initialize bot
    app_loop.run_until_complete(initialize_bot())
    app_loop.run_until_complete(setup_webhook(application))
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start Flask app
    logger.info("Starting Flask server on port 5000...")
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        app_loop.run_until_complete(shutdown_bot())
        app_loop.close()
        logger.info("Application closed")

if __name__ == '__main__':
    main()
