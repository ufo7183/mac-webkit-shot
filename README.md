# mac-webkit-shot

使用 GitHub Actions 的 macOS runner 與 Playwright WebKit，擷取喆美首頁在 macOS 字型環境中的畫面。

每次執行會產生：

- `mac-webkit-before.png`：正式網站目前畫面。
- `mac-webkit-after-12px.png`：只在測試頁暫時將三組大型中文標題上移 12px。
- `metrics.json`：執行環境與文字框量測資料。

測試不會修改正式網站。
