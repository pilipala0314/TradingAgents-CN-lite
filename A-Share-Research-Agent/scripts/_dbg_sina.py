"""debug: 直接调 sina 拉 002625，看 raw 返回值。"""
import asyncio
import json
import httpx


async def main() -> None:
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {"symbol": "sz002625", "scale": 240, "datalen": 181}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
    text = resp.text
    print("status:", resp.status_code)
    print("text 前 500 字符:", text[:500])
    print()
    data = json.loads(text)
    print("data type:", type(data))
    if isinstance(data, list) and data:
        print("前 3 根:")
        for d in data[:3]:
            print("  ", d)
        print("总根数:", len(data))


if __name__ == "__main__":
    asyncio.run(main())
