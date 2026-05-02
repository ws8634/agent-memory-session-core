from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from .models import Message, Session
from .exceptions import DuplicateMemoryError, CorruptedSessionError
from .constants import (
    MAX_WARM_MEMORIES,
    COLD_MEMORY_DEDUP_MODE,
    COLD_MEMORY_DEDUP_MODE_REJECT,
    COLD_MEMORY_DEDUP_MODE_OVERWRITE,
    SESSION_VERSION,
)


class SessionMemory:
    def __init__(
        self,
        session_id: str,
        max_warm_memories: int = MAX_WARM_MEMORIES,
        cold_dedup_mode: str = COLD_MEMORY_DEDUP_MODE,
    ):
        self.session_id = session_id
        self.max_warm_memories = max_warm_memories
        self.cold_dedup_mode = cold_dedup_mode
        self._session = Session(session_id=session_id)

    @property
    def session(self) -> Session:
        return self._session

    def get_warm_memories(self) -> List[Message]:
        return list(self._session.warm_memories)

    def get_cold_memories(self) -> List[Message]:
        return list(self._session.cold_memories)

    def add_message(self, role: str, content: str, timestamp: Optional[datetime] = None) -> Message:
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        msg = Message(role=role, content=content, timestamp=timestamp)
        self._session.warm_memories.append(msg)

        if len(self._session.warm_memories) > self.max_warm_memories:
            overflow = self._session.warm_memories[0]
            self._session.warm_memories = self._session.warm_memories[1:]
            self._add_to_cold_memory(overflow)

        self._session.updated_at = datetime.now(timezone.utc)
        return msg

    def _add_to_cold_memory(self, message: Message) -> bool:
        existing_idx = self._find_duplicate_cold_memory(message)

        if existing_idx is not None:
            if self.cold_dedup_mode == COLD_MEMORY_DEDUP_MODE_REJECT:
                detail = f"role={message.role}, content={repr(message.content)[:50]}..."
                raise DuplicateMemoryError(detail)
            elif self.cold_dedup_mode == COLD_MEMORY_DEDUP_MODE_OVERWRITE:
                self._session.cold_memories[existing_idx] = message
                return True

        self._session.cold_memories.append(message)
        return True

    def _find_duplicate_cold_memory(self, message: Message) -> Optional[int]:
        for idx, msg in enumerate(self._session.cold_memories):
            if msg.role == message.role and msg.content == message.content:
                return idx
        return None

    def check_cold_duplicate(self, role: str, content: str) -> bool:
        for msg in self._session.cold_memories:
            if msg.role == role and msg.content == content:
                return True
        return False

    def get_all_memories(self) -> List[Message]:
        return self._session.cold_memories + self._session.warm_memories

    def get_context(self, max_tokens: Optional[int] = None) -> List[Dict[str, str]]:
        all_memories = self.get_all_memories()
        return [{"role": m.role, "content": m.content} for m in all_memories]

    def load_from_session(self, session: Session):
        if session.session_id != self.session_id:
            raise ValueError(f"Session ID mismatch: expected {self.session_id}, got {session.session_id}")
        self._session = session

    def clear(self):
        self._session.warm_memories = []
        self._session.cold_memories = []
        self._session.updated_at = datetime.now(timezone.utc)
