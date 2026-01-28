import streamlit as st
import asyncio
import math
import random
import re
import subprocess
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

st.set_page_config(page_title="KREAM 主價格即時報價（免登入）", page_icon="👟")

# 只在啟動時安裝 chromium（Streamlit Cloud 常需要）
@st.cache_resource
def ensure_playwright_browser():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=False, capture_output=True, text=True)
    except Exception:
        pass

ensure_playwright_browser()

def normalize_model(s: str) -> str:
    return (s or "").strip().upper()

def run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

def is_bad_status(status: int | None) -> bool:
    # 常見：被擋/限制/不穩
    return status in (403, 429, 500, 502, 503, 504) or (status is not None and status >= 500)

async def goto_with_retry(page, url: str, retries: int, wait_ms: int, debug_log: dict, tag: str):
    last_status = None
    for i in range(retries + 1):
        resp = await page.goto(url, wait_until="domcontentloaded")
        status = resp.status if resp else None
        last_status = status
        debug_log[f"{tag}_status_try{i+1}"] = status
        debug_log[f"{tag}_url_try{i+1}"] = page.url

        if status is not None and not is_bad_status(status):
            return resp

        # 退避 + 抖動
        await page.wait_for_timeout(wait_ms + int(random.uniform(200, 600)) + i * 400)
    return None

async def fetch_main_price(model_norm: str, timeout_ms: int, debug: bool, retries: int, warmup: bool):
    keyword = quote_plus(model_norm)
    home_url = "https://kream.co.kr/"
    search_url = f"https://kream.co.kr/search?keyword={keyword}&tab=products"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,zh-TW;q=0.6,zh;q=0.5",
                "Referer": "https://kream.co.kr/"
            }
        )
        page = await context.new_page()
        page.set_default_timeout(35000)
        page.set_default_navigation_timeout(timeout_ms)

        # 擋重資源加速
        async def block_heavy(route, request):
            if request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", block_heavy)

        debug_log = {}
        screenshot_bytes = None

        try:
            # 1) 預熱首頁：拿 cookie/session，降低 deep link 500 機率
            if warmup:
                await goto_with_retry(page, home_url, retries=1, wait_ms=600, debug_log=debug_log, tag="home")
                debug_log["home_title"] = await page.title()
                await page.wait_for_timeout(int(random.uniform(400, 800)))

            # 2) 進搜尋頁（含重試）
            resp = await goto_with_retry(page, search_url, retries=retries, wait_ms=900, debug_log=debug_log, tag="search")
            status = resp.status if resp else None
            debug_log["search_status_final"] = status
            debug_log["search_title"] = await page.title()

            # 讀 body（有時會是空白 / challenge / 500 空內容）
            body_head = ""
            try:
                body_head = (await page.inner_text("body"))[:400]
            except Exception:
                body_head = ""
            debug_log["search_body_head"] = body_head

            # 如果回 500/403/429 且 body 幾乎空 → 幾乎就是雲端 IP/站方限制
            if is_bad_status(status) and (not body_head.strip()):
                if debug:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                return {
                    "ok": False,
                    "error": f"⛔ KREAM 回傳 HTTP {status} 且頁面無內容：很像站方對雲端資料中心 IP 限制/不穩（不是 selector 問題）",
                    "debug": debug_log,
                    "screenshot": screenshot_bytes
                }

            # 3) 找第一個商品連結（多種方式）
            product_path = None

            # A) DOM
            try:
                if await page.locator('a[href^="/products/"]').count() > 0:
                    product_path = await page.locator('a[href^="/products/"]').first.get_attribute("href")
            except Exception:
                pass

            # B) JS
            if not product_path:
                try:
                    product_path = await page.evaluate(r"""
() => {
  const a = Array.from(document.querySelectorAll('a[href^="/products/"]'));
  return a[0]?.getAttribute("href") || null;
}
""")
                except Exception:
                    product_path = None

            # C) HTML regex
            if not product_path:
                try:
                    html = await page.content()
                    m = re.search(r'href="(/products/\d+)"', html)
                    if m:
                        product_path = m.group(1)
                except Exception:
                    pass

            if not product_path:
                if debug:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                return {
                    "ok": False,
                    "error": "⚠️ 搜尋頁抓不到商品連結（可能：型號無結果 / 站方限制雲端 IP / 搜尋頁結構改版）",
                    "debug": debug_log,
                    "screenshot": screenshot_bytes
                }

            product_url = "https://kream.co.kr" + product_path if product_path.startswith("/") else product_path

            # 4) 進商品頁（含重試）
            resp2 = await goto_with_retry(page, product_url, retries=retries, wait_ms=900, debug_log=debug_log, tag="product")
            status2 = resp2.status if resp2 else None
            debug_log["product_status_final"] = status2
            debug_log["product_title"] = await page.title()
            await page.wait_for_timeout(int(random.uniform(400, 800)))

            # 5) 抓主價：字體最大且符合 "xx,xxx원"
            price_text = await page.evaluate(r"""
() => {
  const re = /^[0-9]{1,3}(,[0-9]{3})*원$/;
  const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

  const candidates = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!isVisible(el)) continue;
    const t = (el.innerText || "").trim();
    if (!re.test(t)) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize || "0");
    if (fs >= 14) candidates.push({ t, fs });
  }
  candidates.sort((a,b) => b.fs - a.fs);
  return candidates[0]?.t || null;
}
""")

            if not price_text:
                body2 = ""
                try:
                    body2 = (await page.inner_text("body"))[:400]
                except Exception:
                    body2 = ""
                debug_log["product_body_head"] = body2

                if debug:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")

                return {
                    "ok": False,
                    "error": "⚠️ 進到商品頁但抓不到主價格（可能頁面改版 / 價格不顯示 / 或仍被限制）",
                    "debug": debug_log,
                    "screenshot": screenshot_bytes
                }

            krw = int(re.sub(r"[^0-9]", "", price_text))
            return {
                "ok": True,
                "model": model_norm,
                "krw_text": price_text,
                "krw": krw,
                "title": await page.title(),
                "url": page.url,
                "debug": debug_log
            }

        except PlaywrightTimeoutError:
            if debug:
                try:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                except Exception:
                    screenshot_bytes = None
            return {"ok": False, "error": "⚠️ Timeout（雲端連線慢 / 站方限制 / 頁面未渲染）", "debug": debug_log, "screenshot": screenshot_bytes}
        finally:
            await context.close()
            await browser.close()

@st.cache_data(ttl=120)
def fetch_cached(model_norm: str, timeout_ms: int, debug: bool, retries: int, warmup: bool):
    return run_async(fetch_main_price(model_norm, timeout_ms=timeout_ms, debug=debug, retries=retries, warmup=warmup))

# ---------------- UI ----------------
st.title("👟 KREAM 主價格即時報價（免登入）")
st.info("抓商品頁主價格（例：89,000원），不點購買，不會觸發登入。")

with st.sidebar:
    st.subheader("報價設定")
    krw_div = st.number_input("韓元 ÷", value=205.0, min_value=1.0, step=1.0)
    fee1 = st.number_input("係數 1", value=1.03, step=0.01)
    fee2 = st.number_input("係數 2", value=4.55, step=0.01)
    fee3 = st.number_input("係數 3", value=1.10, step=0.01)
    round_to = st.selectbox("進位", options=[10, 100], index=0)

    timeout_ms = st.slider("最大等待時間（秒）", 30, 120, 60) * 1000
    retries = st.selectbox("重試次數", options=[0, 1, 2, 3], index=2)
    warmup = st.checkbox("先開首頁預熱（推薦）", value=True)
    debug = st.checkbox("Debug（失敗顯示截圖/摘要）", value=True)

st.caption(f"公式：(KRW ÷ {krw_div:g}) × {fee1:g} × {fee2:g} × {fee3:g}（進位至 {round_to}）")

model_input = st.text_input("輸入商品型號", placeholder="例如: DD1391-100")

if st.button("🔍 抓主價格"):
    model_norm = normalize_model(model_input)
    if not model_norm:
        st.warning("請先輸入商品型號")
    else:
        with st.spinner("連線中..."):
            r = fetch_cached(model_norm, timeout_ms=timeout_ms, debug=debug, retries=retries, warmup=warmup)

        if not r.get("ok"):
            st.error(r.get("error", "未知錯誤"))
            if r.get("debug"):
                st.json(r["debug"])
            if debug and r.get("screenshot"):
                st.image(r["screenshot"], caption="Debug 截圖（判斷是否被擋/500 空白/挑戰頁）", use_container_width=True)
        else:
            krw = r["krw"]
            raw_twd = (krw / krw_div) * fee1 * fee2 * fee3
            twd = math.ceil(raw_twd / round_to) * round_to

            st.success("✅ 抓取成功")
            st.write(f"**{r['title']}**")
            st.metric("主價格（KRW）", r["krw_text"])
            st.metric("換算報價（TWD）", f"NT$ {twd:,}")
            st.write("商品頁：", r["url"])
            if debug and r.get("debug"):
                st.json(r["debug"])
