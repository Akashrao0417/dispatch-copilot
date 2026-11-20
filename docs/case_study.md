# Case study: a dispatcher copilot for NorthStar Logistics

## The problem

NorthStar Logistics (a fictional company with realistic data) runs 120 drivers and technicians
across five North Carolina cities. Its data lives in three systems that don't agree: a jobs CSV with
six date formats and phones written six different ways (or missing), an Excel sheet of service notes full of duplicates,
and a CRM that returns nested, paginated JSON. Dispatchers couldn't tell which jobs were late or why,
so customers called first.

## What I built

**Data in.** A Python ETL cleans all three sources into Postgres: it parses every date format,
normalizes phones, cities and IDs, removes duplicates, and drops rows that can't be trusted. Every
decision is counted in a data-quality report. On 205 raw job rows it removed 5 duplicates, dropped
4 invalid rows with reasons, and fixed 461 dates. It also removed 18 duplicate tickets.

**AI that answers.** A dispatcher can ask, "Which jobs are late in Charlotte today, and why?" Job
facts come straight from SQL, never from the model, so the answer always matches the dashboard,
with job and ticket IDs as proof. Procedure questions are answered from five one-page SOP PDFs
through retrieval, and every answer cites its exact section. If nothing relevant is retrieved, or
the answer cites nothing real, the copilot says "I don't know."

**AI that acts.** An agent with exactly three tools (reassign technician, create ticket, draft
customer update) proposes actions such as "Reassign J-1005 to Priya, she's free." Nothing runs until
a person clicks Approve. Rules are checked again at approval time, roles are enforced in the backend
(technicians see only their own jobs), and every proposal, approval and refusal is logged with who,
what and when.

## How I measured it

I wrote 30 questions with known answers: SOP questions, questions that must get "I don't know," and
job lookups graded against an independent pandas calculation. The baseline scored 70%. I ranked
failures by harm. Confidently answering an out-of-scope question ranked first, ahead of misrouted
questions. I fixed the root causes (a weak evidence gate, a router that misread procedure questions, a missing filter) and reached 30/30. I then ran 10 new held-out questions once and
scored 90%; the remaining miss is a known limit of keyword retrieval.

## What I'd do next

Move to semantic retrieval and re-run both eval sets. Replace demo sign-in with real
authentication. Connect the approved customer messages to SMS and email.

**Stack:** Python, pandas, FastAPI, Postgres, Streamlit, BM25 retrieval, optional Claude, pytest
(53 tests), Docker, Render.
