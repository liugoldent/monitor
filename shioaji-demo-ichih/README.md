# shioaji-demo

這個資料夾提供一個簡單的自動交易監控流程：

1. 使用 Telethon 監聽指定 Telegram bot 的訊息
2. 解析訊號內容中的 `多/空 X 口`
3. 呼叫 Shioaji API 進行台指期下單

程式進入監控後會持續執行，直到手動停止。

## 注意事項

這個專案目前是正式下單模式，不是模擬單。

`auto_trade.py` 內使用：

```python
api = sj.Shioaji(simulation=False)
```

因此只要訊號符合條件，就會真的登入永豐帳戶並送出委託。使用前請先確認帳號、憑證、策略與風險。

## 目錄內容

- `monitor_and_trade.py`：監聽 Telegram bot 訊號
- `auto_trade.py`：收到訊號後執行永豐期貨下單
- `.env`：Telegram 與 Shioaji 帳號設定

## 系統需求

- Python 3.10 以上
- 可用的永豐 Shioaji API 帳號
- 永豐 CA 憑證檔 `.pfx`
- Telegram 帳號

## 需要安裝的套件

這個資料夾直接使用到的第三方套件如下：

- `telethon`
- `shioaji`
- `requests`

建議安裝方式：

```bash
cd /Users/kt/Desktop/self/monitor/shioaji-demo
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

如果你已經有現成的虛擬環境，也可以直接安裝到既有環境中。

## 環境變數設定

請在 `shioaji-demo/.env` 內設定：

```env
# Telegram API
API_ID=你的_telegram_api_id
API_HASH=你的_telegram_api_hash

# 永豐期貨 API
API_KEY=你的_shioaji_api_key
SECRET_KEY=你的_shioaji_secret_key
PERSON_ID=你的身分證字號

# 可選：如果不填，程式會預設找同資料夾下的 Sinopac.pfx
CA_PATH=/完整路徑/Sinopac.pfx
```

## 憑證檔案

`auto_trade.py` 會依以下順序尋找憑證：

1. `.env` 中的 `CA_PATH`
2. `shioaji-demo/Sinopac.pfx`

如果兩者都沒有，程式收到交易訊號後會無法啟用 CA，也就不能下單。

## 啟動方式

在 `shioaji-demo` 目錄中執行：

```bash
cd /Users/kt/Desktop/self/monitor/shioaji-demo
source .venv/bin/activate
python monitor_and_trade.py
```

啟動後程式會長時間掛著，等待 Telegram 訊號。

第一次執行 Telethon 時，通常會要求你輸入：

- Telegram 手機號碼
- 驗證碼
- 如果帳號有開兩步驗證，還需要輸入密碼

成功後會建立本機 session 檔，後續通常不需要再次登入。

## 程式行為說明

`monitor_and_trade.py` 目前只處理：

- sender 是 bot
- bot username 是 `taiwan_mxf_bot`
- 訊息中包含 `訊號通知`
- 內容可解析出 `多/空 X 口`

符合條件時：

- `多` 會呼叫 `auto_trade("bull")`
- `空` 會呼叫 `auto_trade("bear")`

另外程式有簡單的防重複機制：

- 同方向訊號 10 秒內重複出現，會略過

## 執行前檢查清單

建議在正式執行前逐項確認：

- `.venv` 已建立且可正常啟動
- `telethon`、`shioaji`、`requests` 已安裝
- `.env` 內所有帳號欄位都已填入真實值
- `CA_PATH` 或 `Sinopac.pfx` 已準備完成
- Telegram 帳號可正常登入
- 永豐帳號可正常登入並啟用 CA
- 已確認這是正式交易帳號，不是模擬環境

## 常見問題

### 1. `source .venv/bin/activate` 找不到檔案

代表目前資料夾還沒有建立虛擬環境。先執行：

```bash
python3 -m venv .venv
```

### 2. 缺少 `Sinopac.pfx`

請把憑證放到：

```bash
/Users/kt/Desktop/self/monitor/shioaji-demo/Sinopac.pfx
```

或是在 `.env` 設定正確的 `CA_PATH`。

### 3. 程式啟動後沒反應

這是正常情況。它是監控程式，會持續等待 Telegram bot 訊號。只有收到符合條件的訊息才會進一步處理。

### 4. 第一次執行要求輸入 Telegram 驗證資訊

這也是正常情況。Telethon 需要先建立登入 session。

## 停止程式

在終端機按：

```bash
Ctrl + C
```

## 建議

正式使用前，建議先做以下確認：

- 先用小量測試
- 先確認 Telegram 訊號格式與解析結果一致
- 先確認永豐帳號、憑證與下單權限正常
- 如果要長時間執行，建議搭配 `screen`、`tmux` 或 process manager 使用
