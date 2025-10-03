"""
Persistent Configuration Storage for Krishna Verification Bot
Handles saving and loading bot configuration using PostgreSQL to survive Render restarts
"""

import os
import json
import logging
from typing import Dict, Any, Optional
from sqlalchemy import create_engine, Column, String, Text, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

logger = logging.getLogger(__name__)

Base = declarative_base()

class BotConfiguration(Base):
    __tablename__ = 'bot_configurations'
    
    server_id = Column(String, primary_key=True)
    config_data = Column(Text)  # JSON string of configuration
    is_configured = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    configured_by = Column(String)

class ConfigStorage:
    def __init__(self, database_url: str = None):
        self.database_url = database_url or os.getenv('DATABASE_URL')
        self.current_config = {}
        self.engine = None
        self.SessionLocal = None
        self._initialize_database()
        
    def _initialize_database(self):
        """Initialize database connection and create tables"""
        try:
            if not self.database_url:
                logger.warning("No DATABASE_URL found, falling back to file storage")
                return
                
            self.engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                pool_recycle=300
            )
            self.SessionLocal = sessionmaker(bind=self.engine)
            
            # Create tables if they don't exist
            Base.metadata.create_all(bind=self.engine)
            logger.info("🗄️ Database connection established for bot configuration")
            
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            logger.warning("Falling back to file-based storage")
            self.engine = None
    
    def load_config(self, server_id: str = None) -> Dict:
        """Load configuration from database"""
        if not self.engine:
            return self._load_from_file()
            
        try:
            session = self.SessionLocal()
            
            # Always use SERVER_ID from environment for Neon DB
            server_id = os.getenv('SERVER_ID', '0')
            
            config_record = session.query(BotConfiguration).filter_by(server_id=server_id).first()
            
            if config_record and config_record.config_data:
                config = json.loads(config_record.config_data)
                logger.info(f"📂 Configuration loaded from database for server {server_id}")
                session.close()
                return config
            else:
                logger.info(f"📝 No configuration found in database for server {server_id}")
                session.close()
                return {}
                
        except Exception as e:
            logger.error(f"Error loading config from database: {e}")
            return self._load_from_file()
    
    def save_config(self, config: Dict, server_id: str = None) -> bool:
        """Save configuration to database"""
        if not self.engine:
            return self._save_to_file(config)
            
        try:
            session = self.SessionLocal()
            
            # Always use SERVER_ID from environment for Neon DB
            server_id = os.getenv('SERVER_ID', '0')
            
            # Check if configuration exists
            config_record = session.query(BotConfiguration).filter_by(server_id=server_id).first()
            
            if config_record:
                # Update existing configuration
                config_record.config_data = json.dumps(config, indent=2)
                config_record.is_configured = config.get('is_configured', False)
                config_record.updated_at = datetime.utcnow()
                config_record.configured_by = config.get('configured_by', 'unknown')
            else:
                # Create new configuration
                config_record = BotConfiguration(
                    server_id=server_id,
                    config_data=json.dumps(config, indent=2),
                    is_configured=config.get('is_configured', False),
                    configured_by=config.get('configured_by', 'unknown')
                )
                session.add(config_record)
            
            session.commit()
            session.close()
            
            self.current_config = config.copy()
            logger.info(f"💾 Configuration saved to database for server {server_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving config to database: {e}")
            return self._save_to_file(config)
    
    def get_config(self, server_id: str = None) -> Dict:
        """Get current configuration"""
        if not self.current_config:
            self.current_config = self.load_config()
        return self.current_config
    
    def is_configured(self, server_id: str = None) -> bool:
        """Check if bot is properly configured"""
        config = self.get_config()
        return config.get('is_configured', False)
    
    def update_config(self, updates: Dict, server_id: str = None) -> bool:
        """Update specific configuration values"""
        current = self.get_config()
        current.update(updates)
        return self.save_config(current)
    
    def reset_config(self, server_id: str = None) -> bool:
        """Reset configuration to empty state"""
        return self.save_config({})
    
    # Fallback file-based methods
    def _load_from_file(self) -> Dict:
        """Fallback: Load configuration from JSON file"""
        try:
            config_file = "bot_config.json"
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
                    logger.info(f"📂 Configuration loaded from {config_file} (fallback)")
                    return config
            else:
                logger.info("📝 No configuration file found, starting with empty config")
                return {}
        except Exception as e:
            logger.error(f"Error loading config from file: {e}")
            return {}
    
    def _save_to_file(self, config: Dict) -> bool:
        """Fallback: Save configuration to JSON file"""
        try:
            config_file = "bot_config.json"
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            self.current_config = config.copy()
            logger.info(f"💾 Configuration saved to {config_file} (fallback)")
            return True
        except Exception as e:
            logger.error(f"Error saving config to file: {e}")
            return False