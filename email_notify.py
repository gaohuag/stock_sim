#!/usr/bin/env python3.11
"""
邮件发送模块 - 独立文件，避免 f-string 引号冲突
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email import encoders
import datetime
import os

SMTP_SERVER = "smtp.126.com"
SMTP_PORT = 25
SMTP_USER = "gaohuag@126.com"
SMTP_PASS = os.environ.get("SMTP_PASS", "PMNB4rctZCHezdDw")
RECIPIENT = "gaohuag@126.com"


def send_report(portfolio, watchlist, prices, trade_count):
    """发送每日交易报告邮件（纯文本 + HTML附件）"""
    try:
        # 计算收益
        cash = portfolio.get("cash", 0)
        positions_value = 0
        details = portfolio.get("positions", [])
        lines = []

        lines.append("=" * 50)
        lines.append("模拟炒股系统 - 每日交易报告")
        lines.append("=" * 50)
        lines.append("更新时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("本次执行交易：" + str(trade_count) + " 笔")
        lines.append("")

        # 计算持仓市值
        detail_lines = []
        for pos in details:
            code = pos["code"]
            name = pos["name"]
            qty = pos["quantity"]
            cost = pos["cost_price"]
            price = prices.get(code, cost)
            value = qty * price
            positions_value += value
            pnl = value - qty * cost
            pnl_pct = (pnl / (qty * cost) * 100) if qty * cost > 0 else 0
            sign = "+" if pnl >= 0 else ""
            detail_lines.append(
                "  " + code + " " + name + " " + str(qty) + "股 | 成本" +
                str(cost) + " → 现价" + str(price) + " | 市值" +
                str(round(value, 2)) + " | 盈亏" + sign + str(round(pnl, 2)) +
                "(" + sign + str(round(pnl_pct, 1)) + "%)"
            )

        initial = 85000 + 6000 * 2.9258 + 7000 * 0.6346
        total = cash + positions_value
        total_return = (total - initial) / initial * 100

        lines.append("【账户总览】")
        lines.append("  总市值：" + str(round(total, 2)) + " 元")
        lines.append("  现金：" + str(round(cash, 2)) + " 元")
        lines.append("  持仓市值：" + str(round(positions_value, 2)) + " 元")
        lines.append("  累计收益率：" + ("+" if total_return >= 0 else "") + str(round(total_return, 2)) + "%")
        lines.append("")

        if detail_lines:
            lines.append("【当前持仓】")
            lines.extend(detail_lines)
            lines.append("")

        if trade_count > 0 and portfolio.get("trade_log"):
            lines.append("【本次交易】")
            for t in portfolio["trade_log"][-trade_count:]:
                action_cn = "买入" if t["action"] == "BUY" else "卖出"
                lines.append(
                    "  " + t["time"] + " " + action_cn + " " +
                    t["code"] + " " + t["name"] + " " +
                    str(t["quantity"]) + "股 @ " + str(t["price"]) +
                    " | " + t["reason"]
                )
            lines.append("")

        lines.append("【股票池监控】")
        for s in watchlist.get("stocks", []):
            code = s["code"]
            name = s["name"]
            price = prices.get(code, 0)
            triggers = "、".join([str(t) for t in s.get("buy_triggers", [])])
            lines.append(
                "  " + code + " " + name + " 现价：" + str(price) +
                " | 触发价：" + triggers + " | 止损：" + str(s.get("stop_loss", "-"))
            )

        lines.append("")
        lines.append("⚠️ 本系统为模拟交易，所有收益均为虚拟，不构成任何投资建议。")
        lines.append("=" * 50)

        body = "\n".join(lines)

        # 生成HTML
        from simulator import generate_html_report
        html = generate_html_report(portfolio, watchlist, prices, trade_count)

        # 组装邮件
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_USER
        msg["To"] = RECIPIENT
        subject = ("模拟炒股日报 " + datetime.datetime.now().strftime("%m-%d") +
                   " | 总市值 " + str(int(total)) + "元 (" +
                   ("+" if total_return >= 0 else "") + str(round(total_return, 1)) + "%)")
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [RECIPIENT], msg.as_string())
        s.quit()

        return True, "邮件发送成功"

    except Exception as e:
        return False, str(e)
