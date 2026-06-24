#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tsm4_gui_reboot_standalone.py

Standalone TSM4 Web GUI Reboot tool.
No common.py dependency.

Flow:
    1. Open Chrome
    2. Login TSM4 Web GUI if needed
    3. Go to Settings -> Maintenance
    4. Click Restart
    5. Click Confirm YES
    6. Wait 5 seconds
    7. SSH into booster and run: reboot -f
    8. If SSH fails, use serial port to run: reboot -f
    9. Close Chrome

Dependency:
    pip install selenium paramiko pyserial
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import socket
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    paramiko = None

try:
    import serial
except ImportError:
    serial = None

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ==========================================================
# Default config
# ==========================================================
GATEWAY_URL = "http://192.168.0.1/"
WAIT_TIMEOUT = 30
ROUTER_USERNAME = "admin"
ROUTER_PASSWORD = "5nvvnaf3vr"
CHROME_DRIVER_PATH = str(Path(__file__).resolve().parent / "chromedriver.exe")
TSM4_REBOOT_CHROME_CLOSE_WAIT = 5
TSM4_REBOOT_POST_WAIT = 10
BOOSTER_WAIT_AFTER_TSM4_REBOOT = 5
BOOSTER_HOST = "192.168.0.122"
BOOSTER_SSH_PORT = 22
BOOSTER_SSH_USERNAME = "root"
BOOSTER_SSH_PASSWORD = ""
BOOSTER_SSH_TIMEOUT = 10
BOOSTER_REBOOT_COMMAND = "reboot -f"
BOOSTER_SERIAL_PORT = "COM4"
BOOSTER_SERIAL_BAUDRATE = 115200
BOOSTER_SERIAL_TIMEOUT = 3
BOOSTER_SERIAL_LOGIN_WAIT = 1

# XPath
XPATH_LOGIN_USER = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[1]/input"
XPATH_LOGIN_PASS = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[2]/input"
XPATH_WIFI_SETTINGS = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[3]/div/div/a/p"
XPATH_SETTINGS = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[4]/div/div/a/p"
XPATH_MAINTENANCE = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-top-menu/nav/div/ul/li[8]/a"
XPATH_TSM4_RESTART = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-maintenace-main/div/div/app-maintenance-resets/form/div/div[1]/div[2]/button"
XPATH_CONFIRM_YES = "/html/body/ngb-modal-window/div/div/app-generic-modal/div[3]/button[2]"

LOG_FILE = ""


def init_log_file(test_name: str) -> None:
    global LOG_FILE
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    LOG_FILE = f"{ts}_{test_name}.log"


def log(message: str) -> None:
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    line = f"{ts} [GUI] {message}"
    print(line, flush=True)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_separator(message: str) -> None:
    border = "=" * 70
    log(border)
    log(message)
    log(border)


def gui_sleep(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))


def run_booster_reboot_by_ssh(args: argparse.Namespace) -> bool:
    if not args.enable_booster_reboot:
        log("Booster reboot 已停用，略過。")
        return True

    if paramiko is None:
        log("SSH 需要 paramiko，但目前未安裝。")
        return False

    log_separator("Booster Reboot by SSH")
    log(f"SSH 連線 booster: {args.booster_host}:{args.booster_ssh_port}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=args.booster_host,
            port=args.booster_ssh_port,
            username=args.booster_ssh_username,
            password=args.booster_ssh_password,
            timeout=args.booster_ssh_timeout,
            banner_timeout=args.booster_ssh_timeout,
            auth_timeout=args.booster_ssh_timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        log(f"SSH 已連線，執行: {args.booster_reboot_command}")
        stdin, stdout, stderr = client.exec_command(args.booster_reboot_command, timeout=args.booster_ssh_timeout)
        stdin.close()

        # reboot -f 通常會讓連線立即中斷，所以這裡只短暫嘗試讀取輸出，不把斷線視為失敗。
        try:
            stdout.channel.settimeout(2)
            out = stdout.read().decode(errors="ignore").strip()
            err = stderr.read().decode(errors="ignore").strip()
            if out:
                log(f"SSH stdout: {out}")
            if err:
                log(f"SSH stderr: {err}")
        except (socket.timeout, Exception):
            pass

        log("Booster SSH reboot command 已送出。")
        return True
    except Exception as e:
        log(f"Booster SSH reboot 失敗: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


def run_booster_reboot_by_serial(args: argparse.Namespace) -> bool:
    if not args.enable_booster_reboot:
        return True

    if serial is None:
        log("Serial fallback 需要 pyserial，但目前未安裝。")
        return False

    log_separator("Booster Reboot by Serial")
    log(f"Serial port: {args.booster_serial_port}, baudrate: {args.booster_serial_baudrate}")

    try:
        with serial.Serial(
            port=args.booster_serial_port,
            baudrate=args.booster_serial_baudrate,
            timeout=args.booster_serial_timeout,
            write_timeout=args.booster_serial_timeout,
        ) as ser:
            gui_sleep(args.booster_serial_login_wait)
            ser.reset_input_buffer()
            ser.write(b"\r\n")
            ser.flush()
            gui_sleep(0.5)

            log(f"Serial 執行: {args.booster_reboot_command}")
            ser.write((args.booster_reboot_command + "\r\n").encode("ascii", errors="ignore"))
            ser.flush()
            gui_sleep(0.5)

            try:
                data = ser.read(4096).decode(errors="ignore").strip()
                if data:
                    log(f"Serial output: {data}")
            except Exception:
                pass

        log("Booster serial reboot command 已送出。")
        return True
    except Exception as e:
        log(f"Booster serial reboot 失敗: {type(e).__name__}: {e}")
        return False


def run_booster_reboot(args: argparse.Namespace) -> bool:
    if not args.enable_booster_reboot:
        log("Booster reboot 已停用，略過。")
        return True

    log(f"等待 {args.booster_wait} 秒後執行 booster reboot...")
    gui_sleep(args.booster_wait)

    if run_booster_reboot_by_ssh(args):
        return True

    log("SSH 進不去或執行失敗，改用 serial port fallback。")
    return run_booster_reboot_by_serial(args)


def create_chrome_driver(chromedriver_path: str, headless: bool) -> webdriver.Chrome:
    if not os.path.exists(chromedriver_path):
        raise FileNotFoundError(f"找不到 chromedriver.exe: {chromedriver_path}")

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")

    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    log(f"[Chrome] 使用 chromedriver: {chromedriver_path}")
    log(f"[Chrome] headless mode: {headless}")
    service = Service(executable_path=chromedriver_path)
    return webdriver.Chrome(service=service, options=options)


def wait_loading_done(driver: webdriver.Chrome, timeout: int = 10) -> None:
    try:
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass


def try_login_if_needed(driver: webdriver.Chrome, wait: WebDriverWait, url: str, username: str, password: str) -> None:
    driver.get(url)
    gui_sleep(2)

    # Already logged in if WiFi Settings is visible.
    try:
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_SETTINGS)))
        log("偵測到 Web GUI 已登入，略過登入流程。")
        return
    except Exception:
        pass

    log("Web GUI 尚未登入，執行登入流程...")
    user_input = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_LOGIN_USER)))
    user_input.clear()
    user_input.send_keys(username)
    gui_sleep(0.3)

    pass_input = driver.find_element(By.XPATH, XPATH_LOGIN_PASS)
    pass_input.clear()
    driver.execute_script("arguments[0].value = arguments[1];", pass_input, password)
    driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", pass_input)
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", pass_input)
    gui_sleep(0.5)

    submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
    driver.execute_script("arguments[0].click();", submit_btn)
    wait_loading_done(driver)
    gui_sleep(2)


def navigate_to_maintenance(driver: webdriver.Chrome, wait: WebDriverWait, url: str, username: str, password: str) -> None:
    try_login_if_needed(driver, wait, url, username, password)

    log("進入 Settings -> Maintenance")
    settings_link = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_SETTINGS)))
    driver.execute_script("arguments[0].click();", settings_link)
    gui_sleep(1)
    wait_loading_done(driver)
    gui_sleep(1)

    maintenance_link = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_MAINTENANCE)))
    driver.execute_script("arguments[0].click();", maintenance_link)
    gui_sleep(1)
    wait_loading_done(driver)
    gui_sleep(1)


def run_reboot(args: argparse.Namespace) -> bool:
    driver = None
    try:
        log_separator("TSM4 GUI Reboot Start")
        log(f"目標網址: {args.gateway_url}")
        driver = create_chrome_driver(args.chromedriver, args.headless)
        wait = WebDriverWait(driver, args.wait_timeout)

        navigate_to_maintenance(driver, wait, args.gateway_url, args.username, args.password)

        log("點擊 TSM4 Restart button")
        restart_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_TSM4_RESTART)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", restart_btn)
        gui_sleep(0.5)
        driver.execute_script("arguments[0].click();", restart_btn)
        gui_sleep(1)

        log("點擊確認視窗 Yes")
        confirm_yes = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_CONFIRM_YES)))
        driver.execute_script("arguments[0].click();", confirm_yes)
        action_start_time = time.time()

        log("TSM4 GUI reboot 指令已送出")
        log(f"指令確認時間: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(action_start_time))}")

        booster_ok = run_booster_reboot(args)

        if args.close_wait > 0:
            log(f"等待 {args.close_wait} 秒後關閉 Chrome...")
            gui_sleep(args.close_wait)

        if args.post_wait > 0:
            log(f"Chrome 關閉後，額外等待 TSM4 reboot post wait = {args.post_wait} 秒...")

        if booster_ok:
            log_separator("TSM4 GUI Reboot + Booster Reboot Command Done")
            return True

        log_separator("TSM4 GUI Reboot Done, but Booster Reboot Failed")
        return False

    except Exception as e:
        log(f"TSM4 GUI Reboot 執行失敗: {type(e).__name__}: {e}")
        return False

    finally:
        if driver is not None:
            try:
                driver.quit()
                log("Chrome 已關閉")
            except Exception:
                pass
        # Keep this after Chrome is closed so the browser is not kept alive unnecessarily.
        try:
            post_wait = float(getattr(args, "post_wait", 0))
            if post_wait > 0:
                gui_sleep(post_wait)
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone TSM4 GUI Reboot tool, no common.py dependency")
    parser.add_argument("--gateway-url", default=GATEWAY_URL, help="TSM4 Web GUI URL")
    parser.add_argument("--username", default=ROUTER_USERNAME, help="TSM4 Web GUI username")
    parser.add_argument("--password", default=ROUTER_PASSWORD, help="TSM4 Web GUI password")
    parser.add_argument("--chromedriver", default=CHROME_DRIVER_PATH, help="chromedriver.exe path")
    parser.add_argument("--wait-timeout", type=int, default=WAIT_TIMEOUT, help="Selenium WebDriverWait timeout seconds")
    parser.add_argument("--close-wait", type=float, default=TSM4_REBOOT_CHROME_CLOSE_WAIT, help="Seconds to wait after confirm YES before closing Chrome")
    parser.add_argument("--post-wait", type=float, default=0, help=f"Optional wait after Chrome is closed. Common.py old reference was {TSM4_REBOOT_POST_WAIT}s, but default here is 0.")

    parser.add_argument("--enable-booster-reboot", dest="enable_booster_reboot", action="store_true", help="After TSM4 GUI reboot, also reboot booster")
    parser.add_argument("--disable-booster-reboot", dest="enable_booster_reboot", action="store_false", help="Disable booster reboot")
    parser.set_defaults(enable_booster_reboot=True)
    parser.add_argument("--booster-wait", type=float, default=BOOSTER_WAIT_AFTER_TSM4_REBOOT, help="Seconds to wait after TSM4 GUI reboot command before booster reboot")
    parser.add_argument("--booster-host", default=BOOSTER_HOST, help="Booster SSH host/IP")
    parser.add_argument("--booster-ssh-port", type=int, default=BOOSTER_SSH_PORT, help="Booster SSH port")
    parser.add_argument("--booster-ssh-username", default=BOOSTER_SSH_USERNAME, help="Booster SSH username")
    parser.add_argument("--booster-ssh-password", default=BOOSTER_SSH_PASSWORD, help="Booster SSH password")
    parser.add_argument("--booster-ssh-timeout", type=float, default=BOOSTER_SSH_TIMEOUT, help="Booster SSH timeout seconds")
    parser.add_argument("--booster-reboot-command", default=BOOSTER_REBOOT_COMMAND, help="Booster reboot command")
    parser.add_argument("--booster-serial-port", default=BOOSTER_SERIAL_PORT, help="Booster serial port, e.g. COM3 or /dev/ttyUSB0")
    parser.add_argument("--booster-serial-baudrate", type=int, default=BOOSTER_SERIAL_BAUDRATE, help="Booster serial baudrate")
    parser.add_argument("--booster-serial-timeout", type=float, default=BOOSTER_SERIAL_TIMEOUT, help="Booster serial timeout seconds")
    parser.add_argument("--booster-serial-login-wait", type=float, default=BOOSTER_SERIAL_LOGIN_WAIT, help="Seconds to wait after opening serial port")

    headless_default = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    parser.add_argument("--headless", dest="headless", action="store_true", help="Run Chrome in headless mode")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Run Chrome with visible browser")
    parser.set_defaults(headless=headless_default)
    return parser.parse_args()


def main() -> int:
    init_log_file("tsm4_gui_reboot_booster")
    args = parse_args()
    ok = run_reboot(args)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
