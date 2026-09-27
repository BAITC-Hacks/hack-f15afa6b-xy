"""Serve the pinned Pulse 109 Qwen model on a private GPU endpoint."""

from __future__ import annotations

import argparse
import hmac
import os
import threading
import time
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class CompletionRequest(BaseModel):
    model: str
    messages: list[Message] = Field(min_length=1, max_length=12)
    temperature: float = Field(default=0, ge=0, le=1)
    max_tokens: int = Field(default=700, ge=1, le=1000)
    response_format: dict | None = None


def authorized(header, api_key):
    return bool(api_key) and hmac.compare_digest(header or "", "Bearer " + api_key)


def create_service(model_id, revision, device, api_key):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, revision=revision, torch_dtype=torch.float16, low_cpu_mem_usage=True,
    ).to(device).eval()
    lock = threading.Lock()
    app = FastAPI(title="Pulse 109 private Qwen Copilot")

    @app.middleware("http")
    async def require_key(request: Request, call_next):
        if not authorized(request.headers.get("authorization"), api_key):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": model_id, "revision": revision, "device": device}

    @app.post("/v1/chat/completions")
    def complete(req: CompletionRequest):
        if req.model != model_id:
            raise HTTPException(422, "Unknown model")
        messages = [message.model_dump() for message in req.messages]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([prompt], return_tensors="pt").to(device)
        kwargs = {"max_new_tokens": req.max_tokens, "do_sample": req.temperature > 0,
                  "pad_token_id": tokenizer.eos_token_id}
        if req.temperature > 0:
            kwargs["temperature"] = req.temperature
        with lock, torch.inference_mode():
            output = model.generate(**inputs, **kwargs)
        content = tokenizer.decode(output[0][inputs.input_ids.shape[-1]:], skip_special_tokens=True).strip()
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion",
            "created": int(time.time()), "model": model_id,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
        }

    return app


def self_check():
    assert authorized("Bearer secret", "secret")
    assert not authorized("Bearer wrong", "secret")
    assert not authorized(None, "secret")
    request = CompletionRequest(model="Qwen/Qwen3-4B-Instruct-2507", messages=[
        Message(role="system", content="Return JSON."), Message(role="user", content="Synthetic test"),
    ])
    assert request.max_tokens == 700 and request.temperature == 0
    print("PASS: private Qwen request and bearer validation")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    api_key = os.environ.get("P109_COPILOT_API_KEY", "")
    if not api_key:
        raise SystemExit("P109_COPILOT_API_KEY is required")
    import uvicorn
    uvicorn.run(create_service(args.model, args.revision, args.device, api_key),
                host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
