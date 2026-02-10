#!/usr/bin/env python3
"""
104 企業大師 (pro.104.com.tw) 自動打卡腳本
使用 Playwright 進行瀏覽器自動化 + Gmail IMAP 讀取 2FA 驗證碼

使用方式:
    python clock_in.py --action clock_in   # 上班打卡
    python clock_in.py --action clock_out  # 下班打卡

環境變數:
    PRO104_ACCOUNT    - 104 登入帳號
    PRO104_PASSWORD   - 104 登入密碼
    GMAIL_ADDRESS     - Gmail 地址 (收驗證碼用)
    GMAIL_APP_PASSWORD - Gmail 應用程式密碼 (非 Gmail 登入密碼)

作者: Claude (為 jason 客製化)
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
from email.header import decode_header
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# 載入 .env 檔案（自動找腳本同目錄下的 .env）
load_dotenv(Path(__file__).parent / ".env")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("請先安裝 Playwright: pip install playwright && playwright install chromium")
    sys.exit(1)


# ============================================================
# 設定區
# ============================================================

# 104 企業大師相關 URL
LOGIN_URL = "https://bsignin.104.com.tw/login"
CLOCK_URL = "https://pro.104.com.tw/psc2/attendance/punch"

# 登入資訊 (從 .env 檔案或環境變數讀取)
ACCOUNT = os.environ.get("PRO104_ACCOUNT", "")
PASSWORD = os.environ.get("PRO104_PASSWORD", "")

# Gmail 設定 (用於讀取 2FA 驗證碼)
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_IMAP_SERVER = "imap.gmail.com"
GMAIL_IMAP_PORT = 993

# 驗證碼相關設定
VERIFICATION_CODE_WAIT = 60      # 最多等待驗證碼幾秒
VERIFICATION_CODE_POLL = 5       # 每幾秒檢查一次信箱
VERIFICATION_CODE_LENGTH = 6     # 驗證碼長度 (通常是 6 碼數字)

# 104 寄件者 email（用於篩選信件）
# ⬇️ 請根據實際收到的驗證碼信件調整寄件者 ⬇️
SENDER_FILTERS = [
    "104.com.tw",
    "pro.104.com.tw",
    "noreply@104.com.tw",
    "service@104.com.tw",
]

# 隨機延遲範圍（秒），避免每天在完全相同的時間打卡
RANDOM_DELAY_MIN = int(os.environ.get("RANDOM_DELAY_MIN", "0"))       # 最少延遲秒數
RANDOM_DELAY_MAX = int(os.environ.get("RANDOM_DELAY_MAX", "300"))     # 最多延遲秒數 (預設 5 分鐘)

# 重試設定
MAX_RETRIES = 3
RETRY_INTERVAL = 30  # 秒

# 截圖保存路徑（用於除錯）
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"

# 日誌設定
LOG_DIR = Path(__file__).parent / "logs"


# ============================================================
# 日誌設定
# ============================================================

def setup_logging():
    """設定日誌"""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"clockin_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(__name__)


logger = setup_logging()


# ============================================================
# 工具函式
# ============================================================

def random_delay():
    """加入隨機延遲，模擬人類行為"""
    delay = random.randint(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX)
    if delay > 0:
        logger.info(f"隨機延遲 {delay} 秒...")
        time.sleep(delay)


def take_screenshot(page, name: str):
    """截圖用於除錯"""
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = SCREENSHOT_DIR / f"{name}_{timestamp}.png"
    page.screenshot(path=str(filepath), full_page=True)
    logger.info(f"截圖已保存: {filepath}")


def is_weekday() -> bool:
    """檢查今天是否為工作日 (週一到週五)"""
    return datetime.now().weekday() < 5


# ============================================================
# Gmail 2FA 驗證碼讀取
# ============================================================

def decode_mime_header(header_value: str) -> str:
    """解碼 MIME 編碼的 email header"""
    decoded_parts = decode_header(header_value)
    result = ""
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(charset or "utf-8", errors="replace")
        else:
            result += part
    return result


def get_email_body(msg) -> str:
    """從 email message 物件中取得純文字內容"""
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # 跳過附件
            if "attachment" in content_disposition:
                continue

            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body += payload.decode(charset, errors="replace")
            elif content_type == "text/html" and not body:
                # 如果沒有純文字版本，用 HTML 版本
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html_text = payload.decode(charset, errors="replace")
                    # 簡單移除 HTML 標籤
                    body += re.sub(r"<[^>]+>", " ", html_text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")

    return body


def extract_verification_code(text: str) -> str | None:
    """
    從文字中提取驗證碼

    ⬇️ 請根據實際收到的驗證碼信件格式調整 ⬇️

    常見的驗證碼格式:
    - 純數字: 123456
    - 信件中包含: "驗證碼: 123456" 或 "verification code: 123456"
    """
    # 策略1: 找「驗證碼」關鍵字後面的數字
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

    # 策略2: 找獨立的 N 位數字 (可能是驗證碼)
    # 尋找被空白或標點符號包圍的 4-8 位數字
    standalone_numbers = re.findall(r"(?<!\d)(\d{4,8})(?!\d)", text)
    if standalone_numbers:
        # 優先回傳指定長度的
        for num in standalone_numbers:
            if len(num) == VERIFICATION_CODE_LENGTH:
                return num
        # 否則回傳第一個找到的
        return standalone_numbers[0]

    return None


def fetch_verification_code_from_gmail(after_timestamp: datetime) -> str | None:
    """
    從 Gmail 讀取 104 企業大師的 2FA 驗證碼

    Args:
        after_timestamp: 只搜尋這個時間之後的信件

    Returns:
        驗證碼字串，找不到則回傳 None
    """
    logger.info("正在連接 Gmail IMAP...")

    try:
        # 連接 Gmail IMAP
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP_SERVER, GMAIL_IMAP_PORT)
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mail.select("INBOX")

        logger.info("已連接 Gmail，正在搜尋驗證碼信件...")

        # 搜尋條件：今天的信件
        date_str = after_timestamp.strftime("%d-%b-%Y")
        search_criteria = f'(SINCE "{date_str}")'

        status, message_ids = mail.search(None, search_criteria)

        if status != "OK" or not message_ids[0]:
            logger.info("沒有找到符合條件的信件")
            mail.logout()
            return None

        # 從最新的信件開始找
        ids = message_ids[0].split()
        ids.reverse()  # 最新的在前面

        for msg_id in ids[:20]:  # 只檢查最近 20 封
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # 檢查寄件者
            sender = decode_mime_header(msg.get("From", ""))
            is_from_104 = any(f in sender.lower() for f in SENDER_FILTERS)

            if not is_from_104:
                continue

            # 檢查信件時間
            date_str_raw = msg.get("Date", "")
            try:
                msg_date = email.utils.parsedate_to_datetime(date_str_raw)
                # 確保是在觸發時間之後收到的
                if msg_date.replace(tzinfo=None) < after_timestamp.replace(tzinfo=None):
                    continue
            except (ValueError, TypeError):
                pass  # 無法解析日期，繼續嘗試

            # 取得信件內容
            subject = decode_mime_header(msg.get("Subject", ""))
            body = get_email_body(msg)

            logger.info(f"找到 104 的信件: {subject}")

            # 先從主旨找驗證碼
            code = extract_verification_code(subject)
            if code:
                logger.info(f"從信件主旨中找到驗證碼: {code}")
                mail.logout()
                return code

            # 再從內文找驗證碼
            code = extract_verification_code(body)
            if code:
                logger.info(f"從信件內文中找到驗證碼: {code}")
                mail.logout()
                return code

            logger.debug(f"這封信沒有找到驗證碼，繼續搜尋...")

        mail.logout()
        return None

    except imaplib.IMAP4.error as e:
        logger.error(f"Gmail IMAP 錯誤: {e}")
        logger.error("請確認 GMAIL_APP_PASSWORD 是否正確設定")
        return None
    except Exception as e:
        logger.error(f"讀取 Gmail 時發生錯誤: {e}")
        return None


def wait_and_get_verification_code(after_timestamp: datetime) -> str | None:
    """
    等待並取得驗證碼（會持續輪詢 Gmail）

    Args:
        after_timestamp: 只搜尋這個時間之後的信件

    Returns:
        驗證碼字串，超時則回傳 None
    """
    logger.info(
        f"等待驗證碼信件... (最多等待 {VERIFICATION_CODE_WAIT} 秒, "
        f"每 {VERIFICATION_CODE_POLL} 秒檢查一次)"
    )

    elapsed = 0
    while elapsed < VERIFICATION_CODE_WAIT:
        code = fetch_verification_code_from_gmail(after_timestamp)
        if code:
            return code

        logger.info(
            f"尚未收到驗證碼，{VERIFICATION_CODE_POLL} 秒後重試... "
            f"({elapsed}/{VERIFICATION_CODE_WAIT}s)"
        )
        time.sleep(VERIFICATION_CODE_POLL)
        elapsed += VERIFICATION_CODE_POLL

    logger.error(f"等待 {VERIFICATION_CODE_WAIT} 秒後仍未收到驗證碼")
    return None


# ============================================================
# 核心邏輯
# ============================================================

def login(page) -> bool:
    """
    登入 104 企業大師（含 2FA 驗證碼處理）

    流程:
    1. 輸入帳號密碼 → 點擊登入
    2. 頁面出現驗證碼輸入框
    3. 從 Gmail 讀取驗證碼
    4. 輸入驗證碼 → 完成登入

    ⚠️ 重要：你需要根據實際頁面調整下方的 selector
    """
    logger.info(f"正在前往登入頁: {LOGIN_URL}")
    page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
    time.sleep(2)

    take_screenshot(page, "01_login_page")

    # -----------------------------------------------------------
    # 步驟 1: 輸入帳號密碼
    # ⬇️ 以下 selector 需要根據實際頁面調整 ⬇️
    # -----------------------------------------------------------

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

    # 找到帳號輸入框
    account_input = _find_element(page, account_selectors, "帳號輸入框")
    if not account_input:
        return False

    # 找到密碼輸入框
    password_input = _find_element(page, password_selectors, "密碼輸入框")
    if not password_input:
        return False

    # 輸入帳號密碼（模擬人類打字速度）
    account_input.click()
    account_input.fill("")
    account_input.type(ACCOUNT, delay=random.randint(50, 150))
    time.sleep(0.5)

    password_input.click()
    password_input.fill("")
    password_input.type(PASSWORD, delay=random.randint(50, 150))
    time.sleep(0.5)

    take_screenshot(page, "02_credentials_filled")

    # 記錄送出時間（用於篩選驗證碼信件）
    submit_timestamp = datetime.now() - timedelta(seconds=30)

    # 點擊登入
    login_button = _find_element(page, login_button_selectors, "登入按鈕")
    if not login_button:
        return False

    login_button.click()
    logger.info("已點擊登入按鈕，等待頁面回應...")

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        pass
    time.sleep(3)

    take_screenshot(page, "03_after_first_login")

    # -----------------------------------------------------------
    # 步驟 2: 處理 2FA 驗證碼
    # ⬇️ 以下 selector 需要根據實際頁面調整 ⬇️
    # -----------------------------------------------------------

    verification_input_selectors = [
        'input[name="verificationCode"]',
        'input[name="verification_code"]',
        'input[name="otp"]',
        'input[name="code"]',
        'input[placeholder*="驗證碼"]',
        'input[placeholder*="認證碼"]',
        'input[placeholder*="verification"]',
        'input[type="tel"]',  # 有些 OTP 欄位用 tel type
        'input[maxlength="6"]',  # 6 碼輸入框
        '.otp-input input',
        '#verificationCode',
        '#otp',
    ]

    verification_submit_selectors = [
        'button[type="submit"]',
        'button:has-text("確認")',
        'button:has-text("驗證")',
        'button:has-text("送出")',
        'button:has-text("確定")',
        'button:has-text("Submit")',
        'button:has-text("Verify")',
    ]

    # 檢查是否出現驗證碼輸入框
    verification_input = _find_element(
        page, verification_input_selectors, "驗證碼輸入框", required=False
    )

    if verification_input:
        logger.info("偵測到 2FA 驗證碼頁面，開始從 Gmail 讀取驗證碼...")

        # 檢查 Gmail 設定
        if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
            logger.error("需要 Gmail 設定來讀取驗證碼！")
            logger.error("請設定環境變數 GMAIL_ADDRESS 和 GMAIL_APP_PASSWORD")
            return False

        # 從 Gmail 讀取驗證碼
        code = wait_and_get_verification_code(submit_timestamp)

        if not code:
            logger.error("無法取得驗證碼！")
            take_screenshot(page, "error_no_verification_code")
            return False

        logger.info(f"取得驗證碼: {code}")

        # 輸入驗證碼
        verification_input.click()
        verification_input.fill("")
        verification_input.type(code, delay=random.randint(80, 200))
        time.sleep(1)

        take_screenshot(page, "04_verification_code_filled")

        # 點擊驗證送出按鈕
        verify_button = _find_element(
            page, verification_submit_selectors, "驗證碼送出按鈕"
        )
        if verify_button:
            verify_button.click()
            logger.info("已送出驗證碼，等待驗證...")
        else:
            # 有些頁面輸入完會自動送出，或按 Enter 即可
            try:
                # 重新取得驗證碼輸入框（避免 DOM 分離問題）
                verification_input_selectors = [
                    'input[name="otp"]',
                    'input[placeholder*="驗證碼"]',
                    'input[id*="otp"]',
                    'input[id*="verification"]',
                    'input[type="tel"]'
                ]
                fresh_verification_input = _find_element(
                    page, verification_input_selectors, "驗證碼輸入框"
                )
                if fresh_verification_input:
                    fresh_verification_input.press("Enter")
                    logger.info("嘗試按 Enter 送出驗證碼...")
                else:
                    # 如果輸入框也找不到，嘗試通用的 Enter 按鍵
                    page.keyboard.press("Enter")
                    logger.info("使用全域 Enter 送出驗證碼...")
            except Exception as e:
                logger.warning(f"按 Enter 失敗: {e}，嘗試其他提交方式...")
                # 嘗試點擊任何可能的提交按鈕
                submit_buttons = page.locator('button[type="submit"], input[type="submit"]').all()
                for btn in submit_buttons:
                    try:
                        if btn.is_visible():
                            btn.click()
                            logger.info("找到並點擊了提交按鈕")
                            break
                    except:
                        continue

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass
        time.sleep(3)

        take_screenshot(page, "05_after_verification")
    else:
        logger.info("未偵測到 2FA 頁面，可能不需要驗證碼或已直接登入成功")

    # -----------------------------------------------------------
    # 步驟 3: 處理「服務項目」選擇頁面
    # 根據截圖: 頁面標題「服務項目」，104 企業大師是一個 a.block.py-24 的連結
    # -----------------------------------------------------------

    service_link_selectors = [
        'a.block.py-24',                  # 截圖中的精確 class
        'a.block',                         # 備用: 只用 block class
        'a:has-text("企業大師")',           # 備用: 用文字找
        'a:has-text("104企業大師")',
    ]

    service_link = _find_element(
        page, service_link_selectors, "104 企業大師服務連結", required=False
    )

    if service_link:
        logger.info("偵測到服務選擇頁面，點擊「104 企業大師」...")
        service_link.click()
        time.sleep(3)

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass

        take_screenshot(page, "06_after_service_selection")
    else:
        logger.info("未偵測到服務選擇頁面，可能已直接進入主頁")

    # -----------------------------------------------------------
    # 步驟 4: 點擊「私人秘書」進入 psc2 頁面
    # 根據截圖: sidebar 中有 div.-major.widget.psc 包含「私人秘書」
    # -----------------------------------------------------------

    psc_selectors = [
        'div.-major.widget.psc',           # 截圖中的精確 class
        'a:has-text("私人秘書")',
        'div:has-text("私人秘書") >> visible=true',
        '.widget.psc',
    ]

    psc_button = _find_element(
        page, psc_selectors, "私人秘書按鈕", required=False
    )

    if psc_button:
        logger.info("找到「私人秘書」，點擊進入...")
        psc_button.click()
        time.sleep(3)

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass

        take_screenshot(page, "07_after_psc_click")
    else:
        # 可能已經在 psc2 頁面了，嘗試直接導航
        logger.info("未找到「私人秘書」按鈕，嘗試直接前往 psc2...")
        try:
            page.goto("https://pro.104.com.tw/psc2", wait_until="networkidle", timeout=15000)
        except PlaywrightTimeout:
            pass
        time.sleep(3)
        take_screenshot(page, "07_navigate_psc2")

    # -----------------------------------------------------------
    # 步驟 5: 確認登入成功
    # -----------------------------------------------------------

    current_url = page.url
    logger.info(f"目前的 URL: {current_url}")

    if "login" in current_url.lower():
        error_text = page.query_selector(
            ".error-message, .alert-danger, .error, .text-danger"
        )
        if error_text:
            logger.error(f"登入失敗: {error_text.inner_text()}")
        else:
            logger.error("登入似乎失敗了（仍在登入頁面）")
        take_screenshot(page, "error_login_failed")
        return False

    logger.info("✅ 登入成功！")
    return True


def _find_element(page, selectors: list, name: str, required: bool = True):
    """
    嘗試多個 selector 找到頁面元素

    Args:
        page: Playwright page
        selectors: 要嘗試的 selector 列表
        name: 元素名稱（用於日誌）
        required: 是否為必要元素（找不到時是否報錯）

    Returns:
        找到的元素，或 None
    """
    for selector in selectors:
        try:
            element = page.wait_for_selector(selector, timeout=3000)
            if element and element.is_visible():
                logger.info(f"找到{name}: {selector}")
                return element
        except PlaywrightTimeout:
            continue

    if required:
        logger.error(f"找不到{name}！請檢查 selector 設定。")
        take_screenshot(page, f"error_no_{name}")
    return None


def punch(page, action: str) -> bool:
    """
    執行打卡動作

    根據截圖，打卡頁面結構:
    - 打卡區塊: div.PSC-HomeWidget.clockIn
    - 標題: h3.ico.ico-m4 "網路打卡"
    - 打卡按鈕: span.btn.btn-lg.btn-block "打卡"
    - 上班模式: div.PSC-ClockIn.morning

    Args:
        page: Playwright page object
        action: "clock_in" (上班打卡) 或 "clock_out" (下班打卡)
    """
    action_text = "上班" if action == "clock_in" else "下班"

    # 確認目前在 psc2 頁面（登入流程最後應該已經導航到這裡）
    current_url = page.url
    if "psc2" not in current_url:
        logger.info("目前不在 psc2 頁面，嘗試導航...")
        try:
            page.goto("https://pro.104.com.tw/psc2", wait_until="networkidle", timeout=30000)
        except PlaywrightTimeout:
            logger.warning("psc2 頁面載入超時，嘗試繼續...")
        time.sleep(3)

    take_screenshot(page, f"08_punch_page_{action}")

    # -----------------------------------------------------------
    # 找到打卡按鈕
    # 根據截圖: span.btn.btn-lg.btn-block 文字為「打卡」
    # 位於 div.PSC-HomeWidget.clockIn 區塊內
    # -----------------------------------------------------------

    punch_selectors = [
        'span.btn.btn-lg.btn-block',                        # 截圖中的精確 selector
        '.PSC-HomeWidget.clockIn span.btn',                  # 在打卡區塊內找按鈕
        '.PSC-HomeWidget span.btn-block',                    # 備用
        'span.btn-block:has-text("打卡")',                   # 用文字 + class
        '.PSC-ClockIn-root span.btn',                        # ClockIn root 內的按鈕
        'span:has-text("打卡")',                              # 最後手段: 純文字
    ]

    punch_button = _find_element(page, punch_selectors, "打卡按鈕")

    if not punch_button:
        logger.error("找不到打卡按鈕！")
        take_screenshot(page, "error_no_punch_button")
        return False

    punch_button.click()
    logger.info(f"已點擊打卡按鈕 ({action_text})")

    time.sleep(3)
    take_screenshot(page, f"09_after_punch_click_{action}")

    # -----------------------------------------------------------
    # 等待「打卡成功」popup
    # -----------------------------------------------------------

    success_selectors = [
        'text="打卡成功"',
        ':has-text("打卡成功")',
        '.modal:has-text("打卡成功")',
        '.popup:has-text("打卡成功")',
        '.alert:has-text("打卡成功")',
        '.swal2-popup:has-text("打卡成功")',        # SweetAlert2
        '.toast:has-text("打卡成功")',
    ]

    for selector in success_selectors:
        try:
            element = page.wait_for_selector(selector, timeout=5000)
            if element:
                logger.info(f"✅ {action_text}打卡成功！")
                take_screenshot(page, f"10_punch_success_{action}")

                # 關閉 popup（如果有確認按鈕）
                try:
                    close_btn = page.wait_for_selector(
                        'button:has-text("確認"), button:has-text("確定"), '
                        'button:has-text("OK"), .swal2-confirm',
                        timeout=3000,
                    )
                    if close_btn and close_btn.is_visible():
                        close_btn.click()
                except PlaywrightTimeout:
                    pass

                return True
        except PlaywrightTimeout:
            continue

    logger.warning("未找到「打卡成功」訊息，請檢查截圖確認結果")
    take_screenshot(page, f"10_punch_result_unknown_{action}")
    return True


def run(action: str, skip_weekday: bool = False):
    """
    主要執行流程
    """
    # 驗證必要環境變數
    if not ACCOUNT or not PASSWORD:
        logger.error("請設定環境變數 PRO104_ACCOUNT 和 PRO104_PASSWORD")
        sys.exit(1)

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        logger.warning("⚠️  未設定 Gmail 環境變數 (GMAIL_ADDRESS, GMAIL_APP_PASSWORD)")
        logger.warning("   如果 104 需要 2FA 驗證碼，將無法自動讀取！")

    # 檢查是否為工作日
    if not skip_weekday and not is_weekday():
        logger.info("今天不是工作日，跳過打卡。")
        return

    # 加入隨機延遲
    random_delay()

    action_text = "上班打卡" if action == "clock_in" else "下班打卡"
    logger.info(f"===== 開始 {action_text} =====")
    logger.info(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"第 {attempt}/{MAX_RETRIES} 次嘗試")

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

                page = context.new_page()

                # 步驟1: 登入 (含 2FA)
                if not login(page):
                    raise Exception("登入失敗")

                # 步驟2: 打卡
                if not punch(page, action):
                    raise Exception("打卡失敗")

                logger.info(f"===== {action_text}完成 =====")
                browser.close()
                return

        except Exception as e:
            logger.error(f"第 {attempt} 次嘗試失敗: {e}")
            if attempt < MAX_RETRIES:
                logger.info(f"等待 {RETRY_INTERVAL} 秒後重試...")
                time.sleep(RETRY_INTERVAL)
            else:
                logger.error(
                    f"已達最大重試次數 ({MAX_RETRIES})，{action_text}失敗！"
                )
                sys.exit(1)


# ============================================================
# 程式進入點
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="104 企業大師自動打卡腳本 (含 2FA)")
    parser.add_argument(
        "--action",
        choices=["clock_in", "clock_out"],
        required=True,
        help="打卡動作: clock_in (上班) 或 clock_out (下班)",
    )
    parser.add_argument(
        "--no-delay",
        action="store_true",
        help="跳過隨機延遲（測試用）",
    )
    parser.add_argument(
        "--skip-weekday-check",
        action="store_true",
        help="跳過工作日檢查（測試用）",
    )
    parser.add_argument(
        "--test-gmail",
        action="store_true",
        help="只測試 Gmail 連線和讀取驗證碼（不執行打卡）",
    )

    args = parser.parse_args()

    # 測試 Gmail 模式
    if args.test_gmail:
        logger.info("===== 測試 Gmail 連線 =====")
        if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
            logger.error("請設定 GMAIL_ADDRESS 和 GMAIL_APP_PASSWORD")
            sys.exit(1)
        # 搜尋最近 10 分鐘的信件
        after = datetime.now() - timedelta(minutes=10)
        code = fetch_verification_code_from_gmail(after)
        if code:
            logger.info(f"找到驗證碼: {code}")
        else:
            logger.info("最近 10 分鐘內沒有找到 104 的驗證碼信件")
        logger.info("Gmail 連線測試完成")
        return

    # 覆蓋隨機延遲設定
    if args.no_delay:
        global RANDOM_DELAY_MIN, RANDOM_DELAY_MAX
        RANDOM_DELAY_MIN = 0
        RANDOM_DELAY_MAX = 0

    run(args.action, skip_weekday=args.skip_weekday_check)


if __name__ == "__main__":
    main()
