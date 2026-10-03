# Cloud Deployment Guide — DigitalOcean (24/7 Hosting)

This gets your Alpaca paper-trading bot running on an always-on cloud server so
it keeps trading whether or not your laptop is on. It's written to follow after
`SETUP_GUIDE.md` — you should already have the bot working locally and your
Alpaca **paper** keys in hand.

**What you're building:** a small Linux server (DigitalOcean calls it a
"Droplet") that runs the bot around the clock and restarts itself if it crashes
or the server reboots. You connect to it only to set it up and check on it.

**Cost:** the smallest Basic Droplet is **$4/month** <!-- asof:do-droplet-smallest --> (512 MB RAM); I recommend
the **1 GB RAM tier (~$6/month)** <!-- asof:do-droplet-recommended --> so Python has breathing room. Billing is
per-second with a monthly cap, and **new accounts get $200 <!-- asof:do-free-credit --> of free credit for
60 days** <!-- asof:do-free-credit-days -->, so your first couple of months are effectively free. You only pay
while the Droplet exists — destroy it and billing stops.

**Important scope:** this hosts the **Alpaca** bots (stocks / ETFs / options),
which are pure API calls and run perfectly on a headless server. The **IBKR**
futures bot is *not* covered here — it needs IBKR's Gateway desktop app running,
which requires extra tooling on a server. Keep IBKR on your desktop for now.

You'll spend about 30–45 minutes the first time. Commands you run **on your
Windows laptop** are marked *(laptop)*; commands you run **on the server** are
marked *(server)*.

---

## Part 1 — Create a DigitalOcean account

1. Go to **https://www.digitalocean.com/** and sign up. You'll need to add a
   payment method (card or PayPal) to verify identity, even to use the free
   credit.
2. Look for the **$200 / 60-day free credit** for new accounts (usually applied
   automatically; there may be a promo banner). This covers your hosting while
   you evaluate.

---

## Part 2 — Create an SSH key on your laptop *(laptop)*

An SSH key is a secure passwordless way to log into your server. Windows 10/11
has the tool built in.

1. Open **PowerShell** and run:
   ```powershell
   ssh-keygen -t ed25519 -C "trading-bot"
   ```
2. Press **Enter** to accept the default file location. When it asks for a
   passphrase, you can press Enter twice for none (simpler) or set one (more
   secure — you'll type it when connecting).
3. Show your **public** key so you can copy it in the next part:
   ```powershell
   Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
   ```
   Copy the whole line it prints (starts with `ssh-ed25519 …`). This is the
   *public* half and is safe to share. Never share the other file
   (`id_ed25519`, no `.pub`) — that's your private key.

---

## Part 3 — Create the Droplet

1. In the DigitalOcean control panel, click **Create → Droplets**.
2. **Choose an image:** Ubuntu, version **24.04 (LTS)**.
3. **Choose a region:** pick **New York** (closest to US markets — latency isn't
   critical for this bot, but there's no reason not to).
4. **Choose size:** under **Basic → Regular**, pick the **1 GB RAM / 1 vCPU**
   option (about $6/mo <!-- asof:do-droplet-recommended -->). The $4 <!-- asof:do-droplet-smallest --> option (512 MB) also works if you add swap
   later (Part 5), but 1 GB is the hassle-free choice.
5. **Authentication:** choose **SSH Key → Add New SSH Key**, paste the public key
   you copied in Part 2, give it a name, and add it.
6. **Hostname:** name it something like `trading-bot`.
7. Click **Create Droplet**. After ~30 seconds it'll show an **IP address** —
   copy it. That's your server's address (referred to below as `YOUR_IP`).

---

## Part 4 — Connect to your server *(laptop)*

In PowerShell:
```powershell
ssh root@YOUR_IP
```
The first time it'll ask "are you sure you want to continue connecting?" — type
**yes**. You should land at a prompt like `root@trading-bot:~#`. You're on the
server now.

> If it refuses the key, make sure you pasted the correct public key in Part 3.
> See Troubleshooting.

---

## Part 5 — Secure the server *(server)*

Running as `root` all the time is risky. Create a normal user and update the
system. Run these on the server (replace `trader` with any username you like):

```bash
# Update the system:
apt update && apt upgrade -y

# Create a user and give it admin (sudo) rights:
adduser trader          # it'll prompt for a password — set one you'll remember
usermod -aG sudo trader

# Let that user log in with the same SSH key you already used:
rsync --archive --chown=trader:trader ~/.ssh /home/trader
```

**(Optional but recommended) add swap** — a safety net so a small Droplet doesn't
run out of memory while installing packages:
```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Now log out and back in as the new user:
```bash
exit
```
```powershell
# (laptop) reconnect as trader:
ssh trader@YOUR_IP
```
Your prompt should now read `trader@trading-bot:~$`. From here you'll prefix
admin commands with `sudo`.

---

## Part 6 — Install Python and tools *(server)*

Ubuntu 24.04 ships with Python 3.12 <!-- asof:python-version --> (same as your laptop). Install the extras:
```bash
sudo apt install -y python3-venv python3-pip unzip
```

---

## Part 7 — Copy your code to the server

You'll send the zip from your laptop, then unzip it on the server.

1. *(laptop)* In PowerShell, upload the zip (adjust the path to where your zip
   is; note the space before `trader@`):
   ```powershell
   scp C:\algo-trading.zip trader@YOUR_IP:/home/trader/
   ```
   (If your project is a folder rather than a zip, either zip it first, or use
   `scp -r C:\algo-trading trader@YOUR_IP:/home/trader/`.)
2. *(server)* Unzip it:
   ```bash
   cd ~
   unzip algo-trading.zip
   cd algo-trading
   ls        # you should see run_paper.py, strategies/, etc.
   ```

---

## Part 8 — Set up the Python environment *(server)*

```bash
python3 -m venv venv
source venv/bin/activate         # note: Linux uses "source", not .\  like Windows
pip install -r requirements.txt
```
This installs everything (a minute or two). When it finishes without red ERROR
lines, run a quick offline check to confirm the engine works here:
```bash
python run_backtest.py --synthetic --strategy meanrev
```
A results table means you're good.

---

## Part 9 — Store your API keys securely on the server *(server)*

Don't hardcode keys into files that could get shared. Put them in a locked-down
environment file that only your user can read:

```bash
cd ~/algo-trading
nano alpaca.env
```
In the editor, type these two lines with your **paper** keys (no quotes, no
spaces around the `=`):
```
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=your_secret_key_here
```
Save and exit nano: **Ctrl+O**, **Enter**, then **Ctrl+X**.

Lock the file down so only you can read it:
```bash
chmod 600 alpaca.env
```

---

## Part 10 — Test the bot manually *(server)*

Before making it permanent, confirm it connects to Alpaca. Load the keys into
your current shell and run it briefly:
```bash
set -a && source alpaca.env && set +a      # loads the keys into this session
python run_paper.py --broker alpaca --symbols USO --strategy meanrev --interval 5Min
```
You should see ticks. During market hours it'll evaluate/trade; off-hours it
prints "market closed; waiting" (the bot uses **Alpaca's** market clock, so the
server's time zone doesn't matter). If you see a 401, the keys are wrong — revisit
Part 9. Press **Ctrl+C** to stop the manual test.

---

## Part 11 — Run it 24/7 with auto-restart *(server)*

We'll register the bot as a **systemd service** so it starts on boot and
restarts itself if it ever crashes. This is the robust, "set it and forget it"
approach.

1. Create the service file:
   ```bash
   sudo nano /etc/systemd/system/trading-bot.service
   ```
2. Paste this, adjusting the `ExecStart` line to the exact bot command you want
   to run (symbols/strategy/interval). Keep the full paths as-is if your
   username is `trader`:
   ```ini
   [Unit]
   Description=Alpaca paper trading bot
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=trader
   WorkingDirectory=/home/trader/algo-trading
   EnvironmentFile=/home/trader/algo-trading/alpaca.env
   ExecStart=/home/trader/algo-trading/venv/bin/python run_paper.py --broker alpaca --symbols USO --strategy meanrev --interval 5Min
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
   Save and exit (**Ctrl+O**, **Enter**, **Ctrl+X**).
3. Start it and set it to launch on boot:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now trading-bot
   ```
4. Check it's running:
   ```bash
   sudo systemctl status trading-bot
   ```
   Look for **active (running)** in green. Press **q** to exit the status view.

That's it — the bot is now running on the server and will keep running after you
disconnect, close your laptop, or the server reboots.

---

## Part 12 — Managing the bot *(server)*

```bash
# Watch live output (the ticks, orders, errors):
journalctl -u trading-bot -f          # Ctrl+C to stop watching (bot keeps running)

# Stop / start / restart:
sudo systemctl stop trading-bot
sudo systemctl start trading-bot
sudo systemctl restart trading-bot

# Change what it trades: edit the ExecStart line, then:
sudo nano /etc/systemd/system/trading-bot.service
sudo systemctl daemon-reload && sudo systemctl restart trading-bot
```

**To update the code later:** upload a new zip (Part 7), unzip it over the old
folder, then `sudo systemctl restart trading-bot`. Your `venv` and `alpaca.env`
stay in place. (If a new version adds dependencies, re-run
`source venv/bin/activate && pip install -r requirements.txt` first.)

---

## Cost management

- Billing runs while the Droplet **exists**, even if the bot is stopped or the
  server is powered off (the resources stay reserved). To truly stop paying,
  **destroy** the Droplet: control panel → your Droplet → **Destroy**.
- Before destroying, note you'll lose everything on it. If you might come back,
  take a **Snapshot** first (small one-time storage cost) so you can recreate it.
- Keep an eye on the billing page during your $200 credit window so there's no
  surprise when it ends.

---

## Security checklist

- Keys live only in `alpaca.env` with `chmod 600` — never in the code or a repo.
- You're running as a non-root user (`trader`), not root.
- These are **paper** keys, so worst case is limited — but treat them like
  passwords anyway. If they ever leak, regenerate them in the Alpaca dashboard
  (which invalidates the old pair) and update `alpaca.env`.
- Optional hardening later: a `ufw` firewall and disabling root SSH login. Not
  required for a simple outbound-only bot, but good habits if you keep the server.

---

## Troubleshooting

**`ssh: connect to host … Connection refused` or key rejected**
Give the Droplet a minute after creation to finish booting. Confirm you used the
right `YOUR_IP`, and that the public key pasted in Part 3 matches the one on your
laptop (`Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub`).

**`scp` says permission denied**
Make sure you created the `trader` user and ran the `rsync … /home/trader` line
in Part 5 so the key works for that user. You can also scp as `root@YOUR_IP` and
move the file afterward.

**`pip install` killed / "MemoryError" on a 512 MB Droplet**
Add the swap file (Part 5, optional step), then retry. Or resize the Droplet up
to 1 GB in the control panel.

**Bot logs show `401 Authorization Required`**
Wrong or swapped keys in `alpaca.env`, or they're live keys not paper. Paper key
IDs start with `PK`. Fix the file, then `sudo systemctl restart trading-bot`.

**`systemctl status` shows "failed"**
Run `journalctl -u trading-bot -n 50` to see the actual error. Most common: a
typo in a path in the `.service` file, or the venv/deps not installed (Part 8).

**Bot only ever says "market closed; waiting"**
Normal outside US market hours (9:30 AM–4:00 PM Eastern, weekdays). It uses
Alpaca's clock, so it'll wake up on its own when the market opens.

---

## Quick reference — the whole flow

```
(laptop)  ssh-keygen -t ed25519 -C "trading-bot"      # once
(DO panel) create Ubuntu 24.04 Droplet, 1GB, add your SSH key
(laptop)  ssh root@YOUR_IP
(server)  apt update && apt upgrade -y
(server)  adduser trader && usermod -aG sudo trader
(server)  rsync --archive --chown=trader:trader ~/.ssh /home/trader
(laptop)  ssh trader@YOUR_IP
(server)  sudo apt install -y python3-venv python3-pip unzip
(laptop)  scp C:\algo-trading.zip trader@YOUR_IP:/home/trader/
(server)  unzip algo-trading.zip && cd algo-trading
(server)  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
(server)  nano alpaca.env   (add keys)  &&  chmod 600 alpaca.env
(server)  sudo nano /etc/systemd/system/trading-bot.service   (paste service)
(server)  sudo systemctl daemon-reload && sudo systemctl enable --now trading-bot
(server)  journalctl -u trading-bot -f    # watch it go
```

*Paper trading only. Not financial advice. Keep it on paper for weeks and
understand the behavior before considering real money.*
