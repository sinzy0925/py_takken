import os
import json
import asyncio
import threading
from dotenv import load_dotenv
import inspect  # --- ▼▼▼ 修正ポイント1: inspectモジュールをインポート ▼▼▼ ---
import re

# .envファイルから環境変数を読み込む（空の環境変数を上書き）
_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=_ENV_PATH, override=True)

# セッションファイル（最後に使ったキーのインデックスを保存する場所）
SESSION_FILE = os.getenv("API_KEY_SESSION_FILE", os.path.join(os.getcwd(), '.session_data.json'))

class ApiKeyManager:
    """
    複数のAPIキーを管理し、安全なローテーション、セッションの永続化、
    および高負荷な並列処理下でのレースコンディションを回避するシステム。
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(ApiKeyManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self._api_keys: list[str] = []
        self._current_index: int = -1
        self._key_selection_lock = threading.Lock()
        
        self._load_api_keys_from_env()
        self._load_session()
        
        print(f"[{self.__class__.__name__}] 初期化完了。{len(self._api_keys)}個のキーをロードしました。")

    def _load_api_keys_from_env(self):
        keys: list[str] = []

        numbered: list[tuple[int, str]] = []
        for name, value in os.environ.items():
            if not name.startswith("GOOGLE_API_KEY_"):
                continue
            suffix = name.replace("GOOGLE_API_KEY_", "", 1)
            if suffix.isdigit() and value:
                numbered.append((int(suffix), value))

        range_text = os.getenv("API_KEY_RANGE", "").strip()
        if not range_text:
            term_text = os.getenv("API_KEY_TERM", "").strip()
            if term_text.isdigit():
                term_value = int(term_text)
                range_text = f"{term_value * 10}-{term_value * 10 + 9}"

        if range_text:
            match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", range_text)
            if match:
                start = int(match.group(1))
                end = int(match.group(2))
                if start <= end:
                    numbered = [(idx, val) for idx, val in numbered if start <= idx <= end]
                    print(f"[{self.__class__.__name__}] APIキー範囲を適用: {start}-{end}")
                else:
                    print(f"警告: API_KEY_RANGE の範囲が不正です: {range_text}")
            else:
                print(f"警告: API_KEY_RANGE の形式が不正です: {range_text}")

        for _, value in sorted(numbered):
            if value not in keys:
                keys.append(value)

        self._api_keys = keys
        if not self._api_keys:
            print("警告: 有効なAPIキーが.envファイルに設定されていません。")

    def _load_session(self):
        try:
            if os.path.exists(SESSION_FILE):
                with open(SESSION_FILE, 'r') as f:
                    data = json.load(f)
                    last_index = data.get('lastKeyIndex', -1)
                    if 0 <= last_index < len(self._api_keys):
                        self._current_index = last_index
                        print(f"[{self.__class__.__name__}] セッションをロードしました。次のキーインデックスは { (last_index + 1) % len(self._api_keys) } から開始します。")
                    else:
                        self._current_index = -1
        except (IOError, json.JSONDecodeError) as e:
            print(f"セッションファイルの読み込み中にエラーが発生しました: {e}")
            self._current_index = -1

    def save_session(self):
        if not self._api_keys:
            return
        try:
            with open(SESSION_FILE, 'w') as f:
                json.dump({'lastKeyIndex': self._current_index}, f)
        except IOError as e:
            print(f"セッションファイルの保存に失敗しました: {e}")

    def _build_caller_info(self, depth: int = 2) -> str:
        try:
            caller_frame = inspect.stack()[depth]
            return f"From: {os.path.basename(caller_frame.filename)}:{caller_frame.lineno}"
        except Exception:
            return "呼び出し元: 不明"

    def _select_next_key(self, caller_info: str) -> str | None:
        if not self._api_keys:
            print("エラー: 利用可能なAPIキーがありません。")
            return None
        with self._key_selection_lock:
            self._current_index = (self._current_index + 1) % len(self._api_keys)
            selected_key = self._api_keys[self._current_index]
            print(
                f"[{self.__class__.__name__}] APIkey: idx: {self._current_index}, key: {selected_key[-4:]} [{caller_info}]"
            )
            return selected_key

    async def get_next_key(self) -> str | None:
        """
        次の利用可能なAPIキーを、安全な排他制御付きで取得する。
        """
        caller_info = self._build_caller_info()
        return self._select_next_key(caller_info)

    def get_next_key_sync(self) -> str | None:
        caller_info = self._build_caller_info()
        return self._select_next_key(caller_info)

    @property
    def key_count(self) -> int:
        return len(self._api_keys)

    @property
    def last_used_key_info(self) -> dict:
        if self._current_index == -1 or not self._api_keys:
            return {
                "key_snippet": "N/A",
                "index": -1,
                "total": len(self._api_keys)
            }
        
        key = self._api_keys[self._current_index]
        return {
            "key_snippet": key[-4:],
            "index": self._current_index,
            "total": len(self._api_keys)
        }

api_key_manager = ApiKeyManager()