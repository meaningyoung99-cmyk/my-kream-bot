import streamlit as st
import asyncio
import math
import random
import re
import subprocess
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ----------------------------
# Streamlit 基本設定
# ----------------------------
st.set_page_config(page_title="KREAM 主價格即時報價（免登入）", page_icon="👟")

# ----------------------------
# 只在啟動時安裝 Playwright Chromium（避免每次按按鈕都裝）
# ----------------------------
@st.cache_resource
def ensure_playwright_browser():
    # Streamlit Cloud 第一次啟動通常需要安裝瀏覽器
    # 若你已經在 build 階段安裝，這段也不會造成太大影響
    try:
        subprocess.run(["playwright", "install", "chromium"], check=False, capture_output=True, text=True)
    except Exception:
        # 不要讓安裝失敗直接把整個 app 打掛，後面 Playwright 會再報錯提示你缺什麼
        pass

ensure_playwright_browser()

# ----------------------------
# 工具：型號正規化
# ----------------------------
def normalize_model(s: str) -> str:
    return (s or "").strip().upper()

# ----------------------------
# 抓主價格（免登入）
# ----------------------------
async def fetch_kream_main_price(model_norm: str, timeout_ms: int = 60000, debug: bool = False):
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
            }
        )
        page = await context.new_page()

        # 雲端通常慢：調高 timeout
        page.set_default_timeout(35000)
        page.set_default_navigation_timeout(timeout_ms)

        # ✅ 擋掉重資源（大幅降低 Timeout）
        async def block_heavy(route, request):
            if request.resource_type in ("image", "media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", block_heavy)

        screenshot_bytes = None

        try:
            # 1) 進搜尋頁（不要用 networkidle）
            await page.goto(search_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(int(random.uniform(500, 900)))

            # 2) 找第一個商品連結（最穩）
            first_product = page.locator('a[href^="/products/"]').first

            # 有時候 KREAM 會延遲渲染，給它一點時間
            await first_product.wait_for(timeout=25000)
            await first_product.click()

            # 3) 等到商品頁
            await page.wait_for_url("**/products/**", timeout=30000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(int(random.uniform(500, 900)))

            # 4) 抓主價：找字體最大且符合 "xx,xxx원" 的元素
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

            title = await page.title()
            url = page.url

            if not price_text:
                # Debug 截圖（有助判斷是否被擋、或頁面變了）
                if debug:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                return {
                    "ok": False,
                    "error": "❌ 找不到主價格（可能頁面改版、或雲端 IP 被限制）",
                    "url": url,
                    "title": title,
                    "screenshot": screenshot_bytes
                }

            krw = int(re.sub(r"[^0-9]", "", price_text))
            return {
                "ok": True,
                "model": model_norm,
                "krw_text": price_text,
                "krw": krw,
                "title": title,
                "url": url
            }

        except PlaywrightTimeoutError:
            if debug:
                try:
                    screenshot_bytes = await page.screenshot(full_page=True, type="png")
                except Exception:
                    screenshot_bytes = None
            return {
                "ok": False,
                "error": "⚠️ 連線逾時（Timeout）。常見原因：雲端連線慢 / 站方限制資料中心 IP / selector 沒出現",
                "url": page.url,
                "title": await page.title(),
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
                "error": f"⚠️ 抓取失敗：{type(e).__name__}: {str(e)}",
                "url": page.url,
                "title": await page.title(),
                "screenshot": screenshot_bytes
            }
        finally:
            await context.close()
            await browser.close()

# ----------------------------
# 同步包裝（給 Streamlit button 用）
# ----------------------------
@st.cache_data(ttl=120)
def get_main_price_cached(model_norm: str, timeout_ms: int, debug: bool):
    return asyncio.run(fetch_kream_main_price(model_norm, timeout_ms=timeout_ms, debug=debug))

# ----------------------------
# UI
# ----------------------------
st.title("👟 KREAM 主價格即時報價（免登入）")
st.info("抓商品頁主價格（例如 89,000원），不點購買，不會觸發登入。")

with st.sidebar:
    st.subheader("報價設定")
    krw_div = st.number_input("韓元 ÷", value=205.0, min_value=1.0, step=1.0)
    fee1 = st.number_input("係數 1", value=1.03, step=0.01)
    fee2 = st.number_input("係數 2", value=4.55, step=0.01)
    fee3 = st.number_input("係數 3", value=1.10, step=0.01)
    round_to = st.selectbox("進位", options=[10, 100], index=0)
    timeout_ms = st.slider("最大等待時間（秒）", 30, 120, 60) * 1000
    debug = st.checkbox("Debug 模式（出錯顯示截圖）", value=False)

st.caption(f"公式：(KRW ÷ {krw_div:g}) × {fee1:g} × {fee2:g} × {fee3:g}（進位至 {round_to}）")

model_input = st.text_input("輸入商品型號", placeholder="例如: DD1391-100")

if st.button("🔍 抓主價格"):
    model_norm = normalize_model(model_input)
    if not model_norm:
        st.warning("請先輸入商品型號")
    else:
        with st.spinner("連線中..."):
            r = get_main_price_cached(model_norm, timeout_ms=timeout_ms, debug=debug)

        if not r.get("ok"):
            st.error(r.get("error", "未知錯誤"))
            st.write("目前頁面：", r.get("url", ""))
            st.write("頁面標題：", r.get("title", ""))

            if debug and r.get("screenshot"):
                st.image(r["screenshot"], caption="Debug 截圖（有助判斷是否被擋/挑戰頁）", use_container_width=True)
        else:
            krw = r["krw"]
            raw_twd = (krw / krw_div) * fee1 * fee2 * fee3
            twd = math.ceil(raw_twd / round_to) * round_to

            st.success("✅ 抓取成功")
            st.write(f"**{r['title']}**")
            st.metric("主價格（KRW）", r["krw_text"])
            st.metric("換算報價（TWD）", f"NT$ {twd:,}")
            st.write("商品頁：", r["url"])
