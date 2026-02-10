#!/bin/bash

echo "🚀 Setting up Python & Playwright development environment..."

# Update package lists
sudo apt-get update

# Install basic tools
sudo apt-get install -y \
    curl \
    git \
    wget \
    unzip \
    vim \
    zsh

# Install Oh My Zsh for pwuser (default user in Playwright image)
if [ ! -d "/home/pwuser/.oh-my-zsh" ]; then
    echo "🐚 Installing Oh My Zsh for pwuser..."
    sudo -u pwuser sh -c "$(curl -fsSL https://raw.github.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" || true

    # Set zsh as default shell
    sudo chsh -s $(which zsh) pwuser || true
fi

# Install project dependencies
echo "📦 Installing Python dependencies..."
if [ -f requirements.txt ]; then
    pip install --no-cache-dir -r requirements.txt
else
    echo "⚠️  requirements.txt not found, installing basic dependencies..."
    pip install python-dotenv
fi

# Playwright browsers should already be in the image, but ensure latest version
echo "🌐 Ensuring Playwright browsers are installed..."
playwright install chromium --with-deps || true

# Set permissions
sudo chmod +x setup_cron.sh || true

echo "✅ Development environment setup complete!"
echo "📝 You can now run your clock_in.py script"
echo "🎭 Playwright is ready with Chromium browser"
