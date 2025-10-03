#!/usr/bin/env python3
"""
Installation script for Krishna-Conscious Discord Verification Bot
"""

import os
import sys
import subprocess
import shutil

def check_python_version():
    """Check if Python version is 3.8+"""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8 or higher is required")
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} detected")

def install_dependencies():
    """Install required Python packages"""
    print("📦 Installing dependencies...")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-r", "bot/requirements.txt"
        ])
        print("✅ Dependencies installed successfully")
    except subprocess.CalledProcessError:
        print("❌ Failed to install dependencies")
        sys.exit(1)

def setup_environment():
    """Set up environment file"""
    env_example = ".env.example"
    env_file = ".env"
    
    if os.path.exists(env_file):
        print("⚠️ .env file already exists")
        return
    
    if os.path.exists(env_example):
        shutil.copy(env_example, env_file)
        print(f"✅ Created {env_file} from template")
        print("📝 Please edit .env file with your Discord bot token and settings")
    else:
        print("❌ .env.example template not found")

def main():
    """Main installation function"""
    print("🌸 Krishna-Conscious Discord Bot Installation")
    print("=" * 50)
    
    check_python_version()
    install_dependencies()
    setup_environment()
    
    print("\n🎉 Installation complete!")
    print("\nNext steps:")
    print("1. Edit .env file with your Discord bot token")
    print("2. Run: python3 krishna_bot.py")
    print("3. In your Discord server, use /setup command to configure")
    print("\n🙏 Hare Krishna!")

if __name__ == "__main__":
    main()