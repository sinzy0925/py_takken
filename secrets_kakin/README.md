# secrets_kakin — Cloud Text-to-Speech 用サービスアカウント

`tts/kakin_app.py`（WaveNet / 標準音声）専用の認証情報を置くフォルダです。  
**このフォルダ内の JSON は Git にコミットしないでください。**

## 1. GCP でサービスアカウントを作成

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを選択（または新規作成）
2. **課金を有効化**（無料枠利用でも課金アカウントの紐付けが必要です）
3. **API とサービス** → **ライブラリ** → **Cloud Text-to-Speech API** を **有効化**  
   （サービスアカウントの `project_id` と **同じプロジェクト** で有効にすること。未設定だと `SERVICE_DISABLED` / 403 になります）  
   直接リンク: https://console.cloud.google.com/apis/library/texttospeech.googleapis.com
4. **IAM と管理** → **サービスアカウント** → **サービスアカウントを作成**
   - 名前例: `tts-kakin`
5. 作成したアカウントの **キー** タブ → **鍵を追加** → **新しい鍵を作成** → **JSON** をダウンロード

## 2. JSON をこのフォルダに配置

推奨ファイル名（どちらか一方）:

```
secrets_kakin/service-account.json
```

または任意名（フォルダ内に **JSON が1つだけ** の場合は自動検出されます）:

```
secrets_kakin/my-project-tts.json
```

複数の JSON がある場合は、実行時に `--credentials` で指定してください。

## 3. 権限（IAM）

サービスアカウントに次のいずれかを付与します。

| ロール | 用途 |
|--------|------|
| `roles/cloudtts.user` | Text-to-Speech の利用（推奨） |
| `roles/editor` | 広い権限（開発用のみ） |

**IAM と管理** → サービスアカウント → 対象アカウント → **アクセス権を付与** から追加します。

## 4. 月次文字数上限（任意）

既定ではリポジトリルートの `.kakin_usage.json` に、合成成功ごとの文字数を追記します（上限既定 **100万文字/月**）。

```powershell
python tts/kakin_app.py --input no-text-txt1 --output mp3 --monthly-char-limit 1000000
```

合成 API の成功ごとに **5 秒待機**（既定）。変更する場合:

```powershell
python tts/kakin_app.py --input no-text-txt1 --output mp3 --api-delay-sec 5
```

待機なし: `--api-delay-sec 0`

上限に達すると API を呼ばずに停止します。記録だけ行い上限チェックを無効にする場合は `--monthly-char-limit 0` を指定してください。

## 5. 実行

リポジトリルート（`py_takken`）で:

```powershell
cd C:\Users\sinzy\py_takken
.\tts\.venv\Scripts\Activate.ps1   # 未作成なら python -m venv tts\.venv && pip install -r tts\requirements.txt
python tts/kakin_app.py --input no-text-txt1 --output mp3
```

標準音声に切り替える例:

```powershell
python tts/kakin_app.py --input no-text-txt1 --output mp3 --voice-type standard
```

音声名を明示する例:

```powershell
python tts/kakin_app.py --input no-text-txt1 --output mp3 --voice-name ja-JP-Wavenet-D
```

## 6. 認証パスの指定方法（優先順）

1. `--credentials path/to/key.json`
2. 環境変数 `GOOGLE_APPLICATION_CREDENTIALS` または `KAKIN_CREDENTIALS`
3. `secrets_kakin/service-account.json`
4. `secrets_kakin/` 内の `*.json` が **1ファイルのみ**

## 参考

- [Cloud Text-to-Speech 料金](https://cloud.google.com/text-to-speech/pricing?hl=ja)
- [対応音声一覧](https://cloud.google.com/text-to-speech/docs/voices?hl=ja)
