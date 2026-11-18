# -*- coding: utf-8 -*-
"""AI 编辑部：把一堆标题变成「今天值得看什么」。

做三件事：
1. 跨源合并——同一件事被多家报道时并成一条；
2. 重要度打分——1 到 5 分，按读者画像判断，不是按热度；
3. 写人话——一句话说清讲了什么、为什么值得看。

模型不可用时全部跳过，主流程照常出看板（退化为按关键词 + 时间排序）。
"""

import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations

import requests

BASE = os.path.dirname(os.path.abspath(__file__))

PUNCT_RE = re.compile(r"[\s\u3000·、，。！？“”‘’：；（）()\[\]【】《》<>|/\\\-—_.,!?:;\"'`~@#$%^&*+=]+")
TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------- 密钥

def load_key(cfg):
    key = os.environ.get(cfg["key_env"], "").strip()
    if key:
        return key
    path = os.path.join(BASE, cfg["key_file"])
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    return ""


# ---------------------------------------------------------------- 调用

def chat(cfg, key, messages, json_mode=True, temperature=0.2, retries=2):
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(cfg["base_url"], headers=headers, json=body,
                              timeout=cfg.get("timeout", 120))
            if r.status_code >= 400 and json_mode:
                # 少数模型不支持 response_format，去掉再试一次
                body.pop("response_format", None)
                r = requests.post(cfg["base_url"], headers=headers, json=body,
                                  timeout=cfg.get("timeout", 120))
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            if attempt + 1 < retries:
                print(f"  [重试] 模型超时，再试一次……")
    raise last


def _json_block(text):
    """模型偶尔会包一层 ```json、前后带解释，甚至并排输出多个对象。"""
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("返回里没有 JSON")

    decoder = json.JSONDecoder()
    objects, pos = [], start
    while pos < len(text):
        while pos < len(text) and text[pos] in " \r\n\t,，":
            pos += 1
        if pos >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, pos)
        except ValueError:
            break
        if isinstance(obj, dict):
            objects.append(obj)
        pos = end
    if not objects:
        raise ValueError("返回内容解析失败")
    return objects



# ---------------------------------------------------------------- 去重

def _norm(title):
    return PUNCT_RE.sub("", (title or "").lower())


def _bigrams(text):
    return {text[i:i + 2] for i in range(len(text) - 1)} or {text}


def cluster(items, threshold):
    """按标题相似度分组，返回 [[index...], ...]。

    用字符二元组 + IDF 加权余弦。IDF 的作用是压掉「训练营」「助手」这类
    出现在一堆标题里的模板词，只让真正有区分度的词决定相似度，
    避免把「选股助手」和「手游助手」当成同一件事。
    """
    n = len(items)
    if n < 2:
        return [[i] for i in range(n)]

    grams = [_bigrams(_norm(it["title"])) for it in items]
    df = {}
    for g in grams:
        for x in g:
            df[x] = df.get(x, 0) + 1
    idf = {x: math.log((n + 1) / (d + 0.5)) for x, d in df.items()}

    weights = [{x: idf[x] for x in g} for g in grams]
    norms = [math.sqrt(sum(w * w for w in ws.values())) or 1.0 for ws in weights]

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in combinations(range(n), 2):
        common = grams[i] & grams[j]
        if not common:
            continue
        num = sum(idf[x] * idf[x] for x in common)
        score = num / (norms[i] * norms[j])
        if score >= threshold:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


# ---------------------------------------------------------------- 打分

SYSTEM = """你是一个挑剔的产品经理情报编辑。读者每天只看 6 条，所以你的核心工作是把 90% 的内容压低，只把极少数抬起来。评分慷慨等于没有评分。

评分标准（严格执行配额）：
- 5 分：本批最多 1-2 条（约 10%）。只给「今天不看就会错过实际机会」的：开放报名/截止、明确的招聘偏好、能直接复刻的完整产品复盘；
- 4 分：本批最多 3 条（约 25%）。行业里真正的新变化，或能改变他做事方式的方法；
- 3 分：值得扫一眼，有信息量但不紧急；
- 2 分：泛泛而谈、营销味重、信息密度低；
- 1 分：广告、软文、凑数内容、与他完全无关。

特别规则：
- 同一模板的系列帖（例如同一个训练营的结营作业），只有内容最完整的一条可以到 4 分，其余最多 3 分；
- 活动报名、沙龙、直播预告、课程推广，最多 2 分；
- 标题带「绝了」「震惊」「必看」「炸了」这类词的营销文，最多 2 分；
- 纯融资、股价、口水战、论文，最多 2 分；
- 同类话题里信息量重复的，只保留最好的一条高分，其余降一档。

写作要求：
- one_line：用你自己的话概括这条具体讲了什么，禁止照抄标题，不超过 35 字；
- why：说清「这条具体能让他做什么」，禁止「对求职有帮助」「值得学习」这类套话，不超过 40 字；3 分及以下留空；
- topic：4-8 个字的中文主题词，例如「AI 编程」「模型发布」「求职方法」，不要用 category 里的英文名；
- tags：1 到 3 个，优先从给定的关注方向里选。

去重：如果两条讲的是同一件事（只是措辞不同、来源不同），在重复那条里加 "same_as": 主条目的 id。没有重复就不要写这个字段。

只输出 JSON，不要任何解释。"""


def _fmt_profile(profile, focus):
    return f"{profile}\n\n他的长期关注方向：{'、'.join(focus)}"


def _prompt(batch, profile, focus):
    rows = []
    for i, it in enumerate(batch):
        rows.append({
            "id": i + 1,
            "title": it["title"],
            "source": it.get("source", ""),
            "summary": (it.get("summary") or "")[:160],
            "category": it.get("category", ""),
        })
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            _fmt_profile(profile, focus)
            + "\n\n下面是候选条目（已做过初步去重，每组代表一件事）：\n"
            + json.dumps(rows, ensure_ascii=False)
            + """

请逐条评估，输出这种结构：
{"items":[{"id":1,"importance":4,"topic":"AI 编程","one_line":"…","why":"…","tags":["AI 产品"]}]}

必须为每个 id 都输出一条，不要遗漏，不要新增。"""},
    ]


CATEGORY_NAMES = {
    "contest": "比赛与获奖", "pm": "产品经理", "ai_product": "AI 产品动态",
    "tech": "技术动态", "vibe": "Vibe Coding",
}


def _calibrate(rows):
    """模型打分偏松是常态，这里按比例强制压一遍。

    rows: [(idx, info, title), ...]，按输入顺序。
    """
    n = len(rows)
    if not n:
        return
    cap5 = max(1, round(n * 0.15))
    cap4 = max(2, round(n * 0.32))
    fives = [r for r in rows if r[1]["importance"] == 5]
    for r in fives[cap5:]:
        r[1]["importance"] = 4
    fours = [r for r in rows if r[1]["importance"] == 4]
    for r in fours[cap4:]:
        r[1]["importance"] = 3


def _tidy(info, title):
    """修掉三类典型偷懒：摘要抄标题、topic 写英文分类名、低分还写套话理由。"""
    tn, ln = _norm(title), _norm(info["one_line"])
    if ln and (ln in tn or tn in ln):
        info["one_line"] = ""
    elif ln and tn:
        a, b = _bigrams(ln), _bigrams(tn)
        if a and b and len(a & b) / len(a | b) > 0.7:
            info["one_line"] = ""

    topic = info["topic"].strip().lower()
    if topic in CATEGORY_NAMES:
        info["topic"] = CATEGORY_NAMES[topic]

    if info["importance"] <= 3:
        info["why"] = ""


def enrich(cfg, key, items, clusters, profile, focus):
    """对每个簇调用一次模型，批处理并发执行（默认 4 路）。

    返回 {cluster_index: {...}}，失败返回 {}。
    """
    reps = []
    for group in clusters:
        rep = max((items[i] for i in group), key=lambda x: len(x.get("summary") or ""))
        reps.append(rep)

    size = max(1, cfg.get("batch_size", 12))
    concurrency = cfg.get("concurrency", 4)

    # 先把所有批次切好
    batches = []
    for start in range(0, len(reps), size):
        batches.append((start, reps[start:start + size]))

    out = {}
    def _run(start, batch):
        """单批执行：调模型 + 解析 + 校准。返回 (start, got_list)。"""
        try:
            raw = chat(cfg, key, _prompt(batch, profile, focus))
            objects = _json_block(raw)
            rows = []
            for obj in objects:
                rows.extend(obj.get("items") or obj.get("data") or [])
        except Exception as e:
            print(f"  [AI 失败] 第 {start // size + 1} 批：{type(e).__name__}: {e}")
            return start, []

        got = []
        for row in rows:
            try:
                idx = int(row.get("id")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue
            try:
                same = int(row.get("same_as")) - 1
                same = start + same if 0 <= same < len(batch) else None
            except (TypeError, ValueError):
                same = None
            info = {
                "importance": max(1, min(5, int(row.get("importance") or 3))),
                "topic": _clean_text(row.get("topic"), 20),
                "one_line": _clean_text(row.get("one_line"), 80),
                "why": _clean_text(row.get("why"), 90),
                "tags": [str(t)[:12] for t in (row.get("tags") or [])][:3],
                "same_as": same,
            }
            _tidy(info, batch[idx]["title"])
            got.append((start + idx, info, batch[idx]["title"]))

        _calibrate(got)
        return start, got

    # 并发跑所有批次
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_run, s, b): s for s, b in batches}
        for fut in as_completed(futures):
            _, got = fut.result()
            for idx, info, _ in got:
                out[idx] = info

    return out


def _clean_text(value, limit):
    if not value:
        return ""
    text = TAG_RE.sub(" ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# ---------------------------------------------------------------- 入口

def process(new_items, cfg, profile, focus, threshold):
    """返回 (clusters, enrichments, error)。没有任何 AI 配置时 error 非空。"""
    key = load_key(cfg)
    if not key:
        return None, None, "未配置 API Key"

    clusters = cluster(new_items, threshold)
    try:
        result = enrich(cfg, key, new_items, clusters, profile, focus)
    except Exception as e:
        return clusters, None, f"{type(e).__name__}: {e}"
    if not result:
        return clusters, None, "模型没有返回可用结果"
    return clusters, result, None


if __name__ == "__main__":
    # 手动试一下连接是否通
    sys.path.insert(0, BASE)
    from config import AI, READER_PROFILE, FOCUS_TOPICS

    k = load_key(AI)
    if not k:
        print("没有找到 API Key")
        raise SystemExit(1)
    print(chat(AI, k, [{"role": "user", "content": "只回一个词：通了"}]))
