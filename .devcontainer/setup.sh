#!/bin/bash

echo "🚀 Setting up Python & Playwright development environment..."

# 更新 package lists
sudo apt-get update

# 安装基础工具
sudo apt-get install -y \
    curl \
    git \
    wget \
    unzip \
    vim \
    zsh

# 为 pwuser 用户安装 Oh My Zsh (Playwright 镜像的默认用户)
if [ ! -d "/home/pwuser/.oh-my-zsh" ]; then
    echo "🐚 Installing Oh My Zsh for pwuser..."
    sudo -u pwuser sh -c "$(curl -fsSL https://raw.github.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" || true
    
    # 设置 zsh 为默认 shell
    sudo chsh -s $(which zsh) pwuser || true
fi

# 安装项目依赖
echo "📦 Installing Python dependencies..."
if [ -f requirements.txt ]; then
    pip install --no-cache-dir -r requirements.txt
else
    echo "⚠️  requirements.txt not found, installing basic dependencies..."
    pip install python-dotenv
fi

# Playwright 浏览器应该已经在镜像中，但确保最新版本
echo "🌐 Ensuring Playwright browsers are installed..."
playwright install chromium --with-deps || true

# 设置权限
sudo chmod +x setup_cron.sh || true

echo "✅ Development environment setup complete!"
echo "📝 You can now run your clock_in.py script"
echo "🎭 Playwright is ready with Chromium browser"