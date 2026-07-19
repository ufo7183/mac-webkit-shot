"""native_capture：Mac Safari 原生長截圖引擎套件。

從 C1 已通過生死閘門的 spike（spikes/native_safari_spike.py）抽出並整理成正式模組，
API 與演算法邏輯與 spike 保持一致，不重寫另一套邏輯。

模組對應：
- url_validation：URL 安全驗證（GUI 端與 macOS 端共用）。
- safari_session：建立 webdriver.Safari() session 並驗證 capabilities。
- page_prepare：字體等待、viewport 校正、預捲穩定、動畫凍結、fixed/sticky 處理。
- stitcher：分段擷取與拼接成整頁長圖。
- metrics：metrics.json schema_version 1 產生器。
- preview_bundle：面板預覽 bundle（manifest.json + tiles/*.png）產生器。
- cli：命令列進入點，串接以上模組。
"""

__version__ = "0.1.0"
