import os
import tempfile
import shutil
import pytest


@pytest.fixture
def temp_persist_dir():
    tmp_dir = tempfile.mkdtemp(prefix="ams_test_")
    yield tmp_dir
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)


@pytest.fixture
def short_lock_timeout():
    from agent_memory_session import constants
    original = constants.LOCK_TIMEOUT_SECONDS
    constants.LOCK_TIMEOUT_SECONDS = 0.5
    yield 0.5
    constants.LOCK_TIMEOUT_SECONDS = original
