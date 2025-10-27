import logging
import os
import re
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import json
from flask import Flask, request
import threading

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
MAIN_CHANNEL_ID = -1003117912335  # User's main channel
REQUEST_GROUP_ID = -1002686709725  # Request group
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://your-app.onrender.com')

# Movie database
MOVIES_DB = {}
SEARCH_CACHE = {}

app = Flask(__name__)
application = None

@app.route('/')
def health_check():
    return 'Bot is running', 200

@app.route(f'/webhook/{BOT_TOKEN}', methods=['POST'])
async def webhook():
    """Handle incoming Telegram updates via webhook"""
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
        return 'ok', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'error', 500

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler"""
    await update.message.reply_text(
        "🎬 Movie Search Bot Started!\n\n"
        "Send movie names in the request group and I'll search for them in our collection.\n"
        "Admin: Use /sync to load movies from main channel."
    )

async def sync_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to sync movies from main channel"""
    user_id = update.effective_user.id
    
    ADMIN_IDS = [6643046428]  # Replace with actual admin IDs
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return
    
    try:
        msg = await update.message.reply_text("🔄 Syncing movies from main channel...")
        
        messages = []
        try:
            async for message in context.bot.get_chat_history(MAIN_CHANNEL_ID, limit=1000):
                if message.text:
                    messages.append(message)
        except Exception as e:
            logger.warning(f"get_chat_history error: {e}, trying alternative method")
            await msg.edit_text("⚠️ Trying alternative method to fetch messages...")
            try:
                # Alternative: fetch using get_chat_history with different approach
                chat = await context.bot.get_chat(MAIN_CHANNEL_ID)
                await msg.edit_text(f"Channel found: {chat.title}\nFetching messages...")
            except Exception as e2:
                logger.error(f"Alternative method failed: {e2}")
                await msg.edit_text(f"❌ Error: Bot needs admin access to channel. Error: {str(e2)}")
                return
        
        # Parse movie titles from messages
        movie_count = 0
        for message in messages:
            text = message.text
            # Extract movie title (adjust regex based on your format)
            match = re.search(r'📽️\s*(.+?)(?:\n|$)', text)
            if match:
                title = match.group(1).strip().lower()
                if title not in MOVIES_DB:
                    MOVIES_DB[title] = {
                        'message_id': message.message_id,
                        'full_text': text,
                        'date': message.date.isoformat()
                    }
                    movie_count += 1
        
        await msg.edit_text(f"✅ Synced {movie_count} movies from main channel!\n\nDatabase: {len(MOVIES_DB)} total movies")
        logger.info(f"Synced {movie_count} movies. Total: {len(MOVIES_DB)}")
        
    except Exception as e:
        logger.error(f"Error syncing movies: {e}")
        await update.message.reply_text(f"❌ Error syncing: {str(e)}")

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
        f"🔍 Searching for '{movie_name}'...",
        reply_to_message_id=update.message.message_id
    )
    
    try:
        await asyncio.sleep(5)
        
        results = []
        
        # First pass: Exact and partial matches
        for db_title, movie_info in MOVIES_DB.items():
            if movie_name in db_title:
                results.append((db_title, movie_info, 1.0))  # Exact match
            elif db_title in movie_name:
                results.append((db_title, movie_info, 0.9))  # Partial match
        
        # Second pass: Fuzzy search if no exact matches
        if not results:
            for db_title, movie_info in MOVIES_DB.items():
                similarity = calculate_similarity(movie_name, db_title)
                if similarity > 0.6:
                    results.append((db_title, movie_info, similarity))
        
        # Sort by similarity score
        results.sort(key=lambda x: x[2], reverse=True)
        
        if results:
            # Found movies
            response_text = f"✅ Found {len(results)} result(s) for '{movie_name}':\n\n"
            
            keyboard = []
            for idx, (title, info, score) in enumerate(results[:5]):  # Show top 5 results
                response_text += f"{idx + 1}. {title}\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"📥 Get Movie {idx + 1}",
                        url=f"https://t.me/c/1003117912335/{info['message_id']}"
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await searching_msg.edit_text(
                response_text + f"\n👤 Requested by: {update.effective_user.mention_html()}",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            
        else:
            # Not found
            await searching_msg.edit_text(
                f"❌ '{movie_name}' not found in our collection.\n\n"
                f"📝 Please ask admin with the correct movie name.\n"
                f"💾 We have {len(MOVIES_DB)} movies in database.\n"
                f"👤 Requested by: {update.effective_user.mention_html()}",
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

Commands:
/start - Start the bot
/help - Show this help message
/sync - Sync movies from main channel (Admin only)

How to use:
1. Send a movie name in the request group
2. Bot will search in main channel
3. Click the button to get the movie
    """
    await update.message.reply_text(help_text)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

async def setup_webhook(app_instance):
    """Setup webhook for Telegram"""
    try:
        webhook_url = f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"
        await app_instance.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to {webhook_url}")
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")

def main() -> None:
    """Start the bot"""
    global application
    
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("sync", sync_movies))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    logger.info("Setting up webhook...")
    asyncio.run(setup_webhook(application))
    
    # Start Flask app (handles webhook requests)
    logger.info("Starting Flask server on port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

if __name__ == '__main__':
    main()
