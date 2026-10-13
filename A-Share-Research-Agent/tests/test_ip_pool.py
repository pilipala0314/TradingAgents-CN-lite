"""IP 池 / 代理路由单测（无网络，验证配置加载与白名单路由）。"""
import pytest

from datasource.resilience.ip_pool import ProxyPool, _mask


@pytest.mark.unit
class TestProxyPoolConfig:
    def test_unconfigured_returns_none(self, monkeypatch):
        monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
        monkeypatch.delenv("DATASOURCE_PROXY_LIST", raising=False)
        monkeypatch.delenv("DATASOURCE_PROXY_API", raising=False)
        pool = ProxyPool()
        assert pool.is_configured() is False
        assert pool.proxy_for("https://push2.eastmoney.com/x") is None
        assert pool.should_proxy("https://push2.eastmoney.com/x") is False

    def test_tunnel_proxy(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", "http://user:pass@proxy.example.com:8080")
        monkeypatch.delenv("DATASOURCE_PROXY_LIST", raising=False)
        pool = ProxyPool()
        assert pool.is_configured() is True
        # 东财走代理
        p = pool.proxy_for("https://push2.eastmoney.com/api/qt/clist/get")
        assert p == {"http": "http://user:pass@proxy.example.com:8080",
                     "https": "http://user:pass@proxy.example.com:8080"}
        # 非白名单域名直连
        assert pool.proxy_for("https://hq.sinajs.cn/list=x") is None

    def test_proxy_list_rotation(self, monkeypatch):
        monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
        monkeypatch.setenv("DATASOURCE_PROXY_LIST",
                           '["http://ip1:8080", "http://ip2:8080"]')
        pool = ProxyPool()
        assert pool.is_configured() is True
        p1 = pool.proxy_for("https://push2.eastmoney.com/x")
        p2 = pool.proxy_for("https://push2.eastmoney.com/x")
        assert p1["http"] != p2["http"]  # 轮换
        assert p1["http"] in ("http://ip1:8080", "http://ip2:8080")

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY_ENABLED", "false")
        monkeypatch.setenv("DATASOURCE_PROXY", "http://user:pass@proxy.example.com:8080")
        pool = ProxyPool()
        assert pool.proxy_for("https://push2.eastmoney.com/x") is None

    def test_custom_hosts_whitelist(self, monkeypatch):
        monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
        monkeypatch.setenv("DATASOURCE_PROXY_LIST", '["http://ip1:8080"]')
        monkeypatch.setenv("DATASOURCE_PROXY_HOSTS", "eastmoney.com,cls.cn")
        pool = ProxyPool()
        assert pool.should_proxy("https://push2.eastmoney.com/x") is True
        assert pool.should_proxy("https://www.cls.cn/api") is True
        assert pool.should_proxy("https://hq.sinajs.cn/list=x") is False

    def test_describe_shape(self, monkeypatch):
        monkeypatch.setenv("DATASOURCE_PROXY", "http://user:pass@proxy.example.com:8080")
        pool = ProxyPool()
        d = pool.describe()
        assert d["tunnel"] == "http://user:***@proxy.example.com:8080"  # 脱敏
        assert d["hosts"] == ["eastmoney.com"]

    def test_suffix_match_rejects_lookalike(self, monkeypatch):
        """白名单后缀匹配：eastmoney.com 命中子域，但不命中 eastmoney.com.evil.com。"""
        monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
        monkeypatch.setenv("DATASOURCE_PROXY_LIST", '["http://ip1:8080"]')
        pool = ProxyPool()
        assert pool.should_proxy("https://push2.eastmoney.com/x") is True
        assert pool.should_proxy("https://eastmoney.com.evil.com/x") is False
        assert pool.should_proxy("https://noteastmoney.com/x") is False

    def test_reload_bumps_generation_and_rereads_env(self, monkeypatch):
        """reload() 重读开关/代理并递增 generation（供 _http 丢弃旧 client）。"""
        monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
        monkeypatch.delenv("DATASOURCE_PROXY_LIST", raising=False)
        pool = ProxyPool()
        gen0 = pool.generation
        assert pool.is_configured() is False

        monkeypatch.setenv("DATASOURCE_PROXY", "http://user:pass@proxy.example.com:8080")
        pool.reload()
        assert pool.generation == gen0 + 1
        assert pool.is_configured() is True

        monkeypatch.setenv("DATASOURCE_PROXY_ENABLED", "false")
        pool.reload()
        assert pool.should_proxy("https://push2.eastmoney.com/x") is False  # 开关重读生效

    def test_http_layer_rotates_proxy_clients(self, monkeypatch):
        """列表模式下 _http._get_client 每次调用命中不同代理 client（轮换真实生效）。"""
        import asyncio

        import datasource.providers._http as http_mod
        import datasource.resilience.ip_pool as ip_mod

        monkeypatch.setenv("DATASOURCE_PROXY_LIST", '["http://ip1:8080", "http://ip2:8080"]')
        monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
        monkeypatch.setattr(ip_mod, "_pool", None)  # 重置全局池以读取新 env
        monkeypatch.setattr(http_mod, "_proxy_clients", {})
        monkeypatch.setattr(http_mod, "_proxy_clients_generation", -1)

        async def run():
            c1 = await http_mod._get_client("https://push2.eastmoney.com/a")
            c2 = await http_mod._get_client("https://push2.eastmoney.com/b")
            return c1, c2

        c1, c2 = asyncio.run(run())
        assert c1 is not c2  # 轮换到第二个代理 → 另一个 client
        assert len(http_mod._proxy_clients) == 2

        # 清理：关闭临时 client，避免资源告警
        async def cleanup():
            for c in list(http_mod._proxy_clients.values()):
                await c.aclose()

        asyncio.run(cleanup())
        monkeypatch.setattr(ip_mod, "_pool", None)
        monkeypatch.setattr(http_mod, "_proxy_clients", {})
        monkeypatch.setattr(http_mod, "_proxy_clients_generation", -1)


@pytest.mark.unit
class TestMask:
    def test_masks_password(self):
        assert _mask("http://user:secret@host:8080") == "http://user:***@host:8080"

    def test_no_password_unchanged(self):
        assert _mask("http://host:8080") == "http://host:8080"


def _extract_api_pool(monkeypatch) -> ProxyPool:
    """构造仅配提取 API 的池（清空隧道/列表）。"""
    monkeypatch.delenv("DATASOURCE_PROXY", raising=False)
    monkeypatch.delenv("DATASOURCE_PROXY_LIST", raising=False)
    monkeypatch.setenv("DATASOURCE_PROXY_API", "http://api.example/get")
    pool = ProxyPool()
    return pool


@pytest.mark.unit
class TestDynamicExtraction:
    def test_parse_text_and_json(self):
        import datasource.resilience.ip_pool as m

        p = m.ProxyPool()
        # 纯文本（巨量 result_type=text&split=1）
        assert p._parse_extract_response("1.2.3.4:8080\n5.6.7.8:9090\n") == [
            "1.2.3.4:8080", "5.6.7.8:9090"
        ]
        # 巨量 JSON（对象项 ip/port）
        js = '{"code":200,"data":{"proxy_list":[{"ip":"9.9.9.9","port":3000}]}}'
        assert p._parse_extract_response(js) == ["9.9.9.9:3000"]
        # JSON 字符串项
        js2 = '{"data":[{"proxy":"8.8.8.8:80"}]}'
        assert p._parse_extract_response(js2) == ["8.8.8.8:80"]
        # 错误 / 空响应
        assert p._parse_extract_response('{"code":10003,"msg":"余额不足"}') == []
        assert p._parse_extract_response("") == []

    def test_lazy_extract_on_first_request(self, monkeypatch):
        pool = _extract_api_pool(monkeypatch)
        assert pool.is_configured() is True
        assert pool._proxies == []  # 惰性：初始不提取

        calls = {"n": 0}

        def fake_extract():
            calls["n"] += 1
            pool._proxies = [{"http": "http://1.1.1.1:80", "https": "http://1.1.1.1:80"}]
            pool._proxy_acquired_at = __import__("time").time()
            pool._extractions += 1

        monkeypatch.setattr(pool, "_extract_once", fake_extract)
        p = pool.proxy_for("https://push2.eastmoney.com/x")
        assert p["http"] == "http://1.1.1.1:80"
        assert calls["n"] == 1
        # TTL 内复用，不再提取
        pool.proxy_for("https://push2.eastmoney.com/x")
        assert calls["n"] == 1

    def test_ttl_expiry_reextracts(self, monkeypatch):
        pool = _extract_api_pool(monkeypatch)
        pool._proxies = [{"http": "http://old:80", "https": "http://old:80"}]
        pool._proxy_acquired_at = 0.0  # 很久以前 → 已过期
        calls = {"n": 0}

        def fake_extract():
            calls["n"] += 1
            pool._proxies = [{"http": "http://new:80", "https": "http://new:80"}]
            pool._proxy_acquired_at = __import__("time").time()

        monkeypatch.setattr(pool, "_extract_once", fake_extract)
        p = pool.proxy_for("https://push2.eastmoney.com/x")
        assert p["http"] == "http://new:80"
        assert calls["n"] == 1

    def test_failure_removes_and_next_request_extracts(self, monkeypatch):
        import time as _t

        pool = _extract_api_pool(monkeypatch)
        pool._proxies = [{"http": "http://dead:80", "https": "http://dead:80"}]
        pool._proxy_acquired_at = _t.time()

        # 上报失效 → 摘除
        pool.report_proxy_failure({"http": "http://dead:80", "https": "http://dead:80"})
        assert pool._proxies == []

        def fake_extract():
            pool._proxies = [{"http": "http://alive:80", "https": "http://alive:80"}]
            pool._proxy_acquired_at = _t.time()

        monkeypatch.setattr(pool, "_extract_once", fake_extract)
        p = pool.proxy_for("https://push2.eastmoney.com/x")
        assert p["http"] == "http://alive:80"

    def test_non_em_domain_does_not_extract(self, monkeypatch):
        pool = _extract_api_pool(monkeypatch)
        def boom():
            raise AssertionError("非白名单域名不应触发提取")
        monkeypatch.setattr(pool, "_extract_once", boom)
        assert pool.proxy_for("https://hq.sinajs.cn/x") is None
        assert pool._proxies == []
