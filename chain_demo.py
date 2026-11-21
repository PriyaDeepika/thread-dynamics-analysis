#!/usr/bin/env python3
"""
Five-process request chain for a PRISM / Hade diagnosis demo.

    Client -> Gateway -> API -> Inventory -> Database
    (every reply travels back along the same path)

Why anonymous pipes?
    PRISM's VFS probe (vfs.bpf.c) only accounts reads/writes on S_IFIFO and
    S_IFCHR inodes, and Ripple (pid_connections.sql) only links two PIDs that
    read/wrote the *same* inode whose superblock magic is PIPEFS_MAGIC.
    Anonymous pipes created with pipe(2) live on pipefs; named FIFOs (mkfifo)
    would carry the host filesystem's magic and be ignored by Ripple. Localhost
    TCP needs the tcp_discovery handshake, which is what produced zero edges.

    The launcher creates one request pipe and one reply pipe per adjacent pair,
    hands each child only its own pipe ends (pass_fds), then closes its copies.
    Children never share a pipe with a non-neighbour, never write to the
    terminal, and log only to regular files, so no extra edges can appear.

Fault:
    Inventory's background thread "inv-lockholder" holds a glibc
    pthread_mutex_t (called via ctypes, so it is a real futex word that both
    threads wait on and wake, independent of the Python version) for ~500 ms
    while the request thread needs it. Toggled with SIGUSR1 (on) / SIGUSR2 (off).

Usage:
    python3 chain_demo.py run                      # interactive (waits for PRISM)
    python3 chain_demo.py run --no-wait            # scripted
    python3 chain_demo.py run --help               # phase lengths etc.
"""

import argparse
import csv
import ctypes
import ctypes.util
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

ROLES = ["client", "gateway", "api", "inventory", "database"]
FRAME = struct.Struct("<QQ")  # (sequence number, payload) = 16 bytes, < PIPE_BUF so writes are atomic

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
PR_SET_NAME = 15


# --------------------------------------------------------------------------- helpers
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fmt(ts: datetime) -> str:
    # Naive UTC with milliseconds: the same form PRISM's DuckDB TIMESTAMPs take.
    return ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def set_thread_name(name: str) -> None:
    """Set the kernel comm of the *calling* thread (what PRISM stores as comm)."""
    libc.prctl(PR_SET_NAME, ctypes.c_char_p(name.encode()[:15]), 0, 0, 0)


def read_exact(fd: int, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            return b""  # EOF: upstream closed
        buf += chunk
    return buf


def pipe_inode(pid, fd) -> str:
    try:
        return os.readlink(f"/proc/{pid}/fd/{fd}")
    except OSError:
        return "?"


def log_line(fh, message: str) -> None:
    fh.write(f"{fmt(utc_now())} UTC | {message}\n")
    fh.flush()


def write_ready(run_dir: str, role: str, extra: dict) -> None:
    info = {"role": role, "pid": os.getpid(), "main_tid": threading.get_native_id(), **extra}
    tmp = os.path.join(run_dir, f".ready_{role}.tmp")
    with open(tmp, "w") as f:
        json.dump(info, f)
    os.replace(tmp, os.path.join(run_dir, f"ready_{role}.json"))


# --------------------------------------------------------------------------- pthread mutex
class PthreadMutex:
    """A default glibc mutex: a single futex word. Waiters FUTEX_WAIT on it and
    the unlocking thread FUTEX_WAKEs the same word, so in PRISM both the waiter
    and the holder touch one futex key."""

    def __init__(self):
        self._buf = ctypes.create_string_buffer(64)  # zeroed == PTHREAD_MUTEX_INITIALIZER (glibc)
        rc = libc.pthread_mutex_init(self._buf, None)
        if rc != 0:
            raise OSError(rc, "pthread_mutex_init failed")
        self.address = ctypes.addressof(self._buf)

    def lock(self) -> None:
        rc = libc.pthread_mutex_lock(self._buf)  # ctypes releases the GIL while blocked
        if rc != 0:
            raise OSError(rc, "pthread_mutex_lock failed")

    def unlock(self) -> None:
        rc = libc.pthread_mutex_unlock(self._buf)
        if rc != 0:
            raise OSError(rc, "pthread_mutex_unlock failed")


# --------------------------------------------------------------------------- roles
def role_client(args) -> None:
    set_thread_name("client")
    log = open(os.path.join(args.run_dir, "client.log"), "a")
    req_w, rep_r = args.down_req_w, args.down_rep_r

    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))

    write_ready(args.run_dir, "client", {"threads": {"client": threading.get_native_id()},
                                         "fds": {"down_req_w": pipe_inode("self", req_w),
                                                 "down_rep_r": pipe_inode("self", rep_r)}})
    log_line(log, f"client PID {os.getpid()} waiting for SIGUSR1 to start load")
    signal.sigwait({signal.SIGUSR1})
    log_line(log, f"load started: closed loop, 1 outstanding request, think time {args.think_ms} ms")

    think = args.think_ms / 1000.0
    seq = 0
    with open(os.path.join(args.run_dir, "latency.csv"), "w", newline="") as lat, \
         open(os.path.join(args.run_dir, "latency_detail.csv"), "w", newline="") as det:
        lw, dw = csv.writer(lat), csv.writer(det)
        lw.writerow(["ts", "response_ms"])
        dw.writerow(["seq", "send_ts", "recv_ts", "response_ms"])
        while not stop["flag"]:
            seq += 1
            send_wall = utc_now()
            t0 = time.perf_counter_ns()
            os.write(req_w, FRAME.pack(seq, 1))
            reply = read_exact(rep_r, FRAME.size)
            t1 = time.perf_counter_ns()
            if not reply:
                log_line(log, "chain closed the reply pipe; stopping")
                break
            rseq, _ = FRAME.unpack(reply)
            if rseq != seq:
                raise RuntimeError(f"reply out of order: sent {seq}, got {rseq}")
            ms = (t1 - t0) / 1e6
            recv_wall = send_wall + timedelta(microseconds=(t1 - t0) / 1000)
            lw.writerow([fmt(recv_wall), f"{ms:.3f}"])
            dw.writerow([seq, fmt(send_wall), fmt(recv_wall), f"{ms:.3f}"])
            lat.flush()
            det.flush()
            time.sleep(think)
    log_line(log, f"client stopped after {seq} requests")
    os.close(req_w)  # EOF cascades down the chain and every server exits


def role_relay(args, name: str, work_s: float) -> None:
    """Gateway and API: read a request, forward it downstream, wait for the reply,
    send it back upstream. Logs how much of each request was spent waiting downstream."""
    set_thread_name(name)
    log = open(os.path.join(args.run_dir, f"{name}.log"), "a")
    write_ready(args.run_dir, name, {"threads": {name: threading.get_native_id()},
                                     "fds": {k: pipe_inode("self", getattr(args, k)) for k in
                                             ("up_req_r", "up_rep_w", "down_req_w", "down_rep_r")}})
    log_line(log, f"{name} PID {os.getpid()} TID {threading.get_native_id()} serving")
    with open(os.path.join(args.run_dir, f"hop_{name}.csv"), "w", newline="") as hf:
        hw = csv.writer(hf)
        hw.writerow(["ts", "seq", "total_ms", "downstream_ms"])
        while True:
            req = read_exact(args.up_req_r, FRAME.size)
            if not req:
                break
            t0 = time.perf_counter_ns()
            if work_s:
                time.sleep(work_s)
            os.write(args.down_req_w, req)
            t1 = time.perf_counter_ns()
            rep = read_exact(args.down_rep_r, FRAME.size)
            t2 = time.perf_counter_ns()
            if not rep:
                break
            os.write(args.up_rep_w, rep)
            seq, _ = FRAME.unpack(req)
            hw.writerow([fmt(utc_now()), seq, f"{(time.perf_counter_ns()-t0)/1e6:.3f}", f"{(t2-t1)/1e6:.3f}"])
            hf.flush()
    log_line(log, f"{name} upstream closed; exiting")
    os.close(args.down_req_w)


def role_inventory(args) -> None:
    set_thread_name("inventory")  # main thread == request-handling thread (TID == PID)
    log = open(os.path.join(args.run_dir, "inventory.log"), "a")
    mutex = PthreadMutex()
    state = {"fault": False}
    holder_tid = {}
    ready = threading.Event()

    # Block the control signals in every thread; the holder consumes them with sigtimedwait.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1, signal.SIGUSR2})

    def lock_holder():
        set_thread_name("inv-lockholder")
        holder_tid["tid"] = threading.get_native_id()
        ready.set()
        with open(os.path.join(args.run_dir, "inventory_holder.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["lock_ts", "unlock_ts", "tid", "fault", "wait_ms", "hold_ms"])
            while True:
                fault = state["fault"]
                hold_s = args.fault_hold_ms / 1000.0 if fault else args.normal_hold_ms / 1000.0
                gap_s = args.fault_gap_ms / 1000.0 if fault else args.normal_gap_ms / 1000.0
                t0 = time.perf_counter_ns()
                mutex.lock()
                t1 = time.perf_counter_ns()
                lock_wall = utc_now()
                time.sleep(hold_s)  # "restock job" holding the stock-table lock
                t2 = time.perf_counter_ns()
                mutex.unlock()
                w.writerow([fmt(lock_wall), fmt(utc_now()), holder_tid["tid"], int(fault),
                            f"{(t1-t0)/1e6:.3f}", f"{(t2-t1)/1e6:.3f}"])
                f.flush()
                # The gap doubles as the control-signal check (delivered here, never mid-hold).
                deadline = time.monotonic() + gap_s
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    sig = signal.sigtimedwait({signal.SIGUSR1, signal.SIGUSR2}, remaining)
                    if sig is None:
                        break
                    state["fault"] = sig.si_signo == signal.SIGUSR1
                    log_line(log, f"FAULT {'ON' if state['fault'] else 'OFF'} applied by "
                                  f"inv-lockholder TID {holder_tid['tid']}")

    threading.Thread(target=lock_holder, name="inv-lockholder", daemon=True).start()
    ready.wait()
    req_tid = threading.get_native_id()
    write_ready(args.run_dir, "inventory", {
        "threads": {"inventory (request thread)": req_tid, "inv-lockholder": holder_tid["tid"]},
        "mutex_address": hex(mutex.address),
        "fds": {k: pipe_inode("self", getattr(args, k)) for k in
                ("up_req_r", "up_rep_w", "down_req_w", "down_rep_r")}})
    log_line(log, f"inventory PID {os.getpid()} request TID {req_tid} lock-holder TID {holder_tid['tid']} "
                  f"mutex @ {hex(mutex.address)}")

    with open(os.path.join(args.run_dir, "inventory_requests.csv"), "w", newline="") as f, \
         open(os.path.join(args.run_dir, "hop_inventory.csv"), "w", newline="") as hf:
        w, hw = csv.writer(f), csv.writer(hf)
        w.writerow(["ts", "seq", "tid", "lock_wait_ms", "hold_ms"])
        hw.writerow(["ts", "seq", "total_ms", "downstream_ms"])
        while True:
            req = read_exact(args.up_req_r, FRAME.size)
            if not req:
                break
            seq, _ = FRAME.unpack(req)
            t0 = time.perf_counter_ns()
            os.write(args.down_req_w, req)  # read stock level from Database
            rep = read_exact(args.down_rep_r, FRAME.size)
            t1 = time.perf_counter_ns()
            if not rep:
                break
            mutex.lock()  # reserve stock: needs the same lock as the restock job
            t2 = time.perf_counter_ns()
            time.sleep(args.request_hold_ms / 1000.0)
            t3 = time.perf_counter_ns()
            mutex.unlock()
            os.write(args.up_rep_w, rep)
            now = fmt(utc_now())
            w.writerow([now, seq, req_tid, f"{(t2-t1)/1e6:.3f}", f"{(t3-t2)/1e6:.3f}"])
            hw.writerow([now, seq, f"{(time.perf_counter_ns()-t0)/1e6:.3f}", f"{(t1-t0)/1e6:.3f}"])
            f.flush()
            hf.flush()
    log_line(log, "inventory upstream closed; exiting")
    os.close(args.down_req_w)


def role_database(args) -> None:
    set_thread_name("database")
    log = open(os.path.join(args.run_dir, "database.log"), "a")
    table = {i: 100 + i % 7 for i in range(1024)}
    write_ready(args.run_dir, "database", {"threads": {"database": threading.get_native_id()},
                                           "fds": {k: pipe_inode("self", getattr(args, k))
                                                   for k in ("up_req_r", "up_rep_w")}})
    log_line(log, f"database PID {os.getpid()} TID {threading.get_native_id()} serving")
    while True:
        req = read_exact(args.up_req_r, FRAME.size)
        if not req:
            break
        seq, _ = FRAME.unpack(req)
        time.sleep(0.0005)  # index lookup
        os.write(args.up_rep_w, FRAME.pack(seq, table[seq % 1024]))
    log_line(log, "database upstream closed; exiting")


# --------------------------------------------------------------------------- launcher
def spawn(role, run_dir, fds: dict, opts: list, log_path: str):
    cmd = [sys.executable, os.path.abspath(__file__), "role", role, "--run-dir", run_dir]
    for k, v in fds.items():
        cmd += [f"--{k.replace('_', '-')}", str(v)]
    cmd += opts
    out = open(log_path, "ab")
    return subprocess.Popen(cmd, pass_fds=list(fds.values()), stdin=subprocess.DEVNULL,
                            stdout=out, stderr=subprocess.STDOUT, close_fds=True)


def pipe_set(pid: int) -> set:
    s = set()
    for fd in os.listdir(f"/proc/{pid}/fd"):
        link = pipe_inode(pid, fd)
        if link.startswith("pipe:["):
            s.add(link)
    return s


def sleep_until(t: datetime) -> None:
    while True:
        d = (t - utc_now()).total_seconds()
        if d <= 0:
            return
        time.sleep(min(d, 0.5))


def cmd_run(a) -> None:
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    run_dir = os.path.abspath(os.path.join(a.out, stamp))
    os.makedirs(run_dir)

    # One request pipe and one reply pipe per adjacent pair: hop i joins ROLES[i] -> ROLES[i+1].
    hops = [{"req": os.pipe(), "rep": os.pipe()} for _ in range(4)]  # (read_end, write_end)
    fds = {r: {} for r in ROLES}
    for i, h in enumerate(hops):
        up, down = ROLES[i], ROLES[i + 1]
        fds[up]["down_req_w"] = h["req"][1]
        fds[up]["down_rep_r"] = h["rep"][0]
        fds[down]["up_req_r"] = h["req"][0]
        fds[down]["up_rep_w"] = h["rep"][1]

    role_opts = {
        "client": ["--think-ms", str(a.think_ms)],
        "inventory": ["--fault-hold-ms", str(a.fault_hold_ms), "--fault-gap-ms", str(a.fault_gap_ms),
                      "--normal-hold-ms", str(a.normal_hold_ms), "--normal-gap-ms", str(a.normal_gap_ms),
                      "--request-hold-ms", str(a.request_hold_ms)],
    }
    procs = {}
    for role in reversed(ROLES):  # servers first, client last
        procs[role] = spawn(role, run_dir, fds[role], role_opts.get(role, []),
                            os.path.join(run_dir, f"{role}.stdout"))
    for h in hops:  # the launcher keeps no pipe ends, so EOF propagates and it never joins the graph
        for end in (*h["req"], *h["rep"]):
            os.close(end)

    deadline = time.time() + 15
    while not all(os.path.exists(os.path.join(run_dir, f"ready_{r}.json")) for r in ROLES):
        if time.time() > deadline or any(p.poll() is not None for p in procs.values()):
            for r in ROLES:
                p = os.path.join(run_dir, f"{r}.stdout")
                if os.path.exists(p) and os.path.getsize(p):
                    print(f"--- {r}.stdout ---\n" + open(p).read())
            sys.exit("a role failed to start; see the logs above")
        time.sleep(0.05)
    ready = {r: json.load(open(os.path.join(run_dir, f"ready_{r}.json"))) for r in ROLES}

    # ---- topology check from /proc: adjacent pairs share exactly 2 pipe inodes, others none
    sets = {r: pipe_set(ready[r]["pid"]) for r in ROLES}
    topo_ok = True
    topo = []
    for i in range(len(ROLES)):
        for j in range(i + 1, len(ROLES)):
            shared = sets[ROLES[i]] & sets[ROLES[j]]
            expect = 2 if j == i + 1 else 0
            ok = len(shared) == expect
            topo_ok &= ok
            if expect or shared:
                topo.append((ROLES[i], ROLES[j], sorted(shared), ok))

    pids = ",".join(str(ready[r]["pid"]) for r in ROLES)
    db_name = f"chain-{stamp}.db3"
    meta = {"run_dir": run_dir, "stamp": stamp, "pids": pids, "db_name": db_name,
            "roles": {r: {k: ready[r][k] for k in ready[r] if k != "role"} for r in ROLES},
            "params": vars(a)}
    json.dump(meta, open(os.path.join(run_dir, "run.json"), "w"), indent=2, default=str)

    print(f"\nRun directory: {run_dir}\n")
    print(f"{'role':<10} {'PID':>8}  threads (name=TID)")
    for r in ROLES:
        t = ", ".join(f"{k}={v}" for k, v in ready[r]["threads"].items())
        print(f"{r:<10} {ready[r]['pid']:>8}  {t}")
    print(f"\nInventory mutex address: {ready['inventory']['mutex_address']}")
    print("\nPipe topology (from /proc/<pid>/fd):")
    for a_, b_, shared, ok in topo:
        print(f"  {'OK ' if ok else 'BAD'} {a_:<9} <-> {b_:<9} {' '.join(shared) or 'no shared pipe'}")
    if not topo_ok:
        for p in procs.values():
            p.terminate()
        sys.exit("pipe topology is wrong; aborting")

    print("\nStart PRISM in a second terminal (from the PRISM repository root):")
    print(f"  sudo RUST_LOG=info ./target/release/metric-collector --machine-id 1 \\\n"
          f"       --pids {pids} --duckdb-directory ./data --duckdb-file {db_name}")
    print("  # or: sudo RUST_LOG=info nix run .#prism -- --machine-id 1 --pids "
          f"{pids} --duckdb-file {db_name}")
    if a.no_wait:
        time.sleep(a.start_delay)
    else:
        input("\nWhen PRISM is running (it logs that its subscribers started), press Enter to start the phases... ")

    # ---- phases, aligned to whole seconds so Hade windows map onto PRISM's 1 s buckets
    t = (utc_now() + timedelta(seconds=2)).replace(microsecond=0)
    plan = [("warmup", a.warmup), ("baseline", a.baseline), ("fault", a.fault), ("recovery", a.recovery)]
    phases = []
    for name, dur in plan:
        phases.append((name, t, t + timedelta(seconds=dur)))
        t = t + timedelta(seconds=dur)
    phase_log = open(os.path.join(run_dir, "phases.log"), "a")

    sleep_until(phases[0][1])
    procs["client"].send_signal(signal.SIGUSR1)
    for name, start, end in phases:
        sleep_until(start)
        if name == "fault":
            procs["inventory"].send_signal(signal.SIGUSR1)
        if name == "recovery":
            procs["inventory"].send_signal(signal.SIGUSR2)
        log_line(phase_log, f"{name.upper()} START")
        print(f"{fmt(utc_now())} UTC  {name.upper()} START  ({int((end-start).total_seconds())} s)", flush=True)
    sleep_until(phases[-1][2])
    log_line(phase_log, "RECOVERY END")
    print(f"{fmt(utc_now())} UTC  RECOVERY END", flush=True)

    with open(os.path.join(run_dir, "phases.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase", "start", "end"])
        for name, s, e in phases:
            w.writerow([name, fmt(s), fmt(e)])

    # Equal-length analysis windows, trimmed inside each phase to drop transition samples.
    n = min(a.baseline, a.fault, a.recovery) - 2 * a.trim
    win = {}
    for name in ("baseline", "fault", "recovery"):
        s = dict((p[0], p[1]) for p in phases)[name] + timedelta(seconds=a.trim)
        win[name] = (s, s + timedelta(seconds=n))
    with open(os.path.join(run_dir, "windows.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["range_type", "start", "end", "hade"])
        w.writerow(["baseline", fmt(win["baseline"][0]), fmt(win["baseline"][1]), "yes"])
        w.writerow(["compare", fmt(win["fault"][0]), fmt(win["fault"][1]), "yes"])
        w.writerow(["recovery", fmt(win["recovery"][0]), fmt(win["recovery"][1]), "check only"])

    print("\nHade KPI ranges (type these into '3 · Ranges in use', UTC):")
    print(f"  baseline  {fmt(win['baseline'][0])}  ->  {fmt(win['baseline'][1])}")
    print(f"  compare   {fmt(win['fault'][0])}  ->  {fmt(win['fault'][1])}")
    print(f"  (recovery check window {fmt(win['recovery'][0])} -> {fmt(win['recovery'][1])}, not entered in Hade)")

    if a.no_wait:
        time.sleep(a.stop_delay)
    else:
        input("\nStop PRISM now (Ctrl-C in its terminal, wait for it to exit), then press Enter to stop the chain... ")
    procs["client"].send_signal(signal.SIGTERM)
    for r in ROLES:
        try:
            procs[r].wait(timeout=10)
        except subprocess.TimeoutExpired:
            procs[r].kill()
    log_line(phase_log, "CHAIN STOPPED")
    print(f"\nDone. latency.csv and logs are in {run_dir}")
    print(f"Next: python3 analyze_run.py {run_dir} --db <path to {db_name}> --prism-repo <PRISM repo root>")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="launch the five processes and drive the phases")
    r.add_argument("--out", default="runs")
    r.add_argument("--warmup", type=int, default=15)
    r.add_argument("--baseline", type=int, default=40)
    r.add_argument("--fault", type=int, default=40)
    r.add_argument("--recovery", type=int, default=40)
    r.add_argument("--trim", type=int, default=5, help="seconds dropped at each end of a phase")
    r.add_argument("--think-ms", type=float, default=100.0)
    r.add_argument("--fault-hold-ms", type=float, default=500.0)
    r.add_argument("--fault-gap-ms", type=float, default=2.0)
    r.add_argument("--normal-hold-ms", type=float, default=1.0)
    r.add_argument("--normal-gap-ms", type=float, default=40.0)
    r.add_argument("--request-hold-ms", type=float, default=3.0)
    r.add_argument("--no-wait", action="store_true", help="do not prompt (scripted runs)")
    r.add_argument("--start-delay", type=float, default=5.0)
    r.add_argument("--stop-delay", type=float, default=3.0)

    ro = sub.add_parser("role")
    ro.add_argument("role", choices=ROLES)
    ro.add_argument("--run-dir", required=True)
    for k in ("up-req-r", "up-rep-w", "down-req-w", "down-rep-r"):
        ro.add_argument(f"--{k}", type=int)
    ro.add_argument("--think-ms", type=float, default=100.0)
    ro.add_argument("--fault-hold-ms", type=float, default=500.0)
    ro.add_argument("--fault-gap-ms", type=float, default=2.0)
    ro.add_argument("--normal-hold-ms", type=float, default=1.0)
    ro.add_argument("--normal-gap-ms", type=float, default=40.0)
    ro.add_argument("--request-hold-ms", type=float, default=3.0)

    a = ap.parse_args()
    if a.cmd == "run":
        if min(a.baseline, a.fault, a.recovery) <= 2 * a.trim:
            ap.error("each phase must be longer than 2 * --trim")
        cmd_run(a)
    else:
        {"client": role_client,
         "gateway": lambda x: role_relay(x, "gateway", 0.0002),
         "api": lambda x: role_relay(x, "api", 0.0002),
         "inventory": role_inventory,
         "database": role_database}[a.role](a)


if __name__ == "__main__":
    main()
