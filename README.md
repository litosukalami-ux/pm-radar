# 产品雷达 · PM Radar

每天自动抓 14 个信息源，交给大模型做完去重、打分、写摘要，最后只留下 6 条真正值得你看的。

## 一句话规格

> 抓取 → 跨源去重 → 按「我是谁、我要什么」打分 → 一句话摘要 → 手机可看的网页。

## 每天你会看到什么

- **今日必看**：AI 从几百条里挑出的 6 条，第 1 条是你今天最该点开的；
- **每条带两句话**：一句说清了什么，一句说为什么值得看；
- **同一条新闻只出现一次**：多家媒体报道会合并，不重复占位；
- **我的收藏**：收藏过的内容留在收藏页，收藏过的主题会持续优先推给你。

## 目录

```
pm-radar/
├── config.py            # 信息源、分类、AI 配置、你的求职画像
├── ai.py                # 去重 + 打分 + 摘要（调大模型）
├── radar.py             # 抓取 + 入库 + 生成看板
├── competitions.json    # 比赛日历（手动维护）
├── templates/index.html # 看板模板
├── docs/index.html      # 生成的看板（GitHub Pages 就发布这个目录）
├── data/radar.db        # SQLite，存历史与加工结果
├── .github/workflows/daily.yml  # 云端每天 8:00 自动跑
└── zhipu_key.txt        # 你的 API Key（不进 Git）
```

## 怎么用

```powershell
python radar.py           # 抓取 + AI 加工 + 重建看板
python radar.py --ai      # 不抓取，只给没加工过的条目补跑 AI
python radar.py --build   # 不联网，只用已有数据重建看板
```

双击 `run.bat` 等于执行第一条。桌面「产品雷达」图标直接用 Edge 应用窗口打开看板。

## AI 是怎么判断「值得看」的

判断标准不在代码里，在 `config.py` 的 `READER_PROFILE`：

- 5 分：直接影响求职或作品集，错过有实际损失；
- 4 分：行业里真正的新东西；
- 3 分：值得扫一眼；
- 2 分以下：不进入今日必看。

所以换阶段只改一段话就行。比如从「找实习」切到「做项目」，把画像改成
「我在做一个 AI 产品，需要竞品和用户反馈的方法」即可，不用动逻辑。

`FOCUS_TOPICS` 是长期关注方向，标题/摘要命中越多，排序越靠前。

## 去重怎么做的

两层：

1. **标题相似度**：字符二元组 + IDF 加权余弦。IDF 会压掉「训练营」「助手」
   这类在一堆标题里都出现的模板词，避免把「选股助手」和「手游助手」判成同一件事；
2. **模型判定**：同一次调用里让模型指出「这两条其实是同一件事」，
   处理换了措辞的转载和改写。

合并后只保留信息量最大的一条，其余标记重复、不再展示。

## 偏好系统

- 点卡片右下角**收藏**，会进入「我的收藏」，跨刷新保留；
- 收藏过的 `tags` / `topic` 会被记成兴趣信号，之后同类条目标注「为你」；
- 点标题会在本设备标记**已读**，卡片变淡。

收藏和已读存在浏览器 localStorage，所以只在本设备有效（手机和电脑各自独立）。
要跨设备同步，需要账号系统，当前版本没做。

## 每天自动跑

云端：`.github/workflows/daily.yml`，每天 UTC 00:00（北京时间 08:00）执行，
跑完自动把看板提交回仓库，GitHub Pages 随即更新。电脑关机也照常更新。

本地：原本的 Windows 计划任务 `PM-Radar-Daily` 已删除，避免和云端重复跑。
需要本地兜底时，用下面的命令重新注册即可。

```powershell
schtasks /Create /TN "PM-Radar-Daily" /SC DAILY /ST 08:30 /F `
  /TR "\"C:\Users\grey\Documents\Trae-CN\default\pm-radar\daily.bat\""
schtasks /Run    /TN "PM-Radar-Daily"
schtasks /Delete /TN "PM-Radar-Daily" /F
```

日志在 `logs/radar.log`。

## 换模型 / 换平台

`config.py` 的 `AI` 段只要兼容 OpenAI 的 `/chat/completions` 就能用：

```python
AI = {
    "key_env": "ZHIPU_API_KEY",
    "key_file": "zhipu_key.txt",
    "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "model": "glm-4-flash",
    "timeout": 120,
    "batch_size": 12,
}
```

Key 的读取顺序：环境变量 → `zhipu_key.txt`。仓库里不要提交 Key。

## 加一个信息源

先确认它有 RSS（浏览器打开 `/feed` 看到 XML），然后在 `config.py` 加一条：

```python
{"name": "某站点", "url": "https://example.com/feed",
 "category": "ai_product", "max_items": 25},
```

`category` 只能是这五个：`contest` / `pm` / `ai_product` / `vibe` / `tech`。

## 已知限制

- 只抓 RSS，不抓公众号、小红书、知乎（需要登录态，易失效）；
- 掘金 RSS 经常连接超时，失败会自动跳过，不影响其它源；
- 不做全文摘要，AI 只看标题 + 源摘要；
- 收藏/已读不跨设备；
- 模型不可用时会退化成「关键词 + 时间」排序，看板照常生成，不会白屏。
