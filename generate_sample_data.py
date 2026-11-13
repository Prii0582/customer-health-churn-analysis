"""
Generate synthetic sample data for the customer health analysis.

Everything here is fictional: 40 B2B software customers ("Customer A01" ...),
12 months of product usage per module, support tickets and a renewal month.
A fixed random seed means the same data is produced every time.

Run:  python generate_sample_data.py
Writes: data/customers.csv, data/monthly_usage.csv, data/support_tickets.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_CUSTOMERS = 40
MODULES = ["Dashboards", "Reports", "Inventory", "Quality", "Planning", "Mobile App"]
MONTHS = pd.period_range("2025-11", "2026-10", freq="M")  # 12 months
AS_OF = pd.Timestamp("2026-10-31")

INDUSTRIES = ["Manufacturing", "Logistics", "Retail", "Healthcare", "Engineering"]
REGIONS = ["North", "South", "East", "West"]
CSMS = ["CSM 1", "CSM 2", "CSM 3", "CSM 4"]

# Customer "stories" used to shape the synthetic data so the analysis has
# something realistic to find. Weights add up to 1.
ARCHETYPES = {
    "steady_growth": 0.32,   # healthy, growing usage
    "stable":        0.26,   # flat usage, decent adoption
    "slow_decline":  0.15,   # usage drifting down
    "sharp_drop":    0.08,   # usage fell off a cliff recently
    "low_adoption":  0.11,   # only one or two modules used
    "ticket_heavy":  0.08,   # usage ok but many unresolved issues
}

DATA_DIR = Path(__file__).parent / "data"


def usage_multiplier(archetype: str, m: int, rng: np.random.Generator) -> float:
    """Return a 0..~1.4 multiplier for month index m (0..11)."""
    if archetype == "steady_growth":
        base = 0.85 + 0.025 * m
    elif archetype == "stable":
        base = 0.95
    elif archetype == "slow_decline":
        base = 1.0 - 0.03 * m
    elif archetype == "sharp_drop":
        base = 1.0 if m < 8 else 0.6 - 0.05 * (m - 8)
    elif archetype == "low_adoption":
        base = 0.75
    else:  # ticket_heavy
        base = 0.95 - 0.01 * m
    return max(0.0, base + rng.normal(0, 0.04))


def main() -> None:
    rng = np.random.default_rng(SEED)
    DATA_DIR.mkdir(exist_ok=True)

    names = list(ARCHETYPES)
    probs = list(ARCHETYPES.values())

    customers, usage_rows, tickets = [], [], []
    ticket_no = 1

    for i in range(1, N_CUSTOMERS + 1):
        cid = f"A{i:02d}"
        archetype = rng.choice(names, p=probs)
        onboarded = pd.Timestamp("2022-01-01") + pd.Timedelta(days=int(rng.integers(0, 1100)))
        renewal = (AS_OF.to_period("M") + int(rng.integers(1, 13))).strftime("%Y-%m")
        licensed_users = int(rng.choice([10, 15, 20, 25, 40, 60]))

        customers.append({
            "customer_id": cid,
            "customer_name": f"Customer {cid}",
            "industry": rng.choice(INDUSTRIES),
            "region": rng.choice(REGIONS),
            "csm": rng.choice(CSMS),
            "onboarded_on": onboarded.date().isoformat(),
            "licensed_users": licensed_users,
            "renewal_month": renewal,
        })

        # Which modules does this customer use at all?
        if archetype == "low_adoption":
            n_mod = int(rng.integers(1, 3))
        elif archetype in ("steady_growth", "stable"):
            n_mod = int(rng.integers(4, 7))
        else:
            n_mod = int(rng.integers(3, 6))
        used = set(rng.choice(MODULES, size=n_mod, replace=False))

        # Modules that fall out of use in the second half for decliners
        dropped = set()
        if archetype in ("slow_decline", "sharp_drop") and n_mod > 2:
            dropped = set(rng.choice(sorted(used), size=int(rng.integers(1, 3)), replace=False))

        for m, month in enumerate(MONTHS):
            mult = usage_multiplier(archetype, m, rng)
            for mod in MODULES:
                if mod not in used or (mod in dropped and m >= 7):
                    days, users, sessions = 0, 0, 0
                else:
                    days = int(np.clip(round(19 * mult + rng.normal(0, 1.5)), 0, 22))
                    users = int(np.clip(round(licensed_users * 0.7 * mult + rng.normal(0, 1.5)), 0, licensed_users))
                    sessions = int(max(0, round(users * days * 0.9 + rng.normal(0, 5)))) if days and users else 0
                    if sessions == 0:
                        days = users = 0
                usage_rows.append({
                    "customer_id": cid,
                    "month": str(month),
                    "module": mod,
                    "active_days": days,
                    "active_users": users,
                    "sessions": sessions,
                })

        # Support tickets over the year
        lam = {"ticket_heavy": 8, "sharp_drop": 4, "slow_decline": 3.5}.get(archetype, 2.5)
        for _ in range(int(rng.poisson(lam))):
            opened = AS_OF - pd.Timedelta(days=int(rng.integers(1, 365)))
            priority = rng.choice(["Low", "Medium", "High"], p=[0.4, 0.4, 0.2])
            # recent tickets and troubled accounts are more likely still open
            p_open = 0.05 + (0.30 if archetype in ("ticket_heavy", "sharp_drop") else 0)
            p_open += 0.25 if (AS_OF - opened).days < 45 else 0
            is_open = rng.random() < min(p_open, 0.9)
            closed = None if is_open else min(AS_OF, opened + pd.Timedelta(days=int(rng.integers(1, 25))))
            tickets.append({
                "ticket_id": f"T{ticket_no:04d}",
                "customer_id": cid,
                "opened_on": opened.date().isoformat(),
                "closed_on": closed.date().isoformat() if closed is not None else "",
                "priority": priority,
                "category": rng.choice(["Bug", "How-to", "Data issue", "Access", "Feature request"]),
                "module": rng.choice(sorted(used)),
            })
            ticket_no += 1

    pd.DataFrame(customers).to_csv(DATA_DIR / "customers.csv", index=False)
    pd.DataFrame(usage_rows).to_csv(DATA_DIR / "monthly_usage.csv", index=False)
    pd.DataFrame(tickets).sort_values("opened_on").to_csv(DATA_DIR / "support_tickets.csv", index=False)
    print(f"Wrote {len(customers)} customers, {len(usage_rows)} usage rows, {len(tickets)} tickets to {DATA_DIR}")


if __name__ == "__main__":
    main()
