from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from .models import Message, Session
from .persistence import GlobalColdMemoryStore
from .exceptions import DuplicateMemoryError, CorruptedSessionError
from .constants import (
    MAX_WARM_MEMORIES,
    COLD_MEMORY_DEDUP_MODE,
    COLD_MEMORY_DEDUP_MODE_REJECT,
    COLD_MEMORY_DEDUP_MODE_OVERWRITE,
    SESSION_VERSION,
    PERSIST_DIR,
    LOCK_TIMEOUT_SECONDS,
)


class SessionMemory:
    def __init__(
        self,
        session_id: str,
        max_warm_memories: int = MAX_WARM_MEMORIES,
        cold_dedup_mode: str = COLD_MEMORY_DEDUP_MODE,
        persist_dir: str = PERSIST_DIR,
        global_cold_store: Optional[GlobalColdMemoryStore] = None,
        lock_timeout: float = LOCK_TIMEOUT_SECONDS,
    ):
        self.session_id = session_id
        self.max_warm_memories = max_warm_memories
        self.cold_dedup_mode = cold_dedup_mode
        self.persist_dir = persist_dir
        self.lock_timeout = lock_timeout
        self._session = Session(session_id=session_id)

        if global_cold_store is not None:
            self._global_cold_store = global_cold_store
        else:
            self._global_cold_store = GlobalColdMemoryStore(
                persist_dir=persist_dir,
                dedup_mode=cold_dedup_mode,
                lock_timeout=lock_timeout,
            )

    @property
    def session(self) -> Session:
        return self._session

    @property
    def global_cold_store(self) -> GlobalColdMemoryStore:
        return self._global_cold_store

    def get_warm_memories(self) -> List[Message]:
        return list(self._session.warm_memories)

    def get_cold_memories(self) -> List[Message]:
        return self._global_cold_store.get_all()

    def add_message(self, role: str, content: str, timestamp: Optional[datetime] = None) -> Message:
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        msg = Message(role=role, content=content, timestamp=timestamp)
        self._session.warm_memories.append(msg)

        if len(self._session.warm_memories) > self.max_warm_memories:
            overflow = self._session.warm_memories[0]
            self._session.warm_memories = self._session.warm_memories[1:]
            self._add_to_global_cold_memory(overflow)

        self._session.updated_at = datetime.now(timezone.utc)
        return msg

    def _add_to_global_cold_memory(self, message: Message) -> bool:
        return self._global_cold_store.add(message)

    def check_cold_duplicate(self, role: str, content: str) -> bool:
        return self._global_cold_store.check_duplicate(role, content)

    def get_all_memories(self) -> List[Message]:
        return self._global_cold_store.get_all() + self._session.warm_memories

    def get_context(self, max_tokens: Optional[int] = None) -> List[Dict[str, str]]:
        all_memories = self.get_all_memories()
        return [{"role": m.role, "content": m.content} for m in all_memories]

    def load_from_session(self, session: Session):
        if session.session_id != self.session_id:
            raise ValueError(f"Session ID mismatch: expected {self.session_id}, got {session.session_id}")
        self._session = session

    def clear(self):
        self._session.warm_memories = []
        self._global_cold_store.clear()
        self._session.updated_at = datetime.now(timezone.utc)
