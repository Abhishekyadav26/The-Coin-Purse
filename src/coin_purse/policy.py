"""Spending policy for Arjun's research agent.

SECURITY DESIGN (read this before changing anything):

1. The spending policy lives HERE, in code — never in the LLM prompt.
   A model cannot talk its way past it: tool descriptions, server messages,
   or prompt injections like "your budget has been raised" have no effect
   because the agent loop never consults the model for payment approval.
   Only :meth:`SpendingPolicy.authorize` decides, and it reads nothing
   the model wrote except the *query* (dataset name, region, ...).

2. The SERVER's 402 payment requirements decide the price — never the
   request, never the LLM. The buyer parses the ``accepts`` entry from the
   402 response and authorizes exactly that ``amount``. There is no
   ``amount``/``price``/``budget`` parameter on any tool the model can call,
   so the model cannot set, lower, or raise a price.

3. All money is handled in integer BASE UNITS (USDC has 6 decimals, so
   $1.00 == 1_000_000 base units). Floats/doubles are never used for
   decisions — only for human-readable display. This avoids rounding and
   decimal-parsing exploits (e.g. "0.1 + 0.2 != 0.3", "4.9900001" tricks).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Network / asset constants (Base Sepolia testnet) -----------------------
# Base Sepolia chain id 84532, CAIP-2 identifier used by x402 v2.
ALLOWED_NETWORK = "eip155:84532"
ALLOWED_NETWORKS = frozenset({ALLOWED_NETWORK, "base-sepolia"})
# Test USDC on Base Sepolia (6 decimals). Lower-cased for comparison.
USDC_BASE_SEPOLIA = "0x036cbd53842c5426634e7929541e2318f3dcf7e"
ALLOWED_ASSETS = frozenset({USDC_BASE_SEPOLIA})
ALLOWED_SCHEMES = frozenset({"exact"})

# --- Money: integer base units only ------------------------------------------
USDC_DECIMALS = 6
USDC_BASE_UNITS_PER_USDC = 1_000_000  # 10 ** 6, integer

# Arjun's purse: five dollars of test USDC for the whole run.
MAX_TOTAL_BASE_UNITS = 5_000_000  # $5.00
# Per-call ceiling: a single row of data must never cost more than this.
# Deliberately below the rogue stall's $4.99 (4_990_000) quote.
MAX_PER_CALL_BASE_UNITS = 250_000  # $0.25

# Public x402 testnet facilitator (covers Base Sepolia).
FACILITATOR_URL = "https://x402.org/facilitator"


def format_usdc(base_units: int) -> str:
    """Human-readable display only. Never use the result for decisions."""
    dollars = base_units // USDC_BASE_UNITS_PER_USDC
    cents_remainder = base_units % USDC_BASE_UNITS_PER_USDC
    # 6-decimal fixed rendering without floats.
    return f"${dollars}.{cents_remainder:06d} USDC"


def parse_amount_to_base_units(amount: object) -> int:
    """Strictly parse an x402 ``amount`` (string of base-unit integer).

    Raises ValueError on anything that is not a plain integer string.
    Floats, decimals ("4.99"), empty strings, negatives are all rejected.
    """
    if isinstance(amount, int):
        value = amount
    elif isinstance(amount, str):
        text = amount.strip()
        if not text or text.startswith(("-", "+")):
            raise ValueError(f"invalid amount: {amount!r}")
        if not text.isdigit():
            raise ValueError(f"amount must be base-unit integer string, got {amount!r}")
        value = int(text)
    else:
        raise ValueError(f"invalid amount type: {type(amount).__name__}: {amount!r}")
    if value <= 0:
        raise ValueError(f"amount must be positive, got {value}")
    return value


@dataclass
class SpendingPolicy:
    """Hard spending limits enforced in code.

    The LLM is never consulted. Instances track cumulative spend for the run
    so a sequence of small charges cannot exceed the total budget.
    """

    max_per_call_base_units: int = MAX_PER_CALL_BASE_UNITS
    max_total_base_units: int = MAX_TOTAL_BASE_UNITS
    allowed_assets: frozenset = field(default_factory=lambda: ALLOWED_ASSETS)
    allowed_networks: frozenset = field(default_factory=lambda: ALLOWED_NETWORKS)
    allowed_schemes: frozenset = field(default_factory=lambda: ALLOWED_SCHEMES)
    spent_base_units: int = 0  # integer base units, mutated only on paid settlement

    def remaining_base_units(self) -> int:
        return self.max_total_base_units - self.spent_base_units

    def authorize(self, requirement: dict) -> tuple[bool, str]:
        """Decide whether a single 402 ``accepts`` entry may be paid.

        ``requirement`` is the server's payment requirement (scheme, network,
        asset, amount). Returns (allowed, reason). Never raises for policy
        rejections — only for malformed input, which is treated as refuse
        by the caller.
        """
        scheme = str(requirement.get("scheme", ""))
        network = str(requirement.get("network", ""))
        asset = str(requirement.get("asset", "")).lower()
        raw_amount = requirement.get("amount")

        if scheme not in self.allowed_schemes:
            return False, f"refused: unsupported scheme {scheme!r} (only 'exact' allowed)"
        if network not in self.allowed_networks:
            return False, f"refused: unsupported network {network!r} (only {ALLOWED_NETWORK})"
        if asset not in self.allowed_assets:
            return False, (
                f"refused: untrusted asset {requirement.get('asset')!r} "
                f"(only test USDC {USDC_BASE_SEPOLIA} on Base Sepolia)"
            )
        try:
            amount_base_units = parse_amount_to_base_units(raw_amount)
        except ValueError as exc:
            return False, f"refused: malformed amount {raw_amount!r} ({exc})"

        if amount_base_units > self.max_per_call_base_units:
            return (
                False,
                f"refused: price {format_usdc(amount_base_units)} "
                f"({amount_base_units} base units) exceeds per-call limit "
                f"{format_usdc(self.max_per_call_base_units)}",
            )
        if self.spent_base_units + amount_base_units > self.max_total_base_units:
            return (
                False,
                f"refused: price {format_usdc(amount_base_units)} would exceed "
                f"total budget {format_usdc(self.max_total_base_units)} "
                f"(already spent {format_usdc(self.spent_base_units)})",
            )
        return True, (
            f"approved: {format_usdc(amount_base_units)} within per-call "
            f"{format_usdc(self.max_per_call_base_units)} and total "
            f"{format_usdc(self.max_total_base_units)} "
            f"(spent {format_usdc(self.spent_base_units)} so far)"
        )

    def record_spent(self, amount_base_units: int) -> None:
        """Add a settled payment to the running total. Integer arithmetic."""
        if amount_base_units <= 0:
            raise ValueError("spent amount must be positive")
        self.spent_base_units += amount_base_units
