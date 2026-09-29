# 🔴 HIGH: PF Facesheet Pull Can Be Silently Dropped

## What happened
On Sep 29 the 1 AM Practice Fusion facesheet pull was triggered but **did not run**. A user's manual request took the PF browser at the same second, and the scheduled pull was rejected. **Nothing loaded that day, and nothing alerted us.**

## Problem
- All PF work runs through **one browser and one login**, so only one job can run at a time.
- **Any user request can block the nightly pull.**
- The failure appears only in the server log. The job-log table on the VM is **empty, even for successful runs**.

## Impact
- **Billing is delayed:** a missed pull means that day's demographics, coverages and diagnoses don't load.
- **It fails silently:** we find out only when data is missing.
- **It gets worse as usage grows:** more users means more collisions, more "browser busy" errors, and more missed pulls.

## Fixes
| Fix | Status |
|---|---|
| 1. Scheduled jobs wait up to 10 min for the browser, and failures are logged | ✅ Done (`274d159`), needs deploy |
| 2. **Users read appointments from the DB, not the live browser** 
| 3. First-come, first-served job queue with status tracking | If usage grows |

