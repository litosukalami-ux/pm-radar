import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"

# 极简单的"阅读器"：优先用 readability-lxml，没有就用 BeautifulSoup + 打分启发式
try:
    from readability import Document
    HAS_READABILITY = True
except ImportError:
    HAS_READABILITY = False


def extract_main(html, base_url):
    """返回 (plain_text, [img_urls])。失败返回 ("", [])。"""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "iframe", "svg", "footer", "nav",
                    "header", "form", "input", "button", "aside"]):
        t.decompose()

    if HAS_READABILITY:
        doc = Document(html)
        summary = doc.summary(html_partial=True)
        soup2 = BeautifulSoup(summary, "lxml")
    else:
        # 启发式：找 id/class 里带 article/post/content/main 的 div
        target = None
        for cand in soup.find_all(["article", "div", "section"]):
            cls = (cand.get("class") or []) + ([cand.get("id")] if cand.get("id") else [])
            cls_text = " ".join(cls).lower()
            if any(k in cls_text for k in ["article", "content", "post", "main", "entry"]):
                cand_score = len(cand.get_text(strip=True)) + len(cand.find_all("p")) * 30
                if target is None or cand_score > target[1]:
                    target = (cand, cand_score)
        if target:
            summary = str(target[0])
            soup2 = BeautifulSoup(summary, "lxml")
        else:
            soup2 = soup

    # 抽正文文本
    text = soup2.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text[:2500]  # 站内阅读只保留前 2500 字，足够看要点

    # 抽图片（最大 6 张）
    imgs = []
    for img in soup2.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not src:
            continue
        src = urljoin(base_url, src)
        if any(x in src.lower() for x in [".svg", ".gif", "1x1", "avatar", "logo", "icon"]):
            continue
        w = img.get("width"); h = img.get("height")
        if w and h and (int(w) < 120 or int(h) < 120):
            continue
        imgs.append(src)
        if len(imgs) >= 6:
            break
    return text.strip(), imgs


def fetch_article(url, timeout=20):
    """抓原文 HTML，返回 (title, text, [imgs])。失败返回 ("", "", [])。"""
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                        "Accept": "text/html,application/xhtml+xml",
                                        "Accept-Language": "zh-CN,zh;q=0.9"},
                         timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        return "", "", []

    soup = BeautifulSoup(r.text[:500000], "lxml")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    text, imgs = extract_main(r.text[:1000000], url)
    return title, text, imgs
