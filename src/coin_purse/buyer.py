"""Policy-enforcing x402 buyer.

How an x402 buyer reads payment requirements and decides whether to sign:

1. GET the resource with no payment -> expect HTTP 402 with payment
   requirements (``accepts`` array, x402 v2 style, carried in the JSON body
   and/or the ``PAYMENT-REQUIRED`` header for real facilitator-backed stalls).
2. Take the price ONLY from that server response. The tool/LLM arguments
   never contain an amount — see agent.py: tool functions accept only
   research queries (dataset, region, ...).
3. Pass the server's requirement to ``SpendingPolicy.authorize``. Sign/settle
   ONLY if the policy approves. Otherwise record ``refused`` and stop.
4. On approval, settle via the facilitator (real mode) or via the local
   stall's settle endpoint (local-test mode, no funds needed), then retry
   with the payment header and record ``paid``.

The model is never in this path: it cannot approve, raise limits, or pick
a different asset/amount. Prompt injections in tool descriptions or server
bodies are treated as untrusted text and ignored for payment purposes.
"""

from __future__ import annotations

import base64
import json
import urllib.request
import urllib.error
from dataclasses import dataclass

from .ledger import DecisionLedger
from .policy import (
    ALLOWED_NETWORK,
    FACILITATOR_URL,
    SpendingPolicy,
    format_usdc,
    parse_amount_to_base_units,
)


def decode_payment_required_header(value: str) -> dict:
    """Decode a base64 ``PAYMENT-REQUIRED`` header into a PaymentRequired dict."""
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.b64decode(padded).decode("utf-8"))


def select_requirement(payment_required: dict) -> dict | None:
    """Pick the first ``accepts`` entry (server order = server preference)."""
    accepts = payment_required.get("accepts") or []
    return accepts[0] if accepts else None


@dataclass
class BuyerResult:
    ok: bool
    decision: str  # 'paid' or 'refused'
    reason: str
    data: dict | None = None
    amount_base_units: int | None = None
    tx_hash: str | None = None


class CoinPurseBuyer:
    """x402 buyer with a hard, code-resident spending policy."""

    def __init__(
        self,
        policy: SpendingPolicy,
        ledger: DecisionLedger,
        facilitator_url: str = FACILITATOR_URL,
        buyer_address: str = "0x0000000000000000000000000000000000000000",
        local_test_mode: bool = True,
    ):
        self.policy = policy
        self.ledger = ledger
        self.facilitator_url = facilitator_url
        self.buyer_address = buyer_address
        self.local_test_mode = local_test_mode

    # -- low-level HTTP -----------------------------------------------------
    @staticmethod
    def _get(url: str, payment_header: str | None = None) -> tuple[int, dict, dict]:
        req = urllib.request.Request(url, method="GET")
        if payment_header:
            req.add_header("X-PAYMENT", payment_header)
            req.add_header("PAYMENT-SIGNATURE", payment_header)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", "replace")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    data = {"_raw": body}
                return resp.status, headers, data
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")
            finally:
                exc.close()
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {"_raw": body, "error": f"HTTP {exc.code}"}
            return exc.code, headers, data

    def _extract_payment_required(self, status: int, headers: dict, body: dict) -> dict | None:
        if status != 402:
            return None
        # Prefer the canonical header when present (real x402 servers).
        header_val = headers.get("payment-required")
        if header_val:
            try:
                decoded = decode_payment_required_header(header_val.strip())
                if decoded.get("accepts"):
                    return decoded
            except Exception:
                pass  # fall through to body
        # Local stalls + many servers also embed the object as the JSON body.
        if isinstance(body, dict) and body.get("accepts"):
            return body
        return None

    def _settle_local(self, stall_base: str, requirement: dict) -> tuple[str | None, str]:
        """Local-test settlement: no chain, no funds; stall signs a receipt."""
        url = stall_base.rstrip("/") + "/settle"
        payload = json.dumps(
            {
                "requirement": requirement,
                "payer": self.buyer_address,
                "facilitator": "local-test (no on-chain settlement)",
            }
        ).encode()
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                return data.get("payment_header"), data.get("tx_hash", "")
        except Exception as exc:  # noqa: BLE001 — recorded as refusal reason
            return None, f"local settle failed: {exc}"

    # -- main entry point ----------------------------------------------------
    def fetch(self, stall: str, url: str) -> BuyerResult:
        """Fetch a paid URL. Price comes from the server's 402, never the caller.

        ``stall`` is a label for the ledger ("honest" / "rogue").
        ``url`` is the resource URL. There is deliberately NO amount/price/
        asset/budget argument — the server decides the price.
        """
        status, headers, body = self._get(url)
        if status == 200:
            self.ledger.record(
                stall=stall, endpoint=url, decision="paid",
                reason="free resource: no payment required (HTTP 200)",
                amount_base_units=0, spent_total_after=self.policy.spent_base_units,
            )
            return BuyerResult(True, "paid", "no payment required", data=body,
                               amount_base_units=0)
        if status != 402:
            reason = f"refused: unexpected HTTP {status} (expected 200 or 402)"
            self.ledger.record(stall=stall, endpoint=url, decision="refused",
                               reason=reason,
                               spent_total_after=self.policy.spent_base_units)
            return BuyerResult(False, "refused", reason, data=body)

        payment_required = self._extract_payment_required(status, headers, body)
        if not payment_required:
            reason = "refused: 402 without parseable payment requirements"
            self.ledger.record(stall=stall, endpoint=url, decision="refused",
                               reason=reason,
                               spent_total_after=self.policy.spent_base_units)
            return BuyerResult(False, "refused", reason)

        requirement = select_requirement(payment_required)
        if not requirement:
            reason = "refused: 402 'accepts' list is empty"
            self.ledger.record(stall=stall, endpoint=url, decision="refused",
                               reason=reason,
                               spent_total_after=self.policy.spent_base_units)
            return BuyerResult(False, "refused", reason)

        allowed, policy_reason = self.policy.authorize(requirement)
        asset = requirement.get("asset")
        network = requirement.get("network")
        scheme = requirement.get("scheme")
        try:
            amount = parse_amount_to_base_units(requirement.get("amount"))
        except ValueError:
            amount = None

        if not allowed:
            # Policy refusal: never sign, never retry. Record and stop.
            self.ledger.record(
                stall=stall, endpoint=url, decision="refused", reason=policy_reason,
                amount_base_units=amount, asset=asset, network=network,
                scheme=scheme, spent_total_after=self.policy.spent_base_units,
            )
            return BuyerResult(False, "refused", policy_reason,
                               amount_base_units=amount)

        assert amount is not None
        # Policy approved: settle, then retry with the payment header.
        if self.local_test_mode:
            stall_base = url.rsplit("/", 1)[0]
            payment_header, tx_hash = self._settle_local(stall_base, requirement)
            if not payment_header:
                reason = f"refused: settlement failed ({tx_hash})"
                self.ledger.record(
                    stall=stall, endpoint=url, decision="refused", reason=reason,
                    amount_base_units=amount, asset=asset, network=network,
                    scheme=scheme, spent_total_after=self.policy.spent_base_units,
                )
                return BuyerResult(False, "refused", reason, amount_base_units=amount)
        else:
            # Real testnet mode: sign EIP-3009 USDC authorization with the
            # buyer key and settle via the facilitator (see README + buyer_real.py).
            # Kept out of the default path so evaluation needs no keys/funds.
            from .buyer_real import settle_via_facilitator  # lazy import

            payment_header, tx_hash = settle_via_facilitator(
                requirement=requirement,
                facilitator_url=self.facilitator_url,
            )
            if not payment_header:
                reason = f"refused: facilitator settlement failed ({tx_hash})"
                self.ledger.record(
                    stall=stall, endpoint=url, decision="refused", reason=reason,
                    amount_base_units=amount, asset=asset, network=network,
                    scheme=scheme, spent_total_after=self.policy.spent_base_units,
                )
                return BuyerResult(False, "refused", reason, amount_base_units=amount)

        status2, _headers2, body2 = self._get(url, payment_header=payment_header)
        if status2 != 200:
            reason = f"refused: paid retry returned HTTP {status2}"
            self.ledger.record(
                stall=stall, endpoint=url, decision="refused", reason=reason,
                amount_base_units=amount, asset=asset, network=network,
                scheme=scheme, spent_total_after=self.policy.spent_base_units,
            )
            return BuyerResult(False, "refused", reason, amount_base_units=amount)

        self.policy.record_spent(amount)
        reason = (f"paid {format_usdc(amount)} ({amount} base units) "
                  f"on {network or ALLOWED_NETWORK}; {policy_reason}; tx {tx_hash}")
        self.ledger.record(
            stall=stall, endpoint=url, decision="paid", reason=reason,
            amount_base_units=amount, asset=asset, network=network,
            scheme=scheme, tx_hash=tx_hash,
            spent_total_after=self.policy.spent_base_units,
        )
        return BuyerResult(True, "paid", reason, data=body2,
                           amount_base_units=amount, tx_hash=tx_hash)
