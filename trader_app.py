"""Day Trade Risk Manager desktop app: opens its own window, shows a setup wizard on first run, then the trader.

Run:  python trader_app.py        (customers use the installer built by GitHub Actions)
Reset settings: run with --setup
Settings and the daily-lock file live in  ~/.daytrade_risk_manager/
"""
import os, sys, json, stat, time, socket, secrets, threading, webbrowser, importlib
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

DATA = Path.home() / ".daytrade_risk_manager"
CFG = DATA / "config.json"

SETUP_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Day Trade Risk Manager setup</title><style>
*{box-sizing:border-box;border-radius:0}body{background:#000;color:#fff;font:14px/1.5 "IBM Plex Mono","Courier New",monospace;margin:0;padding:20px}
.w{max-width:520px;margin:0 auto;border:3px double #fff;padding:22px}h1{margin:0 0 4px;letter-spacing:3px}
label{display:block;margin:14px 0 4px;color:#bdbdbd;font-size:12px;text-transform:uppercase;letter-spacing:1px}
input,select{width:100%;background:#000;color:#fff;border:2px solid #fff;font:inherit;padding:9px}
.row{display:flex;gap:14px;flex-wrap:wrap}.row label{display:flex;gap:6px;align-items:center;margin:6px 0;color:#fff;text-transform:none;font-size:14px}
.row input{width:auto}button{margin-top:22px;width:100%;background:#fff;color:#000;border:2px solid #fff;font:700 16px inherit;padding:14px;letter-spacing:2px;box-shadow:3px 3px 0 #8a8a8a}
#err{background:#fff;color:#000;padding:8px 10px;margin:12px 0;display:none;font-weight:600}.h{color:#8a8a8a;font-size:12px}
</style></head><body><form class="w" id="setupform"><h1>RISK MANAGER SETUP</h1><div class="h">Your keys are saved only on this computer.</div>
<div id="err">__ERR__</div>
<label>Broker</label><select id="broker"><option value="oanda">OANDA (forex)</option><option value="paradex">Paradex (crypto)</option></select>
<div id="oanda"><label>OANDA API token</label><input id="okey" type="password" autocomplete="off">
<label>OANDA account ID (like 101-001-1234567-001)</label><input id="oacc">
<label>Account type</label><select id="live"><option value="0">Demo / practice (recommended first)</option><option value="1">LIVE (real money)</option></select>
<label>Pairs in the switcher</label><div class="row" id="pairs"></div></div>
<div id="paradex" style="display:none"><label>Paradex L2 address</label><input id="padd" autocomplete="off">
<label>Paradex subkey private key</label><input id="pkey" type="password" autocomplete="off"></div>
<label>Risk per trade (% of account)</label><input id="risk" type="number" step="0.1" min="0.1" max="5" value="1">
<label>Daily lock opens at (hour, 0-23)</label><input id="hour" type="number" min="0" max="23" value="6">
<label>Your timezone</label><input id="tz">
<button type="submit" id="go">SAVE &amp; START</button></form>
<script>
const $=i=>document.getElementById(i);
["EUR_USD","GBP_USD","USD_JPY","AUD_USD","USD_CHF","USD_CAD"].forEach(p=>$("pairs").innerHTML+=`<label><input type="checkbox" value="${p}" checked>${p.replace("_","/")}</label>`);
$("tz").value=Intl.DateTimeFormat().resolvedOptions().timeZone||"UTC";
if($("err").textContent.trim())$("err").style.display="block";
$("broker").onchange=()=>{$("oanda").style.display=$("broker").value=="oanda"?"block":"none";$("paradex").style.display=$("broker").value=="paradex"?"block":"none"};
$("setupform").onsubmit=async e=>{e.preventDefault();$("err").style.display="none";$("go").textContent="CHECKING...";
 const b=$("broker").value,body={broker:b,risk_pct:+$("risk").value,reset_hour:+$("hour").value,tz:$("tz").value.trim(),
  oanda:{key:$("okey").value.trim(),account:$("oacc").value.trim(),live:$("live").value=="1",pairs:[...document.querySelectorAll("#pairs input:checked")].map(x=>x.value)},
  paradex:{address:$("padd").value.trim(),key:$("pkey").value.trim()}};
 const r=await fetch("/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(r=>r.json()).catch(()=>({error:"Could not reach the setup program."}));
 if(r.error){$("err").textContent=r.error;$("err").style.display="block";$("go").textContent="SAVE & START";return}
 $("go").textContent="STARTING...";
 for(let i=0;i<60;i++){await new Promise(s=>setTimeout(s,1000));
  try{const t=await fetch("/").then(x=>x.text());if(!t.includes('id="setupform"')){location.href="/?t="+r.token;return}}catch(_){}}
 $("err").textContent="Start failed. Close this window and run the program again.";$("err").style.display="block"};
</script></body></html>"""


def free_port(start=8000):
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def validate(d):
    """Return an error string, or '' if the settings look usable."""
    if not (0 < d.get("risk_pct", 0) <= 5):
        return "Risk must be between 0.1 and 5 percent."
    if not (0 <= d.get("reset_hour", -1) <= 23):
        return "Lock hour must be 0 to 23."
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(d.get("tz", ""))
    except Exception:
        return "Timezone not recognised. Use a name like Asia/Manila or Europe/London."
    if d["broker"] == "oanda":
        o = d["oanda"]
        if not o["key"] or not o["account"] or not o["pairs"]:
            return "Fill in the OANDA token, account ID and pick at least one pair."
        base = "https://api-fxtrade.oanda.com" if o["live"] else "https://api-fxpractice.oanda.com"
        try:
            r = requests.get(f"{base}/v3/accounts/{o['account']}/summary", headers={"Authorization": f"Bearer {o['key']}"}, timeout=10)
        except Exception as e:
            return f"Could not reach OANDA: {e}"
        if r.status_code != 200:
            return "OANDA rejected these details. Check the token, the account ID, and that Demo/Live matches the account."
        if r.json()["account"].get("currency") != "USD":
            return "This version needs a USD-denominated OANDA account."
    else:
        if not d["paradex"]["address"] or not d["paradex"]["key"]:
            return "Fill in the Paradex address and subkey private key."
    return ""


def run_setup(port, err=""):
    done, out = threading.Event(), {}
    app = FastAPI()

    @app.get("/")
    def page():
        return HTMLResponse(SETUP_HTML.replace("__ERR__", err))

    @app.post("/save")
    async def save(request: Request):
        d = await request.json()
        msg = validate(d)
        if msg:
            return JSONResponse({"error": msg})
        d["token"] = secrets.token_urlsafe(16)
        d["port"] = port
        DATA.mkdir(exist_ok=True)
        CFG.write_text(json.dumps(d, indent=2))
        try:
            os.chmod(CFG, stat.S_IRUSR | stat.S_IWUSR)   # keys readable by this user only
        except Exception:
            pass
        out["cfg"] = d
        threading.Timer(0.5, done.set).start()
        return {"ok": True, "token": d["token"]}

    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    done.wait()
    srv.should_exit = True
    t.join(5)
    return out["cfg"]


def load_app(c):
    e = os.environ
    e.update(ACCESS_TOKEN=c["token"], RISK_PCT=str(c["risk_pct"]), LOCK_TZ=c["tz"],
             LOCK_RESET_HOUR=str(c["reset_hour"]), LOCK_FILE=str(DATA / "lock.json"))
    if c["broker"] == "oanda":
        o = c["oanda"]
        e.update(OANDA_API_KEY=o["key"], OANDA_ACCOUNT_ID=o["account"], OANDA_LIVE="1" if o["live"] else "0",
                 OANDA_INSTRUMENTS=",".join(o["pairs"]))
        name = "server_oanda"
    else:
        e.update(L2_PRIVATE_KEY=c["paradex"]["key"], L2_ADDRESS=c["paradex"]["address"])
        name = "server"
    sys.modules.pop(name, None)
    sys.modules.pop("daylock", None)   # re-read timezone / reset hour if setup was retried
    return importlib.import_module(name).app


def backend(port):
    """Runs in the background: setup wizard if needed, then the trader server."""
    cfg = None
    if CFG.exists() and "--setup" not in sys.argv:
        try:
            cfg = json.loads(CFG.read_text())
        except Exception:
            cfg = None
    err = ""
    while True:
        if cfg is None:
            cfg = run_setup(port, err)
        try:
            app = load_app(cfg)
            break
        except BaseException as ex:   # wrong keys etc.: show the setup page again with the reason
            err, cfg = f"Could not start with those settings: {ex}", None
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)


def show_window(url):
    """Own app window (Edge WebView2). Falls back to the normal browser if that is unavailable."""
    t0 = time.time()
    try:
        import webview
        webview.create_window("Day Trade Risk Manager", url, width=440, height=880, min_size=(360, 600), background_color="#000000")
        webview.start()
        if time.time() - t0 > 3:   # the window was really shown and then closed by the user
            os._exit(0)
    except Exception:
        pass
    webbrowser.open(url)
    threading.Event().wait()


def main():
    DATA.mkdir(exist_ok=True)
    if sys.stdout is None or sys.stderr is None:   # windowed build has no console: log to a file instead
        sys.stdout = sys.stderr = open(DATA / "app.log", "a", buffering=1, encoding="utf-8")
    port = free_port()
    token = None
    if CFG.exists() and "--setup" not in sys.argv:
        try:
            token = json.loads(CFG.read_text()).get("token")
        except Exception:
            token = None
    threading.Thread(target=backend, args=(port,), daemon=True).start()
    for _ in range(180):   # wait (up to 90 s) until the setup page or the trader is answering
        with socket.socket() as sk:
            if sk.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.5)
    show_window(f"http://127.0.0.1:{port}/" + (f"?t={token}" if token else ""))
    os._exit(0)


if __name__ == "__main__":
    main()
