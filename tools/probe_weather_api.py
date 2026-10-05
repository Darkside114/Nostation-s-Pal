"""验证天气数据源：Open-Meteo（无需 Key）与和风天气 QWeather（需 Key）。

要验证的完整链路：
  用户输入"精确到区"的地址  ->  地理编码得到经纬度  ->  查当前天气  ->  拿到温度/湿度

本脚本只做网络请求验证，不碰设备。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

TIMEOUT = 25


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "nostation-pal-test"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Open-Meteo：免费、无需 API Key
# ---------------------------------------------------------------------------
def test_open_meteo(place):
    print("=== Open-Meteo（无需 Key）===")
    print("  查询地点: %s" % place)

    # 1) 地理编码
    url = ("https://geocoding-api.open-meteo.com/v1/search?"
           + urllib.parse.urlencode({
               "name": place, "count": 5, "language": "zh", "format": "json"}))
    print("  地理编码: %s" % url)
    try:
        g = get_json(url)
    except Exception as exc:
        print("  !! 地理编码失败: %s" % exc)
        return None

    results = g.get("results") or []
    if not results:
        print("  !! 没有匹配地点（Open-Meteo 对中文区名的支持有限）")
        return None
    print("  匹配到 %d 个地点:" % len(results))
    for i, r in enumerate(results[:5]):
        print("    [%d] %s / %s / %s  (%.3f, %.3f)  人口=%s" % (
            i, r.get("name"), r.get("admin1"), r.get("admin2") or r.get("admin3") or "-",
            r.get("latitude"), r.get("longitude"), r.get("population")))

    hit = results[0]
    lat, lon = hit["latitude"], hit["longitude"]

    # 2) 当前天气（含相对湿度）
    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode({
               "latitude": lat, "longitude": lon,
               "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code",
               "timezone": "auto"}))
    print("  天气查询: %s" % url)
    try:
        w = get_json(url)
    except Exception as exc:
        print("  !! 天气查询失败: %s" % exc)
        return None
    cur = w.get("current") or {}
    print("  --- 结果 ---")
    print("    地点    : %s %s" % (hit.get("name"), hit.get("admin1") or ""))
    print("    坐标    : %.4f, %.4f" % (lat, lon))
    print("    温度    : %s %s" % (cur.get("temperature_2m"), w.get("current_units", {}).get("temperature_2m")))
    print("    湿度    : %s %s" % (cur.get("relative_humidity_2m"), w.get("current_units", {}).get("relative_humidity_2m")))
    print("    体感    : %s" % cur.get("apparent_temperature"))
    print("    观测时间: %s" % cur.get("time"))
    return {"temp": cur.get("temperature_2m"), "humidity": cur.get("relative_humidity_2m"),
            "name": hit.get("name"), "lat": lat, "lon": lon,
            "time": cur.get("time"), "source": "Open-Meteo"}


# ---------------------------------------------------------------------------
# 和风天气 QWeather：需要 API Key；支持 LocationID 精确到区
# ---------------------------------------------------------------------------
def test_qweather(place, key, host=None):
    print()
    print("=== 和风天气 QWeather ===")
    if not key:
        print("  未提供 Key（设置环境变量 QWEATHER_KEY 后再试）")
        print("  Key 可免费申请: https://dev.qweather.com/")
        return None
    host = host or os.environ.get("QWEATHER_HOST", "devapi.qweather.com")
    print("  查询地点: %s   API Host: %s" % (place, host))

    url = ("https://%s/geo/v2/city/lookup?" % host
           + urllib.parse.urlencode({"location": place, "number": 5, "lang": "zh"}))
    try:
        g = get_json(url, {"X-QW-Api-Key": key, "User-Agent": "nostation-pal-test"})
    except Exception as exc:
        print("  !! 城市查询失败: %s" % exc)
        return None
    if g.get("code") != "200":
        print("  !! 接口返回 code=%s" % g.get("code"))
        return None
    locs = g.get("location") or []
    print("  匹配到 %d 个地点:" % len(locs))
    for i, l in enumerate(locs[:5]):
        print("    [%d] %s / %s / %s / %s  LocationID=%s" % (
            i, l.get("name"), l.get("adm1"), l.get("adm2"), l.get("name"),
            l.get("id")))
    loc = locs[0]

    url = ("https://%s/v7/weather/now?" % host
           + urllib.parse.urlencode({"location": loc["id"], "lang": "zh"}))
    try:
        w = get_json(url, {"X-QW-Api-Key": key, "User-Agent": "nostation-pal-test"})
    except Exception as exc:
        print("  !! 天气查询失败: %s" % exc)
        return None
    now = w.get("now") or {}
    print("  --- 结果 ---")
    print("    地点    : %s %s %s" % (loc.get("adm1"), loc.get("adm2"), loc.get("name")))
    print("    温度    : %s C" % now.get("temp"))
    print("    湿度    : %s %%" % now.get("humidity"))
    print("    天气    : %s" % now.get("text"))
    print("    观测时间: %s" % now.get("obsTime"))
    return {"temp": float(now.get("temp", 0)), "humidity": float(now.get("humidity", 0)),
            "name": loc.get("name"), "source": "QWeather",
            "time": now.get("obsTime")}


def main():
    place = sys.argv[1] if len(sys.argv) > 1 else "浦东新区"
    print("测试地点: %s" % place)
    print()
    om = test_open_meteo(place)
    qw = test_qweather(place, os.environ.get("QWEATHER_KEY"))
    print()
    print("=== 汇总 ===")
    for label, r in (("Open-Meteo", om), ("QWeather", qw)):
        if r:
            print("  %-12s %s C / %s %%   (%s)" % (
                label, r["temp"], r["humidity"], r["name"]))
        else:
            print("  %-12s 不可用" % label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
