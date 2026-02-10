# ============================================================
# 104 Pro Auto Clock-In - Docker Image
# ============================================================
#
# Build:
#   docker build -t 104-clockin .
#
# Run:
#   docker run --rm \
#     -e PRO104_ACCOUNT="your_account" \
#     -e PRO104_PASSWORD="your_password" \
#     104-clockin --action clock_in
#
# ============================================================

FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

WORKDIR /app

# Set timezone to Taipei
ENV TZ=Asia/Taipei
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy dependency file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium --with-deps

# Copy application code
COPY clock_in.py .

# Create log and screenshot directories
RUN mkdir -p logs screenshots

ENTRYPOINT ["python", "clock_in.py"]
CMD ["--action", "clock_in"]
