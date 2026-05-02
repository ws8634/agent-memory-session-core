import sys
import argparse
import json
from typing import Optional

from .memory import SessionMemory
from .persistence import SessionPersistence
from .exceptions import (
    AgentMemoryError,
    LockTimeoutError,
    ConcurrentPersistError,
    CorruptedSessionError,
    DuplicateMemoryError,
    VersionMismatchError,
    IOError,
)
from .constants import (
    PERSIST_DIR,
    LOCK_TIMEOUT_SECONDS,
    ERROR_SUFFIX,
    EXIT_CODE_SUCCESS,
    MAX_WARM_MEMORIES,
    SESSION_VERSION,
)


def format_error(e: AgentMemoryError) -> str:
    return str(e)


def main(args=None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent Memory Session CLI - 管理 Agent 会话记忆"
    )
    parser.add_argument(
        "--persist-dir",
        type=str,
        default=PERSIST_DIR,
        help=f"持久化目录 (默认: {PERSIST_DIR})",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=LOCK_TIMEOUT_SECONDS,
        help=f"锁超时时间(秒) (默认: {LOCK_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-warm",
        type=int,
        default=MAX_WARM_MEMORIES,
        help=f"热记忆最大数量 (默认: {MAX_WARM_MEMORIES})",
    )

    subparsers = parser.add_subparsers(title="命令", dest="command")

    add_parser = subparsers.add_parser("add", help="添加消息到会话")
    add_parser.add_argument("session_id", type=str, help="会话ID")
    add_parser.add_argument("role", type=str, choices=["user", "assistant", "system", "tool"], help="消息角色")
    add_parser.add_argument("content", type=str, help="消息内容")
    add_parser.add_argument("--force", action="store_true", help="强制覆盖已有会话")

    get_parser = subparsers.add_parser("get", help="获取会话记忆")
    get_parser.add_argument("session_id", type=str, help="会话ID")
    get_parser.add_argument("--type", type=str, choices=["all", "warm", "cold"], default="all", help="记忆类型")
    get_parser.add_argument("--no-version-check", action="store_true", help="跳过版本检查")

    list_parser = subparsers.add_parser("list", help="列出所有会话")

    delete_parser = subparsers.add_parser("delete", help="删除会话")
    delete_parser.add_argument("session_id", type=str, help="会话ID")

    upgrade_parser = subparsers.add_parser("upgrade", help="升级旧版本会话")
    upgrade_parser.add_argument("session_id", type=str, help="会话ID")

    if args is None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(args)

    if not args.command:
        parser.print_help()
        return EXIT_CODE_SUCCESS

    try:
        persist = SessionPersistence(
            persist_dir=args.persist_dir,
            lock_timeout=args.lock_timeout,
            expected_version=SESSION_VERSION,
        )

        if args.command == "add":
            return cmd_add(args, persist)
        elif args.command == "get":
            return cmd_get(args, persist)
        elif args.command == "list":
            return cmd_list(args, persist)
        elif args.command == "delete":
            return cmd_delete(args, persist)
        elif args.command == "upgrade":
            return cmd_upgrade(args, persist)

        return EXIT_CODE_SUCCESS

    except AgentMemoryError as e:
        print(format_error(e), file=sys.stderr)
        return e.exit_code
    except FileNotFoundError as e:
        print(f"[AgentMemory] 文件未找到: {str(e)}{ERROR_SUFFIX}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[AgentMemory] 未预期错误: {str(e)}{ERROR_SUFFIX}", file=sys.stderr)
        return 1


def cmd_add(args, persist: SessionPersistence) -> int:
    session_id = args.session_id
    memory = SessionMemory(
        session_id=session_id,
        max_warm_memories=args.max_warm,
        persist_dir=args.persist_dir,
    )

    if persist.exists(session_id):
        if args.force:
            try:
                session = persist.load(session_id, check_version=False)
                memory.load_from_session(session)
            except Exception:
                pass
        else:
            try:
                session = persist.load(session_id)
                memory.load_from_session(session)
            except VersionMismatchError:
                raise

    memory.add_message(role=args.role, content=args.content)
    persist.save(memory.session, force_overwrite=args.force)
    print(f"已添加消息到会话: {session_id}")
    return EXIT_CODE_SUCCESS


def cmd_get(args, persist: SessionPersistence) -> int:
    session_id = args.session_id
    check_version = not args.no_version_check

    session = persist.load(session_id, check_version=check_version)

    if args.type == "warm":
        memories = [m.to_dict() for m in session.warm_memories]
    elif args.type == "cold":
        memories = [m.to_dict() for m in session.cold_memories]
    else:
        memories = [m.to_dict() for m in session.cold_memories + session.warm_memories]

    output = {
        "session_id": session.session_id,
        "version": session.version,
        "memories": memories,
        "count": len(memories),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return EXIT_CODE_SUCCESS


def cmd_list(args, persist: SessionPersistence) -> int:
    import glob
    import os

    sessions = []
    json_files = glob.glob(os.path.join(persist.persist_dir, "*.json"))

    for json_file in json_files:
        if json_file.endswith(".old") or json_file.endswith(".tmp"):
            continue
        filename = os.path.basename(json_file)
        session_hash = filename[:-5]
        sessions.append({
            "file": json_file,
            "hash": session_hash,
        })

    print(json.dumps(sessions, indent=2, ensure_ascii=False))
    return EXIT_CODE_SUCCESS


def cmd_delete(args, persist: SessionPersistence) -> int:
    session_id = args.session_id
    if persist.exists(session_id):
        persist.delete(session_id)
        print(f"已删除会话: {session_id}")
    else:
        print(f"会话不存在: {session_id}")
    return EXIT_CODE_SUCCESS


def cmd_upgrade(args, persist: SessionPersistence) -> int:
    session_id = args.session_id
    if not persist.exists(session_id):
        print(f"会话不存在: {session_id}")
        return 1

    success = persist.upgrade_session(session_id)
    if success:
        print(f"已升级会话: {session_id} 到版本 {SESSION_VERSION}")
    return EXIT_CODE_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
