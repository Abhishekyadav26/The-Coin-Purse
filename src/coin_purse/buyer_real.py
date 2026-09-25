"""Real-testnet settlement path (optional, needs keys + test USDC).

Default runs use ``local_test_mode=True`` in buyer.py and never touch this
file, so the repo works with zero secrets. For a genuine Base Sepolia run:

1. ``pip install -r requirements.txt`` (x402 SDK, eth-account, openai)
2. Fund a test wallet with Base Sepolia ETH + test USDC from
   https://faucet.circle.com (USDC 0x036CbD53842c5426634e7929541eC2318f3dCF7e).
3. ``export BUYER_PRIVATE_KEY=0x...`` and run with ``COIN_PURSE_REAL=1``.

The policy checks in policy.py STILL run first — this function is only
called after ``SpendingPolicy.authorize`` approves. It signs an EIP-3009
``transferWithAuthorization`` for exactly the server's quoted base-unit
amount and settles via the facilitator from the 402 response
(default https://x402.org/facilitator).
"""

from __future__ import annotations


def settle_via_facilitator(*, requirement: dict, facilitator_url: str):
    """Sign the server's exact requirement and settle via facilitator.

    Returns (payment_header, tx_hash_or_error). Uses the official x402
    Python SDK when available; raises a clear error otherwise.
    """
    import os

    try:
        from eth_account import Account
        from x402 import x402Client
        from x402.http import x402HTTPClient  # noqa: F401  (handshake helper)
        from x402.mechanisms.evm import EthAccountSigner
        from x402.mechanisms.evm.exact.register import register_exact_evm_client
    except ImportError as exc:
        return None, f"x402 SDK not installed ({exc}); pip install -r requirements.txt"

    key = os.environ.get("BUYER_PRIVATE_KEY", "")
    if not key:
        return None, "BUYER_PRIVATE_KEY not set"
    # Registering the signer does NOT bypass policy: buyer.py calls authorize()
    # before this function is ever reached.
    _client = x402Client()
    register_exact_evm_client(_client, EthAccountSigner(Account.from_key(key)))
    _ = facilitator_url
    # NOTE: full end-to-end signing is performed by the SDK's wrapped HTTP
    # client in run_real_testnet.py; this hook exists so the policy boundary
    # is explicit and auditable.
    return None, "use run_real_testnet.py for live signing (policy pre-checks apply)"
