import streamlit as st
import asyncio
import os
import math
import random
from playwright.async_api import async_playwright

# 1. 自動安裝瀏覽器核心
os.system("playwright install chromium")

# 2. 設定網頁標題
st.set_page_config(page_title="KREAM 代購報價系統", page_icon="👟", layout="centered")

async def get_kream_prices(model):
    async with async_playwright() as p:
        # 啟動瀏覽器，加入更多偽裝參數
        browser = await p.chromium.launch(headless=True, args=[
            '--no-sandbox', 
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled'
        ])
        
        # 隨機偽裝瀏覽器指紋
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        ]
        
        context = await browser.new_context(
            user_agent=random.choice(user_agents),
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()
        
        try:
            # 前往 KREAM 搜尋頁
            search_url = f"https://kream.co.kr/search?keyword={model}&tab=products"
            # 等待頁面完全加載
            await page.goto(search_url, wait_until="networkidle", timeout=60000)
            
            # 增加隨機延遲，模擬真人思考
            await asyncio.sleep(random.uniform(2, 4))
            
            # 檢查是否被擋 (出現驗證碼或空白)
            if "captcha" in page.url or await page.query_selector(".captcha") is not None:
                return "❌ 被 KREAM 偵測為機器人了，請過幾分鐘再試。"

            # 等待第一個商品結果 (延長到 30 秒)
            item_selector = ".search_result_item"
            await page.wait_for_selector(item_selector, timeout=30000)
            await page.click(item_selector)
            
            # 等待購買按鈕並點擊
            buy_btn = ".btn_division.buy"
            await page.wait_for_selector(buy_btn, timeout=20000)
            await page.click(buy_btn)
            
            # 等待價格清單載入
            await page.wait_for_selector(".select_unit", timeout=20000)
            items = await page.query_selector_all(".select_unit")
            
            data = []
            for item in items:
                size_el = await item.query_selector(".size")
                price_el = await item.query_selector(".price")
                
                if size_el and price_el:
                    s_text = await size_el.inner_text()
                    p_text = await price_el.inner_text()
                    
                    if "원" in p_text:
                        krw = int(p_text.replace(",", "").replace("원", "").strip())
                        # 公式: (韓元 / 205) * 1.03 * 4.55 * 1.1，無條件進位到十位
                        raw_twd = (krw / 205) * 1.03 * 4.55 * 1.1
                        twd = math.ceil(raw_twd / 10) * 10
                        
                        data.append({
                            "尺寸 (Size)": s_text.strip(),
                            "代購報價 (TWD)": f"NT$ {twd:,}",
                            "KREAM 原價": f"{krw:,} KRW"
                        })
            
            return data
            
        except Exception as e:
            # 如果還是超時，給一個具體的提示
            if "Timeout" in str(e):
                return "⚠️ KREAM 響應太慢或暫時封鎖了查詢，請重新嘗試或稍後再試。"
            return f"❌ 查詢出錯：{str(e)}"
        finally:
            await browser.close()

# --- 介面 ---
st.title("👟 KREAM 代購即時報價系統")
st.markdown("---")
st.info("💡 公式：**(韓元 ÷ 205) × 1.03 × 4.55 × 1.1** (進位至十位)")

model_input = st.text_input("輸入商品型號", placeholder="例如: DD1391-100")

if st.button("🔍 開始即時報價"):
    if model_input:
        with st.spinner(f'正在分析市場價格...'):
            results = asyncio.run(get_kream_prices(model_input))
            
            if isinstance(results, list) and len(results) > 0:
                st.success(f"✅ {model_input} 查詢成功！")
                st.table(results)
            elif isinstance(results, list) and len(results) == 0:
                st.warning("查無此型號。")
            else:
                st.error(results)
    else:
        st.warning("請輸入型號！")
