# Offline logging: manual test

Checks that taps logged with no signal are never lost and never doubled
(issue #31). Run it on a real phone for each platform: Android Chrome, and
iOS Safari from the home-screen app, since iOS has no Background Sync.

The automated side is `stock/test_offline_sync.py`: idempotency, timestamp
limits, and a tap queued by one user never being logged under another. This
script covers what only a real browser can show.

## Setup
1. Sign the phone in with the practice login, then tap an assistant's code.
2. Open **Log usage** once with a connection. The grid is cached after
   login too, but opening it once makes sure.
3. On a laptop, open **Activity** as an admin and note the most recent entry.

## 1. Five taps in airplane mode
1. Turn on airplane mode.
2. The bar above the bottom navigation says **"Offline · taps are saved and
   sync later"**.
3. Search for an item. The grid filters as you type.
4. Log 5 taps on different items: open a tile, then tap "Used 1…",
   "Used the last one" or "Running low".
   - Each tap shows **"Saved offline · will sync"** and closes the sheet.
   - The bar counts up: "Offline · 5 taps waiting to sync".
5. On the laptop, refresh Activity. None of the 5 taps are there yet.

## 2. Reconnect
1. Turn airplane mode off, staying on the Log usage page.
2. Within a few seconds: **"5 taps logged offline have synced."**, and the
   bar disappears.
3. On the laptop, refresh Activity. **Each of the 5 taps appears exactly
   once**, at the time it was tapped, not the time it synced.

## 3. App opened with no signal
1. Close the app completely. Turn on airplane mode, then open the app.
2. You get the "No connection" page. Tap **Log usage**: the cached grid
   opens.
3. Log 2 taps. Each shows "Saved offline · will sync".
4. Close the app, turn airplane mode off, and open the app again.
5. "2 taps logged offline have synced." Activity shows both, once each.

## 4. Patchy Wi-Fi
1. Stand where the Wi-Fi is weak, or connect to a network with no internet.
2. Log a tap. If the server doesn't answer within 8 seconds, it's saved
   offline instead, and syncs later once.
3. Check Activity: the tap is there exactly once, even if the first attempt
   did reach the server.

## Pass
Every tap made offline appears in Activity exactly once, under the person
who made it, at the time it was made.
