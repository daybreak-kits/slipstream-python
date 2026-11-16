"""Manual smoke test against the real upstreams listed in note.txt.

Never prints credentials. OpenRouter is limited to free models.
Run the API on 127.0.0.1:3681 first, then:
    python scripts/manual_smoke.py
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:3681"
NOTE = Path(__file__).resolve().parents[2] / "note.txt"


def parse_note() -> list[dict]:
    text = NOTE.read_text(encoding="utf-8")
    sections = re.split(r"^\[\d+\]\s*", text, flags=re.MULTILINE)[1:]
    result = []
    for section in sections:
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        name = lines[0]
        url = next(line.split("：", 1)[1] for line in lines if "连接地址" in line)
        key = next(line.split("：", 1)[1] for line in lines if "渠道密钥" in line)
        models = [line.lstrip("- ").strip() for line in lines if line.startswith("- ")]
        if "OpenRouter" in name:
            models = [m for m in models if m.endswith(":free")]
        result.append({"name": name, "url": url, "key": key, "models": models})
    return result


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=180) as client:
        login = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        login.raise_for_status()
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        key_resp = await client.post("/api/keys", json={"label": "manual-smoke"})
        key_resp.raise_for_status()
        api_key = key_resp.json()["key"]

        for cfg in parse_note():
            slug = re.sub(r"[^a-z0-9]+", "-", cfg["name"].lower()).strip("-")
            created = await client.post(
                "/api/channels",
                json={
                    "name": cfg["name"],
                    "slug": slug,
                    "connection_url": cfg["url"],
                    "api_key": cfg["key"],
                    "protocol": "openai_chat",
                },
            )
            if created.status_code != 201:
                print(f"[{cfg['name']}] channel create failed: {created.status_code} {created.text[:160]}")
                continue
            channel = created.json()
            model_ids = []
            for model in cfg["models"]:
                resp = await client.post(
                    f"/api/channels/{channel['id']}/models",
                    json={"upstream_model": model, "call_model": model[:64]},
                )
                if resp.status_code == 201:
                    model_ids.append(resp.json()["id"])
                else:
                    print(f"[{cfg['name']}] model {model} create failed: {resp.status_code}")

            shallow_ok = 0
            for model_id in model_ids:
                probe = await client.post(f"/api/channel-models/{model_id}/probe", json={"mode": "shallow"})
                if probe.status_code == 200 and probe.json().get("status") == "ok":
                    shallow_ok += 1
            print(f"[{cfg['name']}] models={len(model_ids)} shallow_probe_ok={shallow_ok}")

            for chosen in cfg["models"]:
                chat = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": f"{slug}/{chosen}",
                        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
                        "max_tokens": 8,
                    },
                )
                body = chat.json() if chat.headers.get("content-type", "").startswith("application/json") else {}
                content = ""
                if isinstance(body, dict):
                    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
                detail = "" if chat.status_code == 200 else str(body)[:140]
                print(f"[{cfg['name']}] chat model={chosen} status={chat.status_code} content={content[:60]!r} {detail}")
                if chat.status_code == 200:
                    break

        logs = await client.get("/api/logs", params={"page": 1, "page_size": 5})
        print(f"logs_status={logs.status_code} total={logs.json().get('total') if logs.status_code == 200 else '-'}")
        summary = await client.get("/api/dashboard/summary")
        print(f"dashboard_status={summary.status_code} body={str(summary.json())[:200] if summary.status_code == 200 else '-'}")


if __name__ == "__main__":
    asyncio.run(main())
