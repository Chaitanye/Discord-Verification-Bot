#!/usr/bin/env python3
"""
Enhanced startup script for Krishna Verification Bot with Cloudflare protection
"""

import os
import sys
import asyncio
import time
import random
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    """Enhanced startup with Cloudflare protection"""
    
    logger.info("🌸 Krishna Verification Bot - Enhanced Cloudflare Startup")
    logger.info("=" * 60)
    
    # Random startup delay (0-30 seconds) to avoid synchronized restarts
    startup_delay = random.uniform(0, 30)
    logger.info(f"⏳ Random startup delay: {startup_delay:.1f}s (avoids synchronized restarts)")
    await asyncio.sleep(startup_delay)
    
    # Check environment variables
    required_vars = ['DISCORD_BOT_TOKEN', 'SERVER_ID']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {missing_vars}")
        return
    
    logger.info("✅ Environment variables configured")
    
    # Try to import the main bot
    try:
        from krishna_bot import main as bot_main
        logger.info("✅ Bot module imported successfully")
    except ImportError as e:
        logger.error(f"❌ Failed to import bot module: {e}")
        return
    
    # Start the bot with additional protection
    attempt = 1
    max_attempts = 10
    
    while attempt <= max_attempts:
        try:
            logger.info(f"🚀 Starting bot (attempt {attempt}/{max_attempts})")
            
            # Add extra delay for subsequent attempts
            if attempt > 1:
                delay = min(300, attempt * 30)  # 30s, 60s, 90s, etc. up to 5 minutes
                logger.info(f"⏳ Waiting {delay}s before retry (Cloudflare protection)")
                await asyncio.sleep(delay)
            
            # Run the main bot
            await bot_main()
            
            # If we get here, the bot ran successfully
            logger.info("✅ Bot completed successfully")
            break
            
        except KeyboardInterrupt:
            logger.info("🙏 Bot stopped by user")
            break
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Check if it's a Cloudflare-related error
            if any(keyword in error_str for keyword in ['cloudflare', 'rate limit', '429', 'too many requests', 'error 1015']):
                logger.error(f"🚫 Cloudflare rate limiting detected (attempt {attempt}/{max_attempts}): {e}")
                
                if attempt >= max_attempts:
                    logger.error("❌ Max startup attempts reached. The service will restart automatically on Render.")
                    logger.error("💡 This is likely temporary Cloudflare blocking. The bot will retry when Render restarts.")
                    break
                    
                attempt += 1
                continue
                
            else:
                logger.error(f"❌ Unexpected error: {e}")
                # For non-Cloudflare errors, don't retry immediately
                if attempt >= 3:
                    logger.error("❌ Multiple unexpected errors - stopping")
                    break
                attempt += 1
                await asyncio.sleep(60)  # Wait 1 minute for unexpected errors
                continue
    
    logger.info("🔚 Startup script completed")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"❌ Startup script error: {e}")
        sys.exit(1)