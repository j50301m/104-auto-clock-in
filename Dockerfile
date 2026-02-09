# ============================================================
# 104 企業大師自動打卡 - Docker 映像檔
# ============================================================
#
# 建構方式:
#   docker build -t 104-clockin .
#
# 執行方式:
#   docker run --rm \
#     -e PRO104_ACCOUNT="your_account" \
#     -e PRO104_PASSWORD="your_password" \
#     104-clockin --action clock_in
#
# ============================================================

FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# 設定時區為台北
ENV TZ=Asia/Taipei
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 複製依賴檔案
COPY requirements.txt .

# 安裝 Python 依賴
RUN pip install --no-cache-dir -r requirements.txt

# 安裝 Playwright 瀏覽器
RUN playwright install chromium --with-deps

# 複製應用程式碼
COPY clock_in.py .

# 建立日誌和截圖目錄
RUN mkdir -p logs screenshots

ENTRYPOINT ["python", "clock_in.py"]
CMD ["--action", "clock_in"]
