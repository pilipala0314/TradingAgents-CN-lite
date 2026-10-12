"""HTTP 栈成本守护单测（无网络）。

锁定两条「按请求付费」前提下的成本约束：
1. HTTP 栈内建重试必须为 0（重试权单一交给 resilience 层），
   否则 urllib3 Retry(3) × resilience Retry(3) 叠加放大 9 次/逻辑调用；
2. 代理失败后的「直连兜底」默认关闭（走代理的域名正是直连被 WAF 封锁的域名，
   兜底必失败，白等超时），异常上抛交由 resilience 重试（隧道重试即换出口 IP）。
"""
import asyncio

import httpx
import pytest
import requests

import datasource.providers._http as http_mod
from datasource.providers._shared import http_client


@pytest.mark.unit
class TestSessionNoBuiltinRetry:
    def test_session_adapters_have_zero_retry(self):
        """requests 栈不得挂 urllib3 Retry（否则与 resilience 层叠加烧钱）。"""
        for prefix in ("http://", "https://"):
            adapter = http_client.EM_SESSION.get_adapter(prefix + "example.com")
            retries = getattr(adapter, "max_retries", None)
            # HTTPAdapter.max_retries=0 → urllib3 Retry(total=0)
            total = getattr(retries, "total", retries)
            assert total == 0


@pytest.mark.unit
class TestRequestsStackDirectFallbackGate:
    def test_default_off_raises_without_direct_retry(self, monkeypatch):
        monkeypatch.setattr(http_client, "EM_MIN_INTERVAL", 0)
        monkeypatch.setattr(http_client, "_PROXY_DIRECT_FALLBACK", False)
        monkeypatch.setattr(
            http_client, "proxy_for",
            lambda url: {"http": "http://p:1", "https": "http://p:1"},
        )
        calls: list[str] = []

        def _boom(*a, **k):
            calls.append("proxy")
            raise requests.exceptions.ConnectionError("tunnel down")

        monkeypatch.setattr(http_client.EM_SESSION, "get", _boom)
        with pytest.raises(requests.exceptions.ConnectionError):
            http_client.em_get("https://push2.eastmoney.com/x")
        assert calls == ["proxy"]  # 无直连兜底 → 仅一次尝试

    def test_enabled_falls_back_to_direct(self, monkeypatch):
        monkeypatch.setattr(http_client, "EM_MIN_INTERVAL", 0)
        monkeypatch.setattr(http_client, "_PROXY_DIRECT_FALLBACK", True)
        monkeypatch.setattr(
            http_client, "proxy_for",
            lambda url: {"http": "http://p:1", "https": "http://p:1"},
        )
        calls: list[str] = []

        def _get(url, **kwargs):
            if kwargs.get("proxies"):
                calls.append("proxy")
                raise requests.exceptions.ConnectionError("tunnel down")
            calls.append("direct")
            return type("R", (), {"status_code": 200})()

        monkeypatch.setattr(http_client.EM_SESSION, "get", _get)
        resp = http_client.em_get("https://push2.eastmoney.com/x")
        assert resp.status_code == 200
        assert calls == ["proxy", "direct"]


@pytest.mark.unit
class TestHttpxStackDirectFallbackGate:
    @staticmethod
    def _patch(monkeypatch, *, fallback: bool):
        monkeypatch.setattr(http_mod, "_PROXY_DIRECT_FALLBACK", fallback)

        class _Pool:
            generation = 0

            def should_proxy(self, url):
                return True

            def proxy_for(self, url):
                return {"https": "http://p:1"}

        monkeypatch.setattr(http_mod, "get_proxy_pool", lambda: _Pool())

        async def _no_throttle(url):
            return None

        monkeypatch.setattr(http_mod, "_throttle_if_eastmoney", _no_throttle)

    def test_default_off_raises(self, monkeypatch):
        self._patch(monkeypatch, fallback=False)

        class _Client:
            async def request(self, *a, **k):
                raise httpx.ConnectError("tunnel down")

        async def _get_client(url=None):
            return _Client()

        monkeypatch.setattr(http_mod, "_get_client", _get_client)

        async def _main():
            await http_mod._request("GET", "https://push2.eastmoney.com/x")

        with pytest.raises(httpx.TransportError):
            asyncio.run(_main())

    def test_enabled_uses_direct_client(self, monkeypatch):
        self._patch(monkeypatch, fallback=True)

        calls: list[str] = []

        class _ProxyClient:
            async def request(self, *a, **k):
                calls.append("proxy")
                raise httpx.ConnectError("tunnel down")

        class _DirectClient:
            async def request(self, *a, **k):
                calls.append("direct")
                return httpx.Response(200, request=httpx.Request("GET", "https://x"))

        async def _get_client(url=None):
            return _ProxyClient()

        async def _get_direct():
            return _DirectClient()

        monkeypatch.setattr(http_mod, "_get_client", _get_client)
        monkeypatch.setattr(http_mod, "_get_direct_client", _get_direct)

        async def _main():
            return await http_mod._request("GET", "https://push2.eastmoney.com/x")

        resp = asyncio.run(_main())
        assert resp.status_code == 200
        assert calls == ["proxy", "direct"]
