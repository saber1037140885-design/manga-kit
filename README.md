# manga-kit

从一句话到一页成品漫画。三个脚本，一条完整链路，全部本地跑。

中文圈现在的情况是：会写提示词的人排不出页，会排页的人出不好图，两头都缺教程。这套东西把中间那段接上了——分镜 → 出图 → 排版，每一步都有可调参数，每一步都能单独用。

```
剧本 / 参考图
   │
   ├─[img2src]──→ 这张图是谁画的、什么 tag、哪个作品     （反查）
   │
   ├─[comfy-gen]─→ 按分镜批量出图                        （出图）
   │
   └─[comic-layout]→ 加对白、加拟声、排成成品页           （排版）
```

---

## 三个工具

### 1. `tools/img2src.py` — 以图搜图

给一张图，回答三个问题：谁画的、什么作品、在哪个平台。

```bash
python tools/img2src.py <图片路径>
```

输出长这样：

```
[saucenao] 3 条  (2532ms)
   1.  94.55%  Danbooru  Creator: fymrie
      Source: pixiv #127381096 Material: original
      https://danbooru.donmai.us/post/show/8876074

pixiv 详情:
  #127381096  エロメイド   [illust]
    作者  FymriE (userId 73117898)
    发布  2025-02-18   尺寸 1400x2548   页数 1
    浏览 240,884 / 收藏 61,426   R18=0
    标签  キャラクター、巨乳、へそ、太もも、皮膚、金髪、魅惑の谷間
    https://www.pixiv.net/artworks/127381096
```

**免 key，不用注册**。SauceNAO 的匿名网页查询每天有额度，够个人用。

细节和踩过的坑写在 [`docs/反爬笔记.md`](docs/反爬笔记.md)。

### 2. `tools/comfy-gen.mjs` — 批量出图

读分镜里的 tag 块，调 ComfyUI 的 HTTP 接口出图。

```bash
node tools/comfy-gen.mjs --prompt "1girl, silver hair, ..." --out ./out --n 4
```

可选的库在 `lib/`：场景、服装、姿势、表情、负面词、种子表。用法见各自文件头部。

### 3. `tools/comic-layout.py` — 排版

把分镜图排成漫画页，自动画气泡、断行、加拟声。

```bash
python tools/comic-layout.py --src ./out --out ./page --mode 4koma
```

三种版式：

- `4koma` — 2×2 四格（默认，日式四格）
- `3rows` — 纵向三行，适合手机阅读
- `1page` — 一格一页，适合条漫

输出 `page_001.png`（1480×1934，A5 300dpi）+ `comic.pdf`。

**气泡语法**：图目录下放一个 `dialogue.json`

```json
{
  "001": [
    {"text": "今晚不打算解释。", "x": 0.62, "y": 0.18, "w": 0.34, "tail": "left"},
    {"sfx": "啪叽", "x": 0.30, "y": 0.72, "size": 90, "angle": -12}
  ]
}
```

`x` / `y` / `w` 都是格内相对比例（0–1）。`tail` 控制气泡小尾巴朝向：`left` / `right` / `none`。

跑一下自带的例子：

```bash
python tools/comic-layout.py --src examples --out ./page --mode 4koma
```

---

## 依赖

```bash
pip install pillow requests beautifulsoup4 curl_cffi
node --version   # 18+
```

ComfyUI 在 `127.0.0.1:8188` 跑着就行，不用装在同一个目录。

---

## 实测过的坑

这些是跑出来撞到的，不是查文档抄的。完整记录在 [`docs/反爬笔记.md`](docs/反爬笔记.md)。

| 现象 | 原因 | 怎么办 |
|---|---|---|
| SauceNAO 报「注册页打不开」 | 注册页有 Cloudflare JS 挑战 | 不用注册，匿名查询本来就免 key |
| pixiv 页面 403 `Just a moment...` | CF 只守 HTML 页面 | 走 `/ajax/illust/<id>` 接口，不设防 |
| 匹配到 47% 的「同名不同图」 | 60% 以下是噪音 | 脚本已内置阈值过滤 |
| 直连图站被重置 `10054` | 网络层 | 走本地代理，脚本支持 `--proxy` |
| `curl_cffi` 报 `files is not supported` | 新版改了 API | 用 `CurlMime`，参数叫 `data` 不叫 `content` |
| SauceNAO 偶发 502 / 521 | 源站抽风 | 脚本自动重试三次 |

---

## 目录

```
manga-kit/
├── tools/
│   ├── img2src.py        以图搜图（免 key）
│   ├── comfy-gen.mjs     批量出图
│   └── comic-layout.py   排版成页
├── lib/                  可复用的词库
│   ├── 场景库.md
│   ├── 服装库.md
│   ├── 姿势库.md           （含手部风险分级）
│   ├── 表情库.md
│   ├── 负面词库.json
│   ├── 手部修复.md
│   ├── 种子表.md
│   └── 验收标准.md
├── examples/             四格示例（跑得通的）
└── docs/
    └── 反爬笔记.md
```

---

## 许可

工具部分随便用。`lib/` 里的词库是从实际出图里攒的，改了不用跟我说。
