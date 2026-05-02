from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import json

from .constants import SESSION_VERSION


@dataclass
class Message:
    role: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        if "timestamp" in data and isinstance(data["timestamp"], str):
            timestamp = datetime.fromisoformat(data["timestamp"])
        else:
            timestamp = datetime.now(timezone.utc)
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=timestamp,
        )


@dataclass
class Session:
    session_id: str
    version: str = SESSION_VERSION
    warm_memories: List[Message] = field(default_factory=list)
    cold_memories: List[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "version": self.version,
            "warm_memories": [m.to_dict() for m in self.warm_memories],
            "cold_memories": [m.to_dict() for m in self.cold_memories],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        warm_memories = [Message.from_dict(m) for m in data.get("warm_memories", [])]
        cold_memories = [Message.from_dict(m) for m in data.get("cold_memories", [])]

        if "created_at" in data and isinstance(data["created_at"], str):
            created_at = datetime.fromisoformat(data["created_at"])
        else:
            created_at = datetime.now(timezone.utc)

        if "updated_at" in data and isinstance(data["updated_at"], str):
            updated_at = datetime.fromisoformat(data["updated_at"])
        else:
            updated_at = datetime.now(timezone.utc)

        return cls(
            session_id=data["session_id"],
            version=data.get("version", "0.9"),
            warm_memories=warm_memories,
            cold_memories=cold_memories,
            created_at=created_at,
            updated_at=updated_at,
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Session":
        data = json.loads(json_str)
        return cls.from_dict(data)
