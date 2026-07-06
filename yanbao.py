# -*- coding: utf-8 -*-
"""
同花顺个股研报抓取 + 新财富最佳分析师匹配

功能：
  1. 从同花顺 stockpage API 抓取个股研报（含分析师姓名 author 字段）
  2. 匹配新财富最佳分析师各赛道前5名
  3. 输出涉及的股票列表（CSV）

数据源：
  - 研报数据：同花顺 stockpage.10jqka.com.cn API
    接口：/stock_page/api/v1/stockpage/reports?code={code}&marketId={marketId}
    返回字段：id, title, summary, author(分析师), source(券商), publishTime, jumpUrl
  - 新财富分析师：newfortune.com.cn（运行时尝试抓取，失败则用配置列表）
  - 股票列表：xtdata（QMT环境）或 东方财富API（fallback）

运行方式：
  python yanbao.py                  # 默认扫描全部A股
  python yanbao.py --scope all      # 扫描全部A股
  python yanbao.py --pages 3        # 每只股票抓取3页研报（默认1页=20条）

前置条件：
  1. 网络可访问 stockpage.10jqka.com.cn
  2. （可选）mini QMT 终端已启动（用 xtdata 获取股票列表及名称）
  3. 新财富分析师名单（自动抓取或手动配置 XF_ANALYSTS_FALLBACK）

新财富分析师名单更新：
  官网 https://www.newfortune.com.cn/bestanalyst 每年发布各赛道前5名。
  若自动抓取失败（官网有反爬），请手动将分析师姓名填入
  XF_ANALYSTS_FALLBACK 字典，或写入同目录 xf_analysts.txt（每行一个姓名）。
"""

import requests
import json
import csv
import time
import datetime
import argparse
import sys
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ===== 同花顺 stockpage 研报 API =====
THS_REPORTS_API = "https://stockpage.10jqka.com.cn/stock_page/api/v1/stockpage/reports"
THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://stockpage.10jqka.com.cn/",
    "Accept": "application/json, text/plain, */*",
}

# 东方财富股票列表 API（xtdata 不可用时 fallback）
EM_STOCK_API = "http://80.push2.eastmoney.com/api/qt/clist/get"

# ===== 新财富最佳分析师名单（各赛道前5名）=====
# 若无法从网络获取，使用此列表。请根据最新一届更新。
# 格式：{"分析师姓名": "所属券商"}
# 更新来源：https://www.newfortune.com.cn/bestanalyst
XF_ANALYSTS_FALLBACK = {
    # ---- 以下为示例，请替换为最新一届新财富最佳分析师各赛道前5名 ----
    # 银行业
    "倪军": "广发证券",
    "沈娟": "华泰证券",
    "林虎": "华福证券",
    "徐凝碧": "国海证券",
    # 完整名单请访问新财富官网或同花顺研报中心查看
}

# ===== 抓取参数 =====
DEFAULT_SCOPE = "all"
DEFAULT_PAGES = 1
CONCURRENT_WORKERS = 15
REQUEST_TIMEOUT = 10
REQUEST_DELAY = 0.05
OUTPUT_CSV = "yanbao_result.csv"

# 同花顺市场ID：上海17，深圳33
MARKET_ID_SH = "17"
MARKET_ID_SZ = "33"

# 沪深300/中证500成分股的东方财富板块代码
EM_SECTOR_CODES = {
    "hs300": "b:BK0500",
    "zz500": "b:MK0406",
}


# ============================================================
#  新财富分析师名单获取
# ============================================================

def get_xf_analysts():
    """
    获取新财富最佳分析师各赛道前5名名单。

    优先级：
      1. 本地文件 xf_analysts.txt（每行一个分析师姓名）
      2. 在线抓取 newfortune.com.cn（可能被反爬拦截）
      3. 内置 XF_ANALYSTS_FALLBACK 列表
    返回：(set: 分析师姓名集合, dict: {姓名: 券商})
    """
    analysts = {}

    # 方式1：本地文件
    local_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xf_analysts.txt")
    if os.path.exists(local_file):
        with open(local_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                name = parts[0].strip()
                org = parts[1].strip() if len(parts) > 1 else ""
                if name:
                    analysts[name] = org
        if analysts:
            print(f"从本地文件加载新财富分析师 {len(analysts)} 名")
            return set(analysts.keys()), analysts

    # 方式2：在线抓取
    online = _fetch_xf_online()
    if online:
        print(f"从新财富官网抓取分析师 {len(online)} 名")
        return set(online.keys()), online

    # 方式3：fallback
    print(f"使用内置新财富分析师名单 {len(XF_ANALYSTS_FALLBACK)} 名")
    if not XF_ANALYSTS_FALLBACK or len(XF_ANALYSTS_FALLBACK) <= 4:
        print("[!] 内置名单为示例数据，请手动更新：")
        print("  方式1：编辑 yanbao.py 中的 XF_ANALYSTS_FALLBACK")
        print("  方式2：创建 xf_analysts.txt（每行一个姓名，可用Tab分隔券商）")
        print(f"  新财富官网：https://www.newfortune.com.cn/bestanalyst")
    return set(XF_ANALYSTS_FALLBACK.keys()), dict(XF_ANALYSTS_FALLBACK)


def _fetch_xf_online():
    """尝试从新财富官网抓取最佳分析师名单（可能被403拦截）"""
    try:
        r = requests.get(
            "https://www.newfortune.com.cn/bestanalyst",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None
        # 解析页面中的分析师名单（页面结构可能变化，此处为通用解析）
        text = r.content.decode("utf-8", errors="replace")
        names = re.findall(r'分析师[：:]\s*([^\s<,，、]+)', text)
        result = {}
        for n in names:
            n = n.strip()
            if len(n) >= 2 and len(n) <= 6:
                result[n] = ""
        return result if result else None
    except Exception:
        return None


# ============================================================
#  股票列表获取
# ============================================================

def get_stock_list(scope):
    """
    获取股票列表，返回 [(code, name), ...]
    scope: "hs300" / "zz500" / "all"
    """
    # 优先用xtdata（QMT环境，数据最全）
    try:
        stocks = _get_stocks_xtdata(scope)
        if scope != "all":
            print(f"  (注: 指数成分股暂不可用，使用全部A股 {len(stocks)} 只)")
        return stocks
    except Exception:
        pass

    # fallback: 东方财富API
    try:
        return _get_stocks_eastmoney(scope)
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        print("请确保 mini QMT 终端已启动（用 xtdata），或网络可访问东方财富API")
        sys.exit(1)


def _get_stocks_xtdata(scope):
    """通过 xtdata 获取股票列表（需 QMT 环境）"""
    from xtquant import xtdata
    # xtdata 指数成分板块返回空，仅支持全市场板块
    sector = "沪深A股"
    codes = xtdata.get_stock_list_in_sector(sector)
    if not codes:
        raise Exception(f"xtdata 返回空列表: {sector}")

    result = []
    for code in codes:
        pure_code = code.split(".")[0]
        detail = xtdata.get_instrument_detail(code)
        stock_name = detail.get("InstrumentName", "") if isinstance(detail, dict) else ""
        result.append((pure_code, stock_name))

    print(f"xtdata 获取 {sector} {len(result)} 只")
    if scope == "all":
        return result
    # 指数成分股用东方财富API筛选
    raise Exception("指数成分股需用东方财富API")


def _get_stocks_eastmoney(scope):
    """通过东方财富API获取股票列表"""
    if scope in EM_SECTOR_CODES:
        fs = EM_SECTOR_CODES[scope]
    else:
        # 全部A股：深圳主板+创业板+上海主板+科创板
        fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"

    params = {
        "pn": 1,
        "pz": 10000,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fs": fs,
        "fields": "f12,f14",
    }
    r = requests.get(EM_STOCK_API, params=params, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0"},
                     proxies={"http": None, "https": None})
    data = r.json()
    items = data.get("data", {}).get("diff", [])
    result = [(item["f12"], item["f14"]) for item in items if "f12" in item]
    print(f"东方财富API获取 {scope} 股票 {len(result)} 只")
    return result


# ============================================================
#  同花顺研报抓取
# ============================================================

def get_market_id(code):
    """根据股票代码返回同花顺市场ID"""
    if code.startswith("6"):
        return MARKET_ID_SH  # 上海
    return MARKET_ID_SZ     # 深圳(含创业板/科创板北交所)


def fetch_stock_reports(code, name, max_pages):
    """
    从同花顺 stockpage API 抓取个股研报。
    返回：(code, name, [report, ...])
    每个 report 为 dict: {title, author, source, publishTime, jumpUrl}
    """
    market_id = get_market_id(code)
    reports = []
    for page in range(1, max_pages + 1):
        params = {
            "code": code,
            "marketId": market_id,
            "page": page,
            "pageSize": 20,
        }
        try:
            r = requests.get(THS_REPORTS_API, params=params,
                             headers=THS_HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                break
            data = r.json().get("data")
            if not data:
                break
            report_list = data.get("reportList", [])
            if not report_list:
                break
            reports.extend(report_list)
            if not data.get("hasMore"):
                break
            time.sleep(REQUEST_DELAY)
        except Exception:
            break
    return code, name, reports


# ============================================================
#  分析师匹配 & 输出
# ============================================================

def match_analysts(stocks, xf_names, xf_map, max_pages):
    """
    遍历股票列表，抓取研报并匹配新财富分析师。
    返回匹配结果列表。
    """
    results = []
    total = len(stocks)
    done = 0
    matched_stocks = set()

    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        futures = {
            executor.submit(fetch_stock_reports, code, name, max_pages): (code, name)
            for code, name in stocks
        }
        for future in as_completed(futures):
            code, name = futures[future]
            done += 1
            try:
                _, _, reports = future.result()
            except Exception:
                reports = []

            for rep in reports:
                author = (rep.get("author") or "").strip()
                if not author:
                    continue
                # 同花顺 author 可能含多个分析师，用逗号分隔
                for person in re.split(r"[,，、/]", author):
                    person = person.strip()
                    if person in xf_names:
                        ts = rep.get("publishTime", 0)
                        try:
                            dt = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else ""
                        except Exception:
                            dt = ""
                        results.append({
                            "stock_code": code,
                            "stock_name": name,
                            "analyst": person,
                            "brokerage": rep.get("source", ""),
                            "xf_brokerage": xf_map.get(person, ""),
                            "report_title": rep.get("title", ""),
                            "publish_date": dt,
                            "jump_url": rep.get("jumpUrl", ""),
                        })
                        matched_stocks.add(code)

            if done % 50 == 0 or done == total:
                print(f"  进度: {done}/{total}  匹配股票: {len(matched_stocks)}  匹配研报: {len(results)}")

    return results


def save_results(results, output_file):
    """保存结果到CSV"""
    if not results:
        print("无匹配结果")
        return

    fieldnames = ["stock_code", "stock_name", "analyst", "brokerage",
                  "xf_brokerage", "report_title", "publish_date", "jump_url"]
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\n结果已保存到 {output_file}（{len(results)} 条记录）")


def print_summary(results):
    """打印汇总信息"""
    if not results:
        print("\n未找到新财富分析师涉及的股票")
        return

    stocks = {}
    for r in results:
        key = (r["stock_code"], r["stock_name"])
        if key not in stocks:
            stocks[key] = []
        stocks[key].append(r["analyst"])

    print(f"\n{'='*60}")
    print(f"新财富分析师涉及的股票: {len(stocks)} 只")
    print(f"匹配研报总数: {len(results)} 条")
    print(f"{'='*60}")
    print(f"{'代码':<8} {'名称':<10} {'分析师'}")
    print("-" * 60)
    for (code, name), analysts in sorted(stocks.items()):
        unique_analysts = sorted(set(analysts))
        print(f"{code:<8} {name:<10} {', '.join(unique_analysts)}")


# ============================================================
#  主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="同花顺个股研报抓取 + 新财富分析师匹配")
    parser.add_argument("--scope", default=DEFAULT_SCOPE,
                        choices=["hs300", "zz500", "all"],
                        help="股票范围: hs300/zz500/all (默认 all)")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES,
                        help="每只股票抓取研报页数，每页20条 (默认1)")
    parser.add_argument("--output", default=OUTPUT_CSV,
                        help=f"输出CSV文件名 (默认 {OUTPUT_CSV})")
    args = parser.parse_args()

    print("=" * 60)
    print("同花顺个股研报抓取 + 新财富最佳分析师匹配")
    print("=" * 60)

    # 1. 获取新财富分析师名单
    print("\n[1/4] 获取新财富分析师名单...")
    xf_names, xf_map = get_xf_analysts()
    if not xf_names:
        print("错误：无新财富分析师名单，请配置 XF_ANALYSTS_FALLBACK 或 xf_analysts.txt")
        sys.exit(1)
    print(f"  分析师名单: {sorted(xf_names)}")

    # 2. 获取股票列表
    print(f"\n[2/4] 获取股票列表 (scope={args.scope})...")
    stocks = get_stock_list(args.scope)
    if not stocks:
        print("错误：未获取到股票列表")
        sys.exit(1)

    # 3. 抓取研报并匹配
    print(f"\n[3/4] 抓取同花顺研报并匹配分析师 (共{len(stocks)}只股票, {args.pages}页/只)...")
    results = match_analysts(stocks, xf_names, xf_map, args.pages)

    # 4. 输出结果
    print(f"\n[4/4] 输出结果...")
    print_summary(results)
    save_results(results, args.output)


if __name__ == "__main__":
    main()
