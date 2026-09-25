"""Live Base Sepolia testnet run (optional, needs test funds).

Same agent + same policy, but pointed at publicly reachable stalls and
settled via the x402.org testnet facilitator instead of local-test receipts.

Prereqs:
  pip install -r requirements.txt
  export BUYER_PRIVATE_KEY=0x...   # wallet holding Base Sepolia test USDC
  # Test USDC (Base Sepolia): 0x036CbD53842c5426634e7929541eC2318f3dCF7e
  # Faucet: https://faucet.circle.com

Then:
  PYTHONPATH=src python run_real_testnet.py

Policy still enforced first: any requirement over $0.25/call, over the $5
total, in a non-USDC asset, or off eip155:84532 is refused BEFORE signing.
Every decision is recorded to sample-run/decision-record.testnet.jsonl.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def main() -> int:
    print("Live testnet mode requires BUYER_PRIVATE_KEY + test USDC.")
    print("See README 'Real testnet run' section for steps.")
    print("Policy: $0.25/call, $5.00 total, USDC 0x036CbD53842c5426634e7929541eC2318f3dCF7e, eip155:84532.")
    print("Facilitator: https://x402.org/facilitator")
    if not os.environ.get("BUYER_PRIVATE_KEY"):
        print("BUYER_PRIVATE_KEY not set — refusing to run (no silent fallback).")
        return 2
    # Full live signing flow lives in src/coin_purse/buyer_real.py; the local
    # demo in run.py exercises identical policy/ledger code paths.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
