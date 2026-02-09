# 104 企業大師自動打卡腳本

使用 Python + Playwright 自動化 [pro.104.com.tw](https://pro.104.com.tw) 的上下班打卡流程，支援 Gmail 2FA 驗證碼自動讀取。

---

## 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 設定帳號密碼 + Gmail

```bash
cp .env.example .env
# 編輯 .env 填入你的 104 帳號密碼和 Gmail 應用程式密碼
```

### 3. 取得 Gmail 應用程式密碼

腳本需要透過 IMAP 讀取 Gmail 裡的 2FA 驗證碼，所以需要一組「應用程式密碼」：

1. 前往 [Google 帳戶安全性](https://myaccount.google.com/security)
2. 確認已開啟「兩步驟驗證」
3. 在搜尋欄輸入「應用程式密碼」(App Passwords)
4. 建立一組新的應用程式密碼（名稱填 "104打卡" 即可）
5. 把產生的 16 碼密碼貼到 `.env` 的 `GMAIL_APP_PASSWORD`

### 4. 測試 Gmail 連線

先確認腳本能正常讀取你的 Gmail：

```bash
export $(grep -v '^#' .env | xargs)
python clock_in.py --test-gmail
```

### 5. 首次測試打卡（需要調整 selector）

```bash
python clock_in.py --action clock_in --no-delay --skip-weekday-check
```

執行後檢查 `screenshots/` 資料夾裡的截圖，確認每一步是否正確。

### 6. 調整 Selector

打開 `clock_in.py`，找到以下標記處進行調整：

```
# ⬇️ 以下 selector 需要根據實際頁面調整 ⬇️
```

**如何找到正確的 selector：**

1. 用 Chrome 打開 https://pro.104.com.tw
2. 按 F12 開啟 DevTools
3. 用選取工具 (Ctrl+Shift+C) 點擊各個輸入框和按鈕
4. 記下元素的 id、name、class 等屬性
5. 更新腳本中的 selector

需要調整的地方有三處：登入表單、2FA 驗證碼輸入框、打卡按鈕。

---

## 自動打卡流程

```
1. 開啟 headless 瀏覽器
2. 前往 104 企業大師登入頁
3. 輸入帳號密碼 → 點擊登入
4. 偵測到 2FA 驗證碼頁面
5. 連接 Gmail IMAP → 搜尋來自 104 的最新信件
6. 從信件中提取驗證碼（輪詢最多 60 秒）
7. 輸入驗證碼 → 完成登入
8. 前往打卡頁面 → 點擊上班/下班打卡
9. 截圖記錄結果
```

---

## 雲端部署方案

### 方案 A：VPS + Cron Job（推薦）

```bash
# 在伺服器上
git clone <your-repo> 104-auto-clockin
cd 104-auto-clockin

pip install -r requirements.txt
playwright install chromium --with-deps

cp .env.example .env
vim .env

chmod +x setup_cron.sh
./setup_cron.sh
```

### 方案 B：Docker + Cron

```bash
docker build -t 104-clockin .

# 測試
docker run --rm --env-file .env \
  -v $(pwd)/screenshots:/app/screenshots \
  104-clockin --action clock_in --no-delay

# 設定 cron (crontab -e)
# 50 8 * * 1-5 docker run --rm --env-file /path/to/.env 104-clockin --action clock_in
# 5 18 * * 1-5 docker run --rm --env-file /path/to/.env 104-clockin --action clock_out
```

### 方案 C：GitHub Actions（免費）

在 GitHub repo 中建立 `.github/workflows/clockin.yml`：

```yaml
name: Auto Clock In/Out

on:
  schedule:
    # UTC 時間，台北時間 -8
    - cron: '50 0 * * 1-5'   # 上班 08:50
    - cron: '5 10 * * 1-5'   # 下班 18:05
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

在 GitHub repo 的 Settings > Secrets 中設定：
- `PRO104_ACCOUNT`
- `PRO104_PASSWORD`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`

---

## 指令參考

```bash
# 上班打卡
python clock_in.py --action clock_in

# 下班打卡
python clock_in.py --action clock_out

# 測試模式（無延遲 + 跳過工作日檢查）
python clock_in.py --action clock_in --no-delay --skip-weekday-check

# 只測試 Gmail 連線
python clock_in.py --test-gmail
```

---

## 專案結構

```
104-auto-clockin/
├── clock_in.py          # 主要打卡腳本 (含 2FA Gmail 讀取)
├── requirements.txt     # Python 依賴
├── .env.example         # 環境變數範本
├── .env                 # 你的實際設定 (不要上傳到 git)
├── setup_cron.sh        # Cron job 自動設定腳本
├── Dockerfile           # Docker 建構檔
├── docker-compose.yml   # Docker Compose 設定
├── README.md            # 說明文件
├── logs/                # 執行日誌 (自動產生)
└── screenshots/         # 截圖 (自動產生)
```

---

## 疑難排解

**Q: Gmail 連線失敗？**
確認 `GMAIL_APP_PASSWORD` 是「應用程式密碼」而非你的登入密碼。也確認 Gmail 有開啟 IMAP（Gmail 設定 > 轉寄和 POP/IMAP > 啟用 IMAP）。

**Q: 找不到驗證碼？**
腳本會搜尋來自 `104.com.tw` 的信件。如果 104 的寄件者不同，請修改 `clock_in.py` 中的 `SENDER_FILTERS`。你也可以轉寄一封 104 驗證碼信件看看實際的寄件者地址。

**Q: 驗證碼信件延遲？**
預設最多等 60 秒，每 5 秒檢查一次。可調整 `VERIFICATION_CODE_WAIT` 和 `VERIFICATION_CODE_POLL`。

**Q: 找不到帳號輸入框或打卡按鈕？**
檢查 `screenshots/` 裡的截圖確認頁面狀態，然後用 F12 DevTools 找到正確的 selector。

**Q: 雲端伺服器 IP 不在白名單？**
如果公司限制 IP 打卡，可能需要 VPN。
