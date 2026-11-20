"""Generate NorthStar Logistics' three messy source systems.

Outputs (in data/raw/):
  jobs.csv           ~200 jobs, mixed date formats, missing/dirty phones, dup rows
  service_logs.xlsx  'Service Logs' sheet (tickets, with duplicates) +
                     'Technician Roster' sheet (120 technicians, with duplicates)
  crm_customers.json backing store for the fake CRM API (backend/crm_api.py)

Usage:
  python data/generate_data.py            # anchors "today" to the current time
  python data/generate_data.py --as-of "2026-11-19 14:00"
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parent / "raw"
SEED = 42

CITIES = ["Charlotte", "Raleigh", "Durham", "Greensboro", "Winston-Salem"]
CITY_MESS = {
    "Charlotte": ["Charlotte", "charlotte", "CHARLOTTE ", " Charlotte", "CLT", "Charlotte, NC"],
    "Raleigh": ["Raleigh", "raleigh", "RALEIGH", "Raleigh, NC"],
    "Durham": ["Durham", "durham ", "Durham, NC"],
    "Greensboro": ["Greensboro", "greensboro", "GSO"],
    "Winston-Salem": ["Winston-Salem", "Winston Salem", "winston-salem"],
}
FIRST = ["Priya", "Marcus", "Elena", "Jamal", "Sofia", "Diego", "Aisha", "Tom", "Grace", "Kevin",
         "Nina", "Omar", "Laura", "Ravi", "Hannah", "Luis", "Mei", "Andre", "Fatima", "Chris",
         "Zoe", "Ian", "Keisha", "Sam", "Arjun", "Bella", "Victor", "Tara", "Noah", "Lena"]
LAST = ["Patel", "Johnson", "Garcia", "Kim", "Williams", "Nguyen", "Brown", "Singh", "Lopez",
        "Davis", "Chen", "Miller", "Okafor", "Wilson", "Reddy", "Moore", "Taylor", "Ali"]
COMPANIES = ["Blue Ridge", "Queen City", "Piedmont", "Carolina", "Tar Heel", "Harbor", "Summit",
             "Pinecrest", "Oakline", "Riverbend", "Crescent", "Magnolia", "Ironwood", "Lakeview"]
SUFFIX = ["Dental", "Bakery", "Fitness", "Pharmacy", "Auto Parts", "Hardware", "Clinic",
          "Coffee Co", "Florist", "Print Shop", "Vet Hospital", "Grocers"]
JOB_TYPES = ["Delivery", "Install", "Repair", "Pickup", "Inspection"]
PRIORITIES = ["High", "Normal", "Low"]
DELAY_REASONS = {
    "Traffic delay": ["Stuck on I-77 due to accident", "Heavy traffic on I-40, ETA +45m",
                      "Road closure on Tryon St, rerouting"],
    "Vehicle breakdown": ["Van flat tire, waiting on roadside", "Engine warning light, pulled over"],
    "Parts missing": ["Wrong part loaded at depot", "Part on backorder, customer informed"],
    "Customer not home": ["No answer at door, left card", "Gate locked, customer unreachable"],
    "Address issue": ["Address not found on map", "Suite number missing in order"],
    "Weather": ["Heavy rain, slowed route", "Lightning hold at site"],
}
ROUTINE_NOTES = {
    "Routine update": ["Arrived on site", "Job completed, signature captured",
                       "Customer requested receipt by email"],
}


def phone(rng: random.Random) -> str:
    return f"704{rng.randint(2000000, 9999999)}"


def messy_phone(rng: random.Random, p: str | None) -> str:
    if p is None or rng.random() < 0.18:
        return rng.choice(["", "", "N/A", "unknown"])
    d = p
    return rng.choice([
        d, f"({d[:3]}) {d[3:6]}-{d[6:]}", f"{d[:3]}-{d[3:6]}-{d[6:]}", f"+1 {d[:3]}.{d[3:6]}.{d[6:]}",
        f"1{d}", f"{d[:3]} {d[3:6]} {d[6:]}",
    ])


def messy_date(rng: random.Random, dt: datetime | None) -> str:
    if dt is None:
        return ""
    fmt = rng.choice([
        "%Y-%m-%d %H:%M", "%m/%d/%Y %I:%M %p", "%d-%b-%Y %H:%M", "%Y/%m/%dT%H:%M:%S",
        "%m/%d/%y %H:%M", "%Y-%m-%dT%H:%M:%S",
    ])
    return dt.strftime(fmt)


def messy_tech(rng: random.Random, tid: str) -> str:
    n = int(tid.split("-")[1])
    return rng.choice([tid, f"T{n:03d}", f"t-{n}", tid.lower(), f" {tid} "])


def build(as_of: datetime) -> dict:
    rng = random.Random(SEED)
    today = as_of.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- Technicians (120) -------------------------------------------------
    techs = []
    for i in range(1, 121):
        city = CITIES[0] if i <= 40 else rng.choice(CITIES)  # Charlotte is the biggest hub
        techs.append({"id": f"T-{i:03d}", "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                      "city": city, "phone": phone(rng),
                      "skill": rng.choice(["Driver", "Driver", "Installer", "Repair Tech"]),
                      "hire_date": (today - timedelta(days=rng.randint(60, 2500)))})
    techs[0]["name"] = "Priya Sharma"  # the free driver in the demo story

    # --- Customers (80, in CRM) -------------------------------------------
    customers = []
    for i in range(1, 81):
        city = rng.choice(CITIES[:1] * 3 + CITIES[1:])
        p = phone(rng) if rng.random() > 0.15 else None  # CRM itself is missing some phones
        customers.append({"id": f"C-{i:03d}", "name": f"{rng.choice(COMPANIES)} {rng.choice(SUFFIX)}",
                          "city": city, "phone": p,
                          "email": f"ops{i}@example.com" if rng.random() > 0.1 else None,
                          "tier": rng.choice(["Gold", "Silver", "Silver", "Bronze", "Bronze"])})

    # --- Jobs (200) ---------------------------------------------------------
    jobs, tickets = [], []
    tk = 5001
    for j in range(200):
        job_id = f"J-{1001 + j}"
        cust = rng.choice(customers)
        city = cust["city"]
        local_techs = [t for t in techs if t["city"] == city] or techs
        tech = rng.choice(local_techs)
        day_offset = rng.choices([-3, -2, -1, 0, 1], weights=[1, 1, 2, 5, 1])[0]
        sched = today + timedelta(days=day_offset, hours=rng.randint(7, 18), minutes=rng.choice([0, 15, 30, 45]))
        created = sched - timedelta(hours=rng.randint(4, 72))
        window_end = sched + timedelta(hours=2)

        completed, status, late_reason = None, "Scheduled", None
        roll = rng.random()
        if sched > as_of:
            status = "Scheduled"
        elif roll < 0.05:
            status = "Cancelled"
        elif roll < 0.22:  # still open past its window -> delayed
            status = rng.choice(["En Route", "In Progress"])
            late_reason = rng.choice(list(DELAY_REASONS))
        else:
            late = roll < 0.35
            mins = rng.randint(130, 300) if late else rng.randint(20, 110)
            completed = sched + timedelta(minutes=mins)
            if completed > as_of:
                completed, status = None, "In Progress"
            else:
                status = "Completed"
            if late:
                late_reason = rng.choice(list(DELAY_REASONS))

        jobs.append({
            "Job ID": job_id, "Cust ID": cust["id"],
            "Customer Phone": messy_phone(rng, cust["phone"] or (phone(rng) if rng.random() < 0.5 else None)),
            "City": rng.choice(CITY_MESS[city]), "Tech": messy_tech(rng, tech["id"]),
            "Created": messy_date(rng, created), "Scheduled Date": messy_date(rng, sched),
            "Completed": messy_date(rng, completed),
            "Status": rng.choice([status, status.upper(), status.lower()]),
            "Priority": rng.choice(PRIORITIES), "Job Type": rng.choice(JOB_TYPES),
        })

        # Tickets: every late job gets a reason ticket, ~30% of others a routine note
        if late_reason or rng.random() < 0.3:
            cat = late_reason or "Routine update"
            notes = DELAY_REASONS.get(cat) or ROUTINE_NOTES[cat]
            when = min(as_of, sched + timedelta(minutes=rng.randint(30, 150)))
            tickets.append({"Ticket #": f"TK-{tk}", "Job Ref": job_id, "Logged At": when,
                            "Tech": tech["id"], "Category": cat, "Notes": rng.choice(notes),
                            "Resolved": "Y" if completed else "N"})
            tk += 1

    # Inject jobs.csv problems
    jobs[17]["Cust ID"] = ""                         # missing customer
    jobs[63]["Scheduled Date"] = "TBD"               # unparseable date
    jobs[88]["Cust ID"] = "C-999"                    # not in CRM
    jobs[121]["Tech"] = "T-999"                      # unknown technician
    for idx in (5, 42, 77, 150, 190):               # exact duplicate rows
        jobs.append(dict(jobs[idx]))
    rng.shuffle(jobs)

    # Inject service_logs.xlsx duplicates
    for t in rng.sample(tickets, 12):
        tickets.append(dict(t))                       # exact dupes
    for t in rng.sample(tickets[:40], 6):
        d = dict(t); d["Notes"] = "  " + d["Notes"].upper() + " "; d["Tech"] = d["Tech"].lower()
        tickets.append(d)                             # same ticket, re-keyed by a different clerk
    rng.shuffle(tickets)

    roster = [{"Tech ID": t["id"], "Name": t["name"], "Home City": t["city"], "Mobile": t["phone"],
               "Skill": t["skill"], "Hire Date": t["hire_date"].date()} for t in techs]
    roster += [dict(r) for r in rng.sample(roster, 4)]  # roster dupes too

    crm = [{"id": c["id"],
            "profile": {"company_name": c["name"], "tier": c["tier"]},
            "contact": {"phone": c["phone"], "email": c["email"]},
            "address": {"city": c["city"], "state": "NC"},
            "updated_at": (as_of - timedelta(days=rng.randint(1, 400))).isoformat()} for c in customers]
    return {"jobs": jobs, "tickets": tickets, "roster": roster, "crm": crm}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help='"YYYY-MM-DD HH:MM" (default: now)')
    args = ap.parse_args()
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d %H:%M") if args.as_of else datetime.now().replace(second=0, microsecond=0)

    out = build(as_of)
    RAW.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out["jobs"]).to_csv(RAW / "jobs.csv", index=False)
    with pd.ExcelWriter(RAW / "service_logs.xlsx") as xw:
        pd.DataFrame(out["tickets"]).to_excel(xw, sheet_name="Service Logs", index=False)
        pd.DataFrame(out["roster"]).to_excel(xw, sheet_name="Technician Roster", index=False)
    (RAW / "crm_customers.json").write_text(json.dumps(out["crm"], indent=2))
    print(f"as_of={as_of:%Y-%m-%d %H:%M}  jobs.csv={len(out['jobs'])} rows  "
          f"service_logs={len(out['tickets'])} tickets / {len(out['roster'])} roster rows  "
          f"crm={len(out['crm'])} customers")


if __name__ == "__main__":
    main()
