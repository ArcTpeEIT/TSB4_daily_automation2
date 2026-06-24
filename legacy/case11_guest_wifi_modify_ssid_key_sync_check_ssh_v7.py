#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
guest_wifi_modify_ssid_key_sync_check.py

用途:
    透過 TSM4 GUI 修改 Guest WiFi SSID / key，並透過 RE SSH 檢查設定是否同步。

測試流程:
    1. ETH BH: relay 6 on
    2. GUI 修改第一組隨機 Guest SSID / key
    3. receive_monitor ETH_AFTER_GUI_APPLY_MONITOR_TIME 秒
    4. 透過 RE serial console 執行 ifconfig br-lan 取得 RE IP，再 SSH 查 UCI value 是否同步
    5. PASS 後 receive_monitor PASS_COOLDOWN_TIME 秒
    6. WiFi BH: relay 6 off
    7. GUI 修改第二組隨機 Guest SSID / key
    8. receive_monitor WIFI_AFTER_GUI_APPLY_MONITOR_TIME 秒
    9. 透過 RE serial console 執行 ifconfig br-lan 取得 RE IP，再 SSH 查 UCI value 是否同步
    10. 若還有下一個 loop，WiFi BH PASS 後先切回 ETH BH 並等待 LOOP_ETH_RESTORE_WAIT 秒
    11. 若任一階段 FAIL，執行 check_RE_status.py <COM port> 並將輸出附加到 Summary log
    11. 全部 PASS 後 relay 6 on 切回 ETH BH，再進 Web GUI 將 Guest WiFi 關閉

安裝:
    pip install selenium webdriver-manager pyserial

執行:
    python guest_wifi_modify_ssid_key_sync_check.py
"""

import argparse
import datetime
import io
import os
import random
import re
import string
import subprocess
import sys
import time
import traceback

import serial
import paramiko
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


# ==========================================================
# 基本參數
# ==========================================================

TOTAL_LOOPS = 2

# Serial / relay
BOOSTER_PORT = "COM4"
RELAY_PORT = "COM3"
BAUD_RATE = 115200
RELAY_ETH_PORT = 6

# GUI
GATEWAY_URL = "http://192.168.0.1/"
WAIT_TIMEOUT = 60
ROUTER_USERNAME = "admin"
ROUTER_PASSWORD = "5nvvnaf3vr"

# RE SSH login，用於 UCI value check，避免 serial console 截字 / 被 prompt 干擾。
# 預設會先透過 RE serial console 執行 ifconfig br-lan，自動抓 192.168.x.x 再 SSH login。
# 若現場需要固定 IP，可用 --ssh-host 手動覆蓋，手動指定時會跳過 serial IP auto discovery。
SSH_HOST = None
SSH_PORT = 22
SSH_USERNAME = "25g5@rIj2Z"
SSH_PASSWORD = "x@u4194j042u/4m,4@"
SSH_TIMEOUT = 10
SSH_AUTO_DISCOVER_HOST = True
SSH_DISCOVER_INTERFACE = "br-lan"
SSH_DISCOVER_CMD_TIMEOUT = 8

# Random SSID / key
ETH_SSID_PREFIX = "ETHGUEST"
WIFI_SSID_PREFIX = "WIFIGUEST"
SSID_RANDOM_LEN = 8
WIFI_KEY_RANDOM_LEN = 14

# 必須包含特殊字元。避免使用 quotes/backslash/space 這類容易干擾 GUI / shell 的字元。
SPECIAL_CHARS = "!@#$%^&*_-+=?"

# Timing
AFTER_RELAY_SWITCH_WAIT = 2
ETH_AFTER_GUI_APPLY_MONITOR_TIME = 30
WIFI_AFTER_GUI_APPLY_MONITOR_TIME = 210
PASS_COOLDOWN_TIME = 30
# WiFi BH PASS 後若還有下一個 loop，先切回 ETH BH 並等待 mesh / backhaul sync 穩定。
LOOP_ETH_RESTORE_WAIT = 60
RESTORE_ETH_BH_WAIT = 10
AFTER_WIFI_PAGE_CLICK_WAIT = 10
AFTER_FIELD_SCROLL_WAIT = 0.5
BEFORE_APPLY_WAIT = 1.5
AFTER_APPLY_CLICK_WAIT = 1
AFTER_APPLY_DONE_WAIT = 2
BEFORE_QUIT_WAIT = 3

# Behavior
HEADLESS = False
DO_SIGN_OUT = True
SAVE_SCREENSHOT_ON_ERROR = True

# Fail diagnostic
CHECK_RE_STATUS_SCRIPT = "check_RE_status.py"
CHECK_RE_STATUS_TIMEOUT = 120
# None 表示跟 BOOSTER_PORT 使用同一個 COM port；目前預設會帶 COM4。
CHECK_RE_STATUS_COM_PORT = None
# 預設用 positional argument：python check_RE_status.py COM4
# 若你的 check_RE_status.py 是用 --com-port COM4，執行主程式時可加：
#   --check-re-status-com-port-arg --com-port
CHECK_RE_STATUS_COM_PORT_ARG = ""

# ==========================================================
# XPath
# ==========================================================

XPATH_LOGIN_USER = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[1]/input"
XPATH_LOGIN_PASS = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[2]/input"

XPATH_WIFI_SETTINGS = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[3]/div/div/a/p"

# Guest WiFi page
XPATH_WIFI_GUEST = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/nav/div/ul/li[10]/a"
XPATH_WIFI_GUEST_BUTTON = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[2]/div/div/div/app-label-toggle/div/div[2]/div"
XPATH_WIFI_GUEST_SSID = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[8]/app-label-input/div/div[2]/input"
XPATH_WIFI_GUEST_KEY = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[3]/div[8]/div/div/div/input"
XPATH_GUEST_APPLY = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[5]/div/button[2]"
XPATH_DISCARD_CHANGES_YES = "/html/body/ngb-modal-window/div/div/app-modal-discard-changes/div[3]/div/button[2]"

XPATH_SIGNOUT = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[3]/div[2]/button"

# ==========================================================
# UCI check commands
# ==========================================================

SSID_UCI_CMDS = [
    "uci get wireless.@wifi-iface[7].ssid",
    "uci get wireless.mld8.mld_ssid",
    "uci get wireless.@wifi-iface[8].ssid",
]

KEY_UCI_CMDS = [
    "uci get wireless.@wifi-iface[7].sae_password",
    "uci get wireless.@wifi-iface[7].key",
    "uci get wireless.@wifi-iface[8].sae_password",
    "uci get wireless.@wifi-iface[8].key",
]

# ==========================================================
# Log files
# ==========================================================

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
except Exception:
    pass

CURRENT_START_TIME = datetime.datetime.now().strftime("%Y%m%d_%H%M")
TEST_CASE_NAME = "case11_Guest_WiFi_Random_SSID_Key_Sync_SpecialChar"
FULL_CONSOLE_LOG = f"{CURRENT_START_TIME}_{TEST_CASE_NAME}_Console.log"
SUMMARY_LOG = f"{CURRENT_START_TIME}_{TEST_CASE_NAME}_Summary.log"

# RE serial 會在程式一開始開啟，等待期間都用 receive_monitor() 收 log。
SERIAL_HANDLE = None


def log(message):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    line = f"{ts} [PROGRESS] >>> {message}\n"
    with open(FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    sys.stdout.write(line)
    sys.stdout.flush()


def log_separator(message):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    border = "=" * 72
    msg = f"\n{ts} {border}\n{ts} [PHASE START] >>> {message}\n{ts} {border}\n"
    with open(FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
        f.write(msg)
    sys.stdout.write(msg)
    sys.stdout.flush()


def init_summary_log():
    write_header = not os.path.exists(SUMMARY_LOG) or os.path.getsize(SUMMARY_LOG) == 0
    with open(SUMMARY_LOG, "a", encoding="utf-8") as f:
        if write_header:
            f.write(f"{TEST_CASE_NAME}\n")
            f.write("-" * 110 + "\n")
            f.write(
                f"{'Time':<20} | {'Loop':<8} | {'Interface':<12} | "
                f"{'SSID':<24} | {'Key':<18} | {'Result':<8} | {'Fail_Reason'}\n"
            )
            f.write("-" * 110 + "\n")


def format_summary_reason(result, reason):
    """主表只顯示短 Fail_Reason，詳細原因另外寫在下方。"""
    if result == "PASS":
        return "None"

    if not reason or reason == "None":
        return "FAIL"

    return "See details"


def resolve_check_re_status_script():
    """尋找 check_RE_status.py。

    優先順序：
        1. CHECK_RE_STATUS_SCRIPT 若為絕對路徑，直接使用。
        2. 主程式同一層資料夾。
        3. 執行 python 指令時的目前工作目錄。
    """
    if os.path.isabs(CHECK_RE_STATUS_SCRIPT):
        return CHECK_RE_STATUS_SCRIPT

    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, CHECK_RE_STATUS_SCRIPT),
        os.path.join(os.getcwd(), CHECK_RE_STATUS_SCRIPT),
    ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    # 找不到時仍回傳主程式同層路徑，方便 log 顯示預期位置。
    return candidates[0]


def run_fail_diagnostic(loop_str, interface_name):
    """FAIL 時額外執行 check_RE_status.py，並回傳要附加到 Summary log 的文字。

    這個 function 會同時：
        1. 在 CMD / console 顯示 check_RE_status.py 執行狀態。
        2. 把 stdout / stderr 寫入 FULL_CONSOLE_LOG。
        3. 回傳同一份內容，讓 write_summary() 附加到 SUMMARY_LOG。
    """
    script_path = resolve_check_re_status_script()
    script_dir = os.path.dirname(os.path.abspath(script_path))
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    diag_com_port = CHECK_RE_STATUS_COM_PORT or BOOSTER_PORT

    header = (
        f"  Check_RE_Status[{ts}][Loop={loop_str or '-'}][{interface_name}]\n"
        f"    Script: {script_path}\n"
        f"    COM_Port: {diag_com_port}\n"
    )

    log(f"[FAIL_DIAG] 準備執行 {CHECK_RE_STATUS_SCRIPT}")
    log(f"[FAIL_DIAG] script_path={script_path}")
    log(f"[FAIL_DIAG] com_port={diag_com_port}")

    if not os.path.exists(script_path):
        msg = f"    - SKIP: {CHECK_RE_STATUS_SCRIPT} not found. Expected path: {script_path}\n"
        log(f"[FAIL_DIAG] {CHECK_RE_STATUS_SCRIPT} 不存在，略過執行")
        return header + msg + "\n"

    cmd = [sys.executable, script_path]
    if CHECK_RE_STATUS_COM_PORT_ARG:
        cmd.extend([CHECK_RE_STATUS_COM_PORT_ARG, diag_com_port])
    else:
        cmd.append(diag_com_port)

    log(f"[FAIL_DIAG][CMD] {' '.join(cmd)}")

    serial_closed_for_diag = False
    try:
        if diag_com_port.upper() == BOOSTER_PORT.upper():
            try:
                if SERIAL_HANDLE is not None and SERIAL_HANDLE.is_open:
                    log("[FAIL_DIAG] check_RE_status.py 需要使用同一個 COM port，先暫時關閉主程式 RE serial")
                    close_re_serial()
                    serial_closed_for_diag = True
            except Exception as e:
                log(f"[FAIL_DIAG] 檢查 / 關閉主程式 RE serial 發生異常: {type(e).__name__}: {e}")

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=CHECK_RE_STATUS_TIMEOUT,
            cwd=script_dir,
        )

        if serial_closed_for_diag:
            open_re_serial()
            serial_closed_for_diag = False
            log("[FAIL_DIAG] check_RE_status.py 執行後已重新開啟主程式 RE serial")

        log(f"[FAIL_DIAG] {CHECK_RE_STATUS_SCRIPT} 執行完成 exit_code={proc.returncode}")

        stdout_text = proc.stdout.strip()
        stderr_text = proc.stderr.strip()

        if stdout_text:
            for line in stdout_text.splitlines():
                log(f"[FAIL_DIAG][STDOUT] {line}")
        else:
            log("[FAIL_DIAG][STDOUT] <empty>")

        if stderr_text:
            for line in stderr_text.splitlines():
                log(f"[FAIL_DIAG][STDERR] {line}")
        else:
            log("[FAIL_DIAG][STDERR] <empty>")

        output_lines = [
            f"    Exit_Code: {proc.returncode}",
            "    STDOUT:",
        ]

        if stdout_text:
            output_lines.extend(f"      {line}" for line in stdout_text.splitlines())
        else:
            output_lines.append("      <empty>")

        output_lines.append("    STDERR:")
        if stderr_text:
            output_lines.extend(f"      {line}" for line in stderr_text.splitlines())
        else:
            output_lines.append("      <empty>")

        return header + "\n".join(output_lines) + "\n\n"

    except subprocess.TimeoutExpired:
        if serial_closed_for_diag:
            try:
                open_re_serial()
                log("[FAIL_DIAG] timeout 後已重新開啟主程式 RE serial")
            except Exception as reopen_error:
                log(f"[FAIL_DIAG] timeout 後重新開啟 RE serial 失敗: {reopen_error}")
        msg = f"    - TIMEOUT: over {CHECK_RE_STATUS_TIMEOUT} seconds\n"
        log(f"[FAIL_DIAG] {CHECK_RE_STATUS_SCRIPT} 執行逾時，timeout={CHECK_RE_STATUS_TIMEOUT}s")
        return header + msg + "\n"

    except Exception as e:
        if serial_closed_for_diag:
            try:
                open_re_serial()
                log("[FAIL_DIAG] exception 後已重新開啟主程式 RE serial")
            except Exception as reopen_error:
                log(f"[FAIL_DIAG] exception 後重新開啟 RE serial 失敗: {reopen_error}")
        msg = f"    - ERROR: {type(e).__name__}: {e}\n"
        log(f"[FAIL_DIAG] {CHECK_RE_STATUS_SCRIPT} 執行失敗: {type(e).__name__}: {e}")
        return header + msg + "\n"

def write_summary(loop_str, interface_name, ssid, key, result, reason):
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    short_reason = format_summary_reason(result, reason)

    line = (
        f"{ts:<20} | {loop_str:<8} | {interface_name:<12} | "
        f"{ssid:<24} | {key:<18} | {result:<8} | {short_reason}\n"
    )

    with open(SUMMARY_LOG, "a", encoding="utf-8") as f:
        f.write(line)

        if result != "PASS" and reason and reason != "None":
            f.write(f"  Fail_Detail[{ts}][Loop={loop_str or '-'}][{interface_name}]\n")

            detail_items = [item.strip() for item in str(reason).split(";") if item.strip()]
            if not detail_items:
                detail_items = [str(reason)]

            for item in detail_items:
                f.write(f"    - {item}\n")

            f.write("\n")

        if result != "PASS":
            f.write(run_fail_diagnostic(loop_str, interface_name))

# ==========================================================
# Random generation
# ==========================================================

def generate_random_value(prefix, total_random_len):
    """
    產生一定包含特殊字元的 random string。
    SSID / key 都會包含 SPECIAL_CHARS 其中至少一個字元。
    """
    normal_chars = string.ascii_letters + string.digits
    special = random.choice(SPECIAL_CHARS)

    if total_random_len < 2:
        total_random_len = 2

    random_part = [random.choice(normal_chars) for _ in range(total_random_len - 1)]
    random_part.append(special)
    random.shuffle(random_part)

    return f"{prefix}-{''.join(random_part)}"


def generate_wifi_profile(prefix):
    ssid = generate_random_value(prefix, SSID_RANDOM_LEN)
    key = generate_random_value("K", WIFI_KEY_RANDOM_LEN)

    if len(key) < 8 or len(key) > 63:
        raise ValueError(f"Generated WiFi key length invalid: {len(key)}")

    return ssid, key


# ==========================================================
# Serial / relay
# ==========================================================

def open_re_serial():
    """程式一開始開啟 RE serial port，後續等待都用同一個 port 收 console log。"""
    global SERIAL_HANDLE

    if SERIAL_HANDLE is not None:
        try:
            if SERIAL_HANDLE.is_open:
                return SERIAL_HANDLE
        except Exception:
            pass

    SERIAL_HANDLE = serial.Serial(BOOSTER_PORT, BAUD_RATE, timeout=0.1)
    log(f"[SERIAL] 已開啟 RE console port: {BOOSTER_PORT}")
    return SERIAL_HANDLE


def close_re_serial():
    global SERIAL_HANDLE

    if SERIAL_HANDLE is not None:
        try:
            if SERIAL_HANDLE.is_open:
                SERIAL_HANDLE.close()
                log(f"[SERIAL] 已關閉 RE console port: {BOOSTER_PORT}")
        except Exception as e:
            log(f"[SERIAL] 關閉 RE console port 失敗: {e}")
        finally:
            SERIAL_HANDLE = None


def receive_monitor(wait_seconds, ser=None):
    """等待期間持續收 RE console log。

    若 ser=None，會使用程式一開始開好的 SERIAL_HANDLE。
    """
    start_time = time.time()
    collected_output = ""
    close_after_use = False

    try:
        if ser is None:
            ser = open_re_serial()

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
        log(f"Serial Monitor Error: {e}")

    finally:
        if close_after_use and ser:
            try:
                ser.close()
            except Exception:
                pass

    return collected_output



def control_relay_channel(channel, state):
    try:
        with serial.Serial(RELAY_PORT, BAUD_RATE, timeout=1) as ser:
            ser.write(f"relay {channel} {state}\r".encode("utf-8"))
        log(f"[RELAY] 成功下達指令: relay {channel} {state}")
        return True
    except Exception as e:
        log(f"[RELAY] 操作失敗: relay {channel} {state}, error={e}")
        return False


def set_backhaul(interface_name):
    if interface_name == "ETH BH":
        log("切換 ETH BH: relay 6 on")
        return control_relay_channel(RELAY_ETH_PORT, "on")

    if interface_name == "WiFi BH":
        log("切換 WiFi BH: relay 6 off")
        return control_relay_channel(RELAY_ETH_PORT, "off")

    raise ValueError(f"Unsupported interface_name: {interface_name}")



def restore_eth_backhaul(reason="test finished"):
    """PASS / FAIL 結果出來後，等待 RESTORE_ETH_BH_WAIT 秒再切回 ETH BH。"""
    try:
        log(f"[RESTORE] {reason}: receive_monitor {RESTORE_ETH_BH_WAIT} 秒後切回 ETH BH relay 6 on")
        receive_monitor(RESTORE_ETH_BH_WAIT)
        ok = control_relay_channel(RELAY_ETH_PORT, "on")
        receive_monitor(AFTER_RELAY_SWITCH_WAIT)
        if ok:
            log("[RESTORE] 已切回 ETH BH: relay 6 on")
        else:
            log("[RESTORE] 切回 ETH BH 失敗: relay command failed")
        return ok
    except Exception as e:
        log(f"[RESTORE] 切回 ETH BH 發生異常: {type(e).__name__}: {e}")
        return False


def serial_exec_console_command(cmd, timeout=8):
    """透過已開啟的 RE serial console 下 command，回傳 console output。

    注意：此函式假設 serial console 已在 shell prompt 或可直接收 command 的狀態。
    若現場 console 停在 login prompt，需要先手動登入或另外加 login handling。
    """
    ser = open_re_serial()
    output = ""

    try:
        # 清掉 command 前殘留 buffer，避免把舊 log 誤判成 ifconfig 結果。
        if ser.in_waiting > 0:
            stale = ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
            if stale.strip():
                log("[SERIAL][CMD] 清除 command 前殘留 serial buffer")

        log(f"[SERIAL][CMD] {cmd}")
        ser.write(b"\r")
        time.sleep(0.2)
        ser.write((cmd + "\r").encode("utf-8"))

        start_time = time.time()
        while (time.time() - start_time) < float(timeout):
            if ser.in_waiting > 0:
                raw = ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
                output += raw
            time.sleep(0.1)

        if output.strip():
            for line in output.splitlines():
                if line.strip():
                    log(f"[SERIAL][CMD_OUT] {line.strip()}")
        else:
            log("[SERIAL][CMD_OUT] <empty>")

    except Exception as e:
        log(f"[SERIAL][CMD] 執行失敗: {type(e).__name__}: {e}")

    return output


def extract_br_lan_ipv4(ifconfig_output):
    """從 ifconfig br-lan output 抓出 192.168.x.x IPv4。"""
    candidates = []

    # BusyBox / net-tools 常見格式：
    #   inet addr:192.168.0.122
    #   inet 192.168.0.122  netmask 255.255.255.0
    patterns = [
        r"inet addr:(192\.168\.\d{1,3}\.\d{1,3})",
        r"\binet\s+(192\.168\.\d{1,3}\.\d{1,3})",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, ifconfig_output):
            candidates.append(match)

    for ip in candidates:
        parts = ip.split(".")
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            continue

        if len(nums) != 4:
            continue

        # 排除 network / broadcast 這類不適合 SSH 的位址。
        if all(0 <= x <= 255 for x in nums) and nums[-1] not in (0, 255):
            return ip

    return None


def discover_ssh_host_from_serial():
    """用 serial console 執行 ifconfig br-lan，自動取得 RE br-lan 192.168.x.x IP。"""
    cmd = f"ifconfig {SSH_DISCOVER_INTERFACE}"
    log(f"[SSH][DISCOVER] 透過 serial console 取得 {SSH_DISCOVER_INTERFACE} IP: {cmd}")

    output = serial_exec_console_command(cmd, timeout=SSH_DISCOVER_CMD_TIMEOUT)
    ip = extract_br_lan_ipv4(output)

    if not ip:
        raise RuntimeError(
            f"無法從 serial command '{cmd}' output 解析 192.168.x.x，"
            "請確認 console 已登入 shell，或使用 --ssh-host 手動指定 RE IP"
        )

    log(f"[SSH][DISCOVER] 從 {SSH_DISCOVER_INTERFACE} 解析到 RE SSH host={ip}")
    return ip


def resolve_ssh_host():
    """取得 SSH host。若 --ssh-host 有指定，直接使用；否則 serial auto discovery。"""
    if SSH_HOST:
        log(f"[SSH][DISCOVER] 使用手動指定 SSH host={SSH_HOST}")
        return SSH_HOST

    if not SSH_AUTO_DISCOVER_HOST:
        raise RuntimeError("SSH_HOST 未設定，且 SSH_AUTO_DISCOVER_HOST=False")

    return discover_ssh_host_from_serial()


def create_ssh_client():
    """建立 RE SSH client，用於讀取 UCI value。"""
    ssh_host = resolve_ssh_host()
    log(f"[SSH] 嘗試登入 RE host={ssh_host}, port={SSH_PORT}, user={SSH_USERNAME}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh.connect(
        hostname=ssh_host,
        port=SSH_PORT,
        username=SSH_USERNAME,
        password=SSH_PASSWORD,
        timeout=SSH_TIMEOUT,
        banner_timeout=SSH_TIMEOUT,
        auth_timeout=SSH_TIMEOUT,
        look_for_keys=False,
        allow_agent=False,
    )

    log("[SSH] 登入成功")
    return ssh


def ssh_exec(ssh, cmd, timeout=20):
    """透過 SSH 執行 command，回傳 stdout/stderr/exit_code。"""
    log(f"[SSH][CMD] {cmd}")

    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)

    out = stdout.read().decode("utf-8", errors="ignore").strip()
    err = stderr.read().decode("utf-8", errors="ignore").strip()
    exit_code = stdout.channel.recv_exit_status()

    if out:
        for line in out.splitlines():
            log(f"[SSH][STDOUT] {line}")

    if err:
        for line in err.splitlines():
            log(f"[SSH][STDERR] {line}")

    log(f"[SSH][CMD] exit_code={exit_code}")
    return out, err, exit_code


def read_uci_value_ssh(ssh, cmd):
    """透過 SSH 讀取單一 UCI value。"""
    out, err, exit_code = ssh_exec(ssh, cmd)

    if exit_code != 0:
        return f"__SSH_CMD_FAIL__ exit={exit_code}, stderr={err}"

    # uci get 正常只會回一行 value；若多行取最後一個非空白行。
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def check_re_wifi_sync(expected_ssid, expected_key):
    """透過 SSH 檢查 RE 上的 Guest WiFi SSID/key 是否同步。

    serial port 仍然只負責背景 console log 收集；value check 改走 SSH，
    避免 serial console echo/prompt/buffer 導致值被截斷。
    """
    log("開始透過 RE SSH 檢查 Guest WiFi SSID/key 是否同步")
    failures = []
    ssh = None

    try:
        ssh = create_ssh_client()

        for cmd in SSID_UCI_CMDS:
            actual = read_uci_value_ssh(ssh, cmd)
            log(f"[CHECK][SSID] {cmd} => {actual}")

            if actual != expected_ssid:
                failures.append(f"{cmd}: expected='{expected_ssid}', actual='{actual}'")

        for cmd in KEY_UCI_CMDS:
            actual = read_uci_value_ssh(ssh, cmd)
            log(f"[CHECK][KEY] {cmd} => <hidden>")

            if actual != expected_key:
                failures.append(f"{cmd}: expected='<hidden>', actual='<hidden>'")

    except Exception as e:
        failures.append(f"SSH check failed: {type(e).__name__}: {e}")

    finally:
        if ssh:
            try:
                ssh.close()
                log("[SSH] connection closed")
            except Exception:
                pass

    if failures:
        log("RE Guest WiFi sync check FAIL")
        for item in failures:
            log(f"  - {item}")
        return False, "; ".join(failures)

    log("RE Guest WiFi sync check PASS")
    return True, "None"


# ==========================================================
# GUI
# ==========================================================

def create_driver(headless=False):
    options = webdriver.ChromeOptions()

    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")

    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def wait_loading_done(wait, timeout_note="loadingModal"):
    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        log(f"等待 {timeout_note} 消失逾時或未出現，繼續流程")


def js_set_input_value(driver, element, text):
    driver.execute_script(
        """
        const input = arguments[0];
        const text = arguments[1];

        input.focus();
        input.value = text;

        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.blur();
        """,
        element,
        text,
    )


def js_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    receive_monitor(AFTER_FIELD_SCROLL_WAIT)
    driver.execute_script("arguments[0].click();", element)


def is_logged_in(driver):
    try:
        short_wait = WebDriverWait(driver, 5)
        short_wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_SETTINGS)))
        return True
    except Exception:
        return False


def login_gateway(driver, wait, username, password):
    log("開啟 TSM4 GUI")
    driver.get(GATEWAY_URL)
    receive_monitor(2)

    if is_logged_in(driver):
        log("偵測到 Web GUI 已登入，略過登入流程。")
        return

    log("Web GUI 尚未登入，執行登入流程...")

    user_input = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_LOGIN_USER)))
    user_input.clear()
    user_input.send_keys(username)
    receive_monitor(0.3)

    pass_input = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_LOGIN_PASS)))
    pass_input.clear()

    log("使用 JS 強制填入 password 並派發 input/change event")
    js_set_input_value(driver, pass_input, password)
    receive_monitor(0.5)

    submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
    driver.execute_script("arguments[0].click();", submit_btn)

    wait_loading_done(wait)
    receive_monitor(2)

    if not is_logged_in(driver):
        log("登入後尚未偵測到 WiFi Settings，額外等待 3 秒再確認...")
        receive_monitor(3)
        if not is_logged_in(driver):
            raise RuntimeError("登入後仍找不到 WiFi Settings，可能登入失敗或 XPath 已變更")

    log("Web GUI login 完成")



def handle_discard_changes_modal(driver, note=""):
    """若切頁時跳出 Discard Changes 視窗，點擊 Yes 繼續切頁。"""
    try:
        short_wait = WebDriverWait(driver, 3)
        discard_yes = short_wait.until(
            EC.element_to_be_clickable((By.XPATH, XPATH_DISCARD_CHANGES_YES))
        )
        log(f"偵測到 Discard Changes 視窗，點擊 Yes 繼續切頁 {note}")
        driver.execute_script("arguments[0].click();", discard_yes)
        receive_monitor(1.5)
        return True
    except Exception:
        return False


def get_guest_wifi_toggle_text(driver, toggle):
    """讀取 Guest Enable Wireless toggle 附近文字，用於判斷 On / Off。"""
    return driver.execute_script(
        """
        let e = arguments[0];
        for (let i = 0; i < 6 && e; i++) {
            if (e.innerText && (e.innerText.includes('On') || e.innerText.includes('Off'))) {
                return e.innerText;
            }
            e = e.parentElement;
        }
        return arguments[0].innerText || '';
        """,
        toggle,
    )


def set_guest_wifi_enabled(driver, wait, enable=True):
    """設定 Guest WiFi Enable Wireless。

    enable=True  : 若目前 Off，點 toggle 打開。
    enable=False : 若目前 On，點 toggle 關閉。
    """
    desired_text = "On" if enable else "Off"
    opposite_text = "Off" if enable else "On"

    toggle = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_GUEST_BUTTON)))
    state_text = str(get_guest_wifi_toggle_text(driver, toggle))
    log(f"Guest WiFi toggle 狀態文字: {state_text}")

    need_click = opposite_text in state_text

    if need_click:
        log(f"Guest WiFi 目前不是 {desired_text}，點擊 Enable Wireless toggle 切成 {desired_text}")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", toggle)
        receive_monitor(AFTER_FIELD_SCROLL_WAIT)
        driver.execute_script("arguments[0].click();", toggle)
        receive_monitor(2)
    else:
        log(f"Guest WiFi 看起來已是 {desired_text}，略過 toggle")

    return True


def ensure_guest_wifi_enabled(driver, wait):
    """Guest WiFi sync test 前，確保 Guest WiFi 是 On。"""
    return set_guest_wifi_enabled(driver, wait, enable=True)


def navigate_to_guest_page(driver, wait):
    """登入後進入 WiFi Settings -> Guest page，不改 Guest enable 狀態。"""
    log("進入 WiFi Settings")
    wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_SETTINGS)))
    js_click(driver, wifi_link)

    log(f"等待 WiFi 頁面切換與動畫完成 {AFTER_WIFI_PAGE_CLICK_WAIT}s")
    receive_monitor(AFTER_WIFI_PAGE_CLICK_WAIT)

    log("進入 WiFi Guest page")
    guest_link = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_GUEST)))
    js_click(driver, guest_link)

    # 從 WiFi Basic 切到 Guest 時，GUI 有時會跳 Discard Changes。
    # 這裡選 Yes，丟棄目前 Basic page 未保存狀態，繼續切到 Guest page。
    handle_discard_changes_modal(driver, note="after clicking Guest tab")

    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass

    log(f"等待 Guest WiFi 頁面切換與動畫完成 {AFTER_WIFI_PAGE_CLICK_WAIT}s")
    receive_monitor(AFTER_WIFI_PAGE_CLICK_WAIT)


def open_wifi_settings(driver, wait):
    navigate_to_guest_page(driver, wait)
    ensure_guest_wifi_enabled(driver, wait)


def change_wifi_settings(driver, wait, ssid, wifi_password):
    log(f"修改 Guest SSID = {ssid}")
    ssid_input = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_GUEST_SSID)))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", ssid_input)
    receive_monitor(AFTER_FIELD_SCROLL_WAIT)
    js_set_input_value(driver, ssid_input, ssid)

    log("修改 Guest WiFi Password")
    wifi_pass_input = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_GUEST_KEY)))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", wifi_pass_input)
    receive_monitor(AFTER_FIELD_SCROLL_WAIT)
    js_set_input_value(driver, wifi_pass_input, wifi_password)

    receive_monitor(BEFORE_APPLY_WAIT)


def apply_settings(driver, wait):
    log("按下 Guest Apply 並等待設定儲存")
    apply_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_GUEST_APPLY)))
    js_click(driver, apply_btn)

    receive_monitor(AFTER_APPLY_CLICK_WAIT)
    wait_loading_done(wait)
    receive_monitor(AFTER_APPLY_DONE_WAIT)


def sign_out(driver, wait):
    log("Sign Out")
    signout_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_SIGNOUT)))
    js_click(driver, signout_btn)


def modify_wifi_by_gui(ssid, wifi_password):
    driver = None

    try:
        driver = create_driver(headless=HEADLESS)
        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        login_gateway(driver, wait, ROUTER_USERNAME, ROUTER_PASSWORD)
        open_wifi_settings(driver, wait)
        change_wifi_settings(driver, wait, ssid, wifi_password)
        apply_settings(driver, wait)

        if DO_SIGN_OUT:
            sign_out(driver, wait)

        return True, "None"

    except Exception as e:
        reason = f"GUI modify failed: {type(e).__name__}: {e}"
        log(reason)

        if driver and SAVE_SCREENSHOT_ON_ERROR:
            try:
                screenshot = f"wifi_modify_error_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                driver.save_screenshot(screenshot)
                log(f"已儲存錯誤截圖: {screenshot}")
            except Exception as screenshot_error:
                log(f"截圖失敗: {screenshot_error}")

        traceback.print_exc()
        return False, reason

    finally:
        if driver:
            receive_monitor(BEFORE_QUIT_WAIT)
            driver.quit()


def disable_guest_wifi_by_gui():
    """整體 PASS 後進 Web GUI 將 Guest WiFi 關閉並 Apply。"""
    driver = None

    try:
        log("Cleanup: 開啟 TSM4 GUI，準備關閉 Guest WiFi")
        driver = create_driver(headless=HEADLESS)
        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        login_gateway(driver, wait, ROUTER_USERNAME, ROUTER_PASSWORD)
        navigate_to_guest_page(driver, wait)
        set_guest_wifi_enabled(driver, wait, enable=False)
        apply_settings(driver, wait)

        if DO_SIGN_OUT:
            sign_out(driver, wait)

        log("Cleanup: Guest WiFi 已關閉")
        return True, "None"

    except Exception as e:
        reason = f"Cleanup Guest WiFi off failed: {type(e).__name__}: {e}"
        log(reason)

        if driver and SAVE_SCREENSHOT_ON_ERROR:
            try:
                screenshot = f"guest_wifi_cleanup_off_error_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                driver.save_screenshot(screenshot)
                log(f"已儲存錯誤截圖: {screenshot}")
            except Exception as screenshot_error:
                log(f"截圖失敗: {screenshot_error}")

        traceback.print_exc()
        return False, reason

    finally:
        if driver:
            receive_monitor(BEFORE_QUIT_WAIT)
            driver.quit()


# ==========================================================
# Test flow
# ==========================================================

def run_one_stage(loop, interface_name, ssid, key):
    log_separator(f"LOOP {loop} - {interface_name} Guest WiFi modify + sync check")
    log(f"Target SSID={ssid}, key=<hidden>")

    if not set_backhaul(interface_name):
        return False, "Relay switch failed"

    receive_monitor(AFTER_RELAY_SWITCH_WAIT)

    gui_ok, gui_reason = modify_wifi_by_gui(ssid, key)
    if not gui_ok:
        return False, gui_reason

    log(f"GUI apply 完成，receive_monitor {ETH_AFTER_GUI_APPLY_MONITOR_TIME} 秒等待 ETH BH RE 同步")
    receive_monitor(ETH_AFTER_GUI_APPLY_MONITOR_TIME)

    sync_ok, sync_reason = check_re_wifi_sync(ssid, key)
    if not sync_ok:
        return False, sync_reason

    return True, "None"


def run_wifi_stage_after_eth_pass(loop, ssid, key):
    """WiFi BH flow.

    ETH BH PASS 後，run_test() 會先等待 PASS_COOLDOWN_TIME 秒。
    進入本函式後：
        1. 立刻 relay 6 off 切到 WiFi BH。
        2. 透過 TSM4 GUI 修改 WiFi BH 用的 Guest SSID/key。
        3. 等 WIFI_AFTER_GUI_APPLY_MONITOR_TIME 秒。
        4. 最後 SSH check RE UCI Guest SSID/key。

    注意：check_re_wifi_sync() 不再執行 chk_Status.sh / WiFi_inf_ChOnOff.sh。
    RE 狀態檢查改由 FAIL diagnostic 的 check_RE_status.py 負責。
    """
    interface_name = "WiFi BH"

    log_separator(f"LOOP {loop} - {interface_name} Guest WiFi modify + sync check")
    log(f"Target SSID={ssid}, key=<hidden>")
    log("WiFi BH 新流程：PASS_COOLDOWN_TIME 後立刻 relay 6 off，再進 GUI 修改 Guest SSID/key")

    if not set_backhaul(interface_name):
        return False, "Relay switch failed"

    receive_monitor(AFTER_RELAY_SWITCH_WAIT)

    gui_ok, gui_reason = modify_wifi_by_gui(ssid, key)
    if not gui_ok:
        return False, gui_reason

    log(f"GUI apply 完成，receive_monitor {WIFI_AFTER_GUI_APPLY_MONITOR_TIME} 秒等待 WiFi BH RE 同步")
    receive_monitor(WIFI_AFTER_GUI_APPLY_MONITOR_TIME)

    sync_ok, sync_reason = check_re_wifi_sync(ssid, key)
    if not sync_ok:
        return False, sync_reason

    return True, "None"


def run_test():
    init_summary_log()

    log_separator("Guest WiFi modify SSID/key sync check start")
    log(f"SSID / key 會自動隨機產生，且一定包含特殊字元: {SPECIAL_CHARS}")

    overall_pass = True

    try:
        for loop in range(1, TOTAL_LOOPS + 1):
            eth_ssid, eth_key = generate_wifi_profile(ETH_SSID_PREFIX)
            wifi_ssid, wifi_key = generate_wifi_profile(WIFI_SSID_PREFIX)

            eth_ok, eth_reason = run_one_stage(loop, "ETH BH", eth_ssid, eth_key)
            write_summary(str(loop), "ETH BH", eth_ssid, eth_key, "PASS" if eth_ok else "FAIL", eth_reason)

            if not eth_ok:
                log(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                overall_pass = False
                break

            log(f"ETH BH PASS，receive_monitor {PASS_COOLDOWN_TIME} 秒後進入 WiFi BH GUI modify flow")
            receive_monitor(PASS_COOLDOWN_TIME)

            wifi_ok, wifi_reason = run_wifi_stage_after_eth_pass(loop, wifi_ssid, wifi_key)
            write_summary("", "WiFi BH", wifi_ssid, wifi_key, "PASS" if wifi_ok else "FAIL", wifi_reason)

            if not wifi_ok:
                log(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                overall_pass = False
                break

            log(f"LOOP {loop} PASS")

            if loop < TOTAL_LOOPS:
                log(f"LOOP {loop} PASS，準備下一輪測試：先切回 ETH BH relay 6 on")
                if not set_backhaul("ETH BH"):
                    log(f"LOOP {loop} 切回 ETH BH 失敗，停止測試。")
                    overall_pass = False
                    break

                log(f"已切回 ETH BH，receive_monitor {LOOP_ETH_RESTORE_WAIT} 秒等待 ETH mesh / backhaul sync 穩定")
                receive_monitor(LOOP_ETH_RESTORE_WAIT)

        if overall_pass:
            log_separator("Guest WiFi modify SSID/key sync check PASS")
        else:
            log_separator("Guest WiFi modify SSID/key sync check FAIL")

    finally:
        restore_eth_backhaul("PASS/FAIL 結果已產生")

        if overall_pass:
            log("整體測試 PASS，切回 ETH BH 後，進 Web GUI 將 Guest WiFi 關閉")
            cleanup_ok, cleanup_reason = disable_guest_wifi_by_gui()
            if not cleanup_ok:
                log(f"Guest WiFi cleanup OFF 失敗: {cleanup_reason}")
        else:
            log("測試未完整 PASS，保留 Guest WiFi 狀態以利 debug，不執行 cleanup OFF")


def parse_args():
    parser = argparse.ArgumentParser(description="Guest WiFi SSID/key modify and RE sync check")

    parser.add_argument("--loops", type=int, default=TOTAL_LOOPS, help="Total test loops")
    parser.add_argument("--gateway-url", default=GATEWAY_URL, help="TSM4 gateway URL")
    parser.add_argument("--router-username", default=ROUTER_USERNAME, help="TSM4 GUI username")
    parser.add_argument("--router-password", default=ROUTER_PASSWORD, help="TSM4 GUI password")
    parser.add_argument("--ssh-host", default=None, help="Manual RE SSH host/IP. If omitted, script runs 'ifconfig br-lan' via serial console and uses discovered 192.168.x.x")
    parser.add_argument("--ssh-discover-interface", default=SSH_DISCOVER_INTERFACE, help="Interface used for serial IP discovery. Default: br-lan")
    parser.add_argument("--ssh-discover-timeout", type=int, default=SSH_DISCOVER_CMD_TIMEOUT, help="Timeout seconds for serial 'ifconfig <interface>' discovery")
    parser.add_argument("--ssh-port", type=int, default=SSH_PORT, help="RE SSH port")
    parser.add_argument("--ssh-username", default=SSH_USERNAME, help="RE SSH username")
    parser.add_argument("--ssh-password", default=SSH_PASSWORD, help="RE SSH password")
    parser.add_argument("--booster-port", default=BOOSTER_PORT, help="RE serial port for console log only")
    parser.add_argument("--relay-port", default=RELAY_PORT, help="Relay serial port")
    parser.add_argument("--loop-eth-restore-wait", type=int, default=LOOP_ETH_RESTORE_WAIT, help="Seconds to wait after switching back to ETH BH between loops")
    parser.add_argument("--eth-ssid-prefix", default=ETH_SSID_PREFIX, help="ETH BH random Guest SSID prefix")
    parser.add_argument("--wifi-ssid-prefix", default=WIFI_SSID_PREFIX, help="WiFi BH random Guest SSID prefix")
    parser.add_argument("--headless", action="store_true", help="Run Chrome in headless mode")
    parser.add_argument("--no-signout", action="store_true", help="Do not sign out after applying settings")
    parser.add_argument("--check-re-status-script", default=CHECK_RE_STATUS_SCRIPT, help="Run this script and append output to Summary log when any stage FAILs")
    parser.add_argument("--check-re-status-com-port", default=None, help="COM port passed to check_RE_status.py when any stage FAILs. Default: same as --booster-port")
    parser.add_argument("--check-re-status-com-port-arg", default=CHECK_RE_STATUS_COM_PORT_ARG, help="Optional argument name before COM port. Default is positional COM port, e.g. check_RE_status.py COM4. Use --check-re-status-com-port-arg --com-port if needed")

    return parser.parse_args()


def apply_args(args):
    global TOTAL_LOOPS
    global GATEWAY_URL, ROUTER_USERNAME, ROUTER_PASSWORD
    global SSH_HOST, SSH_PORT, SSH_USERNAME, SSH_PASSWORD
    global SSH_DISCOVER_INTERFACE, SSH_DISCOVER_CMD_TIMEOUT
    global BOOSTER_PORT, RELAY_PORT
    global LOOP_ETH_RESTORE_WAIT
    global ETH_SSID_PREFIX, WIFI_SSID_PREFIX
    global HEADLESS, DO_SIGN_OUT
    global CHECK_RE_STATUS_SCRIPT, CHECK_RE_STATUS_COM_PORT, CHECK_RE_STATUS_COM_PORT_ARG

    TOTAL_LOOPS = args.loops
    GATEWAY_URL = args.gateway_url
    ROUTER_USERNAME = args.router_username
    ROUTER_PASSWORD = args.router_password

    # 若 --ssh-host 未指定，SSH_HOST 保持 None，後續會透過 serial console 自動抓 br-lan IP。
    SSH_HOST = getattr(args, "ssh_host", None) or None
    SSH_PORT = getattr(args, "ssh_port", SSH_PORT)
    SSH_USERNAME = getattr(args, "ssh_username", SSH_USERNAME)
    SSH_PASSWORD = getattr(args, "ssh_password", SSH_PASSWORD)
    SSH_DISCOVER_INTERFACE = getattr(args, "ssh_discover_interface", SSH_DISCOVER_INTERFACE)
    SSH_DISCOVER_CMD_TIMEOUT = getattr(args, "ssh_discover_timeout", SSH_DISCOVER_CMD_TIMEOUT)

    BOOSTER_PORT = args.booster_port
    RELAY_PORT = args.relay_port
    LOOP_ETH_RESTORE_WAIT = args.loop_eth_restore_wait
    ETH_SSID_PREFIX = args.eth_ssid_prefix
    WIFI_SSID_PREFIX = args.wifi_ssid_prefix
    HEADLESS = args.headless
    DO_SIGN_OUT = not args.no_signout
    CHECK_RE_STATUS_SCRIPT = args.check_re_status_script
    CHECK_RE_STATUS_COM_PORT = args.check_re_status_com_port or BOOSTER_PORT
    CHECK_RE_STATUS_COM_PORT_ARG = args.check_re_status_com_port_arg or ""


def main():
    args = parse_args()
    apply_args(args)

    try:
        open_re_serial()
        run_test()
        return 0
    finally:
        close_re_serial()


if __name__ == "__main__":
    sys.exit(main())
