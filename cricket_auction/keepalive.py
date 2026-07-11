#!/usr/bin/env python3
"""
Keep-alive ping for Render free tier.
Run this as a separate worker or scheduled job.
Prevents cold starts by hitting your own app every 10 minutes.
"""

import urllib.request
import time
import os

YOUR_APP_URL = os.environ.get('APP_URL', 'https://ipl-auction-p98z.onrender.com')
PING_INTERVAL = 600  # 10 minutes

def ping():
    try:
        req = urllib.request.Request(
            f'{YOUR_APP_URL}/health',
            method='HEAD',
            headers={'User-Agent': 'Render-KeepAlive/1.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f'[{time.strftime("%H:%M:%S")}] Keep-alive OK: {resp.status}')
    except Exception as e:
        print(f'[{time.strftime("%H:%M:%S")}] Keep-alive failed: {e}')

if __name__ == '__main__':
    print(f'Starting keep-alive for {YOUR_APP_URL}')
    while True:
        ping()
        time.sleep(PING_INTERVAL)