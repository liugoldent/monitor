import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

def get_realtime_turnover():
    # 設定 Chrome 選項
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # 不開啟瀏覽器視窗
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # 初始化 WebDriver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    url = "https://tw.stock.yahoo.com/rank/turnover"
    
    try:
        print(f"正在抓取資料：{url} ...")
        driver.get(url)
        
        # 等待頁面加載完成（視網路情況調整秒數）
        time.sleep(3)

        # 找到表格內容
        # Yahoo 的排行榜通常在一個列表容器中，這裡直接抓取整個清單
        rows = driver.find_elements(By.CSS_SELECTOR, r'li.List\(n\)')
        
        data_list = []
        
        for row in rows:
            # 提取各欄位資料，Yahoo 結構可能會變動，以下是針對目前的 CSS 類別
            try:
                # 這裡使用相對路徑提取
                name_code = row.find_element(By.CSS_SELECTOR, r'.Lh\(20px\)').text
                price = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[0].text
                change = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[1].text
                change_percent = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[2].text
                volume = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[3].text
                turnover = row.find_elements(By.CSS_SELECTOR, r'.Jc\(fe\)')[4].text
                
                data_list.append({
                    "股票名稱/代碼": name_code.replace('\n', ' '),
                    "成交價": price,
                    "漲跌": change,
                    "幅度": change_percent,
                    "成交量(張)": volume,
                    "成交值(億)": turnover
                })
            except:
                continue # 略過標頭或其他非資料行

        # 轉成 Pandas DataFrame
        df = pd.DataFrame(data_list)
        return df

    except Exception as e:
        print(f"發生錯誤: {e}")
        return None
    finally:
        driver.quit()

if __name__ == "__main__":
    df_result = get_realtime_turnover()
    
    if df_result is not None and not df_result.empty:
        print("\n--- 即時成交值排行榜 (前幾名) ---")
        print(df_result.head(10))
        # 也可以存成 CSV
        # df_result.to_csv("turnover_rank.csv", index=False, encoding="utf-8-sig")
    else:
        print("未能抓取到資料。")