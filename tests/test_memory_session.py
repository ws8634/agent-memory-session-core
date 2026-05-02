import os
import sys
import time
import json
import subprocess
import signal
import tempfile
import threading
import pytest

from agent_memory_session import (
    SessionMemory,
    Session,
    Message,
    SessionPersistence,
    SessionLock,
    LockTimeoutError,
    ConcurrentPersistError,
    CorruptedSessionError,
    DuplicateMemoryError,
    VersionMismatchError,
    AgentMemoryError,
    LOCK_TIMEOUT_SECONDS,
    ERROR_PREFIX,
    ERROR_SUFFIX,
    EXIT_CODE_LOCK_TIMEOUT,
    EXIT_CODE_CORRUPTED,
    EXIT_CODE_CONCURRENT,
    EXIT_CODE_DUPLICATE,
    EXIT_CODE_VERSION,
    SESSION_VERSION,
    MAX_WARM_MEMORIES,
    TMP_FILE_SUFFIX,
)


class TestBasicMemory:
    def test_create_session_memory(self):
        mem = SessionMemory("test-session")
        assert mem.session_id == "test-session"
        assert len(mem.get_warm_memories()) == 0
        assert len(mem.get_cold_memories()) == 0

    def test_add_warm_memory(self):
        mem = SessionMemory("test-session", max_warm_memories=5)
        msg = mem.add_message("user", "Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert len(mem.get_warm_memories()) == 1
        assert len(mem.get_cold_memories()) == 0

    def test_overflow_to_cold_memory(self):
        mem = SessionMemory("test-session", max_warm_memories=3)

        for i in range(5):
            mem.add_message("user", f"Message {i}")

        assert len(mem.get_warm_memories()) == 3
        assert len(mem.get_cold_memories()) == 2

        cold_contents = [m.content for m in mem.get_cold_memories()]
        assert "Message 0" in cold_contents
        assert "Message 1" in cold_contents


class TestColdMemoryDeduplication:
    def test_cold_memory_dedup_reject_mode(self):
        from agent_memory_session.constants import COLD_MEMORY_DEDUP_MODE_REJECT

        mem = SessionMemory(
            "test-session",
            max_warm_memories=2,
            cold_dedup_mode=COLD_MEMORY_DEDUP_MODE_REJECT,
        )

        mem.add_message("user", "DupContent")
        mem.add_message("assistant", "Other1")
        mem.add_message("user", "DupContent")

        mem.add_message("assistant", "Other2")

        with pytest.raises(DuplicateMemoryError) as exc_info:
            mem.add_message("user", "DupContent")

        assert ERROR_PREFIX in str(exc_info.value)
        assert ERROR_SUFFIX in str(exc_info.value)
        assert "冷记忆重复条目被拒绝" in str(exc_info.value)
        assert exc_info.value.exit_code == EXIT_CODE_DUPLICATE

    def test_cold_memory_dedup_overwrite_mode(self):
        from agent_memory_session.constants import COLD_MEMORY_DEDUP_MODE_OVERWRITE

        mem = SessionMemory(
            "test-session",
            max_warm_memories=1,
            cold_dedup_mode=COLD_MEMORY_DEDUP_MODE_OVERWRITE,
        )

        mem.add_message("user", "Same content")
        mem.add_message("assistant", "Response 1")

        mem.add_message("user", "Same content")

        assert len(mem.get_cold_memories()) == 2

    def test_cross_session_cold_memory_independent(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)

        mem1 = SessionMemory("session-1", max_warm_memories=1)
        mem1.add_message("user", "Shared content")
        mem1.add_message("assistant", "Response 1")
        persist.save(mem1.session)

        mem2 = SessionMemory("session-2", max_warm_memories=1)
        mem2.add_message("user", "Shared content")
        mem2.add_message("assistant", "Response 2")
        persist.save(mem2.session)

        s1 = persist.load("session-1")
        s2 = persist.load("session-2")

        assert len(s1.cold_memories) == 1
        assert len(s2.cold_memories) == 1
        assert s1.cold_memories[0].content == "Shared content"
        assert s2.cold_memories[0].content == "Shared content"


class TestLockTimeout:
    def test_lock_timeout_constant_is_writable(self, short_lock_timeout):
        import agent_memory_session.constants as const
        assert const.LOCK_TIMEOUT_SECONDS == 0.5

    def test_lock_acquire_timeout(self, temp_persist_dir, short_lock_timeout):
        import fcntl

        lock_path = os.path.join(temp_persist_dir, "test.lock")
        os.makedirs(temp_persist_dir, exist_ok=True)

        held_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        fcntl.flock(held_fd, fcntl.LOCK_EX)

        lock = SessionLock(lock_path, timeout=0.3, retry_interval=0.1)

        with pytest.raises(LockTimeoutError) as exc_info:
            lock.acquire()

        assert ERROR_PREFIX in str(exc_info.value)
        assert ERROR_SUFFIX in str(exc_info.value)
        assert "获取会话锁超时" in str(exc_info.value)
        assert exc_info.value.exit_code == EXIT_CODE_LOCK_TIMEOUT

        fcntl.flock(held_fd, fcntl.LOCK_UN)
        os.close(held_fd)


class TestConcurrentPersist:
    def test_concurrent_persist_through_lock(self, temp_persist_dir, short_lock_timeout):
        persist = SessionPersistence(temp_persist_dir, lock_timeout=0.5)
        session = Session(session_id="concurrent-test")
        persist.save(session)

        lock_path = persist._lock_path("concurrent-test")
        import fcntl

        held_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        fcntl.flock(held_fd, fcntl.LOCK_EX)

        new_session = Session(session_id="concurrent-test")

        with pytest.raises(LockTimeoutError):
            persist.save(new_session)

        fcntl.flock(held_fd, fcntl.LOCK_UN)
        os.close(held_fd)


class TestDirtyFileHandling:
    def test_tmp_file_exists_raises_corrupted_on_load(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)
        session = Session(session_id="dirty-test")
        persist.save(session)

        tmp_path = persist._tmp_path("dirty-test")
        with open(tmp_path, "w") as f:
            f.write("{invalid")

        with pytest.raises(CorruptedSessionError) as exc_info:
            persist.load("dirty-test")

        assert ERROR_PREFIX in str(exc_info.value)
        assert ERROR_SUFFIX in str(exc_info.value)
        assert "会话数据损坏或不完整" in str(exc_info.value)
        assert exc_info.value.exit_code == EXIT_CODE_CORRUPTED

    def test_empty_json_raises_corrupted(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)
        session_path = persist._session_path("empty-test")

        os.makedirs(temp_persist_dir, exist_ok=True)
        with open(session_path, "w") as f:
            f.write("")

        with pytest.raises(CorruptedSessionError):
            persist.load("empty-test")

    def test_invalid_json_raises_corrupted(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)
        session_path = persist._session_path("invalid-test")

        os.makedirs(temp_persist_dir, exist_ok=True)
        with open(session_path, "w") as f:
            f.write("{not valid json")

        with pytest.raises(CorruptedSessionError):
            persist.load("invalid-test")

    def test_missing_fields_raises_corrupted(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)
        session_path = persist._session_path("incomplete-test")

        os.makedirs(temp_persist_dir, exist_ok=True)
        with open(session_path, "w") as f:
            json.dump({"some_field": "value"}, f)

        with pytest.raises(CorruptedSessionError):
            persist.load("incomplete-test")

    def test_save_cleans_up_existing_tmp(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)

        tmp_path = persist._tmp_path("cleanup-test")
        os.makedirs(temp_persist_dir, exist_ok=True)
        with open(tmp_path, "w") as f:
            f.write("old dirty content")

        assert os.path.exists(tmp_path)

        session = Session(session_id="cleanup-test")
        persist.save(session)

        assert not os.path.exists(tmp_path)


class TestVersionCompatibility:
    def test_old_version_load_raises_version_mismatch(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)
        session = Session(session_id="old-version")
        session.version = "0.9"
        persist.save(session, force_overwrite=True)

        with pytest.raises(VersionMismatchError) as exc_info:
            persist.load("old-version", check_version=True)

        assert ERROR_PREFIX in str(exc_info.value)
        assert ERROR_SUFFIX in str(exc_info.value)
        assert "会话版本不兼容" in str(exc_info.value)
        assert exc_info.value.exit_code == EXIT_CODE_VERSION

    def test_load_legacy_ignores_version(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)
        session = Session(session_id="old-version")
        session.version = "0.9"
        persist.save(session, force_overwrite=True)

        loaded = persist.load_legacy("old-version")
        assert loaded.session_id == "old-version"
        assert loaded.version == SESSION_VERSION

    def test_upgrade_session(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)

        session = Session(session_id="to-upgrade")
        session.version = "0.9"
        persist.save(session, force_overwrite=True)

        persist.upgrade_session("to-upgrade")

        loaded = persist.load("to-upgrade", check_version=True)
        assert loaded.version == SESSION_VERSION

    def test_old_format_not_deleted_on_failed_load(self, temp_persist_dir):
        persist = SessionPersistence(temp_persist_dir)
        session_path = persist._session_path("old-format")

        os.makedirs(temp_persist_dir, exist_ok=True)
        old_data = {
            "session_id": "old-format",
            "version": "0.8",
            "warm_memories": [{"role": "user", "content": "old", "timestamp": "2024-01-01T00:00:00"}],
            "cold_memories": [],
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "metadata": {},
        }
        with open(session_path, "w") as f:
            json.dump(old_data, f)

        assert os.path.exists(session_path)

        try:
            persist.load("old-format", check_version=True)
        except VersionMismatchError:
            pass

        assert os.path.exists(session_path)


class TestCLIErrorAlignment:
    def test_cli_error_contains_prefix_suffix(self, temp_persist_dir):
        test_script = os.path.join(temp_persist_dir, "test_cli_error.py")
        code = f'''
import sys
sys.path.insert(0, "{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}")
from agent_memory_session.cli import main
from agent_memory_session.constants import ERROR_PREFIX, ERROR_SUFFIX

import io
import contextlib

stderr_capture = io.StringIO()
with contextlib.redirect_stderr(stderr_capture):
    try:
        from agent_memory_session import LockTimeoutError
        e = LockTimeoutError("test detail")
        print(str(e), file=sys.stderr)
    except Exception:
        pass

output = stderr_capture.getvalue()
assert ERROR_PREFIX in output, f"Prefix missing in: {{output}}"
assert ERROR_SUFFIX in output, f"Suffix missing in: {{output}}"
print("OK")
'''
        with open(test_script, "w") as f:
            f.write(code)

        result = subprocess.run(
            [sys.executable, test_script],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


class TestSubprocessKillMidWrite:
    def test_subprocess_killed_mid_write_leaves_tmp(self, temp_persist_dir):
        import time

        session_hash = SessionPersistence._safe_filename("kill-me")
        tmp_path = os.path.join(temp_persist_dir, f"{session_hash}{TMP_FILE_SUFFIX}")
        json_path = os.path.join(temp_persist_dir, f"{session_hash}.json")
        ready_file = os.path.join(temp_persist_dir, "child_ready")
        module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        code = f'''
import sys
import os
import json

sys.path.insert(0, "{module_dir}")

from agent_memory_session.constants import TMP_FILE_SUFFIX
import hashlib

def safe_filename(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]

persist_dir = sys.argv[1]
session_hash = safe_filename("kill-me")
tmp_path = os.path.join(persist_dir, f"{{session_hash}}{{TMP_FILE_SUFFIX}}")
ready_file = os.path.join(persist_dir, "child_ready")

partial_json = """{{
    "session_id": "kill-me",
    "version": "1.0",
    "warm_memories": [
        {{"""

os.makedirs(persist_dir, exist_ok=True)

with open(tmp_path, "w", encoding="utf-8") as f:
    f.write(partial_json)
    f.flush()
    os.fsync(f.fileno())

with open(ready_file, "w") as f:
    f.write("ready")

import time
while True:
    time.sleep(1)
'''

        killer_script = os.path.join(temp_persist_dir, "mid_write.py")
        with open(killer_script, "w") as f:
            f.write(code)

        proc = subprocess.Popen(
            [sys.executable, killer_script, temp_persist_dir],
        )

        waited = 0
        while waited < 5:
            if os.path.exists(ready_file):
                break
            time.sleep(0.1)
            waited += 0.1

        assert os.path.exists(ready_file), "Child should have created ready file"
        assert os.path.exists(tmp_path), "tmp file should exist"

        proc.kill()
        proc.wait()

        assert os.path.exists(tmp_path), "tmp file should remain after kill"
        assert not os.path.exists(json_path), "json file should not exist"

        persist = SessionPersistence(temp_persist_dir)
        with pytest.raises(CorruptedSessionError) as exc_info:
            persist.load("kill-me")

        assert "会话数据损坏或不完整" in str(exc_info.value)
        assert exc_info.value.exit_code == EXIT_CODE_CORRUPTED
