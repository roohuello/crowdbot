import requests
import json
import re
import time

POLL_URL = "https://poll.fm/17221304"
TARGET_ANSWER = "75253678"
VOTES = 900000
DELAY = 35

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": POLL_URL,
}

voted = 0
rejected = 0
t0 = time.time()

print(f"Voting for BINI (direct IP)  |  Target: {VOTES}\n")

while voted < VOTES:
    s = requests.Session()
    s.headers.update(headers)

    try:
        resp = s.get(POLL_URL, timeout=15)
        match = re.search(r'data-vote="([^"]+)"', resp.text)
        if not match:
            s.close()
            time.sleep(5)
            continue
        data = json.loads(match.group(1).replace("&quot;", '"'))
        params = {
            "va": data["at"], "pt": data["m"], "r": data["b"],
            "p": data["id"], "a": f"{TARGET_ANSWER},", "o": "",
            "t": data["t"], "token": data["n"], "pz": 1,
        }
        r = s.get("https://poll.fm/vote", params=params, allow_redirects=True, timeout=15)
        if "msg=voted" in r.url:
            voted += 1
        else:
            rejected += 1
    except Exception:
        s.close()
        time.sleep(5)
        continue

    s.close()

    elapsed = time.time() - t0
    rate = voted / elapsed * 60
    print(f"[{voted:6d}]  {rate:.0f}/min  {voted} voted  {rejected} rejected")
    time.sleep(DELAY)

elapsed = time.time() - t0
print(f"\nDone: {voted} voted, {rejected} rejected in {elapsed:.0f}s ({voted/elapsed*60:.0f}/min)")
