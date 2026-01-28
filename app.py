import streamlit as st
import asyncio
import math
import random
import re
import subprocess
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

st.set_page_config(page_title="KREAM 主價格即時報價（免登入）", page_icon="👟")

# 只在啟動時安裝（避免每次按鈕都裝）
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
    """避免某些環境下 asyncio.run 衝突"""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

async def fetch_main_price(model_norm: str, timeout_ms: int = 60000, debug: bool = False):
    keyword = quote_plus(model_norm)
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
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7,zh-TW;q=0.6,zh;q=0.5"
            },
        )
        page = await context.new_page()

        page.set_default_timeout(35000)
        page.set_default_navigation_timeout(timeout_ms)

        # 擋掉重資源（加速）
        async def block_heavy(route, request):
            if request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", block_heavy)

        screenshot_bytes = None

        try:
            # --- 1) 打開搜尋頁 ---
            resp = await page.goto(search_url, wait_until="domcontentloaded")
            status = resp.status if resp else None
            await page.wait_for_timeout(int(random.uniform(400, 800)))

            # 快速讀取頁面資訊（用來判斷被擋/無結果）
            title = await page.title()
            body_text = (await page.inner_text("body"))[:3000]  # 取前段避免太大

            # 常見「無結果」字樣（KREAM 可能會變，先抓大方向）
            if ("검색 결과" in body_text and "없" in body_text) or ("결과가 없습니다" in body_text) or ("No results" in body_text):
                return {
                    "ok": False,
                    "error": "❌ 找不到該型號（搜尋結果為空）",
                    "debug": {"status": status, "title": title, "url": page.url, "body_head": body_text[:500]}
                }

            # 常見「被擋/挑戰」字樣（不一定會有，但有就能秒判斷）
            lower = body_text.lower()
            if any(k in lower for k in ["access denied", "captcha", "robot", "forbidden", "blocked"]):
                if debug:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                return {
                    "ok": False,
                    "error": "⛔ 疑似被站方限制（雲端資料中心 IP 常見）",
                    "debug": {"status": status, "title": title, "url": page.url, "body_head": body_text[:500]},
                    "screenshot": screenshot_bytes
                }

            # --- 2) 取得第一個商品 URL：三層 fallback ---
            product_path = None

            # (A) DOM 直接抓
            if await page.locator('a[href^="/products/"]').count() > 0:
                href = await page.locator('a[href^="/products/"]').first.get_attribute("href")
                product_path = href

            # (B) JS 抓全站 links
            if not product_path:
                product_path = await page.evaluate(r"""
() => {
  const a = Array.from(document.querySelectorAll('a[href^="/products/"]'));
  return a[0]?.getAttribute("href") || null;
}
""")

            # (C) Regex 從 HTML 抓（就算 DOM 沒渲染，也可能抓得到）
            if not product_path:
                html = await page.content()
                m = re.search(r'href="(/products/\d+)"', html)
                if m:
                    product_path = m.group(1)

            if not product_path:
                # 這裡不再 timeout，直接回報：可能無結果或被擋
                if debug:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                return {
                    "ok": False,
                    "error": "⚠️ 搜尋頁抓不到商品連結（可能：型號無結果 / 站方限制雲端 IP / 頁面結構變更）",
                    "debug": {"status": status, "title": title, "url": page.url, "body_head": body_text[:500]},
                    "screenshot": screenshot_bytes
                }

            if product_path.startswith("/"):
                product_url = "https://kream.co.kr" + product_path
            else:
                product_url = product_path

            # --- 3) 打開商品頁 ---
            resp2 = await page.goto(product_url, wait_until="domcontentloaded")
            status2 = resp2.status if resp2 else None
            await page.wait_for_timeout(int(random.uniform(400, 800)))

            title2 = await page.title()

            # --- 4) 抓主價格（字體最大且符合 xx,xxx원）---
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
                body2 = (await page.inner_text("body"))[:3000]
                if debug:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                return {
                    "ok": False,
                    "error": "⚠️ 進到商品頁但抓不到主價格（可能被擋、或此商品頁不顯示價格）",
                    "debug": {"status": status2, "title": title2, "url": page.url, "body_head": body2[:500]},
                    "screenshot": screenshot_bytes
                }

            krw = int(re.sub(r"[^0-9]", "", price_text))
            return {
                "ok": True,
                "model": model_norm,
                "krw_text": price_text,
                "krw": krw,
                "title": title2,
                "url": page.url,
                "debug": {"status": status2}
            }

        except PlaywrightTimeoutError:
            if debug:
                try:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                except Exception:
                    screenshot_bytes = None
            return {
                "ok": False,
                "error": "⚠️ Timeout（雲端慢 / 站方限制 / 頁面未渲染）",
                "debug": {"url": page.url, "title": await page.title()},
                "screenshot": screenshot_bytes
            }
        except Exception as e:
            if debug:
                try:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                except Exception:
                    screenshot_bytes = None
            return {
                "ok": False,
                "error": f"⚠️ 錯誤：{type(e).__name__}: {str(e)}",
                "debug": {"url": page.url, "title": await page.title()},
                "screenshot": screenshot_bytes
            }
        finally:
            await context.close()
            await browser.close()

@st.cache_data(ttl=120)
def fetch_cached(model_norm: str, timeout_ms: int, debug: bool):
    return run_async(fetch_main_price(model_norm, timeout_ms=timeout_ms, debug=debug))

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
    debug = st.checkbox("Debug 模式（失敗顯示截圖/摘要）", value=True)

st.caption(f"公式：(KRW ÷ {krw_div:g}) × {fee1:g} × {fee2:g} × {fee3:g}（進位至 {round_to}）")

model_input = st.text_input("輸入商品型號", placeholder="例如: DD1391-100")

if st.button("🔍 抓主價格"):
    model_norm = normalize_model(model_input)
    if not model_norm:
        st.warning("請先輸入商品型號")
    else:
        with st.spinner("連線中..."):
            r = fetch_cached(model_norm, timeout_ms=timeout_ms, debug=debug)

        if not r.get("ok"):
            st.error(r.get("error", "未知錯誤"))
            dbg = r.get("debug", {})
            if dbg:
                st.write("Debug：", dbg)
            if debug and r.get("screenshot"):
                st.image(r["screenshot"], caption="Debug 截圖（判斷是否被擋/挑戰頁/無結果）", use_container_width=True)
        else:
            krw = r["krw"]
            raw_twd = (krw / krw_div) * fee1 * fee2 * fee3
            twd = math.ceil(raw_twd / round_to) * round_to

            st.success("✅ 抓取成功")
            st.write(f"**{r['title']}**")
            st.metric("主價格（KRW）", r["krw_text"])
            st.metric("換算報價（TWD）", f"NT$ {twd:,}")
            st.write("商品頁：", r["url"])
