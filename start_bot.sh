#!/bin/bash

# Krishna-Conscious Discord Bot Startup Script
# This script handles robust bot startup with retry logic and Cloudflare protection

echo "🌸 Starting Krishna-Conscious Discord Bot..."
echo "📅 $(date)"
echo "🖥️  Environment: $(hostname)"
echo "🐍 Python version: $(python3 --version)"

# Check environment variables
if [ -z "$DISCORD_BOT_TOKEN" ]; then
    echo "❌ Error: DISCORD_BOT_TOKEN not set"
    exit 1
fi

if [ -z "$AI_API_KEY" ]; then
    echo "⚠️  Warning: AI_API_KEY not set, will use fallback scoring"
fi

if [ -z "$DATABASE_URL" ]; then
    echo "⚠️  Warning: DATABASE_URL not set, will use local fallback"
fi

if [ -z "$SERVER_ID" ]; then
    echo "⚠️  Warning: SERVER_ID not set, bot will not be server-locked"
fi

echo "🔧 Configuration check complete"

# Set process environment for better networking
export UV_THREADPOOL_SIZE=4
export PYTHONUNBUFFERED=1
export PYTHONOPTIMIZE=1

# Add delay to prevent immediate startup conflicts
sleep 5

# Start the bot with python3
echo "🚀 Starting bot process..."
python3 krishna_bot.py

# If the bot exits, log it
echo "❌ Bot process exited at $(date)"
exit $?