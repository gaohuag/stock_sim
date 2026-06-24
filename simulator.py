#!/usr/bin/env python3
"""
模拟炒股交易引擎 - 全自动执行版本 v2
使用天天基金API获取净值数据
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_FILE = os.path.join(SIM_DIR, "portfolio.json")
WATCHLIST_FILE = os.path.join(SIM_DIR, "watchlist.json")
TRADE_LOG_FILE = os.path.join(SIM_DIR, "trades.log")
REPORT_FILE = os.path.join(SIM_DIR, "report.html")

# ============================================================
# 工具函数
# ============================================================

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_fund_nav(code):
    """
    获取基金/ETF净值（天天基金API）
    code: 159558, 560390, 515050, 513130 等
    返回：最新净值 float，失败返回 None
    """
    import urllib.request
    import re

    # 天天基金API：https://fundgz.1234567.com.cn/js/159558.js
    url = f"https://fundgz.1234567.com.cn/js/{code}.js"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            content = resp.read().decode("utf-8")
            # 返回格式：jsonpgz({"gszzl":"0.04","gsz":"3.6790",...})
            match = re.search(r'jsonpgz\((.*)\)', content)
            if match:
                data = json.loads(match.group(1))
                nav = float(data.get("gsz", 0))
                if nav > 0:
                    return nav
    except Exception as e:
        pass

    # 备用API：历史净值API
    try:
        url2 = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=1"
        req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=8) as resp:
            d = json.loads(resp.read())
            data_list = d.get("Data", {}).get("LSJZList", [])
            if data_list:
                nav = float(data_list[0].get("DWJZ", 0))
                if nav > 0:
                    return nav
    except Exception as e:
        pass

    return None


def get_stock_price_sina(code):
    """
    获取股票实时价格（新浪财经API）
    适用于：605117, 688019, 600460 等股票
    返回：最新价 float，失败返回 None
    """
    import urllib.request
    import re

    # 判断市场
    if code.startswith("6") or code.startswith("5"):
        market = "sh"
    else:
        market = "sz"

    url = f"https://hq.sinajs.cn/list={market}{code}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            content = resp.read().decode("gbk")
            match = re.search(r'="(.*)"', content)
            if match:
                fields = match.group(1).split(",")
                if len(fields) >= 4:
                    price = float(fields[3])  # 当前价
                    if price > 0:
                        return price
    except Exception as e:
        pass

    return None


def get_all_prices(codes):
    """
    批量获取价格
    codes: list of fund/stock codes
    返回：{code: price}
    """
    results = {}
    for code in codes:
        # ETF基金用天天基金API
        if len(code) == 6 and (code.startswith("159") or code.startswith("56") or code.startswith("51") or code.startswith("50")):
            price = get_fund_nav(code)
            if price:
                results[code] = price
                log(f"  📈 {code} 净值：{price}")
            else:
                log(f"  ⚠️ {code} 净值获取失败")
            time.sleep(0.3)
        else:
            # 股票用新浪API
            price = get_stock_price_sina(code)
            if price:
                results[code] = price
                log(f"  📈 {code} 股价：{price}")
            else:
                log(f"  ⚠️ {code} 股价获取失败")
            time.sleep(0.3)

    return results


# ============================================================
# 核心交易逻辑
# ============================================================

def check_buy_signals(portfolio, watchlist, prices):
    """
    检查买入信号
    """
    trades = []
    cash = portfolio["cash"]
    positions = {p["code"]: p for p in portfolio["positions"]}

    for stock in watchlist["stocks"]:
        code = stock["code"]
        name = stock["name"]
        price = prices.get(code)
        if price is None:
            continue

        has_position = code in positions
        current_qty = positions[code]["quantity"] if has_position else 0

        # 买入决策：价格 <= 触发价（回踩买入）
        # 对于黄金ETF等避险标的，只允许回踩，禁止追涨
        allow_chase = stock.get('allow_chase', True)
        triggered = False
        triggered_trigger = None
        triggered_reason = ''
        for trigger in stock["buy_triggers"]:
            trigger = float(trigger)
            # 触发条件1：价格 <= 触发价（回踩买入）
            if price <= trigger:
                triggered = True
                triggered_trigger = trigger
                triggered_reason = '回踩买入'
                break
            # 触发条件2：追涨买入（防止踏空，避险标的禁用）
            elif allow_chase and (not has_position) and (price > trigger) and (price <= trigger * 1.03):
                triggered = True
                triggered_trigger = trigger
                triggered_reason = '追涨买入'
                break

        if not triggered:
            continue

        total = portfolio.get("total_assets", 110000)
        target_pct = stock.get("position_size_pct", 10)
        target_amount = total * target_pct / 100.0

        if has_position:
            current_value = current_qty * price
            if current_value >= target_amount * 0.8:
                continue
            buy_amount = min(target_amount - current_value, cash)
        else:
            buy_amount = min(target_amount, cash)

        if buy_amount < 1000:
            continue

        qty = int(buy_amount / price / 100) * 100
        if qty <= 0:
            continue

        actual_amount = qty * price
        fee = actual_amount * 0.0003
        total_cost = actual_amount + fee

        if total_cost > cash:
            qty = int(cash / (price * 1.0003) / 100) * 100
            if qty <= 0:
                continue
            actual_amount = qty * price
            total_cost = actual_amount * 1.0003

        trades.append({
            "code": code,
            "name": name,
            "action": "BUY",
            "price": price,
            "quantity": qty,
            "amount": round(actual_amount, 2),
            "trigger": triggered_trigger,
            "reason": f"{triggered_reason}：价格{price:.4f}（触发价{triggered_trigger}）"
        })

    return trades


def check_sell_signals(portfolio, watchlist, prices):
    """
    检查卖出信号：目标价 or 止损价
    """
    trades = []
    positions = {p["code"]: p for p in portfolio["positions"]}

    for stock in watchlist["stocks"]:
        code = stock["code"]
        name = stock["name"]
        price = prices.get(code)
        if price is None or code not in positions:
            continue

        pos = positions[code]
        qty = pos["quantity"]
        cost = pos["cost_price"]

        # 止损检查
        stop_loss = float(stock.get("stop_loss", 0))
        if stop_loss > 0 and price <= stop_loss:
            sell_qty = qty
            trades.append({
                "code": code,
                "name": name,
                "action": "SELL",
                "price": price,
                "quantity": sell_qty,
                "amount": round(sell_qty * price, 2),
                "reason": f"⚠️ 止损！价格{price:.4f}触及止损价{stop_loss}（成本{cost:.4f}）"
            })
            continue

        # 目标价检查（分批卖出）
        targets = stock.get("sell_triggers", [])
        for i, target in enumerate(targets):
            target = float(target)
            if price >= target:
                if i == 0:
                    sell_qty = max(100, int(qty * 0.5 / 100) * 100)
                else:
                    sell_qty = qty
                sell_qty = min(sell_qty, qty)

                if sell_qty > 0:
                    pnl_pct = (price - cost) / cost * 100
                    trades.append({
                        "code": code,
                        "name": name,
                        "action": "SELL",
                        "price": price,
                        "quantity": sell_qty,
                        "amount": round(sell_qty * price, 2),
                        "reason": f"🎯 止盈！价格{price:.4f}触及目标价{target}（成本{cost:.4f}，盈利{pnl_pct:+.1f}%）"
                    })
                break

    return trades


def execute_trades(portfolio, trades):
    """执行交易，更新持仓"""
    for trade in trades:
        code = trade["code"]
        action = trade["action"]
        price = trade["price"]
        qty = trade["quantity"]
        amount = trade["amount"]
        reason = trade["reason"]

        if action == "BUY":
            fee = amount * 0.0003
            total_cost = amount + fee
            if total_cost > portfolio["cash"]:
                log(f"⚠️ 现金不足，跳过 {code}：需要{total_cost:.2f}，可用{portfolio['cash']:.2f}")
                continue

            portfolio["cash"] -= total_cost

            found = False
            for pos in portfolio["positions"]:
                if pos["code"] == code:
                    old_value = pos["quantity"] * pos["cost_price"]
                    new_value = qty * price
                    pos["quantity"] += qty
                    pos["cost_price"] = round((old_value + new_value) / pos["quantity"], 4)
                    pos["buy_date"] = datetime.now().strftime("%Y-%m-%d")
                    found = True
                    break
            if not found:
                portfolio["positions"].append({
                    "code": code,
                    "name": trade["name"],
                    "quantity": qty,
                    "cost_price": price,
                    "buy_date": datetime.now().strftime("%Y-%m-%d")
                })

            log(f"✅ 买入 {code} {trade['name']}：{qty}股 @ {price:.4f}，金额{amount:.2f}｜{reason}")

        elif action == "SELL":
            fee = amount * 0.0013
            portfolio["cash"] += amount - fee

            for i, pos in enumerate(portfolio["positions"]):
                if pos["code"] == code:
                    pos["quantity"] -= qty
                    if pos["quantity"] <= 0:
                        portfolio["positions"].pop(i)
                    break

            log(f"✅ 卖出 {code} {trade['name']}：{qty}股 @ {price:.4f}，金额{amount:.2f}｜{reason}")

    return portfolio


def calc_portfolio_value(portfolio, prices):
    """计算总市值和收益明细"""
    cash = portfolio["cash"]
    positions_value = 0
    details = []

    for pos in portfolio["positions"]:
        code = pos["code"]
        price = prices.get(code, pos["cost_price"])
        qty = pos["quantity"]
        value = qty * price
        cost = qty * pos["cost_price"]
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0

        positions_value += value
        details.append({
            "code": code,
            "name": pos["name"],
            "quantity": qty,
            "cost": pos["cost_price"],
            "price": price,
            "value": value,
            "pnl": pnl,
            "pnl_pct": pnl_pct
        })

    total = cash + positions_value
    return cash, positions_value, total, details


def generate_html_report(portfolio, watchlist, prices, trade_count):
    """生成HTML持仓报告"""
    cash, positions_value, total, details = calc_portfolio_value(portfolio, prices)

    # 计算初始总资产
    initial_cash = 85000
    initial_position_value = 0
    for pos in portfolio.get("_initial_positions", []):
        initial_position_value += pos["quantity"] * pos["cost_price"]

    initial_total = initial_cash + initial_position_value
    if not hasattr(generate_html_report, "_initial_recorded"):
        generate_html_report._initial_total = total
        generate_html_report._initial_recorded = True

    initial = getattr(generate_html_report, "_initial_total", total)
    total_return = ((total - initial) / initial * 100) if initial > 0 else 0

    # 持仓表格
    position_rows = ""
    for d in details:
        color = "#0c9" if d["pnl"] >= 0 else "#f44"
        position_rows += f"""
        <tr>
            <td>{d['code']}</td>
            <td>{d['name']}</td>
            <td>{d['quantity']}</td>
            <td>{d['cost']:.4f}</td>
            <td>{d['price']:.4f}</td>
            <td>{d['value']:.2f}</td>
            <td style="color:{color};font-weight:bold">{d['pnl']:+.2f} ({d['pnl_pct']:+.1f}%)</td>
        </tr>"""

    if not position_rows:
        position_rows = '<tr><td colspan="7" style="text-align:center;color:#999;padding:20px">暂无持仓</td></tr>'

    # 股票池表格
    watch_rows = ""
    for s in watchlist["stocks"]:
        code = s["code"]
        name = s["name"]
        price = prices.get(code)
        price_str = f"{price:.4f}" if price else "获取中..."
        triggers = "、".join([str(t) for t in s.get("buy_triggers", [])])
        watch_rows += f"""
        <tr>
            <td>{code}</td>
            <td>{name}</td>
            <td>{price_str}</td>
            <td>{triggers}</td>
            <td>{s.get('stop_loss', '-')}</td>
            <td>{s.get('notes', '')}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>模拟炒股持仓报告</title>
<style>
    body {{ font-family: -apple-system, "PingFang SC", sans-serif; max-width: 1080px; margin: 0 auto; padding: 24px; background: #f5f6fa; color: #333; }}
    h1 {{ color: #2d3436; margin-bottom: 4px; }}
    .update-time {{ color: #999; font-size: 12px; margin-bottom: 20px; }}
    .card {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    .metrics {{ display: flex; gap: 32px; flex-wrap: wrap; }}
    .metric-label {{ font-size: 13px; color: #999; margin-bottom: 4px; }}
    .metric-value {{ font-size: 32px; font-weight: bold; color: #2d3436; }}
    .positive {{ color: #00b894 !important; }}
    .negative {{ color: #e17055 !important; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th {{ background: #f8f9fa; padding: 10px 12px; text-align: left; font-size: 13px; color: #666; border-bottom: 2px solid #e9ecef; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }}
    tr:hover {{ background: #f8f9fa; }}
    .section-title {{ font-size: 18px; font-weight: 600; margin: 0 0 12px 0; }}
    .disclaimer {{ color: #999; font-size: 12px; margin-top: 24px; padding-top: 16px; border-top: 1px solid #e9ecef; }}
</style>
</head>
<body>
    <h1>📈 模拟炒股持仓报告</h1>
    <p class="update-time">更新时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}｜本次执行交易：{trade_count}笔</p>

    <div class="card">
        <div class="metrics">
            <div>
                <div class="metric-label">总市值</div>
                <div class="metric-value">{total:,.2f} 元</div>
            </div>
            <div>
                <div class="metric-label">现金</div>
                <div class="metric-value" style="color:#0984e3">{cash:,.2f} 元</div>
            </div>
            <div>
                <div class="metric-label">持仓市值</div>
                <div class="metric-value">{positions_value:,.2f} 元</div>
            </div>
            <div>
                <div class="metric-label">累计收益率</div>
                <div class="metric-value {'positive' if total_return >= 0 else 'negative'}">{total_return:+.2f}%</div>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="section-title">📊 当前持仓</div>
        <table>
            <tr>
                <th>代码</th><th>名称</th><th>数量(股)</th><th>成本价</th><th>现价</th><th>市值</th><th>盈亏</th>
            </tr>
            {position_rows}
        </table>
    </div>

    <div class="card">
        <div class="section-title">🔍 股票池</div>
        <table>
            <tr>
                <th>代码</th><th>名称</th><th>现价</th><th>买入触发价</th><th>止损价</th><th>备注</th>
            </tr>
            {watch_rows}
        </table>
    </div>

    <div class="card">
        <div class="section-title">📝 交易规则</div>
        <ul style="line-height: 2; color: #555;">
            <li>买入：价格≤触发价时分批买入，按配置仓位比例执行</li>
            <li>卖出：价格≥目标价时分批止盈（第一批卖50%）</li>
            <li>止损：价格≤止损价时全部卖出</li>
            <li>手续费：买入0.03%，卖出0.13%（含印花税）</li>
            <li>执行方式：全自动（AI分析→自动模拟成交）</li>
        </ul>
    </div>

    <p class="disclaimer">⚠️ 本系统为模拟交易，所有收益均为虚拟，不构成任何投资建议。投资有风险，决策需谨慎。</p>
</body>
</html>"""


# ============================================================
# 主函数
# ============================================================

def main():
    log("=" * 60)
    log("🚀 模拟炒股系统启动（全自动模式 v2）")
    log("=" * 60)

    # 加载数据
    portfolio = load_json(PORTFOLIO_FILE)
    watchlist = load_json(WATCHLIST_FILE)

    # 记录初始总资产（仅第一次运行时）
    global _initial_total
    if not hasattr(main, "_initial_total"):
        _, _, total, _ = calc_portfolio_value(portfolio, {})
        main._initial_total = total
        log(f"📊 初始总资产记录：{total:.2f} 元")

    # 获取所有需要跟踪的标的代码
    all_codes = [s["code"] for s in watchlist["stocks"]]
    log(f"📊 跟踪标的：{all_codes}")
    log("🔄 正在获取实时价格...")

    # 拉取实时价格
    prices = get_all_prices(all_codes)
    log(f"📊 获取到 {len(prices)} 个标的价格")

    # 检查买入信号
    log("🔍 检查买入信号...")
    buy_trades = check_buy_signals(portfolio, watchlist, prices)
    if buy_trades:
        log(f"📈 发现 {len(buy_trades)} 个买入信号")
    else:
        log("👉 无买入信号")

    # 检查卖出信号
    log("🔍 检查卖出信号...")
    sell_trades = check_sell_signals(portfolio, watchlist, prices)
    if sell_trades:
        log(f"📉 发现 {len(sell_trades)} 个卖出信号")
    else:
        log("👉 无卖出信号")

    # 执行交易
    all_trades = buy_trades + sell_trades
    if all_trades:
        log(f"⚡ 执行 {len(all_trades)} 笔交易...")
        portfolio = execute_trades(portfolio, all_trades)
    else:
        log("✅ 无交易执行，市场观望中")

    # 更新总资产
    _, positions_value, total, _ = calc_portfolio_value(portfolio, prices)
    portfolio["total_assets"] = round(total, 2)
    portfolio["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 保存持仓
    save_json(PORTFOLIO_FILE, portfolio)
    log(f"💾 持仓已保存，总市值：{total:.2f} 元")

    # 生成HTML报告
    html = generate_html_report(portfolio, watchlist, prices, len(all_trades))
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"📄 HTML报告已生成：{REPORT_FILE}")

    log("=" * 60)
    log("✅ 执行完成")
    log("=" * 60)

    # 发送邮件报告
    try:
        import sys
        sys.path.insert(0, SIM_DIR)
        import email_notify
        ok, msg = email_notify.send_report(portfolio, watchlist, prices, len(all_trades))
        if ok:
            log("📧 邮件报告已发送至 gaohuag@126.com")
        else:
            log(f"❌ 邮件发送失败：{msg}")
    except Exception as e:
        log(f"❌ 邮件模块加载失败：{e}")



def send_email_report(portfolio, prices, trade_count):
    """运行结束后发送邮件报告（纯文本 + HTML附件）"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email import encoders
    import os

    recipient = "gaohuag@126.com"
    smtp_server = "smtp.126.com"
    smtp_port = 25
    smtp_user = recipient
    smtp_pass = "PMNB4rctZCHezdDw"

    # 计算市值和收益
    cash, positions_value, total, details = calc_portfolio_value(portfolio, prices)
    initial = 85000 + 6000 * 2.9258 + 7000 * 0.6346
    total_return = (total - initial) / initial * 100

    # 拼接邮件正文
    lines = []
    lines.append("=" * 50)
    lines.append("模拟炒股系统 - 每日交易报告")
    lines.append("=" * 50)
    lines.append(f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"本次执行交易：{trade_count} 笔")
    lines.append("")
    lines.append(f"【账户总览】")
    lines.append(f"  总市值：{total:,.2f} 元")
    lines.append(f"  现金：{cash:,.2f} 元")
    lines.append(f"  持仓市值：{positions_value:,.2f} 元")
    lines.append(f"  累计收益率：{total_return:+.2f}%")
    lines.append("")

    if details:
        lines.append("【当前持仓】")
        for d in details:
            pnl_str = f"{d['pnl']:+.2f} ({d['pnl_pct']:+.1f}%)"
            lines.append(f"  {d['code']} {d['name']} {d['quantity']}股 | 成本{d['cost']:.4f} → 现价{d['price']:.4f} | 市值{d['value']:.2f} | 盈亏{pnl_str}")
        lines.append("")

    if trade_count > 0 and portfolio.get("trade_log"):
        lines.append("【本次交易】")
        for t in portfolio["trade_log"][-trade_count:]:
            action_cn = "买入" if t["action"] == "BUY" else "卖出"
            lines.append(f"  {t['time']} {action_cn} {t['code']} {t['name']} {t['quantity']}股 @ {t['price']:.4f} | {t['reason']}")
        lines.append("")

    lines.append("【股票池监控】")
    watchlist = load_json(WATCHLIST_FILE)
    for s in watchlist.get("stocks", []):
        code = s["code"]
        name = s["name"]
        price = prices.get(code, 0)
        triggers = "、".join([str(t) for t in s.get("buy_triggers", [])])
        lines.append(f"  {code} {name} 现价：{price:.4f} | 触发价：{triggers} | 止损：{s.get('stop_loss', '-')}")

    lines.append("")
    lines.append("⚠️ 本系统为模拟交易，所有收益均为虚拟，不构成任何投资建议。")
    lines.append("=" * 50)
    body = "\n".join(lines)
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = smtp_user
        msg["To"] = recipient
        msg["Subject"] = f"模拟炒股日报 {datetime.now().strftime('%m-%d')} | 总市值 {total:,.0f}元 ({total_return:+.1f}%)"

        msg.attach(MIMEText(body, "plain", "utf-8"))

        # HTML版本
        html_content = generate_html_report(portfolio, watchlist, prices, trade_count)
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        s = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
        s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, [recipient], msg.as_string())
        s.quit()
        log(f"📧 邮件报告已发送至 {recipient}")
    except Exception as e:
        log(f"❌ 邮件发送失败：{e}")

if __name__ == "__main__":
    main()
