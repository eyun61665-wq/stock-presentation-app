# 銘柄発表MVP

日本株の銘柄発表に必要な会社情報、PL、セグメント、KPI売上予測、カタリスト、発表メモを、銘柄プロジェクトごとにSQLiteへ保存するローカルWebアプリです。

## 起動方法（Windows）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

または `run_windows.bat` をダブルクリックします。通常は `http://127.0.0.1:8501` で開きます。

## 画面

- 銘柄概要：4桁コードからyfinanceの試作用無料データを取得し、手動修正値を保存
- PL：3期実績、会社予想、独自予想を分けて編集し、利益率を自動計算
- セグメント：年度・区分・セグメント別の売上と利益を保存し、全社売上との差額を確認
- KPI・売上予測：複数KPIの掛け算と複数売上項目の足し算で、弱気・標準・強気を試算
- カタリスト：背景からKPI、売上、PLへの流れと根拠を保存
- 発表メモ：発表原稿、投資仮説、リスク、想定質問などを保存

サイドバーでプロジェクトを選び、「閲覧」と「編集」を切り替えます。閲覧モードは表・グラフ中心、編集モードは入力・保存操作を表示します。

## PDFから入力候補を探す

PLまたはセグメントの編集画面で決算短信・IR資料PDFをアップロードします。

1. PyMuPDFでテキストを抽出
2. 関連キーワード周辺の数値、単位、年度、ページ、原文を候補表示
3. 採用する候補だけ選択
4. 編集表で確認・修正
5. 保存ボタンで確定

PDF抽出結果は、確認なしにSQLiteへ保存されません。画像だけのPDFはOCRせず、手動確認を案内します。

## データと外部機能

- データベース：`stock_projects.db`（Git管理対象外）
- 市場データ：yfinance（APIキー不要、取得保証なし）
- PDF解析：PyMuPDF（ローカル処理）
- J-Quants、EDINET、旧IR自動取得コード：`legacy_app.py`および既存モジュールに残していますが、現行MVPの必須機能ではありません
- OpenAI、Gemini、Notion AI：現行MVPでは使用しません

自動取得できない項目は「取得できませんでした」と表示し、手入力を続けられます。

## バックアップとマイグレーション

DB変更前のファイルは `backup/<日時>/` へ保存します。マイグレーションは列・テーブルの追加を基本とし、旧テーブルを削除しません。古い予想テーブルの構造変更が必要な場合も、旧表を `*_legacy_<日時>` としてDB内に残してからコピーします。

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

OneDriveの一時フォルダで権限エラーになる環境では、次のようにWindowsの一時フォルダを指定できます。

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp "$env:TEMP\stock_mvp_pytest"
```

## 主なファイル

- `app.py`：起動入口
- `mvp_ui.py`：6タブのStreamlit画面
- `database.py`：SQLiteと安全なマイグレーション
- `kpi_engine.py`：KPIシナリオ、売上、寄与、PL反映
- `market_data.py`：yfinance取得
- `pdf_extractor.py`：PDFテキストと候補抽出
- `exporter.py`：CSV、Excel、タブ区切り出力
- `legacy_app.py`：旧画面コードの保管

## 制約

- yfinanceは非公式データ源のため、欠損、遅延、取得制限があります。重要な数値は公式開示資料で確認してください。
- PDFの表構造や年度と数値の対応は会社ごとに異なるため、候補は必ず原文とページを確認してください。
- 画像PDFのOCR、高度なAI解析、ニュース収集、株価予測AIは実装していません。
