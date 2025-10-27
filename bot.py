import logging
import os
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import json

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
MAIN_CHANNEL_ID = -1002570721543  # Replace with your main channel ID (https://t.me/MVPMCC)
REQUEST_GROUP_ID = -1002686709725  # Replace with your request group ID (https://t.me/moviesversebdreq)
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Movie database (stores movie titles and their message IDs from main channel)
MOVIES_DB = {}
SEARCH_CACHE = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler"""
    await update.message.reply_text(
        "🎬 Movie Search Bot Started!\n\n"
        "Send movie names in the request group and I'll search for them in our collection.\n"
        "Admin: Use /sync to update movie database from main channel."
    )

async def sync_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to sync movies from main channel"""
    user_id = update.effective_user.id
    
    # Check if user is admin (you can add admin IDs here)
    ADMIN_IDS = [6643046428]  # Replace with actual admin IDs
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return
    
    try:
        msg = await update.message.reply_text("🔄 Syncing movies from main channel...")
        
        # Get last 1000 messages from main channel
        messages = []
        async for message in context.bot.get_chat(MAIN_CHANNEL_ID).get_messages(limit=1000):
            if message.text:
                messages.append(message)
        
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
        
        await msg.edit_text(f"✅ Synced {movie_count} movies from main channel!")
        logger.info(f"Synced {movie_count} movies")
        
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
    
    # Show searching message
    searching_msg = await update.message.reply_text(
        f"🔍 Searching for '{movie_name}'...",
        reply_to_message_id=update.message.message_id
    )
    
    try:
        # Search in database
        results = []
        for db_title, movie_info in MOVIES_DB.items():
            if movie_name in db_title or db_title in movie_name:
                results.append((db_title, movie_info))
        
        # Also do fuzzy search
        if not results:
            for db_title, movie_info in MOVIES_DB.items():
                similarity = calculate_similarity(movie_name, db_title)
                if similarity > 0.6:
                    results.append((db_title, movie_info))
        
        if results:
            # Found movies
            response_text = f"✅ Found {len(results)} result(s) for '{movie_name}':\n\n"
            
            keyboard = []
            for idx, (title, info) in enumerate(results[:5]):  # Show top 5 results
                response_text += f"{idx + 1}. {title}\n"
                
                # Create button to view in main channel
                keyboard.append([
                    InlineKeyboardButton(
                        f"📥 Get {idx + 1}",
                        url=f"https://t.me/MVPMCC/{info['message_id']}"
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
                f"📝 Please ask admin with the correct movie name and year.\n"
                f"👤 Requested by: {update.effective_user.mention_html()}",
                parse_mode='HTML'
            )
            
    except Exception as e:
        logger.error(f"Error searching movie: {e}")
        await searching_msg.edit_text(f"❌ Error: {str(e)}")

def calculate_similarity(s1: str, s2: str) -> float:
    """Calculate similarity between two strings (simple Levenshtein-like)"""
    s1 = s1.lower().strip()
    s2 = s2.lower().strip()
    
    if s1 == s2:
        return 1.0
    
    # Check if one contains the other
    if s1 in s2 or s2 in s1:
        return 0.8
    
    # Simple character matching
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

Main Channel: https://t.me/MVPMCC
Request Group: https://t.me/moviesversebdreq
    """
    await update.message.reply_text(help_text)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

def main() -> None:
    """Start the bot"""
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
    
    # Start bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
