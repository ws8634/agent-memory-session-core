from setuptools import setup, find_packages

setup(
    name="agent-memory-session-core",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[],
    entry_points={
        "console_scripts": [
            "ams=agent_memory_session.cli:main",
        ],
    },
    author="SoloCoder",
    description="Agent Memory Session Core - 会话记忆管理核心模块",
    python_requires=">=3.7",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
)
