import streamlit as st
import asyncio
import os
import math
import random
from playwright.async_api import async_playwright

# 1. 自動安裝瀏覽器
os.system("playwright install chromium")

st.set_page_config(page_title="KREAM 代購報價系統", page_icon="👟")

async def get_kream_prices(model):
    async with async_playwright() as p:
        # 啟動防偵測模式
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            # 直接跳轉搜尋結果的第一筆，減少搜尋動作
            url = f"https://kream.co.kr/search?keyword={model}&tab=products"
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # 隨機停留模擬真人
            await asyncio.sleep(random.uniform(3, 5))

            # 嘗試點擊商品
            product_link = page.locator(".search_result_item").first
            if await product_link.is_visible():
                await product_link.click()
            else:
                return "❌ 找不到該型號，請檢查型號是否輸入正確（例如：DD1391-100）。"

            # 檢查是否有強制登入彈窗
            await asyncio.sleep(2)
            if await page.locator(".layer_login").is_visible():
                return "⚠️ KREAM 目前要求登入才能查看詳細價格，請稍後再試。"

            # 點擊購買按鈕
            await page.wait_for_selector(".btn_division.buy", timeout=20000)
            await page.click(".btn_division.buy")
            
            # 抓取清單
            await page.wait_for_selector(".select_unit", timeout=20000)
            items = await page.query_selector_all(".select_unit")
            
            data = []
            for item in items:
                size = await (await item.query_selector(".size")).inner_text()
                price_raw = await (await item.query_selector(".price")).inner_text()
                if "원" in price_raw:
                    krw = int(price_raw.replace(",", "").replace("원", "").strip())
                    # 你的公式：(韓元 / 205) * 1.03 * 4.55 * 1.1
                    raw_twd = (krw / 205) * 1.03 * 4.55 * 1.1
                    twd = math.ceil(raw_twd / 10) * 10
                    data.append({"尺寸": size.strip(), "報價 (TWD)": f"NT$ {twd:,}"})
            
            return data
        except Exception as e:
            return f"⚠️ 系統繁忙或 IP 被暫時限制，請 1 分鐘後再試。錯誤資訊: {str(e)}"
        finally:
            await browser.close()

st.title("👟 KREAM 代購即時報價")
st.info("公式：(韓元 ÷ 205) × 1.03 × 4.55 × 1.1 (進位至十位)")
model_input = st.text_input("輸入商品型號", placeholder="例如: DD1391-100")

if st.button("🔍 開始報價"):
    if model_input:
        with st.spinner('連線中...'):
            results = asyncio.run(get_kream_prices(model_input))
            if isinstance(results, list):
                st.table(results)
            else:
                st.error(results)
