import os
import time
import json
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import fcntl

from .models import Session
from .exceptions import (
    LockTimeoutError,
    ConcurrentPersistError,
    CorruptedSessionError,
    IOError,
    VersionMismatchError,
)
from .constants import (
    LOCK_TIMEOUT_SECONDS,
    LOCK_ACQUIRE_RETRY_INTERVAL,
    TMP_FILE_SUFFIX,
    LOCK_FILE_SUFFIX,
    SESSION_VERSION,
    ERROR_PREFIX,
    ERROR_SUFFIX,
)


class SessionLock:
    def __init__(
        self,
        lock_path: str,
        timeout: float = LOCK_TIMEOUT_SECONDS,
        retry_interval: float = LOCK_ACQUIRE_RETRY_INTERVAL,
    ):
        self.lock_path = lock_path
        self.timeout = timeout
        self.retry_interval = retry_interval
        self._lock_file: Optional[int] = None
        self._acquired = False

    def acquire(self) -> bool:
        if self._acquired:
            return True

        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)

        start_time = time.time()
        while True:
            try:
                self._lock_file = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._acquired = True
                return True
            except (OSError, IOError):
                if self._lock_file is not None:
                    os.close(self._lock_file)
                    self._lock_file = None

                elapsed = time.time() - start_time
                if elapsed >= self.timeout:
                    raise LockTimeoutError(f"Timeout after {self.timeout}s waiting for lock: {self.lock_path}")

                time.sleep(self.retry_interval)

    def release(self):
        if self._acquired and self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                os.close(self._lock_file)
            except Exception:
                pass
            finally:
                self._lock_file = None
                self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class SessionPersistence:
    def __init__(
        self,
        persist_dir: str,
        lock_timeout: float = LOCK_TIMEOUT_SECONDS,
        expected_version: str = SESSION_VERSION,
    ):
        self.persist_dir = os.path.abspath(persist_dir)
        self.lock_timeout = lock_timeout
        self.expected_version = expected_version
        os.makedirs(self.persist_dir, exist_ok=True)

    def _session_path(self, session_id: str) -> str:
        safe_id = self._safe_filename(session_id)
        return os.path.join(self.persist_dir, f"{safe_id}.json")

    def _lock_path(self, session_id: str) -> str:
        safe_id = self._safe_filename(session_id)
        return os.path.join(self.persist_dir, f"{safe_id}{LOCK_FILE_SUFFIX}")

    def _tmp_path(self, session_id: str) -> str:
        safe_id = self._safe_filename(session_id)
        return os.path.join(self.persist_dir, f"{safe_id}{TMP_FILE_SUFFIX}")

    @staticmethod
    def _safe_filename(name: str) -> str:
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]

    def _validate_json_structure(self, data: Dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            return False
        if "session_id" not in data:
            return False
        if "warm_memories" not in data or not isinstance(data["warm_memories"], list):
            return False
        if "cold_memories" not in data or not isinstance(data["cold_memories"], list):
            return False
        return True

    def _check_for_tmp_or_dirty(self, session_id: str) -> bool:
        tmp_path = self._tmp_path(session_id)
        return os.path.exists(tmp_path)

    def _session_exists(self, session_id: str) -> bool:
        session_path = self._session_path(session_id)
        return os.path.exists(session_path)

    def save(self, session: Session, force_overwrite: bool = False) -> bool:
        session_id = session.session_id
        session_path = self._session_path(session_id)
        tmp_path = self._tmp_path(session_id)
        lock_path = self._lock_path(session_id)

        with SessionLock(lock_path, timeout=self.lock_timeout):
            if self._check_for_tmp_or_dirty(session_id):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            if not force_overwrite and self._session_exists(session_id):
                try:
                    existing_data = self._load_without_lock(session_id)
                    existing_updated = existing_data.get("updated_at", "")
                    session_updated = session.updated_at.isoformat() if session.updated_at else ""

                    if existing_updated and session_updated and existing_updated > session_updated:
                        raise ConcurrentPersistError(
                            f"Session {session_id} was modified by another process, existing is newer"
                        )
                except CorruptedSessionError:
                    pass

            session.updated_at = datetime.now(timezone.utc)
            json_content = session.to_json(indent=2)

            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(json_content)
                    f.flush()
                    os.fsync(f.fileno())

                if os.path.exists(session_path):
                    old_path = session_path + ".old"
                    if os.path.exists(old_path):
                        os.remove(old_path)
                    os.rename(session_path, old_path)

                os.rename(tmp_path, session_path)

                return True

            except Exception as e:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                raise IOError(f"Failed to save session {session_id}: {str(e)}") from e

    def _load_without_lock(self, session_id: str) -> Dict[str, Any]:
        session_path = self._session_path(session_id)

        if self._check_for_tmp_or_dirty(session_id):
            raise CorruptedSessionError(
                f"Dirty state detected for session {session_id}: temporary file exists"
            )

        if not os.path.exists(session_path):
            raise FileNotFoundError(f"Session file not found: {session_path}")

        try:
            with open(session_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            raise IOError(f"Failed to read session file: {str(e)}") from e

        if not content.strip():
            raise CorruptedSessionError(f"Session file is empty: {session_path}")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise CorruptedSessionError(f"Invalid JSON in session file: {str(e)}") from e

        if not self._validate_json_structure(data):
            raise CorruptedSessionError(
                f"Session data structure is incomplete or invalid: missing required fields"
            )

        return data

    def load(self, session_id: str, check_version: bool = True) -> Session:
        session_path = self._session_path(session_id)
        lock_path = self._lock_path(session_id)

        with SessionLock(lock_path, timeout=self.lock_timeout):
            data = self._load_without_lock(session_id)

            if check_version:
                file_version = data.get("version", "0.9")
                if file_version != self.expected_version:
                    raise VersionMismatchError(
                        f"Session version {file_version} does not match expected {self.expected_version}"
                    )

            return Session.from_dict(data)

    def load_legacy(self, session_id: str) -> Session:
        session_path = self._session_path(session_id)
        lock_path = self._lock_path(session_id)

        with SessionLock(lock_path, timeout=self.lock_timeout):
            data = self._load_without_lock(session_id)
            session = Session.from_dict(data)
            session.version = self.expected_version
            return session

    def exists(self, session_id: str) -> bool:
        return self._session_exists(session_id)

    def delete(self, session_id: str) -> bool:
        session_path = self._session_path(session_id)
        lock_path = self._lock_path(session_id)
        tmp_path = self._tmp_path(session_id)

        with SessionLock(lock_path, timeout=self.lock_timeout):
            deleted = False
            if os.path.exists(session_path):
                os.remove(session_path)
                deleted = True
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return deleted

    def upgrade_session(self, session_id: str) -> bool:
        session = self.load_legacy(session_id)
        return self.save(session, force_overwrite=True)
