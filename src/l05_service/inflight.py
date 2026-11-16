"""节点级在飞请求上限：/v1/chat/completions 快速失败（429）。

进程内计数器 + 动态 cap。cap 每次取槽时现读：设置页保存过的 route_settings
行（进程缓存 15s 内热生效，改完无需重启）优先；从未保存过设置时回落部署
环境变量 ``GATEWAY_INFLIGHT_CAP``；两者皆无则默认 256。0 = 关闭准入控制。
多 worker 部署下这是 per-worker 上限，节点总上限 = cap × worker 数。
管理 API 与 /v1/models 不受限。
"""

from __future__ import annotations

import threading

from l01_core.errors import AppError

# l01_core.ErrorCode 没有节点级限流常量；为避免为单一调用点扩共享错误码表，
# 这里用模块级常量（AppError.code 是普通字符串）。
CODE_INFLIGHT_LIMITED = "INFLIGHT_LIMITED"

_DEFAULT_CAP = 256


class InflightGate:
    """进程内计数器：acquire 超限抛 429（Retry-After=1s），release 归还槽位。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight = 0

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    def _cap(self) -> int:
        # 懒导入：cap 是唯一需要 route_settings / config 的地方，保持模块可最小导入。
        # ADR-077 决策 7：设置页存了行（is_default=False）即生效并压住 env；
        # 无 DB 行时回落部署环境变量 GATEWAY_INFLIGHT_CAP；都没有则默认 256。
        from l05_service import route_settings

        cfg = route_settings.cached_route_settings()
        if not cfg.is_default:
            return max(0, int(cfg.inflight_cap))
        from l01_core.config import get_settings

        env_cap = getattr(get_settings(), "gateway_inflight_cap", None)
        if env_cap is None:
            return route_settings.DEFAULT_INFLIGHT_CAP
        try:
            return max(0, int(env_cap))
        except (TypeError, ValueError):
            return route_settings.DEFAULT_INFLIGHT_CAP

    def acquire(self) -> None:
        """占一个在飞槽位；超上限抛 AppError(429)，cap<=0 时不做任何检查。"""
        cap = self._cap()
        if cap <= 0:
            return
        with self._lock:
            if self._inflight >= cap:
                raise AppError(
                    CODE_INFLIGHT_LIMITED,
                    "服务器在飞请求已达上限，请稍后重试",
                    status=429,
                    details={"retry_after_s": 1, "limit": cap},
                )
            self._inflight += 1

    def release(self) -> None:
        with self._lock:
            if self._inflight > 0:
                self._inflight -= 1


_GATE: InflightGate | None = None
_GATE_LOCK = threading.Lock()


def get_inflight_gate() -> InflightGate:
    """进程级单例：节点内所有 /v1/chat/completions 共享同一计数器。"""
    global _GATE
    gate = _GATE
    if gate is not None:
        return gate
    with _GATE_LOCK:
        gate = _GATE
        if gate is None:
            gate = InflightGate()
            _GATE = gate
        return gate


def reset_inflight_gate() -> InflightGate:
    """测试隔离用：装一个全新的空计数器并返回。"""
    global _GATE
    with _GATE_LOCK:
        _GATE = InflightGate()
        return _GATE
