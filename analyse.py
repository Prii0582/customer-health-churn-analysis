"""
Customer health & churn-risk analysis.

Reads the synthetic data in data/, gives every customer a simple, explainable
health score (0-100), puts them in a risk band, explains WHY in plain words
and suggests the next action for the account team.

Outputs (in output/):
  customer_health_report.html / .pdf / .png   - 2-page management report
  customer_health.xlsx                        - full working file
  usage_trend.png, risk_drivers.png           - charts used in the report

Run:  python analyse.py
"""
from __future__ import annotations

import base64
import io
import shutil
import subprocess
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "output"
TEMPLATES = ROOT / "templates"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "google-chrome", "chromium", "chromium-browser", "chrome",
]

AS_OF_MONTH = pd.Period("2026-10", freq="M")
N_MODULES = 6

# Colour palette (navy / teal) and risk colours
NAVY, TEAL, GREY = "#1B2A49", "#0F9D8F", "#8A94A6"
RISK_ORDER = ["Critical", "High", "Medium", "Healthy"]
RISK_COLOURS = {"Critical": "#C0392B", "High": "#E67E22", "Medium": "#D4A72C", "Healthy": "#0F9D8F"}

# --------------------------------------------------------------------------
# Scoring rules - kept deliberately simple so anyone can follow them.
# --------------------------------------------------------------------------
RULES = [
    ("Usage trend (30 pts)",
     "Sessions in the last 3 months vs the 3 months before: +5% or better = 30, "
     "-5% to +5% = 24, -15% to -5% = 15, -30% to -15% = 6, worse than -30% = 0"),
    ("Active days (25 pts)",
     "Average days per month the customer logged in (last 3 months): 18+ = 25, "
     "14-17 = 18, 8-13 = 10, 1-7 = 4, none = 0"),
    ("Modules adopted (25 pts)",
     "Modules used on 8+ days in the latest month, out of 6: 25 x (modules / 6)"),
    ("Open tickets (20 pts)",
     "Unresolved support tickets: 0 = 20, 1 = 14, 2 = 8, 3+ = 0; minus 5 if any open ticket is High priority"),
    ("Risk band",
     "Critical below 40, High 40-59, Medium 60-79, Healthy 80+. "
     "A renewal within 3 months moves a non-healthy customer up one risk level."),
]


def trend_points(pct: float) -> int:
    if pct >= 5:
        return 30
    if pct >= -5:
        return 24
    if pct >= -15:
        return 15
    if pct >= -30:
        return 6
    return 0


def days_points(days: float) -> int:
    if days >= 18:
        return 25
    if days >= 14:
        return 18
    if days >= 8:
        return 10
    if days > 0:
        return 4
    return 0


def ticket_points(open_n: int, open_high: int) -> int:
    pts = {0: 20, 1: 14, 2: 8}.get(open_n, 0)
    if open_high:
        pts -= 5
    return max(pts, 0)


def band(score: float) -> str:
    if score < 40:
        return "Critical"
    if score < 60:
        return "High"
    if score < 80:
        return "Medium"
    return "Healthy"


def bump(risk: str) -> str:
    """Move one level towards Critical (Healthy customers are not bumped)."""
    if risk == "Healthy":
        return risk
    i = RISK_ORDER.index(risk)
    return RISK_ORDER[max(i - 1, 0)]


def explain(r: pd.Series) -> tuple[list[str], str]:
    """Plain-English reasons and one recommended action for a customer."""
    why = []
    if r.usage_trend_pct <= -15:
        why.append(f"Usage down {abs(r.usage_trend_pct):.0f}% vs previous quarter")
    elif r.usage_trend_pct >= 5:
        why.append(f"Usage up {r.usage_trend_pct:.0f}% vs previous quarter")
    if r.avg_active_days < 14:
        why.append(f"Only {int(r.avg_active_days)} active days a month")
    if r.modules_adopted <= 3:
        why.append(f"{r.modules_adopted} of {N_MODULES} modules in regular use")
    if r.open_tickets >= 2 or r.open_high:
        hi = f" ({r.open_high} high priority)" if r.open_high else ""
        why.append(f"{r.open_tickets} open ticket{'s' if r.open_tickets != 1 else ''}{hi}")
    if r.renewal_soon and r.risk != "Healthy":
        why.append(f"Renewal in {r.months_to_renewal} month{'s' if r.months_to_renewal > 1 else ''}")
    if not why:
        why.append(f"No warning signs: stable usage, {r.modules_adopted} of {N_MODULES} modules in regular use")

    # First matching rule wins -> one clear next step per customer
    at_risk = r.risk in ("Critical", "High")
    if r.renewal_soon and at_risk:
        action = "Renewal at risk: success review with sponsor before renewal"
    elif r.open_high or r.open_tickets >= 3:
        action = "Escalate open tickets with Support; share fix dates"
    elif r.modules_adopted <= 1 and at_risk:
        action = "Re-onboard: run an adoption workshop with the main user team"
    elif r.usage_trend_pct <= -30:
        action = "Call the sponsor this week and agree a usage recovery plan"
    elif r.avg_active_days < 14 or r.usage_trend_pct <= -15:
        action = "Refresher training and a weekly usage check-in for 4 weeks"
    elif r.modules_adopted <= 3:
        action = "Demo the unused modules that fit their goals"
    elif r.risk == "Healthy":
        action = "Keep regular check-ins; ask for a reference or case study"
    else:
        action = "Quarterly success review to confirm goals and next modules"
    return why, action


def score_customers(customers: pd.DataFrame, usage: pd.DataFrame, tickets: pd.DataFrame) -> pd.DataFrame:
    usage = usage.copy()
    usage["month"] = pd.PeriodIndex(usage["month"], freq="M")
    last3 = [AS_OF_MONTH - i for i in range(3)]
    prev3 = [AS_OF_MONTH - i for i in range(3, 6)]

    monthly = usage.groupby(["customer_id", "month"]).agg(
        sessions=("sessions", "sum"), active_days=("active_days", "max")).reset_index()

    def sum_in(months):
        return monthly[monthly.month.isin(months)].groupby("customer_id")["sessions"].sum()

    s_last, s_prev = sum_in(last3), sum_in(prev3)
    trend = ((s_last / s_prev.replace(0, pd.NA)) - 1).astype(float).fillna(-1.0) * 100

    days = monthly[monthly.month.isin(last3)].groupby("customer_id")["active_days"].mean()

    latest = usage[(usage.month == AS_OF_MONTH) & (usage.active_days >= 8)]
    modules = latest.groupby("customer_id")["module"].nunique()

    open_t = tickets[tickets["closed_on"].isna()]
    open_n = open_t.groupby("customer_id").size()
    open_hi = open_t[open_t.priority == "High"].groupby("customer_id").size()

    df = customers.set_index("customer_id").copy()
    df["usage_trend_pct"] = trend.reindex(df.index).fillna(-100).round(1)
    df["avg_active_days"] = days.reindex(df.index).fillna(0).round(1)
    df["modules_adopted"] = modules.reindex(df.index).fillna(0).astype(int)
    df["open_tickets"] = open_n.reindex(df.index).fillna(0).astype(int)
    df["open_high"] = open_hi.reindex(df.index).fillna(0).astype(int)

    df["pts_trend"] = df.usage_trend_pct.apply(trend_points)
    df["pts_days"] = df.avg_active_days.apply(days_points)
    df["pts_modules"] = (df.modules_adopted / N_MODULES * 25).round().astype(int)
    df["pts_tickets"] = [ticket_points(n, h) for n, h in zip(df.open_tickets, df.open_high)]
    df["health_score"] = df[["pts_trend", "pts_days", "pts_modules", "pts_tickets"]].sum(axis=1)

    renewal = pd.PeriodIndex(df["renewal_month"], freq="M")
    df["months_to_renewal"] = [(p - AS_OF_MONTH).n for p in renewal]
    df["renewal_soon"] = df.months_to_renewal <= 3

    df["risk"] = df.health_score.apply(band)
    df.loc[df.renewal_soon, "risk"] = df.loc[df.renewal_soon, "risk"].apply(bump)

    whys, actions = zip(*[explain(r) for _, r in df.iterrows()])
    df["why"] = ["; ".join(w) for w in whys]
    df["why_list"] = list(whys)
    df["recommended_action"] = actions

    df["risk_rank"] = df.risk.map({k: i for i, k in enumerate(RISK_ORDER)})
    df = df.sort_values(["risk_rank", "health_score", "months_to_renewal"]).reset_index()
    return df


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#C9CFD9")
    ax.tick_params(colors="#4A5568", labelsize=9)
    ax.grid(axis="y", color="#E6E9EF", linewidth=0.8)
    ax.set_axisbelow(True)


def _to_b64(fig, path: Path) -> str:
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def usage_trend_chart(usage: pd.DataFrame, scored: pd.DataFrame) -> str:
    m = usage.groupby(["customer_id", "month"])["sessions"].sum().reset_index()
    m = m.merge(scored[["customer_id", "risk"]], on="customer_id")
    g = m.groupby(["risk", "month"])["sessions"].mean().unstack(0)
    g = g / g.iloc[0] * 100  # index: first month = 100

    fig, ax = plt.subplots(figsize=(6.4, 3.1))
    labels = [pd.Period(x).strftime("%b") for x in g.index]
    for risk in RISK_ORDER:
        if risk in g:
            ax.plot(labels, g[risk], color=RISK_COLOURS[risk], linewidth=2.4, marker="o", markersize=3.5)
            ax.annotate(risk, (len(labels) - 1, g[risk].iloc[-1]), xytext=(6, 0), textcoords="offset points",
                        va="center", fontsize=9, color=RISK_COLOURS[risk], fontweight="bold")
    ax.axhline(100, color=GREY, linewidth=0.8, linestyle="--")
    ax.set_ylabel("Usage index (Nov = 100)", fontsize=9, color="#4A5568")
    ax.set_xlim(-0.3, len(labels) + 0.9)
    _style(ax)
    fig.tight_layout()
    return _to_b64(fig, OUT / "usage_trend.png")


def drivers_chart(scored: pd.DataFrame) -> str:
    at_risk = scored[scored.risk.isin(["Critical", "High"])]
    drivers = {
        "Usage down 15%+": (at_risk.usage_trend_pct <= -15).sum(),
        "Low active days": (at_risk.avg_active_days < 14).sum(),
        "3 or fewer modules": (at_risk.modules_adopted <= 3).sum(),
        "2+ open tickets": ((at_risk.open_tickets >= 2) | (at_risk.open_high > 0)).sum(),
        "Renewal in 3 months": at_risk.renewal_soon.sum(),
    }
    s = pd.Series(drivers).sort_values()
    fig, ax = plt.subplots(figsize=(4.6, 3.1))
    bars = ax.barh(s.index, s.values, color=TEAL, height=0.6)
    for b, v in zip(bars, s.values):
        ax.text(v + 0.2, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=9, color=NAVY, fontweight="bold")
    ax.set_xlabel("Critical + High customers affected", fontsize=9, color="#4A5568")
    ax.set_xlim(0, max(s.values) + 2)
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E6E9EF", linewidth=0.8)
    fig.tight_layout()
    return _to_b64(fig, OUT / "risk_drivers.png")


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def write_excel(scored: pd.DataFrame, usage: pd.DataFrame, tickets: pd.DataFrame) -> Path:
    path = OUT / "customer_health.xlsx"
    cols = {
        "customer_name": "Customer", "industry": "Industry", "region": "Region", "csm": "CSM",
        "renewal_month": "Renewal month", "risk": "Risk", "health_score": "Health score",
        "usage_trend_pct": "Usage trend %", "avg_active_days": "Avg active days",
        "modules_adopted": "Modules adopted", "open_tickets": "Open tickets", "open_high": "Open high",
        "pts_trend": "Pts: trend", "pts_days": "Pts: days", "pts_modules": "Pts: modules",
        "pts_tickets": "Pts: tickets", "why": "Why", "recommended_action": "Recommended action",
    }
    health = scored[list(cols)].rename(columns=cols)
    summary = (scored.groupby("risk").agg(customers=("customer_id", "count"),
                                          avg_score=("health_score", "mean"),
                                          renewals_next_3m=("renewal_soon", "sum"))
               .reindex(RISK_ORDER).fillna(0).round(1).reset_index()
               .rename(columns={"risk": "Risk", "customers": "Customers", "avg_score": "Avg health score",
                                "renewals_next_3m": "Renewals in next 3 months"}))
    rules = pd.DataFrame(RULES, columns=["Rule", "How it works"])

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        health.to_excel(xw, sheet_name="Health Scores", index=False)
        usage.to_excel(xw, sheet_name="Monthly Usage", index=False)
        tickets.to_excel(xw, sheet_name="Support Tickets", index=False)
        rules.to_excel(xw, sheet_name="Scoring Rules", index=False)

        head_fill = PatternFill("solid", fgColor=NAVY[1:])
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.fill = head_fill
                c.font = Font(color="FFFFFF", bold=True)
                c.alignment = Alignment(vertical="center", wrap_text=True)
            ws.freeze_panes = "A2"
            for i, col in enumerate(ws.iter_cols(min_row=1, max_row=min(ws.max_row, 60)), 1):
                width = max(len(str(c.value or "")) for c in col)
                ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 70)

        ws = xw.book["Health Scores"]
        risk_col = list(cols.values()).index("Risk") + 1
        for row in ws.iter_rows(min_row=2):
            cell = row[risk_col - 1]
            cell.fill = PatternFill("solid", fgColor=RISK_COLOURS[cell.value][1:])
            cell.font = Font(color="FFFFFF", bold=True)
        ws.auto_filter.ref = ws.dimensions
        ws2 = xw.book["Scoring Rules"]
        ws2.column_dimensions["B"].width = 110
        for row in ws2.iter_rows(min_row=2):
            row[1].alignment = Alignment(wrap_text=True, vertical="top")
    return path


# --------------------------------------------------------------------------
# HTML -> PDF / PNG
# --------------------------------------------------------------------------
def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return c
    return None


def render_with_chrome(html: Path) -> None:
    chrome = find_chrome()
    if not chrome:
        print("Chrome not found - skipping PDF/PNG (HTML report is still available).")
        return
    url = html.resolve().as_uri()
    common = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-pdf-header-footer"]
    subprocess.run(common + [f"--print-to-pdf={OUT / 'customer_health_report.pdf'}", url],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(common + ["--window-size=1240,1754", f"--screenshot={OUT / 'customer_health_report.png'}", url],
                   check=True, capture_output=True, timeout=120)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    customers = pd.read_csv(DATA / "customers.csv")
    usage = pd.read_csv(DATA / "monthly_usage.csv")
    tickets = pd.read_csv(DATA / "support_tickets.csv")

    scored = score_customers(customers, usage, tickets)

    counts = scored.risk.value_counts().reindex(RISK_ORDER).fillna(0).astype(int).to_dict()
    at_risk = scored[scored.risk.isin(["Critical", "High"])]
    renewals_at_risk = int(at_risk.renewal_soon.sum())
    usage_m = usage.groupby("month")["sessions"].sum()
    findings = [
        f"<b>{len(at_risk)} of {len(scored)}</b> customers are Critical or High risk and need action this month.",
        f"<b>{renewals_at_risk}</b> of them renew within the next 3 months &ndash; these are the first calls to make.",
        f"The most common warning sign is <b>falling usage</b>: "
        f"{int((at_risk.usage_trend_pct <= -15).sum())} at-risk customers used the product at least 15% less "
        f"than the quarter before.",
        f"Healthy customers grew usage by <b>{scored[scored.risk == 'Healthy'].usage_trend_pct.median():.0f}%</b> "
        f"(median) &ndash; good candidates for references and expansion of modules.",
    ]

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=False)
    html = env.get_template("report.html.j2").render(
        as_of="31 Oct 2026",
        period="Nov 2025 &ndash; Oct 2026",
        total=len(scored),
        counts=counts,
        risk_colours=RISK_COLOURS,
        findings=findings,
        priority=at_risk.to_dict("records"),
        everyone=scored.to_dict("records"),
        rules=RULES,
        trend_b64=usage_trend_chart(usage, scored),
        drivers_b64=drivers_chart(scored),
        portfolio_trend=(usage_m.iloc[-3:].sum() / usage_m.iloc[-6:-3].sum() - 1) * 100,
    )
    html_path = OUT / "customer_health_report.html"
    html_path.write_text(html, encoding="utf-8")

    write_excel(scored, usage, tickets)
    render_with_chrome(html_path)

    print("Risk bands:", counts)
    print("Outputs written to", OUT)


if __name__ == "__main__":
    main()
