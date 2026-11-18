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

    main_html = ""
    if HAS_READABILITY:
        doc = Document(html)
        try:
            summary = doc.summary(html_partial=True)
            soup2 = BeautifulSoup(summary, "lxml")
            main_html = summary
        except Exception:
            soup2 = BeautifulSoup("", "lxml")
    else:
        soup2 = BeautifulSoup("", "lxml")

    # 抽正文文本
    text = soup2.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)

    # Fallback：readability 找不到容器（< 100 字），就用全页可见文本
    if len(text) < 100:
        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        # 去掉明显导航/页脚行（短行 + 重复关键词）
        noise = ("登录", "注册", "首页", "关于我们", "联系我们", "版权所有", "隐私政策",
                 "服务条款", "下载", "APP", "官方社区", "Skills", "训练营")
        lines = text.split("\n")
        filtered = []
        nav_count = 0
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if len(ln) < 6 and any(n in ln for n in noise):
                nav_count += 1
                continue
            if nav_count > 15 and not any(c in ln for c in ["。", "！", "？", ":", "："]):
                # 导航密集区跳过
                continue
            filtered.append(ln)
        text = "\n".join(filtered)

    text = text[:3000]  # 站内阅读上限

    # 抽图片（最大 6 张）
    imgs = []
    for img in soup.find_all("img"):
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
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": urljoin(url, "/"),
        "Connection": "keep-alive",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception:
        return "", "", []

    soup = BeautifulSoup(r.text[:500000], "lxml")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    text, imgs = extract_main(r.text[:1000000], url)
    return title, text, imgs
