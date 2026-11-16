"""Seed a local Slipstream (驭流) database with a demo channel, models, combo and key.

Usage (from api):
    python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from l01_core.config import get_settings  # noqa: E402
from l02_infra.db import build_engine, build_sessionmaker, init_db  # noqa: E402
from l01_core.timeutil import now_ms  # noqa: E402
from l04_repo import orm  # noqa: E402
from l05_service import channels, channel_models, combos, keys  # noqa: E402
from l05_service.auth import ensure_default_admin  # noqa: E402
from l01_core.ids import new_id  # noqa: E402


async def seed() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    await init_db(engine)
    try:
        async with session_factory() as session:
            await ensure_default_admin(session_factory, settings)
            channel = await channels.create_channel(
                session,
                {
                    "name": "演示渠道",
                    "slug": "demo",
                    "connection_url": "https://api.openai.com/v1",
                    "api_key": "replace-with-a-real-key",
                    "protocol": "openai_chat",
                    "enabled": True,
                },
            )
            model = await channel_models.create_channel_model(
                session,
                channel.id,
                {
                    "upstream_model": "gpt-4o-mini",
                    "call_model": "demo-chat",
                    "parameters": {
                        "max_input_tokens": 128000,
                        "max_output_tokens": 16384,
                        "reasoning_level": "",
                        "input_types": ["text", "image"],
                        "output_types": ["text"],
                    },
                    "input_price_per_1m": 0.15,
                    "output_price_per_1m": 0.6,
                },
            )
            await combos.create_combo(
                session,
                {
                    "name": "demo-combo",
                    "description": "Seed combo",
                    "enabled": True,
                    "targets": [
                        {
                            "target_type": "channel_model",
                            "channel_model_id": model.id,
                            "priority": 1,
                            "enabled": True,
                        }
                    ],
                },
            )
            key, plaintext = await keys.create_api_key(session, "seed-demo")

            now = now_ms()
            for offset in range(20):
                created = now - offset * 6 * 60 * 60 * 1000
                prompt = 120 + offset * 7
                completion = 60 + offset * 3
                cached = prompt // 3 if offset % 3 == 0 else 0
                is_error = offset % 7 == 6
                status = 429 if offset == 6 else 500 if is_error else 200
                cost = (
                    0.0
                    if is_error
                    else round(
                        (prompt - cached) / 1_000_000 * model.input_price_per_1m
                        + completion / 1_000_000 * model.output_price_per_1m,
                        8,
                    )
                )
                session.add(
                    orm.RequestLogORM(
                        id=new_id(),
                        request_id=f"seed-{offset:03d}",
                        api_key_id=key.id,
                        api_key_label=key.label,
                        requested_model="demo-combo" if offset % 2 else f"{channel.slug}/{model.call_model}",
                        resolved_combo_id=None,
                        final_channel_model_id=model.id,
                        channel_name=channel.name,
                        upstream_model=model.upstream_model,
                        response_model=model.upstream_model,
                        is_stream=bool(offset % 2),
                        http_status=status,
                        error_code="RATE_LIMITED" if status == 429 else "UPSTREAM_ERROR" if is_error else None,
                        error_message="Seed rate limit" if status == 429 else "Seed upstream error" if is_error else "",
                        prompt_tokens=prompt,
                        completion_tokens=0 if is_error else completion,
                        cached_tokens=cached,
                        cache_ratio=round(cached / prompt, 4) if prompt else 0.0,
                        cost_usd=cost,
                        ttft_ms=120 + offset if offset % 2 and not is_error else None,
                        total_ms=700 + offset * 10,
                        throughput_tps=40.0 + offset if offset % 2 and not is_error else None,
                        usage_estimated=False,
                        attempts_json="[]",
                        created_at=created,
                    )
                )
            await session.commit()

            print("Seed complete.")
            print(f"Admin: {settings.admin_username} / {settings.admin_password}")
            print(f"Direct model: {channel.slug}/{model.call_model}")
            print(f"Combo: demo-combo")
            print(f"API key (shown once): {plaintext}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
