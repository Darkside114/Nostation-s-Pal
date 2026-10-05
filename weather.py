#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""天气数据获取：按用户输入的地址（精确到区）查真实温度与湿度。

== 设计要点 ==
  1. **两个数据源可选**
     * Open-Meteo：免费、无需 API Key，默认
     * 和风天气 QWeather：需要用户自己的 Key（免费申请），国内速度快、
       区县级精度好

  2. **失败要能自愈**：urllib 在 Windows 上会读系统代理。很多用户装了代理
     软件（Clash 之类）但没在运行，此时连接被立即拒绝、请求静默失败。
     所以第一次按系统设置走，失败就**显式禁用代理**再试一次。
     （这个坑在自动更新那边已经踩过一次，见 companion_app.fetch_latest_release）

  3. **地理编码只做一次并缓存**：地址不会天天变，但天气每 15 分钟要查一次，
     没必要每次都重新地理编码。

  4. **区县级精度**：用户输入"浦东新区"这类区名时，Open-Meteo 的中文
     地理编码有时不够准，所以：
       * 允许用户填写"城市 区"（如"上海 浦东新区"）以提高命中率
       * 结果里带回解析到的行政层级，界面显示出来让用户核对

用法：
    python weather.py 浦东新区              # 命令行试一下
    python weather.py 上海 浦东新区
"""
import gzip
import json
import os
import time
import urllib.parse
import urllib.request

TIMEOUT = 20
GEO_CACHE_TTL = 7 * 24 * 3600      # 地理编码结果缓存 7 天


class WeatherError(Exception):
    """取天气失败（网络、接口、或地址解析不出来）。"""


# ---------------------------------------------------------------------------
# 网络：带"代理失败则直连重试"
# ---------------------------------------------------------------------------
def _open(url, headers=None):
    """先按系统代理请求，失败再显式绕过代理重试一次。返回解析后的 JSON。

    注意：和风天气会对响应体做 **gzip 压缩**（即使请求里不声明 Accept-Encoding，
    它的错误响应也可能是 gzip）。直接用 json.loads 会报
    「Expecting value: line 1 column 1 (char 0)」—— 所以这里统一检测并解压。
    """
    hdrs = {"User-Agent": "NostationsPal/2.1",
            "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last = None
    for use_proxy in (True, False):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            if use_proxy:
                opener = urllib.request.build_opener()
            else:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:
            last = exc
    raise WeatherError("网络请求失败：{}".format(last))


# ---------------------------------------------------------------------------
# Open-Meteo（免费、无需 Key）
# ---------------------------------------------------------------------------
def _om_geocode(place):
    """地理编码。会做多轮降级尝试，提高中文区县级地址的命中率。

    为什么要降级：Open-Meteo 的地理编码对"上海市浦东新区"这类
    **城市+区** 的连写常常查不到，但对单独一个"浦东新区"就能命中。
    所以依次尝试：
        1. 原串
        2. 去掉空格/逗号后的串
        3. 逐段单独查（"上海"、"浦东新区"），并按行政层级+人口挑最好的
    """
    def query(name):
        url = ("https://geocoding-api.open-meteo.com/v1/search?"
               + urllib.parse.urlencode({"name": name, "count": 5,
                                         "language": "zh", "format": "json"}))
        try:
            data = _open(url)
        except WeatherError:
            return []
        return data.get("results") or []

    def score(r, prefer=None):
        """分数越高越可能是我们要的地点。

        排序要点（都踩过坑）：
          * **不要用人口决定**：输入"上海 浦东新区"时会选中"上海"本身。
          * **名字匹配要精确**：之前用 `p in name or name in p` 做模糊匹配，
            导致查"深圳"时 `"北京" in "北京市"` 之类的子串关系给错误结果加分，
            直接把福建泉州的"深圳"村排到了广东深圳前面。
            现在只认精确相等，其次是"名字就是目标市/区"这种明确关系。
          * **同名地点要偏向中国大陆**：搜"深圳"会同时出现台湾桃园的同名点。
        """
        s = 0.0
        name = (r.get("name") or "").strip()
        a1 = (r.get("admin1") or "")
        a2 = (r.get("admin2") or "")
        cc = (r.get("country_code") or "")

        # 1) 名字与输入片段的关系（只认精确相等，避免子串误加分）
        if prefer:
            for p in prefer:
                p = (p or "").strip()
                if not p:
                    continue
                if p == name:
                    s += 10.0
                elif name == p + "市" or name == p + "区" or name == p + "县":
                    s += 8.0
                elif p == name + "市" or p == name + "区":
                    s += 8.0

        # 2) 中国大陆优先
        if cc == "CN":
            s += 6.0
        if "臺灣" in a1 or "台湾" in a1 or cc == "TW":
            s -= 8.0

        # 3) 行政层级：有二级/三级行政区的更可能是"区县"级
        if a2:
            s += 3.0
        if r.get("admin3"):
            s += 2.0

        # 4) 人口只作微弱参考（越大越不像"区"，所以加分递减）
        try:
            pop = int(r.get("population") or 0)
            if pop:
                s += 1.0 / (1.0 + pop / 1000000.0)
        except Exception:
            pass
        return s

    raw = (place or "").strip()
    tries = [raw]
    squeezed = raw.replace(" ", "").replace(",", "").replace("，", "")
    if squeezed and squeezed != raw:
        tries.append(squeezed)
    # 拆段单独查
    parts = [p for p in raw.replace("，", " ").replace(",", " ").split() if p]
    if len(parts) > 1:
        tries.extend(parts)
    # 中文地名常见后缀：把"XX市XX区"拆出尾部再试一次
    for m in ("区", "县"):
        idx = squeezed.rfind(m)
        if idx > 0:
            tail = squeezed[max(0, idx - 4):idx + 1]
            if tail and tail not in tries:
                tries.append(tail)

    best = None
    best_score = -1.0
    for t in tries:
        if not t:
            continue
        for r in query(t):
            sc = score(r, prefer=parts or [raw])
            if sc > best_score:
                best_score = sc
                best = r
    return best


def fetch_open_meteo(place):
    """返回 dict(temp, humidity, name, admin, source, observed)。

    Open-Meteo 的中文地理编码**只有市级粒度**（实测"朝阳区/南山区/西湖区"
    直接查都是 0 条结果）。所以按「城市+区名」输入时：
      1. 先试区名（少数被收录的区，如"浦东新区"能命中，那是真区级）
      2. 不行就用城市名（得到市级数据）
    并把实际精度如实写进 precision/note，由界面提示用户。
    """
    coords = _parse_coords(place)
    note = ""
    hit = None
    used = ""
    if coords:
        lat, lon = coords
        hit = {"name": "%.3f,%.3f" % (lat, lon), "admin1": "", "admin2": "",
               "admin3": "", "latitude": lat, "longitude": lon}
    else:
        splits = split_place(place)
        # 依次尝试：区名 -> 城市名 -> 原串
        tries = []
        for city, district in splits:
            if district and district not in tries:
                tries.append(district)
            if city and city not in tries:
                tries.append(city)
        raw = (place or "").strip()
        if raw and raw not in tries:
            tries.append(raw)
        for t in tries:
            hit = _om_geocode(t)
            if hit:
                used = t
                break
    if not hit:
        raise WeatherError(
            "地址解析不出结果：{}（Open-Meteo 中文只有市级精度，"
            "请按「城市+区名」填写，例如 成都锦江区、北京朝阳区；"
            "想要真正的区级数据请改用和风天气）".format(place))
    lat, lon = hit["latitude"], hit["longitude"]
    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode({
               "latitude": lat, "longitude": lon,
               "current": "temperature_2m,relative_humidity_2m",
               "timezone": "auto"}))
    data = _open(url)
    cur = data.get("current") or {}
    if cur.get("temperature_2m") is None:
        raise WeatherError("接口没有返回温度数据")
    admin = " ".join(x for x in (hit.get("admin1"), hit.get("admin2"),
                                 hit.get("admin3")) if x)
    name = hit.get("name") or place
    # 精度判断：名字带"区/县"且查到二级行政区 -> 区级；否则市级
    precision = "district" if (hit.get("admin2")
                               and any(x in name for x in ("区", "县"))) \
        else "city"
    if coords:
        precision = "coordinates"
    if precision == "city":
        note = ("Open-Meteo 中文只到市级，当前用「{}」代替；"
                "要区级精度请改用和风天气，或直接填经纬度".format(name))
    return {
        "temp": float(cur["temperature_2m"]),
        "humidity": float(cur.get("relative_humidity_2m") or 0),
        "name": name,
        "admin": admin,
        "lat": lat, "lon": lon,
        "source": "Open-Meteo",
        "precision": precision,
        "note": note,
        "observed": cur.get("time") or "",
    }


# ---------------------------------------------------------------------------
# 和风天气 QWeather（需要 Key）
# ---------------------------------------------------------------------------
# 和风 2024 起，免费订阅**不能**用 devapi.qweather.com（会返回
# 403 Invalid Host），必须用控制台里给每个账号分配的专属 Host。
#
# 凭据**不写进代码**：和风 Key 绑定账号、有调用配额，把别人的凭据
# 硬编码进来会消耗对方额度。所以这里从本地私有文件 app_secrets.py
# （已被 .gitignore 忽略）读取；读不到就为空 —— 此时和风不可用，
# 但免费数据源 Open-Meteo 照常工作。见 tools/apply_secrets.py。
try:
    from app_secrets import QWEATHER_KEY as DEFAULT_QW_KEY
    from app_secrets import QWEATHER_HOST as DEFAULT_QW_HOST
except Exception:
    DEFAULT_QW_KEY = ""
    DEFAULT_QW_HOST = ""


def _qw_host():
    """专属 Host。注意空字符串也要兜底 —— 老版本配置里可能存着空值，
    如果直接用它就会覆盖默认值，导致 403 Invalid Host。"""
    return (os.environ.get("QWEATHER_HOST") or "").strip() or DEFAULT_QW_HOST


def _qw_key(key):
    return (key or "").strip() or os.environ.get("QWEATHER_KEY", "") \
        or DEFAULT_QW_KEY


# 中文行政区划后缀，用于把「城市+区名」拆开
_DISTRICT_SUFFIX = ("新区", "区", "县", "旗")
_CITY_SUFFIX = ("自治区", "自治州", "地区", "市", "省", "盟")


def split_place(place):
    """把用户输入拆成 (城市, 区县)。**返回所有合理拆分**。

    约定用户按「城市+区名」填写，三种写法都要支持：
        "成都锦江区"   -> 城市"成都"    + 区"锦江区"
        "成都 锦江区"  -> 城市"成都"    + 区"锦江区"
        "北京朝阳区"   -> 城市"北京"    + 区"朝阳区"
    只填区名（"锦江区"）也允许，此时城市为空。

    为什么返回候选列表而不是唯一结果：中文没有分隔符时，
    "成都锦江区"到底是"成都"+"锦江区"还是"成都锦"+"江区"无法只靠字符串判断，
    最终要靠和风返回的 adm1/adm2 来验证。所以这里把所有合理拆法都给出，
    由 fetch_qweather 逐个查询后挑最匹配的那个。
    """
    raw = (place or "").strip()
    for ch in ("，", ",", "、", "/", "\\", "|"):
        raw = raw.replace(ch, " ")
    raw = " ".join(raw.split())
    parts = [p for p in raw.split(" ") if p]

    cands = []
    if len(parts) >= 2:
        cands.append((parts[0], "".join(parts[1:])))
        cands.append(("".join(parts[:-1]), parts[-1]))
        for p in parts:
            cands.append(("", p))
        return cands

    s = parts[0] if parts else ""
    if not s:
        return [("", "")]

    # 找区/县后缀的位置，把"后缀前 1~4 字"当作区名，其余当作城市
    for suf in _DISTRICT_SUFFIX:
        idx = s.rfind(suf)
        if idx <= 0:
            continue
        end = idx + len(suf)                      # 区名结束位置（不含）
        for dlen in (4, 3, 2, 1):                 # 区名核心字数（不含后缀）
            start = end - len(suf) - dlen
            if start < 0:
                continue
            district = s[start:end]
            city = s[:start]
            cands.append((city, district))
        cands.append(("", s[start:end] if start >= 0 else s))
        break

    # 没有区/县后缀：整串当一个地名
    cands.append(("", s))
    cands.append((s, ""))

    seen = set()
    out = []
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _strip_suffix(text, suffixes):
    for suf in suffixes:
        if text.endswith(suf) and len(text) > len(suf):
            return text[: -len(suf)]
    return text


def _qw_candidates(place):
    """给和风城市查询生成候选写法。

    和风的 city/lookup 是"一个地名"查询：把"成都锦江区"整串丢进去常常查不到，
    而单独"锦江区"就能命中（返回 name=锦江, adm2=成都）。
    所以优先用**拆出来的区县名**去查，再补上城市名和原串。
    """
    out = []
    for city, district in split_place(place):
        if district:
            out.append(district)
        if city:
            out.append(city)
    raw = (place or "").strip()
    if raw:
        out.append(raw)
        squeezed = raw.replace(" ", "")
        if squeezed != raw:
            out.append(squeezed)

    seen = set()
    res = []
    for t in out:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            res.append(t)
    return res


def fetch_qweather(place, key=None, host=None):
    key = _qw_key(key)
    if not key:
        raise WeatherError("没有填写和风天气 API Key")
    host = (host or "").strip() or _qw_host()

    splits = split_place(place)

    # 收集所有候选地，最后统一打分挑一个。
    # 为什么要"先收集再打分"：和风对同名地会返回多条
    # （朝阳区 -> 北京朝阳 / 辽宁朝阳龙城；南山区 -> 深圳南山 / 鹤岗南山 …），
    # 一查到就取第一条很容易挑到冷门的那个。
    pool = []
    for cand in _qw_candidates(place):
        url = ("https://%s/geo/v2/city/lookup?" % host
               + urllib.parse.urlencode({"location": cand, "number": 10,
                                         "lang": "zh"}))
        try:
            data = _open(url, {"X-QW-Api-Key": key})
        except WeatherError:
            # 某个候选写法查不到（和风会直接返回 400）是正常的，
            # 继续试下一个写法，不要当成致命错误。
            continue
        if data.get("code") != "200":
            continue
        for l in (data.get("location") or []):
            l["_cand"] = cand
            pool.append(l)

    loc = None
    if pool:
        def score(l):
            s = 0.0
            if (l.get("country") or "").startswith("中国"):
                s += 2.0
            a1 = l.get("adm1") or ""
            a2 = l.get("adm2") or ""
            nm = l.get("name") or ""
            cand = l.get("_cand") or ""
            # 对每一种拆分方案算一次：名字要对得上区，city 要对得上 adm
            best = -1e9
            for city, district in splits:
                t = 0.0
                c = _strip_suffix(city, _CITY_SUFFIX)
                d = _strip_suffix(district, _DISTRICT_SUFFIX)
                if d:
                    if nm == d or cand == district:
                        t += 25.0
                    elif nm.startswith(d) or d.startswith(nm):
                        t += 12.0
                    else:
                        t -= 10.0        # 名字都对不上，基本可以排除
                if c:
                    if c == a2 or c == a1:
                        t += 30.0
                    elif c in a2 or c in a1:
                        t += 18.0
                    else:
                        t -= 15.0        # 城市对不上，明显不是这个
                if cand == district or cand == d:
                    t += 6.0
                best = max(best, t)
            s += best
            # rank 越小越重要（和风给的优先级，15 表示重点城市）
            try:
                s += 5.0 / (1.0 + float(l.get("rank") or 60))
            except Exception:
                pass
            return s

        loc = sorted(pool, key=score, reverse=True)[0]
    if loc is None:
        msg = "地址解析不出结果：{}".format(place)
        if splits and splits[0][0]:
            msg += "（已按「{} + {}」等多种拆法查询，仍无匹配）".format(
                splits[0][0], splits[0][1])
        msg += "。请按「城市+区名」填写，例如 成都锦江区、北京朝阳区。"
        raise WeatherError(msg)

    url = ("https://%s/v7/weather/now?" % host
           + urllib.parse.urlencode({"location": loc["id"], "lang": "zh"}))
    data = _open(url, {"X-QW-Api-Key": key})
    if data.get("code") != "200":
        raise WeatherError("天气查询返回 code={}".format(data.get("code")))
    now = data.get("now") or {}
    return {
        "temp": float(now.get("temp") or 0),
        "humidity": float(now.get("humidity") or 0),
        "name": loc.get("name") or place,
        "admin": " ".join(x for x in (loc.get("adm1"), loc.get("adm2")) if x),
        "lat": float(loc["lat"]) if loc.get("lat") else None,
        "lon": float(loc["lon"]) if loc.get("lon") else None,
        "source": "QWeather",
        "precision": "district",
        "note": "",
        "observed": now.get("obsTime") or "",
    }


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
# == 两个数据源的实际能力（实测，用于界面提示 ==
#   地址输入约定：按「城市+区名」填写，例如 成都锦江区、北京朝阳区。
#   （中文没有分隔符，"成都锦江区"到底怎么切分无法只靠字符串判断，
#    所以 split_place 会给出多种拆法，再由接口返回的 adm1/adm2 验证。）
#
#   Open-Meteo：
#       免费、无需 Key，但**中文地理编码只有市级粒度**。
#       实测"朝阳区/南山区/西湖区/武侯区"直接查都是 0 条结果
#       （只有"浦东新区"这种恰好被收录的例外）。
#       所以用户填区名时会自动降级到该区所属的市，并在结果里标明。
#       想要真正的区级精度，可以填**经纬度**（如 "39.92,116.44"）。
#   和风天气 QWeather：
#       区县级精度好、国内速度快，但需要用户自己申请免费 Key。
# ---------------------------------------------------------------------------

_COORD_RE = None


def _parse_coords(text):
    """支持直接输入经纬度："39.92,116.44" 或 "39.92 116.44"。"""
    global _COORD_RE
    if _COORD_RE is None:
        import re
        _COORD_RE = re.compile(
            r"^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$")
    m = _COORD_RE.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _city_fallback(place):
    """区名查不到时的降级：去掉"区/县"等后缀，用市名再查一次。

    返回 (hit, note)：note 说明做了降级，界面要显示给用户。
    """
    raw = (place or "").strip()
    parts = [p for p in raw.replace("，", " ").replace(",", " ").split() if p]
    cands = []
    for p in parts:
        cands.append(p)
    # 从"上海市浦东新区"里取出可能的市名
    for p in parts:
        for suffix in ("市", "区", "县"):
            idx = p.find(suffix)
            if idx > 0:
                cands.append(p[:idx + 1])
    for c in cands:
        hit = _om_geocode(c)
        if hit:
            return hit, c
    return None, None


def fetch(place, provider="open-meteo", qweather_key="", qweather_host=""):
    """按 provider 取天气。失败抛 WeatherError。"""
    place = (place or "").strip()
    if not place:
        raise WeatherError("没有填写地址")
    if provider == "qweather":
        return fetch_qweather(place, qweather_key, qweather_host or None)
    return fetch_open_meteo(place)


def describe(result):
    """把结果拼成一行便于界面显示/日志。"""
    if not result:
        return ""
    loc = result.get("name") or ""
    if result.get("admin"):
        loc = "{} {}".format(result["admin"], loc)
    tag = "区级" if result.get("precision") == "district" else "市级"
    if result.get("source") == "QWeather":
        tag = "区级"
    return "{:.1f}°C / {:.0f}%  ({}, {}, {})".format(
        result["temp"], result["humidity"], loc, result["source"], tag)


def short_label(result, limit=17):
    """给屏幕用：优先区名，没有就用城市名。"""
    if not result:
        return ""
    name = result.get("name") or ""
    if not name and result.get("admin"):
        name = result["admin"].split()[-1]
    return name[:limit]


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    place = " ".join(args) if args else "浦东新区"
    print("查询地点:", place)
    for prov in ("open-meteo", "qweather"):
        try:
            r = fetch(place, prov, os.environ.get("QWEATHER_KEY", ""))
            print("  %-12s %s" % (prov, describe(r)))
        except WeatherError as exc:
            print("  %-12s 失败：%s" % (prov, exc))
