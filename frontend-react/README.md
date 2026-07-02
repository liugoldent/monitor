# React Futures Monitor

這是一個獨立的 React + Vite SPA，資料來源是 `../backend-futures-py/tv_doc/webhook_data_1min.csv`。

## Scripts

```bash
pnpm install
pnpm dev
pnpm build
```

開發模式會由 Vite middleware 直接讀取原始 CSV，網址是 `/data/webhook_data_1min.csv`。Production build 會把同一份 CSV 複製到 `dist/data/webhook_data_1min.csv`。
