#!/usr/bin/env python3
"""
Krishna-Conscious Discord Verification Bot
A compassionate AI-powered verification system that acts as a gentle temple gatekeeper.
Standalone version with no web dependencies.
"""

import os
import json
import asyncio
import logging
import re
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from dotenv import load_dotenv
from ai_config import build_complete_ai_prompt
from config_storage import ConfigStorage
import time
import backoff

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class KrishnaVerificationBot(commands.Bot):
    def __init__(self):
        # Bot configuration
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        
        # Enhanced bot configuration with proper rate limiting and retry logic
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None,
            # Add connection timeout and retry settings
            heartbeat_timeout=60.0,
            guild_ready_timeout=30.0,
            # Add chunk_guilds_at_startup for better startup handling
            chunk_guilds_at_startup=False,
            # Add connection retry settings
            max_messages=None,
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.watching, name="for new devotees 🙏"),
            # Add connector configuration to prevent Cloudflare blocking
            connector=None  # Will be set up in setup_hook
        )
        
        # Configuration
        self.target_server_id = int(os.getenv('SERVER_ID', '0'))
        self.ai_api_key = os.getenv('AI_API_KEY', '')
        self.ai_backup_key = os.getenv('AI_BACKUP_KEY', '')  # Backup API key
        
        # Initialize persistent configuration storage with database
        self.config_storage = ConfigStorage()
        
        # Bot state - stored in memory only (no web API dependencies)
        self.verification_sessions = {}  # {user_id: verification_data}
        self.bot_config = self.config_storage.get_config()  # Load persistent configuration from env SERVER_ID
        self.questions = {}  # Question bank loaded from JSON
        self._questions_last_modified = None  # Track questions.json modification time
        
        # Rate limiting and retry configuration
        self.startup_attempts = 0
        self.max_startup_attempts = 5
        self.connection_backoff_factor = 2.0
        self.last_connection_attempt = 0
        self.min_connection_interval = 30  # Minimum seconds between connection attempts
        
        # AI Optimization - Cache and rate limiting
        self.ai_cache = {}  # Cache AI responses to reduce API calls
        self.ai_call_count = 0  # Track daily API usage
        self.ai_last_reset = datetime.utcnow().date()  # Reset counter daily
        self.ai_daily_limit = 1000  # Conservative daily limit
        self.ai_current_key = 'primary'  # Track which key is active
        self.smart_fallback_enabled = True  # Use intelligent fallback before AI
        
    async def setup_hook(self):
        """Setup hook called when bot is starting up - configure HTTP session here"""
        try:
            import aiohttp
            
            # Create custom headers that look like a legitimate browser request
            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0'
            }
            
            # Create a connector with anti-Cloudflare settings
            connector = aiohttp.TCPConnector(
                limit=50,  # Reduced connection limit to be less aggressive
                limit_per_host=5,  # Lower per-host limit
                ttl_dns_cache=600,  # Longer DNS cache
                use_dns_cache=True,
                keepalive_timeout=60,  # Longer keepalive
                enable_cleanup_closed=True,
                family=0,  # Allow both IPv4 and IPv6
                ssl=None,  # Let Discord handle SSL naturally
                resolver=aiohttp.resolver.DefaultResolver(),
                happy_eyeballs_delay=0.25,  # Standard happy eyeballs delay
                sock_connect=None,
                sock_read=None
            )
            
            # Configure timeout settings to be more conservative
            timeout = aiohttp.ClientTimeout(
                total=30,  # Total timeout
                connect=10,  # Connection timeout
                sock_read=20  # Socket read timeout
            )
            
            # Create new session with anti-Cloudflare settings
            session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=headers,
                cookie_jar=aiohttp.CookieJar(),
                raise_for_status=False  # Handle errors manually
            )
            
            # Replace the bot's HTTP session
            if hasattr(self, 'http') and self.http and hasattr(self.http, '_session'):
                old_session = self.http._session
                if old_session and not old_session.closed:
                    await old_session.close()
            
            # Set the new session
            if hasattr(self, 'http') and self.http:
                self.http._session = session
                logger.info("✅ HTTP session configured with enhanced Cloudflare bypass settings")
            else:
                logger.warning("⚠️ Could not configure HTTP session - bot may face rate limiting")
                
        except Exception as e:
            logger.error(f"❌ Failed to configure HTTP session: {e}")
            # Continue anyway - bot might still work with default settings
        
        # Load questions after setting up HTTP
        await self.load_questions()
        logger.info("📚 Questions loaded successfully")
        
        # Register slash commands
        await self.register_slash_commands()
        logger.info("🔧 Slash commands registered")
        
        # Set up prefix commands
        self.setup_prefix_commands()
        logger.info("🔧 Prefix commands registered")
            
    async def register_slash_commands(self):
        """Register all slash commands"""
        try:
            # Setup command
            @app_commands.command(name="setup", description="🛠️ Complete Krishna verification bot setup")
            @app_commands.describe(
                devotee_role="Role assigned to verified devotees (AI score 8-10)",
                seeker_role="Role assigned to seekers (AI score 5-7)",
                verification_channel="Public channel for verification announcements",
                admin_channel="Private admin channel for detailed verification reports",
                general_chat_channel="Channel where verified users receive welcome messages",
                no_role="Optional role for users who don't pass verification (AI score 0-4)",
                admin_role_1="First admin role to mention in notifications (optional)",
                admin_role_2="Second admin role to mention in notifications (optional)"
            )
            async def setup_slash(interaction: discord.Interaction, 
                                devotee_role: discord.Role,
                                seeker_role: discord.Role,
                                verification_channel: discord.TextChannel,
                                admin_channel: discord.TextChannel,
                                general_chat_channel: discord.TextChannel,
                                no_role: discord.Role = None,
                                admin_role_1: discord.Role = None,
                                admin_role_2: discord.Role = None):
                await self.setup_command_logic(interaction, devotee_role, seeker_role, 
                                             verification_channel, admin_channel, 
                                             general_chat_channel=general_chat_channel,
                                             no_role=no_role,
                                             admin_role_1=admin_role_1, admin_role_2=admin_role_2)
            
            # Reload questions command
            @app_commands.command(name="reload_questions", description="🔄 Reload question bank from JSON file")
            async def reload_questions_slash(interaction: discord.Interaction):
                await self.reload_questions_logic(interaction)
            
            # Reload AI config command
            @app_commands.command(name="reload_ai_config", description="🤖 Reload AI configuration from ai_config.py")
            async def reload_ai_config_slash(interaction: discord.Interaction):
                await self.reload_ai_config_logic(interaction)
            
            # Question stats command
            @app_commands.command(name="question_stats", description="📊 View current question bank statistics")
            async def question_stats_slash(interaction: discord.Interaction):
                await self.question_stats_logic(interaction)
            
            # Manual verify command
            @app_commands.command(name="verify", description="🙏 Start your verification process manually")
            async def verify_slash(interaction: discord.Interaction):
                await self.verify_command_logic(interaction)
            
            # Admin verify-for command
            @app_commands.command(name="verify-for", description="🔧 (Admin) Start verification process for a specific user")
            @app_commands.describe(user="The user to start verification for")
            async def verify_for_slash(interaction: discord.Interaction, user: discord.Member):
                await self.verify_for_command_logic(interaction, user)
            
            # Add all commands to the tree
            self.tree.add_command(setup_slash)
            self.tree.add_command(reload_questions_slash)
            self.tree.add_command(reload_ai_config_slash)
            self.tree.add_command(question_stats_slash)
            self.tree.add_command(verify_slash)
            self.tree.add_command(verify_for_slash)
            
            logger.info(f"✅ Registered {len(self.tree.get_commands())} slash commands")
            
        except Exception as e:
            logger.error(f"❌ Error registering slash commands: {e}")

    async def on_connect(self):
        """Called when the bot connects to Discord"""
        logger.info("🔗 Connected to Discord WebSocket")
        
    async def on_disconnect(self):
        """Called when the bot disconnects from Discord"""
        logger.warning("🔌 Disconnected from Discord WebSocket")
        
    async def on_resumed(self):
        """Called when the bot resumes a session"""
        logger.info("🔄 Discord session resumed")
        
    def setup_prefix_commands(self):
        """Set up prefix commands for easier admin usage"""
        @self.command(name='help')
        async def help_command(ctx):
            """Show bot help and setup instructions"""
            embed = discord.Embed(
                title="🙏 Krishna Verification Bot - Help",
                description="Welcome to the Krishna-conscious Discord verification system!",
                color=0x4CAF50
            )
            
            embed.add_field(
                name="🛠️ Setup Commands",
                value="`/setup` - Interactive slash command setup\n"
                      "`!setup @devotee @seeker #verification #admin [@no_role]` - Quick prefix setup",
                inline=False
            )
            
            embed.add_field(
                name="🔧 Admin Commands", 
                value="`/reload_questions` - Reload question bank\n"
                      "`/reload_ai_config` - Reload AI configuration\n"
                      "`/question_stats` - View question statistics\n"
                      "`/verify-for @user` - Restart verification for a specific user",
                inline=False
            )
            
            embed.add_field(
                name="🙏 User Commands",
                value="`/verify` - Start verification manually (use in verification channel)",
                inline=False
            )
            
            embed.add_field(
                name="📋 Required Setup",
                value="• **Devotee Role** - For verified members (AI score 8-10)\n"
                      "• **Seeker Role** - For new seekers (AI score 5-7)\n" 
                      "• **Verification Channel** - Public announcements\n"
                      "• **Admin Channel** - Private detailed reports",
                inline=False
            )
            
            if not self.bot_config.get('is_configured'):
                embed.add_field(
                    name="⚠️ Configuration Status",
                    value="**Bot is NOT configured yet!**\nRun `/setup` or `!setup` to begin.",
                    inline=False
                )
            else:
                embed.add_field(
                    name="✅ Configuration Status", 
                    value="Bot is properly configured and ready!",
                    inline=False
                )
            
            embed.set_footer(text="🌸 Serving the Krishna-conscious community with compassion")
            await ctx.send(embed=embed)
            
        # Add quick setup prefix command for admins
        @self.command(name='setup')
        async def setup_prefix(ctx, devotee_role: discord.Role = None, seeker_role: discord.Role = None, 
                              verification_channel: discord.TextChannel = None, admin_channel: discord.TextChannel = None,
                              no_role: discord.Role = None):
            """Quick setup command using prefix. Usage: !setup @devotee @seeker #verification #admin [@no_role]"""
            
            # Check for required parameters
            if not all([devotee_role, seeker_role, verification_channel, admin_channel]):
                await ctx.send("❌ **Missing required parameters!**\n\n"
                              "**Usage:** `!setup @devotee @seeker #verification #admin [@no_role]`\n\n"
                              "**Required:**\n"
                              "• `@devotee` - Role for verified devotees (AI score 8-10)\n"
                              "• `@seeker` - Role for seekers (AI score 5-7)\n"
                              "• `#verification` - Public channel for announcements\n"
                              "• `#admin` - Private channel for detailed reports\n\n"
                              "**Optional:**\n"
                              "• `@no_role` - Role for users who don't pass (AI score 0-4)")
                return
                
            # Create mock interaction for compatibility
            class MockInteraction:
                def __init__(self, ctx):
                    self.user = ctx.author
                    self.guild = ctx.guild
                    self.guild_id = ctx.guild.id
                    self.channel = ctx.channel
                    self._ctx = ctx
                
                async def response_send_message(self, content=None, embed=None, ephemeral=False):
                    await self._ctx.send(content=content, embed=embed)
                
                @property
                def response(self):
                    return self
                
                async def send_message(self, content=None, embed=None, ephemeral=False):
                    await self._ctx.send(content=content, embed=embed)
            
            mock_interaction = MockInteraction(ctx)
            await self.setup_command_logic(mock_interaction, devotee_role, seeker_role, 
                                         verification_channel, admin_channel, no_role=no_role)

    async def load_questions(self):
        """Load questions from JSON file and track modifications for auto-sync"""
        try:
            questions_file = os.path.join(os.path.dirname(__file__), 'questions.json')
            
            # Check file modification time for auto-sync
            current_modified = os.path.getmtime(questions_file)
            
            with open(questions_file, 'r', encoding='utf-8') as f:
                self.questions = json.load(f)
            
            # Auto-sync AI config if questions file was modified
            if self._questions_last_modified and current_modified != self._questions_last_modified:
                logger.info("🔄 Questions file modified - auto-syncing AI configuration")
                try:
                    import importlib
                    import ai_config
                    importlib.reload(ai_config)
                    logger.info("✅ AI configuration auto-synchronized with updated questions")
                except Exception as ai_error:
                    logger.warning(f"⚠️ Could not auto-sync AI config: {ai_error}")
            
            # Update last modified time
            self._questions_last_modified = current_modified
            logger.info("✅ Question bank loaded successfully")
            
        except Exception as e:
            logger.error(f"❌ Error loading questions: {e}")
            # Fallback to basic questions if file not found
            self.questions = {
                'entry': [
                    {'id': 'E1', 'question': 'What brings you to this Krishna-conscious community?'},
                    {'id': 'E2', 'question': 'Are you someone who values respectful dialogue?'}
                ],
                'reflective': [
                    {'id': 'R1', 'question': 'What do you feel when you see someone living a spiritual life?'},
                    {'id': 'R2', 'question': 'What would you ask Krishna if He stood before you?'}
                ],
                'psychological': {
                    'trusted': [{'id': 'P1', 'question': 'What does humility mean to you?'}],
                    'medium': [{'id': 'P3', 'question': 'How would you handle if your beliefs were mocked?'}],
                    'high': [{'id': 'P5', 'question': 'What would you do if a devotee corrected you?'}]
                }
            }

    async def on_ready(self):
        """Called when the bot is ready"""
        logger.info(f'🌸 {self.user} has awakened to serve Krishna')
        logger.info(f'📍 Watching over server ID: {self.target_server_id}')
        
        # Sync slash commands for the target server
        if self.target_server_id:
            try:
                # First sync globally to ensure commands are registered
                await self.tree.sync()
                logger.info("✅ Global slash commands synced")
                
                # Then sync for the specific guild
                guild = discord.Object(id=self.target_server_id)
                synced = await self.tree.sync(guild=guild)
                logger.info(f"✅ Slash commands synced for target server: {len(synced)} commands")
                
                # Log which commands were synced
                for cmd in self.tree.get_commands():
                    description = getattr(cmd, 'description', 'No description')
                    logger.info(f"   - /{cmd.name}: {description}")
                    
            except Exception as e:
                logger.error(f"Error syncing commands: {e}")
                # Try global sync as fallback
                try:
                    await self.tree.sync()
                    logger.info("✅ Fallback: Global commands synced")
                except Exception as e2:
                    logger.error(f"Failed global sync fallback: {e2}")
        
        # Check if bot is configured and provide helpful instructions
        if not self.bot_config.get('is_configured'):
            logger.warning("⚠️ BOT NOT CONFIGURED YET!")
            logger.warning("📋 To enable verification, run this command in your Discord server:")
            logger.warning("   /setup")
            logger.warning("📝 You'll need to specify:")
            logger.warning("   • Devotee role (for verified members)")
            logger.warning("   • Seeker role (for new seekers)")  
            logger.warning("   • Verification channel (public announcements)")
            logger.warning("   • Admin channel (detailed reports)")
            logger.warning("🚨 New members will be IGNORED until bot is configured!")
        else:
            logger.info("✅ Bot is configured and ready for verification!")
        
        logger.info("🙏 Bot fully ready to serve the Krishna-conscious community!")

    async def on_member_join(self, member):
        """Handle new member joining the server"""
        # Only process members from target server
        if member.guild.id != self.target_server_id:
            return
            
        # Check if bot is configured
        if not self.bot_config.get('is_configured'):
            logger.warning(f"🚨 Bot not configured - skipping verification for {member}")
            logger.warning("🛠️ Run '/setup' command in Discord to configure the bot first!")
            logger.warning(f"📋 Required: devotee role, seeker role, verification channel, admin channel")
            return
        
        logger.info(f"🙏 New seeker arrived: {member} ({member.id})")
        
        # Calculate suspicion score using AI analysis
        suspicion_score = await self.calculate_suspicion_score(member)
        
        # Create verification session
        user_data = {
            'discord_id': str(member.id),
            'username': member.name,
            'discriminator': member.discriminator,
            'avatar': str(member.display_avatar.url) if member.display_avatar else None,
            'account_created_at': member.created_at.isoformat(),
            'joined_at': datetime.utcnow().isoformat(),
            'suspicion_score': suspicion_score,
            'current_question': 0,
            'responses': [],
            'questions_asked': []
        }
        
        await self.start_verification_process(member, user_data)

    async def on_message(self, message):
        """Handle incoming messages, especially DM responses"""
        # Ignore bot messages
        if message.author.bot:
            return
            
        # Only process DMs for verification
        if isinstance(message.channel, discord.DMChannel):
            await self.handle_verification_response(message)
        
        # Process commands
        await self.process_commands(message)

    async def on_reaction_add(self, reaction, user):
        """Handle reaction events for verification restart"""
        if user.bot:
            return
            
        # Check for restart verification (🔄 emoji)
        if str(reaction.emoji) == '🔄' and user.id in self.verification_sessions:
            if self.verification_sessions[user.id].get('status') == 'failed':
                guild = self.get_guild(self.target_server_id)
                if guild:
                    member = guild.get_member(user.id)
                    if member:
                        # Reset session
                        self.verification_sessions[user.id]['status'] = 'pending'
                        self.verification_sessions[user.id]['current_question'] = 0
                        self.verification_sessions[user.id]['responses'] = []
                        
                        await self.start_verification_process(member, self.verification_sessions[user.id])

    async def calculate_suspicion_score(self, member) -> int:
        """Calculate suspicion score with intelligent fallback to minimize AI usage"""
        try:
            # Use enhanced rule-based scoring first - only use AI if borderline case
            fallback_score = self.calculate_fallback_suspicion_score(member)
            
            # Only use AI for borderline cases (score 1-3) to optimize API usage
            if fallback_score in [1, 2, 3] and self.should_use_ai():
                # Gather comprehensive profile data for AI analysis only when needed
                account_age_days = (datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
                join_age_days = (datetime.utcnow() - member.joined_at.replace(tzinfo=None)).days if member.joined_at else 0
                
                profile_data = {
                    'username': member.name,
                    'display_name': member.display_name,
                    'account_age_days': account_age_days,
                    'server_join_age_days': join_age_days,
                    'has_avatar': bool(member.avatar),
                    'is_bot': member.bot,
                    'fallback_score': fallback_score  # Include rule-based score for context
                }
                
                # Use AI to refine borderline cases only
                ai_score = await self.analyze_profile_with_ai(profile_data)
                
                if ai_score is not None:
                    logger.info(f"🤖 AI refined suspicion analysis for {member.name}: {fallback_score} → {ai_score}/4")
                    return min(max(ai_score, 0), 4)
            
            # For clear cases (0 or 4), trust rule-based scoring to save API calls
            logger.info(f"📊 Rule-based suspicion analysis for {member.name}: {fallback_score}/4 (AI skipped)")
            return fallback_score
            
        except Exception as e:
            logger.warning(f"⚠️ Suspicion analysis failed for {member.name}: {e}")
            return self.calculate_fallback_suspicion_score(member)
    
    def should_use_ai(self) -> bool:
        """Determine if AI should be used based on rate limiting and daily usage"""
        # Reset daily counter if new day
        today = datetime.utcnow().date()
        if today > self.ai_last_reset:
            self.ai_call_count = 0
            self.ai_last_reset = today
            logger.info(f"🔄 AI usage counter reset for {today}")
        
        # Check if under daily limit
        if self.ai_call_count >= self.ai_daily_limit:
            logger.warning(f"🚫 Daily AI limit reached ({self.ai_call_count}/{self.ai_daily_limit})")
            return False
        
        # Check if AI is available - fallback only used when both keys fail
        if not self.ai_api_key and not self.ai_backup_key:
            logger.warning("⚠️ No AI API keys configured - fallback will be used")
            return False
        
        # Always try AI if keys are available
        return True
    
    def get_available_ai_key(self) -> Optional[str]:
        """Get available AI API key with backup fallback"""
        if self.ai_current_key == 'primary' and self.ai_api_key:
            return self.ai_api_key
        elif self.ai_backup_key:
            if self.ai_current_key == 'primary':
                logger.info("🔄 Switching to backup AI API key due to primary key issues")
                self.ai_current_key = 'backup'
            return self.ai_backup_key
        elif self.ai_api_key:
            self.ai_current_key = 'primary'
            return self.ai_api_key
        else:
            logger.error("❌ No AI API keys available - both primary and backup are missing")
            return None
    
    def calculate_fallback_suspicion_score(self, member) -> int:
        """Simplified suspicion scoring based only on account age"""
        score = 0
        
        # Account age (newer accounts are more suspicious)
        account_age_days = (datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
        if account_age_days < 1:
            score += 3  # Very new account
        elif account_age_days < 7:
            score += 2  # Week old
        elif account_age_days < 30:
            score += 1  # Month old
        elif account_age_days > 365:
            score -= 1  # Well established account
        
        # Final score adjustment
        final_score = min(max(score, 0), 4)
        logger.info(f"📊 Age-based suspicion analysis for {member.name}: {final_score}/4 (Account age: {account_age_days} days)")
        return final_score

    def get_cache_key(self, data_type: str, data: any) -> str:
        """Generate cache key for AI responses"""
        import hashlib
        data_str = str(data)
        return f"{data_type}_{hashlib.md5(data_str.encode()).hexdigest()[:8]}"

    async def analyze_profile_with_ai(self, profile_data: dict) -> Optional[int]:
        """Use AI to analyze user profile with caching and optimization"""
        try:
            # Check cache first to avoid repeat API calls
            cache_key = self.get_cache_key('profile', profile_data)
            if cache_key in self.ai_cache:
                logger.info(f"💾 Using cached profile analysis for {profile_data['username']}")
                return self.ai_cache[cache_key]
            
            # Get available API key
            api_key = self.get_available_ai_key()
            if not api_key:
                logger.warning("⚠️ No AI API key available for profile analysis")
                return None
            
            # Track API usage
            self.ai_call_count += 1
            logger.info(f"📊 AI API call #{self.ai_call_count}/{self.ai_daily_limit}")
            
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-2.0-flash')
            
            # Optimized, concise prompt for profile analysis
            prompt = f"""
Analyze Discord user suspicion level (0-4 scale):

User: {profile_data['username']} | Age: {profile_data['account_age_days']}d | Avatar: {profile_data['has_avatar']} | Bot: {profile_data['is_bot']} | Rule Score: {profile_data.get('fallback_score', 'N/A')}

Score meaning: 0=clearly legitimate, 1=low, 2=moderate, 3=high, 4=very suspicious

Consider the rule-based score as guidance. Return ONLY number 0-4.
"""
            
            # Generate response with timeout
            response = model.generate_content(prompt)
            ai_text = response.text.strip()
            
            # Parse and cache result
            try:
                score = int(ai_text)
                if 0 <= score <= 4:
                    # Cache successful result
                    self.ai_cache[cache_key] = score
                    # Limit cache size to prevent memory issues
                    if len(self.ai_cache) > 100:
                        # Remove oldest 20 entries
                        keys_to_remove = list(self.ai_cache.keys())[:20]
                        for key in keys_to_remove:
                            del self.ai_cache[key]
                    return score
                else:
                    logger.warning(f"⚠️ AI returned invalid score: {ai_text}")
                    return None
            except ValueError:
                logger.warning(f"⚠️ AI returned non-numeric response: {ai_text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ AI profile analysis failed: {e}")
            # Switch to backup key if primary fails
            if self.ai_current_key == 'primary' and self.ai_backup_key:
                self.ai_current_key = 'backup'
                logger.info("🔄 Switched to backup AI key due to error")
            return None

    def select_questions(self, suspicion_score: int) -> List[str]:
        """Select appropriate questions with mandatory ISKCON question at position 3"""
        questions = []
        
        # Q1: Entry question (normal selection, but exclude ISKCON question)
        entry_questions = [q for q in self.questions['entry'] if q['id'] != 'E3']
        if entry_questions:
            entry_q = random.choice(entry_questions)
            questions.append(entry_q['question'])
        else:
            # Fallback if no entry questions available
            entry_q = random.choice(self.questions['entry'])
            questions.append(entry_q['question'])
        
        # Build pool of all remaining questions for Q2 and Q4
        remaining_pool = []
        
        # Add entry questions (excluding ISKCON and the one already used)
        for q in self.questions['entry']:
            if q['id'] != 'E3' and q['question'] != questions[0]:
                remaining_pool.append(q)
        
        # Add all reflective questions
        remaining_pool.extend(self.questions['reflective'])
        
        # Add all psychological questions from all difficulty levels
        remaining_pool.extend(self.questions['psychological']['trusted'])
        remaining_pool.extend(self.questions['psychological']['medium'])
        remaining_pool.extend(self.questions['psychological']['high'])
        
        # Q2: Random from remaining pool
        if remaining_pool:
            q2 = random.choice(remaining_pool)
            questions.append(q2['question'])
            # Remove selected question from pool to avoid duplicates
            remaining_pool = [q for q in remaining_pool if q['question'] != q2['question']]
        
        # Q3: Always the ISKCON/Prabhupada question (compulsory)
        iskcon_question = next((q for q in self.questions['entry'] if q['id'] == 'E3'), None)
        if iskcon_question:
            questions.append(iskcon_question['question'])
        else:
            # Fallback if ISKCON question not found
            questions.append("What are your views on Srila Prabhupada and ISKCON?")
        
        # Q4: Random from remaining pool
        if remaining_pool:
            q4 = random.choice(remaining_pool)
            questions.append(q4['question'])
        elif len(questions) == 3:
            # Fallback: add a basic question if pool is empty
            questions.append("How would you handle it if your beliefs were questioned?")
        
        return questions

    async def start_verification_process(self, member, user_data: Dict):
        """Begin the verification process for a new member with improved error handling"""
        # Store session
        self.verification_sessions[member.id] = user_data
        
        # Select questions based on suspicion score
        questions = self.select_questions(user_data['suspicion_score'])
        user_data['questions_asked'] = questions
        user_data['status'] = 'pending'
        
        logger.info(f"Starting verification for {member} (suspicion: {user_data['suspicion_score']})")
        
        try:
            # Send notification to verification channel that verification has started
            await self.send_verification_started_notification(member, user_data['suspicion_score'])
            
            # Try to send welcome message and first question with rate limit handling
            welcome_sent = await self.send_verification_welcome_with_retry(member)
            
            if welcome_sent:
                # Send first question
                question_sent = await self.send_verification_question_with_retry(member, questions[0], 1)
                
                if not question_sent:
                    # If question failed to send, mark session as failed with rate limit info
                    user_data['status'] = 'failed'
                    user_data['failure_reason'] = 'rate_limited'
                    logger.warning(f"⚠️ Rate limited - verification failed for {member}")
                    await self.notify_verification_failure_due_to_rate_limit(member)
            else:
                # If welcome failed to send, mark session as failed
                user_data['status'] = 'failed'
                user_data['failure_reason'] = 'rate_limited'
                logger.warning(f"⚠️ Rate limited - verification failed for {member}")
                await self.notify_verification_failure_due_to_rate_limit(member)
                
        except Exception as e:
            logger.error(f"❌ Verification start failed for {member}: {e}")
            user_data['status'] = 'failed'
            user_data['failure_reason'] = 'error'
            raise

    async def send_verification_welcome_with_retry(self, member, max_retries=3):
        """Send verification welcome message with retry logic for rate limiting"""
        for attempt in range(1, max_retries + 1):
            try:
                await self.send_verification_welcome(member)
                return True
            except discord.HTTPException as e:
                if '429' in str(e) or 'rate limit' in str(e).lower():
                    logger.warning(f"Rate limited sending welcome to {member} (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        await asyncio.sleep(min(60, attempt * 20))  # Exponential backoff
                        continue
                    else:
                        logger.error(f"Failed to send welcome to {member} after {max_retries} attempts - rate limited")
                        return False
                else:
                    logger.error(f"Failed to send welcome to {member}: {e}")
                    return False
            except Exception as e:
                logger.error(f"Unexpected error sending welcome to {member}: {e}")
                return False
        return False

    async def send_verification_question_with_retry(self, member, question, question_num, max_retries=3):
        """Send verification question with retry logic for rate limiting"""
        for attempt in range(1, max_retries + 1):
            try:
                await self.send_verification_question(member, question, question_num)
                return True
            except discord.HTTPException as e:
                if '429' in str(e) or 'rate limit' in str(e).lower():
                    logger.warning(f"Rate limited sending question to {member} (attempt {attempt}/{max_retries})")
                    if attempt < max_retries:
                        await asyncio.sleep(min(60, attempt * 20))  # Exponential backoff
                        continue
                    else:
                        logger.error(f"Failed to send question to {member} after {max_retries} attempts - rate limited")
                        return False
                else:
                    logger.error(f"Failed to send question to {member}: {e}")
                    return False
            except Exception as e:
                logger.error(f"Unexpected error sending question to {member}: {e}")
                return False
        return False

    async def notify_verification_failure_due_to_rate_limit(self, member):
        """Notify in verification channel that a user's verification failed due to rate limiting"""
        try:
            channel_id = self.bot_config.get('verification_channel_id')
            if not channel_id:
                return
                
            channel = self.get_channel(int(channel_id))
            if not channel:
                return

            embed = discord.Embed(
                title="⚠️ Verification Failed - Rate Limited",
                description=f"**User:** {member.mention} ({member.name}#{member.discriminator})\n"
                           f"**Reason:** Discord rate limiting prevented DM delivery\n"
                           f"**Solution:** User can try `/verify` in this channel",
                color=0xFF9800
            )
            
            embed.add_field(
                name="📝 Instructions for User",
                value=f"{member.mention}, use `/verify` in this channel to manually start your verification process.",
                inline=False
            )
            
            embed.set_footer(text="🔄 Rate limit protection active")
            await channel.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Failed to send rate limit notification: {e}")
    
    async def send_verification_started_notification(self, member, suspicion_score: int):
        """Send simple notification to verification channel when verification starts for a new user"""
        try:
            channel_id = self.bot_config.get('verification_channel_id')
            if not channel_id:
                logger.warning("⚠️ No verification channel configured - skipping start notification")
                return
                
            channel = self.get_channel(int(channel_id))
            if not channel:
                logger.warning(f"⚠️ Verification channel {channel_id} not found - skipping start notification")
                return
            
            logger.info(f"📤 Sending verification start notification for {member} to {channel.name}")
            
            # Create simple, clean embed for public verification channel
            embed = discord.Embed(
                title="📩 Verification Started",
                description=f"🙏 {member.mention} has joined the server and verification questions have been sent to their DMs.",
                color=0x4CAF50  # Green color
            )
            
            # Add basic process information only
            embed.add_field(
                name="📋 Process Status",
                value="✉️ Questions sent to DMs\n⏳ Awaiting responses\n🤖 AI will analyze answers",
                inline=False
            )
            
            embed.set_footer(text="🌸 Welcome to our Krishna-conscious community!")
            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)
            
            await channel.send(embed=embed)
            logger.info(f"✅ Sent verification start notification for {member} to verification channel #{channel.name}")
            
            # Send detailed analysis to admin channel only
            await self.send_detailed_verification_analysis_to_admin(member, suspicion_score)
            
        except Exception as e:
            logger.error(f"❌ Error sending verification start notification: {e}")
            # Try sending a simple text message as backup
            try:
                if 'channel' in locals() and channel:
                    await channel.send(f"📩 **Verification Started**: {member.mention} has joined and verification questions have been sent to their DMs.")
                    logger.info(f"✅ Sent backup verification start notification for {member}")
            except Exception as backup_error:
                logger.error(f"❌ Backup notification also failed: {backup_error}")

    async def send_detailed_verification_analysis_to_admin(self, member, suspicion_score: int):
        """Send detailed verification analysis to admin channel only"""
        try:
            admin_channel_id = self.bot_config.get('admin_channel_id')
            if not admin_channel_id:
                logger.warning("⚠️ No admin channel configured - skipping detailed analysis")
                return
                
            admin_channel = self.get_channel(int(admin_channel_id))
            if not admin_channel:
                logger.warning(f"⚠️ Admin channel {admin_channel_id} not found - skipping detailed analysis")
                return
            
            # Calculate detailed suspicion factors for admins
            account_age_days = (datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
            has_avatar = bool(member.avatar)
            username = member.name.lower()
            
            # Build suspicion breakdown for admins
            suspicion_factors = []
            if account_age_days < 1:
                suspicion_factors.append("🆕 Brand new account (< 1 day)")
            elif account_age_days < 7:
                suspicion_factors.append(f"🆕 Very new account ({account_age_days} days)")
            elif account_age_days < 30:
                suspicion_factors.append(f"⏰ Recent account ({account_age_days} days)")
            else:
                suspicion_factors.append(f"✅ Established account ({account_age_days} days)")
                
            if not has_avatar:
                suspicion_factors.append("❓ No custom avatar")
            else:
                suspicion_factors.append("✅ Has custom avatar")
                
            # Username pattern analysis for admins
            if re.search(r'\d{6,}', username):
                suspicion_factors.append("🚨 Many numbers in username (6+)")
            elif re.search(r'\d{4,5}', username):
                suspicion_factors.append("⚠️ Several numbers in username")
            elif re.search(r'(discord|nitro|gift|free|hack|bot|raid)', username):
                suspicion_factors.append("🚨 High-risk keywords")
            elif re.search(r'(test|fake|temp|alt)', username):
                suspicion_factors.append("⚠️ Suspicious keywords")
            elif re.search(r'^user\d+$|^[a-z]{1,3}\d{4,}$', username):
                suspicion_factors.append("⚠️ Generic pattern")
            else:
                suspicion_factors.append("✅ Normal username")
            
            # Question difficulty based on suspicion
            if suspicion_score <= 1:
                difficulty = "Standard difficulty questions"
                color = 0x4CAF50  # Green
            elif suspicion_score <= 3:
                difficulty = "Medium difficulty questions"
                color = 0xFF9800  # Orange
            else:
                difficulty = "High difficulty questions"
                color = 0xF44336  # Red
            
            # Create detailed admin embed
            admin_embed = discord.Embed(
                title="🔍 Detailed Verification Analysis",
                description=f"**User:** {member.mention} ({member.name}#{member.discriminator})",
                color=color
            )
            
            # Add suspicion analysis for admins
            admin_embed.add_field(
                name=f"🎯 SUSPICION SCORE: {suspicion_score}/4",
                value="\n".join(suspicion_factors),
                inline=False
            )
            
            # Add admin-specific details
            admin_embed.add_field(
                name="📋 Question Difficulty",
                value=f"✉️ {difficulty}",
                inline=True
            )
            
            # Add profile details for admins
            admin_embed.add_field(
                name="👤 Profile Analysis",
                value=f"**Username:** {member.name}\n**Account Age:** {account_age_days} days\n**Avatar:** {'Custom' if has_avatar else 'Default'}",
                inline=True
            )
            
            admin_embed.set_footer(text="🔒 Admin-only detailed analysis")
            if member.display_avatar:
                admin_embed.set_thumbnail(url=member.display_avatar.url)
            
            await admin_channel.send(embed=admin_embed)
            logger.info(f"✅ Sent detailed verification analysis for {member} to admin channel #{admin_channel.name}")
            
        except Exception as e:
            logger.error(f"❌ Error sending detailed verification analysis to admin: {e}")

    async def send_manual_review_notification(self, user, responses: List[str], questions: List[str], ai_result: Dict):
        """Send manual review notification to admin when AI fails"""
        try:
            admin_channel_id = self.bot_config.get('admin_channel_id')
            if not admin_channel_id:
                logger.warning("⚠️ No admin channel configured - cannot send manual review notification")
                return
                
            admin_channel = self.get_channel(int(admin_channel_id))
            if not admin_channel:
                logger.warning(f"⚠️ Admin channel {admin_channel_id} not found - cannot send manual review notification")
                return

            # Build manual review embed
            embed = discord.Embed(
                title="🔍 Manual Review Required",
                description=f"⚠️ **AI Verification Failed** for {user.mention}\n\n**Reason:** {ai_result.get('ai_optimization', 'Unknown AI error')}",
                color=0xFF9800  # Orange color for attention
            )

            # Add user info
            embed.add_field(
                name="👤 User Details",
                value=f"**Username:** {user.name}\n**ID:** {user.id}\n**Mention:** {user.mention}",
                inline=False
            )

            # Add responses for manual review
            for i, (question, response) in enumerate(zip(questions, responses), 1):
                embed.add_field(
                    name=f"❓ Question {i}",
                    value=f"**Q:** {question[:100]}{'...' if len(question) > 100 else ''}\n**A:** {response[:200]}{'...' if len(response) > 200 else ''}",
                    inline=False
                )

            embed.add_field(
                name="⚡ Action Required",
                value="Please manually review these responses and assign the appropriate role using Discord's member management or bot commands.",
                inline=False
            )

            embed.set_footer(text="🤖 AI verification system unavailable - manual review needed")
            if user.display_avatar:
                embed.set_thumbnail(url=user.display_avatar.url)

            await admin_channel.send(embed=embed)
            logger.info(f"✅ Sent manual review notification for {user} to admin channel")

        except Exception as e:
            logger.error(f"❌ Error sending manual review notification: {e}")

    async def send_manual_review_user_notification(self, user):
        """Notify user that their verification is being reviewed manually"""
        try:
            embed = discord.Embed(
                title="🔍 Verification Under Review",
                description="Thank you for completing your verification! 🙏",
                color=0x4A90E2
            )

            embed.add_field(
                name="📋 What's Happening?",
                value="Your responses are currently being reviewed manually by our admin team.",
                inline=False
            )

            embed.add_field(
                name="⏰ What's Next?",
                value="• An admin will review your answers personally\n• You'll be assigned the appropriate role soon\n• No action needed from you right now",
                inline=False
            )

            embed.add_field(
                name="🕐 Timeline",
                value="Manual reviews typically take a few hours to 1 day depending on admin availability.",
                inline=False
            )

            embed.set_footer(text="🌸 We appreciate your patience while we ensure the best community experience!")

            await user.send(embed=embed)
            logger.info(f"✅ Sent manual review user notification to {user}")

        except discord.Forbidden:
            logger.warning(f"Cannot send manual review DM to {user} - DMs may be disabled")
        except Exception as e:
            logger.error(f"❌ Error sending manual review user notification: {e}")
    
    async def send_verification_welcome(self, member):
        """Send detailed verification flow welcome message with instructions"""
        try:
            # Get server name - handle both Member and User objects
            server_name = "Bhu-Goloka"
            if hasattr(member, 'guild') and member.guild:
                server_name = member.guild.name
            else:
                guild = self.get_guild(getattr(self, "target_server_id", None))
                if guild:
                    server_name = guild.name

            # Build detailed verification flow embed
            welcome_embed = discord.Embed(
                title=f"🙏 Welcome to {server_name}!",
                description=f"Hare Krishna {member.mention}! We're excited you're here. To join **{server_name}**, we'd love to know a bit about you.",
                color=0x4A90E2
            )

            # What to Expect section
            welcome_embed.add_field(
                name="📚 What to Expect:",
                value=(
                    "• 4 simple questions about your spiritual journey\n"
                    "• Just type naturally - no perfect grammar needed!\n"
                    "• We care about your heart, not perfect writing\n"
                    "• Takes about 2-3 minutes"
                ),
                inline=False
            )

            # How to Answer section
            welcome_embed.add_field(
                name="💬 How to Answer:",
                value="Simply type your response and hit enter!",
                inline=False
            )

            # Example formats
            welcome_embed.add_field(
                name="🔹 Example format:",
                value="I'm interested in Krishna consciousness because...",
                inline=False
            )

            welcome_embed.add_field(
                name="🔹 Or just casual:",
                value="idk much but krishna seems cool and i want to learn\n\n**Both are perfectly fine!**",
                inline=False
            )

            # Ready section
            welcome_embed.add_field(
                name="🌸 Ready?",
                value="Your first question is coming right up! Take your time and speak from your heart.",
                inline=False
            )

            # Footer message
            welcome_embed.set_footer(text="❤️ We welcome sincere seekers of all backgrounds and language levels")

            await member.send(embed=welcome_embed)
            logger.info(f"📩 Sent detailed verification welcome message to {member}")

        except discord.Forbidden:
            logger.warning(f"Cannot send welcome DM to {member}")

    async def send_verification_question(self, member, question: str, question_num: int):
        """Send a verification question via DM"""
        try:
            # Get server name - handle both Member and User objects
            server_name = "Unknown Server"
            if hasattr(member, 'guild') and member.guild:
                server_name = member.guild.name
            else:
                # For User objects (DM context), get guild from bot config
                guild = self.get_guild(self.target_server_id)
                if guild:
                    server_name = guild.name
            
            embed = discord.Embed(
                title=f"🙏 Verification for {server_name}",
                description=f"Hare Krishna! 🌸 You're being verified for **{server_name}** server.",
                color=0xFF6B35
            )
            
            # Make the question much more visible with prominent formatting
            embed.add_field(
                name=f"❓ QUESTION {question_num} OF 4 ❓",
                value=f"```\n{question}\n```\n**⬇️ Please answer this question below ⬇️**",
                inline=False
            )
            
            embed.add_field(
                name="📝 How to respond:",
                value="**Just type your answer and send it!** No special format needed.\n\n✅ Write in your own words - be genuine and personal\n\nDon't worry about perfect grammar or spelling - just speak from your heart naturally!",
                inline=False
            )
            
            if question_num == 1:
                embed.add_field(
                    name="💝 Friendly Note:",
                    value="Type however feels comfortable - typos, casual language, and simple words are perfectly fine. We care about your sincerity, not perfect writing.",
                    inline=False
                )
            
            embed.set_footer(text="🌸 Answer with sincerity - we welcome genuine seekers of all backgrounds")
            
            await member.send(embed=embed)
            logger.info(f"Sent question {question_num} to {member}")
            
        except discord.Forbidden:
            # DMs disabled - send fallback
            await self.send_verification_fallback(member)

    async def send_verification_fallback(self, member):
        """Send fallback message if DMs are disabled"""
        try:
            # Try DM fallback channel first, then verification channel
            fallback_channel_id = self.bot_config.get('dm_questions_channel_id') or self.bot_config.get('verification_channel_id')
            
            if fallback_channel_id:
                channel = self.get_channel(int(fallback_channel_id))
                if channel:
                    # Get server name - handle both Member and User objects
                    server_name = "Unknown Server"
                    if hasattr(member, 'guild') and member.guild:
                        server_name = member.guild.name
                    else:
                        # For User objects, get guild from bot config
                        guild = self.get_guild(self.target_server_id)
                        if guild:
                            server_name = guild.name
                    
                    embed = discord.Embed(
                        title=f"🔒 DM Verification Required for {server_name}",
                        description=f"{member.mention}, please enable DMs from server members to complete verification for **{server_name}**.",
                        color=0xFFA500
                    )
                    embed.add_field(
                        name="How to enable DMs:",
                        value="Server Settings → Privacy & Safety → Allow direct messages from server members",
                        inline=False
                    )
                    embed.add_field(
                        name="Why DMs are needed:",
                        value="The verification process uses private messages to ask personal questions about your spiritual journey. This keeps your responses confidential.",
                        inline=False
                    )
                    
                    await channel.send(embed=embed)
                    
                    # Also log to admin channel if configured
                    admin_channel_id = self.bot_config.get('admin_channel_id')
                    if admin_channel_id:
                        admin_channel = self.get_channel(int(admin_channel_id))
                        if admin_channel:
                            admin_embed = discord.Embed(
                                title="⚠️ DM Verification Failed",
                                description=f"User {member.mention} ({member.id}) cannot receive DMs. Fallback message sent.",
                                color=0xFF9800
                            )
                            await admin_channel.send(embed=admin_embed)
                            
        except Exception as e:
            logger.error(f"Error sending fallback message: {e}")

    async def handle_verification_response(self, message):
        """Process verification responses from DMs"""
        user_id = message.author.id
        
        # Check if user has active verification
        if user_id not in self.verification_sessions:
            return
            
        session = self.verification_sessions[user_id]
        
        # Check if verification is still pending
        if session.get('status') != 'pending':
            return
        
        current_q = session['current_question']
        total_questions = len(session['questions_asked'])
        
        # Clean and store response (handle typos, formatting issues)
        cleaned_response = self.clean_user_response(message.content)
        session['responses'].append({
            'question_num': current_q + 1,
            'question': session['questions_asked'][current_q],
            'response': cleaned_response,
            'original_response': message.content,  # Keep original for reference
            'timestamp': datetime.utcnow().isoformat()
        })
        
        logger.info(f"Received response {current_q + 1}/{total_questions} from {message.author}")
        
        # Move to next question
        session['current_question'] += 1
        
        if session['current_question'] < total_questions:
            # Send next question
            next_q = session['questions_asked'][session['current_question']]
            await self.send_verification_question(message.author, next_q, session['current_question'] + 1)
        else:
            # All questions answered - process completion
            await self.process_verification_completion(message.author, session)
    
    def clean_user_response(self, response: str) -> str:
        """Clean and normalize user responses to handle syntax errors, typos, and casual formatting"""
        if not response:
            return response
        
        # Remove excessive whitespace and normalize
        cleaned = ' '.join(response.strip().split())
        
        # Handle common casual patterns and typos
        import re
        
        # Fix common contractions and casual writing
        replacements = {
            r'\bur\b': 'your',
            r'\bu\b': 'you',
            r'\bdont\b': "don't",
            r'\bcant\b': "can't",
            r'\bwont\b': "won't",
            r'\bim\b': "I'm",
            r'\bive\b': "I've",
            r'\btheyre\b': "they're",
            r'\btheres\b': "there's",
            r'\bits\b': "it's",
            r'\bkrishn\b': 'Krishna',
            r'\bhare krishn\b': 'Hare Krishna',
            r'\bprabhupad\b': 'Prabhupada',
            r'\bprabhupaad\b': 'Prabhupada',
            r'\bprabhupada\b': 'Prabhupada',
            r'\bbhakti\b': 'bhakti',
            r'\bdevotee\b': 'devotee',
            r'\bchant\b': 'chant',
            r'\bmantra\b': 'mantra',
            r'\bgod\b': 'God',
            r'\bkrishna\b': 'Krishna'
        }
        
        # Apply word-level replacements (case insensitive)
        for pattern, replacement in replacements.items():
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        
        # Handle multiple punctuation marks
        cleaned = re.sub(r'[.]{2,}', '.', cleaned)
        cleaned = re.sub(r'[!]{2,}', '!', cleaned)
        cleaned = re.sub(r'[?]{2,}', '?', cleaned)
        
        # Fix spacing around punctuation
        cleaned = re.sub(r'\s+([.!?])', r'\1', cleaned)
        cleaned = re.sub(r'([.!?])([a-zA-Z])', r'\1 \2', cleaned)
        
        # Handle common typos in spiritual terms
        spiritual_fixes = {
            r'\bspiritual\b': 'spiritual',
            r'\bspirtual\b': 'spiritual',
            r'\breligious\b': 'religious',
            r'\breligous\b': 'religious',
            r'\bpeaceful\b': 'peaceful',
            r'\bpeacfull\b': 'peaceful',
            r'\bhumble\b': 'humble',
            r'\bhumbl\b': 'humble'
        }
        
        for pattern, replacement in spiritual_fixes.items():
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        
        # Preserve emotional expressions as they show sincerity
        # Don't "fix" expressions like "omg", "wow", "amazing" as they show genuine emotion
        
        # Handle broken sentences - add period if missing and sentence seems complete
        if cleaned and not cleaned[-1] in '.!?':
            # Check if it looks like a complete thought (has subject/verb patterns)
            if len(cleaned.split()) > 3 and any(word.lower() in ['i', 'my', 'me', 'am', 'feel', 'want', 'think', 'believe'] for word in cleaned.split()[:3]):
                cleaned += '.'
        
        return cleaned.strip()

    async def process_verification_completion(self, user, session):
        """Process completed verification with AI scoring"""
        logger.info(f"Processing completed verification for {user}")
        
        responses = [r['response'] for r in session['responses']]
        questions = [r['question'] for r in session['responses']]
        suspicion_score = session['suspicion_score']
        
        # Score responses with AI (suspicion score not used in AI verification)
        ai_result = await self.score_responses_with_ai(responses, questions)
        
        # Check if AI failed (both keys didn't work)
        ai_optimization = ai_result.get('ai_optimization', '')
        ai_failed = ai_optimization in ['both_keys_failed', 'ai_failed_all_retries', 'no_api_key']
        
        if ai_failed:
            logger.warning(f"🤖 AI scoring failed for {user} - sending to admin for manual review")
            
            # Store session data for manual review
            session['final_score'] = None  # No automatic score
            session['ai_reasoning'] = f"AI Failed: {ai_optimization}"
            session['assigned_role'] = None  # No automatic role
            session['status'] = 'pending_manual_review'
            
            # Send manual review notification to admin instead of assigning roles
            await self.send_manual_review_notification(user, responses, questions, ai_result)
            
            # Send user notification about manual review
            await self.send_manual_review_user_notification(user)
            
            return  # Exit early - no role assignment
        
        # Determine role based on AI score (normal flow when AI worked)
        score = ai_result.get('score', 0)
        assigned_role = None
        
        if score >= 8:
            assigned_role = 'devotee'
            session['status'] = 'approved'
        elif score >= 5:
            assigned_role = 'seeker'
            session['status'] = 'conditionally_approved'
        else:
            # Check if no_role is configured
            if self.bot_config.get('no_role_id'):
                assigned_role = 'no'
                session['status'] = 'assigned_no_role'
            else:
                assigned_role = None
                session['status'] = 'rejected'
        
        # Store final results
        session['final_score'] = score
        session['ai_reasoning'] = ai_result.get('reasoning', '')
        session['assigned_role'] = assigned_role
        
        # Assign Discord role
        await self.assign_discord_role(user, assigned_role)
        
        # Send thank you message to user
        await self.send_verification_thank_you(user, assigned_role, score)
        
        # Send notifications
        await self.send_verification_notifications(user, score, assigned_role, ai_result)

    async def score_responses_with_ai(self, responses: List[str], questions: List[str]) -> Dict:
        """Score responses using AI with fallback only when both API keys fail"""
        
        # Check if we should use AI based on rate limits
        if not self.should_use_ai():
            logger.info("🔄 AI rate limit reached - using enhanced fallback scoring")
            return self.fallback_scoring(responses, questions)
        
        # Check cache first
        cache_data = {'responses': responses, 'questions': questions}
        cache_key = self.get_cache_key('responses', cache_data)
        if cache_key in self.ai_cache:
            logger.info(f"💾 Using cached AI response scoring")
            return self.ai_cache[cache_key]
        
        # Enhanced fallback scoring first - but ALWAYS use AI for final verification
        fallback_result = self.fallback_scoring(responses, questions)
        fallback_score = fallback_result.get('score', 5)
        
        # Check for obvious copy-paste or invalid responses that need AI review
        suspicious_patterns = []
        for i, (response, question) in enumerate(zip(responses, questions)):
            # Check if response is suspiciously similar to question
            response_lower = response.lower().strip()
            question_lower = question.lower().strip()
            
            if len(response_lower) > 10 and response_lower in question_lower:
                suspicious_patterns.append(f"Q{i+1}: Response appears to be copy-pasted from question")
            elif len(response_lower) < 5:
                suspicious_patterns.append(f"Q{i+1}: Response too short")
            elif response_lower == question_lower:
                suspicious_patterns.append(f"Q{i+1}: Response identical to question")
        
        # If suspicious patterns detected, force AI review regardless of score
        if suspicious_patterns:
            logger.warning(f"🚨 Suspicious response patterns detected: {'; '.join(suspicious_patterns)}")
            # Force AI review for suspicious cases
        
        # ALWAYS use AI for verification unless rate limited - no "clear case" bypass
        logger.info(f"📊 Fallback score: {fallback_score}/10 - Proceeding with AI verification")
        
        # Try AI with both primary and backup keys before falling back
        max_retries = 3  # Increased to properly try both keys
        
        for attempt in range(1, max_retries + 1):
            try:
                # Get available AI key with backup support
                api_key = self.get_available_ai_key()
                if not api_key:
                    logger.warning("⚠️ No AI API key available - using fallback")
                    fallback_result['ai_optimization'] = 'no_api_key'
                    return fallback_result
                
                # Track API usage
                self.ai_call_count += 1
                logger.info(f"📊 AI verification call #{self.ai_call_count}/{self.ai_daily_limit} (using {self.ai_current_key} key)")
                
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel('gemini-2.0-flash')
                
                # Build FULL AI prompt using config files - not the optimized version
                prompt = build_complete_ai_prompt(questions, responses, 0)  # Use 0 for suspicion to focus on responses only
                
                # Generate response with timeout
                def generate_content():
                    response = model.generate_content(prompt)
                    return response.text
                
                ai_text = await asyncio.get_event_loop().run_in_executor(None, generate_content)
                
                if ai_text:
                    result = self.parse_ai_response(ai_text)
                    result['attempt'] = attempt
                    result['fallback_score'] = fallback_score
                    result['ai_optimization'] = 'ai_success'
                    result['api_key_used'] = self.ai_current_key
                    
                    # Cache successful result
                    self.ai_cache[cache_key] = result
                    
                    # Manage cache size
                    if len(self.ai_cache) > 50:  # Smaller cache for memory efficiency
                        keys_to_remove = list(self.ai_cache.keys())[:10]
                        for key in keys_to_remove:
                            del self.ai_cache[key]
                    
                    logger.info(f"✅ AI scoring successful: {fallback_score} → {result.get('score', 'N/A')} (attempt {attempt}, {self.ai_current_key} key)")
                    return result
                else:
                    logger.warning(f"AI returned empty response (attempt {attempt} with {self.ai_current_key} key)")
                    
            except Exception as e:
                logger.error(f"AI scoring error (attempt {attempt} with {self.ai_current_key} key): {e}")
                
                # Try switching to backup key if primary failed
                if self.ai_current_key == 'primary' and self.ai_backup_key and attempt < max_retries:
                    self.ai_current_key = 'backup'
                    logger.info("🔄 Switching to backup AI key due to primary key failure")
                    continue
                    
                # If backup key also fails, or no backup available
                if attempt == max_retries:
                    logger.error("❌ Both AI keys failed - falling back to rule-based scoring")
                    fallback_result['ai_optimization'] = 'both_keys_failed'
                    return fallback_result
                continue
        
        # Return enhanced fallback only if both AI keys completely fail
        logger.error("❌ AI verification failed after all attempts with both keys")
        fallback_result['ai_optimization'] = 'ai_failed_all_retries'
        return fallback_result

    def build_scoring_prompt(self, responses: List[str], questions: List[str], suspicion_score: int) -> str:
        """Build AI scoring prompt using external configuration file"""
        return build_complete_ai_prompt(questions, responses, suspicion_score)
    
    def build_optimized_scoring_prompt(self, responses: List[str], questions: List[str], fallback_score: int) -> str:
        """Build optimized AI prompt for refinement scoring - much shorter to save tokens"""
        
        # Format responses concisely
        qa_pairs = []
        for i, (q, r) in enumerate(zip(questions, responses), 1):
            qa_pairs.append(f"Q{i}: {q[:60]}{'...' if len(q) > 60 else ''}\nA{i}: {r[:100]}{'...' if len(r) > 100 else ''}")
        
        qa_text = "\n\n".join(qa_pairs)
        
        # Shortened, focused prompt for refinement
        prompt = f"""
KRISHNA VERIFICATION REFINEMENT

Rule-based score: {fallback_score}/10

{qa_text}

Refine score (0-10) considering:
- Spiritual sincerity vs knowledge 
- Respectful tone and humility
- Genuine seeking vs superficial answers
- Copy-paste or invalid responses (should score 0-2)

Reply format: SCORE: X
REASON: [one sentence]
"""
        
        return prompt

    def parse_ai_response(self, ai_text: str) -> Dict:
        """Parse AI response into structured data"""
        try:
            # Log the full AI response for debugging
            logger.info(f"🤖 Full AI response: {ai_text[:500]}...")
            
            # Extract score - try multiple patterns
            score_match = re.search(r'(?:SCORE|FINAL.*SCORE|OVERALL.*SCORE):\s*(\d+)', ai_text, re.IGNORECASE)
            if not score_match:
                # Try to find just a number at the end
                score_match = re.search(r'(\d+)\s*(?:/10)?(?:\s*$|\s*\n)', ai_text)
            score = int(score_match.group(1)) if score_match else 5
            
            # Extract reasoning - try multiple patterns
            reasoning_match = re.search(r'(?:REASON|REASONING|EXPLANATION|ANALYSIS):\s*(.+)', ai_text, re.DOTALL | re.IGNORECASE)
            if not reasoning_match:
                # Try to extract the main text as reasoning
                lines = ai_text.strip().split('\n')
                reasoning_lines = [line for line in lines if not re.match(r'^\s*(?:SCORE|FINAL)', line, re.IGNORECASE)]
                reasoning = '\n'.join(reasoning_lines[:3]).strip() if reasoning_lines else "No reasoning provided"
            else:
                reasoning = reasoning_match.group(1).strip()
            
            return {
                'score': max(0, min(10, score)),  # Clamp to 0-10
                'reasoning': reasoning[:500]  # Limit reasoning length
            }
        except Exception as e:
            logger.error(f"Error parsing AI response: {e}")
            return {'score': 5, 'reasoning': 'Error processing AI response'}

    def fallback_scoring(self, responses: List[str], questions: List[str] = None) -> Dict:
        """Advanced fallback scoring incorporating spiritual discernment when AI is unavailable"""
        score = 5  # Default neutral score
        reasoning_parts = []
        
        for i, response in enumerate(responses):
            response_lower = response.lower()
            response_points = 0
            
            # 1. Spiritual humility indicators (+2 to +3)
            humility_words = ['learn', 'don\'t know', 'want to understand', 'feel peace', 'inspired', 'humble', 'mercy', 'guidance']
            if any(phrase in response_lower for phrase in humility_words):
                response_points += 2
                reasoning_parts.append(f"Q{i+1}: Shows spiritual humility")
            
            # 2. Devotional mood indicators (+2)
            devotional_words = ['krishna', 'devotion', 'service', 'chanting', 'prayer', 'temple', 'devotees']
            if any(word in response_lower for word in devotional_words):
                response_points += 2
                reasoning_parts.append(f"Q{i+1}: Mentions devotional concepts")
            
            # 3. Genuine seeking indicators (+1)
            seeking_words = ['spiritual', 'connection', 'divine', 'peace', 'grow', 'journey']
            if any(word in response_lower for word in seeking_words):
                response_points += 1
            
            # 4. Red flags for impersonalism (-2)
            impersonal_phrases = ['all gods same', 'we are all god', 'i am god', 'all paths equal', 'krishna is one of many']
            if any(phrase in response_lower for phrase in impersonal_phrases):
                response_points -= 2
                reasoning_parts.append(f"Q{i+1}: Contains impersonalist views")
            
            # 5. Mockery or offense (-3 to -5)
            offensive_words = ['cult', 'fake', 'nonsense', 'stupid', 'bullshit', 'cow worship', 'mythology']
            if any(word in response_lower for word in offensive_words):
                response_points -= 3
                reasoning_parts.append(f"Q{i+1}: Contains offensive language")
            
            # 6. Passive argumentative mood (-1)
            challenging_phrases = ['is krishna real though', 'why would anyone believe', 'don\'t you think this is', 'prove it']
            if any(phrase in response_lower for phrase in challenging_phrases):
                response_points -= 1
                reasoning_parts.append(f"Q{i+1}: Shows challenging/testing attitude")
            
            # 7. Spiritual ego indicators (-1)
            ego_phrases = ['i am already spiritual', 'i don\'t need', 'i am enlightened', 'transcended religion']
            if any(phrase in response_lower for phrase in ego_phrases):
                response_points -= 1
                reasoning_parts.append(f"Q{i+1}: Shows spiritual pride")
            
            # 8. Vulnerability (should not be penalized)
            vulnerable_words = ['lost', 'confused', 'hurt', 'struggling', 'difficult']
            if any(word in response_lower for word in vulnerable_words):
                # Don't penalize vulnerability, but check if it's accompanied by humility
                if any(word in response_lower for word in ['want', 'hope', 'help', 'learn']):
                    response_points += 1
                    reasoning_parts.append(f"Q{i+1}: Shows vulnerable but seeking heart")
            
            # 9. Length and effort consideration
            if len(response.strip()) < 5:
                response_points -= 2  # Severely penalize very short responses
            elif len(response.strip()) > 50:  # Thoughtful length
                response_points += 0.5
            
            # 10. Check for copy-paste responses (CRITICAL)
            response_lower = response.lower().strip()
            # Get the corresponding question for this response
            question_lower = ""
            if questions and i < len(questions):
                question_lower = questions[i].lower().strip()
            
            # Detect if response is suspiciously similar to question
            if question_lower and len(response_lower) > 10:
                if response_lower == question_lower:
                    response_points -= 8  # Extreme penalty for identical response
                    reasoning_parts.append(f"Q{i+1}: IDENTICAL TO QUESTION")
                elif response_lower in question_lower or question_lower in response_lower:
                    response_points -= 5  # Massive penalty for copy-paste
                    reasoning_parts.append(f"Q{i+1}: COPY-PASTE DETECTED")
            
            # 11. Detect generic/template responses
            generic_responses = [
                'i want to learn more', 'i am interested', 'tell me more', 
                'i would like to know', 'please explain', 'i need guidance',
                'i want to understand', 'i seek knowledge'
            ]
            
            if any(generic.lower() in response_lower for generic in generic_responses) and len(response.strip()) < 30:
                response_points -= 1
                reasoning_parts.append(f"Q{i+1}: Generic template response")
            
            score += response_points
        
        # Final adjustments
        final_score = max(0, min(10, int(score)))
        
        if not reasoning_parts:
            reasoning = "Fallback scoring applied - responses show neutral spiritual disposition"
        else:
            reasoning = f"Fallback scoring: {'; '.join(reasoning_parts[:3])}"  # Limit to first 3 observations
        
        return {
            'score': final_score,
            'reasoning': reasoning
        }

    async def assign_discord_role(self, user, assigned_role: Optional[str]):
        """Assign Discord role based on verification result"""
        if not assigned_role:
            return
            
        try:
            guild = self.get_guild(self.target_server_id)
            if not guild:
                return
                
            member = guild.get_member(user.id)
            if not member:
                return
            
            # Handle "no" role assignment (score 0-4)
            if assigned_role == "no":
                role_id = self.bot_config.get('no_role_id')
                if role_id:
                    role = guild.get_role(int(role_id))
                    if role:
                        await member.add_roles(role)
                        logger.info(f"Assigned {role.name} role to {member}")
                else:
                    logger.info(f"No role assigned to {member} (no role configured)")
            else:
                # Handle devotee/seeker roles
                role_id_key = f"{assigned_role}_role_id"
                role_id = self.bot_config.get(role_id_key)
                
                if role_id:
                    role = guild.get_role(int(role_id))
                    if role:
                        await member.add_roles(role)
                        logger.info(f"Assigned {role.name} role to {member}")
                        
                        # Send welcome message to general chat for verified users
                        await self.send_general_chat_welcome(member, assigned_role)
                    
        except Exception as e:
            logger.error(f"Error assigning role: {e}")

    async def send_general_chat_welcome(self, user, assigned_role: str):
        """Send simple welcome message to general chat for verified users"""
        try:
            # Only send welcome for verified users (devotee/seeker)
            if assigned_role not in ['devotee', 'seeker']:
                return
                
            channel_id = self.bot_config.get('general_chat_channel_id')
            if not channel_id:
                logger.info("⚠️ No general chat channel configured - skipping welcome message")
                return
                
            channel = self.get_channel(int(channel_id))
            if not channel:
                # Try to fetch channel if not cached
                try:
                    guild = self.get_guild(self.target_server_id)
                    if guild:
                        channel = await guild.fetch_channel(int(channel_id))
                    if not channel:
                        logger.warning(f"⚠️ General chat channel {channel_id} not found or inaccessible")
                        return
                except Exception as e:
                    logger.warning(f"⚠️ Failed to fetch general chat channel {channel_id}: {e}")
                    return
            
            # Send simple text welcome message for both seeker and devotee roles
            welcome_message = f"Hare Krishna, welcome dear devotee! {user.mention}\nMay your journey be enriched with devotee association."
            
            # Send the welcome message with error handling
            try:
                await channel.send(welcome_message)
                logger.info(f"✅ Sent general chat welcome message for {user} ({assigned_role}) to #{channel.name}")
            except discord.Forbidden:
                logger.error(f"❌ No permission to send message in general chat channel #{channel.name}")
            except discord.HTTPException as e:
                logger.error(f"❌ Failed to send welcome message to #{channel.name}: {e}")
            
        except Exception as e:
            logger.error(f"❌ Error sending general chat welcome message: {e}")

    async def send_verification_thank_you(self, user, assigned_role: Optional[str], score: int):
        """Send thank you message to user after completing verification"""
        try:
            # Create thank you embed based on role assignment
            if assigned_role == 'devotee':
                embed = discord.Embed(
                    title="🙏 Thank You for Your Time!",
                    description="Hare Krishna! Thank you for taking the time to share your spiritual journey with us.",
                    color=0x4CAF50
                )
                embed.add_field(
                    name="✅ Welcome to the Community",
                    value="You have been welcomed as a **Devotee**! 🌸\n\nYour sincere responses show a beautiful devotional heart. We're excited to have you as part of our Krishna-conscious family.",
                    inline=False
                )
                embed.add_field(
                    name="🕉️ Next Steps",
                    value="Feel free to explore all channels and participate in our community discussions. May Krishna's blessings be with you!",
                    inline=False
                )
            elif assigned_role == 'seeker':
                embed = discord.Embed(
                    title="🙏 Thank You for Your Time!",
                    description="Hare Krishna! Thank you for taking the time to share your thoughts with us.",
                    color=0x2196F3
                )
                embed.add_field(
                    name="🌱 Welcome as a Seeker",
                    value="You have been welcomed as a **Seeker**! 🌿\n\nYour responses show genuine interest in spiritual growth. We're happy to support you on your journey toward Krishna consciousness.",
                    inline=False
                )
                embed.add_field(
                    name="📚 Next Steps",
                    value="Explore our beginner-friendly channels and feel free to ask questions. Every sincere seeker is welcome here!",
                    inline=False
                )
            elif assigned_role == 'no':
                embed = discord.Embed(
                    title="🙏 Thank You for Your Time!",
                    description="Hare Krishna! Thank you for taking the time to complete our verification questions.",
                    color=0xFF9800
                )
                embed.add_field(
                    name="📋 Verification Complete",
                    value="Your responses have been reviewed. You have been assigned a role that gives you access to certain areas of our community.",
                    inline=False
                )
                embed.add_field(
                    name="🤝 Moving Forward",
                    value="If you have any questions about your access level, please feel free to reach out to our moderators.",
                    inline=False
                )
            else:
                embed = discord.Embed(
                    title="🙏 Thank You for Your Time!",
                    description="Hare Krishna! Thank you for taking the time to complete our verification questions.",
                    color=0xFF9800
                )
                embed.add_field(
                    name="⏳ Under Review",
                    value="Your responses are being reviewed by our moderators. You will be notified once a decision is made.",
                    inline=False
                )
                embed.add_field(
                    name="🕊️ Patience",
                    value="We appreciate your patience as we carefully consider each member's application with care and devotion.",
                    inline=False
                )
            
            embed.add_field(
                name="🌸 Gratitude",
                value="We truly appreciate the time you took to answer our questions thoughtfully. Your sincerity means a lot to our community.",
                inline=False
            )
            
            embed.set_footer(text="🕉️ Hare Krishna, Hare Krishna, Krishna Krishna, Hare Hare")
            
            # Send DM to user
            try:
                await user.send(embed=embed)
                logger.info(f"Sent thank you message to {user}")
            except discord.Forbidden:
                logger.warning(f"Could not send thank you DM to {user} - DMs disabled")
            except Exception as e:
                logger.error(f"Error sending thank you DM to {user}: {e}")
                
        except Exception as e:
            logger.error(f"Error in send_verification_thank_you: {e}")

    async def send_verification_notifications(self, user, score: int, assigned_role: Optional[str], ai_result: Dict):
        """Send verification notifications to public and admin channels"""
        await self.send_public_notification(user, assigned_role)
        await self.send_admin_notification(user, score, assigned_role, ai_result)

    async def send_public_notification(self, user, assigned_role: Optional[str]):
        """Send public verification notification"""
        try:
            channel_id = self.bot_config.get('verification_channel_id')
            if not channel_id:
                return
                
            channel = self.get_channel(int(channel_id))
            if not channel:
                return
            
            if assigned_role == 'devotee':
                embed = discord.Embed(
                    title="✅ New Devotee Verified!",
                    description=f"🙏 {user.mention} has been welcomed as a **Devotee**! 🌸",
                    color=0x4CAF50
                )
            elif assigned_role == 'seeker':
                embed = discord.Embed(
                    title="⚪ New Seeker Joined",
                    description=f"🌱 {user.mention} has been added as a **Seeker**. Welcome to the community!",
                    color=0x2196F3
                )
            elif assigned_role == 'no':
                # Get the no_role name for display
                no_role_id = self.bot_config.get('no_role_id')
                role_name = "Limited Access"
                if no_role_id:
                    guild = self.get_guild(self.target_server_id)
                    if guild:
                        role = guild.get_role(int(no_role_id))
                        if role:
                            role_name = role.name
                
                embed = discord.Embed(
                    title="📝 Verification Complete",
                    description=f"📋 {user.mention} has been assigned {role_name} role.",
                    color=0xFF9800
                )
            else:
                embed = discord.Embed(
                    title="⏳ Verification Under Review",
                    description=f"📋 {user.mention}'s verification is being reviewed by moderators.",
                    color=0xFF9800
                )
            
            embed.set_footer(text="🕉️ Hare Krishna")
            await channel.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error sending public notification: {e}")

    async def send_admin_notification(self, user, score: int, assigned_role: Optional[str], ai_result: Dict):
        """Send clean, compact admin notification with thread for details"""
        try:
            channel_id = self.bot_config.get('admin_channel_id')
            if not channel_id:
                return
                
            channel = self.get_channel(int(channel_id))
            if not channel:
                return
            
            session = self.verification_sessions.get(user.id, {})
            
            # Send compact main notification
            main_message = await self.send_compact_admin_summary(channel, user, score, assigned_role, ai_result)
            
            # Create thread for detailed information
            if main_message:
                try:
                    thread = await main_message.create_thread(
                        name=f"📋 {user.display_name[:15]} - Details",
                        auto_archive_duration=1440  # 24 hours
                    )
                    
                    # Send detailed information in thread
                    await self.send_detailed_verification_thread(thread, user, session, ai_result)
                    
                except Exception as thread_error:
                    logger.warning(f"Could not create thread, sending details in channel: {thread_error}")
                    # Fallback: send abbreviated details in main channel
                    await self.send_abbreviated_details(channel, user, session, ai_result)
            
        except Exception as e:
            logger.error(f"Error sending admin notification: {e}")
    
    async def send_compact_admin_summary(self, channel, user, score: int, assigned_role: Optional[str], ai_result: Dict):
        """Send compact one-line admin notification with key details"""
        # Build admin mentions
        admin_mentions = []
        admin_role_1_id = self.bot_config.get('admin_role_1_id')
        admin_role_2_id = self.bot_config.get('admin_role_2_id')
        
        if admin_role_1_id:
            admin_mentions.append(f"<@&{admin_role_1_id}>")
        if admin_role_2_id:
            admin_mentions.append(f"<@&{admin_role_2_id}>")
        
        mentions_text = " ".join(admin_mentions) if admin_mentions else ""
        
        # Role emoji and color
        if assigned_role == "devotee":
            role_emoji = "🏵️"
            embed_color = 0x4CAF50  # Green
        elif assigned_role == "seeker":
            role_emoji = "🌱"  
            embed_color = 0xFF9800  # Orange
        else:
            role_emoji = "⚠️"
            embed_color = 0xF44336  # Red
        
        # Get AI analysis summary - ensure it's actual AI reasoning, not fallback
        ai_summary = ai_result.get('reasoning', 'AI analysis pending')
        
        # Check if this is fallback text and provide better summary
        if ai_summary.startswith('Fallback scoring applied'):
            ai_summary = f"Score: {ai_result.get('score', 'N/A')}/10 • Evaluation completed"
        elif len(ai_summary) > 100:
            # Truncate but preserve meaning for compact view
            ai_summary = ai_summary[:97] + "..."
        
        # Get suspicion score from session
        session = self.verification_sessions.get(user.id, {})
        suspicion_score = session.get('suspicion_score', 'N/A')
        
        embed = discord.Embed(
            title=f"{role_emoji} Verification Complete",
            description=f"**{user.display_name}** • Verification Score: {score}/10 • **Suspicion: {suspicion_score}/4** • Role: {assigned_role or 'None'}",
            color=embed_color,
            timestamp=datetime.utcnow()
        )
        
        # Add suspicion details breakdown
        session = self.verification_sessions.get(user.id, {})
        suspicion_score = session.get('suspicion_score', 0)
        
        # Calculate suspicion factors for display
        account_age_days = (datetime.utcnow() - user.created_at.replace(tzinfo=None)).days
        has_avatar = bool(user.avatar)
        
        suspicion_factors = []
        if account_age_days < 7:
            suspicion_factors.append(f"🆕 New account ({account_age_days}d)")
        elif account_age_days < 30:
            suspicion_factors.append(f"⏰ Recent account ({account_age_days}d)")
        else:
            suspicion_factors.append(f"✅ Established account ({account_age_days}d)")
            
        if not has_avatar:
            suspicion_factors.append("❓ No custom avatar")
        else:
            suspicion_factors.append("✅ Has custom avatar")
            
        # Check username patterns
        username = user.name.lower()
        if re.search(r'\d{4,}', username):
            suspicion_factors.append("⚠️ Many numbers in username")
        elif re.search(r'(discord|bot|fake|test)', username):
            suspicion_factors.append("🚨 Suspicious keywords in username")
        else:
            suspicion_factors.append("✅ Normal username pattern")
        
        suspicion_text = "\n".join(suspicion_factors[:3])  # Show top 3 factors
        
        embed.add_field(
            name=f"🎯 Suspicion Analysis ({suspicion_score}/4)",
            value=suspicion_text,
            inline=True
        )
        
        embed.add_field(
            name="🤖 AI Assessment",
            value=ai_summary,
            inline=True
        )
        
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Click to view thread for complete details")
        
        # Send compact message with mentions
        content = mentions_text if mentions_text else None
        return await channel.send(content=content, embed=embed)
    
    async def send_detailed_verification_thread(self, thread, user, session: Dict, ai_result: Dict):
        """Send complete verification details in thread"""
        try:
            # Welcome message in thread with suspicion score
            session = self.verification_sessions.get(user.id, {})
            suspicion_score = session.get('suspicion_score', 'N/A')
            
            await thread.send(f"📋 **Complete Verification Details for {user.display_name}**\n{'-' * 50}")
            
            # Detailed suspicion score breakdown
            account_age_days = (datetime.utcnow() - user.created_at.replace(tzinfo=None)).days
            has_avatar = bool(user.avatar)
            username = user.name.lower()
            
            # Pre-calculate values to avoid backslash in f-string
            username_pattern = r'\d{4,}|(discord|bot|fake|test)'
            username_status = '✅ Normal' if not re.search(username_pattern, username) else '⚠️ Suspicious'
            
            age_status = '(🆕 Very New)' if account_age_days < 7 else '(⏰ Recent)' if account_age_days < 30 else '(✅ Established)'
            avatar_status = '✅ Custom avatar' if has_avatar else '❓ Default avatar (no custom image)'
            
            if suspicion_score <= 1:
                quality_status = '🟢 Looks legitimate'
            elif suspicion_score <= 2:
                quality_status = '🟡 Some concerns'
            elif suspicion_score <= 3:
                quality_status = '🟠 Multiple red flags'
            else:
                quality_status = '🔴 High suspicion'
            
            if suspicion_score <= 1:
                questions_given = 'Standard difficulty'
            elif suspicion_score <= 3:
                questions_given = 'Medium difficulty'
            else:
                questions_given = 'High difficulty (extra verification)'
            
            suspicion_details = f"""
🎯 **SUSPICION SCORE: {suspicion_score}/4**

**Profile Analysis:**
• **Username:** {user.name} {username_status}
• **Account Age:** {account_age_days} days {age_status}
• **Avatar:** {avatar_status}
• **Profile Quality:** {quality_status}

**Questions Given:** {questions_given}
"""
            
            await thread.send(suspicion_details)
            
            # Send complete Q&A - FULL ANSWERS, NO TRUNCATION
            responses = session.get('responses', [])
            if responses:
                await thread.send("📝 **Complete Questions & Answers:**\n" + "─" * 40)
                
                for i, response in enumerate(responses, 1):
                    question = response.get('question', f'Question {i}')
                    answer = response.get('response', 'No answer provided')
                    
                    # Send each Q&A pair separately to avoid truncation
                    question_msg = f"**❓ Question {i}:**\n{question}"
                    answer_msg = f"**💬 Answer {i}:**\n{answer}"
                    
                    await thread.send(question_msg)
                    await thread.send(answer_msg)
                    
                    # Add separator except for last pair
                    if i < len(responses):
                        await thread.send("─" * 20)
            
            # Send AI analysis - DYNAMIC AI REASONING
            if ai_result:
                await thread.send("\n🤖 **AI Analysis & Evaluation:**\n" + "─" * 40)
                
                # Overall score
                score = ai_result.get('score', 'N/A')
                await thread.send(f"**🎯 Overall Score:** {score}/10")
                
                # AI reasoning - check if it's actual AI analysis or fallback
                reasoning = ai_result.get('reasoning', 'No AI analysis available')
                if reasoning.startswith('Fallback scoring applied'):
                    await thread.send(f"**⚠️ Note:** AI analysis was unavailable, score based on response length and basic evaluation")
                    await thread.send(f"**📊 Fallback Assessment:** {reasoning}")
                else:
                    await thread.send(f"**🧠 AI Reasoning:**\n{reasoning}")
                
                # Detailed scores if available
                spiritual_seeking = ai_result.get('spiritual_seeking')
                devotional_tone = ai_result.get('devotional_tone')
                respectfulness = ai_result.get('respectfulness')
                humility = ai_result.get('humility')
                
                if any([spiritual_seeking, devotional_tone, respectfulness, humility]):
                    await thread.send("\n**📊 Detailed Category Scores:**")
                    if spiritual_seeking:
                        await thread.send(f"• **Spiritual Seeking:** {spiritual_seeking}/10")
                    if devotional_tone:
                        await thread.send(f"• **Devotional Tone:** {devotional_tone}/10")
                    if respectfulness:
                        await thread.send(f"• **Respectfulness:** {respectfulness}/10")
                    if humility:
                        await thread.send(f"• **Humility:** {humility}/10")
        
        except Exception as e:
            logger.error(f"Error sending thread details: {e}")
            await thread.send("❌ Error loading verification details")
    
    async def send_abbreviated_details(self, channel, user, session: Dict, ai_result: Dict):
        """Send abbreviated details if thread creation fails"""
        try:
            content = f"📋 **{user.display_name} - Quick Summary**\n"
            responses = session.get('responses', [])
            if responses and len(responses) > 0:
                last_response = responses[-1].get('response', 'No response')[:100]
                content += f"Last Answer: {last_response}...\n"
            
            if ai_result:
                reasoning = ai_result.get('reasoning', '')[:150]
                content += f"AI: {reasoning}..."
            
            await channel.send(content)
        except Exception as e:
            logger.error(f"Error sending abbreviated details: {e}")
    
    def _split_message(self, content: str, max_length: int = 1900) -> List[str]:
        """Split long messages into smaller parts"""
        if len(content) <= max_length:
            return [content]
        
        parts = []
        while content:
            if len(content) <= max_length:
                parts.append(content)
                break
            
            # Find the last newline before max_length
            split_pos = content.rfind('\n', 0, max_length)
            if split_pos == -1:
                split_pos = max_length
            
            parts.append(content[:split_pos])
            content = content[split_pos:].lstrip()
        
        return parts
    
    async def send_complete_questions_and_answers(self, channel, user, responses: List[Dict]):
        """Send complete questions and answers, splitting into multiple messages if needed"""
        if not responses:
            await channel.send("📝 **No responses recorded**")
            return
        
        # Send header
        header_embed = discord.Embed(
            title="📝 Complete Questions & Answers",
            description=f"Full verification responses from {user.mention}",
            color=0x2196F3
        )
        await channel.send(embed=header_embed)
        
        # Send each Q&A pair
        for i, resp in enumerate(responses):
            question = resp.get('question', 'Unknown question')
            answer = resp.get('response', 'No response')
            
            # Create individual embed for each Q&A
            qa_embed = discord.Embed(
                title=f"Question {i+1}",
                color=0x4CAF50
            )
            
            # Add question (limit to 1024 chars for embed field)
            if len(question) > 1024:
                qa_embed.add_field(name="❓ Question", value=question[:1024], inline=False)
                # Send remaining question text as separate message if needed
                remaining_q = question[1024:]
                while remaining_q:
                    chunk = remaining_q[:2000]  # Discord message limit
                    remaining_q = remaining_q[2000:]
                    await channel.send(f"**Question {i+1} (continued):** {chunk}")
            else:
                qa_embed.add_field(name="❓ Question", value=question, inline=False)
            
            await channel.send(embed=qa_embed)
            
            # Send answer - handle long answers by splitting into multiple messages
            answer_header = f"**💬 Answer {i+1}:**"
            
            if len(answer) <= 1900:  # Leave room for header
                await channel.send(f"{answer_header}\n{answer}")
            else:
                # Split long answer into chunks
                await channel.send(answer_header)
                remaining_answer = answer
                chunk_num = 1
                while remaining_answer:
                    chunk = remaining_answer[:2000]
                    remaining_answer = remaining_answer[2000:]
                    chunk_header = f"**Answer {i+1} (part {chunk_num}):**" if chunk_num > 1 else ""
                    await channel.send(f"{chunk_header}\n{chunk}" if chunk_header else chunk)
                    chunk_num += 1
            
            # Add separator between Q&A pairs
            if i < len(responses) - 1:
                await channel.send("─" * 30)
    
    async def send_ai_feedback_details(self, channel, user, ai_result: Dict):
        """Send complete AI feedback and analysis"""
        if not ai_result:
            await channel.send("🤖 **No AI analysis available**")
            return
        
        # Send AI analysis header
        ai_header_embed = discord.Embed(
            title="🤖 AI Analysis & Feedback",
            description=f"Detailed AI evaluation for {user.mention}",
            color=0xFF9800
        )
        await channel.send(embed=ai_header_embed)
        
        # Send AI reasoning (full version)
        reasoning = ai_result.get('reasoning', 'No reasoning provided')
        if reasoning and reasoning != 'No reasoning provided':
            reasoning_header = "**🧠 AI Reasoning:**"
            
            if len(reasoning) <= 1900:
                await channel.send(f"{reasoning_header}\n{reasoning}")
            else:
                await channel.send(reasoning_header)
                remaining_reasoning = reasoning
                chunk_num = 1
                while remaining_reasoning:
                    chunk = remaining_reasoning[:2000]
                    remaining_reasoning = remaining_reasoning[2000:]
                    chunk_header = f"**Reasoning (part {chunk_num}):**" if chunk_num > 1 else ""
                    await channel.send(f"{chunk_header}\n{chunk}" if chunk_header else chunk)
                    chunk_num += 1
        
        # Send individual scores if available
        scores = ai_result.get('scores', {})
        if scores:
            scores_embed = discord.Embed(
                title="📊 Detailed Scoring Breakdown",
                color=0x9C27B0
            )
            
            for category, score in scores.items():
                scores_embed.add_field(
                    name=category.replace('_', ' ').title(),
                    value=f"**{score}/10**",
                    inline=True
                )
            
            await channel.send(embed=scores_embed)
        
        # Send any additional AI feedback
        additional_feedback = ai_result.get('additional_notes', '')
        if additional_feedback:
            feedback_header = "**📋 Additional AI Notes:**"
            
            if len(additional_feedback) <= 1900:
                await channel.send(f"{feedback_header}\n{additional_feedback}")
            else:
                await channel.send(feedback_header)
                remaining_feedback = additional_feedback
                chunk_num = 1
                while remaining_feedback:
                    chunk = remaining_feedback[:2000]
                    remaining_feedback = remaining_feedback[2000:]
                    chunk_header = f"**Notes (part {chunk_num}):**" if chunk_num > 1 else ""
                    await channel.send(f"{chunk_header}\n{chunk}" if chunk_header else chunk)
                    chunk_num += 1
        
        # Send final separator
        final_embed = discord.Embed(
            description="═" * 40,
            color=0x9C27B0
        )
        final_embed.set_footer(text="🕉️ End of verification details")
        await channel.send(embed=final_embed)


    async def setup_command_logic(self, interaction: discord.Interaction, 
                                devotee_role: discord.Role,
                                seeker_role: discord.Role,
                                verification_channel: discord.TextChannel,
                                admin_channel: discord.TextChannel,
                                general_chat_channel: discord.TextChannel = None,
                                dm_questions_channel: discord.TextChannel = None,
                                log_channel: discord.TextChannel = None,
                                welcome_channel: discord.TextChannel = None,
                                no_role: discord.Role = None,
                                admin_role_1: discord.Role = None,
                                admin_role_2: discord.Role = None):
        """🛠️ Complete setup command for comprehensive bot configuration"""
        
        # Check if user is admin
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only administrators can configure the bot.", ephemeral=True)
            return
        
        # Check server restriction
        if interaction.guild.id != self.target_server_id:
            await interaction.response.send_message("❌ This bot is not configured for this server.", ephemeral=True)
            return
        
        try:
            # Store comprehensive configuration  
            config_data = {
                'server_id': str(interaction.guild.id),
                
                # Role Configuration
                'devotee_role_id': str(devotee_role.id),
                'seeker_role_id': str(seeker_role.id),
                'no_role_id': str(no_role.id) if no_role else None,
                'admin_role_1_id': str(admin_role_1.id) if admin_role_1 else None,
                'admin_role_2_id': str(admin_role_2.id) if admin_role_2 else None,
                
                # Channel Configuration
                'verification_channel_id': str(verification_channel.id),  # Public verification results
                'admin_channel_id': str(admin_channel.id),  # Detailed admin reports
                'general_chat_channel_id': str(general_chat_channel.id) if general_chat_channel else None,  # General chat welcome messages
                'dm_questions_channel_id': str(dm_questions_channel.id) if dm_questions_channel else None,  # Fallback for DM failures
                'log_channel_id': str(log_channel.id) if log_channel else None,  # Bot activity logs
                'welcome_channel_id': str(welcome_channel.id) if welcome_channel else None,  # Welcome messages
                
                # AI Configuration
                'ai_model': 'meta-llama/Llama-2-70b-chat-hf',
                'ai_prompt_version': 'prabhupada_v1',
                
                # System Configuration
                'is_configured': True,
                'configured_at': datetime.utcnow().isoformat(),
                'configured_by': str(interaction.user.id),
                
                # Question Flow Settings
                'dm_verification': True,  # Use DMs for questions
                'fallback_channel': dm_questions_channel.id if dm_questions_channel else verification_channel.id,
                'auto_role_assignment': True,  # Automatically assign roles based on scores
                'admin_notification': True,  # Send detailed reports to admin channel
                'public_announcements': True,  # Send public verification results
                
                # Scoring Thresholds
                'devotee_threshold': 8,  # Minimum score for devotee role
                'seeker_threshold': 5,   # Minimum score for seeker role
                'rejection_threshold': 4  # Below this = no role + admin review
            }
            
            # Save configuration persistently using env SERVER_ID
            if self.config_storage.save_config(config_data):
                # Update in-memory config
                self.bot_config = config_data
                logger.info(f"✅ Configuration saved persistently to Neon database by {interaction.user}")
            else:
                logger.error("❌ Failed to save configuration to disk")
                await interaction.response.send_message("❌ Failed to save configuration. Please try again.", ephemeral=True)
                return
            
            # Create comprehensive setup confirmation
            embed = discord.Embed(
                title="✅ Krishna Verification Bot - Complete Setup",
                description="All bot functions have been configured successfully! 🌸",
                color=0x4CAF50
            )
            
            # Role Configuration
            role_config = f"**Devotee Role (8-10):** {devotee_role.mention}\n**Seeker Role (5-7):** {seeker_role.mention}"
            if no_role:
                role_config += f"\n**No Role (0-4):** {no_role.mention}"
            else:
                role_config += f"\n**No Role (0-4):** None (user gets no role)"
            
            embed.add_field(
                name="🎭 Role Assignment",
                value=role_config,
                inline=False
            )
            
            # Channel Configuration
            channel_config = f"**Verification Results:** {verification_channel.mention}\n**Admin Reports:** {admin_channel.mention}"
            if general_chat_channel:
                channel_config += f"\n**General Chat Welcome:** {general_chat_channel.mention}"
            if dm_questions_channel:
                channel_config += f"\n**DM Fallback:** {dm_questions_channel.mention}"
            if log_channel:
                channel_config += f"\n**Activity Logs:** {log_channel.mention}"
            if welcome_channel:
                channel_config += f"\n**Welcome Messages:** {welcome_channel.mention}"
            
            embed.add_field(
                name="📺 Channel Configuration", 
                value=channel_config,
                inline=False
            )
            
            # Question Flow
            embed.add_field(
                name="❓ Verification Process",
                value="**Method:** DM-based questions\n**Questions:** 4 per user (1 entry + 2 reflective + 1 psychological)\n**AI Analysis:** Krishna-conscious evaluation",
                inline=False
            )
            
            # Scoring System
            embed.add_field(
                name="📊 Scoring System",
                value="**8-10 points:** @Devotee role + public welcome\n**5-7 points:** @Seeker role + admin review\n**0-4 points:** No role + admin alert",
                inline=False
            )
            
            embed.set_footer(text="🙏 Bot is now ready to welcome new devotees and seekers to Krishna consciousness!")
            
            await interaction.response.send_message(embed=embed)
            logger.info(f"Complete bot configuration set by {interaction.user} - All channels and roles configured")
            
            # Send a test message to admin channel
            if admin_channel:
                test_embed = discord.Embed(
                    title="🤖 Bot Configuration Test",
                    description="This is your admin channel for detailed verification reports. You'll receive:\n• User responses to all 4 questions\n• AI scoring and reasoning\n• Suspicion scores and account details\n• Role assignment decisions",
                    color=0x9C27B0
                )
                await admin_channel.send(embed=test_embed)
            
        except Exception as e:
            logger.error(f"Error during comprehensive setup: {e}")
            await interaction.response.send_message("❌ Error during setup. Please try again.", ephemeral=True)

    async def reload_questions_logic(self, interaction: discord.Interaction):
        """Reload questions from JSON file"""
        
        # Check if user is admin
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only administrators can reload questions.", ephemeral=True)
            return
        
        # Check server restriction
        if interaction.guild.id != self.target_server_id:
            await interaction.response.send_message("❌ This bot is not configured for this server.", ephemeral=True)
            return
        
        try:
            # Reload questions
            await self.load_questions()
            
            # Auto-reload AI config to maintain synchronization
            try:
                import importlib
                import ai_config
                importlib.reload(ai_config)
                logger.info("🔄 AI configuration automatically synchronized with questions")
                ai_sync_status = "✅ Synchronized"
            except Exception as ai_error:
                logger.warning(f"⚠️ Could not auto-sync AI config: {ai_error}")
                ai_sync_status = "⚠️ Manual sync needed"
            
            # Count questions
            entry_count = len(self.questions.get('entry', []))
            reflective_count = len(self.questions.get('reflective', []))
            trusted_count = len(self.questions.get('psychological', {}).get('trusted', []))
            medium_count = len(self.questions.get('psychological', {}).get('medium', []))
            high_count = len(self.questions.get('psychological', {}).get('high', []))
            
            embed = discord.Embed(
                title="🔄 Questions & AI Config Synchronized",
                description="Question bank reloaded and AI configuration automatically synchronized! 🌸",
                color=0x4CAF50
            )
            embed.add_field(name="Entry Questions", value=f"{entry_count} questions", inline=True)
            embed.add_field(name="Reflective Questions", value=f"{reflective_count} questions", inline=True)
            embed.add_field(name="Trusted Level", value=f"{trusted_count} questions", inline=True)
            embed.add_field(name="Medium Suspicion", value=f"{medium_count} questions", inline=True)
            embed.add_field(name="High Suspicion", value=f"{high_count} questions", inline=True)
            embed.add_field(name="🤖 AI Config", value=ai_sync_status, inline=True)
            embed.set_footer(text="🙏 Questions & AI stay synchronized automatically")
            
            await interaction.response.send_message(embed=embed)
            logger.info(f"Questions and AI config synchronized by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error reloading questions: {e}")
            await interaction.response.send_message("❌ Error reloading questions. Check the JSON file format.", ephemeral=True)

    async def question_stats_logic(self, interaction: discord.Interaction):
        """Display current question bank statistics"""
        
        # Check server restriction
        if interaction.guild.id != self.target_server_id:
            await interaction.response.send_message("❌ This bot is not configured for this server.", ephemeral=True)
            return
        
        try:
            # Count questions
            entry_count = len(self.questions.get('entry', []))
            reflective_count = len(self.questions.get('reflective', []))
            trusted_count = len(self.questions.get('psychological', {}).get('trusted', []))
            medium_count = len(self.questions.get('psychological', {}).get('medium', []))
            high_count = len(self.questions.get('psychological', {}).get('high', []))
            
            embed = discord.Embed(
                title="📊 Question Bank Statistics",
                description="Current question pool available for verification 🌸",
                color=0x2196F3
            )
            embed.add_field(name="🔹 Entry Questions (Q1)", value=f"{entry_count} available", inline=True)
            embed.add_field(name="🌼 Reflective Questions (Q2-Q3)", value=f"{reflective_count} available", inline=True)
            embed.add_field(name="✅ Trusted Level (Q4)", value=f"{trusted_count} available", inline=True)
            embed.add_field(name="⚪ Medium Suspicion (Q4)", value=f"{medium_count} available", inline=True)
            embed.add_field(name="🔴 High Suspicion (Q4)", value=f"{high_count} available", inline=True)
            
            total_combinations = entry_count * (reflective_count * (reflective_count - 1) // 2) * (trusted_count + medium_count + high_count)
            embed.add_field(name="🎲 Total Possible Combinations", value=f"{total_combinations:,}", inline=False)
            
            embed.set_footer(text="💡 Use /reload_questions to update from JSON file")
            
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Error displaying question stats: {e}")
            await interaction.response.send_message("❌ Error retrieving question statistics.", ephemeral=True)
    
    async def reload_ai_config_logic(self, interaction: discord.Interaction):
        """Reload AI configuration from ai_config.py"""
        # Check server authorization
        if interaction.guild_id != self.target_server_id:
            await interaction.response.send_message("❌ This bot is not configured for this server.", ephemeral=True)
            return
        
        try:
            # Reload the ai_config module
            import importlib
            import ai_config
            importlib.reload(ai_config)
            
            # Re-import the function to get the updated version
            global build_complete_ai_prompt
            from ai_config import build_complete_ai_prompt
            
            embed = discord.Embed(
                title="🤖 AI Configuration Reloaded",
                description="AI prompt configuration has been successfully reloaded from `ai_config.py`",
                color=0x00FF00
            )
            embed.add_field(
                name="📝 What's Updated", 
                value="• AI evaluation prompts\n• Scoring criteria\n• Response formatting\n• Question categories mapping", 
                inline=False
            )
            embed.add_field(
                name="🔄 Next Steps", 
                value="Changes will take effect for new verification sessions immediately.", 
                inline=False
            )
            embed.set_footer(text="💡 Modify ai_config.py to customize AI behavior")
            
            await interaction.response.send_message(embed=embed)
            logger.info("✅ AI configuration successfully reloaded")
            
        except Exception as e:
            logger.error(f"Error reloading AI configuration: {e}")
            await interaction.response.send_message(
                f"❌ Error reloading AI configuration: {str(e)}", 
                ephemeral=True
            )

    async def verify_command_logic(self, interaction: discord.Interaction):
        """🙏 Manual verification command for when DMs fail due to rate limiting"""
        
        # Check if interaction has already been responded to
        if interaction.response.is_done():
            logger.warning(f"⚠️ Interaction already acknowledged for {interaction.user}")
            return
        
        # Check if bot is configured
        if not self.bot_config.get('is_configured'):
            await interaction.response.send_message(
                "❌ Bot is not configured yet. An administrator needs to run `/setup` first.",
                ephemeral=True
            )
            return
        
        # Check if this is the verification channel
        verification_channel_id = self.bot_config.get('verification_channel_id')
        if not verification_channel_id or str(interaction.channel.id) != verification_channel_id:
            verification_channel = self.get_channel(int(verification_channel_id)) if verification_channel_id else None
            await interaction.response.send_message(
                f"❌ This command can only be used in the verification channel: {verification_channel.mention if verification_channel else 'Not configured'}",
                ephemeral=True
            )
            return
        
        member = interaction.user
        user_id = member.id
        
        # Check if user already has verification roles
        devotee_role_id = self.bot_config.get('devotee_role_id')
        seeker_role_id = self.bot_config.get('seeker_role_id')
        no_role_id = self.bot_config.get('no_role_id')
        
        user_roles = [role.id for role in member.roles]
        
        if (devotee_role_id and int(devotee_role_id) in user_roles) or \
           (seeker_role_id and int(seeker_role_id) in user_roles):
            await interaction.response.send_message(
                "✅ You are already verified! If you need to re-verify, please contact an administrator.",
                ephemeral=True
            )
            return
        elif no_role_id and int(no_role_id) in user_roles:
            # Users who received no role (0-4 score) cannot re-verify themselves
            await interaction.response.send_message(
                "❌ Your verification has been completed. You cannot start a new verification process.\n\n"
                "If you believe there was a mistake in your verification, please contact a moderator for assistance.",
                ephemeral=True
            )
            return
        
        # Check if user already has an active verification session
        if user_id in self.verification_sessions:
            session = self.verification_sessions[user_id]
            if session.get('status') == 'pending':
                # Check if this is from auto-join vs manual verification
                current_question = session.get('current_question', 0)
                total_questions = len(session.get('questions_asked', []))
                
                await interaction.response.send_message(
                    f"✅ Your verification is already in progress! Please check your DMs.\n\n"
                    f"📋 **Progress:** Question {current_question + 1} of {total_questions}\n"
                    f"💬 **Status:** Waiting for your response to the current question\n\n"
                    f"If you didn't receive the DM, please wait a moment and check again.",
                    ephemeral=True
                )
                return
            elif session.get('status') == 'failed':
                # Allow restart for failed sessions
                logger.info(f"🔄 Restarting failed verification for {member}")
                # Clear the failed session
                del self.verification_sessions[user_id]
            elif session.get('status') == 'completed':
                await interaction.response.send_message(
                    "✅ Your verification is already completed! You should have received your role.",
                    ephemeral=True
                )
                return
            else:
                await interaction.response.send_message(
                    "✅ Your verification is currently being processed. Please wait for the results.",
                    ephemeral=True
                )
                return
        
        # Calculate suspicion score
        suspicion_score = await self.calculate_suspicion_score(member)
        
        # Create verification session
        user_data = {
            'discord_id': str(member.id),
            'username': member.name,
            'discriminator': member.discriminator,
            'avatar': str(member.display_avatar.url) if member.display_avatar else None,
            'account_created_at': member.created_at.isoformat(),
            'joined_at': datetime.utcnow().isoformat(),
            'suspicion_score': suspicion_score,
            'current_question': 0,
            'responses': [],
            'questions_asked': [],
            'manual_verification': True  # Flag to indicate this was manually started
        }
        
        # Store session  
        self.verification_sessions[user_id] = user_data
        user_data['status'] = 'starting'  # Set initial status
        
        # Respond to interaction immediately to prevent Discord timeout
        await interaction.response.send_message(
            f"🙏 Starting verification process... Please wait a moment.",
            ephemeral=True
        )
        
        try:
            # Start verification process after responding
            await self.start_verification_process(member, user_data)
            
            # Edit the response to show success
            await interaction.edit_original_response(
                content=f"✅ Verification started! Please check your DMs for questions.\n\n"
                        f"If you don't receive a DM, it may be due to Discord rate limits. "
                        f"You can wait a few minutes and try `/verify` again."
            )
            
            # Log the manual verification start
            logger.info(f"✅ Manual verification started for {member} ({member.id}) via /verify command")
            
        except Exception as e:
            logger.error(f"❌ Failed to start manual verification for {member}: {e}")
            
            # Clean up session on failure
            if user_id in self.verification_sessions:
                del self.verification_sessions[user_id]
            
            # Edit the response to show error
            try:
                await interaction.edit_original_response(
                    content=f"❌ Failed to start verification process. This may be due to Discord rate limits. "
                            f"Please try again in a few minutes, or contact an administrator if the problem persists."
                )
            except Exception as edit_error:
                logger.error(f"❌ Could not edit interaction response: {edit_error}")

    async def verify_for_command_logic(self, interaction: discord.Interaction, target_user: discord.Member):
        """🔧 Admin command to start verification for a specific user"""
        
        # Check if interaction has already been responded to
        if interaction.response.is_done():
            logger.warning(f"⚠️ Interaction already acknowledged for {interaction.user}")
            return
        
        # Check if bot is configured
        if not self.bot_config.get('is_configured'):
            await interaction.response.send_message(
                "❌ Bot is not configured yet. Run `/setup` first.",
                ephemeral=True
            )
            return
        
        # Check admin permissions
        if not await self.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ This command is only available to administrators and moderators.",
                ephemeral=True
            )
            return
        
        # Check if target user is in the server
        if not target_user:
            await interaction.response.send_message(
                "❌ User not found in this server.",
                ephemeral=True
            )
            return
        
        # Check if target user is a bot
        if target_user.bot:
            await interaction.response.send_message(
                "❌ Cannot verify bot accounts.",
                ephemeral=True
            )
            return
        
        user_id = target_user.id
        
        # Clear any existing verification session for this user
        if user_id in self.verification_sessions:
            logger.info(f"🔄 Admin {interaction.user} clearing existing session for {target_user}")
            del self.verification_sessions[user_id]
        
        # Remove any existing verification roles before restarting
        devotee_role_id = self.bot_config.get('devotee_role_id')
        seeker_role_id = self.bot_config.get('seeker_role_id')
        no_role_id = self.bot_config.get('no_role_id')
        
        roles_to_remove = []
        if devotee_role_id:
            role = interaction.guild.get_role(int(devotee_role_id))
            if role and role in target_user.roles:
                roles_to_remove.append(role)
        
        if seeker_role_id:
            role = interaction.guild.get_role(int(seeker_role_id))
            if role and role in target_user.roles:
                roles_to_remove.append(role)
        
        if no_role_id:
            role = interaction.guild.get_role(int(no_role_id))
            if role and role in target_user.roles:
                roles_to_remove.append(role)
        
        # Remove existing verification roles
        if roles_to_remove:
            try:
                await target_user.remove_roles(*roles_to_remove, reason=f"Admin re-verification by {interaction.user}")
                logger.info(f"🗑️ Removed {len(roles_to_remove)} verification roles from {target_user}")
            except Exception as e:
                logger.error(f"❌ Failed to remove roles from {target_user}: {e}")
        
        # Calculate suspicion score for target user
        suspicion_score = await self.calculate_suspicion_score(target_user)
        
        # Create new verification session
        user_data = {
            'discord_id': str(target_user.id),
            'username': target_user.name,
            'discriminator': target_user.discriminator,
            'avatar': str(target_user.display_avatar.url) if target_user.display_avatar else None,
            'account_created_at': target_user.created_at.isoformat(),
            'joined_at': datetime.utcnow().isoformat(),
            'suspicion_score': suspicion_score,
            'current_question': 0,
            'responses': [],
            'questions_asked': [],
            'admin_verification': True,  # Flag to indicate this was started by admin
            'admin_user': str(interaction.user.id)  # Track which admin started it
        }
        
        # Store session
        self.verification_sessions[user_id] = user_data
        user_data['status'] = 'starting'
        
        # Respond to interaction immediately
        await interaction.response.send_message(
            f"🔧 Starting verification process for {target_user.mention}...",
            ephemeral=True
        )
        
        try:
            # Start verification process
            await self.start_verification_process(target_user, user_data)
            
            # Edit response to show success
            await interaction.edit_original_response(
                content=f"✅ **Verification started for {target_user.mention}**\n\n"
                        f"📋 **Details:**\n"
                        f"• User: {target_user.name}#{target_user.discriminator}\n"
                        f"• Suspicion Score: {suspicion_score}/4\n"
                        f"• DM Status: Questions sent to user's DMs\n"
                        f"• Admin: {interaction.user.mention}\n\n"
                        f"The user will receive verification questions in their DMs and the process will proceed normally."
            )
            
            # Log admin verification start
            logger.info(f"✅ Admin verification started by {interaction.user} for {target_user} ({target_user.id})")
            
            # Send notification to admin channel
            admin_channel_id = self.bot_config.get('admin_channel_id')
            if admin_channel_id:
                admin_channel = self.get_channel(int(admin_channel_id))
                if admin_channel:
                    embed = discord.Embed(
                        title="🔧 Admin Re-verification Started",
                        description=f"**Admin:** {interaction.user.mention}\n"
                                   f"**Target User:** {target_user.mention}\n"
                                   f"**Reason:** Manual verification restart",
                        color=0xFF6B35
                    )
                    embed.add_field(
                        name="📋 Process Info",
                        value=f"• Previous roles removed\n• New verification session created\n• Questions sent to user DMs",
                        inline=False
                    )
                    embed.set_footer(text="🔧 Admin verification system")
                    await admin_channel.send(embed=embed)
            
        except Exception as e:
            logger.error(f"❌ Failed to start admin verification for {target_user}: {e}")
            
            # Clean up session on failure
            if user_id in self.verification_sessions:
                del self.verification_sessions[user_id]
            
            # Edit response to show error
            try:
                await interaction.edit_original_response(
                    content=f"❌ **Failed to start verification for {target_user.mention}**\n\n"
                            f"Error: {str(e)}\n\n"
                            f"This may be due to Discord rate limits or the user having DMs disabled. "
                            f"Please try again in a few moments."
                )
            except Exception as edit_error:
                logger.error(f"❌ Could not edit interaction response: {edit_error}")

    async def is_admin(self, user: discord.Member) -> bool:
        """Check if user has admin permissions for verification commands"""
        # Check if user has administrator permissions
        if user.guild_permissions.administrator:
            return True
        
        # Check if user has manage_guild permissions
        if user.guild_permissions.manage_guild:
            return True
        
        # Check for configured admin roles
        admin_role_1_id = self.bot_config.get('admin_role_1_id')
        admin_role_2_id = self.bot_config.get('admin_role_2_id')
        
        user_role_ids = [role.id for role in user.roles]
        
        if admin_role_1_id and int(admin_role_1_id) in user_role_ids:
            return True
        
        if admin_role_2_id and int(admin_role_2_id) in user_role_ids:
            return True
        
        return False

async def start_bot_with_retry(bot, bot_token, max_retries=8):
    """Start Discord bot with aggressive exponential backoff retry logic for Cloudflare rate limiting"""
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"🔄 Bot connection attempt {attempt}/{max_retries}")
            
            # Aggressive exponential backoff delay for Cloudflare rate limiting
            if attempt > 1:
                # More aggressive backoff: 20s, 60s, 120s, 300s, 600s, 900s, 1200s
                delay = min(1200, (2 ** (attempt - 1)) * 20)
                logger.info(f"⏳ Waiting {delay}s before connection attempt (Cloudflare protection)")
                await asyncio.sleep(delay)
            
            # Add small random delay to avoid synchronized retries
            import random
            jitter = random.uniform(0, 5)
            await asyncio.sleep(jitter)
            
            # Try to start the bot
            logger.info(f"📡 Attempting Discord gateway connection...")
            await bot.start(bot_token)
            logger.info("✅ Bot connected successfully!")
            return
            
        except discord.HTTPException as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['429', 'rate limit', 'cloudflare', 'too many requests', 'error 1015']):
                logger.warning(f"🚫 Rate limited by Cloudflare (attempt {attempt}/{max_retries}): {e}")
                if attempt == max_retries:
                    logger.error("❌ Max retry attempts reached. Render will restart the service automatically.")
                    logger.error("💡 This is likely a temporary Cloudflare rate limit. The bot will retry when restarted.")
                    raise
                continue
            else:
                logger.error(f"❌ Discord API error (non-rate-limit): {e}")
                raise
        except discord.ConnectionClosed as e:
            logger.warning(f"🔌 Discord connection closed (attempt {attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                raise
            await asyncio.sleep(60)  # Wait longer for connection issues
        except discord.GatewayNotFound as e:
            logger.error(f"❌ Discord gateway not found: {e}")
            raise  # Don't retry gateway errors
        except discord.LoginFailure as e:
            logger.error(f"❌ Discord login failure (check bot token): {e}")
            raise  # Don't retry login failures
        except Exception as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['cloudflare', 'rate limit', '429', 'too many requests']):
                logger.warning(f"🚫 Cloudflare-related error (attempt {attempt}/{max_retries}): {e}")
                if attempt == max_retries:
                    logger.error("❌ Max retry attempts reached due to Cloudflare blocking.")
                    raise
                continue
            else:
                logger.error(f"❌ Unexpected error on attempt {attempt}: {e}")
                if attempt == max_retries:
                    raise
                await asyncio.sleep(45)  # Wait before retrying unexpected errors

async def find_available_port(start_port=5000, end_port=5010):
    """Find an available port in the specified range"""
    import socket
    for port in range(start_port, end_port + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(('0.0.0.0', port))
                return port
        except OSError:
            continue
    return None

async def main():
    """Main function to run the bot with web server for Render deployment"""
    bot_token = os.getenv('DISCORD_BOT_TOKEN')
    requested_port = int(os.getenv('PORT', 5000))  # Render provides PORT environment variable
    
    if not bot_token:
        logger.error("❌ DISCORD_BOT_TOKEN not found in environment variables")
        return
    
    # Import web server here to avoid circular imports
    from web_server import KeepAliveServer
    
    bot = KrishnaVerificationBot()
    web_server = KeepAliveServer(bot)
    
    try:
        logger.info("🌸 Starting Krishna verification bot with enhanced Cloudflare protection...")
        
        # Add startup delay to avoid immediate rate limiting
        startup_delay = int(os.getenv('STARTUP_DELAY', '10'))
        if startup_delay > 0:
            logger.info(f"⏳ Startup delay: {startup_delay}s (helps avoid Cloudflare rate limits)")
            await asyncio.sleep(startup_delay)
        
        # Find available port if the requested port is in use
        port = await find_available_port(requested_port, requested_port + 10)
        if not port:
            logger.error(f"❌ No available ports found between {requested_port} and {requested_port + 10}")
            return
        
        if port != requested_port:
            logger.info(f"⚠️ Port {requested_port} in use, using port {port} instead")
        
        # Start web server for Render keep-alive
        await web_server.start_server(port)
        logger.info(f"🌐 Web server running on port {port}")
        
        # Start Discord bot with enhanced retry logic for Cloudflare protection
        logger.info("📡 Starting Discord bot with aggressive retry protection for Cloudflare...")
        await start_bot_with_retry(bot, bot_token, max_retries=8)
        
    except KeyboardInterrupt:
        logger.info("🙏 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        # For Render, we want to restart on errors to retry connection
        raise
    finally:
        if not bot.is_closed():
            await bot.close()
            logger.info("🔌 Bot connection closed")

if __name__ == "__main__":
    asyncio.run(main())
