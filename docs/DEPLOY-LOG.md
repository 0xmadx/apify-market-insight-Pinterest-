# Deploy log

What was actually provisioned, where it lives, and how each claim was checked.
`DEPLOY.md` is the runbook — the instructions. This is the **record**: the
resource inventory a future session needs to find, verify, or tear down what
exists, plus the evidence behind each "done".

Entries are newest first.

---

## 2026-08-27 — Upstash cutover + GCP session farm

**Result:** Steps 1, 2 and 4 complete. Step 3 (Apify) blocked only on
`apify login`. The vault is on Upstash at 6/6 and fed by two independent
writers.

### Inventory — what now exists

**GCP** (all in project `pinterest-keepalive`, billing `01428F-1B6F4C-2EF76D`):

| Resource | Value |
|---|---|
| Project | `pinterest-keepalive` |
| VM | `pinterest-keepalive`, e2-medium, `us-central1-a`, internal `10.128.0.2` |
| External IP | **none** — deliberately removed |
| Disk | `pinterest-keepalive`, 30 GB `pd-balanced` |
| Router | `keepalive-router` (`us-central1`, network `default`) |
| NAT | `keepalive-nat` — auto-allocated IPs, all subnet ranges |
| Firewall | `default-allow-ssh` narrowed to `35.235.240.0/20`; `default-allow-rdp` **deleted** |
| APIs enabled | `compute`, `iap`, `oslogin` |
| Service | `keepalive.timer` → `keepalive.service`, every 5 min |
| Secret | `/etc/pinterest-keepalive/env`, `600 root:root` |
| Repo on VM | `~/pinterest-apify`, cloned via a **read-only deploy key** generated on the VM |

**Upstash:** database `pinterest-apify-vault`, endpoint
`saving-stallion-133172.upstash.io:6379`, TLS. 21 keys, 6 `ads_*` profiles.

⚠️ **One change outside this project:** billing was **unlinked from
`project-e7cb7552-1cb5-4078-b24`** ("My First Project") to free a slot, because
the billing account had hit Google's project-linking quota. That project had no
Compute API, no buckets, and only GCP's default APIs. Nothing was deleted. To
reverse: unlink `pinterest-keepalive` and re-link that one.

### Machine sizing — why e2-medium and not larger

`keepalive.one_pass` iterates profiles **in sequence** ("Every profile, in
sequence"), so exactly one Chromium is alive at a time. More vCPU buys nothing.
Measured: 6 profiles in 31 s.

### Verification, and what each check actually proves

**The farm works** — one pass, from the service journal:

```
6 profile(s) from the vault (pinterest) · one pass
  ads_k1fx40wf   OK   9 cookies via 163.123.202.173  (5.3s)
  ads_k1fy47um   OK   8 cookies via 163.123.203.141  (4.8s)
  ads_k1fy6dnh   OK   7 cookies via 199.187.190.159  (4.4s)
  ads_k1fy6e67   OK   8 cookies via 45.56.159.126    (4.1s)
  ads_k1fy6eoy   OK   8 cookies via 45.56.182.90     (4.7s)
  ads_k1fyn0gc   OK  13 cookies via 72.1.134.148     (7.0s)
  6 written · 0 skipped · 31s
```

Six distinct exit IPs, none of them the VM's NAT address — rule 4 holding. A
proxy that had failed would show the NAT IP and be **skipped**, not written.

**Which writer wrote it.** With AdsPower and GCP both on 5-minute timers, a
fresh heartbeat identifies neither. They leave different cookie counts for the
same profile, so attribution is by content, not timing:

| profile | AdsPower | GCP | Upstash showed |
|---|---|---|---|
| `ads_k1fx40wf` | 11 | 9 | **9** |
| `ads_k1fy6dnh` | 8 | 7 | **7** |
| `ads_k1fyn0gc` | 11 | 13 | **13** |

**The gate**, run against the lab vault so a staleness clock could not fail it
for the wrong reason: 16/16 endpoints, 544 checks
(95+54+34+164+55+90+52), `latest_available_date` → 2026-08-21.

### Three silent failures found

**1. The fonts never installed, and the script reported success.**
`apt` exited 0, `dpkg -l` showed `ii`, `deploy_gcp.sh` printed `installed` —
and `/usr/share/fonts/truetype/msttcorefonts/` held a README and zero `.ttf`.
The package's postinst downloads the fonts separately from SourceForge; that
download failed. Without them every family measures the same fallback width,
which is a Linux tell underneath a Windows user agent, and nothing errors.

Fixed by copying the real files from `C:\Windows\Fonts`. Verified in-browser,
9 distinct widths of 9:

```
Arial=648  Times New Roman=620  Verdana=760  Georgia=697  Tahoma=654
Courier New=562  Comic Sans MS=619  Impact=614  Trebuchet MS=661
```

`deploy_gcp.sh` now **counts resolvable families** rather than trusting an exit
code, and warns below 5.

**2. `browsers.fingerprint` cannot run on the VM.** It loads
`browsers/identities.json`, the one file this design never copies there. The
verification the runbook recommended was unrunnable on the host it verified.

**3. The Upstash migration moved data, not the writer.** Measured minutes
apart: Upstash 0/8 usable at ~13,400 s while the lab sat at 6/6 and ~280 s. The
writer's `REDIS_URL` lives in `/etc/adspower/api.env`, which is root-owned and
**beats both `.env` and the code default**. A first attempt to fix it left the
old value plus an orphan bare-URL line that systemd ignored; the syncer kept
reporting "6/6 synced" into the lab the whole time.

### Traps hit along the way

| Trap | Symptom |
|---|---|
| CRLF on a scp'd script | `set: pipefail: invalid option name` — run the clone's copy, not the Windows one |
| `pscp` (Windows gcloud) | Does not expand `~`; destinations must be absolute |
| `preflight.sh` gcloud check | Reported "not installed" for a working install — the SDK's PATH entry is invisible to already-open shells |
| `preflight.sh` writer check | Called a just-migrated vault "writer is ALIVE". One sample proves a recent *write*, never a live *writer* |
| `gh` not authenticated | Repo visibility had to be confirmed via the unauthenticated GitHub API (404 = private) |

### Remaining

1. **`apify login`**, then `./ship.sh apify` — the only blocker to being live.
2. **Rotate the AdsPower API key** (`ADS_API_KEY`) — it has appeared on screen
   and in logs; `docs/LAUNCH-READINESS.md` already listed it.
3. **Rotate `GO_TOKEN`** in the Etsy project — in this repo's history, see
   `DEPLOY.md` Step 1.
4. **Step 5** — do not disable `adspower-sync.timer` until GCP has held the
   pool through a day of cycles. Rollback is re-enabling that one timer.
5. **Upstash tier** — keepalive alone is ~5,200 commands/day against a ~10k
   free cap, before a single customer run.
