import requests
import json
import time
import re
import threading

POLL_URL = "https://poll.fm/17221304"
RESULTS_URL = "https://poll.fm/17221304/results"
TARGET_ANSWER = "75253678"
PROXY = "http://pkqdjhed-rotate:afv96ptew5fb@p.webshare.io:80/"
MAX_WORKERS = 200

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": POLL_URL,
}

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

count = 0
voted = 0
rejected = 0
t0 = time.time()
lock = threading.Lock()
running = True
target = 0
last_gap_fetch = 0

WORKERS = MAX_WORKERS
print(f"    {WORKERS} workers  |  Webshare rotating endpoint\n")

# ---------------------------------------------------------------------------
# Dynamic target
# ---------------------------------------------------------------------------

def fetch_gap():
    try:
        resp = requests.get(RESULTS_URL, timeout=15)
        m = re.search(r'SB19[^)]*\(([0-9,]+) votes\)', resp.text)
        n = re.search(r'BINI[^)]*\(([0-9,]+) votes\)', resp.text)
        if m and n:
            sb19 = int(m.group(1).replace(',', ''))
            bini = int(n.group(1).replace(',', ''))
            return max(0, sb19 - bini)
    except:
        pass
    return None

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def vote(wid):
    global count, voted, rejected, running

    s = requests.Session()
    s.headers.update(headers)
    s.proxies = {"http": PROXY, "https": PROXY}

    while running:
        try:
            resp = s.get(POLL_URL, timeout=15)
            match = re.search(r'data-vote="([^"]+)"', resp.text)
            if not match:
                time.sleep(1)
                continue
            data = json.loads(match.group(1).replace("&quot;", '"'))

            params = {
                "va": data["at"], "pt": data["m"], "r": data["b"],
                "p": data["id"], "a": f"{TARGET_ANSWER},", "o": "",
                "t": data["t"], "token": data["n"], "pz": 1,
            }

            r = s.get("https://poll.fm/vote", params=params, allow_redirects=True, timeout=15)
            with lock:
                count += 1
                if "msg=voted" in r.url:
                    voted += 1
                else:
                    rejected += 1
        except requests.exceptions.ConnectionError:
            time.sleep(1)
            continue
        except requests.exceptions.Timeout:
            time.sleep(1)
            continue
        except Exception:
            time.sleep(2)
            continue

# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

def supervisor():
    global running, target, last_gap_fetch
    while running:
        time.sleep(2)
        with lock:
            if target and voted >= target:
                running = False
                return

        alive = sum(1 for t in threading.enumerate()
                    if t.name and t.name.startswith("wk") and t.is_alive())

        if alive < WORKERS:
            for wid in range(WORKERS):
                name = f"wk{wid}"
                if not any(t.name == name and t.is_alive() for t in threading.enumerate()):
                    t = threading.Thread(target=vote, args=(wid,), daemon=True, name=name)
                    t.start()
                    time.sleep(0.05)

        now = time.time()
        if not target or now - last_gap_fetch > 300:
            gap = fetch_gap()
            if gap is not None:
                target = gap
                last_gap_fetch = now
                print(f"[i] Target updated: {target:,} votes needed to overtake SB19")

        if not alive:
            running = False

# ---------------------------------------------------------------------------
# Stats printer
# ---------------------------------------------------------------------------

def stats_printer():
    global running
    last = 0
    while running:
        time.sleep(5)
        with lock:
            cur = count
            v = voted
            r = rejected
        alive = sum(1 for t in threading.enumerate()
                    if t.name and t.name.startswith("wk") and t.is_alive())
        rate_now = (cur - last) / 5 * 60
        rate_avg = cur / (time.time() - t0) * 60
        print(f"[{cur:6d}]  {rate_avg:.0f}/min  {v} voted  {r} rejected  {alive}/{WORKERS} wk")
        last = cur

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

print(f"Voting for BINI (Webshare rotating)  |  Target: dynamic (overtake SB19)\n")

for i in range(WORKERS):
    t = threading.Thread(target=vote, args=(i,), daemon=True, name=f"wk{i}")
    t.start()
    time.sleep(0.05)

threading.Thread(target=stats_printer, daemon=True).start()
threading.Thread(target=supervisor, daemon=True).start()

try:
    while running and (not target or voted < target):
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopped by user.")
    running = False

elapsed = time.time() - t0
print(f"\nDone: {count} requests, {voted} voted, {rejected} rejected in {elapsed:.0f}s ({count/elapsed*60:.0f}/min)")
