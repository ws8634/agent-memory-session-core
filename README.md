# agent-memory-session-core

Agent 会话记忆核心模块，支持冷热记忆分离、并发持久化、脏文件容错、版本兼容。

## 安装

```bash
pip install -e .
```

## 核心功能

### 冷热记忆分离
- **热记忆**：最近 N 条消息，快速访问
- **冷记忆**：溢出的历史消息，支持去重
- 去重模式：`reject`（拒收重复）或 `overwrite`（覆盖）

### 并发持久化
- 文件锁保护，同一时间只能一个进程写入
- 锁超时可配置，超时后抛出 `LockTimeoutError`
- 后到者等锁超时会走统一错误路径

### 脏文件处理
- 写入时先写 `.tmp` 临时文件，成功后 rename
- 若 `.tmp` 文件存在，说明上次写入被中断
- load 时检测到 `.tmp` 直接走 `CorruptedSessionError`
- 不会拼半个 Session 出来

### 版本兼容
- 支持旧版本会话读取（`load_legacy`）
- 支持版本升级（`upgrade_session`）
- 旧格式文件不会被误删

## CLI 使用

```bash
# 添加消息
ams add my-session user "你好"

# 获取会话记忆
ams get my-session

# 列出所有会话
ams list

# 删除会话
ams delete my-session

# 升级旧版本会话
ams upgrade my-session
```

## 错误码与消息格式

所有错误消息三头对齐：
- **库内异常**：带 `[AgentMemory]` 前缀 + ` - 操作失败，请检查配置或重试` 后缀
- **CLI stderr**：与库内异常格式完全一致
- **退出码**：每个异常类型对应固定退出码

| 退出码 | 含义 | 异常类 |
|--------|------|--------|
| 0 | 成功 | - |
| 1 | 通用错误 | `AgentMemoryError`, `IOError` |
| 2 | 锁超时 | `LockTimeoutError` |
| 3 | 数据损坏 | `CorruptedSessionError` |
| 4 | 并发冲突 | `ConcurrentPersistError` |
| 5 | 重复被拒 | `DuplicateMemoryError` |
| 6 | 版本不兼容 | `VersionMismatchError` |

## 并发锁超时复现

测试时可通过修改 `LOCK_TIMEOUT_SECONDS` 常量缩短超时时间：

```python
# pytest fixture 示例（已在 conftest.py 中实现）
@pytest.fixture
def short_lock_timeout():
    import agent_memory_session.constants as const
    original = const.LOCK_TIMEOUT_SECONDS
    const.LOCK_TIMEOUT_SECONDS = 0.5  # 改小好测
    yield 0.5
    const.LOCK_TIMEOUT_SECONDS = original
```

手动复现：
1. 进程 A 持有锁（用 `fcntl.flock` 独占锁定 `.lock` 文件）
2. 进程 B 尝试 save/load，等锁
3. 超时后进程 B 抛出 `LockTimeoutError`

## 脏文件复现

两种场景都会触发 `CorruptedSessionError`：

**场景1：半截 tmp 文件**
- 写入过程中进程被杀（SIGKILL/SIGTERM）
- `.tmp` 文件残留，`.json` 文件未创建或仍是旧版
- load 时优先检测 `.tmp`，直接抛错

**场景2：明显脏数据**
- JSON 文件为空
- JSON 格式无效（解析失败）
- 缺少必要字段（`session_id`, `warm_memories`, `cold_memories`）

手动复现半截写入：
```bash
# 1. 找到会话对应的哈希文件名
python -c "from agent_memory_session.persistence import SessionPersistence; print(SessionPersistence._safe_filename('test-session'))"

# 2. 创建不完整的 .tmp 文件
echo '{"session_id": "test-session", "warm_memories": [' > /path/to/persist/{hash}.tmp

# 3. 尝试加载，会触发 CorruptedSessionError
ams get test-session
```

## 测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试
pytest tests/test_memory_session.py::TestLockTimeout -v
pytest tests/test_memory_session.py::TestDirtyFileHandling -v
pytest tests/test_memory_session.py::TestSubprocessKillMidWrite -v
```

## 常量配置（可在测试中动态修改）

```python
from agent_memory_session import constants

# 锁超时（秒）- 写死的常量，pytest 可改小好测
constants.LOCK_TIMEOUT_SECONDS = 3.0

# 锁获取重试间隔
constants.LOCK_ACQUIRE_RETRY_INTERVAL = 0.1

# 热记忆最大条数
constants.MAX_WARM_MEMORIES = 5

# 冷记忆去重模式
constants.COLD_MEMORY_DEDUP_MODE = "reject"  # 或 "overwrite"
```

## 项目定位
SoloCoder 高阶Agent专项合集 - 第04个项目
独立开发者自研，对标 OpenClaw 多Agent调度架构
