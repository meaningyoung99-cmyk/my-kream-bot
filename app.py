import streamlit as st
import asyncio
import os
import math
from playwright.async_api import async_playwright

# 1. 自動安裝瀏覽器核心 (Streamlit 雲端運行必需)
os.system("playwright install chromium")

# 2. 設定網頁標題與樣式
st.set_page_config(page_title="KREAM 代購報價系統", page_icon="👟", layout="centered")

# --- 報價核心函式 ---
async def get_kream_prices(model):
    async with async_playwright() as p:
        # 啟動瀏覽器並模擬真人語系
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
        page = await context.new_page()
        
        try:
            # 前往 KREAM 搜尋頁
            search_url = f"https://kream.co.kr/search?keyword={model}&tab=products"
            await page.goto(search_url, timeout=60000)
            
            # 點擊第一個商品結果
            await page.wait_for_selector(".search_result_item", timeout=10000)
            await page.click(".search_result_item")
            
            # 點擊「購買」按鈕展開各尺寸清單
            await page.wait_for_selector(".btn_division.buy", timeout=10000)
            await page.click(".btn_division.buy")
            
            # 等待價格清單載入
            await page.wait_for_selector(".select_unit", timeout=10000)
            items = await page.query_selector_all(".select_unit")
            
            data = []
            for item in items:
                size_el = await item.query_selector(".size")
                price_el = await item.query_selector(".price")
                
                if size_el and price_el:
                    s_text = await size_el.inner_text()
                    p_text = await price_el.inner_text()
                    
                    if "원" in p_text:
                        # 取得韓元純數字
                        krw = int(p_text.replace(",", "").replace("원", "").strip())
                        
                        # --- 套用你的專屬公式 ---
                        # 公式: (韓元 / 205) * 1.03 * 4.55 * 1.1
                        raw_twd = (krw / 205) * 1.03 * 4.55 * 1.1
                        
                        # 無條件進位到十位數 (例如 4512 -> 4520)
                        twd = math.ceil(raw_twd / 10) * 10
                        
                        data.append({
                            "尺寸 (Size)": s_text.strip(),
                            "代購報價 (TWD)": f"NT$ {twd:,}",
                            "KREAM 原價": f"{krw:,} KRW"
                        })
            
            return data
            
        except Exception as e:
            return f"查詢失敗，原因：{str(e)}"
        finally:
            await browser.close()

# --- 網頁介面設計 ---
st.title("👟 KREAM 代購即時報價系統")
st.markdown("---")
st.info("💡 目前計算公式：**(韓元 ÷ 205) × 1.03 × 4.55 × 1.1** (報價皆無條件進位至十位數)")

model_input = st.text_input("請輸入商品型號 (例如: DD1391-100)", placeholder="請在此輸入...")

if st.button("🔍 開始即時報價"):
    if model_input:
        with st.spinner(f'正在為您連線 KREAM 查詢 {model_input} ...'):
            results = asyncio.run(get_k
