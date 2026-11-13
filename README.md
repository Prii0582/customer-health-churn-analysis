# Customer Health and Churn Risk Analysis

A small analysis that finds which B2B software customers may leave, gives the reason in plain words, and suggests the next step for each one.

The data here is dummy data. I do similar customer tracking at work; I can't share that, so I rebuilt the idea with made-up data.

## Sample output

![Customer health report, page 1](docs/preview.png)

Sample report generated from the dummy data (page 1 of 2). Full PDF: [output/customer_health_report.pdf](output/customer_health_report.pdf)

## Why I made this

A customer success team looks after many business customers. Usage data, support tickets and renewal dates are in different places, so the team often finds out a customer is unhappy too late, close to renewal. I wanted one simple monthly view that tells:

- Which customers are at risk right now
- Why each one is at risk
- What to do, and whom to call first

## What it does

1. Uses 12 months of product usage for 40 dummy customers across 6 product modules, plus support tickets and renewal months.
2. Gives each customer a health score out of 100:

   | Part | Points | What it looks at |
   |---|---|---|
   | Usage trend | 30 | Is usage going up or down vs the previous quarter |
   | Active days | 25 | How many days a month the customer logs in |
   | Modules used | 25 | How many of the 6 modules are used regularly |
   | Open tickets | 20 | Open support issues, especially high priority ones |

3. Puts each customer in a risk band: Critical (below 40), High (40-59), Medium (60-79), Healthy (80+). If renewal is within 3 months, an unhealthy customer moves up one level so they get called first.
4. Writes the reason and the next step for every customer. For example: "Usage down 63% vs previous quarter, 1 of 6 modules in regular use, renewal in 3 months", next step "Renewal at risk: success review with sponsor before renewal".
5. Makes a 2-page PDF report and an Excel file the team can filter and work from.

On the sample data:

- 16 of 40 customers come out as Critical or High risk, each with a reason and next step.
- 6 of them renew within 3 months, so that is the call list for the week.
- Falling usage was the most common warning sign, and it shows up months before renewal.
- Healthy customers are listed too, as possible references or for more module adoption.

## How to run

Needs Python 3.10+. Google Chrome is needed only for the PDF/PNG; the HTML and Excel are made without it.

```bash
pip install -r requirements.txt
python generate_sample_data.py
python analyse.py
```

Everything is saved in `output/`. To use your own data, replace the three CSV files in `data/` and keep the same column names.

## Notes

- `analyse.py` does the scoring and makes all the outputs.
- `output/customer_health.xlsx` has the summary, health scores, usage, tickets and scoring rules.
- The dummy data uses a fixed seed, so results come out the same every time.
- Made with Python, pandas, matplotlib, openpyxl, Jinja2 and headless Chrome.
