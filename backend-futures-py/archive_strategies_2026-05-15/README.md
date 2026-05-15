# Archived strategies

這裡放的是已停用或研究用策略檔。

目前正式保留的策略有：

```text
monitor_and_trade.py -> auto_trade.py
webhook_server.py -> strategy_h_loss_guard.py
```

加碼減碼策略：

```text
H 訊號進場
平常 1 口
單口 MDD >= 1750 點後改 2 口
MDD 歸零後回 1 口
```

反向護欄策略：

```text
webhook_server.py 收到 1 分 webhook 後檢查 H 單是否看錯
若 H 單虧損擴大且短週期確認反向，第二帳號送出反向進出場 Discord 訊號
```

其他 TT/MXF、H follow、V2 研究策略仍維持封存。
