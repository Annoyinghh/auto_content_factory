"""Shared loopback-only Ollama transport."""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_MODEL = "qwen3.5:4b"
VISUAL_INSTRUCT = "qwen3-vl:4b-instruct"
PREFERRED_VISUAL = "acf-vision-qwen35:4b"
VISUAL_MODELS = {
    "design": ("CONTENT_AI_DESIGN_MODEL", "qwen3:4b-instruct-2507-q4_K_M"),
    "editing": ("CONTENT_AI_EDIT_MODEL", "qwen3-vl:4b"),
}


class LocalAIUnavailable(RuntimeError):
    """The model service cannot process further scenes in this batch."""


def _loopback():
    port = int(os.getenv("CONTENT_AI_PORT", "11517" if os.getenv("CONTENT_AI_GPU") == "0" else "11434"))
    if not 1 <= port <= 65535:
        raise ValueError("本机模型端口无效。")
    return f"http://127.0.0.1:{port}"


def model_for(role):
    if role == "writing":
        if (configured := os.getenv("CONTENT_AI_MODEL")) is not None:
            return configured.strip()
        fallback = VISUAL_MODELS["design"][1]
        models = installed_models(local_only=True)
        if 'acf-writer-qwen35:9b' in models:
            return 'acf-writer-qwen35:9b'
        if 'acf-writer:8b' in models:
            return 'acf-writer:8b'
        if PREFERRED_VISUAL in models:
            return PREFERRED_VISUAL
        return fallback if DEFAULT_MODEL not in models and fallback in models else DEFAULT_MODEL
    setting, default = VISUAL_MODELS[role]
    if (configured := os.getenv(setting)) is not None:
        return configured.strip()
    if role == 'design' and 'acf-writer-qwen35:9b' in installed_models(local_only=True):
        return 'acf-writer-qwen35:9b'
    if PREFERRED_VISUAL in installed_models(local_only=True):
        return PREFERRED_VISUAL
    if role == "editing" and VISUAL_INSTRUCT in installed_models(local_only=True):
        return VISUAL_INSTRUCT
    return default


def installed_models(*, local_only=False):
    import requests

    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(f"{_loopback()}/api/tags", timeout=(.5, 2), allow_redirects=False)
            response.raise_for_status()
            entries = response.json()["models"]
            return {entry["name"] for entry in entries if not local_only or (
                not entry.get("remote_model") and not entry.get("remote_host")
                and not entry["name"].casefold().endswith((":cloud", "-cloud"))
            )}
    except (requests.RequestException, AttributeError, KeyError, TypeError, ValueError):
        return set()


def start_portable_ai(*, wait=False):
    from config import BASE_DIR

    if not os.getenv("CONTENT_AI_MODEL", DEFAULT_MODEL).strip():
        return
    studio_root = os.environ.get("ACF_STUDIO_ROOT")
    directory = Path(studio_root) if studio_root else (
        Path(sys.executable).parent if getattr(sys, "frozen", False) else BASE_DIR / "dist")
    root = directory / "local-ai"
    executable = root / "runtime" / "ollama.exe"
    if not executable.is_file():
        return
    address = _loopback().rsplit("/", 1)[-1]
    port = int(address.rsplit(":", 1)[-1])
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.3):
            pass
    except OSError:
        subprocess.Popen(
            [str(executable), "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            cwd=executable.parent,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "OLLAMA_HOST": address, "OLLAMA_MODELS": str(root / "models"),
                 "OLLAMA_CONTEXT_LENGTH": "8192", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
                 "OLLAMA_VULKAN": "false", "OLLAMA_NO_CLOUD": "true",
                 **({"CUDA_VISIBLE_DEVICES": "-1", "OLLAMA_LLM_LIBRARY": "cpu"}
                    if os.getenv("CONTENT_AI_GPU") == "0" else {})},
        )
    if wait:
        import requests

        # Ollama binds its port before completing GPU discovery on Windows.
        deadline = time.monotonic() + 120
        with requests.Session() as session:
            session.trust_env = False
            while time.monotonic() < deadline:
                try:
                    response = session.get(f"{_loopback()}/api/version", timeout=(.5, 1), allow_redirects=False)
                    if response.status_code == 200:
                        return
                except requests.RequestException:
                    pass
                time.sleep(.5)
        raise LocalAIUnavailable("本机 Ollama 已等待两分钟，模型接口仍未就绪。请检查 Ollama 的启动状态后重试。")


def chat_json(instruction, context, schema, *, temperature=0, model=None, images=(), max_tokens=None, reasoning=False):
    import requests

    model = model_for("writing") if model is None else model
    if not model:
        raise RuntimeError("本机 Ollama 模型已禁用，请设置 CONTENT_AI_MODEL 或移除空配置。")
    message = {"role": "user", "content": json.dumps(context, ensure_ascii=False)}
    messages = [{"role": "system", "content": instruction}, message]
    if images:
        message["images"] = [base64.b64encode(image).decode("ascii") for image in images]
        message["content"] = instruction + "\n" + (context if isinstance(context, str) else message["content"])
        messages = [message]
    setting, fallback = VISUAL_MODELS["editing"]
    configured_editor = os.getenv(setting)
    editing_models = {configured_editor.strip()} if configured_editor is not None else {fallback, VISUAL_INSTRUCT, PREFERRED_VISUAL}
    visual_role = bool(images) or (model == model_for("design") or model in editing_models) and model != model_for("writing")
    options = {"temperature": temperature, "num_ctx": 4096 if visual_role else 8192}
    if os.getenv("CONTENT_AI_GPU", "auto") == "0" or visual_role and os.getenv("CONTENT_AI_VISUAL_GPU", "auto") == "0":
        options["num_gpu"] = 0
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                f"{_loopback()}/api/chat", timeout=(2, 420 if visual_role or reasoning else 180), allow_redirects=False,
                json={"model": model, "stream": False, "think": reasoning, "format": schema,
                      "keep_alive": "2m", "options": options, "messages": messages},
            )
            if response.status_code != 200:
                raise LocalAIUnavailable(f"Ollama 返回 HTTP {response.status_code}，请检查 {model} 是否已下载。")
            payload = response.json()
            content = payload['message']['content']
            try:
                return json.loads(content)
            except (ValueError, TypeError) as error:
                reason = str(payload.get('done_reason') or 'unknown')[:32]
                count = payload.get('eval_count')
                count = count if type(count) is int else '未知'
                detail = f'结束原因：{reason}，已生成 {count} token'
                if not content or isinstance(content, str) and not content.strip():
                    thinking = '已生成思考但未生成答案，' if payload['message'].get('thinking') else ''
                    raise RuntimeError(f'本机模型 {model} 返回空内容（{thinking}{detail}）。请检查本次输出预算后重试。') from error
                if reason == 'length':
                    raise RuntimeError(f'本机模型 {model} 的结构化输出达到长度上限（{count} token），JSON 未完成。'
                                       '请提高本次输出预算后重试。') from error
                raise RuntimeError(f'本机模型 {model} 返回的 JSON 格式无效（{detail}）。') from error
    except requests.Timeout as error:
        raise LocalAIUnavailable(f"本机模型 {model} 响应超时。已通过的制作步骤仍可复用。") from error
    except requests.RequestException as error:
        raise LocalAIUnavailable(f"本地 AI 服务暂不可用，请检查本机模型服务后重试。已完成步骤仍可复用。") from error
    except (KeyError, ValueError, TypeError) as error:
        raise RuntimeError("本地 AI 未返回可用的结构化内容。") from error
