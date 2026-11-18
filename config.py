# -*- coding: utf-8 -*-
"""信息源与分类配置。

改这里就能增减信息源，不用动抓取逻辑。
category 必须是 CATEGORIES 里的 key。
"""

# 分类定义：key -> (显示名, 主色, 底色)
CATEGORIES = {
    "contest":     ("比赛与获奖", "#B45309", "#FEF3C7"),
    "pm":          ("产品经理",   "#6D28D9", "#EDE9FE"),
    "ai_product":  ("AI 产品动态", "#1D4ED8", "#DBEAFE"),
    "tech":        ("技术动态",   "#334155", "#E2E8F0"),
    "vibe":        ("Vibe Coding", "#047857", "#D1FAE5"),
}

# 主排序：看板里分类标签的先后顺序
CATEGORY_ORDER = ["contest", "pm", "ai_product", "vibe", "tech"]

# max_items: 每个源单次最多入库条数，防止一次灌进几百条
SOURCES = [
    # ---- 比赛与获奖 ----
    # TRAE 官方社区：赛道帖、获奖公示、vibe coding 实操分享都在这里
    {"name": "TRAE 官方社区", "url": "https://forum.trae.cn/latest.rss",
     "category": "contest", "max_items": 40},

    # ---- 产品经理（就业 / 方法论 / 拆解）----
    {"name": "人人都是产品经理", "url": "https://www.woshipm.com/feed",
     "category": "pm", "max_items": 60},

    # ---- AI 产品动态 ----
    {"name": "极客公园", "url": "https://www.geekpark.net/rss",
     "category": "ai_product", "max_items": 25},
    {"name": "爱范儿", "url": "https://www.ifanr.com/feed",
     "category": "ai_product", "max_items": 25},
    {"name": "量子位", "url": "https://www.qbitai.com/feed",
     "category": "ai_product", "max_items": 30},
    {"name": "Product Hunt", "url": "https://www.producthunt.com/feed",
     "category": "ai_product", "max_items": 30},

    # ---- Vibe Coding 实践 ----
    # 少数派：大量「我用 AI 做了个东西」的实操复盘
    {"name": "少数派", "url": "https://sspai.com/feed",
     "category": "vibe", "max_items": 30},
    # 小众软件：个人开发者做的小工具，选题参考价值高
    {"name": "小众软件", "url": "https://www.appinn.com/feed/",
     "category": "vibe", "max_items": 20},

    # ---- 技术动态 ----
    {"name": "InfoQ 中文", "url": "https://www.infoq.cn/feed",
     "category": "tech", "max_items": 30},
    {"name": "Solidot", "url": "https://www.solidot.org/index.rss",
     "category": "tech", "max_items": 30},
    {"name": "阮一峰的网络日志", "url": "https://www.ruanyifeng.com/blog/atom.xml",
     "category": "tech", "max_items": 15},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage",
     "category": "tech", "max_items": 30},
    {"name": "掘金", "url": "https://juejin.cn/rss",
     "category": "tech", "max_items": 30},
    {"name": "GitHub Trending", "url": "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml",
     "category": "tech", "max_items": 30},
]

# 关键词过滤：命中的条目会被打上 star 标记，在看板里高亮
# 针对「产品经理就业」这个当前最核心的诉求
STAR_KEYWORDS = [
    "产品经理", "PM", "求职", "面试", "简历", "校招", "实习", "秋招", "春招",
    "AI产品", "需求", "用户调研", "竞品", "复盘", "增长", "PRD",
]

# 命中这些词的条目直接丢弃（纯噪音）
BLOCK_KEYWORDS = [
    "招聘启事", "广告", "优惠券", "抽奖", "表情自取", "问题反馈",
]

# 每日精选配额：每个分类最多挑几条进「今日精选」。
# 这个数字就是这工具每天真正要你看的量，不是越多越好。
DAILY_QUOTA = {
    "contest":    3,   # 比赛与获奖，最缺，给足
    "pm":         4,   # 产品经理：求职/方法论/拆解
    "ai_product": 3,   # AI 产品动态
    "vibe":       3,   # 别人怎么用 vibe coding
    "tech":       2,   # 技术动态，扫一眼即可
}

# ---------------------------------------------------------------- AI

# 大模型配置。换模型改这里即可，兼容 OpenAI 接口格式的平台都能用。
AI = {
    # 环境变量名。GitHub Actions 里放 Secret，本地也可以直接写进 zhipu_key.txt
    "key_env": "ZHIPU_API_KEY",
    "key_file": "zhipu_key.txt",
    "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "model": "glm-4-flash",
    "timeout": 90,
    # 每次送给模型多少条。25 是 sweet spot：批次数够用、单次输出不截断
    "batch_size": 25,
    # 并发调模型的线程数。glm-4-flash 免费版建议 2，再多容易超时
    "concurrency": 2,
}

# 「我是谁、我要什么」。这段会放进提示词，直接决定 AI 怎么判断重要度。
# 换阶段（比如从求职切到做项目）时改这里。
READER_PROFILE = """
读者：暨南大学金融专业大二学生，目标是产品经理 / AI 产品经理方向，
正在找寒假实习，同时会用 AI 做自己的小产品（vibe coding）。

对他有用的内容，按优先级：
1. 能直接影响求职的：校招/实习/竞赛开放报名、面试与简历经验、真实招聘偏好；
2. 能写进作品集或简历的：可复刻的 AI 产品拆解、别人用 AI 做产品的完整复盘；
3. AI 产品行业里真正的新东西：新模型、新能力、新产品形态，以及它为什么重要；
4. 方法论：需求判断、竞品分析、用户调研、增长。

对他没用的：融资金额、股价、口水战、纯论文、纯技术实现细节、广告软文。
""".strip()

# 长期关注方向。AI 打标签时会对照这份清单，命中越多越靠前。
FOCUS_TOPICS = [
    "产品经理", "AI 产品", "求职", "实习", "校招", "竞赛",
    "AI 编程", "vibe coding", "智能体", "大模型",
]

# 每天真正给你看几条。宁可少而准，不要多而杂。
DAILY_PICKS = 6

# 低于这个重要度的条目不会进「今日必看」（1-5 分）
DAILY_MIN_IMPORTANCE = 3

# 同一个故事超过多少相似度就合并（0-1，越高越严格）
DEDUP_THRESHOLD = 0.6
