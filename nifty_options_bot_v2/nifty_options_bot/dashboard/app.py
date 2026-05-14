"""
app.py  —  Enhanced Nifty Options Bot Dashboard v2
===================================================
Tabs:
  1. Overview      — KPI strip, equity curve with markers, session PnL
  2. Risk & Greeks — Greeks limits, margin, IV rank, delta drift, breakevens
  3. Analytics     — Win rate, expectancy, profit factor, distributions, edge analysis
  4. Positions     — Leg-wise analytics, slippage, order health, latency
  5. Strategy      — Regime box, config snapshot, backtest vs live

Run standalone:  python dashboard/app.py
"""

from __future__ import annotations
import sys, os, random, math
from datetime import datetime, timedelta
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objects as go

from utils.logger import get_logger
logger = get_logger("dashboard_v2")

# ─── Global bot-state bridge ──────────────────────────────────────────────────
_bot_state: Dict[str, Any] = {
    "strategy_engine": None, "order_manager": None,
    "risk_engine": None,     "data_feed": None,
}

def inject_bot_engines(strategy_engine=None, order_manager=None,
                       risk_engine=None, data_feed=None):
    _bot_state.update(dict(strategy_engine=strategy_engine,
                           order_manager=order_manager,
                           risk_engine=risk_engine, data_feed=data_feed))

# ─── Demo data generators ─────────────────────────────────────────────────────
random.seed(42)

def _make_equity_curve(n=120):
    curve, pnl, events = [], 0.0, []
    base_time = datetime.now() - timedelta(minutes=n)
    for i in range(n):
        pnl += random.gauss(30, 180)
        t = base_time + timedelta(minutes=i)
        curve.append({"time": t, "pnl": round(pnl, 2)})
        if i == 10:  events.append({"time": t, "type": "ENTRY",  "pnl": pnl, "label": "Straddle Entry"})
        if i == 45:  events.append({"time": t, "type": "SL_HIT", "pnl": pnl, "label": "PE SL Hit"})
        if i == 70:  events.append({"time": t, "type": "HEDGE",  "pnl": pnl, "label": "Hedge Added"})
        if i == 100: events.append({"time": t, "type": "EXIT",   "pnl": pnl, "label": "Target Exit"})
    return curve, events

def _make_trades(n=28):
    trades = []
    for i in range(n):
        entry = random.uniform(80, 220)
        exit_ = entry + random.gauss(15, 60)
        pnl   = (entry - exit_) * 50
        trades.append({
            "id": f"T{i+1:03d}",
            "symbol": random.choice(["NIFTY25JAN24500CE","NIFTY25JAN24500PE",
                                      "NIFTY25JAN24400PE","NIFTY25JAN24600CE"]),
            "side": "SELL",
            "entry": round(entry,1), "exit": round(exit_,1),
            "pnl": round(pnl,0), "lots": 1,
            "hour": random.randint(9,15),
            "dow": random.choice(["Mon","Tue","Wed","Thu","Fri"]),
            "strike_dist": random.choice([0,1,2,3]),
            "slippage": round(random.uniform(0.2, 2.5), 2),
        })
    return trades

def _make_spot(n=120):
    spots, s = [], 24500.0
    base = datetime.now() - timedelta(minutes=n)
    for i in range(n):
        s += random.gauss(0, 12)
        spots.append({"time": base + timedelta(minutes=i), "spot": round(s,2)})
    return spots

_EQUITY_CURVE, _EVENTS = _make_equity_curve()
_TRADES = _make_trades()
_SPOT   = _make_spot()
_SEED   = {"pnl": _EQUITY_CURVE[-1]["pnl"], "spot": _SPOT[-1]["spot"],
           "iv": 14.8, "delta": 0.06, "latency": 42}

def _tick():
    _SEED["pnl"]     += random.gauss(25, 150)
    _SEED["spot"]    += random.gauss(0, 8)
    _SEED["iv"]      = max(8, min(50, _SEED["iv"] + random.gauss(0, 0.05)))
    _SEED["delta"]   += random.gauss(0, 0.003)
    _SEED["latency"]  = max(8, _SEED["latency"] + random.gauss(0, 3))
    now = datetime.now()
    _EQUITY_CURVE.append({"time": now, "pnl": round(_SEED["pnl"],2)})
    _SPOT.append({"time": now, "spot": round(_SEED["spot"],2)})
    if len(_EQUITY_CURVE) > 300: _EQUITY_CURVE.pop(0)
    if len(_SPOT) > 300:         _SPOT.pop(0)

def _compute_stats(trades):
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    loses  = [t["pnl"] for t in trades if t["pnl"] <= 0]
    total  = len(trades)
    win_rate      = len(wins)/total*100 if total else 0
    avg_win       = sum(wins)/len(wins)   if wins  else 0
    avg_loss      = sum(loses)/len(loses) if loses else 0
    gross_profit  = sum(wins)
    gross_loss    = abs(sum(loses)) or 1
    profit_factor = gross_profit/gross_loss
    expectancy    = (win_rate/100*avg_win) + ((1-win_rate/100)*avg_loss)
    eq = [d["pnl"] for d in _EQUITY_CURVE]
    peak, mdd = eq[0], 0
    for v in eq:
        peak = max(peak, v)
        mdd  = min(mdd, v - peak)
    return dict(win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
                profit_factor=profit_factor, expectancy=expectancy,
                total_trades=total, max_drawdown=mdd,
                gross_profit=gross_profit, gross_loss=-gross_loss)

# ─── Color system ─────────────────────────────────────────────────────────────
C = {
    "bg":      "#080c10",
    "surface": "#0e1318",
    "card":    "#131920",
    "card2":   "#181f28",
    "border":  "#1e2832",
    "border2": "#253040",
    "text":    "#d4dce8",
    "muted":   "#5a6a7e",
    "green":   "#00d084",
    "red":     "#ff4560",
    "amber":   "#ffb400",
    "blue":    "#2196f3",
    "cyan":    "#00bcd4",
    "purple":  "#9c6fde",
    "orange":  "#ff7043",
}
FM = "'IBM Plex Mono','Fira Code',monospace"
FD = "'Syne','Space Grotesk',sans-serif"

# ─── App ──────────────────────────────────────────────────────────────────────
app = dash.Dash(__name__, title="NOB · Console", update_title=None,
                meta_tags=[{"name":"viewport","content":"width=device-width"}])

app.index_string = f"""<!DOCTYPE html>
<html><head>
{{%metas%}}<title>{{%title%}}</title>{{%favicon%}}{{%css%}}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=Syne:wght@600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:{C['bg']};font-family:{FM};color:{C['text']};font-size:12px;overflow-x:hidden}}
::-webkit-scrollbar{{width:4px;height:4px}}
::-webkit-scrollbar-track{{background:{C['bg']}}}
::-webkit-scrollbar-thumb{{background:{C['border2']};border-radius:2px}}
.card{{background:{C['card']};border:1px solid {C['border']};border-radius:6px;padding:14px;animation:fadeUp .25s ease both}}
.card2{{background:{C['card2']};border:1px solid {C['border']};border-radius:4px;padding:10px}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
.stitle{{font-size:9px;color:{C['muted']};text-transform:uppercase;letter-spacing:2px;margin-bottom:10px;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:11px}}
th{{color:{C['muted']};font-weight:500;font-size:9px;text-transform:uppercase;letter-spacing:1px;padding:6px 8px;border-bottom:1px solid {C['border']};text-align:left;white-space:nowrap}}
td{{padding:6px 8px;border-bottom:1px solid {C['border']};vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:{C['card2']}}}
.badge{{display:inline-block;padding:1px 7px;border-radius:3px;font-size:9px;font-weight:600;letter-spacing:.5px}}
.b-sell{{background:rgba(255,69,96,.12);color:{C['red']};border:1px solid rgba(255,69,96,.25)}}
.b-buy{{background:rgba(0,208,132,.12);color:{C['green']};border:1px solid rgba(0,208,132,.25)}}
.b-ok{{background:rgba(0,208,132,.12);color:{C['green']};border:1px solid rgba(0,208,132,.25)}}
.b-warn{{background:rgba(255,180,0,.12);color:{C['amber']};border:1px solid rgba(255,180,0,.25)}}
.b-info{{background:rgba(33,150,243,.12);color:{C['blue']};border:1px solid rgba(33,150,243,.25)}}
.led{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px}}
.led-ok{{background:{C['green']};box-shadow:0 0 5px {C['green']};animation:blink 2s infinite}}
.led-err{{background:{C['red']};box-shadow:0 0 5px {C['red']}}}
.led-warn{{background:{C['amber']};box-shadow:0 0 5px {C['amber']};animation:blink 1s infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
.prog-bg{{height:4px;background:{C['border2']};border-radius:2px;overflow:hidden;margin-top:5px}}
.tab-bar{{display:flex;gap:0;border-bottom:1px solid {C['border']};padding:0 24px;background:{C['surface']}}}
.tab{{padding:10px 20px;font-size:9px;text-transform:uppercase;letter-spacing:1.5px;cursor:pointer;color:{C['muted']};border-bottom:2px solid transparent;transition:all .2s;font-weight:600}}
.tab:hover{{color:{C['text']}}}
.tab-active{{color:{C['cyan']};border-bottom:2px solid {C['cyan']}}}
.mrow{{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid {C['border']}}}
.mrow:last-child{{border-bottom:none}}
.mkey{{font-size:10px;color:{C['muted']}}}
.mval{{font-size:11px;font-weight:500}}
.regime-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}}
.regime-item{{background:{C['card2']};border:1px solid {C['border']};border-radius:4px;padding:8px 10px}}
.regime-key{{font-size:9px;color:{C['muted']};text-transform:uppercase;letter-spacing:1px}}
.regime-val{{font-size:12px;font-weight:600;margin-top:3px}}
.kpi-strip{{display:flex;overflow-x:auto;gap:0;padding:10px 24px;background:{C['surface']};border-bottom:1px solid {C['border']}}}
.kpi-item{{flex-shrink:0;padding:0 20px;border-right:1px solid {C['border']};min-width:110px}}
.kpi-lbl{{font-size:9px;color:{C['muted']};text-transform:uppercase;letter-spacing:1.5px}}
.kpi-val{{font-size:20px;font-weight:700;letter-spacing:-0.5px;font-family:{FD};margin-top:2px}}
.kpi-sub{{font-size:9px;color:{C['muted']};margin-top:1px}}
.halt-banner{{background:rgba(255,69,96,.08);border:1px solid {C['red']};border-radius:4px;padding:8px 16px;color:{C['red']};font-weight:600;font-size:11px;margin:10px 24px;display:flex;align-items:center;gap:8px}}
.stat-box{{background:{C['card2']};border:1px solid {C['border']};border-radius:4px;padding:10px 14px}}
.stat-lbl{{font-size:9px;color:{C['muted']};text-transform:uppercase;letter-spacing:1px}}
.stat-val{{font-size:18px;font-weight:700;font-family:{FD};margin-top:3px}}
</style>
</head><body>{{%app_entry%}}
<footer>{{%config%}}{{%scripts%}}{{%renderer%}}</footer></body></html>"""

# ─── Layout ───────────────────────────────────────────────────────────────────
app.layout = html.Div([
    dcc.Interval(id="tick", interval=5000, n_intervals=0),
    dcc.Store(id="active-tab", data="overview"),

    # Top bar
    html.Div([
        html.Div([
            html.Span("⬡ ", style={"color":C["cyan"],"fontSize":"15px"}),
            html.Span("NIFTY OPTIONS BOT", style={"fontFamily":FD,"fontSize":"14px",
                "fontWeight":"800","letterSpacing":"2px"}),
            html.Span(" v2 · CONSOLE", style={"fontSize":"9px","color":C["muted"],"marginLeft":"8px"}),
        ], style={"display":"flex","alignItems":"center"}),
        html.Div([
            html.Div(id="led-strip", style={"display":"flex","gap":"14px","alignItems":"center"}),
            html.Div(id="clock-v2", style={"fontSize":"10px","color":C["cyan"],"marginLeft":"18px",
                "fontWeight":"600","borderLeft":f"1px solid {C['border']}","paddingLeft":"18px"}),
        ], style={"display":"flex","alignItems":"center"}),
    ], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
              "padding":"11px 24px","background":C["card"],"borderBottom":f"1px solid {C['border']}",
              "position":"sticky","top":"0","zIndex":"200"}),

    html.Div(id="halt-v2", style={"display":"none"}),
    html.Div(id="kpi-strip", className="kpi-strip"),

    # Tab bar
    html.Div([
        html.Div("Overview",      id="tab-overview",   className="tab tab-active"),
        html.Div("Risk & Greeks", id="tab-risk",       className="tab"),
        html.Div("Analytics",     id="tab-analytics",  className="tab"),
        html.Div("Positions",     id="tab-positions",  className="tab"),
        html.Div("Strategy",      id="tab-strategy",   className="tab"),
    ], className="tab-bar"),

    html.Div(id="tab-content", style={"padding":"16px 24px 28px"}),
])

# Tab switching
app.clientside_callback(
    """function(n1,n2,n3,n4,n5,stored){
        const ctx=dash_clientside.callback_context;
        if(!ctx.triggered.length) return[stored,'tab tab-active','tab','tab','tab','tab'];
        const tid=ctx.triggered[0].prop_id.split('.')[0].replace('tab-','');
        const tabs=['overview','risk','analytics','positions','strategy'];
        return[tid,...tabs.map(t=>t===tid?'tab tab-active':'tab')];
    }""",
    Output("active-tab","data"),
    Output("tab-overview","className"), Output("tab-risk","className"),
    Output("tab-analytics","className"), Output("tab-positions","className"),
    Output("tab-strategy","className"),
    Input("tab-overview","n_clicks"), Input("tab-risk","n_clicks"),
    Input("tab-analytics","n_clicks"), Input("tab-positions","n_clicks"),
    Input("tab-strategy","n_clicks"),
    State("active-tab","data"), prevent_initial_call=True,
)

# ─── Main callback ────────────────────────────────────────────────────────────
@app.callback(
    Output("clock-v2","children"), Output("led-strip","children"),
    Output("halt-v2","children"),  Output("halt-v2","style"),
    Output("kpi-strip","children"), Output("tab-content","children"),
    Input("tick","n_intervals"), Input("active-tab","data"),
)
def refresh(n, tab):

    # Pull from live engines if wired, else use demo data
    data_feed       = _bot_state.get("data_feed")
    order_manager   = _bot_state.get("order_manager")
    risk_engine     = _bot_state.get("risk_engine")
    strategy_engine = _bot_state.get("strategy_engine")
    live_mode       = data_feed is not None
    now             = datetime.now()

    realised = unrealised = 0.0

    if live_mode:
        # LIVE MODE: pull real Nifty spot from Breeze
        try:
            live_spot = data_feed.get_spot_price()
            if live_spot and live_spot > 1000:   # sanity check
                _SEED["spot"] = live_spot
        except Exception:
            pass
        if order_manager:
            positions  = order_manager.get_all_positions()
            realised   = sum(p.realised_pnl   for p in positions)
            unrealised = sum(p.unrealised_pnl  for p in positions)
        _SEED["pnl"] = realised + unrealised
        _EQUITY_CURVE.append({"time": now, "pnl": round(_SEED["pnl"], 2)})
        _SPOT.append({"time": now, "spot": round(_SEED["spot"], 2)})
        if len(_EQUITY_CURVE) > 300: _EQUITY_CURVE.pop(0)
        if len(_SPOT) > 300:         _SPOT.pop(0)
        strat_st = strategy_engine.status if strategy_engine else "ACTIVE"
        mode_lbl = "LIVE DATA | PAPER ORDERS"
    else:
        # DEMO MODE: simulate
        _tick()
        strat_st = "DEMO"
        mode_lbl = "DEMO (simulated)"

    stats   = _compute_stats(_TRADES)
    cur_pnl = _SEED["pnl"]
    spot    = _SEED["spot"]
    iv      = _SEED["iv"]
    ivr     = _iv_rank()

    clock = now.strftime("%H:%M:%S  |  %d %b %Y")

    def led(lbl, ok, warn=False):
        cls = "led led-ok" if ok else ("led led-warn" if warn else "led led-err")
        col = C["green"] if ok else (C["amber"] if warn else C["red"])
        return html.Div([html.Span(className=cls),
                         html.Span(lbl, style={"color":col,"fontSize":"9px"})],
                        style={"display":"flex","alignItems":"center"})

    leds = [
        led("API",          live_mode, not live_mode),
        led("ORDER ENGINE", live_mode, not live_mode),
        led("STRATEGY",     live_mode, not live_mode),
        led("DATA FEED",    live_mode, not live_mode),
        led("WS",           live_mode, not live_mode),
        html.Span(f"tick {now.strftime('%H:%M:%S')}",
                  style={"fontSize":"9px","color":C["muted"]}),
    ]

    is_halted  = risk_engine.is_halted if risk_engine else False
    halt_el    = html.Div(["BOT HALTED - All positions squared off"], className="halt-banner")
    halt_style = {"display":"block"} if is_halted else {"display":"none"}

    pnl_col  = C["green"] if cur_pnl >= 0 else C["red"]
    mae      = min(d["pnl"] for d in _EQUITY_CURVE)
    open_risk= abs(_SEED["delta"]) * spot * 50

    def kpi(lbl, val, col, sub=""):
        return html.Div([
            html.Div(lbl, className="kpi-lbl"),
            html.Div(val, className="kpi-val", style={"color":col}),
            html.Div(sub, className="kpi-sub") if sub else html.Span(),
        ], className="kpi-item")

    spot_col = C["green"] if live_mode else C["blue"]
    spot_sub = "LIVE" if live_mode else "demo"

    kpis = [
        kpi("Net PnL",    f"Rs{cur_pnl:+,.0f}",   pnl_col),
        kpi("Unrealised", f"Rs{unrealised:+,.0f}",  pnl_col),
        kpi("Realised",   f"Rs{realised:+,.0f}",   C["muted"], "today"),
        kpi("Open Risk",  f"Rs{open_risk:,.0f}",   C["amber"]),
        kpi("Max AE",     f"Rs{mae:,.0f}",          C["red"]),
        kpi("Win Rate",   f"{stats['win_rate']:.1f}%",
            C["green"] if stats["win_rate"]>50 else C["red"]),
        kpi("Expectancy", f"Rs{stats['expectancy']:+,.0f}",
            C["green"] if stats["expectancy"]>0 else C["red"]),
        kpi("IV Rank",    f"{ivr:.0f}%",            C["cyan"], "52-wk"),
        kpi("NIFTY SPOT", f"Rs{spot:,.1f}",         spot_col, spot_sub),
        kpi("Mode",       "ATM STRADDLE",            C["cyan"], mode_lbl),
    ]

    content = _render_tab(tab, stats, cur_pnl, spot, iv, ivr)
    return clock, leds, halt_el, halt_style, kpis, content


# ─── Tab renderers ────────────────────────────────────────────────────────────
def _render_tab(tab, stats, cur_pnl, spot, iv, ivr):
    if tab == "overview":  return _tab_overview(stats, cur_pnl, spot)
    if tab == "risk":      return _tab_risk(spot, iv, ivr)
    if tab == "analytics": return _tab_analytics(stats)
    if tab == "positions": return _tab_positions()
    if tab == "strategy":  return _tab_strategy(stats, iv, ivr)
    return _tab_overview(stats, cur_pnl, spot)


# ══ TAB 1 — OVERVIEW ══════════════════════════════════════════════════════════
def _tab_overview(stats, cur_pnl, spot):
    times  = [d["time"] for d in _EQUITY_CURVE]
    values = [d["pnl"]  for d in _EQUITY_CURVE]
    last   = values[-1]

    # Equity curve
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=times, y=values, mode="lines",
        line=dict(color=C["green"] if last>=0 else C["red"], width=2),
        fill="tozeroy",
        fillcolor="rgba(0,208,132,0.06)" if last>=0 else "rgba(255,69,96,0.06)",
        hovertemplate="₹%{y:,.0f}  %{x|%H:%M}<extra></extra>", name="PnL",
    ))
    fig.add_hline(y=0, line_color=C["border2"], line_width=1)
    # Session open marker
    # Session open marker drawn as a scatter point instead of vline
    # (add_vline has compatibility issues with datetime x-axis in this plotly version)

    # Event markers
    MC = {"ENTRY":C["blue"],"EXIT":C["green"],"SL_HIT":C["red"],"HEDGE":C["amber"]}
    MS = {"ENTRY":"triangle-up","EXIT":"circle","SL_HIT":"x","HEDGE":"diamond"}
    for et in set(e["type"] for e in _EVENTS):
        evs = [e for e in _EVENTS if e["type"]==et]
        fig.add_trace(go.Scatter(
            x=[e["time"] for e in evs], y=[e["pnl"] for e in evs],
            mode="markers+text", name=et,
            marker=dict(size=10, color=MC.get(et,C["cyan"]),
                        symbol=MS.get(et,"circle"),
                        line=dict(width=1.5, color=C["bg"])),
            text=[e["label"] for e in evs], textposition="top center",
            textfont=dict(size=9, color=MC.get(et,C["cyan"])),
            hovertemplate="%{text}<br>₹%{y:,.0f}<extra></extra>",
        ))
    fig.update_layout(**_cb(), height=260,
                      legend=dict(orientation="h", y=1.08, x=0, font=dict(size=9),
                                  bgcolor="rgba(0,0,0,0)"))

    # Spot with breakeven
    st = [d["time"] for d in _SPOT[-100:]]
    sv = [d["spot"] for d in _SPOT[-100:]]
    atm = 24500; be = 380
    sfig = go.Figure()
    sfig.add_hrect(y0=atm-be, y1=atm+be, fillcolor="rgba(0,208,132,0.04)",
                   line=dict(color=C["green"], width=0.5, dash="dash"),
                   annotation_text="Profit Zone", annotation_position="top left",
                   annotation_font_size=8, annotation_font_color=C["green"])
    sfig.add_trace(go.Scatter(x=st, y=sv, mode="lines",
                               line=dict(color=C["blue"],width=1.5),
                               hovertemplate="₹%{y:,.2f}<extra></extra>"))
    sfig.add_hline(y=atm+be, line_color=C["red"],   line_dash="dot", line_width=1)
    sfig.add_hline(y=atm-be, line_color=C["red"],   line_dash="dot", line_width=1)
    sfig.add_hline(y=atm,    line_color=C["amber"],  line_dash="dash", line_width=1,
                   annotation_text=f"ATM {atm}", annotation_font_size=8,
                   annotation_font_color=C["amber"])
    sfig.update_layout(**_cb(), height=180, showlegend=False)

    # Stat boxes
    def sbox(lbl, val, col):
        return html.Div([
            html.Div(lbl, className="stat-lbl"),
            html.Div(val, className="stat-val", style={"color":col}),
        ], className="stat-box")

    stat_row = html.Div([
        sbox("Win Rate",      f"{stats['win_rate']:.1f}%",
             C["green"] if stats["win_rate"]>50 else C["red"]),
        sbox("Avg Win",       f"₹{stats['avg_win']:+,.0f}", C["green"]),
        sbox("Avg Loss",      f"₹{stats['avg_loss']:+,.0f}", C["red"]),
        sbox("Profit Factor", f"{stats['profit_factor']:.2f}",
             C["green"] if stats["profit_factor"]>1 else C["red"]),
        sbox("Expectancy",    f"₹{stats['expectancy']:+,.0f}",
             C["green"] if stats["expectancy"]>0 else C["red"]),
        sbox("Max Drawdown",  f"₹{stats['max_drawdown']:,.0f}", C["red"]),
        sbox("Trades",        str(stats["total_trades"]), C["cyan"]),
    ], style={"display":"grid","gridTemplateColumns":"repeat(7,1fr)",
              "gap":"8px","marginBottom":"16px"})

    return html.Div([
        stat_row,
        html.Div([html.Div("Rolling Equity Curve — with Entry/Exit/SL Markers", className="stitle"),
                  dcc.Graph(figure=fig, config={"displayModeBar":False})],
                 className="card", style={"marginBottom":"14px"}),
        html.Div([html.Div("Nifty Spot + Breakeven Zones", className="stitle"),
                  dcc.Graph(figure=sfig, config={"displayModeBar":False})],
                 className="card"),
    ])


# ══ TAB 2 — RISK & GREEKS ═════════════════════════════════════════════════════
def _tab_risk(spot, iv, ivr):
    delta = _SEED["delta"]
    dte   = 3
    exp_move = spot * (iv/100) * math.sqrt(dte/252)

    # Delta drift
    dh = [round(random.gauss(0.06, 0.02), 3) for _ in range(60)]
    dfig = go.Figure()
    dfig.add_trace(go.Scatter(y=dh, mode="lines", fill="tozeroy",
                              line=dict(color=C["amber"],width=1.5),
                              fillcolor="rgba(255,180,0,0.06)",
                              hovertemplate="Δ %{y:.3f}<extra></extra>"))
    dfig.add_hline(y=0.10,  line_color=C["red"], line_dash="dot", line_width=1,
                   annotation_text="Δ +0.10 limit", annotation_font_size=8,
                   annotation_font_color=C["red"])
    dfig.add_hline(y=-0.10, line_color=C["red"], line_dash="dot", line_width=1,
                   annotation_text="Δ -0.10 limit", annotation_font_size=8,
                   annotation_font_color=C["red"])
    dfig.update_layout(**_cb(), height=170, showlegend=False, yaxis_title="Net Delta")

    # IV chart
    ivh = [round(random.gauss(14.8, 0.8), 2) for _ in range(60)]
    ifig = go.Figure()
    ifig.add_trace(go.Scatter(y=ivh, mode="lines", fill="tozeroy",
                              line=dict(color=C["cyan"],width=1.5),
                              fillcolor="rgba(0,188,212,0.06)",
                              hovertemplate="IV: %{y:.2f}%<extra></extra>"))
    ifig.update_layout(**_cb(), height=170, showlegend=False, yaxis_title="IV %")

    left = html.Div([
        html.Div([
            html.Div("Greeks & Limits", className="stitle"),
            _mr("Net Delta",     f"{delta:+.3f}",
                C["red"] if abs(delta)>0.08 else C["text"]),
            _mr("Delta Limit",   "± 0.10", C["muted"]),
            _mr("Net Gamma",     "0.0012",  C["text"]),
            _mr("Net Theta",     "₹-25.6/d",C["green"]),
            _mr("Net Vega",      "₹-18.4/1%",C["text"]),
            _mr("IV (avg)",      f"{iv:.2f}%", C["cyan"]),
            _mr("IV Rank",       f"{ivr:.0f}%",
                C["green"] if ivr<30 else (C["amber"] if ivr<70 else C["red"])),
            _mr("IV Percentile", f"{ivr+3:.0f}%", C["text"]),
            _mr("IV Δ Entry",    f"{iv-14.2:+.2f}%",
                C["red"] if iv>14.2 else C["green"]),
        ], className="card", style={"marginBottom":"12px"}),
        html.Div([
            html.Div("Margin & Exposure", className="stitle"),
            _margin_panel(_SEED["pnl"]),
        ], className="card"),
    ], style={"flex":"1"})

    right = html.Div([
        html.Div([html.Div("Delta Drift Over Time",className="stitle"),
                  dcc.Graph(figure=dfig,config={"displayModeBar":False})],
                 className="card",style={"marginBottom":"12px"}),
        html.Div([html.Div("Implied Volatility",className="stitle"),
                  dcc.Graph(figure=ifig,config={"displayModeBar":False})],
                 className="card",style={"marginBottom":"12px"}),
        html.Div([
            html.Div("Expected Move vs Actual",className="stitle"),
            _mr("DTE",           f"{dte} days",            C["text"]),
            _mr("IV",            f"{iv:.2f}%",              C["cyan"]),
            _mr("Expected Move", f"±₹{exp_move:.0f} ({exp_move/spot*100:.2f}%)", C["amber"]),
            _mr("Upper BE",      f"₹{spot+exp_move:,.0f}", C["red"]),
            _mr("Lower BE",      f"₹{spot-exp_move:,.0f}", C["red"]),
            _mr("Actual Move",   f"₹{abs(spot-24500):,.0f}",C["text"]),
            _mr("vs Expected",   f"{abs(spot-24500)/max(exp_move,1)*100:.0f}% of EM",
                C["green"] if abs(spot-24500)<exp_move else C["red"]),
        ], className="card"),
    ], style={"flex":"1"})

    return html.Div([left, right],
                    style={"display":"flex","gap":"14px","alignItems":"flex-start"})


# ══ TAB 3 — ANALYTICS ════════════════════════════════════════════════════════
def _tab_analytics(stats):
    pnls  = [t["pnl"] for t in _TRADES]
    slips = [t["slippage"] for t in _TRADES]

    def bar(xs, ys, xt, yt, h=170):
        f = go.Figure()
        f.add_trace(go.Bar(x=xs, y=ys,
                           marker_color=[C["green"] if v>=0 else C["red"] for v in ys],
                           marker_line_color=C["border"], marker_line_width=1,
                           hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>"))
        f.update_layout(**_cb(), height=h, showlegend=False,
                        xaxis_title=xt, yaxis_title=yt)
        return f

    hfig = go.Figure()
    hfig.add_trace(go.Histogram(x=pnls, nbinsx=14,
                                marker_color=[C["green"] if p>=0 else C["red"] for p in pnls],
                                marker_line_color=C["border"], marker_line_width=1,
                                hovertemplate="₹%{x}<br>Count:%{y}<extra></extra>"))
    hfig.update_layout(**_cb(), height=185, showlegend=False,
                       xaxis_title="PnL/Trade (₹)", yaxis_title="Count")

    hours = list(range(9,16))
    h_pnl = {h:[] for h in hours}
    for t in _TRADES: h_pnl[t["hour"]].append(t["pnl"])
    h_avg = [sum(h_pnl[h])/max(len(h_pnl[h]),1) for h in hours]

    days  = ["Mon","Tue","Wed","Thu","Fri"]
    d_pnl = {d:[] for d in days}
    for t in _TRADES: d_pnl[t["dow"]].append(t["pnl"])
    d_avg = [sum(d_pnl[d])/max(len(d_pnl[d]),1) for d in days]

    sd_pnl = {k:[] for k in range(4)}
    for t in _TRADES: sd_pnl[t["strike_dist"]].append(t["pnl"])
    sd_avg = [sum(sd_pnl[k])/max(len(sd_pnl[k]),1) for k in range(4)]

    sfig = go.Figure()
    sfig.add_trace(go.Histogram(x=slips, nbinsx=10,
                                marker_color=C["amber"],
                                marker_line_color=C["border"], marker_line_width=1))
    sfig.update_layout(**_cb(), height=170, showlegend=False,
                       xaxis_title="Slippage (₹)", yaxis_title="Count")

    ce_pnl = [t["pnl"] for t in _TRADES if "CE" in t["symbol"]]
    pe_pnl = [t["pnl"] for t in _TRADES if "PE" in t["symbol"]]

    top = html.Div([
        html.Div([html.Div("PnL Distribution",className="stitle"),
                  dcc.Graph(figure=hfig,config={"displayModeBar":False})],
                 className="card",style={"flex":"2"}),
        html.Div([
            html.Div("Performance Summary",className="stitle"),
            _mr("Win Rate",       f"{stats['win_rate']:.1f}%",
                C["green"] if stats["win_rate"]>50 else C["red"]),
            _mr("Avg Win",        f"₹{stats['avg_win']:+,.0f}",  C["green"]),
            _mr("Avg Loss",       f"₹{stats['avg_loss']:+,.0f}", C["red"]),
            _mr("Profit Factor",  f"{stats['profit_factor']:.2f}",
                C["green"] if stats["profit_factor"]>1 else C["red"]),
            _mr("Expectancy",     f"₹{stats['expectancy']:+,.0f}",
                C["green"] if stats["expectancy"]>0 else C["red"]),
            _mr("Max Drawdown",   f"₹{stats['max_drawdown']:,.0f}", C["red"]),
            _mr("Gross Profit",   f"₹{stats['gross_profit']:,.0f}", C["green"]),
            _mr("Gross Loss",     f"₹{stats['gross_loss']:,.0f}",   C["red"]),
            _mr("Total Trades",   str(stats["total_trades"]),         C["text"]),
            html.Div(style={"height":"10px"}),
            html.Div("Leg Analytics",className="stitle"),
            _mr("CE Contribution",f"₹{sum(ce_pnl):+,.0f}",
                C["green"] if sum(ce_pnl)>=0 else C["red"]),
            _mr("PE Contribution",f"₹{sum(pe_pnl):+,.0f}",
                C["green"] if sum(pe_pnl)>=0 else C["red"]),
            _mr("CE Win Rate",    f"{len([p for p in ce_pnl if p>0])/max(len(ce_pnl),1)*100:.0f}%", C["text"]),
            _mr("PE Win Rate",    f"{len([p for p in pe_pnl if p>0])/max(len(pe_pnl),1)*100:.0f}%", C["text"]),
            _mr("Theta Captured", "₹1,286 est.", C["green"]),
        ], className="card",style={"flex":"1"}),
    ], style={"display":"flex","gap":"14px","marginBottom":"14px"})

    bottom = html.Div([
        html.Div([html.Div("Edge by Hour",className="stitle"),
                  dcc.Graph(figure=bar([f"{h}:00" for h in hours],h_avg,"Hour","Avg ₹"),
                            config={"displayModeBar":False})],
                 className="card",style={"flex":"1"}),
        html.Div([html.Div("Edge by Day",className="stitle"),
                  dcc.Graph(figure=bar(days,d_avg,"Day","Avg ₹"),
                            config={"displayModeBar":False})],
                 className="card",style={"flex":"1"}),
        html.Div([html.Div("Edge by Strike Dist.",className="stitle"),
                  dcc.Graph(figure=bar(["ATM","±1","±2","±3"],sd_avg,"Strike","Avg ₹"),
                            config={"displayModeBar":False})],
                 className="card",style={"flex":"1"}),
        html.Div([html.Div("Slippage Dist.",className="stitle"),
                  dcc.Graph(figure=sfig,config={"displayModeBar":False})],
                 className="card",style={"flex":"1"}),
    ], style={"display":"flex","gap":"14px"})

    return html.Div([top, bottom])


# ══ TAB 4 — POSITIONS & EXECUTION ════════════════════════════════════════════
def _tab_positions():
    lat = _SEED["latency"]

    pos_rows = [
        html.Tr([html.Td("NIFTY25JAN24500CE",style={"fontSize":"10px"}),
                 html.Td(html.Span("SELL",className="badge b-sell")),
                 html.Td("1"),html.Td("₹185.5"),html.Td("₹162.0"),
                 html.Td("₹+1,175",style={"color":C["green"],"fontWeight":"600"}),
                 html.Td("-0.42",style={"color":C["blue"]}),
                 html.Td("14.2%",style={"color":C["cyan"]}),
                 html.Td("₹0.8",style={"color":C["amber"]}),
                 html.Td("₹-12.5/d",style={"color":C["green"]})]),
        html.Tr([html.Td("NIFTY25JAN24500PE",style={"fontSize":"10px"}),
                 html.Td(html.Span("SELL",className="badge b-sell")),
                 html.Td("1"),html.Td("₹190.0"),html.Td("₹210.5"),
                 html.Td("₹-1,025",style={"color":C["red"],"fontWeight":"600"}),
                 html.Td("+0.48",style={"color":C["blue"]}),
                 html.Td("15.8%",style={"color":C["cyan"]}),
                 html.Td("₹1.2",style={"color":C["amber"]}),
                 html.Td("₹-13.1/d",style={"color":C["green"]})]),
    ]

    ord_rows = [
        html.Tr([html.Td("ORD-001",style={"color":C["muted"],"fontSize":"9px"}),
                 html.Td("NIFTY25JAN24500CE",style={"fontSize":"10px"}),
                 html.Td(html.Span("SELL",className="badge b-sell")),
                 html.Td("50"),html.Td("₹185.5"),html.Td("MARKET"),
                 html.Td(html.Span("✓ COMPLETE",className="badge b-ok")),
                 html.Td("₹0.8",style={"color":C["amber"]})]),
        html.Tr([html.Td("ORD-002",style={"color":C["muted"],"fontSize":"9px"}),
                 html.Td("NIFTY25JAN24500PE",style={"fontSize":"10px"}),
                 html.Td(html.Span("SELL",className="badge b-sell")),
                 html.Td("50"),html.Td("₹190.0"),html.Td("MARKET"),
                 html.Td(html.Span("✓ COMPLETE",className="badge b-ok")),
                 html.Td("₹1.2",style={"color":C["amber"]})]),
    ]

    metrics = html.Div([
        html.Div([
            html.Div("Bot Health & Latency",className="stitle"),
            _mr("API Round-trip",     f"{lat:.0f} ms",
                C["green"] if lat<100 else C["amber"]),
            _mr("Order Placement",    f"{lat*0.6:.0f} ms",   C["text"]),
            _mr("Data Feed Age",      f"{lat*0.3:.0f} ms",   C["text"]),
            _mr("WS Heartbeat",       "OK",    C["green"]),
            _mr("Strategy Loop",      "ALIVE", C["green"]),
            _mr("Broker Session",     "ACTIVE",C["green"]),
            _mr("Last Update",        datetime.now().strftime("%H:%M:%S"), C["cyan"]),
        ], className="card",style={"flex":"1"}),
        html.Div([
            html.Div("Slippage by Order Type",className="stitle"),
            _mr("MARKET avg slip",  "₹0.95",  C["amber"]),
            _mr("LIMIT avg slip",   "₹0.12",  C["green"]),
            _mr("Fill vs Mid (CE)", "₹0.80 above", C["amber"]),
            _mr("Fill vs Mid (PE)", "₹1.20 above", C["amber"]),
            _mr("STT + charges",    "₹184",    C["red"]),
            _mr("Net PnL post-cost",f"₹{_SEED['pnl']-184:+,.0f}",
                C["green"] if _SEED["pnl"]-184>=0 else C["red"]),
        ], className="card",style={"flex":"1"}),
    ], style={"display":"flex","gap":"14px","marginTop":"14px"})

    return html.Div([
        html.Div([
            html.Div("Open Positions — Leg-wise Analytics",className="stitle"),
            html.Table([
                html.Thead(html.Tr([html.Th("Symbol"),html.Th("Side"),html.Th("Lots"),
                                    html.Th("Entry"),html.Th("LTP"),html.Th("PnL"),
                                    html.Th("Delta"),html.Th("IV"),html.Th("Slip"),html.Th("Theta")])),
                html.Tbody(pos_rows),
            ]),
        ], className="card",style={"marginBottom":"14px"}),
        html.Div([
            html.Div("Live Order Status",className="stitle"),
            html.Table([
                html.Thead(html.Tr([html.Th("Order ID"),html.Th("Symbol"),html.Th("Side"),
                                    html.Th("Qty"),html.Th("Fill Px"),html.Th("Type"),
                                    html.Th("Status"),html.Th("Slippage")])),
                html.Tbody(ord_rows),
            ]),
        ], className="card"),
        metrics,
    ])


# ══ TAB 5 — STRATEGY ══════════════════════════════════════════════════════════
def _tab_strategy(stats, iv, ivr):
    def tb():
        h = datetime.now().hour
        if h<10: return "OPENING"
        if h<12: return "MORNING"
        if h<14: return "MIDDAY"
        return "CLOSING"

    regime = [
        ("Market Regime",  "RANGE-BOUND", C["cyan"]),
        ("IV Environment", "LOW-IV" if iv<15 else "HIGH-IV",
         C["green"] if iv<15 else C["red"]),
        ("Time of Day",    tb(),           C["amber"]),
        ("News Event",     "NONE",         C["green"]),
        ("Bot Mode",       "HOLD",         C["blue"]),
        ("Entry Signal",   "NONE",         C["muted"]),
        ("Exit Signal",    "NONE",         C["muted"]),
        ("Re-entry",       "NOT TRIGGERED",C["muted"]),
    ]

    bt_rows = [
        ("Win Rate",      "54.2%",     f"{stats['win_rate']:.1f}%"),
        ("Expectancy",    "₹+180",     f"₹{stats['expectancy']:+,.0f}"),
        ("Profit Factor", "1.42",      f"{stats['profit_factor']:.2f}"),
        ("Avg Win",       "₹+620",     f"₹{stats['avg_win']:+,.0f}"),
        ("Avg Loss",      "₹-480",     f"₹{stats['avg_loss']:+,.0f}"),
        ("Max Drawdown",  "₹-4,200",   f"₹{stats['max_drawdown']:,.0f}"),
        ("Trades",        "142",        str(stats["total_trades"])),
        ("Slippage/Trade","₹0.30 est.","₹0.95 actual"),
    ]

    left = html.Div([
        html.Div([
            html.Div("Strategy State & Market Regime",className="stitle"),
            html.Div([
                html.Div([
                    html.Div(k,className="regime-key"),
                    html.Div(v,className="regime-val",style={"color":c}),
                ], className="regime-item")
                for k,v,c in regime
            ], className="regime-grid"),
        ], className="card",style={"marginBottom":"12px"}),

        html.Div([
            html.Div("Config Snapshot  ·  v1.0.0",className="stitle"),
            _mr("Strategy",     "ATM Straddle",    C["cyan"]),
            _mr("Entry Time",   "09:20 IST",        C["text"]),
            _mr("Exit Time",    "15:15 IST",        C["text"]),
            _mr("Lots",         "1",                C["text"]),
            _mr("Lot Size",     "50",               C["text"]),
            _mr("Order Type",   "MARKET",           C["text"]),
            _mr("Product",      "MIS",              C["text"]),
            _mr("Max Loss/Day", "₹5,000",           C["red"]),
            _mr("Target/Day",   "₹8,000",           C["green"]),
            _mr("Position SL",  "₹3,000",           C["red"]),
            _mr("Pos Target",   "₹2,000",           C["green"]),
            _mr("Trailing SL",  "Disabled",         C["muted"]),
            _mr("Broker",       "PAPER (demo)",     C["amber"]),
            _mr("Expiry",       "Nearest weekly",   C["text"]),
            _mr("Strike Step",  "₹50",              C["text"]),
        ], className="card"),
    ], style={"flex":"1"})

    right = html.Div([
        html.Div([
            html.Div("Backtest vs Live Comparison",className="stitle"),
            html.Table([
                html.Thead(html.Tr([html.Th("Metric"),html.Th("Backtest"),html.Th("Live")])),
                html.Tbody([
                    html.Tr([html.Td(r[0],style={"color":C["muted"]}),
                             html.Td(r[1],style={"color":C["blue"]}),
                             html.Td(r[2],style={"color":C["cyan"]})])
                    for r in bt_rows
                ]),
            ]),
        ], className="card",style={"marginBottom":"12px"}),

        html.Div([
            html.Div("Trade Journal",className="stitle"),
            *[html.Div([
                html.Span(t,style={"color":C["muted"],"marginRight":"10px","fontSize":"9px",
                                   "minWidth":"50px","display":"inline-block"}),
                html.Span(n,style={"fontSize":"11px"}),
            ], style={"padding":"5px 0","borderBottom":f"1px solid {C['border']}"})
              for t,n in [("09:20","Straddle entry at ATM 24500. IV=14.8%, spot stable."),
                          ("10:45","PE leg under pressure — spot +₹80. Holding position."),
                          ("12:30","Spot retraced. CE recovering. Net PnL turned positive.")]],
        ], className="card",style={"marginBottom":"12px"}),

        html.Div([
            html.Div("Active Alerts",className="stitle"),
            html.Div(html.Span("⚠  Delta > 0.08 — approaching limit",
                               style={"color":C["amber"],"fontSize":"11px"}),
                     style={"padding":"5px 0","borderBottom":f"1px solid {C['border']}"}),
            html.Div(html.Span("ℹ  IV Rank 22% — conditions favour premium selling",
                               style={"color":C["cyan"],"fontSize":"11px"}),
                     style={"padding":"5px 0"}),
        ], className="card"),
    ], style={"flex":"1"})

    return html.Div([left, right],
                    style={"display":"flex","gap":"14px","alignItems":"flex-start"})


# ─── Shared helpers ───────────────────────────────────────────────────────────
def _cb():
    return dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FM, color=C["muted"], size=10),
        margin=dict(l=48,r=10,t=10,b=34),
        xaxis=dict(showgrid=False,zeroline=False,linecolor=C["border"],tickfont=dict(size=9)),
        yaxis=dict(showgrid=True,gridcolor=C["border"],zeroline=False,
                   tickfont=dict(size=9),linecolor=C["border"]),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=C["card"],font_size=11,font_family=FM,bordercolor=C["border2"]),
    )

def _mr(key, val, col=None):
    return html.Div([
        html.Span(key, className="mkey"),
        html.Span(val, className="mval", style={"color": col or C["text"]}),
    ], className="mrow")

def _iv_rank():
    return round(20 + _SEED["iv"] * 1.4, 1)


# ─── Margin helpers ──────────────────────────────────────────────────────────
def _get_live_margin():
    """Read live margin from risk engine, fallback to zeros."""
    re = _bot_state.get("risk_engine")
    om = _bot_state.get("order_manager")
    df = _bot_state.get("data_feed")
    if re and om and df:
        try:
            spot      = df.get_spot_price() or 23850.0
            positions = om.get_all_positions()
            used      = re.margin.compute_current_margin_used(positions, spot)
            limit     = re.margin.max_margin_limit
            capital   = re.margin.total_capital
            return used, limit, capital
        except Exception:
            pass
    return 0.0, 100_000, 150_000

def _margin_panel(cur_pnl):
    """Live margin usage panel for Risk tab."""
    used, limit, capital = _get_live_margin()
    left    = max(0, limit - used)
    pct     = min(100, used / limit * 100) if limit else 0
    pct_str = f"{pct:.1f}%"
    bar_col = C["green"] if pct < 60 else (C["amber"] if pct < 80 else C["red"])
    return html.Div([
        _mr("Total Capital",  f"Rs{capital:,.0f}",       C["text"]),
        _mr("Max Margin",     f"Rs{limit:,.0f}",         C["text"]),
        _mr("Safety Buffer",  f"Rs{capital-limit:,.0f}", C["green"]),
        _mr("Margin Used",    f"Rs{used:,.0f}",          bar_col),
        _mr("Margin %",       pct_str,                   bar_col),
        _mr("Margin Left",    f"Rs{left:,.0f}",
            C["green"] if left > 30000 else C["red"]),
        _mr("Unrealised",     f"Rs{cur_pnl:+,.0f}",
            C["green"] if cur_pnl >= 0 else C["red"]),
        _mr("Realised",       "Rs0", C["muted"]),
        html.Div([
            html.Div([
                html.Span("Margin Used", style={"fontSize":"9px","color":C["muted"]}),
                html.Span(pct_str,       style={"fontSize":"9px","color":bar_col}),
            ], style={"display":"flex","justifyContent":"space-between","marginTop":"10px"}),
            html.Div(html.Div(style={
                "width": pct_str, "background": bar_col,
                "height":"100%", "borderRadius":"2px",
            }), className="prog-bg"),
            html.Div("Rs1,00,000 hard limit", style={
                "fontSize":"9px","color":C["muted"],"marginTop":"4px","textAlign":"right"
            }),
        ]),
    ])

# ─── Entry ────────────────────────────────────────────────────────────────────
def run_dashboard(host="0.0.0.0", port=8050, debug=False):
    logger.info(f"Dashboard v2 → http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)

if __name__ == "__main__":
    run_dashboard(debug=False)
