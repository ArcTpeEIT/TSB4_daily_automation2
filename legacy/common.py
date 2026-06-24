"""
common.py
所有 case 共用的參數、常數、工具函式。
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from scp import SCPClient
import time
import datetime
import sys
import serial
import io
import re
import os
import paramiko

# 開啟行緩衝，確保 print 能與 console log 即時同步
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
except Exception:
    pass

# ==================== 測試參數設定區域 ====================
# 本區只放「測試流程常數 / 環境設定」。
# 排序原則：
#   1. 先放全域共用參數
#   2. 再依 case1 -> case9 順序放各 case 專用參數
#   3. 最後放 Web GUI 共用、Fail Recovery、舊版相容參數
#
# 注意：
#   所有等待若是透過 receive_monitor() 執行，等待期間都會持續保存 RE console log。
#   只有直接 time.sleep() 才不會收 console log。


# ==========================================================
# 1. Global Test Control
# ==========================================================
# 使用 case：case1 ~ case9
TOTAL_LOOPS = 1
# 測試總 loop 數。
# 例如 TOTAL_LOOPS = 3：
#   case1 會跑 3 次 factory_default -> ETH BH -> WiFi BH
#   case2 會跑 3 次 ETH BH -> WiFi BH
#   case6~case9 會跑 3 次 GUI action -> ETH BH -> WiFi BH


# ==========================================================
# 2. Serial / Relay Ports
# ==========================================================
# 使用 case：case1 ~ case9
BOOSTER_PORT = "COM4"
# RE / Booster 的 serial console port。
# 用途：
#   - receive_monitor() 收 RE console log
#   - get_booster_fw_version() 讀 Booster FW
#   - case1 factory_default
#   - case3 warm reboot
#   - polling / final onboarding check

RELAY_PORT = "COM3"
# Relay board 的 serial port。
# 用途：
#   - relay 6 on/off 切換 ETH BH / WiFi BH
#   - case4 relay 1 off/on 做 RE cold reboot
#   - fail recovery 時 relay 2 控制 TSM4 power

BAUD_RATE = 115200
# BOOSTER_PORT / RELAY_PORT 共用 baud rate。

RELAY_ETH_PORT = 6
# relay 6：控制 ETH BH / WiFi BH 實體線路。
#   relay 6 on  -> ETH BH
#   relay 6 off -> WiFi BH

TSM4_POWER_RELAY_PORT = 2
# relay 2：控制 TSM4 power。
# 主要用於 fail recovery：TSM4 + RE 同步 reboot。


# ==========================================================
# 3. Common Polling Parameters
# ==========================================================
# 使用 case：case1 ~ case9
# 主要由 poll_booster_console() 使用。
POLLING_INTERVAL = 10
# 每輪 polling 的週期，單位秒。
# 例如每 10 秒送一次：
#   date; WiFi_inf_ChOnOff.sh; chk_Status.sh; re_chk.sh; ping ...; cat /tmp/cur_led_st.led

MAX_TOTAL_LIMIT = 900
# 單一 ETH BH 或 WiFi BH 判定段的最大等待時間。
# 計算方式約為：
#   init_wait_time + polling 時間 <= MAX_TOTAL_LIMIT

ONBOARDING_THRESHOLD = 5
# 預設連續成功門檻。
# 連續看到：
#   Onboarding : done
#   ping 有 64 bytes from
# 達到 5 次後，才進 final onboarding check。

PASS_COOLDOWN_TIME = 90
# PASS 後冷卻等待時間。
# 目前透過 receive_monitor(PASS_COOLDOWN_TIME) 執行，
# 所以這 60 秒仍會持續保存 RE console log。

FINAL_ONBOARDING_CHECK_WAIT = 3
# 達到 ONBOARDING_THRESHOLD 後，做最後一次 onboarding check 前的等待時間。
# 用途：
#   避免剛好 5/5 PASS 後狀態瞬間掉回 pending。
# 流程：
#   5/5 -> wait 3 秒 -> 再跑 chk_Status.sh/re_chk.sh/ping -> 確認仍 PASS。

INIT_WAIT_TIME = 120
# fallback only。
# 若呼叫 poll_booster_console() 時沒有指定 init_wait_time，才會使用此值。
# 一般不建議直接調這個；請調各 case 專用 init wait。


# ==========================================================
# 4. Case1: RE Factory Default
# ==========================================================
# 流程：
#   每個 loop 都執行：
#   RE factory_default -> ETH BH onboarding check -> WiFi BH onboarding check
CASE1_FACTORY_DEFAULT_INIT_WAIT_TIME = 200
# case1 ETH BH 使用。
# factory_default 指令送出後，進入 ETH BH  前的等待時間。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

CASE1_NORMAL_INIT_WAIT_TIME = 60
# case1 WiFi BH 使用。
# ETH BH PASS 後切 relay 6 off，再進 WiFi BH  前的等待時間。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

CASE1_FACTORY_DEFAULT_THRESHOLD = 8
# case1 ETH BH 使用。
# factory_default 後第一次 ETH BH 較慢，因此要求連續成功 8 次才 PASS。

CASE1_NORMAL_THRESHOLD = 5
# case1 WiFi BH 使用。
# WiFi BH 使用一般門檻，連續成功 5 次才 PASS。


# ==========================================================
# 5. Case2: ETH/WiFi Onboarding Only
# ==========================================================
# 流程：
#   relay 6 on  -> ETH BH onboarding check
#   relay 6 off -> WiFi BH onboarding check
# 不做 reboot，不做 reset。
CASE2_ONBOARDING_INIT_WAIT_TIME = 50
# case2 ETH BH / WiFi BH 共用。
# relay 6 on/off 後，進入  前的等待時間。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。


# ==========================================================
# 6. Case3: RE Warm Reboot
# ==========================================================
# 流程：
#   serial 下 reboot
#   -> 等 RE 重啟
#   -> relay 6 on/off
#   -> onboarding check
RE_WARM_REBOOT_POST_WAIT = 10
# case3 使用。
# 透過 RE serial 送出 reboot 後，等待 RE 開始重啟/回復的時間。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

RE_WARM_REBOOT_RELAY_POST_WAIT = 10
# case3 使用。
# relay 6 on/off 後，等待 ETH/WiFi backhaul 實體狀態穩定。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

RE_WARM_REBOOT_INIT_WAIT_TIME = 150
# case3 使用。
# backhaul relay 切換完成後，進入  前的等待時間。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。


# ==========================================================
# 7. Case4: RE Cold Reboot
# ==========================================================
# 流程：
#   relay 1 off
#   -> relay 1 on
#   -> relay 6 on/off
#   -> onboarding check
RE_COLD_POWER_RELAY_PORT = 1
# case4 使用。
# 控制 RE 電源的 relay channel，目前為 relay 1。

RE_COLD_REBOOT_POWER_OFF_TIME = 10
# case4 使用。
# relay 1 off 後，RE 斷電保持時間。
# 透過 receive_monitor() 等待，所以如果 serial 還有殘留 log 會被保存。

RE_COLD_REBOOT_POST_WAIT = 10
# case4 使用。
# relay 1 on 後，等待 RE boot up 的時間。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

RE_COLD_REBOOT_RELAY_POST_WAIT = 10
# case4 使用。
# relay 6 on/off 後，等待 ETH/WiFi backhaul 狀態穩定。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

RE_COLD_REBOOT_INIT_WAIT_TIME = 140
# case4 使用。
# backhaul relay 切換完成後，進入  前的等待時間。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。


# ==========================================================
# 8. Case5: TSM4 GUI Restart
# ==========================================================
# 流程：
#   TSM4 GUI Settings -> Maintenance -> Restart
#   -> relay 6 on/off
#   -> onboarding check
TSM4_REBOOT_CHROME_CLOSE_WAIT = 5
# case5 使用。
# GUI 按下 Restart 並確認後，等待幾秒再關閉 Chrome。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

TSM4_REBOOT_POST_WAIT = 10
# case5 使用。
# TSM4 GUI Restart 後，等待 TSM4 系統重啟/穩定。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。

TSM4_REBOOT_RELAY_POST_WAIT = 140
# case5 使用。
# relay 6 on/off 後，等待 ETH/WiFi backhaul 穩定。
# 透過 receive_monitor() 等待，所以等待期間會收 RE console log。


# ==========================================================
# 9. Case6: GUI Reboot GW+RE
# ==========================================================
# 流程：
#   GUI Reboot GW+RE
#   -> WAIT_AFTER_GUI_ACTION_BEFORE_RELAY_SWITCH
#   -> relay 6 on/off
#   -> REBOOT_INIT_WAIT_TIME
#   -> onboarding check
#
# 實際共用參數：
#   REBOOT_SYNC_WAIT
#   RELAY_SETTLE_TIME
#   REBOOT_INIT_WAIT_TIME
#
# 這些參數定義在 Case6~Case9 共用 GUI action 區塊。


# ==========================================================
# 10. Case7: GUI Reset Router+Boosters
# ==========================================================
# 流程：
#   GUI Reset Router+Boosters
#   -> WAIT_AFTER_GUI_ACTION_BEFORE_RELAY_SWITCH
#   -> relay 6 on/off
#   -> RESET_INIT_WAIT_TIME
#   -> onboarding check
#
# 實際共用參數：
#   REBOOT_SYNC_WAIT
#   RELAY_SETTLE_TIME
#   RESET_INIT_WAIT_TIME
#
# 這些參數定義在 Case6~Case9 共用 GUI action 區塊。


# ==========================================================
# 11. Case8: GUI Reboot RE
# ==========================================================
# 流程：
#   GUI Reboot RE
#   -> WAIT_AFTER_GUI_ACTION_BEFORE_RELAY_SWITCH
#   -> relay 6 on/off
#   -> REBOOT_INIT_WAIT_TIME
#   -> onboarding check
#
# 實際共用參數：
#   REBOOT_SYNC_WAIT
#   RELAY_SETTLE_TIME
#   REBOOT_INIT_WAIT_TIME
#
# 這些參數定義在 Case6~Case9 共用 GUI action 區塊。


# ==========================================================
# 12. Case9: GUI Reset RE
# ==========================================================
# 流程：
#   GUI Reset RE
#   -> WAIT_AFTER_GUI_ACTION_BEFORE_RELAY_SWITCH
#   -> relay 6 on/off
#   -> RESET_INIT_WAIT_TIME
#   -> onboarding check
#
# 實際共用參數：
#   REBOOT_SYNC_WAIT
#   RELAY_SETTLE_TIME
#   RESET_INIT_WAIT_TIME
#
# 這些參數定義在 Case6~Case9 共用 GUI action 區塊。


# ==========================================================
# 13. Case6~Case9 Common GUI Action Timing
# ==========================================================
# 使用 case：case6, case7, case8, case9
REBOOT_SYNC_WAIT = 20
# GUI 按下 Reset/Reboot 並確認後，到切 relay 6 前的等待時間。
# 注意：
#   不是「30 秒後才開始存 RE console log」。
#   這段是透過 receive_monitor(REBOOT_SYNC_WAIT) 執行，
#   所以 GUI action 後這 20 秒內會持續保存 RE console log。
# 建議理解為：
#   WAIT_AFTER_GUI_ACTION_BEFORE_RELAY_SWITCH

RELAY_SETTLE_TIME = 3
# relay 6 on/off 後的硬體穩定時間。
# case1/case2/case4 也會使用此值做一般 relay settle。

RESTORE_ETH_BH_WAIT = 10
# 每個 case 測試結束或確認 FAIL 後，等待 10 秒再切回 ETH BH。
# 流程：receive_monitor(10) -> relay 6 on -> receive_monitor(RELAY_SETTLE_TIME)

RESET_INIT_WAIT_TIME = 180
# Reset 類 GUI action 使用。
# 使用 case：
#   case7 Reset Router+Boosters
#   case9 Reset RE
# GUI action + relay 切換完成後，進入  前的等待時間。

REBOOT_INIT_WAIT_TIME = 150
# Reboot 類 GUI action 使用。
# 使用 case：
#   case5 TSM4 Restart
#   case6 Reboot GW+RE
#   case8 Reboot RE
# GUI action + relay 切換完成後，進入  前的等待時間。


# ==========================================================
# 14. Web GUI Common Parameters
# ==========================================================
# 使用 case：
#   case1 ~ case4：只用來讀 GW FW version，讀完後關閉 Chrome。
#   case5 ~ case9：讀 GW FW version，並登入 GUI 執行 action。
GATEWAY_URL = "http://192.168.0.1/"
WAIT_TIMEOUT = 30
# Selenium WebDriverWait timeout。

GW_FW_TO_GUI_ACTION_SLEEP = 3
# case5~case9 使用。
# 讀完 GW FW 後，沿用同一個 Chrome 進 GUI action 前等待秒數。
# case1~case4 不使用此值，因為 case1~case4 抓完 GW FW 會直接關閉 Chrome。

ROUTER_USERNAME = "admin"
ROUTER_PASSWORD = "5nvvnaf3vr"
# TSM4 Web GUI 登入帳密。
# case5~case9 登入 GUI action 時使用。

CHROME_DRIVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromedriver.exe")

WEB_GUI_OPEN_RETRY = 6
WEB_GUI_OPEN_RETRY_WAIT = 10

# ==========================================================
# 15. Fail Recovery
# ==========================================================
# 使用時機：
#   -  FAIL
#   - COM Error
#   - Logic Timeout
#
# 注意：
#   GUI Error 目前策略是只寫 Summary，不收 diag、不 recovery。
TSM4_REBOOT_MONITOR_TIME = 300
# TSM4 + RE 同步 reboot 後，統一 monitor console 的時間。
# 用於 reboot_tsm4_and_re() 的最後 receive_monitor()。

# Legacy recovery policy: reboot is disabled here.
# The daily runner (run_all_cases_like_github_v2.py) owns the GW+RE workaround reboot after a case fails.
FAIL_RECOVERY_REBOOT_ENABLE = False
FAIL_RECOVERY_REASON_SUFFIX = "Recovery(diag_only_no_reboot)"
# FAIL 後寫入 Summary 的附加說明標記。


# ==========================================================
# 16. Optional / Compatibility / Currently Unused
# ==========================================================
# 以下參數目前保留給未來流程開關或舊版相容。
# 目前主流程 policy：
#   - FAIL 就停止
#   - ETH BH FAIL 不繼續 WiFi BH
#   - recovery 統一使用 TSM4_REBOOT_MONITOR_TIME monitor
TSM4_REBOOT_POWER_OFF_TIME = 5
# 目前主流程 reboot_tsm4_and_re() 沒有使用此值。
# 如果未來要改成 relay 2 off 後固定等 5 秒，可接回此參數。

RE_REBOOT_MONITOR_TIME = 60
# 目前主流程沒有獨立使用此值。
# 現在 GW + RE recovery 是用 TSM4_REBOOT_MONITOR_TIME 統一監控。

CONTINUE_AFTER_FAIL = False
# 目前主流程固定 FAIL 後停止。
# 此參數目前保留給未來若要改成 FAIL 後繼續 loop 的開關。

CONTINUE_WIFI_AFTER_ETH_FAIL = False
# 目前主流程固定 ETH BH FAIL 後不跑 WiFi BH。
# 此參數目前保留給未來若要改成 ETH FAIL 後仍跑 WiFi BH 的開關。
# ==========================================================

# --- XPath 常數（全部列出，各 case 自行選用） ---
XPATH_ROUTER_FW         = "/html/body/app-root/app-login/div/main/div[1]/div[1]/div[1]/span[2]"
XPATH_LOGIN_USER        = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[1]/input"
XPATH_LOGIN_PASS        = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[2]/input"
XPATH_WIFI_SETTINGS     = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[3]/div/div/a/p"
# XPATH_WIFI_MESH       = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/nav/div/ul/li[3]/a"  # fw 006
XPATH_WIFI_MESH         = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/nav/div/ul/li[4]/a"  # fw 404
XPATH_WIFI_BOOSTERS     = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/div[1]/nav/div/ul/li[2]/a"
XPATH_REBOOT_ALL        = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[1]"
XPATH_RESET_ALL         = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[2]"
XPATH_REBOOT_RE         = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[3]"
XPATH_RESET_RE          = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[4]"
XPATH_CONFIRM_YES       = "/html/body/ngb-modal-window/div/div/app-generic-modal/div[3]/button[2]"
XPATH_DISCARD_CLOSE_BTN = "/html/body/ngb-modal-window/div/div/app-modal-discard-changes/div[3]/div/button[2]"

# Case5: TSM4 GUI Restart path: Settings -> Maintenance -> Restart
XPATH_SETTINGS = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[4]/div/div/a/p"
XPATH_MAINTENANCE = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-top-menu/nav/div/ul/li[8]/a"
XPATH_TSM4_RESTART = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-maintenace-main/div/div/app-maintenance-resets/form/div/div[1]/div[2]/button"
XPATH_TSM4_FACTORY = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-maintenace-main/div/div/app-maintenance-resets/form/div/div[3]/div[2]/button"
# ==========================================================

# ==================== Log 設定（由各 case 覆蓋 TEST_CASE_NAME 後生效） ====================
# 各 case 檔案 import 後覆蓋 TEST_CASE_NAME，再呼叫 init_log_filenames() 初始化。
TEST_CASE_NAME = "unknown_case"
CASE_ID = TEST_CASE_NAME
FULL_CONSOLE_LOG = ""
SUMMARY_LOG = ""

def init_log_filenames():
    """各 case 設定好 TEST_CASE_NAME 後呼叫，初始化 log 檔名。"""
    global CASE_ID, FULL_CONSOLE_LOG, SUMMARY_LOG
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    CASE_ID          = TEST_CASE_NAME.replace("+", "_").replace(" ", "_")
    FULL_CONSOLE_LOG = f"{ts}_{TEST_CASE_NAME}_Console.log"
    SUMMARY_LOG      = f"{ts}_{TEST_CASE_NAME}_Summary.log"
# ======================================================


# ==================== 工具函式 ====================

def create_chrome_driver():
    if not os.path.exists(CHROME_DRIVER_PATH):
        log_progress(f"[Chrome] 找不到 chromedriver.exe: {CHROME_DRIVER_PATH}")
        return None

    options = webdriver.ChromeOptions()

    running_in_github = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"

    if running_in_github:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")

    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    log_progress(f"[Chrome] 使用 chromedriver: {CHROME_DRIVER_PATH}")
    log_progress(f"[Chrome] headless mode: {running_in_github}")

    service = Service(executable_path=CHROME_DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def receive_monitor(wait_seconds, ser=None):
    """邊等待、邊將 Serial log 即時寫入檔案並同步印到終端機。"""
    start_time = time.time()
    collected_output = ""
    close_after_use = False

    try:
        if ser is None:
            ser = serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=0.1)
            close_after_use = True

        while (time.time() - start_time) < float(wait_seconds):
            if ser.in_waiting > 0:
                raw_data = ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
                collected_output += raw_data

                ts_raw = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
                with open(FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
                    for line in raw_data.splitlines():
                        if line.strip():
                            formatted_log = f"{ts_raw} [SERIAL] {line.strip()}\n"
                            f.write(formatted_log)
                            sys.stdout.write(formatted_log)

                sys.stdout.flush()

            time.sleep(0.1)

    except Exception as e:
        print(f"Serial Monitor Error: {e}")

    finally:
        if close_after_use and ser:
            try:
                ser.close()
            except Exception:
                pass

    return collected_output


def init_summary_log(router_fw, booster_fw):
    write_header = not os.path.exists(SUMMARY_LOG) or os.path.getsize(SUMMARY_LOG) == 0
    with open(SUMMARY_LOG, "a", encoding="utf-8") as f:
        if write_header:
            f.write(f"{TEST_CASE_NAME}\n")
            f.write(f"Router Firmware Version : {router_fw}\n")
            f.write(f"Booster Firmware Version: {booster_fw}\n")
            f.write("-" * 95 + "\n")
            f.write(
                f"{'Time':<20} | {'Loop':<8} | {'Interface':<12} | "
                f"{'Duration':<10} | {'Result':<8} | {'Fail_Reason'}\n"
            )
            f.write("-" * 95 + "\n")


def log_progress(message):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    formatted = f"{ts} [PROGRESS] >>> {message}\n"
    with open(FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
        f.write(formatted)
    sys.stdout.write(formatted)
    sys.stdout.flush()


def log_separator(message):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    border = "=" * 70
    msg = f"\n{ts} {border}\n{ts} [PHASE START] >>> {message}\n{ts} {border}\n"
    with open(FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
        f.write(msg)
    sys.stdout.write(msg)
    sys.stdout.flush()


def log_details(message, to_console=True):
    with open(FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    if to_console:
        print(message)


def write_summary(loop_str, interface_name, duration, result, reason):
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    line = (
        f"{ts:<20} | {loop_str:<8} | {interface_name:<12} | "
        f"{duration:<10} | {result:<8} | {reason}\n"
    )
    with open(SUMMARY_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def summary_loop_display(loop_str, interface_name):
    """Summary 顯示格式：同一個 loop 只在 ETH BH 那列顯示 loop number，WiFi BH 留空。"""
    if str(interface_name).strip().lower() == "wifi bh":
        return ""
    return loop_str


def fail_reason_with_recovery(reason):
    """保留舊函式名稱作相容。

    新版策略：
    - Fail_Reason 只放真正失敗原因。
    - Recovery(diag+TSM4_reboot+RE_reboot) 改寫在 Summary 備註區。
    """
    return str(reason)


def build_onboarding_fail_reason(has_onboarding, has_ping, prefix=""):
    """依最後一次 console 判定結果建立 Fail_Reason。"""
    reasons = []

    if not has_onboarding:
        reasons.append("Onboarding Fail")

    if not has_ping:
        reasons.append("Ping Fail")

    if not reasons:
        reasons.append("Logic Timeout")

    reason = " + ".join(reasons)

    if prefix:
        return f"{prefix}: {reason}"

    return reason


def write_recovery_note(interface_name, recovery_action=None):
    """將 recovery 動作寫到 Summary 底部備註，不混入 Fail_Reason。"""
    if recovery_action is None:
        recovery_action = globals().get(
            "FAIL_RECOVERY_REASON_SUFFIX",
            "Recovery(diag+TSM4_reboot+RE_reboot)"
        )

    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    with open(SUMMARY_LOG, "a", encoding="utf-8") as f:
        f.write("\n")
        f.write("備註:\n")
        f.write(f"- {ts} {interface_name} FAIL 後已執行 {recovery_action}，避免影響下一個 case。\n")


def gui_fail_reason(reason="GUI Error"):
    """GUI 操作失敗只寫 Summary，不附加 recovery 標記。"""
    return str(reason)



def get_elapsed_duration(duration_start_time, fallback_start_time, fallback_init_wait_time):
    """計算 Summary Duration。

    新定義：
        Duration = action 成功開始 -> onboarding threshold 達成當下

    若 duration_start_time 沒有被外層 case 傳入，則維持舊版 fallback：
        Duration = poll_start_time + init_wait_time -> threshold 達成當下
    """
    if duration_start_time is not None:
        return round(time.time() - duration_start_time, 2)

    return round(time.time() - fallback_start_time + fallback_init_wait_time, 2)


# ==================== 硬體控制 ====================

def control_relay_channel(channel, state):
    try:
        with serial.Serial(RELAY_PORT, BAUD_RATE, timeout=1) as ser:
            ser.write(f"relay {channel} {state}\r".encode("utf-8"))
        log_progress(f"[RELAY] 成功下達指令: relay {channel} {state}")
    except Exception as e:
        log_progress(f"[RELAY] 操作失敗: relay {channel} {state}, error={e}")


def control_relay(state):
    control_relay_channel(RELAY_ETH_PORT, state)


def restore_eth_backhaul(reason="case finished"):
    """測試結束或確認 FAIL 後，將 backhaul 切回 ETH BH。"""
    try:
        wait_time = globals().get("RESTORE_ETH_BH_WAIT", 10)
        log_progress(f"[RESTORE] {reason}: 等待 {wait_time} 秒後切回 ETH BH - relay {RELAY_ETH_PORT} on")
        receive_monitor(wait_time)
        control_relay("on")
        receive_monitor(RELAY_SETTLE_TIME)
        log_progress("[RESTORE] 已切回 ETH BH - relay 6 on")
    except Exception as e:
        log_progress(f"[RESTORE] 切回 ETH BH 失敗: {type(e).__name__}: {e}")


def reboot_tsm4_and_re():
    """GW 跟 RE 同步重開：
    1. TSM4 power off
    2. 等 2 秒後對 RE 下 reboot -f（RE 還活著，console 可用）
    3. TSM4 power on
    4. 統一 monitor TSM4_REBOOT_MONITOR_TIME 秒
    """
    log_separator("TSM4 + RE 同步 REBOOT")

    log_progress(f"STEP 1: TSM4 power off - relay {TSM4_POWER_RELAY_PORT} off")
    control_relay_channel(TSM4_POWER_RELAY_PORT, "off")

    log_progress("STEP 2: 等待 2 秒後對 RE 下 reboot -f（RE 此時仍存活，console 可用）")
    time.sleep(2)

    try:
        with serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=0.1) as ser:
            log_progress(f"STEP 3: 透過 Serial ({BOOSTER_PORT}) 喚醒 console，連送 3 次 Enter...")
            for _ in range(3):
                ser.write(b"\r\n")
                time.sleep(0.5)
            log_progress("STEP 4: 送出 RE reboot -f")
            ser.write(b"reboot -f\n")
            time.sleep(2)
    except Exception as e:
        log_progress(f"RE reboot 指令送出失敗: {e}（繼續執行 TSM4 power on）")

    log_progress(f"STEP 5: TSM4 power on - relay {TSM4_POWER_RELAY_PORT} on")
    control_relay_channel(TSM4_POWER_RELAY_PORT, "on")

    log_progress(f"STEP 6: GW + RE 同步起來，統一 monitor {TSM4_REBOOT_MONITOR_TIME} 秒")
    receive_monitor(TSM4_REBOOT_MONITOR_TIME)
    log_progress("TSM4 + RE reboot monitor 完成")


# ==================== 版本資訊收集 ====================
def safe_driver_get(driver, url, retry=WEB_GUI_OPEN_RETRY, retry_wait=WEB_GUI_OPEN_RETRY_WAIT):
    """開啟 Web GUI with retry，避免 GitHub runner 剛好遇到 TSM4 GUI 尚未 ready。"""
    last_error = None

    for attempt in range(1, retry + 1):
        try:
            log_progress(f"[Chrome] 開啟 Web GUI: {url}，attempt {attempt}/{retry}")
            driver.get(url)
            return True, "None"

        except Exception as e:
            last_error = e
            log_progress(
                f"[Chrome] Web GUI 開啟失敗 attempt {attempt}/{retry}: "
                f"{type(e).__name__}: {e}"
            )

            if attempt < retry:
                log_progress(f"[Chrome] 等待 {retry_wait} 秒後重試 Web GUI...")
                receive_monitor(retry_wait)

    return False, f"{type(last_error).__name__}: {last_error}"


def get_router_fw_version():
    """開啟 Chrome 抓 Router FW 版本，保留 driver 供第一次 GUI 操作沿用。"""
    log_progress(">>> 收集環境資訊: 透過 Web GUI 獲取 Router Firmware Version...")

    driver = create_chrome_driver()

    if driver is None:
        log_progress("[環境資訊] Chrome driver 建立失敗，略過 Router FW 取得。")
        return "Unknown_Router_FW", None

    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    version = "Unknown_Router_FW"

    try:
        open_ok, open_reason = safe_driver_get(driver, GATEWAY_URL)

        if not open_ok:
            log_progress(f"[環境資訊] Web GUI 開啟失敗，略過 Router FW 取得: {open_reason}")
            return "Unknown_Router_FW", driver

        receive_monitor(2)

        fw_element = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_ROUTER_FW)))
        version_text = fw_element.text.strip()

        if version_text in ("Firmware:", ""):
            log_progress("[環境資訊] 原 XPATH 只抓到標籤，嘗試 span[3]...")
            try:
                fw_element = driver.find_element(
                    By.XPATH,
                    XPATH_ROUTER_FW.replace("span[2]", "span[3]")
                )
                version_text = fw_element.text.strip()
            except Exception as inner_e:
                log_progress(f"[環境資訊] span[3] 失敗: {inner_e}")

        version = version_text.replace("Firmware:", "").strip()
        log_progress(f"[環境資訊] 取得 Router FW: {version}")

    except Exception as e:
        log_progress(f"[環境資訊] 獲取 Router FW 失敗: {type(e).__name__}: {e}")

        try:
            screenshot_name = f"Router_FW_Error_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            driver.save_screenshot(screenshot_name)
            log_progress(f"[環境資訊] Router FW 失敗截圖: {screenshot_name}")
            log_progress(f"[環境資訊] current_url: {driver.current_url}")
            log_progress(f"[環境資訊] title: {driver.title}")
        except Exception as se:
            log_progress(f"[環境資訊] Router FW 截圖失敗: {se}")

    return version if version else "Unknown_Router_FW", driver


def get_booster_fw_version():
    log_progress(f">>> 收集環境資訊: 透過 Serial ({BOOSTER_PORT}) 獲取 Booster Firmware Version...")
    try:
        with serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=0.1) as ser:
            ser.write(b"\r\n")
            receive_monitor(1, ser)
            ser.write(b"uci get glb-cfg.device_info.SoftwareVersion\n")
            raw_data = receive_monitor(2, ser)

        for line in raw_data.splitlines():
            if "TSB" in line and "uci get" not in line:
                version = line.strip()
                log_progress(f"[環境資訊] 取得 Booster FW: {version}")
                return version

        log_progress("[環境資訊] 未找到符合的 Booster FW 版本號。")
        return "Unknown_Booster_FW"

    except Exception as e:
        log_progress(f"[環境資訊] 獲取 Booster FW 失敗: {e}")
        return "Unknown_Booster_FW"

def get_environment_fw_versions_close_browser():
    """給 case1-case4 使用：先抓 GW FW，抓完立刻關閉 Chrome，再抓 Booster FW。"""
    router_fw = "Unknown_Router_FW"
    driver = None

    try:
        router_fw, driver = get_router_fw_version()
    finally:
        if driver is not None:
            try:
                driver.quit()
                log_progress("[環境資訊] GW FW 取得完成，Chrome 已關閉。")
            except Exception as e:
                log_progress(f"[環境資訊] 關閉 Chrome 失敗: {e}")

    booster_fw = get_booster_fw_version()
    return router_fw, booster_fw
# ==================== Fail Log 收集 ====================

def collect_fail_logs(case_name):
    log_separator("FAILED - 開始收集診斷 Log")
    log_progress("STEP 1: 強制 Relay 6 ON 並等待 60s...")
    control_relay("on")
    receive_monitor(60)

    target_ip = "192.168.0.1"
    try:
        with serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=2) as ser:
            ser.write(b"\n")
            receive_monitor(1, ser)
            ser.write(b"ifconfig br-lan\n")
            output = receive_monitor(2, ser)

        ip_match = re.search(r"(?:inet addr:|inet\s+)(\d+\.\d+\.\d+\.\d+)", output)
        if ip_match:
            target_ip = ip_match.group(1)
        log_progress(f"STEP 2: 偵測到 DUT IP 為: {target_ip}")

    except Exception as e:
        log_progress(f"Serial 取得 IP 失敗，使用預設值 {target_ip}: {e}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        log_progress(f"STEP 3: SSH 登入 {target_ip} 執行診斷腳本...")
        ssh.connect(target_ip, username="25g5@rIj2Z", password="x@u4194j042u/4m,4@", timeout=10)

        stdin, stdout, stderr = ssh.exec_command("/usr/scripts/diagnosticcomlog.sh")
        receive_monitor(10)

        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            log_progress(f"診斷腳本回傳非 0: {exit_status}, stderr: {stderr.read().decode('utf-8', errors='ignore')}")

        log_progress("STEP 4: 透過 SCP 下載 Log 檔案...")
        with SCPClient(ssh.get_transport()) as scp_client:
            scp_client.get("/tmp/diagnosticcomlog.tgz", f"{case_name}_diagnosticcomlog.tgz")

        log_progress(f"SUCCESS: Log 已成功存至 {case_name}_diagnosticcomlog.tgz")

    except Exception as e:
        log_progress(f"SSH/SCP 收集失敗: {e}")
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def handle_fail_recovery(case_name):
    """FAIL 後統一流程：只收 diag，不在 legacy common.py 內 reboot Booster / TSM4。

    Reboot workaround is intentionally owned by run_all_cases_like_github_v2.py,
    so a failed case can exit first and the top-level runner decides whether to
    execute GW+Booster reboot before moving to the next case.
    """
    collect_fail_logs(case_name)
    if globals().get("FAIL_RECOVERY_REBOOT_ENABLE", False):
        log_progress("FAIL recovery reboot is enabled in legacy common.py; executing reboot_tsm4_and_re().")
        reboot_tsm4_and_re()
    else:
        log_progress("FAIL recovery reboot 已關閉：legacy common.py 只執行 diag，不 reboot Booster / TSM4。")


def safe_handle_fail_recovery(case_name):
    """Recovery 防呆包裝，避免收 log 過程異常造成整支 script 中止。"""
    try:
        handle_fail_recovery(case_name)
    except Exception as e:
        log_progress(f"FAIL recovery 發生異常，但測試流程繼續: {type(e).__name__}: {e}")
    finally:
        restore_eth_backhaul("FAIL recovery 完成")


# ==================== GUI 操作（共用導覽邏輯） ====================

def _try_login_if_needed(driver, wait):
    """確認 GUI 是否已登入；若尚未登入，執行登入流程。"""
    driver.get(GATEWAY_URL)
    receive_monitor(2)

    # 若已經看得到 WiFi Settings，代表 session 已登入，可直接返回。
    try:
        short_wait = WebDriverWait(driver, 5)
        short_wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_SETTINGS)))
        log_progress("偵測到 Web GUI 已登入，略過登入流程。")
        return
    except Exception:
        pass

    log_progress("Web GUI 尚未登入，執行登入流程...")

    user_input = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_LOGIN_USER)))
    user_input.clear()
    user_input.send_keys(ROUTER_USERNAME)
    receive_monitor(0.3)

    pass_input = driver.find_element(By.XPATH, XPATH_LOGIN_PASS)
    pass_input.clear()
    driver.execute_script("arguments[0].value = arguments[1];", pass_input, ROUTER_PASSWORD)
    driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", pass_input)
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", pass_input)
    receive_monitor(0.5)

    submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
    driver.execute_script("arguments[0].click();", submit_btn)

    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass

    receive_monitor(2)


def _navigate_to_boosters(driver, passed_driver):
    """登入並導覽至 Wi-Fi Boosters 頁面。

    注意：
    passed_driver=True 不再代表已登入。get_router_fw_version() 回傳的 driver
    可能只停在 login page，因此這裡會先檢查 session，必要時仍會登入。
    """
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    _try_login_if_needed(driver, wait)

    log_progress("進入 WiFi Settings -> Wi-Fi Mesh -> Wi-Fi Boosters")
    wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_SETTINGS)))
    driver.execute_script("arguments[0].click();", wifi_link)
    receive_monitor(1)

    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass

    receive_monitor(2)

    mesh_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_MESH)))
    driver.execute_script("arguments[0].click();", mesh_btn)

    try:
        short_wait = WebDriverWait(driver, 3)
        warning_btn = short_wait.until(EC.presence_of_element_located((By.XPATH, XPATH_DISCARD_CLOSE_BTN)))
        log_progress("偵測到 Discard 視窗，強制點擊【捨棄(Discard)】...")
        driver.execute_script("arguments[0].click();", warning_btn)
        receive_monitor(1.5)
    except Exception:
        pass

    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass

    receive_monitor(1.5)

    boosters_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_BOOSTERS)))
    driver.execute_script("arguments[0].click();", boosters_btn)
    receive_monitor(1)

    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass

    receive_monitor(1)




def _navigate_to_maintenance(driver, passed_driver):
    """登入並導覽至 Settings -> Maintenance 頁面，用於 case5 TSM4 Restart。"""
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    _try_login_if_needed(driver, wait)

    log_progress("進入 Settings -> Maintenance")
    settings_link = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_SETTINGS)))
    driver.execute_script("arguments[0].click();", settings_link)
    receive_monitor(1)

    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass

    receive_monitor(1)

    maintenance_link = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_MAINTENANCE)))
    driver.execute_script("arguments[0].click();", maintenance_link)
    receive_monitor(1)

    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass

    receive_monitor(1)


def trigger_tsm4_restart(passed_driver=None):
    """Case5: 透過 TSM4 GUI Settings -> Maintenance -> Restart 觸發 TSM4 reboot。"""
    if passed_driver:
        log_progress("使用已存在的 Browser 實體繼續執行 TSM4 Restart...")
        driver = passed_driver
    else:
        log_progress("開啟新的 Chrome 執行 TSM4 Restart...")
        driver = create_chrome_driver()

    if driver is None:
        log_progress("[Chrome] 建立失敗，無法執行 TSM4 Restart")
        return False, None

    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        _navigate_to_maintenance(driver, passed_driver)

        log_progress("觸發 TSM4 Restart 動作...")
        restart_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_TSM4_RESTART)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", restart_btn)
        receive_monitor(0.5)
        driver.execute_script("arguments[0].click();", restart_btn)
        receive_monitor(1)

        log_progress("點擊確認視窗的 Yes...")
        confirm_yes = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_CONFIRM_YES)))
        driver.execute_script("arguments[0].click();", confirm_yes)
        action_start_time = time.time()

        log_progress("GUI 已確認送出 TSM4 Restart 訊號")
        log_progress("Duration 起算點: TSM4 Restart Confirm YES 後")
        log_progress(f"等待 {TSM4_REBOOT_CHROME_CLOSE_WAIT}s 後關閉 Chrome...")
        receive_monitor(TSM4_REBOOT_CHROME_CLOSE_WAIT)

        return True, action_start_time

    except Exception as e:
        log_progress(f"Web GUI 操作發生異常: {type(e).__name__}: {e}")
        try:
            screenshot_name = f"Error_GUI_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            driver.save_screenshot(screenshot_name)
            log_progress(f"已自動截圖保存至: {screenshot_name}")
        except Exception as se:
            log_progress(f"放棄截圖: {se}")
        return False, None

    finally:
        try:
            driver.quit()
        except Exception:
            pass



def trigger_web_action(action_xpath, action_label, passed_driver=None):
    """登入 GUI → 導覽至 Boosters → 點擊指定按鈕 → 確認。

    Args:
        action_xpath:  要點擊的按鈕 XPATH（各 case 不同）。
        action_label:  動作名稱，用於 log（例如 "Reset All", "Reboot RE"）。
        passed_driver: 若傳入已登入的 driver，跳過開 Chrome 和登入步驟。
    """
    if passed_driver:
        log_progress("使用已存在的 Browser 實體繼續執行...")
        driver = passed_driver
    else:
        log_progress(f"開啟新的 Chrome 執行 {action_label}...")
        driver = create_chrome_driver()

    if driver is None:
        log_progress(f"[Chrome] 建立失敗，無法執行 {action_label}")
        return False, None

    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        _navigate_to_boosters(driver, passed_driver)

        log_progress(f"觸發 {action_label} 動作...")
        action_btn = wait.until(EC.presence_of_element_located((By.XPATH, action_xpath)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", action_btn)
        receive_monitor(0.5)
        driver.execute_script("arguments[0].click();", action_btn)
        receive_monitor(1)

        log_progress("點擊確認視窗的 Yes...")
        confirm_yes = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_CONFIRM_YES)))
        driver.execute_script("arguments[0].click();", confirm_yes)
        action_start_time = time.time()

        log_progress(f"GUI 已確認送出 {action_label} 訊號")
        log_progress(f"Duration 起算點: {action_label} Confirm YES 後")
        return True, action_start_time

    except Exception as e:
        log_progress(f"Web GUI 操作發生異常: {type(e).__name__}: {e}")
        try:
            screenshot_name = f"Error_GUI_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            driver.save_screenshot(screenshot_name)
            log_progress(f"已自動截圖保存至: {screenshot_name}")
        except Exception as se:
            log_progress(f"放棄截圖: {se}")
        return False, None

    finally:
        receive_monitor(3)
        try:
            driver.quit()
        except Exception:
            pass


def get_init_wait_time_by_action(action_label):
    """依 GUI action 類型決定 polling 前等待時間。

    Reset 類動作使用 RESET_INIT_WAIT_TIME。
    Reboot 類動作使用 REBOOT_INIT_WAIT_TIME。
    ETH BH / WiFi BH 目前不拆，兩者共用同一個 action-based wait time。
    """
    label = str(action_label).lower()

    if "reboot" in label:
        return REBOOT_INIT_WAIT_TIME

    if "reset" in label:
        return RESET_INIT_WAIT_TIME

    return INIT_WAIT_TIME


def final_onboarding_check(ser, interface_name):
    """達到連續成功門檻後，再做最後一次 onboarding 狀態確認。

    Returns:
        (final_ok, has_onboarding, has_ping)
    """
    log_progress(f"[{BOOSTER_PORT} - {interface_name}] 最後確認 onboarding 狀態，等待 {FINAL_ONBOARDING_CHECK_WAIT} 秒...")
    receive_monitor(FINAL_ONBOARDING_CHECK_WAIT, ser)

    ser.write(b"date; chk_Status.sh; re_chk.sh; ping 192.168.0.1 -c 1; cat /tmp/cur_led_st.led\n")
    output = receive_monitor(8, ser)

    has_onboarding = "Onboarding : done" in output
    has_ping = "64 bytes from" in output

    log_details("-" * 50)
    log_details("[Final Check]:")
    log_details(f"  > Onboarding: {'SUCCESS' if has_onboarding else 'FAIL/PENDING'}")
    log_details(f"  > Ping      : {'SUCCESS' if has_ping else 'FAIL/TIMEOUT'}")
    log_details("-" * 50)

    return has_onboarding and has_ping, has_onboarding, has_ping


# ==================== Polling ====================

def poll_booster_console(loop_str, interface_name, init_wait_time=None, threshold=None, max_total_limit=None, duration_start_time=None):
    if init_wait_time is None:
        init_wait_time = INIT_WAIT_TIME
    if threshold is None:
        threshold = ONBOARDING_THRESHOLD
    if max_total_limit is None:
        max_total_limit = MAX_TOTAL_LIMIT

    log_progress(f"[{BOOSTER_PORT} - {interface_name}] 初始等待 {init_wait_time} 秒...")
    receive_monitor(init_wait_time)

    try:
        with serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=0.1) as ser:
            log_progress(f"[{BOOSTER_PORT}] 送出 Enter 鍵以喚醒 Console 介面...")
            ser.reset_input_buffer()
            ser.write(b"\r\n")
            receive_monitor(1, ser)

            consecutive_count = 0
            last_fail_reason = "Logic Timeout"
            saw_valid_polling_output = False
            poll_start_time = time.time()
            debug_cmd = (
                b"date; "
                b"DFS_CAC_chk.sh; "
                b"WiFi_inf_ChOnOff.sh; "
                b"chk_Status.sh; "
                b"ping 192.168.0.1 -c 2; "
                b"cat /tmp/cur_led_st.led\n"
            )

            log_progress(f"[{BOOSTER_PORT}] 開始輪詢 (目標：連續成功計數 0/{threshold})...")

            while (time.time() - poll_start_time) < (max_total_limit - init_wait_time):
                loop_start = time.time()
                next_poll_time = loop_start + POLLING_INTERVAL

                try:
                    ser.write(debug_cmd)
                    output = receive_monitor(8, ser)

                    log_details("-" * 50)
                    ts = time.strftime("%H:%M:%S")
                    has_onboarding = "Onboarding : done" in output
                    has_ping       = "64 bytes from" in output

                    log_details(f"[{ts} 輪詢紀錄]:")
                    log_details(f"  > Onboarding: {'SUCCESS' if has_onboarding else 'FAIL/PENDING'}")
                    log_details(f"  > Ping      : {'SUCCESS' if has_ping else 'FAIL/TIMEOUT'}")

                    if output.strip():
                        saw_valid_polling_output = True
                        if not (has_onboarding and has_ping):
                            last_fail_reason = build_onboarding_fail_reason(has_onboarding, has_ping)

                    if has_onboarding and has_ping:
                        consecutive_count += 1
                        log_details(f"  >>> 成功計數: {consecutive_count}/{threshold}")
                        log_progress(f"[{BOOSTER_PORT} - {interface_name}] Onboarding check PASS: {consecutive_count}/{threshold}")
                    else:
                        if consecutive_count > 0:
                            log_details(f"  >>> 條件中斷，計數器由 {consecutive_count}/{threshold} 歸零。")
                            log_progress(f"[{BOOSTER_PORT} - {interface_name}] Onboarding check reset: 0/{threshold}")
                        else:
                            log_details(f"  >>> 條件尚未達成，持續監控中: 0/{threshold}")
                        consecutive_count = 0

                    log_details("-" * 50)

                    if consecutive_count >= threshold:
                        onboarding_duration = get_elapsed_duration(
                            duration_start_time,
                            poll_start_time,
                            init_wait_time
                        )

                        log_progress(
                            f"[{BOOSTER_PORT} - {interface_name}] 已達 {threshold}/{threshold}，"
                            f"Onboarding Duration={onboarding_duration}s，"
                            f"進入冷卻觀察 {PASS_COOLDOWN_TIME} 秒..."
                        )

                        receive_monitor(PASS_COOLDOWN_TIME, ser)

                        log_progress(
                            f"[{BOOSTER_PORT} - {interface_name}] 冷卻完成，執行最後一次 onboarding 確認..."
                        )

                        final_ok, final_has_onboarding, final_has_ping = final_onboarding_check(ser, interface_name)

                        if not final_ok:
                            last_fail_reason = build_onboarding_fail_reason(
                                final_has_onboarding,
                                final_has_ping,
                                prefix="Final Check Fail"
                            )
                            log_progress(
                                f"[{BOOSTER_PORT} - {interface_name}] {last_fail_reason}，"
                                "計數器歸零並重新開始 polling。"
                            )
                            consecutive_count = 0
                            continue

                        log_progress(f"[{BOOSTER_PORT}] >>> PASS！Onboarding Duration: {onboarding_duration}s <<<")
                        write_summary(
                            summary_loop_display(loop_str, interface_name),
                            interface_name,
                            f"{onboarding_duration}s",
                            "PASS",
                            "None"
                        )
                        return True

                    remain = next_poll_time - time.time()
                    if remain > 0:
                        receive_monitor(remain, ser)

                except Exception as e:
                    log_progress(f"[{BOOSTER_PORT}] Serial 異常: {e}")
                    break

    except Exception as e:
        log_progress(f"[{BOOSTER_PORT}] 無法開啟 Serial Port: {e}")
        write_summary(summary_loop_display(loop_str, interface_name), interface_name, "N/A", "FAIL", fail_reason_with_recovery("COM Error"))
        return False

    log_progress(f"[{BOOSTER_PORT}] >>> 輪詢超時 (FAIL)！ <<<")
    if not locals().get("saw_valid_polling_output", False):
        last_fail_reason = "No Valid Console Output"
    write_summary(summary_loop_display(loop_str, interface_name), interface_name, "Timeout", "FAIL", fail_reason_with_recovery(last_fail_reason))
    return False


# ==================== 主測試邏輯 ====================

def execute_one_backhaul_test(loop, interface_name, relay_state, action_xpath, action_label, active_driver=None):
    log_separator(f"LOOP {loop} - {interface_name} 測試開始")
    log_progress(f"STEP: 準備執行 {interface_name} 測試 (GUI 觸發 {action_label})")

    # GUI error policy:
    # 只寫 Summary，不收 diag，不 reboot TSM4+RE，並回傳 False 讓 run_test 停止後續測項。
    gui_ok, duration_start_time = trigger_web_action(action_xpath, action_label, active_driver)
    if not gui_ok:
        write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", gui_fail_reason("GUI Error"))
        log_progress(f"!! {interface_name} GUI 操作失敗，只寫 Summary，不執行 diag / TSM4+RE recovery !!")
        restore_eth_backhaul(f"{interface_name} GUI Error")
        return False

    log_progress(f"等待 {REBOOT_SYNC_WAIT} 秒讓 Booster 確實收到指令...")
    receive_monitor(REBOOT_SYNC_WAIT)

    log_progress(f"STEP: Relay 切換 ({relay_state.upper()}) 配置 {interface_name} 實體環境")
    control_relay(relay_state)
    receive_monitor(RELAY_SETTLE_TIME)

    init_wait_time = get_init_wait_time_by_action(action_label)
    log_progress(f"Polling init wait time 依 action_label='{action_label}' 設定為 {init_wait_time} 秒")
    result = poll_booster_console(
        str(loop),
        interface_name,
        init_wait_time,
        duration_start_time=duration_start_time
    )

    # Polling fail policy:
    # 這是真正 onboarding / ping 判定失敗，才收 diag 並 reboot TSM4+RE。
    if not result:
        log_progress(f"!! {interface_name} 判定失敗，收集診斷 Log 並執行 TSM4+RE recovery !!")
        write_recovery_note(interface_name)
        safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_{interface_name.replace(' ', '_')}_Fail")
        return False

    log_progress(f"{interface_name} 判定 PASS")
    return True





def send_re_serial_command(command, wait_after=0):
    """透過 RE serial console 下指令，並持續收 console log。"""
    try:
        with serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=0.1) as ser:
            ser.write(b"\r\n")
            receive_monitor(1, ser)
            if isinstance(command, str):
                command = command.encode("utf-8")
            ser.write(command)
            if wait_after > 0:
                receive_monitor(wait_after, ser)
        return True
    except Exception as e:
        log_progress(f"Serial command failed: {e}")
        return False


def send_re_serial_command_with_timestamp(command, wait_after=0):
    """透過 RE serial console 下指令，並回傳指令送出成功當下的 timestamp。

    Returns:
        (ok, send_time)
        ok=True  : command 已寫入 serial port，send_time 為 ser.write() 後的 time.time()
        ok=False : command 送出失敗，send_time=None
    """
    try:
        with serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=0.1) as ser:
            ser.write(b"\r\n")
            receive_monitor(1, ser)

            if isinstance(command, str):
                command = command.encode("utf-8")

            ser.write(command)
            send_time = time.time()

            if wait_after > 0:
                receive_monitor(wait_after, ser)

        return True, send_time

    except Exception as e:
        log_progress(f"Serial command failed: {e}")
        return False, None


def run_polling_or_recover(loop, interface_name, init_wait_time, threshold, case_name_suffix, duration_start_time=None):
    """共用 polling policy：polling fail 才收 diag + reboot recovery。"""
    result = poll_booster_console(
        str(loop),
        interface_name,
        init_wait_time,
        threshold,
        duration_start_time=duration_start_time
    )
    if not result:
        log_progress(f"!! {interface_name} 判定失敗，收集診斷 Log 並執行 TSM4+RE recovery !!")
        write_recovery_note(interface_name)
        safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_{case_name_suffix}")
        return False
    return True


def run_test_re_factory_default():
    """Case1: 每一個 loop 都執行 RE factory default -> ETH BH -> WiFi BH onboarding check。

    新流程：
    LOOP 1: RE factory_default -> ETH BH -> WiFi BH
    LOOP 2: RE factory_default -> ETH BH -> WiFi BH
    LOOP N: RE factory_default -> ETH BH -> WiFi BH

    政策：
    - 每個 loop 的 ETH BH 都使用 factory default init wait / threshold。
    - ETH BH FAIL 後停止，不繼續 WiFi BH。
    - Polling FAIL 才收 diag + reboot recovery。
    """
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {TOTAL_LOOPS} Loops) - {TEST_CASE_NAME}")
        log_progress("Case1 policy: 每個 loop 都執行 RE factory default; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, TOTAL_LOOPS + 1):
            loop_label = f"{loop}(Def)"

            # 每個 loop 都重新 factory default
            log_separator(f"LOOP {loop} - Factory Default + ETH BH 測試開始")
            log_progress("STEP: 切換 ETH BH - relay 6 on")
            control_relay("on")
            receive_monitor(2)

            log_progress("STEP: 送出 RE factory_default 指令...")
            cmd_ok, duration_start_time = send_re_serial_command_with_timestamp("factory_default\n", wait_after=0)
            if not cmd_ok:
                write_summary(summary_loop_display(loop_label, "ETH BH"), "ETH BH", "N/A", "FAIL", fail_reason_with_recovery("Serial Command Error"))
                write_recovery_note("ETH BH")
                safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_FactoryDefault_Command_Fail")
                return

            # factory default 之後的 ETH BH onboarding check
            eth_init_wait = CASE1_FACTORY_DEFAULT_INIT_WAIT_TIME
            eth_threshold = CASE1_FACTORY_DEFAULT_THRESHOLD

            result = poll_booster_console(
                loop_label,
                "ETH BH",
                eth_init_wait,
                eth_threshold,
                duration_start_time=duration_start_time
            )
            if not result:
                log_progress(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                write_recovery_note("ETH BH")
                safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_FactoryDefault_ETH_BH_Fail")
                return

            log_progress(f"LOOP {loop} ETH BH PASS，冷卻後切換 WiFi BH。")

            # WiFi BH onboarding check
            log_separator(f"LOOP {loop} - WiFi BH 測試開始")
            log_progress("STEP: 切換 WiFi BH - relay 6 off")
            control_relay("off")
            duration_start_time = time.time()
            receive_monitor(RELAY_SETTLE_TIME)

            wifi_init_wait = CASE1_NORMAL_INIT_WAIT_TIME
            wifi_threshold = CASE1_NORMAL_THRESHOLD

            result = poll_booster_console(
                loop_label,
                "WiFi BH",
                wifi_init_wait,
                wifi_threshold,
                duration_start_time=duration_start_time
            )
            if not result:
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                write_recovery_note("WiFi BH")
                safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_FactoryDefault_WiFi_BH_Fail")
                return

            log_progress(f"LOOP {loop} PASS。")

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")

    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
    except Exception as e:
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")


def run_test_eth_wifi_onboarding():
    """Case2: ETH BH / WiFi BH onboarding check。"""
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {TOTAL_LOOPS} Loops) - {TEST_CASE_NAME}")
        log_progress("Case2 policy: ETH/WiFi onboarding only; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, TOTAL_LOOPS + 1):
            log_separator(f"LOOP {loop} - ETH BH 測試開始")
            control_relay("on")
            duration_start_time = time.time()
            receive_monitor(RELAY_SETTLE_TIME)

            if not run_polling_or_recover(loop, "ETH BH", CASE2_ONBOARDING_INIT_WAIT_TIME, ONBOARDING_THRESHOLD, "ETH_BH_Fail", duration_start_time):
                log_progress(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                return

            log_separator(f"LOOP {loop} - WiFi BH 測試開始")
            control_relay("off")
            duration_start_time = time.time()
            receive_monitor(RELAY_SETTLE_TIME)

            if not run_polling_or_recover(loop, "WiFi BH", CASE2_ONBOARDING_INIT_WAIT_TIME, ONBOARDING_THRESHOLD, "WiFi_BH_Fail", duration_start_time):
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                return

            log_progress(f"LOOP {loop} PASS。")

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")

    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
    except Exception as e:
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")


def run_test_re_warm_reboot():
    """Case3: RE warm reboot -> ETH BH / WiFi BH onboarding check。"""
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {TOTAL_LOOPS} Loops) - {TEST_CASE_NAME}")
        log_progress("Case3 policy: RE warm reboot; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, TOTAL_LOOPS + 1):
            log_separator(f"LOOP {loop} - ETH BH 測試開始")
            log_progress("送出 RE warm reboot 指令: reboot")
            cmd_ok, duration_start_time = send_re_serial_command_with_timestamp("reboot\n", wait_after=0)
            if not cmd_ok:
                write_summary(summary_loop_display(str(loop), "ETH BH"), "ETH BH", "N/A", "FAIL", fail_reason_with_recovery("Serial Command Error"))
                write_recovery_note("ETH BH")
                safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_WarmReboot_Command_Fail")
                return
            receive_monitor(RE_WARM_REBOOT_POST_WAIT)
            control_relay("on")
            receive_monitor(RE_WARM_REBOOT_RELAY_POST_WAIT)

            if not run_polling_or_recover(loop, "ETH BH", RE_WARM_REBOOT_INIT_WAIT_TIME, ONBOARDING_THRESHOLD, "ETH_BH_Fail", duration_start_time):
                log_progress(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                return

            log_separator(f"LOOP {loop} - WiFi BH 測試開始")
            log_progress("送出 RE warm reboot 指令: reboot")
            cmd_ok, duration_start_time = send_re_serial_command_with_timestamp("reboot\n", wait_after=0)
            if not cmd_ok:
                write_summary(summary_loop_display(str(loop), "WiFi BH"), "WiFi BH", "N/A", "FAIL", fail_reason_with_recovery("Serial Command Error"))
                write_recovery_note("WiFi BH")
                safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_WarmReboot_Command_Fail")
                return
            receive_monitor(RE_WARM_REBOOT_POST_WAIT)
            control_relay("off")
            receive_monitor(RE_WARM_REBOOT_RELAY_POST_WAIT)

            if not run_polling_or_recover(loop, "WiFi BH", RE_WARM_REBOOT_INIT_WAIT_TIME, ONBOARDING_THRESHOLD, "WiFi_BH_Fail", duration_start_time):
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                return

            log_progress(f"LOOP {loop} PASS。")

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")

    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
    except Exception as e:
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")


def run_test_re_cold_reboot():
    """Case4: RE cold reboot -> ETH BH / WiFi BH onboarding check。"""
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {TOTAL_LOOPS} Loops) - {TEST_CASE_NAME}")
        log_progress("Case4 policy: RE cold reboot; ETH BH FAIL will not continue WiFi BH.")

        # 確保 RE power relay 初始為 on
        control_relay_channel(RE_COLD_POWER_RELAY_PORT, "on")
        receive_monitor(RELAY_SETTLE_TIME)

        for loop in range(1, TOTAL_LOOPS + 1):
            log_separator(f"LOOP {loop} - ETH BH 測試開始")
            log_progress(f"RE cold reboot: relay {RE_COLD_POWER_RELAY_PORT} off")
            control_relay_channel(RE_COLD_POWER_RELAY_PORT, "off")
            receive_monitor(RE_COLD_REBOOT_POWER_OFF_TIME)

            log_progress(f"RE cold reboot: relay {RE_COLD_POWER_RELAY_PORT} on")
            control_relay_channel(RE_COLD_POWER_RELAY_PORT, "on")
            duration_start_time = time.time()
            receive_monitor(RE_COLD_REBOOT_POST_WAIT)

            control_relay("on")
            receive_monitor(RE_COLD_REBOOT_RELAY_POST_WAIT)

            if not run_polling_or_recover(loop, "ETH BH", RE_COLD_REBOOT_INIT_WAIT_TIME, ONBOARDING_THRESHOLD, "ETH_BH_Fail", duration_start_time):
                log_progress(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                return

            log_separator(f"LOOP {loop} - WiFi BH 測試開始")
            log_progress(f"RE cold reboot: relay {RE_COLD_POWER_RELAY_PORT} off")
            control_relay_channel(RE_COLD_POWER_RELAY_PORT, "off")
            receive_monitor(RE_COLD_REBOOT_POWER_OFF_TIME)

            log_progress(f"RE cold reboot: relay {RE_COLD_POWER_RELAY_PORT} on")
            control_relay_channel(RE_COLD_POWER_RELAY_PORT, "on")
            duration_start_time = time.time()
            receive_monitor(RE_COLD_REBOOT_POST_WAIT)

            control_relay("off")
            receive_monitor(RE_COLD_REBOOT_RELAY_POST_WAIT)

            if not run_polling_or_recover(loop, "WiFi BH", RE_COLD_REBOOT_INIT_WAIT_TIME, ONBOARDING_THRESHOLD, "WiFi_BH_Fail", duration_start_time):
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                return

            log_progress(f"LOOP {loop} PASS。")

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")

    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
    except Exception as e:
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")



def execute_one_tsm4_reboot_backhaul_test(loop, interface_name, relay_state, active_driver=None):
    """Case5 單一 backhaul 測試段落。"""
    log_separator(f"LOOP {loop} - {interface_name} 測試開始")
    log_progress(f"STEP: 準備執行 {interface_name} 測試 (GUI 觸發 TSM4 Restart)")

    gui_ok, duration_start_time = trigger_tsm4_restart(active_driver)
    if not gui_ok:
        write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", gui_fail_reason("GUI Error"))
        log_progress(f"!! {interface_name} GUI 操作失敗，只寫 Summary，不執行 diag / TSM4+RE recovery !!")
        restore_eth_backhaul(f"{interface_name} GUI Error")
        return False

    log_progress(f"等待 TSM4 reboot 後系統穩定 {TSM4_REBOOT_POST_WAIT}s...")
    receive_monitor(TSM4_REBOOT_POST_WAIT)

    log_progress(f"STEP: Relay 切換 ({relay_state.upper()}) 配置 {interface_name} 實體環境")
    control_relay(relay_state)
    receive_monitor(TSM4_REBOOT_RELAY_POST_WAIT)

    init_wait_time = get_init_wait_time_by_action("Reboot TSM4")
    log_progress(f"Polling init wait time 依 action_label='Reboot TSM4' 設定為 {init_wait_time} 秒")
    result = poll_booster_console(
        str(loop),
        interface_name,
        init_wait_time,
        duration_start_time=duration_start_time
    )

    if not result:
        log_progress(f"!! {interface_name} 判定失敗，收集診斷 Log 並執行 TSM4+RE recovery !!")
        write_recovery_note(interface_name)
        safe_handle_fail_recovery(f"Loop{loop}_{CASE_ID}_{interface_name.replace(' ', '_')}_Fail")
        return False

    log_progress(f"{interface_name} 判定 PASS")
    return True


def run_test_tsm4_reboot():
    """Case5 主程式：TSM4 GUI Restart + ETH/WiFi onboarding polling。"""
    active_driver = None

    try:
        router_fw, active_driver = get_router_fw_version()

        log_progress(f"GW FW 取得完成，保留 Chrome，等待 {GW_FW_TO_GUI_ACTION_SLEEP} 秒後繼續 GUI login/navigation...")
        receive_monitor(GW_FW_TO_GUI_ACTION_SLEEP)

        booster_fw = get_booster_fw_version()

        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {TOTAL_LOOPS} Loops) - {TEST_CASE_NAME}")
        log_progress("Case5 policy: TSM4 GUI Restart; GUI Error only writes Summary; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, TOTAL_LOOPS + 1):
            eth_pass = execute_one_tsm4_reboot_backhaul_test(loop, "ETH BH", "on", active_driver)
            active_driver = None

            if not eth_pass:
                log_progress(f"LOOP {loop} ETH BH FAIL / GUI Error，停止測試，不繼續 WiFi BH。")
                return

            wifi_pass = execute_one_tsm4_reboot_backhaul_test(loop, "WiFi BH", "off", None)

            if not wifi_pass:
                log_progress(f"LOOP {loop} WiFi BH FAIL / GUI Error，停止測試。")
                return

            log_progress(f"LOOP {loop} PASS。")

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")

    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")

    except Exception as e:
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")

    finally:
        if active_driver is not None:
            try:
                active_driver.quit()
            except Exception:
                pass



def run_test(action_xpath, action_label):
    """主程式，由各 case 傳入對應的 xpath 和 label 後呼叫。

    目前政策：
    - GUI Error：只寫 Summary，不收 diag，不 reboot TSM4+RE，直接停止。
    - ETH BH FAIL：收 diag + reboot TSM4+RE，直接停止，不繼續 WiFi BH。
    - WiFi BH FAIL：收 diag + reboot TSM4+RE，直接停止。
    """
    active_driver = None

    try:
        router_fw, active_driver = get_router_fw_version()

        # 保留 get_router_fw_version() 開啟的 Chrome，第一次 ETH BH 直接沿用。
        # _navigate_to_boosters() 會自行判斷是否已登入；若未登入會執行 login。
        log_progress(f"GW FW 取得完成，保留 Chrome，等待 {GW_FW_TO_GUI_ACTION_SLEEP} 秒後繼續 GUI login/navigation...")
        receive_monitor(GW_FW_TO_GUI_ACTION_SLEEP)

        booster_fw = get_booster_fw_version()

        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {TOTAL_LOOPS} Loops) - {TEST_CASE_NAME}")
        log_progress("Fail policy: GUI Error only writes Summary; any FAIL stops current script; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, TOTAL_LOOPS + 1):
            eth_pass = execute_one_backhaul_test(
                loop, "ETH BH", "on", action_xpath, action_label, active_driver
            )
            active_driver = None

            if not eth_pass:
                log_progress(f"LOOP {loop} ETH BH FAIL / GUI Error，停止測試，不繼續 WiFi BH。")
                return

            wifi_pass = execute_one_backhaul_test(
                loop, "WiFi BH", "off", action_xpath, action_label
            )

            if not wifi_pass:
                log_progress(f"LOOP {loop} WiFi BH FAIL / GUI Error，停止測試。")
                return

            log_progress(f"LOOP {loop} PASS。")

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")

    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")

    except Exception as e:
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")

    finally:
        if active_driver is not None:
            try:
                active_driver.quit()
            except Exception:
                pass


