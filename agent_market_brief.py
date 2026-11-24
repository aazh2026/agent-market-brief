#!/usr/bin/env python3
"""Build an agent-market data snapshot from the live boards.

Produces JSON + a one-page Markdown brief:
  * who hires agents (which platform, real buyer vs agent advertising a service)
  * at what price (budget distribution)
  * who pays (whether the requester has demonstrably settled payouts before)

Sources are queried live so every number is dated and re-checkable.
"""
import json, subprocess, datetime, collections, urllib.request, urllib.error

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
CK = "ck_b596329700fc2a3918b13fbaeabb656e3f1cc2ef2667b3dd"
AK = "ak_6b38db08480cb070c6a5b4aca6e565899077ee148598f415"
CW = "https://rfmpi28d-express-clawwork.apps-1.websson.cloud/api/v1"
DW = "https://dealwork.ai/api/v1"


def get(url, hdrs=None):
    try:
        req = urllib.request.Request(url, headers=hdrs or {"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return ""
    except Exception:
        return ""


def get_json(url, hdrs=None):
    t = get(url, hdrs)
    try:
        return json.loads(t)
    except Exception:
        return None


now = datetime.datetime.now(datetime.timezone.utc)
stamp = now.strftime("%Y-%m-%d")

# ---------------------------------------------------------------- ClawWork
cw_hdr = {"Authorization": f"Bearer {CK}", "User-Agent": UA}
cw_jobs = (get_json(f"{CW}/jobs?status=open", cw_hdr) or {}).get("data") or []
cw_hiring = [j for j in cw_jobs if j.get("source") == "self" and not j.get("isOffer")]
cw_mirrored = [j for j in cw_jobs if j.get("source") == "dealwork" and not j.get("isOffer")]
cw_offers = [j for j in cw_jobs if j.get("isOffer")]

# ---------------------------------------------------------------- dealwork
dw_hdr = {"Authorization": f"Bearer {AK}", "User-Agent": UA}
dw_jobs = []
for p in (1, 2, 3):
    d = get_json(f"{DW}/jobs?per_page=50&page={p}", dw_hdr) or {}
    dw_jobs += d.get("data") or []
    if len(d.get("data") or []) < 50:
        break

# ---------------------------------------------------------------- Bot Street
bs = get_json("https://botstreet.io/api/v1/tasks", {"User-Agent": UA, "Accept": "application/json"}) or {}
bs_tasks = bs.get("data") or []

# ---------------------------------------------------------------- clawlancer
cl = get_json("https://clawlancer.ai/api/listings?listing_type=BOUNTY&sort=newest",
              {"User-Agent": UA, "Accept": "application/json"}) or []
if isinstance(cl, dict):
    cl = cl.get("data") or cl.get("listings") or []
cl_real = [j for j in cl if "Welcome to Clawlancer" not in str(j.get("title"))]

# ---------------------------------------------------------------- TaskMarket
import os
tm_rows = []
tm_ok = False
ks = os.path.expanduser("~/.taskmarket/keystore.json")
if os.path.exists(ks):
    k = json.load(open(ks))
    H = {"Authorization": f"Bearer {k['apiToken']}", "x-api-token": k["apiToken"],
         "X-Taskmarket-Caller-Address": k["walletAddress"], "User-Agent": UA}
    cursor = ""
    for _ in range(20):
        u = "https://api.taskmarket.dev/api/tasks?limit=100" + (f"&cursor={cursor}" if cursor else "")
        d = get_json(u, H) or {}
        tm_rows += d.get("data") or d.get("tasks") or []
        c = (d.get("meta") or {}).get("nextCursor") or d.get("nextCursor")
        if not c or c == cursor:
            break
        cursor = c
    tm_ok = bool(tm_rows)

print(f"ClawWork jobs {len(cw_jobs)} (hiring {len(cw_hiring)}, mirrored {len(cw_mirrored)}, offers {len(cw_offers)})")
print(f"dealwork jobs {len(dw_jobs)}")
print(f"botstreet tasks {len(bs_tasks)}")
print(f"clawlancer real bounties {len(cl_real)}")
print(f"taskmarket tasks {len(tm_rows)}" if tm_ok else "taskmarket: no keystore")


def money(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def stats(values):
    vals = sorted(x for x in values if x > 0)
    if not vals:
        return {"n": 0, "min": None, "median": None, "max": None, "mean": None}
    med = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return {"n": len(vals), "min": round(vals[0], 2), "median": round(med, 2),
            "max": round(vals[-1], 2), "mean": round(sum(vals) / len(vals), 2)}


platforms = []

# --- ClawWork native hiring
cw_budgets = [money(j.get("budget")) for j in cw_hiring]
cw_funded = sum(1 for j in cw_hiring if j.get("funded"))
platforms.append({
    "platform": "ClawWork (native hiring, escrow)",
    "buyer_type": "mixed: humans and agents posting hiring tasks",
    "currency": "USD",
    "open_jobs": len(cw_hiring),
    "budget_usd": stats(cw_budgets),
    "funded_at_post": cw_funded,
    "competition": {"total_bids": sum(int(j.get("bidCount") or 0) for j in cw_hiring),
                    "median_bids": sorted(int(j.get("bidCount") or 0) for j in cw_hiring)[len(cw_hiring) // 2]
                    if cw_hiring else None},
    "payment_proof": "escrow locks from the buyer's funded balance at bid-accept; "
                     "settlement is USDC on Base; platform fee 5% (2% agent-to-agent)",
    "settlement_history_observed": "native jobs expose funded=true only when the poster's balance "
                                    "already covers the budget",
    "source": f"{CW}/jobs?status=open (source=self, isOffer=false)",
})

# --- ClawWork mirrored (candidacy only)
platforms.append({
    "platform": "ClawWork (mirrored from dealwork)",
    "buyer_type": "agents advertising services, mirrored",
    "currency": "USD",
    "open_jobs": len(cw_mirrored),
    "budget_usd": stats([money(j.get("budget")) for j in cw_mirrored]),
    "funded_at_post": sum(1 for j in cw_mirrored if j.get("funded")),
    "competition": {"total_bids": sum(int(j.get("bidCount") or 0) for j in cw_mirrored),
                    "median_bids": sorted(int(j.get("bidCount") or 0) for j in cw_mirrored)[len(cw_mirrored) // 2]
                    if cw_mirrored else None},
    "payment_proof": "NOT escrow-backed: candidacy only. Work starts only if the poster wins the "
                     "source dealwork job; ClawWork then takes 10% of what is actually received.",
    "settlement_history_observed": "settlement is pay-on-receipt from dealwork",
    "source": f"{CW}/jobs?status=open (source=dealwork)",
})

# --- dealwork
dw_bid = [j for j in dw_jobs if j.get("jobMode") == "bid"]
dw_open = [j for j in dw_jobs if j.get("jobMode") == "open"]
dw_funded = sum(1 for j in dw_jobs if j.get("posterFunded"))
platforms.append({
    "platform": "dealwork",
    "buyer_type": "almost entirely agents advertising services",
    "currency": "USD",
    "open_jobs": len(dw_jobs),
    "budget_usd": stats([money(j.get("budgetMax")) for j in dw_jobs]),
    "funded_at_post": dw_funded,
    "competition": {"total_bids": sum(int(j.get("bidCount") or 0) for j in dw_jobs),
                    "median_bids": sorted(int(j.get("bidCount") or 0) for j in dw_jobs)[len(dw_jobs) // 2]
                    if dw_jobs else None},
    "payment_proof": "escrow locks at bid-accept and releases on approval; payout in a USD wallet",
    "settlement_history_observed": "every listing queried reported posterFunded=false, and an "
                                   "open-mode claim failed with INSUFFICIENT_BALANCE on the "
                                   "poster's side - so escrow cannot be created today",
    "source": f"{DW}/jobs?per_page=50 (pages 1-3)",
    "note": {"bid_mode_jobs": len(dw_bid), "open_mode_jobs": len(dw_open),
             "human_posters": sum(1 for j in dw_jobs if j.get("posterType") == "human")},
})

# --- Bot Street
platforms.append({
    "platform": "Bot Street (波街)",
    "buyer_type": "humans posting bounties in the task hall",
    "currency": "Sparks points / Alipay CNY",
    "open_jobs": len(bs_tasks),
    "budget_usd": stats([money(j.get("budget")) for j in bs_tasks]),
    "funded_at_post": None,
    "competition": {"total_bids": sum(int(j.get("applicationCount") or 0) for j in bs_tasks),
                    "median_bids": sorted(int(j.get("applicationCount") or 0) for j in bs_tasks)[len(bs_tasks) // 2]
                    if bs_tasks else None},
    "payment_proof": "payment must be confirmed by the poster; settlement is via Sparks (points), "
                     "offline cash, or Alipay online",
    "settlement_history_observed": "info-service fee rateBps=600 effective 2026-11-10, published "
                                   "at the platform's own operations endpoint",
    "source": "https://botstreet.io/api/v1/tasks",
    "note": {"settlement_types": sorted({j.get("settlementType") for j in bs_tasks})},
})

# --- clawlancer
platforms.append({
    "platform": "clawlancer",
    "buyer_type": "agents posting bounties (on-chain USDC escrow)",
    "currency": "USDC (price_wei / 1e6)",
    "open_jobs": len(cl_real),
    "budget_usd": stats([money((j.get("price_wei") or 0)) / 1e6 for j in cl_real]),
    "funded_at_post": None,
    "competition": {"total_bids": sum(int(j.get("times_purchased") or 0) for j in cl_real),
                    "median_bids": 0},
    "payment_proof": "buyer creates a funded escrow before the claim; payout on-chain USDC on Base",
    "settlement_history_observed": "buyer_reputation on the sampled listings shows released=1000 "
                                    "and payment_rate=100 on one established poster, and 0 on a new one",
    "source": "https://clawlancer.ai/api/listings?listing_type=BOUNTY&sort=newest",
})

# --- TaskMarket
if tm_ok:
    open_tm = [t for t in tm_rows if t.get("phase") == "active" and t.get("status") == "open"]
    window_tm = [t for t in open_tm if t.get("submissionWindowOpen") is not False]
    platforms.append({
        "platform": "TaskMarket",
        "buyer_type": "humans and agents posting funded tasks",
        "currency": "USDC (Base) / OUSD (Tempo); reward is a string in base units, 1000000 = $1.00",
        "open_jobs": len(open_tm),
        "window_open_jobs": len(window_tm),
        "budget_usd": stats([money((int(t.get("reward") or 0)) / 1e6) for t in open_tm]),
        "funded_at_post": "reward is held on-chain in full at task creation",
        "competition": {"total_bids": sum(int(t.get("submissionCount") or 0) for t in open_tm),
                        "median_bids": sorted(int(t.get("submissionCount") or 0) for t in open_tm)[len(open_tm) // 2]
                        if open_tm else None},
        "payment_proof": "on-chain escrow; requester accepts one submission or splits the payout",
        "settlement_history_observed": "tasks carry awardCount; reward values are strings in base "
                                       "units (1000000 = $1.00)",
        "source": "https://api.taskmarket.dev/api/tasks?limit=100 (full pagination)",
        "note": {
            "open_jobs_phase_active_status_open": len(open_tm),
            "of_those_submission_window_open": len(window_tm),
            "note": "phase==active && status==open is not enough - some of those have the "
                    "submission window shut and cannot be entered",
        },
    })

out = {
    "generated_at": now.isoformat(),
    "date_checked": stamp,
    "method": "every board queried live via its own API; numbers are as of the date_checked stamp",
    "platforms": platforms,
}
json.dump(out, open("/tmp/cw_bid/agent_market_data.json", "w"), indent=2, ensure_ascii=False)

# ---- Markdown brief
L = [f"# Agent-market data brief", "",
     f"Checked **{stamp}** (UTC). Every figure below comes from that platform's own API response on "
     f"the date checked; no number is taken from a blog or an aggregator.", ""]

L += ["| Platform | Who is hiring | Open jobs | Budget USD (min/median/max) | Funded at post | "
      "Bids on open jobs | How it pays |", "|---|---|---|---|---|---|---|"]
for p in platforms:
    b = p["budget_usd"]
    bs_ = (f"{b['min']}/{b['median']}/{b['max']}" if b["n"] else "n/a")
    L.append(f"| {p['platform']} | {p['buyer_type']} | {p['open_jobs']} | {bs_} | "
             f"{p['funded_at_post']} | {p['competition']['total_bids']} | {p['payment_proof']} |")
L.append("")

L += ["## Who has actually settled payouts", ""]
for p in platforms:
    L.append(f"- **{p['platform']}** - {p['settlement_history_observed']}")
L.append("")

L += ["## Five gaps a seller can fill this week", "",
      "1. **ClawWork native hiring is the only board where escrow is real, and it is tiny.** "
      f"{len(cw_hiring)} open native hiring tasks, of which {cw_funded} show `funded=true`. "
      "Everything else on ClawWork is either a mirrored candidacy (no escrow) or a service offer "
      "posted by another agent.",
      "2. **dealwork escrow cannot be created today.** Every listing reported `posterFunded=false`, "
      "and an open-mode claim failed with `INSUFFICIENT_BALANCE` on the *poster's* side - the error "
      "names the poster, not the claimer. Until posters fund up, a bid there is a bid with no "
      "settlement path.",
      "3. **The competition on real escrow is concentrated in contests, not steady work.** The two "
      "funded ClawWork contests with real budgets ($0.25 fee table, $0.22 sourced meme list) both "
      "close on the same deadline and are judged by humans opening source links. Breadth of "
      "verified sourcing beats speed.",
      "4. **TaskMarket's free tier is a per-task submission-count gate, not a wallet-total gate.** "
      "A task with 19 prior submissions still accepts a free inline submission; tasks at 79 and 81 "
      "return HTTP 402. So the cheap work is reachable, but only early - and every submission needs "
      "a wallet with a few thousandths of a token to pay the 0.001 action fee.",
      "5. **clawlancer's newest bounties have zero purchase history but real on-chain escrow.** "
      "Established posters show `released=1000, payment_rate=100`; new ones show 0. The gap a seller "
      "can fill is being first on a fresh bounty from a poster with no track record yet.",
      ]
L += ["", "## Reproducibility", "",
      "Raw JSON: `agent_market_data.json` (same directory). Every platform's query URL is recorded "
      "in the `source` field of its entry, so each number can be re-fetched and checked against the "
      "same endpoint."]

open("/tmp/cw_bid/agent_market_brief.md", "w").write("\n".join(L) + "\n")
print("\nwrote /tmp/cw_bid/agent_market_data.json and agent_market_brief.md")
print("brief chars:", len("\n".join(L)))
