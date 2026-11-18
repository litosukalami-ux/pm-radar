# -*- coding: utf-8 -*-
"""产品雷达 · 抓取 → AI 编辑 → 生成看板。

用法：
    python radar.py            抓取全部源、AI 加工并重建看板
    python radar.py --build    只重建看板（不联网、不调模型）
    python radar.py --ai       只对未处理的条目补跑 AI（不抓取）
"""

import concurrent.futures as cf
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai
from config import (AI, BLOCK_KEYWORDS, CATEGORIES, CATEGORY_ORDER, DAILY_MIN_IMPORTANCE,
                    DAILY_PICKS, DAILY_QUOTA, DEDUP_THRESHOLD, FOCUS_TOPICS,
                    READER_PROFILE, SOURCES, STAR_KEYWORDS)

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "radar.db")
OUT_PATH = os.path.join(BASE, "docs", "index.html")
TEMPLATE_PATH = os.path.join(BASE, "templates", "index.html")
COMPETITIONS_PATH = os.path.join(BASE, "competitions.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# 云端跑在 UTC，展示统一按北京时间
CST = timezone(timedelta(hours=8))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- 解析

def _local(tag):
    return tag.split("}")[-1]


def _text(node, *names):
    """在子节点里按本地名找第一个非空文本。"""
    for name in names:
        for child in node:
            if _local(child.tag) == name:
                txt = "".join(child.itertext()).strip()
                if txt:
                    return txt
    return ""


def _link(node):
    """RSS 的 link 是文本，Atom 的 link 是带 href 的元素。"""
    best = ""
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href:
            rel = child.get("rel", "alternate")
            if rel == "alternate":
                return href.strip()
            best = best or href.strip()
        elif child.text and child.text.strip():
            return child.text.strip()
    return best


def _when(node):
    raw = _text(node, "pubDate", "published", "updated", "date")
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
# 部分源（36氪等）会输出裸 & 或控制字符，直接解析会报 ParseError
BARE_AMP_RE = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(text, limit=220):
    text = TAG_RE.sub(" ", text or "")
    text = WS_RE.sub(" ", text).strip()
    return text[:limit]


def _sanitize(content):
    """把不规范 XML 修补成可解析的文本。"""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    text = text.lstrip("\ufeff")
    text = CTRL_RE.sub("", text)
    return BARE_AMP_RE.sub("&amp;", text)


def parse_feed(content):
    """返回 [{title, link, published, summary}]，兼容 RSS 2.0 与 Atom。"""
    root = ET.fromstring(_sanitize(content))
    nodes = [n for n in root.iter() if _local(n.tag) in ("item", "entry")]
    out = []
    for n in nodes:
        title = _clean(_text(n, "title"), 200)
        link = _link(n)
        if not title or not link:
            continue
        out.append({
            "title": title,
            "link": link,
            "published": _when(n),
            "summary": _clean(_text(n, "description", "summary", "content", "encoded")),
        })
    return out


# ---------------------------------------------------------------- 抓取

def fetch_source(src):
    try:
        r = requests.get(src["url"], headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
        items = parse_feed(r.content)
        return src, items[: src.get("max_items", 30)], None
    except Exception as e:  # 单源失败不影响整体
        return src, [], f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 入库

COLUMNS = {
    "importance": "INTEGER DEFAULT 0",
    "topic": "TEXT DEFAULT ''",
    "one_line": "TEXT DEFAULT ''",
    "why": "TEXT DEFAULT ''",
    "tags": "TEXT DEFAULT ''",
    "dup_of": "TEXT",
    "ai_done": "INTEGER DEFAULT 0",
}


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            guid        TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            link        TEXT NOT NULL,
            source      TEXT NOT NULL,
            category    TEXT NOT NULL,
            summary     TEXT,
            published   TEXT NOT NULL,
            first_seen  TEXT NOT NULL,
            starred     INTEGER DEFAULT 0
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for name, ddl in COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pub ON items(published)")
    conn.commit()
    return conn


def is_blocked(text):
    return any(k in text for k in BLOCK_KEYWORDS)


def is_starred(text):
    return any(k in text.lower() for k in (w.lower() for w in STAR_KEYWORDS))


def save(conn, src, items):
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    for it in items:
        blob = it["title"] + it["summary"]
        if is_blocked(blob):
            continue
        guid = hashlib.md5(it["link"].encode("utf-8")).hexdigest()
        cur = conn.execute(
            """INSERT OR IGNORE INTO items
               (guid, title, link, source, category, summary,
                published, first_seen, starred)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (guid, it["title"], it["link"], src["name"], src["category"],
             it["summary"], it["published"].astimezone(timezone.utc).isoformat(), now,
             1 if is_starred(blob) else 0),
        )
        new_count += cur.rowcount
    conn.commit()
    return new_count


def run_fetch():
    conn = init_db()
    total_new = 0
    before = conn.execute("SELECT MAX(first_seen) FROM items").fetchone()[0]
    if not before:
        before = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with cf.ThreadPoolExecutor(6) as ex:
        futures = [ex.submit(fetch_source, s) for s in SOURCES]
        for fut in cf.as_completed(futures):
            src, items, err = fut.result()
            if err:
                log(f"  [失败] {src['name']:<16} {err}")
                continue
            new = save(conn, src, items)
            total_new += new
            log(f"  [OK]   {src['name']:<16} 抓到 {len(items):>3} 条，新增 {new:>3} 条")
    conn.close()
    log(f"\n本次新增 {total_new} 条")
    # 返回 "这次 run_fetch 之前最新条目时间"，给 process_ai 用来只加工本次新增
    return total_new, before


# ---------------------------------------------------------------- AI 加工

def _recent_rows(conn, since_iso=None, limit=150):
    """取待 AI 加工的条目。

    since_iso 有值时：只跑 first_seen >= since_iso 的（本次 run_fetch 新增）。
    没有时跑存量里 ai_done=0 的（--ai 手动触发时的兜底）。
    """
    if since_iso:
        rows = conn.execute(
            """SELECT guid, title, link, source, category, summary, published
               FROM items
               WHERE ai_done = 0 AND dup_of IS NULL AND first_seen >= ?
               ORDER BY published DESC LIMIT ?""", (since_iso, limit)
        ).fetchall()
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        rows = conn.execute(
            """SELECT guid, title, link, source, category, summary, published
               FROM items
               WHERE ai_done = 0 AND dup_of IS NULL AND published >= ?
               ORDER BY published DESC LIMIT ?""", (since, limit)
        ).fetchall()
    return [{
        "guid": r[0], "title": r[1], "link": r[2], "source": r[3],
        "category": r[4], "summary": r[5] or "", "published": r[6],
    } for r in rows]


def _fallback_score(item):
    """模型不可用时的兜底：有关键词给 3 分，否则 2 分。"""
    return 3 if is_starred(item["title"] + item["summary"]) else 2


def process_ai(since_iso=None):
    conn = init_db()
    items = _recent_rows(conn, since_iso)
    if not items:
        log("没有需要 AI 加工的条目。")
        conn.close()
        return 0
    log(f"AI 开始加工 {len(items)} 条……")

    clusters, result, err = ai.process(
        items, AI, READER_PROFILE, FOCUS_TOPICS, DEDUP_THRESHOLD)
    if err:
        log(f"  [跳过 AI] {err}")

    if result is None:
        # 没有结果就全部按兜底分处理，避免每次重跑
        for it in items:
            conn.execute("UPDATE items SET ai_done = 1, importance = ? WHERE guid = ?",
                         (_fallback_score(it), it["guid"]))
        conn.commit()
        conn.close()
        log("  已按关键词兜底打分。")
        return 0

    merged = 0
    clusters = _merge_by_same_as(clusters, result)

    for group, info in clusters:
        members = [items[i] for i in group]
        primary = max(members, key=lambda x: len(x.get("summary") or ""))
        importance = info.get("importance") or _fallback_score(primary)
        topic = info.get("topic") or CATEGORIES.get(primary["category"], ("",))[0] or "其他"
        for m in members:
            dup = None if m["guid"] == primary["guid"] else primary["guid"]
            if dup:
                merged += 1
            conn.execute(
                """UPDATE items SET importance = ?, topic = ?, one_line = ?, why = ?,
                          tags = ?, dup_of = ?, ai_done = 1 WHERE guid = ?""",
                (importance, topic, info.get("one_line", ""), info.get("why", ""),
                 json.dumps(info.get("tags", []), ensure_ascii=False),
                 dup, m["guid"]))
    conn.commit()
    conn.close()
    log(f"  AI 完成：{len(clusters)} 个主题，合并重复 {merged} 条。")
    return merged


def _merge_by_same_as(clusters, result):
    """标题相似度分组之后，再按模型判定的「同一件事」合并一次。

    返回 [(成员下标列表, 该主题的加工结果), ...]
    """
    n = len(clusters)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    index_owner = {}
    for ci, group in enumerate(clusters):
        for i in group:
            index_owner[i] = ci

    for ci, info in result.items():
        target = index_owner.get(info.get("same_as"))
        if target is None:
            continue
        a, b = find(ci), find(target)
        if a != b:
            parent[b] = a

    groups = {}
    for ci, group in enumerate(clusters):
        groups.setdefault(find(ci), []).extend(group)

    infos = {}
    for ci, info in result.items():
        root = find(ci)
        if root not in infos or info.get("importance", 0) > infos[root].get("importance", 0):
            infos[root] = info

    return [(members, infos.get(root, {})) for root, members in groups.items()]


# ---------------------------------------------------------------- 选今日必看

def load_items(limit=600):
    conn = init_db()
    rows = conn.execute(
        """SELECT title, link, source, category, summary, published, starred,
                  importance, topic, one_line, why, tags, first_seen
           FROM items WHERE dup_of IS NULL
           ORDER BY published DESC LIMIT ?""", (limit,)
    ).fetchall()
    conn.close()

    out = []
    for r in rows:
        if is_blocked((r[0] or "") + (r[4] or "")):
            continue
        try:
            tags = json.loads(r[11] or "[]")
        except (TypeError, ValueError):
            tags = []
        out.append({
            "title": r[0], "link": r[1], "source": r[2], "category": r[3],
            "summary": r[4] or "", "ts": r[5],
            "published": datetime.fromisoformat(r[5]).astimezone(CST).strftime("%m-%d"),
            "starred": bool(r[6]),
            "importance": r[7] or 0,
            "topic": r[8] or "",
            "one_line": r[9] or "",
            "why": r[10] or "",
            "tags": tags,
            "new": datetime.fromisoformat(r[12]).astimezone(CST).date() == datetime.now(CST).date(),
        })
    return out


def pick_daily(items):
    """AI 打分 + 兴趣方向 + 新鲜度，合起来挑今天最该看的几条。"""
    now = datetime.now(timezone.utc)
    for it in items:
        it["daily"] = False

    scored = []
    for it in items:
        age = now - datetime.fromisoformat(it["ts"])
        if age > timedelta(days=4):
            continue
        if it["importance"] < DAILY_MIN_IMPORTANCE:
            continue
        hits = sum(1 for t in FOCUS_TOPICS
                   if t.lower() in (it["title"] + it["summary"] + it["topic"]).lower())
        score = it["importance"] * 10 + hits * 2 + (3 if it["category"] == "contest" else 0)
        score -= age.total_seconds() / 86400 * 1.5
        it["score"] = score
        scored.append(it)

    scored.sort(key=lambda x: x["score"], reverse=True)

    def can_add(it, topic_count, source_count):
        # 同一主题最多两条，同一来源最多两条，避免整屏都是同一类东西
        return (topic_count.get(it["topic"], 0) < 2
                and source_count.get(it["source"], 0) < 2)

    picked, used, topic_count, source_count = [], set(), {}, {}

    # 先保证每个分类都有机会，再按分数补满
    for cat, quota in DAILY_QUOTA.items():
        if len(picked) >= DAILY_PICKS:
            break
        pool = [i for i in scored if i["category"] == cat and i["link"] not in used]
        for it in pool[:max(1, quota // 2)]:
            if len(picked) >= DAILY_PICKS:
                break
            if not can_add(it, topic_count, source_count):
                continue
            picked.append(it)
            used.add(it["link"])
            topic_count[it["topic"]] = topic_count.get(it["topic"], 0) + 1
            source_count[it["source"]] = source_count.get(it["source"], 0) + 1

    for it in scored:
        if len(picked) >= DAILY_PICKS:
            break
        if it["link"] in used or not can_add(it, topic_count, source_count):
            continue
        picked.append(it)
        used.add(it["link"])
        topic_count[it["topic"]] = topic_count.get(it["topic"], 0) + 1
        source_count[it["source"]] = source_count.get(it["source"], 0) + 1

    picked.sort(key=lambda x: x["score"], reverse=True)
    for rank, it in enumerate(picked, 1):
        it["daily"] = True
        it["rank"] = rank
    return len(picked)


def load_competitions():
    """只保留还没开始报名的比赛（stage=upcoming），按预计开放时间排序。"""
    if not os.path.exists(COMPETITIONS_PATH):
        return []
    with open(COMPETITIONS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data = [c for c in data if c.get("stage") == "upcoming"]
    data.sort(key=lambda c: c.get("expected_start") or "9999")
    return data


# ---------------------------------------------------------------- 看板

def build():
    items = load_items()
    picked = pick_daily(items)
    contests = load_competitions()
    updated = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    ai_ready = sum(1 for i in items if i["importance"])

    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    def dump(obj):
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    html = (html
            .replace("__UPDATED__", updated)
            .replace("__COUNT__", str(len(items)))
            .replace("__PICKED__", str(picked))
            .replace("__AI_READY__", str(ai_ready))
            .replace("__REPO__", "litosukalami-ux/pm-radar")
            .replace("__FOCUS__", dump(FOCUS_TOPICS))
            .replace("__ITEMS__", dump(items))
            .replace("__CONTESTS__", dump(contests))
            .replace("__CATS__", dump(CATEGORIES))
            .replace("__ORDER__", dump(CATEGORY_ORDER)))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"看板已生成：{OUT_PATH}（共 {len(items)} 条，今日必看 {picked} 条）")


def main():
    if "--build" in sys.argv:
        build()
        return
    if "--ai" in sys.argv:
        process_ai()
        build()
        return
    log("开始抓取……\n")
    _, before = run_fetch()
    process_ai(before)
    build()


if __name__ == "__main__":
    main()
