import csv
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone

ADDRESS = ("127.0.0.1", 8765)
mutex = threading.Lock()
fault = threading.Event()


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def announce(message):
    print(f"{utc_now()} UTC | {message}", flush=True)


def serve_requests(listener):
    announce(f"Worker A, request handler, TID {threading.get_native_id()}")

    while True:
        connection, _ = listener.accept()
        with connection:
            if not connection.recv(1):
                continue

            started = time.perf_counter()
            with mutex:
                wait_ms = (time.perf_counter() - started) * 1000
                time.sleep(0.001)

            connection.sendall(b"Y")
            if wait_ms >= 50:
                announce(f"Worker A waited {wait_ms:.1f} ms for the mutex")


def use_shared_lock():
    announce(f"Worker B, lock holder, TID {threading.get_native_id()}")

    while True:
        with mutex:
            faulty = fault.is_set()
            time.sleep(0.5 if faulty else 0.001)

        if faulty:
            announce("Worker B released the mutex after a 500 ms hold")
            time.sleep(0.025)
        else:
            time.sleep(0.040)


def run_server():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(ADDRESS)
        listener.listen()
        announce(f"Server PID {os.getpid()}")

        threading.Thread(target=serve_requests, args=(listener,), daemon=True).start()
        threading.Thread(target=use_shared_lock, daemon=True).start()

        input("Start the client and PRISM, then press Enter to begin the recording phases. ")
        announce("BASELINE START")
        time.sleep(20)

        announce("FAULT START")
        fault.set()
        time.sleep(20)

        announce("RECOVERY START")
        fault.clear()
        time.sleep(20)

        announce("RECOVERY END. Stop PRISM and the client before exiting this server.")
        input("Press Enter to exit the server. ")


def run_client():
    announce(f"Client PID {os.getpid()}")
    with open("latency.csv", "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["ts", "response_ms"])
        file.flush()

        while True:
            started = time.perf_counter()

            with socket.create_connection(ADDRESS, timeout=3) as connection:
                connection.sendall(b"Q")
                if connection.recv(1) != b"Y":
                    raise RuntimeError("Server closed without replying")

            elapsed_ms = (time.perf_counter() - started) * 1000
            writer.writerow([utc_now(), f"{elapsed_ms:.3f}"])
            file.flush()
            time.sleep(0.1)


if __name__ == "__main__":
    if sys.argv[1:] == ["server"]:
        run_server()
    elif sys.argv[1:] == ["client"]:
        run_client()
    else:
        raise SystemExit("Usage: python3 demo_workload.py server|client")
