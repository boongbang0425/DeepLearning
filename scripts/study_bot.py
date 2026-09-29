"""CECOM DL Study bot : Notion x GitHub x Discord
usage: python study_bot.py open | close | rollover | remind | activity | share <paths...>
env: NOTION_TOKEN, DASHBOARD, MEMBER_DB, RECORD_DB, STUDY_DB, CURRICULUM_DB, DISCORD_WEBHOOK
"""
import os, sys, math, random, datetime as dt, requests

API = "https://api.notion.com/v1"
H = {"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
     "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
ENV = os.environ.get
KST = dt.timezone(dt.timedelta(hours=9))
# 모임 날짜 (1주차 = OT). 10월은 쉼
SESSIONS = [dt.date(2026, 9, 21), dt.date(2026, 9, 28), dt.date(2026, 11, 2),
            dt.date(2026, 11, 9), dt.date(2026, 11, 16), dt.date(2026, 11, 23)]
LAST = len(SESSIONS)
PLACE = "301관 B203호"
CHECKS = ["출석", "지각", "사유결석"]  # 앞쪽 우선, 아무것도 없으면 무단결석


# Notion helpers
def week_of(d):
    """모임 날이면 주차, 아니면 None"""
    return SESSIONS.index(d) + 1 if d in SESSIONS else None


def query(db, flt=None, sorts=None):
    body, out = {}, []
    if flt:
        body["filter"] = flt
    if sorts:
        body["sorts"] = sorts
    while True:
        r = requests.post(f"{API}/databases/{db}/query", headers=H, json=body).json()
        out += r.get("results", [])
        if not r.get("has_more"):
            return out
        body["start_cursor"] = r["next_cursor"]


def patch(page_id, props):
    requests.patch(f"{API}/pages/{page_id}", headers=H, json={"properties": props})


def children(block_id):
    return requests.get(f"{API}/blocks/{block_id}/children?page_size=100", headers=H).json()["results"]


def plain(rt):
    return "".join(t["plain_text"] for t in rt)


def name(p):
    return plain(p["properties"]["이름"]["title"])


def sel(p, prop):
    s = p["properties"][prop]["select"]
    return s["name"] if s else None


def members(active=True):
    ms = query(ENV("MEMBER_DB"))
    return [m for m in ms if sel(m, "상태") == "활동"] if active else ms


def records(week):
    rs = query(ENV("RECORD_DB"), {"property": "주차", "select": {"equals": f"{week}주차"}})
    return {r["properties"]["멤버"]["relation"][0]["id"]: r
            for r in rs if r["properties"]["멤버"]["relation"]}


def lesson(week):
    """커리큘럼 DB에서 주제와 목표"""
    r = query(ENV("CURRICULUM_DB"), {"property": "주차", "number": {"equals": week}})
    if not r:
        return f"{week}주차", ""
    p = r[0]["properties"]
    return plain(p["주제"]["title"]), plain(p["목표"]["rich_text"])


def discord(msg):
    if ENV("DISCORD_WEBHOOK"):
        requests.post(ENV("DISCORD_WEBHOOK"), json={"content": msg})
    print(msg)


def set_text(block, parts):
    """블록 본문 교체. parts = [(text, bold)]"""
    kind = block["type"]
    for c in children(block["id"]) if block.get("has_children") else []:
        requests.delete(f"{API}/blocks/{c['id']}", headers=H)
    requests.patch(f"{API}/blocks/{block['id']}", headers=H, json={kind: {"rich_text": [
        {"text": {"content": t}, "annotations": {"bold": b}} for t, b in parts]}})


def dashboard_block(pred):
    return next((b for b in children(ENV("DASHBOARD")) if pred(b)), None)


# building blocks
def presenters(ms, slot):
    return [m for m in ms if sel(m, "발표") == slot]


def snapshot(week):
    """출석 카드 → 해당 주차 기록. 체크 없으면 무단결석"""
    rec = records(week)
    for m in members():
        st = next((c for c in CHECKS if m["properties"][c]["checkbox"]), "무단결석")
        if m["id"] in rec:
            patch(rec[m["id"]]["id"], {"상태": {"select": {"name": st}}})


def reset_checks():
    for m in members(active=False):
        patch(m["id"], {c: {"checkbox": False} for c in CHECKS})


def draw(nxt, ms):
    """다음 주 발표자 비복원 추출. k = min(3, max(2, ceil(대기 인원 / 남은 주차)))"""
    rng = random.SystemRandom()
    for m in presenters(ms, "다음"):
        patch(m["id"], {"발표": {"select": None}})
    pool = [m for m in ms if m["properties"]["발표 풀"]["formula"]["string"] == "대기"]
    k = min(3, max(2, math.ceil(len(pool) / (LAST + 1 - nxt))))
    picked = rng.sample(pool, min(k, len(pool)))
    if len(picked) < k:
        rest = [m for m in ms if m not in picked and sel(m, "발표") != "이번 주"]
        picked += rng.sample(rest, min(k - len(picked), len(rest)))
    for m in picked:
        patch(m["id"], {"발표": {"select": {"name": "다음"}}})
    return [name(m) for m in picked]


def refresh(week):
    """대시보드 '이번 주' 카드 갱신"""
    if week > LAST:
        parts = [("과정 종료", True), ("\n수고하셨습니다.", False)]
    else:
        ms = members()
        topic, goal = lesson(week)
        d = SESSIONS[week - 1]
        now_ = ", ".join(name(m) for m in presenters(ms, "이번 주")) or "없음"
        nxt_ = ", ".join(name(m) for m in presenters(ms, "다음")) or (
            "월 18:00 추첨" if week < LAST else "없음 (마지막 주)")
        parts = [(f"{week}주차 : {topic}", True),
                 (f"\n목표 : {goal}\n일시 : {d.month}/{d.day}(월) 18:00, {PLACE}"
                  f"\n이번 주 발표 : {now_}\n다음 발표 : {nxt_}", False)]
    set_text(dashboard_block(lambda b: b["type"] == "callout"), parts)


# jobs
def open_():
    """월 18:00 : 출석 초기화(기본 무단결석), 다음 발표자 추첨, 공지"""
    w = week_of(dt.date.today())
    if not w:  # 모임 없는 월요일
        return
    reset_checks()
    nxt = draw(w + 1, members()) if w < LAST else []
    refresh(w)
    discord(f"**{w}주차 시작** : 출석 초기화 완료\n"
            f"다음 발표 : {', '.join(nxt) or '없음'}")


def close():
    """월 20:00 : 이번 주 발표 완료 처리 후 칸 비움"""
    w = week_of(dt.date.today())
    if not w:  # 모임 없는 월요일
        return
    rec = records(w)
    for m in presenters(members(), "이번 주"):
        if m["id"] in rec:
            patch(rec[m["id"]]["id"], {"발표": {"checkbox": True}})
        patch(m["id"], {"발표": {"select": None}})
    refresh(w)


def rollover():
    """월 22:00 : 출석 기록 저장, 다음 발표자를 이번 주로, 공지, 상벌 알림"""
    w = week_of(dt.date.today())
    if not w:  # 모임 없는 월요일
        return
    snapshot(w)
    for m in presenters(members(), "다음"):
        patch(m["id"], {"발표": {"select": {"name": "이번 주"}}})
    refresh(w + 1)
    ms = members(active=False)
    now_ = [name(m) for m in presenters(ms, "이번 주")]
    alerts = [f"{name(m)} : {m['properties']['상벌']['formula']['string']}" for m in ms
              if (m["properties"]["상벌"]["formula"]["string"] or "").startswith(("추방", "경고"))]
    msg = f"**{w + 1}주차 발표** : {', '.join(now_) or '없음'}" if w < LAST else "과정 종료"
    discord(msg + ("\n\n**운영진 확인**\n" + "\n".join(alerts) if alerts else ""))
    charts()


def remind():
    """일 20:00 : 내일 모임 안내"""
    w = week_of(dt.date.today() + dt.timedelta(days=1))
    if not w:
        return
    topic, goal = lesson(w)
    now_ = [name(m) for m in presenters(members(), "이번 주")]
    discord(f"**{w}주차 : {topic}** : 내일 18:00, {PLACE}\n목표 : {goal}\n"
            f"발표 : {', '.join(now_) or '없음'}\n불참, 지각은 오늘 중 미리 알려주세요.")


def activity():
    """매시 : 공부 순위, 최근 활동 한 줄, 그림 갱신"""
    now = dt.datetime.now(dt.timezone.utc)
    week_ago = (now - dt.timedelta(days=7)).isoformat()
    stats = []
    for card in query(ENV("STUDY_DB")):
        times = []
        for b in children(card["id"]):
            if b["type"] == "child_database":
                times += [r["last_edited_time"] for r in query(b["id"])]
            elif b["type"] == "child_page":
                times.append(b["last_edited_time"])
        stats.append({"card": card, "n": len(times), "latest": max(times, default=None),
                      "week": sum(t >= week_ago for t in times)})
    stats.sort(key=lambda s: (s["week"], s["latest"] or "", s["n"]), reverse=True)
    for rank, s in enumerate(stats, 1):
        props = {"순위": {"number": rank}, "기록 수": {"number": s["n"]},
                 "주간 수정": {"number": s["week"]}}
        if s["latest"]:
            props["최근 학습"] = {"date": {"start": s["latest"]}}
        patch(s["card"]["id"], props)
    top = max((s for s in stats if s["latest"]), key=lambda s: s["latest"], default=None)
    log = dashboard_block(lambda b: b["type"] == "paragraph"
                          and plain(b["paragraph"]["rich_text"]).startswith("가장 최근에"))
    if top and log:
        who = name(top["card"])
        t = dt.datetime.fromisoformat(top["latest"].replace("Z", "+00:00"))
        if now - t < dt.timedelta(hours=1):
            text = f"가장 최근에 {who}님이 공부 중이에요!"
        else:
            k = t.astimezone(KST)
            text = f"가장 최근에 {who}님이 공부했어요! ({k.month}/{k.day} {k:%H:%M})"
        set_text(log, [(text, False)])
    charts()


# 그림 (막대 + 꺾은선 + 추세선, SVG)

FW, FH = 720, 340
L, R, T, B = 58, 70, 62, 48          # 여백
INK, MUTED, HAIR = "#7f8f85", "#9aa89f", "#c9d2cc"
C = {"출석": "#4f7d62", "지각": "#9db5a3", "사유": "#cfd8d1", "무단": "#9a6a5c",
     "line": "#c9a227", "bar": "#5f8a70"}
FONT = "font-family=\"'Noto Serif KR','Nanum Myeongjo',Georgia,serif\""


def _t(x, y, s, size=17, anchor="middle", fill=INK, weight="400"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'fill="{fill}" font-weight="{weight}" {FONT}>{s}</text>')


def _frame(ymax, ystep, right=True):
    pw, ph = FW - L - R, FH - T - B
    out = [f'<line x1="{L}" y1="{T+ph}" x2="{L+pw}" y2="{T+ph}" stroke="{INK}" stroke-width="1.2"/>',
           f'<line x1="{L}" y1="{T}" x2="{L+pw}" y2="{T}" stroke="{INK}" stroke-width="1.6"/>']
    v = 0
    while v <= ymax + 1e-9:
        y = T + ph - v / ymax * ph
        if 0 < v < ymax:
            out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}" stroke="{HAIR}" stroke-width="0.6" stroke-dasharray="2 3"/>')
        out.append(_t(L - 10, y + 6, f"{v:g}", 16, "end"))
        if right:
            out.append(_t(L + pw + 10, y + 6, f"{v / ymax * 100:.0f}%", 16, "start"))
        v += ystep
    return out, pw, ph


def _legend(items):
    out, x = [], L
    for name, color, kind in items:
        if kind == "bar":
            out.append(f'<rect x="{x}" y="16" width="14" height="14" fill="{color}"/>')
        elif kind == "dash":
            out.append(f'<line x1="{x}" y1="23" x2="{x+22}" y2="23" stroke="{color}" stroke-width="2" stroke-dasharray="5 4"/>')
        else:
            out.append(f'<line x1="{x}" y1="23" x2="{x+22}" y2="23" stroke="{color}" stroke-width="2.4"/>'
                       f'<circle cx="{x+11}" cy="23" r="3.6" fill="{color}"/>')
        w = 14 if kind == "bar" else 22
        out.append(_t(x + w + 6, 29, name, 17, "start"))
        x += w + 6 + 17 * len(name) + 18
    return out


def _svg(parts):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FW} {FH}" width="{FW}" height="{FH}">'
            + "".join(parts) + "</svg>")


def _trend(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(pts) < 2:
        return None
    n = len(pts)
    mx, my = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx else 0
    return lambda x: my + b * (x - mx)


def fig_weekly(counts, members):
    """counts: [{출석,지각,사유,무단}] × 주차. 막대 = 인원, 꺾은선 = 출석률, 점선 = 추세"""
    ymax = max(2, members + (members % 2))
    parts, pw, ph = _frame(ymax, 2)
    n = len(counts)
    slot = pw / n
    bw = slot * 0.46
    rates, xs = [], []
    for i, c in enumerate(counts):
        cx = L + slot * (i + 0.5)
        xs.append(cx)
        y = T + ph
        for k in ("출석", "지각", "사유", "무단"):
            v = c.get(k, 0)
            if v:
                h = v / ymax * ph
                y -= h
                parts.append(f'<rect x="{cx-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{C[k]}"/>')
        parts.append(_t(cx, T + ph + 28, f"W{i+1}", 17))
        denom = c.get("출석", 0) + c.get("지각", 0) + c.get("무단", 0)
        rates.append((c.get("출석", 0) + 0.5 * c.get("지각", 0)) / denom if denom else None)
    ry = lambda r: T + ph - r * ph
    f = _trend(range(n), rates)
    idx = [i for i, r in enumerate(rates) if r is not None]
    if f:
        a, b = idx[0], idx[-1]
        parts.append(f'<line x1="{xs[a]:.1f}" y1="{ry(max(0,min(1,f(a)))):.1f}" x2="{xs[b]:.1f}" '
                     f'y2="{ry(max(0,min(1,f(b)))):.1f}" stroke="{C["line"]}" stroke-width="2" stroke-dasharray="7 5"/>')
    pts = [(xs[i], ry(r)) for i, r in enumerate(rates) if r is not None]
    if len(pts) > 1:
        parts.append('<polyline fill="none" stroke="%s" stroke-width="3" points="%s"/>'
                     % (C["line"], " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)))
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{C["line"]}"/>')
    if not pts:
        parts.append(_t(L + pw / 2, T + ph / 2, "첫 기록 후 표시", 18, fill=MUTED))
    parts += _legend([("출석", C["출석"], "bar"), ("지각", C["지각"], "bar"), ("사유", C["사유"], "bar"),
                      ("무단", C["무단"], "bar"), ("출석률", C["line"], "line"), ("추세", C["line"], "dash")])
    return _svg(parts)


def fig_members(names, study, rates):
    """막대 = 최근 7일 학습 기록 수, 꺾은선 = 개인 출석률, 점선 = 평균 출석률"""
    top = max([4] + study)
    step = max(1, round(top / 4))
    ymax = step * 4
    parts, pw, ph = _frame(ymax, step)
    n = len(names)
    slot = pw / max(n, 1)
    bw = slot * 0.46
    ry = lambda r: T + ph - r * ph
    pts = []
    for i, (nm, s, r) in enumerate(zip(names, study, rates)):
        cx = L + slot * (i + 0.5)
        if s:
            h = s / ymax * ph
            parts.append(f'<rect x="{cx-bw/2:.1f}" y="{T+ph-h:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{C["bar"]}"/>')
        parts.append(_t(cx, T + ph + 28, nm[-2:], 17))
        if r is not None:
            pts.append((cx, ry(r)))
    valid = [r for r in rates if r is not None]
    if valid:
        avg = sum(valid) / len(valid)
        parts.append(f'<line x1="{L}" y1="{ry(avg):.1f}" x2="{L+pw}" y2="{ry(avg):.1f}" '
                     f'stroke="{C["line"]}" stroke-width="2" stroke-dasharray="7 5"/>')
    if len(pts) > 1:
        parts.append('<polyline fill="none" stroke="%s" stroke-width="3" points="%s"/>'
                     % (C["line"], " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)))
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{C["line"]}"/>')
    if not any(study) and not pts:
        parts.append(_t(L + pw / 2, T + ph / 2, "첫 기록 후 표시", 18, fill=MUTED))
    parts += _legend([("최근 7일 학습", C["bar"], "bar"), ("출석률", C["line"], "line"),
                      ("평균", C["line"], "dash")])
    return _svg(parts)


def upload_svg(fname, svg):
    r = requests.post(f"{API}/file_uploads", headers=H,
                      json={"filename": fname, "content_type": "image/svg+xml"}).json()
    requests.post(f"{API}/file_uploads/{r['id']}/send",
                  headers={k: v for k, v in H.items() if k != "Content-Type"},
                  files={"file": (fname, svg.encode(), "image/svg+xml")})
    return r["id"]


def set_figure(i, fid):
    """대시보드 i번째 이미지를 새 파일로 교체 (캡션 유지)"""
    imgs = [b for b in children(ENV("DASHBOARD")) if b["type"] == "image"]
    if len(imgs) <= i:
        return
    b = imgs[i]
    img = {"type": "file_upload", "file_upload": {"id": fid}, "caption": b["image"].get("caption", [])}
    r = requests.patch(f"{API}/blocks/{b['id']}", headers=H, json={"image": img})
    if not r.ok:  # 교체가 안 되면 바로 뒤에 새로 넣고 기존 삭제
        requests.patch(f"{API}/blocks/{ENV('DASHBOARD')}/children", headers=H,
                       json={"after": b["id"], "children": [{"type": "image", "image": img}]})
        requests.delete(f"{API}/blocks/{b['id']}", headers=H)


def charts():
    ms = sorted(members(), key=name)
    key = {"출석": "출석", "지각": "지각", "사유결석": "사유", "무단결석": "무단"}
    counts = [{} for _ in range(LAST)]
    for r in query(ENV("RECORD_DB")):
        wk, st = sel(r, "주차"), sel(r, "상태")
        if wk and st:
            c = counts[int(wk[0]) - 1]
            c[key[st]] = c.get(key[st], 0) + 1
    study = {name(c): c["properties"]["주간 수정"]["number"] or 0 for c in query(ENV("STUDY_DB"))}
    rates = [m["properties"]["출석률"]["rollup"].get("number") for m in ms]
    set_figure(0, upload_svg("fig1.svg", fig_weekly(counts, len(ms))))
    set_figure(1, upload_svg("fig2.svg", fig_members([name(m) for m in ms],
                                                      [study.get(name(m), 0) for m in ms], rates)))


def share(paths):
    """push : 공용 저장소 week{N}/{이름}/ 경로 → 해당 기록 공유 체크"""
    hits = {(int(p.split("/")[0][4:]), p.split("/")[1]) for p in paths
            if p.count("/") >= 2 and p.startswith("week") and p.split("/")[0][4:].isdigit()}
    ids = {name(m): m["id"] for m in members(active=False)}
    for w, who in hits:
        r = records(w).get(ids.get(who))
        if r:
            patch(r["id"], {"공유": {"checkbox": True}})
            print(f"shared : W{w} {who}")


if __name__ == "__main__":
    jobs = {"open": open_, "close": close, "rollover": rollover,
            "remind": remind, "activity": activity}
    jobs[sys.argv[1]]() if sys.argv[1] in jobs else share(sys.argv[2:])
