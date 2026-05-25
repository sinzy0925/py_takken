# py_takken

宅地建物取引士試験の問題 PDF や Kindle スクリーンショットをテキスト化し、各問題の直後に正解を差し込むためのツール群です。

## 概要

| スクリプト | 役割 |
|-----------|------|
| `extract_pdf_text.py` | `pdf/` 内の PDF を文字起こしして `txt/` に保存 |
| `extract_image_text.py` | `no-text/` 内の見開き PNG を文字起こしして `no-text-txt/` に保存 |
| `delete_text.py` | `no-text-txt/` から不要行を除去して `no-text-txt1/` に保存 |
| `answer_pic.py` | 生成済み TXT の各【問】の直後に答えを挿入 |
| `slide_speaker-note.py` | `no-text-txt1/` の TXT を Google スライドのスピーカーノートに書き込む |
| `api_key_manager.py` | Gemini 用 API キーのローテーション管理 |

処理はすべて **直列** です（PDF もページも API 呼び出しも順番に実行）。

## ディレクトリ構成

```
py_takken/
├── pdf/                 # 入力 PDF（*.pdf）
├── txt/                 # 出力 TXT
├── no-text/             # 入力 PNG（Kindle 見開きスクリーンショット等）
├── no-text-txt/         # 出力 TXT（画像1枚につき1ファイル）
├── no-text-txt1/        # クリーニング後 TXT
├── delete_text.py
├── extract_pdf_text.py
├── extract_image_text.py
├── answer_pic.py
├── slide_speaker-note.py
├── secrets/             # サービスアカウント JSON（git 管理外）
├── api_key_manager.py
├── requirements.txt
├── .env.example
└── .env                 # 要作成（git 管理外）
```

## セットアップ

### 1. 仮想環境と依存関係

```powershell
cd py_takken
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. API キー（Gemini OCR を使う場合）

[Google AI Studio](https://aistudio.google.com/apikey) でキーを取得し、`.env` を作成します。

```powershell
copy .env.example .env
```

`.env` の例:

```env
GOOGLE_API_KEY_1=...
GOOGLE_API_KEY_2=...
# ... GOOGLE_API_KEY_10 まで
```

- 複数キーを `GOOGLE_API_KEY_1` ～ `GOOGLE_API_KEY_10` で登録すると、呼び出しごとに順番に切り替わります
- 単一キーのみの場合は `GEMINI_API_KEY` でも可（ローテーションなし）
- 最後に使ったキーは `.session_data.json` に保存され、次回実行時に続きから再開します

## 使い方

### ステップ 1: PDF → TXT

`pdf/` に PDF を置き、一括変換します。

```powershell
python extract_pdf_text.py --input pdf --output txt
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力ディレクトリ（既定: `pdf`） |
| `--output` | 出力ディレクトリ（既定: `txt`） |
| `--ocr` | 全ページを OCR |
| `--no-ocr` | テキスト層のみ（画像 PDF ではほぼ空） |
| `--ocr-backend gemini` | Gemini で OCR（既定） |
| `--ocr-backend easyocr` | ローカル EasyOCR（API 不要） |

**PDF の種類による動作**

- **テキスト埋め込み型**（例: 令和7年度など）  
  → PyMuPDF でそのまま抽出。OCR 不要。
- **スキャン画像型**（例: 平成28年度など）  
  → テキストが少ないページを Gemini で OCR。1 回の API 呼び出しに最大 4 ページまとめて送信。

**Gemini OCR 時の挙動（`extract_pdf_text.py` 先頭の定数）**

| 定数 | 既定値 | 内容 |
|------|--------|------|
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | 使用モデル |
| `OCR_GEMINI_PAGES_PER_REQUEST` | `4` | 1 リクエストあたりのページ数 |
| `OCR_GEMINI_INTERVAL_SECONDS` | `6` | 連続呼び出しの間隔 |
| `OCR_GEMINI_MAX_RETRIES` | `3` | 失敗時のリトライ回数 |
| `OCR_GEMINI_RETRY_DELAY_SECONDS` | `5` | リトライ前の待機秒数 |

エラー時は別 API キーへ切り替えて再試行します。

### ステップ 2: 答えを各問題の直後へ

```powershell
python answer_pic.py --input txt\H28-q_a.txt
```

| 処理内容 |
|----------|
| 末尾の正解番号表（`問　１` … `問５０` と答え）を読み取り |
| 各 `【問　N　】` ブロックの直後に `答え　４` 形式で挿入 |
| 正解番号表は **ファイル末尾にそのまま残す** |
| 本文中の `— 1 —` などページ区切り行を削除 |

**入力ファイルは上書き保存**されます。必要なら事前にバックアップを取ってください。

### ステップ 1b: 見開き PNG → TXT（Kindle 等）

`no-text/` に PNG を置き、一括文字起こしします。

```powershell
python extract_image_text.py --input no-text --output no-text-txt
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力ディレクトリ（既定: `no-text`） |
| `--output` | 出力ディレクトリ（既定: `no-text-txt`） |

**見開きの処理**

- 画像の幅/高さ比が **1.5 以上** のとき、中央で **左ページ・右ページ** に分割してから Gemini に送信します（1 API 呼び出しで2ページ分）
- 縦長の単ページ画像は分割せず、そのまま1枚として処理します
- 出力は `FireShot Capture 032 ....png` → `FireShot Capture 032 ....txt` のように **ファイル名1対1**
- API キーのローテーション・リトライ・呼び出し間隔は `extract_pdf_text.py` と同じ

出力 TXT の例（見開き）:

```
--- 左ページ ---

（左ページの本文）

--- 右ページ ---

（右ページの本文）
```

### ステップ 1c: 不要行の除去（Kindle TXT）

```powershell
python delete_text.py --input no-text-txt --output no-text-txt1
```

除去対象:

- `--- 左ページ ---` / `--- 右ページ ---`
- 数字のみの行（例: `14`, `360`）
- 書籍フッター（例: `第2章 権利関係 361`, `予想模擬試験 593`）

### ステップ 2: TXT → Google スライドのスピーカーノート

対象の Google スライドを、サービスアカウントのメールアドレスに **編集者** で共有しておきます。認証 JSON は `secrets/` に置きます（例: `secrets/getstockdata05-service-account.json`）。

```powershell
python slide_speaker-note.py --index 032-033
```

| オプション | 説明 |
|-----------|------|
| `--index` | Capture 番号の範囲（必須）。例: `032-033` → スライド1に032、スライド2に033 |
| `--url` | プレゼン URL。`slide=id.xxx` 付きなら **その枚目から** 書き込み（ブラウザで開いているスライドの URL をそのまま使える） |
| `--input` | TXT ディレクトリ（既定: `no-text-txt1`） |
| `--credentials` | サービスアカウント JSON（既定: `secrets/getstockdata05-service-account.json`） |
| `--slide-start` | 書き込み開始スライド番号（1始まり）。URL に `slide=` が無いときのみ（既定: 1） |

ファイル名は `FireShot Capture 032 - Kindle - [read.amazon.co.jp].txt` のように Capture 番号で対応付けます。

## 推奨ワークフロー

```powershell
# 1. PDF を pdf/ に配置
python extract_pdf_text.py

# 2. 各 TXT に答えを差し込み（例）
python answer_pic.py --input txt\H28-q_a.txt
python answer_pic.py --input txt\H29-q_a.txt
```

## 出力例（answer_pic 後）

```
【問　１】　次の記述のうち、民法の条文に規定されているものはどれか。
1　…
4　…


答え　４


【問　２】　制限行為能力者に関する…
…


答え　４

…

問　１
問　２
…
問５０
４
…
平成２８年度宅地建物取引士資格試験正解番号表
```

## 注意事項

- `.env`、`.session_data.json`、`secrets/` はリポジトリに含めないでください（`.gitignore` 済み）
- 画像 PDF の OCR は API 利用料・レート制限の対象です。キーを複数用意すると 429 などで止まりにくくなります
- OCR 精度は PDF の画質・モデルに依存します。誤認識は手修正が必要な場合があります
- `answer_pic.py` は末尾が「問ラベル10件 + 答え10件」×5 ブロック形式の正解番号表を前提としています

## ライセンス

[MIT License](LICENSE)
