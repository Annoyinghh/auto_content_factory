"""Shared loopback-only Ollama transport."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path


DEFAULT_MODEL = "qwen3.5:4b"


def start_portable_ai():
    from config import BASE_DIR

    if not os.getenv("CONTENT_AI_MODEL", DEFAULT_MODEL).strip():
        return
    directory = Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE_DIR / "dist"
    root = directory / "local-ai"
    executable = root / "runtime" / "ollama.exe"
    if not executable.is_file():
        return
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=.3):
            return
    except OSError:
        pass
    subprocess.Popen(
        [str(executable), "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        cwd=executable.parent,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_MODELS": str(root / "models"),
             "OLLAMA_CONTEXT_LENGTH": "8192", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
             "OLLAMA_VULKAN": "false", "OLLAMA_NO_CLOUD": "true"},
    )


def chat_json(instruction, context, schema, *, temperature=0):
    import requests

    model = os.getenv("CONTENT_AI_MODEL", DEFAULT_MODEL).strip()
    if not model:
        raise RuntimeError("本机 Ollama 模型已禁用，请设置 CONTENT_AI_MODEL 或移除空配置。")
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                "http://127.0.0.1:11434/api/chat", timeout=(2, 180), allow_redirects=False,
                json={"model": model, "stream": False, "think": False, "format": schema,
                      "keep_alive": "2m", "options": {"temperature": temperature, "num_ctx": 8192}, "messages": [
                          {"role": "system", "content": instruction},
                          {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                      ]},
            )
            if response.status_code != 200:
                raise RuntimeError(f"Ollama 返回 HTTP {response.status_code}，请检查 {model} 是否已下载。")
            return json.loads(response.json()["message"]["content"])
    except requests.RequestException as error:
        raise RuntimeError(f"本地 AI 暂不可用。请启动 Ollama 并运行 ollama pull {model}。") from error
    except (KeyError, ValueError, TypeError) as error:
        raise RuntimeError("本地 AI 未返回可用的结构化内容。") from error
