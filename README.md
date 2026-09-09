# mac-webkit-shot

這個 repository 有兩條彼此分開的截圖流程：既有的 Playwright WebKit 比較流程，以及正式的原生 macOS Safari 流程。

## 原生 Safari 流程

`.github/workflows/native-safari.yml` 目前可由 GitHub Actions 手動啟動。它固定使用 `macos-15`，以 `python -m native_capture.cli` 建立原生 Safari WebDriver session，並把網址只透過環境變數交給 Python。每次執行都需要一個新的 UUID `request_id`。

成功 artifact `safari-capture-<request_id>` 會包含：

- `safari-full-page.png`：未壓縮的整頁長圖。
- `safari-first-viewport.png`：第一個真實 Safari viewport。
- `metrics.json`：Safari、macOS、CPU 架構、SafariDriver、Python、DPR、viewport、字型與分段覆蓋資料。
- `versions.txt`：runner 實測版本。
- `preview/manifest.json` 與 `preview/tiles/*.png`：面板預覽 bundle。

成功與失敗都會上傳同一個 UUID artifact；失敗時另保留 `diagnostics/` 與可取得的 SafariDriver log，artifact 保留 7 天。

一個明確的手動流程如下。`HEAD_SHA` 必須是本次已核對、準備執行的遠端 commit；不要從 dispatch 指令的輸出猜 run ID，也不要用 latest run 代替 UUID 查找。

```text
gh workflow run native-safari.yml --repo ufo7183/mac-webkit-shot --ref codex/mac-webkit-shot --field url=https://example.com/ --field request_id=<uuid> --field viewport_width=1136 --field viewport_height=1129 --field settle_ms=2000
gh run list --repo ufo7183/mac-webkit-shot --workflow native-safari.yml --branch codex/mac-webkit-shot --commit <HEAD_SHA> --event workflow_dispatch --json databaseId,displayTitle,headSha,status,conclusion
gh run view <RUN_ID> --repo ufo7183/mac-webkit-shot --json databaseId,headSha,status,conclusion,url
gh run download <RUN_ID> --repo ufo7183/mac-webkit-shot --name safari-capture-<uuid> --dir <output-directory>
```

下載後要以 `request_id`、run 的 `headSha`、artifact 名稱和 `metrics.json` 互相核對，才能把產物交給本機檢視。

## 既有 WebKit 比較流程

既有 `.github/workflows/mac-screenshot.yml` 與 `npm run shot` 使用 Playwright WebKit，產生：

- `mac-webkit-before.png`：正式網站目前畫面。
- `mac-webkit-after-12px.png`：只在測試頁暫時將三組大型中文標題上移 12px。
- `metrics.json`：執行環境與文字框量測資料。

這條流程只作歷史比較，不能作為原生 Safari 驗收證據；測試不會修改正式網站。

## GUI 狀態

Windows GUI、面板預覽的互動載入、EXE 與桌面捷徑屬於後續 C4／C5，尚未在這個 repository 宣稱存在。現階段原生 Safari 的可用交付方式是 GitHub Actions artifact 下載後的本機檢視。
