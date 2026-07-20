import requests
import json
import time
import sys
import re
import threading
import random
from collections import deque

POLL_URL = "https://poll.fm/17221304"
RESULTS_URL = "https://poll.fm/17221304/results"
TARGET_ANSWER = "75253678"
VOTES = 0
DELAY = 0
MAX_WORKERS = 60
PROXY_COOLDOWN = 30
BATCH_SIZE = 1

WEBSHARE_API_KEY = "sz2c0mx1fmbm92c059opdz6bf1vos6m3lu484x11"
PROXY_FILE = "proxies.txt"

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": POLL_URL,
}

# ---------------------------------------------------------------------------
# Proxy bootstrap
# ---------------------------------------------------------------------------

def fetch_webshare_proxies(api_key):
    proxies = []
    page = 1
    while True:
        try:
            resp = requests.get(
                "https://proxy.webshare.io/api/proxy/list/",
                headers={"Authorization": f"Token {api_key}"},
                params={"page": page, "page_size": 25},
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"[!] Webshare API returned {resp.status_code}")
                break
            data = resp.json()
            for p in data["results"]:
                proxies.append(
                    f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['ports']['http']}"
                )
            if data["next"] is None:
                break
            page += 1
        except Exception as e:
            print(f"[!] Webshare API error: {e}")
            break
    return proxies

def load_proxy_file(path):
    proxies = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line)
    except FileNotFoundError:
        pass
    return proxies

proxies = []
if WEBSHARE_API_KEY:
    print("[*] Fetching proxies from Webshare...")
    proxies = fetch_webshare_proxies(WEBSHARE_API_KEY)
    print(f"    {len(proxies)} proxies loaded")

if not proxies:
    proxies = load_proxy_file(PROXY_FILE)
    if proxies:
        print(f"[*] Loaded {len(proxies)} proxies from {PROXY_FILE}")

if not proxies:
    print("    No proxies — using direct connection\n")

# ---------------------------------------------------------------------------
# Proxy pool with cooldown
# ---------------------------------------------------------------------------

class ProxyPool:
    def __init__(self, proxies, cooldown=65):
        self.all = list(proxies)
        self.available = list(proxies)
        self.cooldown = cooldown
        self.cond = threading.Condition()

    def acquire(self):
        with self.cond:
            while not self.available:
                self.cond.wait()
            proxy = random.choice(self.available)
            self.available.remove(proxy)
            return proxy

    def release(self, proxy):
        def _releaser():
            time.sleep(self.cooldown)
            with self.cond:
                if proxy in self.all:
                    self.available.append(proxy)
                    self.cond.notify(1)
        threading.Thread(target=_releaser, daemon=True).start()

    def mark_dead(self, proxy):
        with self.cond:
            if proxy in self.available:
                self.available.remove(proxy)
            if proxy in self.all:
                self.all.remove(proxy)

    def refresh(self, new_proxies):
        with self.cond:
            self.all = list(new_proxies)
            self.available = list(new_proxies)
            self.cond.notify_all()

class _NoProxyPool:
    available = []
    all = []
    def acquire(self): return None
    def release(self, proxy): pass
    def mark_dead(self, proxy): pass
    def refresh(self, new_proxies): pass

pool = ProxyPool(proxies, PROXY_COOLDOWN) if proxies else _NoProxyPool()

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
first_results = deque(maxlen=200)
last_refresh = time.time()
last_gap_fetch = 0

WORKERS = min(MAX_WORKERS, len(proxies)) if proxies else MAX_WORKERS
print(f"    {WORKERS} workers  |  cooldown {PROXY_COOLDOWN}s\n")

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
# Pool refresh
# ---------------------------------------------------------------------------

def record_first_attempt(success):
    first_results.append(success)

def refresh_pool():
    global last_refresh, count, rejected, t0
    print(f"\n[*] Refreshing proxy pool (rejection too high)...")
    new_proxies = fetch_webshare_proxies(WEBSHARE_API_KEY)
    if new_proxies:
        pool.refresh(new_proxies)
        first_results.clear()
        with lock:
            count = 0
            rejected = 0
            t0 = time.time()
        print(f"[*] {len(new_proxies)} fresh proxies loaded\n")
        last_refresh = time.time()
    else:
        print(f"[!] Proxy fetch failed, keeping current pool\n")

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def vote(wid):
    global count, voted, rejected, running
    while running:
        proxy = pool.acquire() if pool.all else None

        s = requests.Session()
        s.headers.update(headers)
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}

        try:
            resp = s.get(POLL_URL, timeout=15)
            match = re.search(r'data-vote="([^"]+)"', resp.text)
            if not match:
                s.close()
                if proxy:
                    pool.release(proxy)
                time.sleep(1)
                continue
            data = json.loads(match.group(1).replace("&quot;", '"'))

            params = {
                "va": data["at"], "pt": data["m"], "r": data["b"],
                "p": data["id"], "a": f"{TARGET_ANSWER},", "o": "",
                "t": data["t"], "token": data["n"], "pz": 1,
            }

            for attempt in range(BATCH_SIZE):
                r = s.get("https://poll.fm/vote", params=params, allow_redirects=True, timeout=15)
                with lock:
                    count += 1
                    if "msg=voted" in r.url:
                        voted += 1
                        if attempt == 0:
                            record_first_attempt(True)
                    else:
                        rejected += 1
                        if attempt == 0:
                            record_first_attempt(False)
                            if proxy:
                                pool.mark_dead(proxy)
                        break
        except requests.exceptions.ConnectionError:
            if proxy:
                pool.mark_dead(proxy)
            s.close()
            time.sleep(1)
            continue
        except requests.exceptions.Timeout:
            if proxy:
                pool.mark_dead(proxy)
            s.close()
            time.sleep(1)
            continue
        except Exception:
            s.close()
            time.sleep(2)
            continue

        s.close()
        if proxy:
            pool.release(proxy)
        if DELAY:
            time.sleep(DELAY)

# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

def pool_refresher():
    global running, last_refresh, target, last_gap_fetch
    while running:
        time.sleep(2)
        with lock:
            if target and voted >= target:
                running = False
                return

        avail = len(pool.available) if pool else 0
        alive = sum(1 for t in threading.enumerate()
                    if t.name and t.name.startswith("wk") and t.is_alive())

        if alive < WORKERS and avail > 0:
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

        rej_ratio = 0
        if len(first_results) >= 50:
            rej_ratio = 1 - sum(first_results) / len(first_results)

        if rej_ratio > 0.8 and now - last_refresh > 60 and pool:
            refresh_pool()
        elif alive == 0 and avail == 0 and pool:
            if rej_ratio > 0.8:
                refresh_pool()
            else:
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
            avail = len(pool.available) if pool else 0
        alive = sum(1 for t in threading.enumerate()
                    if t.name and t.name.startswith("wk") and t.is_alive())
        rate_now = (cur - last) / 5 * 60
        rate_avg = cur / (time.time() - t0) * 60
        print(f"[{cur:6d}]  {rate_avg:.0f}/min  {v} voted  {r} rejected  {alive}/{WORKERS} wk  {avail} proxy")
        last = cur

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

print(f"Voting for BINI  |  Target: dynamic (overtake SB19)\n")

for i in range(WORKERS):
    t = threading.Thread(target=vote, args=(i,), daemon=True, name=f"wk{i}")
    t.start()
    time.sleep(0.1)

threading.Thread(target=stats_printer, daemon=True).start()
threading.Thread(target=pool_refresher, daemon=True).start()

try:
    while running and (not target or voted < target):
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopped by user.")
    running = False

elapsed = time.time() - t0
print(f"\nDone: {count} requests, {voted} voted, {rejected} rejected in {elapsed:.0f}s ({count/elapsed*60:.0f}/min)")
