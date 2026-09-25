"""Run Arjun's agent against both stalls and write the audit record.

Usage:
    PYTHONPATH=src python run.py [--db PATH] [--jsonl PATH] [--report PATH]
                                 [--honest URL] [--rogue URL] [--no-serve]

Default: starts both local stalls in-process, runs the agent (LLM if
OPENAI_API_KEY is set, else deterministic offline planner), prints the
research summary + decision table, and writes decisions to SQLite + JSONL.

Every payment decision (paid or refused, and why) is recorded — Arjun's
morning audit trail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from coin_purse.agent import ResearchAgent
from coin_purse.buyer import CoinPurseBuyer
from coin_purse.ledger import DecisionLedger
from coin_purse.policy import (
    MAX_PER_CALL_BASE_UNITS,
    MAX_TOTAL_BASE_UNITS,
    format_usdc,
)
from coin_purse.stalls import HONEST_PORT, ROGUE_PORT, serve_forever, HonestHandler, RogueHandler


def main() -> int:
    ap = argparse.ArgumentParser(description="The Coin Purse demo run")
    ap.add_argument("--db", default="sample-run/decisions.db")
    ap.add_argument("--jsonl", default="sample-run/decision-record.jsonl")
    ap.add_argument("--report", default="sample-run/report.json")
    ap.add_argument("--honest", default=f"http://127.0.0.1:{HONEST_PORT}")
    ap.add_argument("--rogue", default=f"http://127.0.0.1:{ROGUE_PORT}")
    ap.add_argument("--no-serve", action="store_true",
                    help="don't start local stalls (use --honest/--rogue URLs)")
    args = ap.parse_args()

    if not args.no_serve:
        serve_forever(HonestHandler, HONEST_PORT)
        serve_forever(RogueHandler, ROGUE_PORT)

    from coin_purse.policy import SpendingPolicy

    policy = SpendingPolicy()  # $0.25/call, $5.00 total — code, not prompt
    ledger = DecisionLedger(db_path=args.db, jsonl_path=args.jsonl)
    # No wipe: history persists, and the buyer restores cumulative spend from
    # the ledger on startup so the total budget holds across restarts.
    buyer = CoinPurseBuyer(policy=policy, ledger=ledger,
                           buyer_address="0xArjunAgent000000000000000000000000000001")
    agent = ResearchAgent(buyer=buyer, honest_base=args.honest, rogue_base=args.rogue)

    print(f"Purse: {format_usdc(MAX_TOTAL_BASE_UNITS)} total, "
          f"{format_usdc(MAX_PER_CALL_BASE_UNITS)} per call (Base Sepolia test USDC)")
    report = agent.run()

    print("\n--- research summary ---")
    print(report.summary)
    print("\n--- decision record ---")
    rows = ledger.all()
    for r in rows:
        amt = (f"{format_usdc(r['amount_base_units'])}"
               if r["amount_base_units"] else "-")
        print(f"[{r['decision'].upper():7s}] {r['stall']:7s} {r['endpoint'].split('/')[-1]:10s} "
              f"{amt:18s} :: {r['reason'][:130]}")
    print(f"\nSpent total: {format_usdc(policy.spent_base_units)} "
          f"({policy.spent_base_units} base units)")

    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump({"report": report.to_dict(), "decisions": rows,
                   "spent_base_units": policy.spent_base_units}, fh, indent=2)
    print(f"\nWrote SQLite ledger -> {args.db}")
    print(f"Wrote JSONL record  -> {args.jsonl}")
    print(f"Wrote report        -> {args.report}")
    ledger.close()

    n_paid_honest = sum(1 for r in rows if r["decision"] == "paid" and r["stall"] == "honest")
    n_refused_rogue = sum(1 for r in rows if r["decision"] == "refused" and r["stall"] == "rogue")
    ok = n_paid_honest >= 2 and n_refused_rogue >= 3
    print(f"\nAcceptance: honest paid={n_paid_honest} (>=2), "
          f"rogue refused={n_refused_rogue} (>=3) -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
