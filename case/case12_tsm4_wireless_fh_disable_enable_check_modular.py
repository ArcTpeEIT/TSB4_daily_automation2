#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Case12: TSM4 GUI Wireless Disable/Enable FH sync check.

Purpose:
    Verify that TSM4 GUI Wireless enable/disable state is synced to Booster FH
    UCI state in both ETH BH and WiFi BH stages.

Flow per loop:
    ETH BH:
        relay 6 on -> wait -> GUI disable wireless -> wait -> SSH check FH disabled=1
        GUI enable wireless -> wait -> SSH check FH disabled=0
    WiFi BH:
        relay 6 off -> wait -> same disable/enable FH check

Fail policy:
    - ETH BH FAIL: do not run WiFi BH.
    - Case12 returns rc=1 on FAIL so the current runner can record failure.
    - Case12 runs common fail diagnostic/check_RE_status + ETH restore.
    - Runner-level factory-default/reset GW+RE workaround should not run for case12.
    - If TSM4 enables Wireless but Booster FH remains disabled, Case12 may still
      run its local Booster serial reboot workaround, with COM port released first.
"""
import argparse
import os
import sys
import time
from typing import Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from testlib import config as cfg
from testlib.logger import init_log_filenames, log_progress, log_separator, write_summary, init_summary_log, write_recovery_note
from testlib.env_info import create_chrome_driver, get_environment_fw_versions_close_browser
from testlib.relay import control_relay, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.recovery import safe_handle_fail_recovery
from testlib.serial_console import (
    receive_monitor,
    send_command,
    start_background_serial_logger,
    stop_background_serial_logger,
)
from testlib.ssh_client import discover_ssh_host_by_serial, run_ssh_command, clear_cached_ssh_host
from testlib.web_gui import wait_loading_done, _try_login_if_needed, _js_click


def cfg_get(name, default):
    """Read optional Case12 config while preserving user's existing config.py values."""
    return getattr(cfg, name, default)


# Keep Case12-specific values optional so users do not need to overwrite their tuned config.py.
CASE12_ETH_BH_INIT_WAIT_DEFAULT = cfg_get("CASE12_ETH_BH_INIT_WAIT", 30)
CASE12_WIFI_BH_INIT_WAIT_DEFAULT = cfg_get("CASE12_WIFI_BH_INIT_WAIT", 150)
CASE12_WIRELESS_SYNC_WAIT_DEFAULT = cfg_get("CASE12_WIRELESS_SYNC_WAIT", 30)
CASE12_FAIL_REBOOT_COOLDOWN_DEFAULT = cfg_get("CASE12_FAIL_REBOOT_COOLDOWN", 60)
CASE12_FAIL_REBOOT_CMD_DEFAULT = cfg_get("CASE12_FAIL_REBOOT_CMD", "reboot")
CASE12_ENABLE_RECOVERY_WAIT_DEFAULT = cfg_get("CASE12_ENABLE_RECOVERY_WAIT", 30)
CASE12_GUI_RETRY_WAIT_DEFAULT = cfg_get("CASE12_GUI_RETRY_WAIT", 30)
CASE12_GUI_MAX_ATTEMPTS_DEFAULT = cfg_get("CASE12_GUI_MAX_ATTEMPTS", 2)

FH_24G_CMD = cfg_get("CASE12_FH_24G_DISABLED_CMD", "uci get wireless.@wifi-iface[2].disabled")
FH_5G_CMD = cfg_get("CASE12_FH_5G_DISABLED_CMD", "uci get wireless.@wifi-iface[5].disabled")

XPATH_WIRELESS_ENABLE_TOGGLE = cfg_get(
    "XPATH_WIRELESS_ENABLE_TOGGLE",
    "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[2]/div/div/div/app-label-toggle/div/div[2]/div",
)
XPATH_WIFI_BASIC_APPLY = cfg_get(
    "XPATH_WIFI_BASIC_APPLY",
    "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[4]/div/button[2]",
)


def parse_args():
    parser = argparse.ArgumentParser(description="Case12 TSM4 Wireless Disable/Enable FH sync check")
    parser.add_argument("--loops", type=int, default=cfg.TOTAL_LOOPS)
    parser.add_argument("--booster-port", default=cfg.BOOSTER_PORT)
    parser.add_argument("--relay-port", default=cfg.RELAY_PORT)
    parser.add_argument("--eth-bh-wait", type=int, default=CASE12_ETH_BH_INIT_WAIT_DEFAULT)
    parser.add_argument("--wifi-bh-wait", type=int, default=CASE12_WIFI_BH_INIT_WAIT_DEFAULT)
    parser.add_argument("--sync-wait", type=int, default=CASE12_WIRELESS_SYNC_WAIT_DEFAULT)
    parser.add_argument("--loop-eth-restore-wait", type=int, default=cfg_get("LOOP_ETH_RESTORE_WAIT", 60))
    parser.add_argument("--booster-host", default=cfg_get("ONBOARDING_SSH_HOST", None), help="Booster/RE SSH IP. Default: auto-discover by serial br-lan.")
    parser.add_argument("--ssh-timeout", type=int, default=cfg_get("ONBOARDING_SSH_TIMEOUT", 10))
    parser.add_argument("--fail-reboot-cmd", default=CASE12_FAIL_REBOOT_CMD_DEFAULT)
    parser.add_argument("--fail-reboot-cooldown", type=int, default=CASE12_FAIL_REBOOT_COOLDOWN_DEFAULT)
    parser.add_argument("--enable-recovery-wait", type=int, default=CASE12_ENABLE_RECOVERY_WAIT_DEFAULT, help="Disable-wireless fail workaround: wait after forcing GUI wireless enable.")
    parser.add_argument("--gui-retry-wait", type=int, default=CASE12_GUI_RETRY_WAIT_DEFAULT, help="GUI abnormal/crash retry wait seconds.")
    parser.add_argument("--gui-max-attempts", type=int, default=CASE12_GUI_MAX_ATTEMPTS_DEFAULT, help="GUI max attempts. Default 2 = first try + one retry.")
    return parser.parse_args()


def apply_args(args):
    cfg.TOTAL_LOOPS = args.loops
    cfg.BOOSTER_PORT = args.booster_port
    cfg.RELAY_PORT = args.relay_port
    cfg.CASE12_ETH_BH_INIT_WAIT = args.eth_bh_wait
    cfg.CASE12_WIFI_BH_INIT_WAIT = args.wifi_bh_wait
    cfg.CASE12_WIRELESS_SYNC_WAIT = args.sync_wait
    cfg.LOOP_ETH_RESTORE_WAIT = args.loop_eth_restore_wait
    cfg.ONBOARDING_SSH_HOST = args.booster_host
    cfg.ONBOARDING_SSH_TIMEOUT = args.ssh_timeout
    cfg.CASE12_FAIL_REBOOT_CMD = args.fail_reboot_cmd
    cfg.CASE12_FAIL_REBOOT_COOLDOWN = args.fail_reboot_cooldown
    cfg.CASE12_ENABLE_RECOVERY_WAIT = args.enable_recovery_wait
    cfg.CASE12_GUI_RETRY_WAIT = args.gui_retry_wait
    cfg.CASE12_GUI_MAX_ATTEMPTS = args.gui_max_attempts


def get_booster_host() -> Optional[str]:
    host = cfg.ONBOARDING_SSH_HOST or discover_ssh_host_by_serial(log_prefix="[CASE12][SSH]")
    if host:
        return host
    log_progress("[CASE12][SSH] 無法取得 Booster SSH IP")
    return None


def ssh_get_trim(host: str, command: str) -> Tuple[bool, str, str]:
    log_progress(f"[CASE12][SSH CMD] {command}")
    ok, output, reason = run_ssh_command(host, command, timeout=cfg.ONBOARDING_SSH_TIMEOUT)
    if not ok:
        return False, "", reason
    lines = [line.strip() for line in output.replace("\r", "").splitlines() if line.strip()]
    value = lines[-1] if lines else ""
    return True, value, "None"


def read_booster_fh_disabled_state(host: str) -> Tuple[bool, Optional[str], Optional[str], str]:
    ok24, val24, reason24 = ssh_get_trim(host, FH_24G_CMD)
    ok5, val5, reason5 = ssh_get_trim(host, FH_5G_CMD)
    if not ok24 or not ok5:
        return False, val24 if ok24 else None, val5 if ok5 else None, f"2.4G={reason24}; 5G={reason5}"
    return True, val24, val5, "None"


def check_fh_state(host: str, expected_disabled: str, label: str) -> bool:
    ok, val24, val5, reason = read_booster_fh_disabled_state(host)
    log_separator(f"{label} - SSH check Booster FH .disabled expected={expected_disabled}")
    if not ok:
        log_progress(f"FH check SSH 失敗: {reason}")
        return False

    pass24 = val24 == expected_disabled
    pass5 = val5 == expected_disabled
    log_progress(f"2.4G FH: {FH_24G_CMD} -> {val24} ({'PASS' if pass24 else 'FAIL'})")
    log_progress(f"5G   FH: {FH_5G_CMD} -> {val5} ({'PASS' if pass5 else 'FAIL'})")
    return pass24 and pass5


def read_gui_toggle_state(driver, toggle_element) -> Optional[bool]:
    script = r"""
    const el = arguments[0];
    function textOf(x) { return (x && x.innerText ? x.innerText : '').trim().toLowerCase(); }
    let attrs = ['aria-checked', 'aria-pressed', 'checked', 'class'];
    let values = attrs.map(a => (el.getAttribute(a) || '').toLowerCase()).join(' ');
    let txt = textOf(el);
    let html = (el.outerHTML || '').toLowerCase();
    let combined = values + ' ' + txt + ' ' + html;
    if (combined.includes('disabled') || combined.includes('off') || combined.includes('false')) return false;
    if (combined.includes('enabled') || combined.includes('on') || combined.includes('true')) return true;
    const input = el.querySelector('input[type="checkbox"]');
    if (input) return !!input.checked;
    return null;
    """
    try:
        result = driver.execute_script(script, toggle_element)
        if isinstance(result, bool):
            return result
    except Exception:
        pass
    return None


def open_wifi_basic(driver, wait):
    _try_login_if_needed(driver, wait)
    log_progress("進入 WiFi Settings -> Basic")
    wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
    _js_click(driver, wifi_link, wait_after=1)
    wait_loading_done(wait)
    receive_monitor(2)


def click_apply_if_available(driver, wait):
    try:
        apply_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_BASIC_APPLY)))
        log_progress("點擊 Apply 套用 Wireless 設定")
        _js_click(driver, apply_btn, wait_after=1)
        wait_loading_done(wait)
        receive_monitor(2)
    except Exception as e:
        log_progress(f"Apply button 未出現或不可點擊，繼續流程: {type(e).__name__}: {e}")


def set_tsm4_wireless(driver, wait, desired_enabled: bool, booster_host: str) -> bool:
    open_wifi_basic(driver, wait)
    toggle = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIRELESS_ENABLE_TOGGLE)))
    gui_state = read_gui_toggle_state(driver, toggle)

    if gui_state is None:
        ok, val24, val5, reason = read_booster_fh_disabled_state(booster_host)
        if ok and val24 in ("0", "1") and val5 in ("0", "1"):
            gui_state = val24 == "0" and val5 == "0"
            log_progress(f"GUI wireless toggle state 無法判斷，改用 Booster FH 狀態輔助: enabled={gui_state}")
        else:
            log_progress(f"GUI wireless toggle state 無法判斷，且 Booster FH 狀態讀取失敗: {reason}")

    if gui_state is not None:
        log_progress(f"GUI wireless toggle current state: {'enabled' if gui_state else 'disabled'}")
        if gui_state == desired_enabled:
            log_progress(f"GUI wireless 已是目標狀態: {'enabled' if desired_enabled else 'disabled'}，仍點 Apply 確認。")
            click_apply_if_available(driver, wait)
            return True

    log_progress(f"切換 TSM4 Wireless -> {'Enable' if desired_enabled else 'Disable'}")
    _js_click(driver, toggle, wait_after=1)
    click_apply_if_available(driver, wait)
    return True


def _stop_background_logger_for_serial_owner(reason: str) -> bool:
    """Release COM port before a standalone serial command/script takes ownership."""
    try:
        log_progress(f"[CASE12][SERIAL] 暫停 full-session serial logger，釋放 COM port: {reason}")
        try:
            stop_background_serial_logger(close_serial=True)
        except TypeError:
            stop_background_serial_logger()
        time.sleep(1)
        return True
    except Exception as e:
        log_progress(f"[CASE12][SERIAL] 暫停 background serial logger 失敗，仍繼續嘗試: {type(e).__name__}: {e}")
        return False


def _restart_background_logger_after_serial_owner(reason: str) -> None:
    try:
        log_progress(f"[CASE12][SERIAL] 重啟 full-session serial logger: {reason}")
        start_background_serial_logger()
    except Exception as e:
        log_progress(f"[CASE12][SERIAL] 重啟 background serial logger 失敗: {type(e).__name__}: {e}")


def reboot_booster_and_exit_workaround(reason: str) -> None:
    """Case12-local Booster reboot workaround.

    This is not the runner-level factory-default/reset GW+RE workaround.
    Release the background serial logger before sending the reboot command so
    COM4/BOOSTER_PORT is not occupied by the full-session logger.
    """
    log_progress(f"FAIL workaround: {reason}")
    log_progress(f"透過 serial 對 Booster 送出 {cfg.CASE12_FAIL_REBOOT_CMD!r}")
    cmd = cfg.CASE12_FAIL_REBOOT_CMD.strip() + "\n"

    logger_stopped = _stop_background_logger_for_serial_owner("Case12 booster reboot workaround")
    try:
        send_command(cmd, wait_after=1)
    finally:
        if logger_stopped:
            _restart_background_logger_after_serial_owner("Case12 booster reboot cooldown monitor")

    log_progress(f"Booster reboot 後 cooldown / monitor {cfg.CASE12_FAIL_REBOOT_COOLDOWN} 秒，結束 Case12。")
    receive_monitor(cfg.CASE12_FAIL_REBOOT_COOLDOWN)


def set_tsm4_wireless_with_retry(host: str, desired_enabled: bool, action_label: str) -> bool:
    """Open Chrome only when Case12 needs GUI action, retry once by default, then close Chrome.

    This avoids keeping a blank data: Chrome window open during FW collection, relay waits,
    SSH checks, and sync waits. It also matches the Case5~Case9 GUI retry style.
    """
    max_attempts = int(getattr(cfg, "CASE12_GUI_MAX_ATTEMPTS", CASE12_GUI_MAX_ATTEMPTS_DEFAULT))
    retry_wait = int(getattr(cfg, "CASE12_GUI_RETRY_WAIT", CASE12_GUI_RETRY_WAIT_DEFAULT))

    for attempt in range(1, max_attempts + 1):
        driver = None
        try:
            log_progress(f"[CASE12][GUI] {action_label}: 開啟 Chrome，attempt {attempt}/{max_attempts}")
            driver = create_chrome_driver()
            if driver is None:
                raise RuntimeError("Chrome driver 建立失敗")

            wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)
            return bool(set_tsm4_wireless(driver, wait, desired_enabled=desired_enabled, booster_host=host))

        except Exception as e:
            log_progress(f"[CASE12][GUI] {action_label} 發生異常 (attempt {attempt}/{max_attempts}): {type(e).__name__}: {e}")
            if attempt < max_attempts:
                log_progress(f"[CASE12][GUI] 等待 {retry_wait} 秒後 retry: {action_label}")
                receive_monitor(retry_wait)
            else:
                log_progress(f"[CASE12][GUI] {action_label} 已達最大嘗試次數，判定 GUI 操作失敗。")
                return False
        finally:
            if driver is not None:
                try:
                    driver.quit()
                    log_progress(f"[CASE12][GUI] {action_label}: Chrome 已關閉")
                except Exception:
                    pass

    return False


def enable_wireless_then_reboot_workaround(host: str, stage_name: str, reason: str) -> None:
    """If the disable phase fails, restore GUI Wireless to enabled first, then reboot Booster."""
    log_progress(f"{stage_name} FAIL workaround: disable wireless 階段失敗，先嘗試進 TSM4 GUI 把 Wireless enable 回來。")
    try:
        set_tsm4_wireless_with_retry(host, desired_enabled=True, action_label=f"{stage_name} Recovery Enable Wireless")
        wait_sec = int(getattr(cfg, "CASE12_ENABLE_RECOVERY_WAIT", CASE12_ENABLE_RECOVERY_WAIT_DEFAULT))
        log_progress(f"等待 Wireless enable recovery sync {wait_sec} 秒")
        receive_monitor(wait_sec)
        check_fh_state(host, expected_disabled="0", label=f"{stage_name} Disable FAIL Recovery Enable Wireless")
    except Exception as e:
        log_progress(f"{stage_name} Disable FAIL recovery enable wireless 發生異常，仍繼續 reboot Booster: {type(e).__name__}: {e}")

    reboot_booster_and_exit_workaround(reason)


def run_wireless_disable_enable_stage(stage_name: str, init_wait: int, relay_state: str, host: str) -> bool:
    log_separator(f"{stage_name} 測試開始")
    log_progress(f"STEP: Relay 6 切換 ({relay_state.upper()}) 配置 {stage_name}")
    control_relay(relay_state)
    log_progress(f"{stage_name} init wait = {init_wait} 秒")
    receive_monitor(init_wait)

    log_separator(f"{stage_name} - STEP 1: TSM4 GUI Disable Wireless")
    if not set_tsm4_wireless_with_retry(host, desired_enabled=False, action_label=f"{stage_name} Disable Wireless"):
        log_progress(f"{stage_name} GUI Disable Wireless 失敗")
        enable_wireless_then_reboot_workaround(
            host, stage_name,
            f"{stage_name}: GUI Disable Wireless 失敗，已嘗試 enable wireless recovery。"
        )
        return False
    log_progress(f"等待 Wireless disable sync {cfg.CASE12_WIRELESS_SYNC_WAIT} 秒")
    receive_monitor(cfg.CASE12_WIRELESS_SYNC_WAIT)
    if not check_fh_state(host, expected_disabled="1", label=f"{stage_name} Disable Wireless"):
        log_progress(f"{stage_name} FAIL: TSM4 disable Wireless 後，Booster FH 未全部 disabled=1")
        enable_wireless_then_reboot_workaround(
            host, stage_name,
            f"{stage_name}: TSM4 disable Wireless 後 Booster FH 未全部 disabled=1。"
        )
        return False

    log_separator(f"{stage_name} - STEP 2: TSM4 GUI Enable Wireless")
    if not set_tsm4_wireless_with_retry(host, desired_enabled=True, action_label=f"{stage_name} Enable Wireless"):
        log_progress(f"{stage_name} GUI Enable Wireless 失敗")
        return False
    log_progress(f"等待 Wireless enable sync {cfg.CASE12_WIRELESS_SYNC_WAIT} 秒")
    receive_monitor(cfg.CASE12_WIRELESS_SYNC_WAIT)
    if not check_fh_state(host, expected_disabled="0", label=f"{stage_name} Enable Wireless"):
        reboot_booster_and_exit_workaround(
            f"{stage_name}: TSM4 enable Wireless 後，Booster FH 仍是 disabled 或未全部 enabled。"
        )
        return False

    log_progress(f"{stage_name} PASS: Disable/Enable Wireless FH sync check 通過")
    return True


def _case12_fail_recovery(loop_index: int, interface_name: str, reason: str) -> None:
    """Run current common fail diagnostic flow without factory workaround.

    safe_handle_fail_recovery() runs check_RE_status.py and restores ETH BH.
    The runner-level GW+Booster workaround is still controlled by the runner
    and should not run for case12.
    """
    log_progress(f"[CASE12][FAIL RECOVERY] {interface_name}: {reason}")
    write_recovery_note(interface_name, "Recovery(check_RE_status_only_no_factory_workaround)")
    safe_handle_fail_recovery(f"Loop{loop_index}_{cfg.TEST_CASE_NAME}_{interface_name.replace(' ', '_')}_Fail")


def run_one_loop(loop_index: int, host: str) -> bool:
    eth_ok = run_wireless_disable_enable_stage(
        "ETH BH", cfg.CASE12_ETH_BH_INIT_WAIT, "on", host
    )
    if not eth_ok:
        reason = "ETH_BH_FH_Sync_Fail"
        log_progress(f"LOOP {loop_index} ETH BH FAIL，WiFi BH 不執行，停止測試。")
        write_summary(f"LOOP {loop_index}", "ETH BH", "N/A", "FAIL", reason)
        _case12_fail_recovery(loop_index, "ETH BH", reason)
        return False

    wifi_ok = run_wireless_disable_enable_stage(
        "WiFi BH", cfg.CASE12_WIFI_BH_INIT_WAIT, "off", host
    )
    if not wifi_ok:
        reason = "WiFi_BH_FH_Sync_Fail"
        log_progress(f"LOOP {loop_index} WiFi BH FAIL，停止測試。")
        write_summary(f"LOOP {loop_index}", "WiFi BH", "N/A", "FAIL", reason)
        _case12_fail_recovery(loop_index, "WiFi BH", reason)
        return False

    log_progress(f"LOOP {loop_index} PASS。")
    write_summary(f"LOOP {loop_index}", "ETH/WiFi BH", "N/A", "PASS", "None")
    restore_eth_backhaul_between_loops(loop_index)
    return True


def run_test() -> int:
    router_fw, booster_fw = get_environment_fw_versions_close_browser()
    init_summary_log(router_fw, booster_fw)
    log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
    log_progress("Case12 policy: ETH BH PASS 才執行 WiFi BH；FAIL 會依階段執行 workaround；Enable 後 FH 未起來會 reboot Booster 後離開。")

    host = get_booster_host()
    if not host:
        log_progress("Case12 FAIL: 無法取得 Booster SSH host")
        write_summary("LOOP -", "ETH/WiFi BH", "N/A", "FAIL", "No_Booster_SSH_Host")
        _case12_fail_recovery(0, "ETH BH", "No_Booster_SSH_Host")
        return 1
    log_progress(f"Case12 使用 Booster SSH host: {host}")

    log_progress("Case12 GUI policy: 不預先開啟 Chrome；只在 Disable/Enable Wireless 動作期間開啟，動作完成後立即關閉。")

    for loop in range(1, cfg.TOTAL_LOOPS + 1):
        log_separator(f"LOOP {loop} - Case12 Wireless FH Disable/Enable Sync Check")
        clear_cached_ssh_host()
        # Reuse fixed host unless user left it None; rediscover is only for diagnostics if host changes.
        if cfg.ONBOARDING_SSH_HOST is None:
            latest_host = discover_ssh_host_by_serial(log_prefix="[CASE12][SSH]", force=True)
            if latest_host:
                host = latest_host
                log_progress(f"Case12 更新 Booster SSH host: {host}")
        if not run_one_loop(loop, host):
            return 1

    restore_eth_backhaul("Case12 測試 PASS 結束")
    log_separator("Case12 所有測試迴圈執行完畢，結果 PASS")
    return 0


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "case12_TSM4_Wireless_FH_Disable_Enable_Check"
    args = parse_args()
    apply_args(args)
    init_log_filenames()
    start_background_serial_logger()
    exit_code = 1
    try:
        exit_code = run_test()
    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
        exit_code = 130
    except Exception as e:
        log_progress(f"Case12 主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("Case12 主程式未預期錯誤")
        exit_code = 1
    finally:
        try:
            stop_background_serial_logger(close_serial=True)
        except TypeError:
            stop_background_serial_logger()

    raise SystemExit(exit_code)
