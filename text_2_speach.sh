#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTS_DIR="$ROOT/tts"
VENV="$TTS_DIR/.venv"
INPUT_DIR="$ROOT/no-text-txt1"
OUTPUT_DIR="$ROOT/wav"
LOG_DIR="$ROOT/logs"
TMUX_SESSION="text2speach"

resolve_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    if command -v "$PYTHON" >/dev/null 2>&1; then
      echo "$PYTHON"
      return 0
    fi
    echo "エラー: PYTHON=$PYTHON が見つかりません。" >&2
    exit 1
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo python
    return 0
  fi
  echo "エラー: python3 / python が見つかりません。Python 3.10 以上をインストールしてください。" >&2
  exit 1
}

activate_venv() {
  if [[ -f "$VENV/bin/activate" ]]; then
    # Linux / macOS / WSL / Google Cloud Shell
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
    return 0
  fi
  if [[ -f "$VENV/Scripts/activate" ]]; then
    if [[ "$(uname -s)" == "Linux" ]]; then
      echo "警告: Windows 用の仮想環境を検出しました。WSL 用に再作成します..."
      rm -rf "$VENV"
      return 1
    fi
    # Git Bash / Windows
    # shellcheck disable=SC1091
    source "$VENV/Scripts/activate"
    return 0
  fi
  return 1
}

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
  PYTHON="$(resolve_python)"

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
    "$PYTHON" -m venv "$VENV"
  fi

  if ! activate_venv; then
    echo "仮想環境を作成します: $VENV"
    "$PYTHON" -m venv "$VENV"
    activate_venv || {
      echo "エラー: 仮想環境の activate スクリプトが見つかりません: $VENV" >&2
      exit 1
    }
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
