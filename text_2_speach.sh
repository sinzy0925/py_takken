#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTS_DIR="$ROOT/tts"
VENV="$TTS_DIR/.venv"
INPUT_DIR="$ROOT/no-text-txt1"
OUTPUT_DIR="$ROOT/wav"
LOG_DIR="$ROOT/logs"
TMUX_SESSION="text2speach"

show_log_help() {
  local log_file="${1:-}"
  if [[ -z "$log_file" && -f "$LOG_DIR/latest.log.path" ]]; then
    log_file="$(cat "$LOG_DIR/latest.log.path")"
  fi
  if [[ -z "$log_file" ]]; then
    log_file="$LOG_DIR/text_2_speach_*.log"
  fi
  cat <<EOF

=== ログの見方 ===

【tmux セッションに接続してリアルタイム表示】
  tmux attach -t ${TMUX_SESSION}
  （抜ける: Ctrl+b → d  ※処理はバックグラウンドで継続）

【tmux セッション一覧】
  tmux ls

【ログファイルをリアルタイム表示（別ターミナルから）】
  tail -f ${log_file}

【ログファイルを末尾から表示】
  tail -n 50 ${log_file}

【ログファイルをスクロール表示】
  less ${log_file}

【最新ログファイルのパスを確認】
  cat $LOG_DIR/latest.log.path

【実行中セッションを強制終了】
  tmux kill-session -t ${TMUX_SESSION}

EOF
}

setup_environment() {
  cd "$ROOT"

  if ! command -v python >/dev/null 2>&1; then
    echo "エラー: python が見つかりません。Python 3.10 以上をインストールしてください。" >&2
    exit 1
  fi

  if [[ ! -f "$TTS_DIR/main.py" ]]; then
    echo "エラー: $TTS_DIR/main.py が見つかりません。" >&2
    exit 1
  fi

  if [[ ! -f "$TTS_DIR/.env" ]]; then
    echo "エラー: $TTS_DIR/.env がありません。tts/.env.example をコピーして API キーを設定してください。" >&2
    exit 1
  fi

  if [[ ! -d "$INPUT_DIR" ]]; then
    echo "エラー: 入力ディレクトリ $INPUT_DIR がありません。" >&2
    exit 1
  fi

  if [[ ! -d "$VENV" ]]; then
    echo "仮想環境を作成します: $VENV"
    python -m venv "$VENV"
  fi

  if [[ -f "$VENV/Scripts/activate" ]]; then
    # Git Bash / Windows
    # shellcheck disable=SC1091
    source "$VENV/Scripts/activate"
  elif [[ -f "$VENV/bin/activate" ]]; then
    # Linux / macOS / Google Cloud Shell
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
  else
    echo "エラー: 仮想環境の activate スクリプトが見つかりません: $VENV" >&2
    exit 1
  fi

  echo "依存パッケージをインストールします..."
  python -m pip install --upgrade pip
  pip install -r "$TTS_DIR/requirements.txt"
}

run_tts_batch() {
  mkdir -p "$OUTPUT_DIR"
  echo "TTS バッチ処理を開始します..."
  python "$TTS_DIR/main.py" --input no-text-txt1 --output wav
}

# tmux 内のワーカー: セットアップ → バッチ実行 → ログの見方を表示
if [[ "${TEXT2SPEACH_WORKER:-}" == "1" ]]; then
  setup_environment
  run_tts_batch
  echo ""
  echo "=== TTS バッチ処理が完了しました ==="
  show_log_help "${LOG_FILE:-}"
  if [[ -n "${TMUX:-}" ]]; then
    echo "シェルを終了する: exit"
    exec bash -l
  fi
  exit 0
fi

# ランチャー: 事前チェック → tmux でバックグラウンド起動
setup_environment
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/text_2_speach_$(date +%Y%m%d_%H%M%S).log"
echo "$LOG_FILE" > "$LOG_DIR/latest.log.path"

if ! command -v tmux >/dev/null 2>&1; then
  echo "警告: tmux が見つかりません。フォアグラウンドで実行します。"
  run_tts_batch 2>&1 | tee -a "$LOG_FILE"
  echo ""
  echo "=== TTS バッチ処理が完了しました ==="
  show_log_help "$LOG_FILE"
  exit 0
fi

if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "tmux セッション '${TMUX_SESSION}' は既に実行中です。"
  if [[ -f "$LOG_DIR/latest.log.path" ]]; then
    LOG_FILE="$(cat "$LOG_DIR/latest.log.path")"
  fi
  show_log_help "$LOG_FILE"
  exit 0
fi

echo "tmux セッション '${TMUX_SESSION}' でバックグラウンド実行を開始します..."
echo "ログ: $LOG_FILE"

tmux new-session -d -s "$TMUX_SESSION" \
  "LOG_FILE='$LOG_FILE' TEXT2SPEACH_WORKER=1 bash '$ROOT/text_2_speach.sh' 2>&1 | tee -a '$LOG_FILE'"

echo ""
echo "バックグラウンド実行を開始しました。"
show_log_help "$LOG_FILE"
