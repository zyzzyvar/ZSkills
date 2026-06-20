#!/usr/bin/env python3
"""
A股 Tracker - 数据源韧性层(datasource)

针对中国大陆网络环境与 AKShare/东财接口的已知问题(高频封IP、接口随改版失效),
提供三重保障,确保数据可用且不降低分析质量:

  1. 多源降级:同一数据维度配置多个独立数据源(东财/新浪/腾讯/雪球),
     主源失败自动切换备源,任一成功即返回。
  2. 限频防封:对东财系接口全局限速 + 随机抖动,避免高频请求被 RemoteDisconnected。
  3. 缓存兜底:成功结果落地本地缓存;当日所有源都失败时,降级使用最近的缓存,
     并明确标注数据时效,绝不让分析因短时网络抖动而中断。

被 fetch.py 调用,不单独作为命令行使用。
"""
import json, os, time, random, hashlib
from datetime import datetime, timedelta

# ---- 反爬缓解:给 requests 全局注入浏览器 headers ----
# 东财 push2his 等接口对默认的 python-requests UA 拦截较严,伪装成浏览器可显著降低被拦概率。
def _install_browser_headers():
    try:
        import requests
        _orig = requests.Session.request
        UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        def _patched(self, method, url, **kwargs):
            headers = kwargs.get("headers") or {}
            headers.setdefault("User-Agent", UA)
            headers.setdefault("Accept", "*/*")
            headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9")
            if "eastmoney" in str(url):
                headers.setdefault("Referer", "https://data.eastmoney.com/")
            kwargs["headers"] = headers
            return _orig(self, method, url, **kwargs)
        if not getattr(requests.Session, "_zskills_patched", False):
            requests.Session.request = _patched
            requests.Session._zskills_patched = True
    except Exception:
        pass

_install_browser_headers()

DATA_DIR = os.environ.get("ASTOCK_DIR", os.path.join(os.path.expanduser("~"), ".astock-tracker"))
CACHE_DIR = os.path.join(DATA_DIR, "cache")

# 东财系接口的全局最小请求间隔(秒)+ 随机抖动,防止高频封IP
_EM_MIN_INTERVAL = 0.6
_last_em_call = [0.0]


def _throttle_em():
    """东财接口限频:确保两次调用间隔 >= 最小间隔 + 随机抖动。"""
    now = time.time()
    wait = _EM_MIN_INTERVAL - (now - _last_em_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.4))
    _last_em_call[0] = time.time()


def _cache_path(key):
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{h}.json")


def _cache_write(key, payload):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump({"key": key, "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "payload": payload}, f, ensure_ascii=False, default=str)
    except Exception:
        pass


def _cache_read(key, max_age_hours=72):
    try:
        p = _cache_path(key)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            blob = json.load(f)
        age = datetime.now() - datetime.strptime(blob["cached_at"], "%Y-%m-%d %H:%M:%S")
        if age > timedelta(hours=max_age_hours):
            return None
        blob["age_hours"] = round(age.total_seconds() / 3600, 1)
        return blob
    except Exception:
        return None


def robust_fetch(key, sources, cache_hours=72, tries_per_source=2):
    """
    依次尝试多个数据源,返回第一个成功的结果。

    sources: list of (source_name, callable, is_eastmoney) —— callable 返回 DataFrame
    返回: dict {ok, source, data(records), from_cache, cache_age_hours, errors}

    全部源失败时,降级返回缓存(若有),from_cache=True。
    """
    errors = {}
    for name, fn, is_em in sources:
        for attempt in range(tries_per_source):
            try:
                if is_em:
                    _throttle_em()
                df = fn()
                if df is not None and len(df) > 0:
                    records = df.to_dict("records")
                    _cache_write(key, {"source": name, "records": records})
                    return {"ok": True, "source": name, "data": records,
                            "from_cache": False, "errors": errors}
                else:
                    errors[name] = "返回空数据"
            except Exception as e:
                errors[name] = str(e)[:80]
                # 疑似被限频/封IP,多等一会儿再试下一次
                if "RemoteDisconnected" in str(e) or "Connection aborted" in str(e):
                    time.sleep(random.uniform(1.5, 3.0))
                else:
                    time.sleep(random.uniform(0.5, 1.0))
    # 所有源失败 → 缓存兜底
    cached = _cache_read(key, cache_hours)
    if cached:
        return {"ok": True, "source": cached["payload"].get("source", "cache") + "(缓存)",
                "data": cached["payload"]["records"], "from_cache": True,
                "cache_age_hours": cached.get("age_hours"), "errors": errors}
    return {"ok": False, "source": None, "data": None, "from_cache": False, "errors": errors}


# ---- 代码格式转换(各数据源要求不同前缀) ----
def code_with_market(code, market):
    """统一生成各源所需的代码格式。"""
    m = market.lower()
    return {
        "em": code,                       # 东财: 600519
        "tx": f"{m}{code}",               # 腾讯: sh600519
        "sina": f"{m}{code}",             # 新浪: sh600519
        "xq": f"{m.upper()}{code}",       # 雪球: SH600519
    }
