# Development Container README

## Included Features

### Python Environment
- Python 3.11
- pip package manager
- Based on Debian Bullseye

### Playwright Support
- Playwright auto-installed
- Includes Chromium, Firefox, and WebKit browsers
- Required system dependency libraries

### VS Code Extensions
- Python official extension
- Pylint code linting
- Black code formatting
- Jupyter Notebook support

### Development Tools
- Zsh shell configuration
- Oh My Zsh framework
- Common development utilities

## Usage

1. Make sure Docker and the VS Code Dev Containers extension are installed
2. Open the project in VS Code
3. Press `Cmd+Shift+P` (macOS) or `Ctrl+Shift+P` (Windows/Linux)
4. Select "Dev Containers: Reopen in Container"
5. Wait for the container to build and configure

## Environment Variables

Remember to set the required environment variables in your .env file:

```env
PRO104_ACCOUNT=your_account
PRO104_PASSWORD=your_password
GMAIL_ADDRESS=your_gmail@gmail.com
GMAIL_APP_PASSWORD=your_app_password
RANDOM_DELAY_MIN=0
RANDOM_DELAY_MAX=300
```

## Testing

After the container starts, you can test if Playwright is working properly:

```bash
python3 clock_in.py --test-gmail
python3 clock_in.py --action clock_in --skip-weekday-check
```
