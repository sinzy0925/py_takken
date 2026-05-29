# py_takken

宅地建物取引士試験の問題 PDF や Kindle スクリーンショットをテキスト化し、正解の差し込み・スライドのスピーカーノート・音声化まで行うためのツール群です。

## スクリプト一覧

| スクリプト | 役割 |
|-----------|------|
| `split_spread_pages.py` | 見開き PNG を中央で左右分割し、外側の余白を除去 |
| `png_pdf.py` | 1 フォルダ内の PNG を **複数ページ** の PDF にまとめる |
| `png_pdf_split.py` | 番号付きサブフォルダごとに PNG を **縦連結** して PDF 化 |
| `extract_pdf_text.py` | `pdf/` 内の PDF を文字起こしして `txt/` に保存 |
| `extract_image_text.py` | `no-text/` 内の見開き PNG を文字起こしして `no-text-txt/` に保存 |
| `delete_text.py` | `no-text-txt/` から不要行を除去して `no-text-txt1/` に保存 |
| `answer_pic.py` | 各【問】の直後に正解番号表から答えを挿入 |
| `text2_speaker-note_sinzy0925.py` | TXT を Google スライドのスピーカーノートに書き込む（認証: `secrets/`） |
| `text2_speaker-note_ferrari.py` | 同上（既定の認証 JSON が異なる） |
| `wav_m4a.py` | `mp4` / `wav` を倍速・モノラル・m4a に変換 |
| `api_key_manager.py` | Gemini 用 API キーのローテーション（他スクリプトから利用） |
| `tts/main.py` | Gemini TTS でテキストを WAV 化 |
| `tts/kakin_app.py` | Google Cloud TTS（WaveNet/標準）で TXT を MP3 化 |

処理は基本的に **直列** です（PDF・画像・API 呼び出しを順番に実行）。

## ディレクトリ構成（例）

```
py_takken/
├── pdf/                      # 入力 PDF
├── txt/                      # PDF 文字起こし結果
├── no-text/                  # Kindle 見開きスクリーンショット（PNG）
│   └── 1章/
│       ├── FireShot Capture 032 ....png
│       └── split/            # split_spread_pages.py の出力例
│           ├── 1/            # 番号フォルダ（png_pdf_split 用）
│           └── 1.pdf
├── no-text-txt/              # 画像 OCR 結果
├── no-text-txt1/             # delete_text.py 後
├── mp4/ / wav/               # wav_m4a.py の入力
├── m4a/                      # wav_m4a.py の出力
├── secrets/                  # Google スライド用サービスアカウント（git 管理外）
├── secrets_kakin/            # Cloud TTS 用サービスアカウント（git 管理外）
├── requirements.txt          # ルートの Python 依存
├── tts/
│   ├── main.py
│   ├── kakin_app.py
│   ├── requirements.txt      # TTS 用の追加依存
│   └── README.md             # Gemini TTS の詳細
└── .env                      # API キー等（git 管理外）
```

## セットアップ

### 1. 仮想環境と依存関係

```powershell
cd py_takken
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Google スライド・Cloud TTS を使う場合は、それぞれ追加パッケージが必要です。

```powershell
# スピーカーノート書き込み（google-api-python-client は requirements.txt に含まれる）
# サービスアカウント JSON を secrets/ に配置

# Gemini TTS / Cloud TTS
pip install -r tts/requirements.txt
```

### 2. ffmpeg（音声変換）

`wav_m4a.py` には [ffmpeg](https://ffmpeg.org/) を PATH に入れてください。

### 3. API キー（Gemini OCR / Gemini TTS）

[Google AI Studio](https://aistudio.google.com/apikey) でキーを取得し、`.env` を作成します。

```powershell
copy .env.example .env
```

`.env` の例:

```env
GOOGLE_API_KEY_1=...
GOOGLE_API_KEY_2=...
# ... GOOGLE_API_KEY_10 まで

# 単一キーのみの場合（ローテーションなし）
# GEMINI_API_KEY=...
```

- 複数キーを `GOOGLE_API_KEY_1` ～ `GOOGLE_API_KEY_10` で登録すると、呼び出しごとに順番に切り替わります
- 最後に使ったキーは `.session_data.json` に保存され、次回実行時に続きから再開します
- 詳細は `api_key_manager.py` を参照

### 4. Google スライド（スピーカーノート）

対象プレゼンを、サービスアカウントのメールアドレスに **編集者** で共有します。認証 JSON は `secrets/` に置きます。

### 5. Cloud TTS（課金 API）

`tts/kakin_app.py` 用の手順は [secrets_kakin/README.md](secrets_kakin/README.md) を参照してください。

---

## 推奨ワークフロー（Kindle 見開き）

```powershell
# 1. 見開き PNG を左右分割＋余白除去
python split_spread_pages.py "no-text/1章" -o "no-text/1章/split"

# 2. （任意）番号フォルダごとに縦連結 PDF
python png_pdf_split.py --in "no-text/1章/split" --out "no-text/1章/split"

# 3. OCR（Gemini）
python extract_image_text.py --input no-text --output no-text-txt

# 4. 不要行除去
python delete_text.py --input no-text-txt --output no-text-txt1

# 5. スピーカーノートへ書き込み
python text2_speaker-note_sinzy0925.py --index 032-033

# 6. （任意）Cloud TTS で MP3 化
python tts/kakin_app.py --input no-text-txt1 --output mp3
```

PDF 過去問の場合:

```powershell
python extract_pdf_text.py --input pdf --output txt
python answer_pic.py --input txt\H28-q_a.txt
```

---

## 各ツールの使い方

### `split_spread_pages.py`

見開きスクリーンショットを **幅の中央** で左右に分割し、左半分は **左余白**、右半分は **右余白** を自動で切り落とします。

```powershell
python split_spread_pages.py "no-text/4模擬試験" -o "no-text/4模擬試験/split"
```

| 引数 | 説明 |
|------|------|
| `inputs` | 入力 PNG ファイル、または PNG を含むフォルダ（1 階層のみ `*.png`） |
| `-o`, `--output-dir` | 出力先（省略時は入力と同じフォルダ） |
| `--threshold` | 余白判定のしきい値 0–255（既定: `250`） |

- 出力: `{元ファイル名}_left.png`, `{元ファイル名}_right.png`
- 片方が真っ白（解答用紙の空白ページなど）のときは余白トリムをスキップし、警告を表示して処理を続行します

---

### `png_pdf.py`

**1 つのフォルダ** にある PNG を、Capture 番号順に並べて **1 つの PDF（複数ページ）** にします。

```powershell
python png_pdf.py --input no-text/1章/split --output no-text/1章/split/chapter1.pdf
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力ディレクトリ（既定: `no-text`） |
| `--output` | 出力 PDF のパス、または出力ディレクトリ（ディレクトリ指定時は `{入力フォルダ名}.pdf`） |

並び順: ファイル名の `Capture 123` の番号昇順。

---

### `png_pdf_split.py`

親フォルダ直下の **番号付きサブフォルダ**（`1/`, `2/`, …）ごとに、中の PNG を **上から縦に連結** した **1 ページ PDF** を作ります。

```powershell
python png_pdf_split.py --in "no-text/1章/split" --out "no-text/1章/split"
```

| オプション | 説明 |
|-----------|------|
| `--in` | サブフォルダを含む親ディレクトリ |
| `--out` | PDF の出力先（例: `1.pdf`, `2.pdf`） |

PNG の並び順は `png_pdf.py` と同じ（Capture 番号順）。幅の異なる画像は最大幅に合わせ、左右は白で中央寄せします。

---

### `extract_pdf_text.py`

`pdf/` 内の PDF を直列で文字起こしし、`txt/` に保存します。

```powershell
python extract_pdf_text.py --input pdf --output txt
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力ディレクトリ（既定: `pdf`） |
| `--output` | 出力ディレクトリ（既定: `txt`） |
| `--ocr` | 全ページを OCR |
| `--no-ocr` | テキスト層のみ（スキャン PDF ではほぼ空） |
| `--ocr-backend` | `gemini`（既定）または `easyocr`（API 不要） |

**PDF の種類による動作**

- **テキスト埋め込み型** … PyMuPDF で抽出。OCR 不要。
- **スキャン画像型** … テキストが少ないページを Gemini で OCR（1 リクエスト最大 4 ページ）。

スクリプト先頭の定数（`GEMINI_MODEL`, `OCR_GEMINI_PAGES_PER_REQUEST` など）で Gemini の挙動を調整できます。エラー時は別 API キーへ切り替えて再試行します。

---

### `extract_image_text.py`

`no-text/` 内の PNG を Gemini で文字起こしし、`no-text-txt/` に保存します（入力ディレクトリ直下の `*.png` のみ。再帰しません）。

```powershell
python extract_image_text.py --input no-text --output no-text-txt
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力ディレクトリ（既定: `no-text`） |
| `--output` | 出力ディレクトリ（既定: `no-text-txt`） |

**見開きの処理**

- 幅/高さ比が **1.5 以上** のとき、中央で左右に分割してから Gemini に送信（1 API 呼び出しで 2 ページ分）
- 縦長の単ページはそのまま 1 枚として処理
- 出力は PNG と **同名の `.txt`**（1 対 1）

見開きの出力例:

```
--- 左ページ ---

（左ページの本文）

--- 右ページ ---

（右ページの本文）
```

---

### `delete_text.py`

OCR テキストから不要行を除去し、別フォルダに保存します。

```powershell
python delete_text.py --input no-text-txt --output no-text-txt1
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力 TXT ディレクトリ（既定: `no-text-txt`） |
| `--output` | 出力 TXT ディレクトリ（既定: `no-text-txt1`） |

**除去・変換するもの**

- `--- 左ページ ---` / `--- 右ページ ---`（右ページマーカーは `----------` に置換）
- 数字のみの行（ページ番号など）
- 書籍フッター（例: `第2章 権利関係 361`, `予想模擬試験 593`）

**追加処理**

- 右ページ区切り `----------` の前後を、文の途中でさらに 4 分割する区切りを自動挿入（スピーカーノート用）

---

### `answer_pic.py`

末尾の正解番号表を読み取り、各 `【問　N　】` の直後に `答え　４` 形式で挿入します。**入力ファイルは上書き**されます。

```powershell
python answer_pic.py --input txt\H28-q_a.txt
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力 `.txt` ファイル（必須） |

| 処理内容 |
|----------|
| 末尾の正解番号表（`問　１` … `問５０` と答え）を解析 |
| 各問題ブロックの直後に答えを挿入 |
| 正解番号表はファイル末尾に残す |
| 本文中の `— 1 —` などページ区切り行を削除 |

事前にバックアップを取ってください。末尾が「問ラベル + 答え」×5 ブロック形式の正解番号表を前提としています。

---

### `text2_speaker-note_sinzy0925.py` / `text2_speaker-note_ferrari.py`

`no-text-txt1/` の TXT を Google スライドの **スピーカーノート** に書き込みます。2 ファイルは **既定のプレゼン URL と認証 JSON のみ異なり**、使い方は同じです。

```powershell
python text2_speaker-note_sinzy0925.py --index 032-033
python text2_speaker-note_ferrari.py --index 032 --url "https://docs.google.com/presentation/d/.../edit"
```

| オプション | 説明 |
|-----------|------|
| `--index` | Capture 番号（例: `032`）または範囲（例: `032-034`）**必須** |
| `--url` | プレゼン URL。`slide=id.xxx` 付きなら **その枚目から** 書き込み |
| `--input` | TXT ディレクトリ（既定: `no-text-txt1`） |
| `--credentials` | サービスアカウント JSON（sinzy: `secrets/getstockdata05-service-account.json` / ferrari: `secrets/ferrari01-service-account.json`） |
| `--slide-start` | 書き込み開始スライド番号（1 始まり）。URL に `slide=` が無いときのみ（既定: `1`） |

ファイル名は `FireShot Capture 032 - Kindle - [read.amazon.co.jp].txt` のように Capture 番号で対応付けます。書き込み前に **全スライドのスピーカーノートをクリア** します。

---

### `wav_m4a.py`

フォルダ内の `*.mp4` / `*.wav` を順番に **倍速・モノラル（既定）・m4a** に変換します。

```powershell
python wav_m4a.py --input mp4 --output m4a
python wav_m4a.py --input wav --output m4a --speed 1.3
```

| オプション | 説明 |
|-----------|------|
| `--input` | 入力ディレクトリ（既定: `mp4`） |
| `--output` | 出力ディレクトリ（既定: `m4a`） |
| `--speed` | 再生速度倍率（ffmpeg `atempo`。既定: `1.5`、範囲 0.5–2.0） |
| `--stereo` | 指定時はモノラル化しない（2ch のまま） |

`sample.mp4` → `m4a/sample.m4a`（拡張子のみ変更）。

---

### `api_key_manager.py`

CLI ではありません。`extract_pdf_text.py` / `extract_image_text.py` などから import され、`.env` の `GOOGLE_API_KEY_N` をローテーションします。

| 環境変数 | 説明 |
|---------|------|
| `GOOGLE_API_KEY_1` … | 番号付きキー |
| `GEMINI_API_KEY` | 単一キー（ローテーションなし） |
| `API_KEY_RANGE` | 使用する番号範囲（例: `1-10`） |
| `API_KEY_SESSION_FILE` | セッション保存先（既定: `.session_data.json`） |

---

### `tts/main.py`（Gemini TTS）

[Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation) でテキストを音声化します。詳細は [tts/README.md](tts/README.md) を参照。

```powershell
cd tts
pip install -r requirements.txt

# 単一ファイル（tts/prompt.txt）
python main.py -o speech.wav

# ディレクトリ内 TXT を一括 WAV 化
python main.py --input ../no-text-txt1 --output ../wav

# Web UI
python main.py --web
```

| オプション | 説明 |
|-----------|------|
| `-o`, `--output` | 出力 WAV パス、またはバッチ時の出力ディレクトリ |
| `--input` | 読み上げ元 TXT ディレクトリ（`--output` と併用） |
| `--web` | `http://127.0.0.1:8000` でブラウザからダウンロード |
| `--host` / `--port` | Web サーバのバインド先 |
| `-v` / `-q` | ログ詳細度 |

`.env` に `GEMINI_API_KEY`（または `GOOGLE_API_KEY`）が必要です。

---

### `tts/kakin_app.py`（Google Cloud TTS）

Gemini ではなく **Cloud Text-to-Speech**（WaveNet / 標準）で、TXT ディレクトリを MP3 に変換します。

```powershell
python tts/kakin_app.py --input no-text-txt1 --output mp3
```

| オプション | 説明 |
|-----------|------|
| `--input` | 読み上げ元 TXT ディレクトリ（必須） |
| `--output` | 出力 MP3 ディレクトリ（必須） |
| `--voice-type` | `wavenet` / `standard`（既定: `wavenet`） |
| `--voice-name` | 音声名（例: `ja-JP-Wavenet-D`）。未指定時は自動選択 |
| `--credentials` | サービスアカウント JSON（既定: `secrets_kakin/` 内を自動検出） |
| `--language` | 言語コード（既定: `ja-JP`） |
| `--speaking-rate` | 話速 0.25–4.0（既定: `1.0`） |
| `--monthly-char-limit` | 今月の合成文字数上限（`0` でチェックなし） |
| `--no-move-bup` | 処理後に TXT を `bup/` へ移動しない |
| `--no-usage-log` | 使用量ファイルへの記録を行わない |
| `-v` / `-q` | ログ詳細度 |

認証・GCP 設定は [secrets_kakin/README.md](secrets_kakin/README.md) を参照してください。

---

## 注意事項

- `.env`、`.session_data.json`、`secrets/`、`secrets_kakin/*.json` はリポジトリに含めないでください（`.gitignore` 済み）
- 画像 PDF / 見開き OCR は API 利用料・レート制限の対象です。キーを複数用意すると 429 などで止まりにくくなります
- OCR 精度は画質・モデルに依存します。誤認識は手修正が必要な場合があります
- `answer_pic.py` は入力ファイルを上書きします
- `extract_image_text.py` / `split_spread_pages.py` は入力フォルダの **直下のファイルのみ** 処理します（サブフォルダは再帰しません）

## ライセンス

[MIT License](LICENSE)
