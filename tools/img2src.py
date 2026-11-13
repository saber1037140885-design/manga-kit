#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
img2src.py —— 以图搜图：把一张图丢给几个反向检索站，回来说它在哪个平台、哪张作品。

用法：
  python scripts/img2src.py <图片路径> [--saucenao-key KEY] [--json]

引擎（按信噪比排序）：
  1. ascii2d  —— 免 key，日系图强，直接命中 pixiv / twitter 原帖，给作者和链接
  2. iqdb     —— 免 key，老库但稳，主要覆盖 danbooru / konachan
  3. saucenao —— 要免费 key（注册即得，每天 100 次），pixiv / twitter 命中率最高

输出：每个引擎一段，命中就打印 相似度 / 标题 / 作者 / 链接 / 平台。
"""
import argparse
import re
import io
import json
import os
import sys
import time

import requests
from curl_cffi import requests as creq, CurlMime  # TLS 指纹伪装，专治 Cloudflare 403
from PIL import Image
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8,zh-CN;q=0.7",
    "Accept-Encoding": "gzip, deflate",
}

# 这台机器直连 iqdb / ascii2d 会被重置（10054），必须走本地代理。
# 默认用系统代理 127.0.0.1:7897；用 --proxy 换端口，用 --no-proxy 强制直连。
PROXY_URL = os.environ.get("IMG2SRC_PROXY", "http://127.0.0.1:7897")
PROXIES = {"http": PROXY_URL, "https": PROXY_URL}

SESSION = requests.Session()
SESSION.trust_env = False

SITE_NAME = {
    "www.pixiv.net": "pixiv",
    "pixiv.net": "pixiv",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "danbooru.donmai.us": "Danbooru",
    "konachan.com": "Konachan",
    "yande.re": "yande.re",
    "gelbooru.com": "Gelbooru",
    "safebooru.org": "Safebooru",
    "deviantart.com": "DeviantArt",
    "artstation.com": "ArtStation",
    "fanbox.cc": "FANBOX",
    "skeb.jp": "Skeb",
}


def site_of(url: str) -> str:
    if not url:
        return "?"
    low = url.lower()
    for dom, name in SITE_NAME.items():
        if dom in low:
            return name
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.replace("www.", "")
        return host or "?"
    except Exception:
        return "?"


def prep_image(path: str, max_side=1500) -> tuple:
    """统一转成 JPEG 字节流，压到搜索引擎能吃的尺寸。"""
    im = Image.open(path)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    if max(w, h) > max_side:
        r = max_side / max(w, h)
        im = im.resize((int(w * r), int(h * r)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue(), os.path.basename(path), im.size


def post_file(url, img_bytes, fname, data=None, timeout=40, referer=None, field="file"):
    """curl_cffi 新版不支持 files=，要用 CurlMime 拼 multipart。"""
    mp = CurlMime()
    mp.addpart(name=field, data=img_bytes, filename=fname,
               content_type="image/jpeg")
    hdr = dict(HEADERS)
    if referer:
        hdr["Referer"] = referer
    try:
        return creq.post(url, multipart=mp, data=data, headers=hdr,
                         timeout=timeout, proxies=PROXIES,
                         impersonate="chrome124")
    finally:
        mp.close()



# ---------------- ascii2d ----------------
def search_ascii2d(img_bytes, fname, mode="color", timeout=40):
    """mode: color（色合检索，按整体配色）/ texture（特征检索，按线稿纹理）"""
    url = "https://ascii2d.net/search/file"
    files = {"file": (fname, img_bytes, "image/jpeg")}
    data = {"search[" + mode + "]": ""}
    r = post_file(url, img_bytes, fname, data=data, timeout=timeout)
    if r.status_code != 200:
        return {"engine": "ascii2d/" + mode, "ok": False,
                "error": f"HTTP {r.status_code}"}
    soup = BeautifulSoup(r.text, "html.parser")

    boxes = soup.select(".item-box")
    hits = []
    for box in boxes:
        # 第一个 box 通常是自己上传的预览图，没有外部链接
        a = box.select_one("a.item-link") or box.select_one(".item-link a")
        href = a.get("href") if a else None
        if not href:
            continue
        title_el = box.select_one(".item-title")
        title = title_el.get_text(" ", strip=True) if title_el else ""

        details = []
        for d in box.select(".item-detail"):
            t = d.get_text(" ", strip=True)
            if t:
                details.append(t)

        # 外部缩略图（结果图）
        ext = ""
        for img in box.select("img"):
            src = img.get("src") or ""
            if "data:image" not in src and src.startswith("http"):
                ext = src
                break

        hits.append({
            "url": href,
            "site": site_of(href),
            "title": title,
            "detail": " | ".join(details),
            "thumb": ext,
        })
    return {"engine": "ascii2d/" + mode, "ok": True, "hits": hits}


# ---------------- IQDB ----------------
def search_iqdb(img_bytes, fname, timeout=40):
    files = {"file": (fname, img_bytes, "image/jpeg")}
    data = {"service": "1"}  # 1 = 全部服务
    r = post_file("https://iqdb.org/", img_bytes, fname, data={"service": "1"}, timeout=180)
    if r.status_code != 200:
        return {"engine": "iqdb", "ok": False, "error": f"HTTP {r.status_code}"}
    soup = BeautifulSoup(r.text, "html.parser")

    hits = []
    tbl = soup.select_one("#pages") or soup.select_one("table")
    if tbl:
        for tr in tbl.select("tr"):
            tds = tr.select("td")
            if len(tds) < 4:
                continue
            sim = tds[0].get_text(strip=True)
            if "%" not in sim:
                continue
            a = tds[2].select_one("a") if len(tds) > 2 else None
            href = a.get("href") if a else None
            if not href:
                continue
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://iqdb.org" + href
            hits.append({
                "similarity": sim,
                "url": href,
                "site": site_of(href),
                "title": tds[2].get_text(" ", strip=True),
                "detail": tds[3].get_text(" ", strip=True) if len(tds) > 3 else "",
            })
    return {"engine": "iqdb", "ok": True, "hits": hits}


def search_saucenao(img_bytes, fname, api_key=None, timeout=70):
    """免 key 走网页表单（匿名额度按 IP 计）。有 key 才走 JSON API。"""
    if api_key:
        files = {"file": (fname, img_bytes, "image/jpeg")}
        data = {"output_type": "2", "api_key": api_key, "numres": "8", "database": "999"}
        r = SESSION.post("https://saucenao.com/search.php", files=files, data=data,
                         headers={"User-Agent": UA}, timeout=timeout, proxies=PROXIES)
        if r.status_code != 200:
            return {"engine": "saucenao", "ok": False, "error": f"HTTP {r.status_code}"}
        try:
            j = r.json()
        except Exception:
            return {"engine": "saucenao", "ok": False, "error": "non-json response"}
        if j.get("header", {}).get("status", 0) < 0:
            return {"engine": "saucenao", "ok": False,
                    "error": j["header"].get("message", "api error")}
        hits = []
        for res in j.get("results", []):
            h, d = res.get("header", {}), res.get("data", {})
            try:
                simv = float(h.get("similarity"))
            except Exception:
                simv = 0.0
            if simv < 55:
                continue
            hits.append({
                "similarity": f"{simv:.1f}%",
                "site": site_of((d.get("ext_urls") or [""])[0]),
                "title": d.get("title") or d.get("jp_name") or d.get("source") or "",
                "author": d.get("author_name") or d.get("member_name") or "",
                "url": (d.get("ext_urls") or [""])[0],
                "pixiv_id": d.get("pixiv_id", ""),
                "twitter_id": d.get("twitter_user_handle", ""),
            })
        return {"engine": "saucenao", "ok": True, "mode": "api", "hits": hits}

    # --- 匿名网页查询 ---
    r = post_file("https://saucenao.com/search.php", img_bytes, fname,
                  data={"db": "999", "numres": "8"}, timeout=timeout,
                  referer="https://saucenao.com/")
    if r.status_code != 200:
        return {"engine": "saucenao", "ok": False, "error": f"HTTP {r.status_code}"}
    soup = BeautifulSoup(r.text, "html.parser")
    hits = []
    for blk in soup.select(".result"):
        title_el = blk.select_one(".resulttitle")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        cols = [c.get_text(" ", strip=True) for c in blk.select(".resultcontentcolumn")]
        # 只认外部链接（跳过 saucenao 自己的预览图链接）
        urls = []
        for a in blk.select("a"):
            href = a.get("href") or ""
            if href.startswith("http") and "saucenao.com" not in href:
                urls.append(href)
        if not urls and not title:
            continue
        m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", blk.get_text(" ", strip=True))
        simv = float(m.group(1)) if m else 0.0
        if simv and simv < 60:
            continue  # 60% 以下基本是误配，别当真
        hits.append({
            "similarity": (m.group(1) + "%") if m else "",
            "site": site_of(urls[0] if urls else ""),
            "title": title[:100],
            "detail": " | ".join(cols[:3])[:160],
            "url": urls[0] if urls else "",
            "all_urls": urls[:4],
        })
    return {"engine": "saucenao", "ok": True, "mode": "web", "hits": hits}

# ---------------- pixiv 元数据补全 ----------------
PIXIV_REFERER = "https://www.pixiv.net/"


def pixiv_ajax(pid, timeout=30):
    """pixiv 的 HTML 页面被 Cloudflare 挡死，但 /ajax/illust/ 接口不挡——直接给 JSON。
    original 直链在未登录时返回 None，这是 pixiv 的限制，不是解析问题。"""
    url = f"https://www.pixiv.net/ajax/illust/{pid}"
    hdr = dict(HEADERS)
    hdr["Referer"] = PIXIV_REFERER
    hdr["Accept"] = "application/json"
    hdr["Accept-Language"] = "ja,en-US;q=0.9"
    try:
        r = creq.get(url, headers=hdr, timeout=timeout, proxies=PROXIES,
                     impersonate="chrome124")
        if r.status_code != 200:
            return {"id": pid, "error": f"HTTP {r.status_code}"}
        j = r.json()
        if j.get("error"):
            return {"id": pid, "error": j.get("message") or "api error"}
        b = j.get("body") or {}
        t = b.get("tags") or {}
        return {
            "id": b.get("illustId") or pid,
            "title": b.get("illustTitle") or "",
            "author": b.get("userName") or "",
            "user_id": b.get("userId") or "",
            "date": (b.get("uploadDate") or "")[:10],
            "size": f'{b.get("width")}x{b.get("height")}',
            "pages": b.get("pageCount"),
            "views": b.get("viewCount"),
            "bookmarks": b.get("bookmarkCount"),
            "r18": b.get("xRestrict"),
            "type": {0: "illust", 1: "manga", 2: "ugoira"}.get(b.get("illustType"), "?"),
            "tags": [x.get("tag") for x in (t.get("tags") or [])][:14],
            "original": (b.get("urls") or {}).get("original"),
            "url": f"https://www.pixiv.net/artworks/{b.get('illustId') or pid}",
        }
    except Exception as e:
        return {"id": pid, "error": f"{type(e).__name__}: {e}"}


def extract_pixiv_ids(payload):
    """从 SauceNAO 的命中里把 pixiv 作品 ID 抠出来。"""
    ids = []
    for r in payload.get("results", []):
        if not r.get("ok"):
            continue
        for h in r.get("hits", []):
            cand = []
            if h.get("pixiv_id"):
                cand.append(str(h["pixiv_id"]))
            blob = " ".join(str(h.get(k, "")) for k in
                            ("detail", "title", "url", "detail"))
            cand += re.findall(r"pixiv ID:\s*(\d+)", blob)
            cand += re.findall(r"pixiv\s*#\s*(\d+)", blob)      # "Source: pixiv #127381096"
            cand += re.findall(r"illust_id=(\d+)", blob)
            cand += re.findall(r"artworks/(\d+)", blob)
            for c in cand:
                if c.isdigit() and c not in ids:
                    ids.append(c)
    return ids


# ---------------- 汇总 ----------------
def run(path, saucenao_key=None, use=("saucenao", "ascii2d-color", "ascii2d-texture")):
    img_bytes, fname, size = prep_image(path)
    results = []
    plan = []
    if "ascii2d-color" in use:
        plan.append(("ascii2d/color", lambda: search_ascii2d(img_bytes, fname, "color")))
    if "ascii2d-texture" in use:
        plan.append(("ascii2d/texture", lambda: search_ascii2d(img_bytes, fname, "texture")))
    if "iqdb" in use:
        plan.append(("iqdb", lambda: search_iqdb(img_bytes, fname)))
    if "saucenao" in use:
        # 没 key 就走匿名网页查询，一样能出结果
        plan.append(("saucenao", lambda: search_saucenao(img_bytes, fname, saucenao_key or None)))

    for label, fn in plan:
        t0 = time.time()
        last = None
        for attempt in range(3):
            try:
                r = fn()
                last = r
                if r.get("ok"):
                    break
                err = str(r.get("error", ""))
                # 502/521/503 是 SauceNAO 源站抽风，隔几秒再打一次
                if any(c in err for c in ("502", "521", "503", "504")):
                    time.sleep(3 * (attempt + 1))
                    continue
                break
            except Exception as e:
                last = {"engine": label, "ok": False,
                        "error": f"{type(e).__name__}: {e}"}
                time.sleep(2 * (attempt + 1))
        r = last
        r["ms"] = int((time.time() - t0) * 1000)
        results.append(r)
        time.sleep(1.0)  # 别打太快


    payload = {"file": os.path.abspath(path), "size": size, "results": results}

    # 命中里有 pixiv ID 的，自动拿 ajax 接口补全标题/作者/日期/收藏
    if "pixiv" in use or True:
        pids = extract_pixiv_ids(payload)
        if pids:
            enriched = []
            for pid in pids[:4]:
                d = pixiv_ajax(pid)
                enriched.append(d)
                time.sleep(0.6)
            payload["pixiv"] = enriched

    return payload



def verdict(payload):
    """把人能看的一句话结论抽出来。"""
    pixiv, tw, other = [], [], []
    for r in payload["results"]:
        if not r.get("ok"):
            continue
        for h in r.get("hits", []):
            site = h.get("site", "")
            url = h.get("url", "")
            if site == "pixiv":
                pixiv.append(h)
            elif site.startswith("X"):
                tw.append(h)
            elif url:
                other.append(h)
    return pixiv, tw, other


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--saucenao-key", default=os.environ.get("SAUCENAO_KEY", ""))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--proxy", default=PROXY_URL, help="http 代理，默认 127.0.0.1:7897")
    ap.add_argument("--no-proxy", action="store_true", help="强制直连")
    a = ap.parse_args()

    global PROXIES
    if a.no_proxy:
        PROXIES = {"http": None, "https": None}
    elif a.proxy:
        PROXIES = {"http": a.proxy, "https": a.proxy}

    if not os.path.exists(a.image):
        print("文件不存在:", a.image)
        sys.exit(2)

    payload = run(a.image, a.saucenao_key or None)
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"图: {os.path.basename(a.image)}  {payload['size'][0]}x{payload['size'][1]}")
    print("=" * 60)

    for r in payload["results"]:
        eng = r["engine"]
        if not r.get("ok"):
            print(f"[{eng}] 失败: {r.get('error')}  ({r['ms']}ms)")
            continue
        hits = r.get("hits", [])
        print(f"[{eng}] {len(hits)} 条  ({r['ms']}ms)")
        if not hits:
            print("   没有命中——这张可能没被这几家收录")
            continue
        for i, h in enumerate(hits[:5], 1):
            sim = h.get("similarity", "")
            site = h.get("site", "?")
            title = (h.get("title") or "")[:70]
            who = h.get("author") or h.get("detail", "")[:50]
            url = h.get("url", "")
            print(f"   {i}. {sim:>7s}  {site:<14s} {title}")
            if who:
                print(f"      {who}")
            if url:
                print(f"      {url}")
        print()

    p, t, o = verdict(payload)
    print("=" * 60)

    # pixiv 详情（ajax 接口补全，不碰被 CF 挡的 HTML 页面）
    px = payload.get("pixiv") or []
    if px:
        print("pixiv 详情:")
        for d in px:
            if d.get("error"):
                print(f"  #{d.get('id')}  取不到: {d['error']}")
                continue
            print(f"  #{d['id']}  {d['title']}   [{d.get('type')}]")
            print(f"    作者  {d['author']} (userId {d['user_id']})")
            print(f"    发布  {d['date']}   尺寸 {d['size']}   页数 {d['pages']}")
            print(f"    浏览 {d['views']:,} / 收藏 {d['bookmarks']:,}   R18={d['r18']}")
            if d.get("tags"):
                print(f"    标签  {'、'.join(d['tags'])}")
            print(f"    {d['url']}")
            if not d.get("original"):
                print("    原图直链: 未登录不给（pixiv 限制，非解析失败）")
        print()

    print("结论:")
    if px and not p:
        print(f"  ★ pixiv 命中（SauceNAO 的 source 字段带出 ID {px[0].get('id')}）")
        print(f"     {px[0].get('url')}")
    if p:
        print(f"  ★ pixiv 命中 {len(p)} 条 —— 优先看第一条")
        for h in p[:3]:
            print("     ", h.get("url"))
    if t:
        print(f"  ★ X (Twitter) 命中 {len(t)} 条")
        for h in t[:3]:
            print("     ", h.get("url"))
    if not p and not t and not px:
        print("  没命中 pixiv / X。可能：图太新、被裁过、或被压缩改头换面。")



if __name__ == "__main__":
    main()
