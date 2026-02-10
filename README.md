# 104 Pro Auto Clock-In Script

Automates the clock-in/out process on [pro.104.com.tw](https://pro.104.com.tw) using Python + Playwright, with support for automatic Gmail 2FA verification code retrieval.

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Account & Gmail

```bash
cp .env.example .env
# Edit .env and fill in your 104 account credentials and Gmail app password
```

### 3. Get a Gmail App Password

The script needs to read 2FA verification codes from Gmail via IMAP, so you need an "App Password":

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Make sure "2-Step Verification" is enabled
3. Search for "App Passwords" in the search bar
4. Create a new app password (you can name it "104-clockin")
5. Paste the generated 16-character password into `GMAIL_APP_PASSWORD` in your `.env` file

### 4. Test Gmail Connection

Verify that the script can read your Gmail:

```bash
export $(grep -v '^#' .env | xargs)
python clock_in.py --test-gmail
```

### 5. First Clock-In Test (may need selector adjustments)

```bash
python clock_in.py --action clock_in --no-delay --skip-weekday-check
```

After running, check the screenshots in the `screenshots/` folder to confirm each step worked correctly.

### 6. Adjust Selectors

Open `clock_in.py` and look for the selector sections that may need adjustment.

**How to find the correct selectors:**

1. Open https://pro.104.com.tw in Chrome
2. Press F12 to open DevTools
3. Use the element picker (Ctrl+Shift+C) to click on input fields and buttons
4. Note the element's id, name, class, and other attributes
5. Update the selectors in the script

There are three areas that may need adjustment: the login form, the 2FA verification code input, and the punch button.

---

## Auto Clock-In Flow

```
1. Launch headless browser
2. Navigate to 104 Pro login page
3. Enter account credentials → Click login
4. Detect 2FA verification page
5. Connect to Gmail IMAP → Search for the latest email from 104
6. Extract verification code from email (polls for up to 60 seconds)
7. Enter verification code → Complete login
8. Navigate to punch page → Click clock-in/clock-out
9. Take screenshot to record result
```

---

## Cloud Deployment Options

### Option A: VPS + Cron Job (Recommended)

```bash
# On your server
git clone <your-repo> 104-auto-clockin
cd 104-auto-clockin

pip install -r requirements.txt
playwright install chromium --with-deps

cp .env.example .env
vim .env

chmod +x setup_cron.sh
./setup_cron.sh
```

### Option B: Docker + Cron

```bash
docker build -t 104-clockin .

# Test
docker run --rm --env-file .env \
  -v $(pwd)/screenshots:/app/screenshots \
  104-clockin --action clock_in --no-delay

# Set up cron (crontab -e)
# 50 8 * * 1-5 docker run --rm --env-file /path/to/.env 104-clockin --action clock_in
# 5 18 * * 1-5 docker run --rm --env-file /path/to/.env 104-clockin --action clock_out
```

### Option C: GitHub Actions (Free)

Create `.github/workflows/clockin.yml` in your GitHub repo:

```yaml
name: Auto Clock In/Out

on:
  schedule:
    # UTC time, Taipei time is UTC+8
    - cron: '50 0 * * 1-5'   # Clock in at 08:50
    - cron: '5 10 * * 1-5'   # Clock out at 18:05
  workflow_dispatch:
    inputs:
      action:
        description: 'clock_in or clock_out'
        required: true
        default: 'clock_in'

jobs:
  punch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install chromium --with-deps

      - name: Determine action
        id: action
        run: |
          if [ "${{ github.event_name }}" = "schedule" ]; then
            HOUR=$(date -u +%H)
            if [ "$HOUR" -lt 5 ]; then
              echo "action=clock_in" >> $GITHUB_OUTPUT
            else
              echo "action=clock_out" >> $GITHUB_OUTPUT
            fi
          else
            echo "action=${{ github.event.inputs.action }}" >> $GITHUB_OUTPUT
          fi

      - name: Run clock in/out
        env:
          PRO104_ACCOUNT: ${{ secrets.PRO104_ACCOUNT }}
          PRO104_PASSWORD: ${{ secrets.PRO104_PASSWORD }}
          GMAIL_ADDRESS: ${{ secrets.GMAIL_ADDRESS }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
        run: python clock_in.py --action ${{ steps.action.outputs.action }}

      - name: Upload screenshots
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: screenshots-${{ github.run_id }}
          path: screenshots/
          retention-days: 7
```

Set the following in your GitHub repo under Settings > Secrets:
- `PRO104_ACCOUNT`
- `PRO104_PASSWORD`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`

---

## Command Reference

```bash
# Clock in
python clock_in.py --action clock_in

# Clock out
python clock_in.py --action clock_out

# Test mode (no delay + skip weekday check)
python clock_in.py --action clock_in --no-delay --skip-weekday-check

# Test Gmail connection only
python clock_in.py --test-gmail
```

---

## Project Structure

```
104-auto-clockin/
├── clock_in.py          # Main clock-in script (with 2FA Gmail reader)
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── .env                 # Your actual config (do not commit to git)
├── setup_cron.sh        # Cron job setup script
├── Dockerfile           # Docker build file
├── docker-compose.yml   # Docker Compose config
├── README.md            # Documentation
├── logs/                # Execution logs (auto-generated)
└── screenshots/         # Screenshots (auto-generated)
```

---

## Troubleshooting

**Q: Gmail connection failed?**
Make sure `GMAIL_APP_PASSWORD` is an "App Password", not your regular Gmail login password. Also verify that IMAP is enabled in Gmail (Gmail Settings > Forwarding and POP/IMAP > Enable IMAP).

**Q: Can't find the verification code?**
The script searches for emails from `104.com.tw`. If 104 uses a different sender address, update `SENDER_FILTERS` in `clock_in.py`. You can also forward a 104 verification email to check the actual sender address.

**Q: Verification code email is delayed?**
By default, the script waits up to 60 seconds, checking every 5 seconds. You can adjust `VERIFICATION_CODE_WAIT` and `VERIFICATION_CODE_POLL`.

**Q: Can't find the account input field or punch button?**
Check the screenshots in `screenshots/` to see the page state, then use F12 DevTools to find the correct selectors.

**Q: Cloud server IP not whitelisted?**
If your company restricts clock-in by IP, you may need a VPN.
