#!/usr/bin/env python3
"""
Simple web server for Render deployment to keep the service alive
Runs alongside the Krishna bot
"""

import asyncio
import logging
from aiohttp import web, web_runner
import os
from datetime import datetime

logger = logging.getLogger(__name__)

class KeepAliveServer:
    def __init__(self, bot_instance=None):
        self.bot = bot_instance
        self.app = web.Application()
        self.setup_routes()
        
    def setup_routes(self):
        """Setup web routes for health checks and status"""
        self.app.router.add_get('/', self.home)
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/status', self.bot_status)
        self.app.router.add_get('/ping', self.ping)
        
    async def home(self, request):
        """Home page showing bot status"""
        bot_status = "Connected" if self.bot and not self.bot.is_closed() else "Disconnected"
        server_id = os.getenv('SERVER_ID', 'Not configured')
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Krishna Verification Bot</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
                .container {{ max-width: 600px; margin: 0 auto; background: rgba(255,255,255,0.1); padding: 30px; border-radius: 15px; }}
                .status {{ padding: 10px; border-radius: 5px; margin: 10px 0; }}
                .connected {{ background: rgba(76, 175, 80, 0.3); }}
                .disconnected {{ background: rgba(244, 67, 54, 0.3); }}
                h1 {{ color: #FFD700; text-align: center; }}
                .info {{ background: rgba(255,255,255,0.1); padding: 15px; border-radius: 10px; margin: 15px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🌸 Krishna-Conscious Verification Bot</h1>
                <div class="info">
                    <h3>Bot Status</h3>
                    <div class="status {'connected' if bot_status == 'Connected' else 'disconnected'}">
                        Status: {bot_status}
                    </div>
                    <p><strong>Server ID:</strong> {server_id}</p>
                    <p><strong>Deployment:</strong> Render Free Tier</p>
                    <p><strong>Last Check:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>
                <div class="info">
                    <h3>About</h3>
                    <p>This bot serves as a compassionate gatekeeper for Krishna-conscious Discord communities, 
                    evaluating new members based on sincerity, devotional attitude, and spiritual humility 
                    according to Srila Prabhupada's teachings.</p>
                </div>
                <div class="info">
                    <h3>Features</h3>
                    <ul>
                        <li>AI-powered spiritual discernment</li>
                        <li>Dynamic question system</li>
                        <li>Comprehensive setup command</li>
                        <li>Role-based access control</li>
                    </ul>
                </div>
            </div>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')
        
    async def health_check(self, request):
        """Health check endpoint for Render"""
        health_data = {
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat(),
            'bot_connected': self.bot and not self.bot.is_closed() if self.bot else False,
            'server_id': os.getenv('SERVER_ID', 'Not configured')
        }
        return web.json_response(health_data)
        
    async def bot_status(self, request):
        """Detailed bot status endpoint"""
        if not self.bot:
            return web.json_response({
                'status': 'error',
                'message': 'Bot instance not available'
            }, status=500)
            
        status_data = {
            'connected': not self.bot.is_closed(),
            'guilds': len(self.bot.guilds) if hasattr(self.bot, 'guilds') else 0,
            'latency': round(self.bot.latency * 1000, 2) if hasattr(self.bot, 'latency') else None,
            'user': str(self.bot.user) if self.bot.user else None,
            'target_server': os.getenv('SERVER_ID'),
            'configured': getattr(self.bot, 'bot_config', {}).get('is_configured', False),
            'questions_loaded': len(getattr(self.bot, 'questions', {})) > 0,
            'timestamp': datetime.utcnow().isoformat()
        }
        return web.json_response(status_data)
        
    async def ping(self, request):
        """Simple ping endpoint"""
        return web.json_response({'pong': datetime.utcnow().isoformat()})
        
    async def start_server(self, port=5000):
        """Start the web server"""
        runner = web_runner.AppRunner(self.app)
        await runner.setup()
        
        site = web_runner.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        logger.info(f"🌐 Web server started on port {port}")
        logger.info("📡 Render keep-alive server is running")
        
    async def run_forever(self, port=5000):
        """Run the server indefinitely"""
        await self.start_server(port)
        
        try:
            # Keep the server running forever
            while True:
                await asyncio.sleep(60)  # Check every minute
                
        except Exception as e:
            logger.error(f"Web server error: {e}")
            raise

def create_app(bot_instance=None):
    """Create and return the web application"""
    server = KeepAliveServer(bot_instance)
    return server.app