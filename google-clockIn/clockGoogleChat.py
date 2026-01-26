from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 連線到已開啟的 Chrome（remote debugging: 9222）
options = webdriver.ChromeOptions()
options.debugger_address = "127.0.0.1:9222"
driver = webdriver.Chrome(options=options)

try:
    # 打開 Google Chat
    driver.get("https://chat.google.com/")

    # 等待使用者登入（若已有登入會自動跳過）
    WebDriverWait(driver, 120).until(
        EC.presence_of_element_located((
            By.CSS_SELECTOR,
            "div[aria-label='聊天室'], div[aria-label='聊天室列表']"
        ))
    )

    # 找到左側固定區的 eric 聊天室
    # Google Chat UI 會產生一個列表，每個項目可能有 aria-label 或文字
    # 使用 XPATH 尋找包含文字 "eric" 的元素
    eric_chat = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable(
            (By.XPATH, "//span[contains(text(),'h')]/ancestor::a")
        )
    )
    eric_chat.click()

    # 等待聊天輸入框可用
    input_box = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "div[contenteditable='true']")
        )
    )

    # 在輸入框輸入 '1'
    input_box.click()
    input_box.send_keys("1")

    # 按 Enter 發送
    input_box.send_keys(Keys.ENTER)

    print("訊息已送出。")

finally:
    # 若不需要保持開啟，可結束瀏覽器
    # driver.quit()
    pass
