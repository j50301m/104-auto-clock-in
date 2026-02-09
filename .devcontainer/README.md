# Development Container README

## 包含的功能

### Python 环境
- Python 3.11
- pip 包管理器
- 基于 Debian Bullseye

### Playwright 支持
- 自动安装 Playwright
- 包含 Chromium, Firefox, WebKit 浏览器
- 所需的系统依赖库

### VS Code 扩展
- Python 官方扩展
- Pylint 代码检查
- Black 代码格式化
- Jupyter Notebook 支持

### 开发工具
- Zsh shell 配置
- Oh My Zsh 框架
- 常用开发工具

## 使用方法

1. 确保已安装 Docker 和 VS Code Dev Containers 扩展
2. 在 VS Code 中打开项目
3. 按 `Cmd+Shift+P` (macOS) 或 `Ctrl+Shift+P` (Windows/Linux)
4. 选择 "Dev Containers: Reopen in Container"
5. 等待容器构建和配置完成

## 环境变量

记得在 .env 文件中设置必要的环境变量：

```env
PRO104_ACCOUNT=your_account
PRO104_PASSWORD=your_password
GMAIL_ADDRESS=your_gmail@gmail.com
GMAIL_APP_PASSWORD=your_app_password
RANDOM_DELAY_MIN=0
RANDOM_DELAY_MAX=300
```

## 测试

容器启动后，你可以测试 Playwright 是否正常工作：

```bash
python3 clock_in.py --test-gmail
python3 clock_in.py --action clock_in --skip-weekday-check
```