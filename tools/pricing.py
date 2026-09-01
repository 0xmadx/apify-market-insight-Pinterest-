"""What to charge, worked from YOUR real bills — not a guessed public rate.

WHY THIS TAKES COSTS AS INPUTS INSTEAD OF KNOWING THEM
--------------------------------------------------------
Looking up "GCP e2-medium monthly price" and "Apify compute unit price" turned
up THREE different numbers per search, none agreeing, all claiming to be
current. That is the same shape of problem as the DeepSeek pricing mix-up
earlier and the Upstash "Prod Pack" surprise before that: a plausible number
presented with more confidence than the evidence supports.

So this tool asks for costs pulled from actual invoices/console pages, the
same way the Upstash bill itself (not a guess about Upstash's pricing) is what
actually explained the $32.34 charge. A guessed default here would be a wrong
number with a green checkmark next to it — worse than an honest blank.

WHAT IT DOES NOT DO
--------------------
It does not recommend WHETHER to publish anything, and it is not wired into
any of the four agents. Pricing math is planning material; the decision to
list an Actor publicly is the operator's, stated explicitly and not something
any tool here should nudge toward.
"""
import argparse
import sys

# Measured on this project's own deployed actor, 2026-08-27 (see
# docs/DEPLOY-LOG.md and .actor/actor.json's defaultMemoryMbytes history):
# 0.0069 CU at the old 4096 MB allocation, ~16x cheaper at the current 256 MB
# pin. This is the one number in this file that IS a real measurement, not a
# looked-up rate -- it comes from this project's own run, not a search result.
MEASURED_CU_PER_RUN = 0.0069 / 16


def compute_cost_per_run(cu_price_usd):
    """Apify platform cost for one typical run, at a $/CU rate YOU supply.

    Look up your own rate on https://console.apify.com/billing — it depends on
    your plan tier, and public sources disagreed by nearly 2x when checked.
    """
    return MEASURED_CU_PER_RUN * cu_price_usd


def amortized_account_cost(cost_per_account_usd, accounts_per_month):
    """Turn a ONE-TIME cost (buying/replacing a Pinterest account) into a fair
    monthly figure, by spreading it over how often you expect to pay it.

    Why this cannot just be added to the monthly total as-is: a one-time cost
    dropped straight into --other would be charged EVERY month forever, which
    overcounts it by however many months you are not actually replacing an
    account. And silently treating it as zero when it is real is the opposite
    mistake -- the one this whole file exists to refuse. Amortizing is the
    only honest way to fold a one-time cost into a recurring total.
    """
    return cost_per_account_usd * accounts_per_month


def break_even(fixed_costs_usd, runs_per_month):
    """The price per run that exactly covers fixed monthly costs. Below this,
    every run is a loss regardless of how the platform bills compute.
    """
    if runs_per_month <= 0:
        raise ValueError("runs_per_month must be positive — dividing by zero "
                         "or a negative volume is not a price, it is an error")
    return fixed_costs_usd / runs_per_month


def suggested_price(fixed_costs_usd, runs_per_month, margin_pct,
                    cu_price_usd=None):
    """Break-even plus a margin, plus the (usually negligible) compute floor.

    Returns a dict rather than a bare number, because a single dollar figure
    with no breakdown is exactly the kind of report that made the Upstash
    charge unreadable until the invoice was opened.
    """
    be = break_even(fixed_costs_usd, runs_per_month)
    compute = compute_cost_per_run(cu_price_usd) if cu_price_usd else 0.0
    price = be * (1 + margin_pct / 100) + compute
    return {
        "break_even_per_run": round(be, 4),
        "compute_cost_per_run": round(compute, 6),
        "margin_pct": margin_pct,
        "suggested_price_per_run": round(price, 4),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gcp", type=float, default=None,
                    help="monthly GCP VM cost in USD, from your actual bill")
    ap.add_argument("--proxies", type=float, default=None,
                    help="monthly proxy cost in USD (Webshare or similar)")
    ap.add_argument("--other", type=float, default=0.0,
                    help="anything else recurring: AdsPower subscription, "
                         "Upstash if it stops being free, etc.")
    ap.add_argument("--runs-per-month", type=int, default=None,
                    help="how many PAYING runs you expect in a month")
    ap.add_argument("--margin", type=float, default=50.0,
                    help="target margin over break-even, as a percent (default 50)")
    ap.add_argument("--cu-price", type=float, default=None,
                    help="your Apify $/compute-unit rate, from "
                         "console.apify.com/billing — omit to leave compute "
                         "cost out of the estimate rather than guess it")
    ap.add_argument("--account-cost", type=float, default=None,
                    help="ONE-TIME cost in USD to acquire/replace one "
                         "Pinterest account. Requires --accounts-per-month "
                         "too, so it can be amortized rather than either "
                         "over- or under-counted")
    ap.add_argument("--accounts-per-month", type=float, default=None,
                    help="how many accounts you expect to create or replace "
                         "per month, on average — fractions are fine (0.5 = "
                         "one every two months)")
    args = ap.parse_args()

    # Both or neither. One without the other is not a smaller mistake, it is
    # a different mistake in each direction: --account-cost alone with no
    # rate would either be dropped (undercounting a real cost) or charged
    # every month forever (overcounting it); --accounts-per-month alone has
    # nothing to multiply.
    if (args.account_cost is None) != (args.accounts_per_month is None):
        print("--account-cost and --accounts-per-month must be given "
              "together, or not at all — one without the other cannot be "
              "amortized correctly.", file=sys.stderr)
        return 2

    # Refuse rather than guess. A missing cost silently treated as $0 would
    # produce a price that looks calculated but is actually wrong — the exact
    # failure this project exists to prevent, applied to a dollar figure
    # instead of a Pinterest number.
    missing = [name for name, val in
              (("--gcp", args.gcp), ("--proxies", args.proxies),
               ("--runs-per-month", args.runs_per_month)) if val is None]
    if missing:
        print("Need real numbers for: " + ", ".join(missing), file=sys.stderr)
        print("", file=sys.stderr)
        print("Where to find them:", file=sys.stderr)
        print("  --gcp             Google Cloud Console -> Billing -> this "
              "project, the e2-medium VM's actual charge", file=sys.stderr)
        print("  --proxies         your Webshare invoice", file=sys.stderr)
        print("  --runs-per-month  your own estimate of paying usage — a "
              "guess is fine here, a missing platform cost is not",
              file=sys.stderr)
        return 2

    account_amortized = 0.0
    if args.account_cost is not None:
        account_amortized = amortized_account_cost(args.account_cost,
                                                    args.accounts_per_month)

    fixed = args.gcp + args.proxies + args.other + account_amortized
    result = suggested_price(fixed, args.runs_per_month, args.margin,
                             args.cu_price)

    breakdown = f"GCP ${args.gcp:.2f} + proxies ${args.proxies:.2f}"
    if args.other:
        breakdown += f" + other ${args.other:.2f}"
    if args.account_cost is not None:
        breakdown += (f" + accounts ${account_amortized:.4f} "
                      f"(${args.account_cost:.2f} x "
                      f"{args.accounts_per_month:g}/mo)")
    print(f"  fixed monthly costs   ${fixed:.2f}  ({breakdown})")
    print(f"  expected runs/month   {args.runs_per_month}")
    print(f"  break-even per run    ${result['break_even_per_run']:.4f}")
    if args.cu_price:
        print(f"  + compute cost/run    ${result['compute_cost_per_run']:.6f}"
              f"  (at ${args.cu_price:.2f}/CU — negligible at this scale)")
    else:
        print("  compute cost/run      not included — pass --cu-price to add it "
              "(it will barely move the total; measured at "
              f"{MEASURED_CU_PER_RUN:.6f} CU/run)")
    print(f"  + {args.margin:.0f}% margin")
    print(f"  = suggested price     ${result['suggested_price_per_run']:.4f} "
          f"per run")
    print()
    print("  This is planning math only. It does not decide whether or when "
          "to list anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
