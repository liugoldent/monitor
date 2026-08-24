# 關掉docker
cd "C:\Users\USER\OneDrive\桌面\monitor"
docker compose down
到 Windows 右下角系統匣：
Docker 圖示 → 右鍵 → Quit Docker Desktop

# 重開
cd "C:\Users\USER\OneDrive\桌面\monitor"
.\run-windows-services.cmd

# win cloudfare
win-monitor.fintechinternational.org

# docker某服務重啟
cd "$HOME\OneDrive\桌面\monitor"
docker compose up -d --build webhook-server
docker compose ps webhook-server