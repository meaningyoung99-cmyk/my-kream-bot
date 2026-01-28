import streamlit as st
import asyncio
import os
from playwright.async_api import async_playwright

# 確保雲端環境有安裝瀏覽器
os.system("playwright install chromium")

# --- 代購參數設定 (你可以隨時在這裡改) ---
EXCHANGE_RATE = 0.026  # 匯率
SHIPPING_FEE = 250     # 運費
PROFIT_MARGIN = 1.1    # 利潤 (1.1 = 10%)

async def get_kream_prices(model):
    async with async_playwright() as p:
        # 這裡針對雲端環境加了參數防止當機
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        page = await browser.new_page()
        try:
            # 搜尋
            await page.goto(f"https://kream.co.kr/search?keyword={model}&tab=products", timeout=60000)
            await page.wait_for_selector(".search_result_item", timeout=10000)
            await page.click(".search_result_item")
            
            # 點擊購買看報價
            await page.wait_for_selector(".btn_division.buy", timeout=10000)
            await page.click(".btn_division.buy")
            
            # 抓取尺寸與價格
            await page.wait_for_selector(".select_unit", timeout=10000)
            items = await page.query_selector_all(".select_unit")
            
            data = []
            for item in items:
                size_el = await item.query_selector(".size")
                price_el = await item.query_selector(".price")
                if size_el and price_el:
                    s = await size_el.inner_text()
                    p_text = await price_el.inner_text()
                    if "원" in p_text:
                        krw = int(p_text.replace(",", "").replace("원", "").strip())
                        # 計算報價
                        twd = int((krw * EXCHANGE_RATE + SHIPPING_FEE) * PROFIT_MARGIN)
                        data.append({"尺寸": s.strip(), "代購報價(TWD)": f"${twd:,}", "KREAM原價": f"{krw:,} KRW"})
            return data
        except Exception as e:
            return f"發生錯誤：{str(e)}"
        finally:
            await browser.close()

# --- 網頁介面 ---
st.set_page_config(page_title="KREAM 即時報價", page_icon="👟")
st.title("👟 KREAM 代購即時報價系統")
st.write("輸入型號，系統會自動抓取 KREAM 最新價格並計算報價。")

model_input = st.text_input("請輸入商品型號 (例如: DD1391-100)", "")

if st.button("開始查詢"):
    if model_input:
        with st.spinner('正在分析 KREAM 數據...'):
            result = asyncio.run(get_kream_prices(model_input))
            if isinstance(result, list):
                st.success(f"查詢成功！型號：{model_input}")
                st.table(result)
            else:
                st.error(f"查詢失敗：{result}")
    else:
        st.warning("請先輸入型號")
