#!/usr/bin/env python3
"""
Verify a chain_demo.py run, and (with --db) the PRISM trace recorded during it.

    python3 analyze_run.py RUN_DIR                                   # workload evidence only
    python3 analyze_run.py RUN_DIR --db data/chain-XXXX.db3 --prism-repo ~/prism

Workload checks (from the run directory, no PRISM needed):
    latency rise in the compare window and recovery afterwards, throughput change,
    Inventory lock wait/hold times per window, how long each upstream hop waits downstream.

Trace checks (--db):
    time coverage, the five PIDs/threads, Ripple edges (the repo's own pid_connections.sql
    when --prism-repo is given), the pipe inodes behind each edge, Inventory futex evidence
    (key matched to the logged mutex address), Degradation scores (the repo's own share
    queries and scoring formula), and the Thread dynamics edges Hade draws for Inventory.

Nothing here invents edges or numbers: every value printed is read from the run files or
the .db3. A check that cannot be evaluated is reported as SKIP, not PASS.
Requires: pip install duckdb jinja2   (only for --db)
"""

import argparse
import csv
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta

ROLES = ["client", "gateway", "api", "inventory", "database"]
RESULTS = []


def report(status, what, detail=""):
    RESULTS.append((status, what))
    print(f"  [{status}] {what}" + (f" — {detail}" if detail else ""))


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")


def pct(v, p):
    if not v:
        return float("nan")
    v = sorted(v)
    k = (len(v) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    return v[f] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def in_win(t, w):
    return w[0] <= t < w[1]


# ============================================================================ workload
def workload(run_dir):
    meta = json.load(open(os.path.join(run_dir, "run.json")))
    wins = {r["range_type"]: (ts(r["start"]), ts(r["end"])) for r in rows(os.path.join(run_dir, "windows.csv"))}
    phases = rows(os.path.join(run_dir, "phases.csv"))
    names = [("baseline", "baseline"), ("compare", "compare"), ("recovery", "recovery")]
    secs = {k: (w[1] - w[0]).total_seconds() for k, w in wins.items()}

    print("\n== Run")
    print(f"  run dir : {run_dir}")
    for r in ROLES:
        th = ", ".join(f"{k}={v}" for k, v in meta["roles"][r]["threads"].items())
        print(f"  {r:<10} PID {meta['roles'][r]['pid']:<8} {th}")
    print(f"  Inventory mutex address: {meta['roles']['inventory']['mutex_address']}")
    print("  phases (UTC):")
    for p in phases:
        print(f"    {p['phase']:<9} {p['start']} -> {p['end']}")
    for line in open(os.path.join(run_dir, "inventory.log")):
        if "FAULT" in line:
            print("   ", line.strip())
    print("  analysis windows (UTC, equal length, transitions trimmed):")
    for k, _ in names:
        print(f"    {k:<9} {wins[k][0]} -> {wins[k][1]}  ({secs[k]:.0f} s)")
    eq = len({round(secs[k]) for k, _ in names}) == 1
    report("PASS" if eq else "FAIL", "baseline and compare windows have equal length", f"{secs['baseline']:.0f} s each")

    print("\n== KPI: client round-trip latency (latency.csv)")
    lat = [(ts(r["ts"]), float(r["response_ms"])) for r in rows(os.path.join(run_dir, "latency.csv"))]
    stats = {}
    print(f"  {'window':<9} {'n':>5} {'req/s':>7} {'mean':>9} {'p50':>9} {'p95':>9} {'max':>9}  (ms)")
    for k, _ in names:
        v = [ms for t, ms in lat if in_win(t, wins[k])]
        stats[k] = {"n": len(v), "rps": len(v) / secs[k], "mean": statistics.fmean(v) if v else float("nan"),
                    "p50": pct(v, 50), "p95": pct(v, 95), "max": max(v) if v else float("nan")}
        s = stats[k]
        print(f"  {k:<9} {s['n']:>5} {s['rps']:>7.2f} {s['mean']:>9.2f} {s['p50']:>9.2f} {s['p95']:>9.2f} {s['max']:>9.2f}")
    b, c, r = stats["baseline"], stats["compare"], stats["recovery"]
    report("PASS" if c["p50"] >= 10 * b["p50"] else "FAIL", "latency rises in the compare window",
           f"p50 {b['p50']:.1f} -> {c['p50']:.1f} ms (x{c['p50']/b['p50']:.0f})")
    report("PASS" if r["p95"] <= 2 * b["p95"] + 2 else "FAIL", "latency recovers after the fault",
           f"recovery p95 {r['p95']:.1f} ms vs baseline p95 {b['p95']:.1f} ms")
    print(f"  throughput: baseline {b['rps']:.2f} req/s, compare {c['rps']:.2f} req/s "
          f"({(c['rps']/b['rps']-1)*100:+.0f}%), recovery {r['rps']:.2f} req/s. The client is closed-loop "
          f"(1 outstanding request, {meta['params']['think_ms']:.0f} ms think time, same request type), "
          f"so completed requests per second fall when each request takes longer.")

    print("\n== Inventory lock (workload logs, measured with perf_counter around pthread_mutex_lock)")
    inv = [(ts(x["ts"]), float(x["lock_wait_ms"]), float(x["hold_ms"]))
           for x in rows(os.path.join(run_dir, "inventory_requests.csv"))]
    hold = [(ts(x["lock_ts"]), ts(x["unlock_ts"]), float(x["wait_ms"]), float(x["hold_ms"]), int(x["fault"]))
            for x in rows(os.path.join(run_dir, "inventory_holder.csv"))]
    req_tid = meta["roles"]["inventory"]["threads"]["inventory (request thread)"]
    hold_tid = meta["roles"]["inventory"]["threads"]["inv-lockholder"]
    lockstats = {}
    print(f"  request thread TID {req_tid} (waiter) / inv-lockholder TID {hold_tid} (holder)")
    print(f"  {'window':<9} {'req':>4} {'wait mean':>10} {'wait p95':>9} {'wait s/s':>9} | "
          f"{'holds':>5} {'hold mean':>10} {'hold max':>9} {'holder blocked':>15}")
    for k, _ in names:
        w = wins[k]
        waits = [x[1] for x in inv if in_win(x[0], w)]
        hs = [x for x in hold if in_win(x[0], w)]
        # wait accumulated per second of wall time (same unit as PRISM futex total_time per 1 s bucket)
        acc = sum(waits) / 1000 / secs[k]
        lockstats[k] = acc
        print(f"  {k:<9} {len(waits):>4} {statistics.fmean(waits) if waits else 0:>10.2f} {pct(waits, 95):>9.2f} "
              f"{acc:>9.3f} | {len(hs):>5} {statistics.fmean([h[3] for h in hs]) if hs else 0:>10.2f} "
              f"{max([h[3] for h in hs], default=0):>9.2f} {sum(1 for h in hs if h[2] > 0.5):>15}")
    report("PASS" if lockstats["compare"] > 10 * max(lockstats["baseline"], 0.001) else "FAIL",
           "request thread's lock wait rises in compare",
           f"{lockstats['baseline']:.3f} -> {lockstats['compare']:.3f} s of wait per second")
    print("  per-request latency (ms per request) vs wait accumulated per interval (s per 1 s): one request "
          "waiting ~400 ms adds ~0.4 s to the 1 s bucket(s) it spans; PRISM reports the second form.")

    print("\n== Where each hop spends a request (hop_*.csv)")
    for role in ("gateway", "api", "inventory"):
        h = [(ts(x["ts"]), float(x["total_ms"]), float(x["downstream_ms"]))
             for x in rows(os.path.join(run_dir, f"hop_{role}.csv"))]
        parts = []
        for k in ("baseline", "compare"):
            sel = [x for x in h if in_win(x[0], wins[k])]
            if sel:
                tot = sum(x[1] for x in sel)
                parts.append(f"{k} {statistics.fmean([x[1] for x in sel]):.1f} ms/request, "
                             f"{sum(x[2] for x in sel)/tot*100:.0f}% waiting on downstream reply")
        print(f"  {role:<9} " + " | ".join(parts))
    print("  (Inventory's 'downstream' is the Database call only; its lock wait is local, which is the point.)")
    return meta, wins, secs, lockstats


# ============================================================================ trace
def trace(meta, wins, secs, lockstats, db_path, repo, machine_id):
    try:
        import duckdb
    except ImportError:
        report("SKIP", "trace checks", "pip install duckdb")
        return
    con = duckdb.connect(db_path, read_only=True)
    q = lambda sql: con.execute(sql).fetchall()
    pids = {r: int(meta["roles"][r]["pid"]) for r in ROLES}
    role_of = {v: k for k, v in pids.items()}
    plist = ",".join(map(str, pids.values()))
    inv_pid = pids["inventory"]
    req_tid = int(meta["roles"]["inventory"]["threads"]["inventory (request thread)"])
    hold_tid = int(meta["roles"]["inventory"]["threads"]["inv-lockholder"])
    W = {k: (str(v[0]), str(v[1])) for k, v in wins.items()}

    def win_filter(col, k):
        return f"(({col} >= '{W[k][0]}' AND {col} < '{W[k][1]}'))"

    print(f"\n== Trace: {db_path}")
    t0, t1 = q("SELECT min(ts), max(ts) FROM taskstats")[0]
    print(f"  taskstats covers {t0} -> {t1} (UTC as stored by PRISM)")
    covered = t0 is not None and t0 <= wins["baseline"][0] and t1 >= wins["recovery"][1]
    report("PASS" if covered else "FAIL", "trace covers baseline, compare and recovery windows",
           "" if covered else "if the offset is a whole number of hours, the CSV and DB use different time zones")

    print("\n  processes and threads seen in taskstats:")
    seen = {pid: (comm, n, tids) for pid, comm, n, tids in q(
        f"SELECT pid, last(comm ORDER BY ts), count(*), list(DISTINCT tid ORDER BY tid) FROM taskstats "
        f"WHERE machine_id={machine_id} AND pid IN ({plist}) GROUP BY pid")}
    for r in ROLES:
        s = seen.get(pids[r])
        print(f"    {r:<10} PID {pids[r]:<8} " + (f"comm={s[0]!r} rows={s[1]} tids={s[2]}" if s else "NOT IN TRACE"))
    report("PASS" if len(seen) == 5 else "FAIL", "all five processes recorded", f"{len(seen)}/5")
    inv_tids = set(seen.get(inv_pid, (0, 0, []))[2])
    report("PASS" if {req_tid, hold_tid} <= inv_tids else "FAIL",
           "Inventory request and lock-holder threads recorded", f"TIDs {req_tid}, {hold_tid}")

    # ---------------------------------------------------------------- Ripple
    print("\n== Ripple edges")
    sql_dir = os.path.join(repo, "analysis", "src", "sql") if repo else None
    if sql_dir and os.path.exists(os.path.join(sql_dir, "pid_connections.sql")):
        from jinja2 import Template
        sql = Template(open(os.path.join(sql_dir, "pid_connections.sql")).read()).render(vfs_ts_filter="true")
        src = "repo pid_connections.sql (exactly what Hade's Ripple page queries)"
        edges = [(int(a), int(b), int(n)) for _, a, _, _, b, _, n in q(sql)]
    else:
        src = "embedded pipe join (pass --prism-repo to use the repo's own Ripple SQL)"
        edges = [(int(a), int(b), int(n)) for a, b, n in q("""
            WITH p AS (SELECT DISTINCT v.machine_id, v.pid, v.inode_id FROM vfs v
                       JOIN linux_consts c ON c.const_type='fs_magic' AND c.value=v.fs_magic
                       WHERE c.const_name='PIPEFS_MAGIC')
            SELECT l.pid, r.pid, count(*) FROM p l JOIN p r
              ON l.machine_id=r.machine_id AND l.inode_id=r.inode_id AND l.pid<>r.pid
            GROUP BY 1,2""")]
    print(f"  source: {src}")
    undirected = {}
    for a, b, n in edges:
        key = tuple(sorted((a, b)))
        undirected[key] = max(undirected.get(key, 0), n)
    expected = {tuple(sorted((pids[ROLES[i]], pids[ROLES[i + 1]]))) for i in range(4)}
    inside = {k: v for k, v in undirected.items() if k[0] in role_of and k[1] in role_of}
    outside = {k: v for k, v in undirected.items() if (k[0] in role_of) != (k[1] in role_of)}
    for k in sorted(inside, key=lambda e: ROLES.index(role_of[e[0]])):
        tag = "expected" if k in expected else "UNEXPECTED"
        print(f"    {role_of[k[0]]:<9} ({k[0]}) <-> {role_of[k[1]]:<9} ({k[1]})  connections={inside[k]}  {tag}")
    for k, v in outside.items():
        p_in = k[0] if k[0] in role_of else k[1]
        p_out = k[1] if p_in == k[0] else k[0]
        print(f"    {role_of[p_in]} ({p_in}) <-> other PID {p_out}  connections={v}")
    missing = expected - set(inside)
    report("PASS" if not missing else "FAIL", "Ripple has the four chain edges",
           f"{len(expected & set(inside))}/4" + (f"; missing {[(role_of[a], role_of[b]) for a, b in missing]}" if missing else ""))
    report("PASS" if set(inside) <= expected else "FAIL", "no edges between non-adjacent chain processes")
    if outside:
        report("WARN", "chain processes also linked to other PIDs", "PRISM auto-discovered them; see list above")

    print("\n  pipe inodes behind the edges (vfs rows, PIPEFS_MAGIC):")
    want = {}
    for r in ROLES:
        for name, link in meta["roles"][r].get("fds", {}).items():
            if link.startswith("pipe:["):
                want.setdefault(int(link[6:-1]), []).append(f"{r}.{name}")
    got = q(f"""SELECT v.inode_id, v.pid, v.op, sum(v.total_requests)
                FROM vfs v JOIN linux_consts c ON c.const_type='fs_magic' AND c.value=v.fs_magic
                WHERE c.const_name='PIPEFS_MAGIC' AND v.pid IN ({plist}) GROUP BY 1,2,3 ORDER BY 1,2,3""")
    per_inode = {}
    for ino, pid, op, n in got:
        per_inode.setdefault(int(ino), []).append(f"{role_of[int(pid)]} {'read' if op == 0 else 'write' if op == 1 else op} x{n}")
    for ino in sorted(set(want) | set(per_inode)):
        print(f"    pipe:[{ino}] launcher-assigned {want.get(ino, ['-'])}: " + "; ".join(per_inode.get(ino, ["no vfs rows"])))
    matched = sum(1 for ino in want if ino in per_inode)
    report("PASS" if matched == len(want) == 8 else "FAIL",
           "every launcher pipe inode appears in PRISM's vfs table", f"{matched}/{len(want)}")

    # ---------------------------------------------------------------- futex
    print("\n== Inventory futex evidence")
    addr = int(meta["roles"]["inventory"]["mutex_address"], 16)
    word, off = addr & ~0xFFF, addr & 0xFFF
    print(f"  mutex {hex(addr)} -> expected futex key word={word} offset={off} (private futex: page-aligned address + offset)")
    keys = q(f"""SELECT futex_key_word, futex_key_offset,
                   sum(total_time) FILTER (WHERE tid={req_tid} AND {win_filter('ts_s','compare')})/1e9 AS req_wait_cmp,
                   sum(total_time)/1e9 AS total_wait
                 FROM futex_wait WHERE machine_id={machine_id} AND pid={inv_pid}
                 GROUP BY 1,2 ORDER BY req_wait_cmp DESC NULLS LAST LIMIT 6""")
    mkey = None
    print(f"  {'key word':>18} {'off':>5}  {'req-thread wait in compare (s)':>30}  note")
    for w_, o_, rc, tot in keys:
        is_m = int(w_) == word and (int(o_) & ~3) == (off & ~3)
        if is_m:
            mkey = (int(w_), int(o_))
        print(f"  {int(w_):>18} {int(o_):>5}  {rc or 0:>30.3f}  {'<- the pthread mutex' if is_m else ''}")
    report("PASS" if mkey else "FAIL", "the logged mutex address is a futex key in the trace")
    if keys and mkey:
        report("PASS" if (int(keys[0][0]), int(keys[0][1])) == mkey else "WARN",
               "the mutex is the futex the request thread waits on most in compare")
    if mkey:
        kf = f"futex_key_word={mkey[0]} AND futex_key_offset={mkey[1]} AND pid={inv_pid} AND machine_id={machine_id}"
        print(f"\n  per-thread activity on the mutex key (wait seconds per second of window; wakes = waiters woken):")
        print(f"  {'thread':<28} " + " ".join(f"{k + ' wait':>15} {k + ' wakes':>15}" for k in ("baseline", "compare", "recovery")))
        tw = {}
        for tid, label in ((req_tid, "inventory request (waiter)"), (hold_tid, "inv-lockholder (holder)")):
            cells = []
            for k in ("baseline", "compare", "recovery"):
                wt = q(f"SELECT coalesce(sum(total_time),0)/1e9 FROM futex_wait WHERE {kf} AND tid={tid} AND {win_filter('ts_s', k)}")[0][0]
                wk = q(f"SELECT coalesce(sum(total_requests),0) FROM futex_wake WHERE {kf} AND tid={tid} AND {win_filter('ts_s', k)}")[0][0]
                tw[(tid, k)] = (wt / secs[k], wk)
                cells.append(f"{wt / secs[k]:>15.3f} {wk:>15}")
            print(f"  {label:<28} " + " ".join(cells))
        rb, rc_, rr = tw[(req_tid, "baseline")][0], tw[(req_tid, "compare")][0], tw[(req_tid, "recovery")][0]
        report("PASS" if rc_ > 10 * max(rb, 0.001) else "FAIL", "request thread's futex wait rises baseline -> compare",
               f"{rb:.3f} -> {rc_:.3f} s/s")
        report("PASS" if rr < 0.2 * rc_ else "FAIL", "request thread's futex wait drops again in recovery", f"{rr:.3f} s/s")
        report("PASS" if tw[(hold_tid, "compare")][1] > 0 else "FAIL",
               "the lock-holder thread wakes the request thread on that key in compare",
               f"{tw[(hold_tid, 'compare')][1]} wakes by TID {hold_tid}")
        both = q(f"""SELECT count(*) FROM (SELECT DISTINCT tid FROM futex_wait WHERE {kf} AND
                       ({win_filter('ts_s','compare')} OR {win_filter('ts_s','baseline')})) w
                     JOIN (SELECT DISTINCT tid FROM futex_wake WHERE {kf} AND
                       ({win_filter('ts_s','compare')} OR {win_filter('ts_s','baseline')})) k USING (tid)""")[0][0]
        report("PASS" if both > 0 else "WARN", "Hade classifies the mutex as a contention futex",
               "a thread both waits on and wakes the key" if both else "no thread both waited and woke; Hade will call it a schedule futex")
        print(f"  cross-check with workload log: lock wait {lockstats['compare']:.3f} s/s (measured in-process) vs "
              f"{rc_:.3f} s/s (PRISM futex) in compare")
        print("\n  per-second series for the request thread on the mutex (paste into Debug page, see README):")
        series = q(f"""SELECT date_trunc('second', ts_s) s, round(sum(total_time)/1e9, 3) FROM futex_wait
                       WHERE {kf} AND tid={req_tid} AND ts_s >= '{W['baseline'][0]}' AND ts_s < '{W['recovery'][1]}'
                       GROUP BY 1 ORDER BY 1""")
        print("   ", ", ".join(f"{str(s)[11:19]}={v}" for s, v in series))

    # ---------------------------------------------------------------- degradation + dynamics
    if not sql_dir:
        report("SKIP", "Degradation scores and Thread dynamics edges", "pass --prism-repo")
        return
    from jinja2 import Template

    def tf(col, kind):
        k = "compare" if kind == "compare" else "baseline"
        return f"(({col} >= '{W[k][0]}' AND {col} <= '{W[k][1]}'))"

    tv = {"compare_filter": lambda c: tf(c, "compare"), "baseline_filter": lambda c: tf(c, "baseline"),
          "total_compare_seconds": secs["compare"], "total_baseline_seconds": secs["baseline"]}

    print("\n== Degradation (repo share queries + the scoring formula from pages/degradation.py)")
    shares = [("run", "pid_run_share.sql"), ("runqueue", "pid_rq_share.sql"),
              ("uninterruptible", "pid_uninterruptible_share.sql"), ("blkio", "pid_blkio_share.sql"),
              ("futex_schedule", "pid_futex_total_schedule.sql"), ("futex_contention", "pid_futex_total_contention.sql")]
    tc, tb = secs["compare"], secs["baseline"]
    table = []
    for r in ROLES:
        pf = f"(pid={pids[r]} AND machine_id={machine_id})"
        res = {}
        detail = {}
        for name, f in shares:
            sql = Template(open(os.path.join(sql_dir, "distributions", f)).read()).render(**tv, pid_filter=pf)
            agg = {"compare": 0.0, "baseline": 0.0}
            present = set()
            for row in con.execute(sql).fetchall():
                typ, share = row[-1], float(row[-2])
                present.add(typ)
                agg[typ] += min(share, tc if typ == "compare" else tb)
            score = 0.0
            cmp_, base = agg["compare"] / tc, agg["baseline"] / tc  # degradation.py divides both by compare time
            if "compare" in present and "baseline" in present:
                score = cmp_ if base < 0.01 else (cmp_ - base) / base * cmp_
                score = 0.0 if score < 0.001 else score
            elif "compare" in present:
                score = cmp_
            res[name] = score
            detail[name] = (base, cmp_)
        res["score"] = math.sqrt(sum(v * v for v in res.values()))
        table.append((r, res, detail))
    table.sort(key=lambda x: -x[1]["score"])
    print(f"  {'service':<10} {'score':>8} " + " ".join(f"{n[:12]:>12}" for n, _ in shares))
    for r, res, _ in table:
        print(f"  {r:<10} {res['score']:>8.3f} " + " ".join(f"{res[n]:>12.3f}" for n, _ in shares))
    print("  raw share sums (baseline -> compare, per second of window) for the metrics that moved:")
    for r, res, detail in table:
        moved = [f"{n} {b:.3f}->{c:.3f}" for n, (b, c) in detail.items() if abs(c - b) > 0.01]
        print(f"    {r:<10} " + (", ".join(moved) if moved else "no metric moved by more than 0.01"))
    top = table[0]
    driver = max(((n, top[1][n]) for n, _ in shares), key=lambda x: x[1])
    report("PASS" if top[0] == "inventory" else "WARN", "Inventory has the highest Degradation score",
           f"top = {top[0]} ({top[1]['score']:.3f}), driven by {driver[0]}")

    print("\n== Thread dynamics edges for Inventory (repo dynamics_edges.sql, compare window; this is what the SVG view draws)")
    sql = Template(open(os.path.join(sql_dir, "dynamics_edges.sql")).read()).render(
        **tv, pid_filter=f"((machine_id = {machine_id}) AND (pid = {inv_pid}))", pid=inv_pid, machine_id=machine_id)
    td = con.execute(sql).fetchall()
    names = {req_tid: "inventory request", hold_tid: "inv-lockholder"}
    for src_, dst, et in sorted(td):
        lab = lambda n: n + (f" ({names[int(n[7:])]})" if n.startswith("thread-") and int(n[7:]) in names else "")
        print(f"    {lab(src_):<40} -> {lab(dst):<40} {et}")
    cont = {s for s, d, _ in td if s.startswith("contention-") and d == f"thread-{req_tid}"}
    linked = {d for s, d, _ in td if s == f"thread-{hold_tid}" and d in cont}
    report("PASS" if linked else "FAIL", "graph links the lock-holder to the request thread through a contention node",
           ", ".join(sorted(linked)) if linked else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--db")
    ap.add_argument("--prism-repo", help="PRISM repository root (uses analysis/src/sql)")
    ap.add_argument("--machine-id", type=int, default=1)
    a = ap.parse_args()
    meta, wins, secs, lockstats = workload(os.path.abspath(a.run_dir))
    if a.db:
        trace(meta, wins, secs, lockstats, a.db, a.prism_repo, a.machine_id)
    else:
        report("SKIP", "trace checks", "pass --db <file.db3>")
    print("\n== Summary")
    for s in ("PASS", "WARN", "FAIL", "SKIP"):
        n = [w for st, w in RESULTS if st == s]
        if n:
            print(f"  {s}: {len(n)}")
    sys.exit(1 if any(st == "FAIL" for st, _ in RESULTS) else 0)


if __name__ == "__main__":
    main()
