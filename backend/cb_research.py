"""Publish dated, read-only convertible-bond research and record simulated fills."""
import argparse
import json
import math
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .cb_research_rules import decide, evaluate_rows
from .news_cleaning import SHANGHAI
from .trading_calendar import is_trade_day

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "cb-research" / "research.sqlite3"
TRADE_ACTIONS = {"enter", "switch", "exit"}


def research_path():
    return Path(os.environ.get("FINPULSE_CB_RESEARCH_PATH", str(DEFAULT_PATH)))


def _empty():
    return {"history": [], "snapshot": None, "position_code": None}


def _load(database):
    row = database.execute("SELECT document FROM research_state WHERE id = 1").fetchone()
    return json.loads(row[0]) if row else _empty()


def _save(database, state):
    database.execute(
        "INSERT OR REPLACE INTO research_state (id, document) VALUES (1, ?)",
        (json.dumps(state, ensure_ascii=False, allow_nan=False),),
    )


@contextmanager
def _transaction(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "CREATE TABLE IF NOT EXISTS research_state (id INTEGER PRIMARY KEY, document TEXT NOT NULL)"
        )
        database.execute("BEGIN IMMEDIATE")
        yield database


def _view(state):
    snapshot = state["snapshot"]
    history = state["history"]
    latest = history[0] if history else {}
    return {
        "status": "ready" if snapshot else "no_data",
        "as_of": None,
        "generated_at": None,
        "collected_at": None,
        "close_confirmed": None,
        "sources": [],
        "warnings": [],
        "entries": [],
        "preview": False,
        "next_review": None,
        **(snapshot or {}),
        "position_code": state["position_code"],
        "decision": latest.get("decision"),
        "execution": latest.get("execution"),
        "history": history,
    }


def read_research(path=None):
    path = Path(path or research_path())
    try:
        if not path.exists():
            return _view(_empty())
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
            return _view(_load(database))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        empty = _view(_empty())
        return {**empty, "status": "data_error", "warnings": ["可转债研究记录暂不可读取。"]}


def _iso_date(value):
    if not isinstance(value, str):
        raise ValueError("需要 YYYY-MM-DD 格式日期")
    day = date.fromisoformat(value)
    if value != day.isoformat():
        raise ValueError("需要 YYYY-MM-DD 格式日期")
    return day


def _weekly_close(day):
    if is_trade_day(day) is not True:
        return False
    return all(
        is_trade_day(day + timedelta(days=offset)) is False
        for offset in range(1, 5 - day.weekday())
    )


def _next_session(day):
    for offset in range(1, 15):
        session = day + timedelta(days=offset)
        trading = is_trade_day(session)
        if trading is None:
            return None
        if trading:
            return session
    return None


def _next_review(day):
    for offset in range(1, 15):
        candidate = day + timedelta(days=offset)
        if _weekly_close(candidate):
            return candidate.isoformat()
    return None


def publish_snapshot(snapshot, path=None, *, preview=False, order_size=200000, switch_cost=0.004, now=None):
    """Build a weekly decision, or an off-cycle risk exit based on an observed event."""
    path = Path(path or research_path())
    if not isinstance(snapshot, dict):
        raise ValueError("快照须为 JSON 对象")
    day = _iso_date(snapshot.get("as_of"))
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if day > now.date() or not preview and day == now.date() and now.time() < time(15):
        raise ValueError("正式研究信号应在对应交易日收盘后生成")
    if not math.isfinite(order_size) or order_size <= 0 or not math.isfinite(switch_cost) or not 0 <= switch_cost < 1:
        raise ValueError("研究名义金额或换仓成本无效")
    rows = snapshot.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("快照没有可转债数据")
    codes = [row.get("code") if isinstance(row, dict) else None for row in rows]
    if any(not isinstance(code, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", code) for code in codes):
        raise ValueError("转债代码必须包含沪深交易所后缀")
    if any(not isinstance(row.get("name"), str) or not row["name"].strip() for row in rows):
        raise ValueError("快照缺少转债名称")
    if len(set(codes)) != len(codes):
        raise ValueError("快照存在重复转债代码")
    sources = snapshot.get("sources", [])
    warnings = snapshot.get("warnings", [])
    if any(not isinstance(items, list) or any(not isinstance(item, str) for item in items) for items in (sources, warnings)):
        raise ValueError("数据来源和提示应为文本列表")

    if not preview and snapshot.get("close_confirmed") is not True:
        raise ValueError("正式决策需要已确认收盘的行情快照")

    with _transaction(path) as database:
        state = _load(database)
        history = state["history"]
        prior = history[0] if history else None
        if preview and prior:
            raise ValueError("已有正式周度决策，保留该周信号与行情的统一口径")
        if prior and day <= _iso_date(prior["as_of"]):
            raise ValueError("新信号日期必须晚于已有记录")
        if preview and state["snapshot"] and day < _iso_date(state["snapshot"]["as_of"]):
            raise ValueError("预览日期早于已有快照")

        position_code = state["position_code"]
        entries = evaluate_rows(day.isoformat(), rows, position_code, order_size=order_size)
        current = {row["code"]: row for row in entries}
        previous_failures = 0
        if prior and position_code == prior["decision"]["position_code"]:
            previous_failures = prior["decision"]["thesis_failures"]
        result = {
            "as_of": day.isoformat(),
            "generated_at": now.isoformat(timespec="seconds"),
            "collected_at": snapshot.get("collected_at"),
            "close_confirmed": snapshot.get("close_confirmed"),
            "sources": sources,
            "warnings": warnings,
            "entries": entries,
            "preview": preview,
            "next_review": day.isoformat() if preview and day == now.date() and _weekly_close(day) else _next_review(day),
        }
        if not preview:
            decision = decide(entries, position_code, previous_failures, switch_cost=switch_cost)
            if not _weekly_close(day) and decision["action"] != "exit":
                raise ValueError("正式决策只在一周最后一个交易日收盘后发布。其他日期可导入预览。")
            if prior and prior["execution"] and prior["execution"]["status"] == "pending":
                if decision["action"] != "exit" or prior["decision"]["action"] != "switch":
                    raise ValueError("请先记录上一轮模拟成交或未成交结果")
                prior["execution"].update(status="blocked", date=day.isoformat(),
                                          reason="原待确认换仓被新的风险退出信号覆盖")
            decision["position_code"] = position_code
            execution = None
            if decision["action"] in TRADE_ACTIONS:
                execution = {"status": "pending", "date": None, "reason": None,
                             "buy_price": None, "sell_price": None, "cost": None}
            history.insert(0, {"as_of": day.isoformat(), "decision": decision, "execution": execution,
                               "position": current.get(position_code), "candidate": current.get(decision["candidate_code"])})
        state["snapshot"] = result
        _save(database, state)
        return _view(state)


def record_execution(signal_date, execution_date, path=None, *, buy_price=None, sell_price=None, cost=None, blocked=None, now=None):
    """Record one observed simulated result. A switch requires both legs to be executable."""
    signal_day = _iso_date(signal_date)
    fill_day = _iso_date(execution_date)
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if fill_day > now.date() or fill_day == now.date() and now.time() < time(9, 30):
        raise ValueError("模拟执行日期尚未开始交易")
    next_session = _next_session(signal_day)
    if next_session is None or fill_day != next_session:
        raise ValueError("成交或未成交记录须对应信号后的下一交易日")
    path = Path(path or research_path())
    with _transaction(path) as database:
        state = _load(database)
        latest = state["history"][0] if state["history"] else None
        execution = latest["execution"] if latest and latest["as_of"] == signal_date else None
        if not execution or execution["status"] != "pending":
            raise ValueError("该信号没有待确认的模拟执行")
        decision = latest["decision"]
        if blocked and blocked.strip():
            if any(value is not None for value in (buy_price, sell_price, cost)):
                raise ValueError("未成交记录不能同时填写成交价格或费用")
            execution.update(status="blocked", date=execution_date, reason=blocked.strip())
        else:
            buys = decision["action"] in {"enter", "switch"}
            sells = decision["action"] in {"exit", "switch"}
            for required, price in ((buys, buy_price), (sells, sell_price)):
                if required and (price is None or not math.isfinite(price) or price <= 0):
                    raise ValueError("请为每个成交方向填写有效模拟成交价")
                if not required and price is not None:
                    raise ValueError("成交价格与信号的买卖方向不符")
            if cost is None or not math.isfinite(cost) or not 0 <= cost < 1:
                raise ValueError("请填写实际采用的模拟交易成本比例")
            execution.update(status="filled", date=execution_date, buy_price=buy_price,
                             sell_price=sell_price, cost=cost)
            state["position_code"] = decision["candidate_code"] if buys else None
        _save(database, state)
        return _view(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--order-size", type=float, default=200000)
        command.add_argument("--switch-cost", type=float, default=0.004)
        if name == "publish":
            command.add_argument("snapshot", type=Path)
            command.add_argument("--preview", action="store_true")
        else:
            command.set_defaults(preview=True)
    fill = commands.add_parser("record-execution")
    fill.add_argument("signal_date")
    fill.add_argument("execution_date")
    fill.add_argument("--buy-price", type=float)
    fill.add_argument("--sell-price", type=float)
    fill.add_argument("--cost", type=float)
    fill.add_argument("--blocked")
    args = parser.parse_args()
    try:
        if args.command == "record-execution":
            result = record_execution(args.signal_date, args.execution_date, buy_price=args.buy_price,
                                      sell_price=args.sell_price, cost=args.cost, blocked=args.blocked)
        else:
            if args.command == "collect":
                from .cb_research_data import collect_snapshot

                snapshot = collect_snapshot()
            else:
                snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            result = publish_snapshot(snapshot, preview=args.preview, order_size=args.order_size,
                                      switch_cost=args.switch_cost)
    except (OSError, TypeError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps({key: result[key] for key in ("as_of", "preview", "decision", "execution", "warnings")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
