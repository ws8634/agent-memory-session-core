from .constants import (
    ERROR_PREFIX,
    ERROR_SUFFIX,
    EXIT_CODE_ERROR,
    EXIT_CODE_LOCK_TIMEOUT,
    EXIT_CODE_CORRUPTED,
    EXIT_CODE_CONCURRENT,
    EXIT_CODE_DUPLICATE,
    EXIT_CODE_VERSION,
)


class AgentMemoryError(Exception):
    exit_code = EXIT_CODE_ERROR
    message_template = "未知错误"

    def __init__(self, detail: str = "", *args):
        self.detail = detail
        full_message = f"{ERROR_PREFIX}{self.message_template}"
        if detail:
            full_message += f": {detail}"
        full_message += ERROR_SUFFIX
        super().__init__(full_message, *args)


class LockTimeoutError(AgentMemoryError):
    exit_code = EXIT_CODE_LOCK_TIMEOUT
    message_template = "获取会话锁超时"


class ConcurrentPersistError(AgentMemoryError):
    exit_code = EXIT_CODE_CONCURRENT
    message_template = "并发持久化冲突"


class CorruptedSessionError(AgentMemoryError):
    exit_code = EXIT_CODE_CORRUPTED
    message_template = "会话数据损坏或不完整"


class DuplicateMemoryError(AgentMemoryError):
    exit_code = EXIT_CODE_DUPLICATE
    message_template = "冷记忆重复条目被拒绝"


class IOError(AgentMemoryError):
    exit_code = EXIT_CODE_ERROR
    message_template = "文件读写错误"


class VersionMismatchError(AgentMemoryError):
    exit_code = EXIT_CODE_VERSION
    message_template = "会话版本不兼容"
