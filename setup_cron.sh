#!/bin/bash
# ============================================================
# 104 企業大師自動打卡 - Cron Job 設定腳本
# ============================================================
#
# 使用方式:
#   chmod +x setup_cron.sh
#   ./setup_cron.sh
#
# 此腳本會設定以下排程:
#   - 週一到週五 08:50 執行上班打卡
#   - 週一到週五 18:05 執行下班打卡
#
# 你可以在下方修改時間和其他設定
# ============================================================

# ---------- 請根據需求修改以下設定 ----------

# 上班打卡時間 (建議比實際上班時間早 10 分鐘)
CLOCK_IN_HOUR="8"
CLOCK_IN_MINUTE="50"

# 下班打卡時間 (建議比實際下班時間晚 5 分鐘)
CLOCK_OUT_HOUR="18"
CLOCK_OUT_MINUTE="5"

# 腳本的絕對路徑 (請修改為你的實際路徑)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/clock_in.py"

# Python 路徑 (如果使用虛擬環境，改成虛擬環境的 python 路徑)
PYTHON_PATH="$(which python3)"

# .env 檔案路徑
ENV_FILE="$SCRIPT_DIR/.env"

# ---------- 設定結束 ----------

echo "============================================"
echo "  104 企業大師自動打卡 - Cron Job 設定"
echo "============================================"
echo ""
echo "腳本路徑: $SCRIPT_PATH"
echo "Python 路徑: $PYTHON_PATH"
echo "上班打卡: 每週一至五 ${CLOCK_IN_HOUR}:${CLOCK_IN_MINUTE}"
echo "下班打卡: 每週一至五 ${CLOCK_OUT_HOUR}:${CLOCK_OUT_MINUTE}"
echo ""

# 檢查 .env 檔案
if [ ! -f "$ENV_FILE" ]; then
    echo "⚠️  找不到 .env 檔案！"
    echo "   請先複製 .env.example 為 .env 並填入帳號密碼:"
    echo "   cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    echo ""
fi

# 建立 wrapper 腳本 (載入環境變數)
WRAPPER_SCRIPT="$SCRIPT_DIR/run_clockin.sh"
cat > "$WRAPPER_SCRIPT" << 'WRAPPER_EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 載入環境變數
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

# 設定 PATH (確保可以找到 playwright 的瀏覽器)
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"

# 執行打卡腳本
PYTHON_PATH="__PYTHON_PATH__"
"$PYTHON_PATH" "$SCRIPT_DIR/clock_in.py" "$@" >> "$SCRIPT_DIR/logs/cron_$(date +%Y%m%d).log" 2>&1
WRAPPER_EOF

# 替換 Python 路徑
sed -i "s|__PYTHON_PATH__|$PYTHON_PATH|g" "$WRAPPER_SCRIPT"
chmod +x "$WRAPPER_SCRIPT"
echo "✅ 已建立 wrapper 腳本: $WRAPPER_SCRIPT"

# 建立 cron job
CRON_CLOCK_IN="${CLOCK_IN_MINUTE} ${CLOCK_IN_HOUR} * * 1-5 $WRAPPER_SCRIPT --action clock_in"
CRON_CLOCK_OUT="${CLOCK_OUT_MINUTE} ${CLOCK_OUT_HOUR} * * 1-5 $WRAPPER_SCRIPT --action clock_out"

# 備份現有 crontab
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d%H%M%S) 2>/dev/null

# 移除舊的 104 打卡 cron (如果有的話)
crontab -l 2>/dev/null | grep -v "clock_in.py" | grep -v "run_clockin.sh" > /tmp/crontab_new

# 加入新的 cron
echo "# === 104 企業大師自動打卡 ===" >> /tmp/crontab_new
echo "$CRON_CLOCK_IN" >> /tmp/crontab_new
echo "$CRON_CLOCK_OUT" >> /tmp/crontab_new

# 安裝新的 crontab
crontab /tmp/crontab_new
rm /tmp/crontab_new

echo "✅ Cron job 已設定完成！"
echo ""
echo "目前的 crontab 內容:"
echo "--------------------------------------------"
crontab -l
echo "--------------------------------------------"
echo ""
echo "📋 接下來請確認:"
echo "  1. 複製 .env.example 為 .env 並填入帳號密碼"
echo "  2. 手動測試一次: $WRAPPER_SCRIPT --action clock_in --no-delay"
echo "  3. 檢查 logs/ 資料夾查看執行日誌"
echo "  4. 檢查 screenshots/ 資料夾查看截圖"
