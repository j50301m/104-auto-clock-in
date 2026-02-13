#!/usr/bin/env python3
"""
104 Pro (pro.104.com.tw) Automatic Clock-In Script
Uses Playwright for browser automation + Gmail IMAP for 2FA code retrieval

Usage:
    python clock_in.py --action clock_in   # Clock in (start of work)
    python clock_in.py --action clock_out  # Clock out (end of work)

Environment Variables:
    PRO104_ACCOUNT     - 104 login account
    PRO104_PASSWORD    - 104 login password
    GMAIL_ADDRESS      - Gmail address (for receiving verification codes)
    GMAIL_APP_PASSWORD - Gmail app password (not Gmail login password)

Author: Claude (customized for jason)
"""

import os
import sys
import re
import time
import random
import logging
import argparse
import imaplib
import email
import json
import urllib.request
import urllib.parse
from email.header import decode_header
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional

# Load .env file (automatically find .env in the same directory as the script)
load_dotenv(Path(__file__).parent / ".env")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("Please install Playwright: pip install playwright && playwright install chromium")
    sys.exit(1)


class Config:
    """Configuration manager for the clock-in script"""

    # URLs
    LOGIN_URL = "https://bsignin.104.com.tw/login"
    CLOCK_URL = "https://pro.104.com.tw/psc2/attendance/punch"

    # Login credentials (from .env or environment variables)
    ACCOUNT = os.environ.get("PRO104_ACCOUNT", "")
    PASSWORD = os.environ.get("PRO104_PASSWORD", "")

    # Gmail settings (for 2FA code retrieval)
    GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
    GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
    GMAIL_IMAP_SERVER = "imap.gmail.com"
    GMAIL_IMAP_PORT = 993

    # Telegram Bot settings (for success notifications)
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

    # Verification code settings
    VERIFICATION_CODE_WAIT = 60      # Max seconds to wait for verification code
    VERIFICATION_CODE_POLL = 5       # Check email every N seconds
    VERIFICATION_CODE_LENGTH = 6     # Verification code length (usually 6 digits)

    # 104 sender email filters
    SENDER_FILTERS = [
        "104.com.tw",
        "pro.104.com.tw",
        "noreply@104.com.tw",
        "service@104.com.tw",
    ]

    # Random delay range (seconds) to avoid clocking in at the exact same time every day
    RANDOM_DELAY_MIN = int(os.environ.get("RANDOM_DELAY_MIN", "0"))
    RANDOM_DELAY_MAX = int(os.environ.get("RANDOM_DELAY_MAX", "300"))

    # Retry settings
    MAX_RETRIES = 3
    RETRY_INTERVAL = 30  # seconds

    # Screenshot save path (for debugging)
    SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

    # Log settings
    LOG_DIR = Path(__file__).parent / "logs"

    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration"""
        if not cls.ACCOUNT or not cls.PASSWORD:
            return False
        return True


class Logger:
    """Logging manager"""

    def __init__(self):
        self.logger = self._setup_logging()

    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        Config.LOG_DIR.mkdir(exist_ok=True)
        log_file = Config.LOG_DIR / f"clockin_{datetime.now().strftime('%Y%m%d')}.log"

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
        return logging.getLogger(__name__)

    def info(self, message: str):
        self.logger.info(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str):
        self.logger.error(message)

    def debug(self, message: str):
        self.logger.debug(message)


class TelegramNotifier:
    """Telegram notification sender"""

    def __init__(self, logger: Logger):
        self.logger = logger
        self.bot_token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID

    def send(self, message: str) -> bool:
        """
        Send Telegram notification

        Args:
            message: Message content to send

        Returns:
            True if successful, False otherwise
        """
        if not self.bot_token or not self.chat_id:
            self.logger.debug("Telegram bot not configured, skipping notification")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    self.logger.info("✅ Telegram notification sent")
                    return True
                else:
                    self.logger.warning(f"Telegram notification failed: HTTP {response.status}")
                    return False

        except Exception as e:
            self.logger.warning(f"Error sending Telegram notification: {e}")
            return False


class GmailOTPReader:
    """Gmail OTP (One-Time Password) reader for 2FA"""

    def __init__(self, logger: Logger):
        self.logger = logger
        self.gmail_address = Config.GMAIL_ADDRESS
        self.gmail_password = Config.GMAIL_APP_PASSWORD
        self.imap_server = Config.GMAIL_IMAP_SERVER
        self.imap_port = Config.GMAIL_IMAP_PORT

    @staticmethod
    def decode_mime_header(header_value: str) -> str:
        """Decode MIME-encoded email header"""
        decoded_parts = decode_header(header_value)
        result = ""
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(charset or "utf-8", errors="replace")
            else:
                result += part
        return result

    @staticmethod
    def get_email_body(msg) -> str:
        """Extract plain text content from email message object"""
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                # Skip attachments
                if "attachment" in content_disposition:
                    continue

                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body += payload.decode(charset, errors="replace")
                elif content_type == "text/html" and not body:
                    # Use HTML version if no plain text version
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html_text = payload.decode(charset, errors="replace")
                        # Simple HTML tag removal
                        body += re.sub(r"<[^>]+>", " ", html_text)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")

        return body

    def extract_verification_code(self, text: str) -> Optional[str]:
        """
        Extract verification code from text

        Common verification code formats:
        - Pure digits: 123456
        - In email: "Verification code: 123456" or "verification code: 123456"
        """
        # Strategy 1: Find digits after "verification code" keywords
        patterns = [
            r"驗證碼[：:\s]*(\d{4,8})",
            r"verification\s*code[：:\s]*(\d{4,8})",
            r"認證碼[：:\s]*(\d{4,8})",
            r"確認碼[：:\s]*(\d{4,8})",
            r"OTP[：:\s]*(\d{4,8})",
            r"代碼[：:\s]*(\d{4,8})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        # Strategy 2: Find standalone N-digit numbers (possibly verification code)
        # Find 4-8 digit numbers surrounded by whitespace or punctuation
        standalone_numbers = re.findall(r"(?<!\d)(\d{4,8})(?!\d)", text)
        if standalone_numbers:
            # Prefer the specified length
            for num in standalone_numbers:
                if len(num) == Config.VERIFICATION_CODE_LENGTH:
                    return num
            # Otherwise return the first one found
            return standalone_numbers[0]

        return None

    def fetch_verification_code(self, after_timestamp: datetime) -> Optional[str]:
        """
        Read 104 Pro 2FA verification code from Gmail

        Args:
            after_timestamp: Only search for emails after this time

        Returns:
            Verification code string, or None if not found
        """
        self.logger.info("Connecting to Gmail IMAP...")

        try:
            # Connect to Gmail IMAP
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.gmail_address, self.gmail_password)
            mail.select("INBOX")

            self.logger.info("Connected to Gmail, searching for verification code email...")

            # Search criteria: emails from today
            date_str = after_timestamp.strftime("%d-%b-%Y")
            search_criteria = f'(SINCE "{date_str}")'

            status, message_ids = mail.search(None, search_criteria)

            if status != "OK" or not message_ids[0]:
                self.logger.info("No emails found matching criteria")
                mail.logout()
                return None

            # Start from the most recent emails
            ids = message_ids[0].split()
            ids.reverse()  # Most recent first

            for msg_id in ids[:20]:  # Check only the last 20 emails
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Check sender
                sender = self.decode_mime_header(msg.get("From", ""))
                is_from_104 = any(f in sender.lower() for f in Config.SENDER_FILTERS)

                if not is_from_104:
                    continue

                # Check email timestamp
                date_str_raw = msg.get("Date", "")
                try:
                    msg_date = email.utils.parsedate_to_datetime(date_str_raw)
                    # Ensure it was received after the trigger time
                    if msg_date.replace(tzinfo=None) < after_timestamp.replace(tzinfo=None):
                        continue
                except (ValueError, TypeError):
                    pass  # Unable to parse date, continue trying

                # Get email content
                subject = self.decode_mime_header(msg.get("Subject", ""))
                body = self.get_email_body(msg)

                self.logger.info(f"Found email from 104: {subject}")

                # First try to find code in subject
                code = self.extract_verification_code(subject)
                if code:
                    self.logger.info(f"Found verification code in subject: {code}")
                    mail.logout()
                    return code

                # Then try to find code in body
                code = self.extract_verification_code(body)
                if code:
                    self.logger.info(f"Found verification code in body: {code}")
                    mail.logout()
                    return code

                self.logger.debug("No verification code found in this email, continuing search...")

            mail.logout()
            return None

        except imaplib.IMAP4.error as e:
            self.logger.error(f"Gmail IMAP error: {e}")
            self.logger.error("Please check if GMAIL_APP_PASSWORD is correctly set")
            return None
        except Exception as e:
            self.logger.error(f"Error reading Gmail: {e}")
            return None

    def wait_and_fetch(self, after_timestamp: datetime) -> Optional[str]:
        """
        Wait and fetch verification code (continuously polls Gmail)

        Args:
            after_timestamp: Only search for emails after this time

        Returns:
            Verification code string, or None if timeout
        """
        self.logger.info(
            f"Waiting for verification code email... (max wait {Config.VERIFICATION_CODE_WAIT}s, "
            f"checking every {Config.VERIFICATION_CODE_POLL}s)"
        )

        elapsed = 0
        while elapsed < Config.VERIFICATION_CODE_WAIT:
            code = self.fetch_verification_code(after_timestamp)
            if code:
                return code

            self.logger.info(
                f"Verification code not yet received, retrying in {Config.VERIFICATION_CODE_POLL}s... "
                f"({elapsed}/{Config.VERIFICATION_CODE_WAIT}s)"
            )
            time.sleep(Config.VERIFICATION_CODE_POLL)
            elapsed += Config.VERIFICATION_CODE_POLL

        self.logger.error(f"No verification code received after waiting {Config.VERIFICATION_CODE_WAIT}s")
        return None


class Pro104ClockIn:
    """104 Pro automatic clock-in bot"""

    def __init__(self, logger: Logger, debug: bool = False):
        self.logger = logger
        self.debug = debug
        self.otp_reader = GmailOTPReader(logger)
        self.notifier = TelegramNotifier(logger)
        self.page = None

    def take_screenshot(self, name: str):
        """Take screenshot for debugging (only in debug mode)"""
        if not self.debug:
            return
        Config.SCREENSHOT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = Config.SCREENSHOT_DIR / f"{name}_{timestamp}.png"
        self.page.screenshot(path=str(filepath), full_page=True)
        self.logger.info(f"Screenshot saved: {filepath}")

    @staticmethod
    def is_weekday() -> bool:
        """Check if today is a weekday (Monday to Friday)"""
        return datetime.now().weekday() < 5

    def random_delay(self):
        """Add random delay to simulate human behavior"""
        delay = random.randint(Config.RANDOM_DELAY_MIN, Config.RANDOM_DELAY_MAX)
        if delay > 0:
            self.logger.info(f"Random delay: {delay}s...")
            time.sleep(delay)

    def find_element(self, selectors: list, name: str, required: bool = True):
        """
        Try multiple selectors to find page element

        Args:
            selectors: List of selectors to try
            name: Element name (for logging)
            required: Whether this is a required element (error if not found)

        Returns:
            Found element, or None
        """
        for selector in selectors:
            try:
                element = self.page.wait_for_selector(selector, timeout=3000)
                if element and element.is_visible():
                    self.logger.info(f"Found {name}: {selector}")
                    return element
            except PlaywrightTimeout:
                continue

        if required:
            self.logger.error(f"Cannot find {name}! Please check selector settings.")
            self.take_screenshot(f"error_no_{name}")
        return None

    def login(self) -> bool:
        """
        Login to 104 Pro (including 2FA verification code handling)

        Flow:
        1. Enter account and password → Click login
        2. Verification code input appears on page
        3. Read verification code from Gmail
        4. Enter verification code → Complete login
        """
        self.logger.info(f"Navigating to login page: {Config.LOGIN_URL}")
        self.page.goto(Config.LOGIN_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        self.take_screenshot("01_login_page")

        # Step 1: Enter account and password
        account_selectors = [
            'input[name="account"]',
            'input[name="username"]',
            'input[name="email"]',
            'input[type="email"]',
            'input[placeholder*="帳號"]',
            'input[placeholder*="Email"]',
            '#account',
            '#username',
        ]

        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            '#password',
        ]

        login_button_selectors = [
            'button[type="submit"]',
            'button:has-text("登入")',
            'input[type="submit"]',
            'a:has-text("登入")',
            '.login-btn',
            '#loginBtn',
        ]

        # Find account input
        account_input = self.find_element(account_selectors, "account input")
        if not account_input:
            return False

        # Find password input
        password_input = self.find_element(password_selectors, "password input")
        if not password_input:
            return False

        # Enter account and password (simulate human typing speed)
        account_input.click()
        account_input.fill("")
        account_input.type(Config.ACCOUNT, delay=random.randint(50, 150))
        time.sleep(0.5)

        password_input.click()
        password_input.fill("")
        password_input.type(Config.PASSWORD, delay=random.randint(50, 150))
        time.sleep(0.5)

        self.take_screenshot("02_credentials_filled")

        # Record submission time (for filtering verification code emails)
        submit_timestamp = datetime.now() - timedelta(seconds=30)

        # Click login
        login_button = self.find_element(login_button_selectors, "login button")
        if not login_button:
            return False

        login_button.click()
        self.logger.info("Login button clicked, waiting for page response...")

        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass
        time.sleep(3)

        self.take_screenshot("03_after_first_login")

        # Step 2: Handle 2FA verification code
        verification_input_selectors = [
            'input[name="otp"]',
            'input[name="verificationCode"]',
            'input[name="verification_code"]',
            'input[name="code"]',
            'input[placeholder*="驗證碼"]',
            'input[placeholder*="認證碼"]',
            'input[placeholder*="verification"]',
            'input[type="tel"]',
            'input[maxlength="6"]',
            '.otp-input input',
            '#verificationCode',
            '#otp',
        ]

        # Check if verification code input appears
        verification_input = self.find_element(
            verification_input_selectors, "verification code input", required=False
        )

        if verification_input:
            self.logger.info("Detected 2FA verification page, reading code from Gmail...")

            # Check Gmail settings
            if not Config.GMAIL_ADDRESS or not Config.GMAIL_APP_PASSWORD:
                self.logger.error("Gmail settings required to read verification code!")
                self.logger.error("Please set GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment variables")
                return False

            # Read verification code from Gmail
            code = self.otp_reader.wait_and_fetch(submit_timestamp)

            if not code:
                self.logger.error("Unable to retrieve verification code!")
                self.take_screenshot("error_no_verification_code")
                return False

            self.logger.info(f"Retrieved verification code: {code}")

            # Enter verification code
            verification_input.click()
            verification_input.fill("")
            verification_input.type(code, delay=random.randint(80, 200))
            time.sleep(2)

            self.take_screenshot("04_verification_code_filled")

            # 104's OTP usually auto-submits after entering 6 digits
            self.logger.info("Waiting for OTP auto-submit...")
            time.sleep(3)

            # Check if page has left OTP page (auto-submit successful)
            otp_still_visible = False
            try:
                otp_check = self.page.wait_for_selector('input[name="otp"]', timeout=2000)
                if otp_check and otp_check.is_visible():
                    otp_still_visible = True
            except PlaywrightTimeout:
                pass

            if otp_still_visible:
                # OTP not auto-submitted, manually click verify button
                self.logger.info("OTP not auto-submitted, trying to click verify button...")
                verify_button = self.find_element(
                    [
                        'button:has-text("驗證")',
                        'button:has-text("確認")',
                        'button:has-text("送出")',
                        'button[type="submit"]',
                    ],
                    "verify button",
                    required=False,
                )
                if verify_button:
                    verify_button.click()
                    self.logger.info("Verify button clicked")
                else:
                    self.page.keyboard.press("Enter")
                    self.logger.info("Attempting to submit verification code with Enter key")
            else:
                self.logger.info("OTP auto-submitted, page has redirected")

            # Wait for page to finish loading
            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass
            time.sleep(3)

            self.take_screenshot("05_after_verification")
        else:
            self.logger.info("No 2FA page detected, may not need verification code or already logged in")

        # Step 3: Handle "Service Selection" page
        service_link_selectors = [
            'a[href="https://pro.104.com.tw/"]',
            'a.block.py-24',
            '.MultipleProduct__product a',
            'a:has(img[src*="104logo_pro"])',
            'a:has-text("企業大師")',
        ]

        service_link = self.find_element(
            service_link_selectors, "104 Pro service link", required=False
        )

        if service_link:
            self.logger.info("Detected service selection page, clicking '104 Pro'...")
            service_link.click()
            time.sleep(3)

            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass

            self.take_screenshot("06_after_service_selection")
        else:
            self.logger.info("No service selection page detected, may already be on main page")

        # Step 4: Click "Private Secretary" to enter psc2 page
        psc_selectors = [
            'div.-major.widget.psc',
            'a:has-text("私人秘書")',
            'div:has-text("私人秘書") >> visible=true',
            '.widget.psc',
        ]

        psc_button = self.find_element(psc_selectors, "Private Secretary button", required=False)

        if psc_button:
            self.logger.info("Found 'Private Secretary', clicking to enter...")
            psc_button.click()
            time.sleep(3)

            try:
                self.page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass

            self.take_screenshot("07_after_psc_click")
        else:
            # May already be on psc2 page, try direct navigation
            self.logger.info("Private Secretary button not found, attempting direct navigation to psc2...")
            try:
                self.page.goto("https://pro.104.com.tw/psc2", wait_until="networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass
            time.sleep(3)
            self.take_screenshot("07_navigate_psc2")

        # Step 5: Confirm successful login
        current_url = self.page.url
        self.logger.info(f"Current URL: {current_url}")

        if "login" in current_url.lower():
            error_text = self.page.query_selector(
                ".error-message, .alert-danger, .error, .text-danger"
            )
            if error_text:
                self.logger.error(f"Login failed: {error_text.inner_text()}")
            else:
                self.logger.error("Login seems to have failed (still on login page)")
            self.take_screenshot("error_login_failed")
            return False

        self.logger.info("✅ Login successful!")
        return True

    def punch(self, action: str) -> bool:
        """
        Execute clock-in/out action

        Args:
            action: "clock_in" (start of work) or "clock_out" (end of work)
        """
        action_text = "Clock In" if action == "clock_in" else "Clock Out"

        # Confirm currently on psc2 page
        current_url = self.page.url
        if "psc2" not in current_url:
            self.logger.info("Not currently on psc2 page, attempting navigation...")
            try:
                self.page.goto("https://pro.104.com.tw/psc2", wait_until="networkidle", timeout=30000)
            except PlaywrightTimeout:
                self.logger.warning("psc2 page load timeout, attempting to continue...")
            time.sleep(3)

        self.take_screenshot(f"08_punch_page_{action}")

        # Find punch button
        punch_selectors = [
            'span.btn.btn-lg.btn-block',
            '.PSC-HomeWidget.clockIn span.btn',
            '.PSC-HomeWidget span.btn-block',
            'span.btn-block:has-text("打卡")',
            '.PSC-ClockIn-root span.btn',
            'span:has-text("打卡")',
        ]

        punch_button = self.find_element(punch_selectors, "punch button")

        if not punch_button:
            self.logger.error("Cannot find punch button!")
            self.take_screenshot("error_no_punch_button")
            return False

        punch_button.click()
        self.logger.info(f"Punch button clicked ({action_text})")

        time.sleep(3)
        self.take_screenshot(f"09_after_punch_click_{action}")

        # Wait for "Punch Success" popup (J104BoxDialog)
        success_selectors = [
            '.J104BoxDialog .title:has-text("打卡成功")',
            '.J104BoxDialog:has-text("打卡成功")',
            'text="打卡成功"',
            ':has-text("打卡成功")',
        ]

        for selector in success_selectors:
            try:
                element = self.page.wait_for_selector(selector, timeout=5000)
                if element:
                    self.logger.info(f"✅ {action_text} successful!")
                    self.take_screenshot(f"10_punch_success_{action}")

                    # Close the J104BoxDialog popup
                    close_selectors = [
                        '.J104BoxDialog .close.fa',
                        '.J104BoxDialog .close',
                        '.J104BoxDialog button:has-text("確認")',
                        '.J104BoxDialog button:has-text("確定")',
                        'button:has-text("確認")',
                        'button:has-text("確定")',
                    ]
                    for close_sel in close_selectors:
                        try:
                            close_btn = self.page.wait_for_selector(close_sel, timeout=3000)
                            if close_btn and close_btn.is_visible():
                                close_btn.click()
                                self.logger.info("Popup closed")
                                break
                        except PlaywrightTimeout:
                            continue

                    return True
            except PlaywrightTimeout:
                continue

        self.logger.warning("'Punch Success' message not found, please check screenshot to confirm result")
        self.take_screenshot(f"10_punch_result_unknown_{action}")
        return True

    def run(self, action: str, skip_weekday_check: bool = False):
        """
        Main execution flow

        Args:
            action: "clock_in" or "clock_out"
            skip_weekday_check: Skip weekday check (for testing)
        """
        # Validate required environment variables
        if not Config.validate():
            self.logger.error("Please set PRO104_ACCOUNT and PRO104_PASSWORD environment variables")
            sys.exit(1)

        if not Config.GMAIL_ADDRESS or not Config.GMAIL_APP_PASSWORD:
            self.logger.warning("⚠️  Gmail environment variables not set (GMAIL_ADDRESS, GMAIL_APP_PASSWORD)")
            self.logger.warning("   If 104 requires 2FA verification code, it cannot be automatically retrieved!")

        # Check if it's a weekday
        if not skip_weekday_check and not self.is_weekday():
            self.logger.info("Today is not a weekday, skipping clock-in.")
            return

        # Add random delay
        self.random_delay()

        action_text = "Clock In" if action == "clock_in" else "Clock Out"
        self.logger.info(f"===== Starting {action_text} =====")
        self.logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        for attempt in range(1, Config.MAX_RETRIES + 1):
            self.logger.info(f"Attempt {attempt}/{Config.MAX_RETRIES}")

            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=[
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                        ],
                    )

                    context = browser.new_context(
                        viewport={"width": 1280, "height": 720},
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        locale="zh-TW",
                        timezone_id="Asia/Taipei",
                    )

                    self.page = context.new_page()

                    # Step 1: Login (including 2FA)
                    if not self.login():
                        raise Exception("Login failed")

                    # Step 2: Punch
                    if not self.punch(action):
                        raise Exception("Punch failed")

                    # Step 3: Send Telegram notification
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    notification_message = (
                        f"🎉 <b>104 Clock-In Successful</b>\n\n"
                        f"📋 Type: {action_text}\n"
                        f"🕐 Time: {now}\n"
                        f"✅ Status: Success"
                    )
                    self.notifier.send(notification_message)

                    self.logger.info(f"===== {action_text} Completed =====")
                    browser.close()
                    return

            except Exception as e:
                self.logger.error(f"Attempt {attempt} failed: {e}")
                if attempt < Config.MAX_RETRIES:
                    self.logger.info(f"Waiting {Config.RETRY_INTERVAL}s before retry...")
                    time.sleep(Config.RETRY_INTERVAL)
                else:
                    self.logger.error(
                        f"Max retries reached ({Config.MAX_RETRIES}), {action_text} failed!"
                    )
                    sys.exit(1)


def main():
    """Program entry point"""
    parser = argparse.ArgumentParser(description="104 Pro Automatic Clock-In Script (with 2FA)")
    parser.add_argument(
        "--action",
        choices=["clock_in", "clock_out"],
        required=True,
        help="Clock action: clock_in (start) or clock_out (end)",
    )
    parser.add_argument(
        "--no-delay",
        action="store_true",
        help="Skip random delay (for testing)",
    )
    parser.add_argument(
        "--skip-weekday-check",
        action="store_true",
        help="Skip weekday check (for testing)",
    )
    parser.add_argument(
        "--test-gmail",
        action="store_true",
        help="Only test Gmail connection and verification code retrieval (no clock-in)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (saves screenshots)",
    )

    args = parser.parse_args()

    # Initialize logger
    logger = Logger()

    # Test Gmail mode
    if args.test_gmail:
        logger.info("===== Testing Gmail Connection =====")
        if not Config.GMAIL_ADDRESS or not Config.GMAIL_APP_PASSWORD:
            logger.error("Please set GMAIL_ADDRESS and GMAIL_APP_PASSWORD")
            sys.exit(1)

        otp_reader = GmailOTPReader(logger)
        # Search for emails from the last 10 minutes
        after = datetime.now() - timedelta(minutes=10)
        code = otp_reader.fetch_verification_code(after)
        if code:
            logger.info(f"Found verification code: {code}")
        else:
            logger.info("No 104 verification code email found in the last 10 minutes")
        logger.info("Gmail connection test completed")
        return

    # Override random delay settings
    if args.no_delay:
        Config.RANDOM_DELAY_MIN = 0
        Config.RANDOM_DELAY_MAX = 0

    # Run clock-in bot
    bot = Pro104ClockIn(logger, debug=args.debug)
    bot.run(args.action, skip_weekday_check=args.skip_weekday_check)


if __name__ == "__main__":
    main()
