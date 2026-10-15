#!/usr/bin/env python3
"""Synthèse quotidienne - Association Pour le Sourire de Louise.
Lit les fichiers b-*.json du Dropbox, résume les ventes des dernières heures
et les stocks bas (<=3), puis envoie un mail aux administrateurs."""
import os, sys, json, base64, time, smtplib, ssl, urllib.request, urllib.parse
from email.message import EmailMessage
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOW, DAY = 3, 86400
HOURS = float(os.environ.get("WINDOW_HOURS", "24"))
TZ = ZoneInfo("Europe/Paris")

def post(url, data=None, headers=None, raw=None):
    req = urllib.request.Request(url, data=raw if raw is not None else urllib.parse.urlencode(data or {}).encode(), headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def dropbox_token(code):
    c = json.loads(base64.b64decode(code.strip()[4:]).decode("utf-8"))
    j = json.loads(post("https://api.dropboxapi.com/oauth2/token",
        {"grant_type": "refresh_token", "refresh_token": c["r"], "client_id": c["k"]}))
    return j["access_token"]

def files(tk):
    H = {"Authorization": "Bearer " + tk, "Content-Type": "application/json"}
    j = json.loads(post("https://api.dropboxapi.com/2/files/list_folder", headers=H, raw=json.dumps({"path": ""}).encode()))
    out = []
    for e in j["entries"]:
        n = e["name"]
        if n.startswith("b-") and n.endswith(".json"):
            d = post("https://content.dropboxapi.com/2/files/download",
                     headers={"Authorization": "Bearer " + tk, "Dropbox-API-Arg": json.dumps({"path": e["path_lower"]})}, raw=b"")
            out.append(json.loads(d.decode("utf-8")))
    return out

def low_stock(D, now_ms):
    res = []
    for i in D.get("items") or []:
        ad = [a for a in D.get("adds") or [] if a.get("item") == i["id"]]
        add = sum(a["q"] for a in ad if a["q"] > 0)
        ret = -sum(a["q"] for a in ad if a["q"] < 0)
        ss = [x for x in D.get("sales") or [] if x.get("item") == i["id"] and not x.get("pend")]
        st = add - ret - sum(x["d"] for x in ss)
        if add <= 0 or st > LOW:
            continue
        rc = [x for x in ss if x["t"] > now_ms - 60 * DAY * 1000]
        sold = sum(x["d"] for x in rc)
        days = None
        if sold > 0:
            first = min(x["t"] for x in rc)
            win = min(60, max(7, (now_ms - first) / (DAY * 1000)))
            days = max(st, 0) / (sold / win)
        res.append((i["name"], max(st, 0), 0 if st <= 0 else days))
    return sorted(res, key=lambda a: 1e9 if a[2] is None else a[2])

def rem(days, st):
    if st <= 0: return "rupture de stock"
    if days is None: return "aucune vente récente"
    if days < 1: return "moins d'un jour de vente"
    if days < 14:
        n = max(1, round(days)); return f"{n} jour{'s' if n > 1 else ''} de vente"
    if days < 60:
        n = round(days / 7); return f"{n} semaine{'s' if n > 1 else ''} de vente"
    n = round(days / 30); return f"{n} mois de vente"

def eur(x): return f"{x:.2f} €".replace(".", ",")

def build(data, now_ms):
    since = now_ms - HOURS * 3600 * 1000
    names = {}
    lines, alerts, tot, qty = [], [], 0.0, 0
    for D in data:
        for i in D.get("items") or []: names[i["id"]] = i["name"]
    for D in sorted(data, key=lambda d: str(d.get("v") or "")):
        who = D.get("v") or "?"
        its = {i["id"]: i["name"] for i in D.get("items") or []}
        new = [x for x in D.get("sales") or [] if x.get("t", 0) > since]
        ok = [x for x in new if not x.get("pend")]
        pend = [x for x in new if x.get("pend")]
        if ok or pend:
            agg = {}
            for x in ok:
                a = agg.setdefault(its.get(x["item"], names.get(x["item"], "?")), [0, 0.0]); a[0] += x["d"]; a[1] += x["d"] * x.get("p", 0)
            q = sum(a[0] for a in agg.values()); m = sum(a[1] for a in agg.values())
            tot += m; qty += q
            det = ", ".join(f"{n} ×{a[0]}" for n, a in agg.items() if a[0])
            s = f"• {who} : {q} article(s), {eur(m)}" + (f" ({det})" if det else "")
            if pend: s += f" + {sum(x['d'] for x in pend)} avance(s) à régler"
            lines.append(s)
        for n, st, d in low_stock(D, now_ms):
            alerts.append(f"• {who} – {n} : {st} restant(s), {rem(d, st)}")
    h = int(HOURS) if HOURS == int(HOURS) else HOURS
    b = [f"Synthèse des {h} dernières heures – {datetime.fromtimestamp(now_ms/1000, TZ):%d/%m/%Y %H:%M}", ""]
    if lines:
        b += [f"VENTES : {qty} article(s), {eur(tot)}"] + lines
    else:
        b += ["VENTES : aucune vente enregistrée."]
    b += [""]
    b += (["⚠️ STOCKS BAS (à réapprovisionner)"] + alerts) if alerts else ["Aucun stock bas."]
    b += ["", "Message automatique – Pour le Sourire de Louise."]
    subj = f"Ventes : {qty} article(s), {eur(tot)}" + (f" – {len(alerts)} stock(s) bas" if alerts else "") + " – Pour le Sourire de Louise"
    return subj, "\n".join(b), bool(lines or alerts)

def send(subj, body):
    m = EmailMessage(); m["Subject"] = subj; m["From"] = os.environ["SMTP_USER"]
    m["To"] = os.environ["MAIL_TO"]; m.set_content(body)
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465, context=ssl.create_default_context()) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)

if __name__ == "__main__":
    now = int(time.time() * 1000)
    data = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else files(dropbox_token(os.environ["ASL_CODE"]))
    subj, body, news = build(data, now)
    print(subj + "\n\n" + body)
    if os.environ.get("DRY_RUN"): sys.exit(0)
    if news or os.environ.get("ALWAYS_SEND", "1") == "1":
        send(subj, body)
