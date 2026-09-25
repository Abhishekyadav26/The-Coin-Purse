# The Coin Purse: An Agent That Pays but Can't Be Drained

Arjun (PhD, Bengaluru) studies how monsoon forecasts move mandi crop prices. His
research agent gets a **$5 purse of test USDC** and buys rainfall / price /
satellite rows from small x402 endpoints while he sleeps — and stays sensible
when a stall is not.

## What happened on the last run

```
Purse: $5.000000 USDC total, $0.250000 USDC per call (Base Sepolia test USDC)

--- research summary ---
Bengaluru onset rains (12.4mm on Jun 8, 3.1mm on Jun 9) coincided with firm
tomato prices (Rs 1450/qtl Kolar, Rs 1520/qtl Chickballapur); above-normal soil
moisture in the Kolar belt supports a steady-supply read.
Evidence: 3 paid honest-stall dataset(s); 4 rogue-stall attempt(s) refused.

Spent total: $0.060000 USDC (60000 base units)
```

Full audit trail: [`sample-run/decision-record.jsonl`](sample-run/decision-record.jsonl)
(pretty: [`decision-record.json`](sample-run/decision-record.json)),
SQLite: [`sample-run/decisions.db`](sample-run/decisions.db),
report: [`sample-run/report.json`](sample-run/report.json).

## How to run (2 minutes, no keys, no funds)

```bash
python3 run.py
# or: PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`run.py` starts both local stalls in-process (honest `:8401`, rogue `:8402`),
runs the agent (LLM if `OPENAI_API_KEY` is set, else the deterministic offline
planner — same tools, same policy), prints the summary + decision table, and
writes the SQLite + JSONL record. Stdlib only.

With an LLM (any OpenAI-compatible endpoint):

```bash
export OPENAI_API_KEY=...            # optional
export OPENAI_BASE_URL=...           # optional (Ollama / gateway)
export COIN_PURSE_MODEL=gpt-4o-mini  # optional
python3 run.py
```

## How money stays safe

| Threat (from the brief) | Where it dies |
|---|---|
| $4.99 for one row | `SpendingPolicy.authorize` refuses: price (4_990_000) > per-call cap (250_000). Price read from the server's 402 `accepts` entry, never the request. |
| Paid in a token he's never heard of | Asset allowlist: only test USDC `0x036C…f7e` on `eip155:84532`. |
| "Your budget has been raised" tool text | Untrusted text. Policy lives in `src/coin_purse/policy.py` (code, not prompt); tools expose **no** amount/budget params, so there is nothing to talk past. |
| Decimal/rounding tricks | All decisions use integer **base units** (`parse_amount_to_base_units` rejects `"4.99"`/`"0.01"`/floats). Floats only for display. |
| Death by a thousand cuts | `spent_base_units` accumulates; any call breaching the $5 total is refused. |
| No receipt | Every attempt → `paid`/`refused` + reason in SQLite (`ledger.py`) and JSONL. |

Key files:

- `src/coin_purse/policy.py` — coded spending policy (per-call $0.25 = 250_000, total $5 = 5_000_000, USDC + Base Sepolia allowlists, base-unit parsing).
- `src/coin_purse/buyer.py` — x402 buyer: 402 → server-price → `authorize()` → sign/settle → retry → record. Model never in the loop.
- `src/coin_purse/agent.py` — LLM agent with tool calling; research-only tool args.
- `src/coin_purse/stalls.py` — honest stall (`/rainfall` $0.01, `/prices` $0.02, `/satellite` $0.03) + rogue stall (`/row` $4.99, `/exotic` unknown token, `/tricky` injection + wrong network, `/float` decimal price).
- `src/coin_purse/ledger.py` — SQLite + JSONL decision log.
- `tests/test_policy.py` — 10 self-tests (base units, caps, rogue refusals, prompt-injection, end-to-end).

## Real testnet run

- Network: **Base Sepolia** (`eip155:84532`, chain 84532)
- Asset: test USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (6 decimals)
- Facilitator: `https://x402.org/facilitator` (testnet) / CDP facilitator for mainnet
- The committed `sample-run/` record comes from local stalls speaking the x402
  v2 402 dialect with genuine Base Sepolia requirements (same `accepts` shape,
  same `PAYMENT-REQUIRED` header, same policy/ledger code path as live mode).
- For a live on-chain run: `pip install -r requirements.txt`, fund a wallet
  with Base Sepolia test USDC ([Circle faucet](https://faucet.circle.com)),
  `export BUYER_PRIVATE_KEY=0x…`, see `run_real_testnet.py` + `buyer_real.py`
  (policy pre-checks still apply before any signature).

## Repo layout

```
run.py  run_real_testnet.py  requirements.txt
src/coin_purse/{policy,buyer,buyer_real,agent,stalls,ledger}.py
tests/test_policy.py
sample-run/{decision-record.jsonl,decision-record.json,decisions.db,report.json}
```
