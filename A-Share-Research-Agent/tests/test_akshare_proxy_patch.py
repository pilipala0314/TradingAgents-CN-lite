"""akshare 东财请求代理注入单测（无网络）。

背景：akshare 内部走 requests/curl_cffi，本身不进 datasource 代理栈 →
东财系接口（成分股/涨停池/资金流）在 WAF 封锁期全挂。
本补丁让 akshare 的东财请求也经 IP 池（付费隧道）出口。
"""
import pytest
import requests
from requests.sessions import Session

from datasource.providers import akshare as ak_mod
from datasource.resilience import ip_pool

_PROXY_ENV = "http://u:p@proxy.example:8080"


def _fresh_pool(monkeypatch):
    """强制重建 IP 池单例（读取当前环境变量），并登记 teardown 复位。"""
    monkeypatch.setattr(ip_pool, "_pool", None)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)


@pytest.mark.unit
class TestEmProxiesFor:
    def test_eastmoney_gets_proxy_and_counted(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", _PROXY_ENV)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", False)
        _fresh_pool(monkeypatch)
        try:
            got = ak_mod._em_proxies_for("https://push2.eastmoney.com/api/qt/clist/get")
            assert got and "proxy.example" in str(got)
            pool = ip_pool.get_proxy_pool()
            before = pool.routed_requests
            ak_mod._em_proxies_for("https://datacenter-web.eastmoney.com/x")
            assert pool.routed_requests == before + 1  # 计入付费请求统计
        finally:
            ip_pool._pool = None

    def test_non_eastmoney_not_proxied(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", _PROXY_ENV)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", False)
        _fresh_pool(monkeypatch)
        try:
            assert ak_mod._em_proxies_for("https://hq.sinajs.cn/list=sh600519") is None
        finally:
            ip_pool._pool = None

    def test_bypass_flag_disables(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", _PROXY_ENV)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", True)
        _fresh_pool(monkeypatch)
        try:
            assert ak_mod._em_proxies_for("https://push2.eastmoney.com/x") is None
        finally:
            ip_pool._pool = None


@pytest.mark.unit
class TestEmWillProxy:
    """路径选择判据（不计数）：配代理 → requests；未配 → curl_cffi。"""

    def test_true_when_proxy_configured(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", _PROXY_ENV)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", False)
        _fresh_pool(monkeypatch)
        try:
            pool = ip_pool.get_proxy_pool()
            before = pool.routed_requests
            assert ak_mod._em_will_proxy("https://17.push2.eastmoney.com/api/qt/clist/get") is True
            # 关键：路径选择判定不得计入付费请求（只有真正发请求才计）
            assert pool.routed_requests == before
        finally:
            ip_pool._pool = None

    def test_false_without_proxy(self, monkeypatch):
        monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", False)
        _fresh_pool(monkeypatch)
        try:
            assert ak_mod._em_will_proxy("https://push2.eastmoney.com/x") is False
            assert ak_mod._em_will_proxy("https://hq.sinajs.cn/x") is False
        finally:
            ip_pool._pool = None

    def test_false_when_bypass(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", _PROXY_ENV)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", True)
        _fresh_pool(monkeypatch)
        try:
            assert ak_mod._em_will_proxy("https://push2.eastmoney.com/x") is False
        finally:
            ip_pool._pool = None


@pytest.mark.unit
class TestSessionRequestProxyInjection:
    def test_patch_installed(self):
        ak_mod._init_akshare_anti_scraping_patch()
        assert getattr(Session, "_ashare_agent_proxy_patched", False) is True
        assert getattr(requests, "_ashare_agent_headers_patched", False) is True

    def test_request_injects_proxy_for_eastmoney(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", _PROXY_ENV)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", False)
        _fresh_pool(monkeypatch)
        ak_mod._init_akshare_anti_scraping_patch()

        captured: dict = {}

        class _FakeResp:
            status_code = 200
            content = b"{}"
            text = "{}"

            def json(self):
                return {}

        def fake_send(self, request, **kwargs):
            captured["proxies"] = kwargs.get("proxies")
            return _FakeResp()

        monkeypatch.setattr(Session, "send", fake_send)
        try:
            Session().request("GET", "https://push2.eastmoney.com/api/qt/clist/get")
        finally:
            ip_pool._pool = None
        assert captured["proxies"] and "proxy.example" in str(captured["proxies"])

    def test_request_skips_when_proxies_explicit(self, monkeypatch):
        """已显式传 proxies 的调用（如 _shared.http_client.em_get）不得重复注入。"""
        monkeypatch.setenv("DATASOURCE_PROXY", _PROXY_ENV)
        monkeypatch.setattr(ak_mod, "_EM_BYPASS_PROXY", False)
        _fresh_pool(monkeypatch)
        ak_mod._init_akshare_anti_scraping_patch()

        captured: dict = {}

        class _FakeResp:
            status_code = 200
            content = b"{}"
            text = "{}"

            def json(self):
                return {}

        def fake_send(self, request, **kwargs):
            captured["proxies"] = kwargs.get("proxies")
            return _FakeResp()

        monkeypatch.setattr(Session, "send", fake_send)
        explicit = {"http": "http://explicit:1", "https": "http://explicit:1"}
        try:
            Session().request("GET", "https://push2.eastmoney.com/x", proxies=explicit)
        finally:
            ip_pool._pool = None
        assert captured["proxies"] == explicit


@pytest.mark.unit
class TestCurlCffiFallbackClassification:
    """curl_cffi 回退判据：只有编程错才回退，传输类失败不回退（避免双倍消费代理）。"""

    def test_programming_errors_fallback(self):
        assert ak_mod._curl_cffi_should_fallback(TypeError("unexpected kwarg proxies")) is True
        assert ak_mod._curl_cffi_should_fallback(AttributeError("no attr")) is True
        assert ak_mod._curl_cffi_should_fallback(ValueError("bad value")) is True

    def test_transport_errors_no_fallback(self):
        assert ak_mod._curl_cffi_should_fallback(ConnectionError("tunnel down")) is False
        assert ak_mod._curl_cffi_should_fallback(TimeoutError("slow")) is False
        assert ak_mod._curl_cffi_should_fallback(OSError("reset")) is False
