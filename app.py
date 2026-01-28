import streamlit as st
import asyncio
import math
import random
import re
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

st.set_page_config(page_title="KREAM 主價格報價", page_icon="👟")

# ✅ 建議：不要在程式裡安裝瀏覽器（部署/建置階段做）
# os.system("playwright install chromium")

def normalize_model(s: str) -> str:
    return s.strip().upper()

@st.cache_data(ttl=120)
def cached_main_price(model_norm: str):
    return asyncio.run(get_kream_main_price(model_norm))

async def get_kream_main_price(model_norm: str):
    keyword = quote_plus(model_norm)
    search_url = f"https://kream.co.kr/search?keyword={keyword}&tab=products"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )
        context = await browser.new_context(
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # 等搜尋結果出現
            await page.wait_for_selector(".search_result_item", timeout=20000)

            # 點第一筆商品
            await page.locator(".search_result_item").first.click()
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(random.uniform(0.8, 1.5))

            # ✅ 用「字體最大且符合 xxx원」抓主價格（通常就是右側大字價格）
            price_text = await page.evaluate(r"""
() => {
  const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const re = /^[0-9]{1,3}(,[0-9]{3})*원$/;

  const candidates = [];
  const all = document.querySelectorAll("body *");

  for (const el of all) {
    if (!isVisible(el)) continue;
    const t = (el.innerText || "").trim();
    if (!t) continue;
    if (t.length > 20) continue;
    if (!re.test(t)) continue;

    const style = window.getComputedStyle(el);
    const fs = parseFloat(style.fontSize || "0");
    if (!fs || fs < 14) continue;

    candidates.push({ t, fs });
  }

  candidates.sort((a, b) => b.fs - a.fs);
  return candidates[0]?.t || null;
}
""")

            if not price_text:
                return {"ok": False, "error": "❌ 找不到主價格（可能頁面改版或該商品暫無顯示價格）"}

            krw = int(re.sub(r"[^0-9]", "", price_text))

            # 你的公式：(韓元 / 205) * 1.03 * 4.55 * 1.1 (進位至十位)
            raw_twd = (krw / 205) * 1.03 * 4.55 * 1.1
            twd = math.ceil(raw_twd / 10) * 10

            title = await page.title()
            product_url = page.url

            return {
                "ok": True,
                "model": model_norm,
                "krw_text": price_text,
                "krw": krw,
                "twd": twd,
                "title": title,
                "url": product_url
            }

        except PlaywrightTimeoutError:
            return {"ok": False, "error": "⚠️ 連線逾時（Timeout），請稍後再試"}
        except Exception as e:
            return {"ok": False, "error": f"⚠️ 抓取失敗：{type(e).__name__}: {str(e)}"}
        finally:
            await context.close()
            await browser.close()

# ---------- UI ----------
st.title("👟 KREAM 主價格即時報價（免登入）")
st.info("抓商品頁主價格（例如 89,000원），不點購買，不會觸發登入。")
st.caption("公式：(韓元 ÷ 205) × 1.03 × 4.55 × 1.1（進位至十位）")

model_input = st.text_input("輸入商品型號", placeholder="例如: DD1391-100")

if st.button("🔍 抓主價格"):
    model_norm = normalize_model(model_input)
    if not model_norm:
        st.warning("請先輸入商品型號")
    else:
        with st.spinner("連線中..."):
            r = cached_main_price(model_norm)

        if not r.get("ok"):
            st.error(r.get("error", "未知錯誤"))
        else:
            st.success("✅ 抓取成功")
            st.write(f"**{r['title']}**")
            st.metric("主價格（KRW）", r["krw_text"])
            st.metric("換算報價（TWD）", f"NT$ {r['twd']:,}")
            st.write("商品頁：", r["url"])
