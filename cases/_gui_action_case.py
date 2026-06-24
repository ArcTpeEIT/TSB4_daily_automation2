"""Shared runner for Case6~Case9 GUI action onboarding cases."""
from testlib import config as cfg
from testlib.logger import (
    init_summary_log,
    log_progress,
    log_step,
    log_result,
    log_separator,
    write_summary,
    summary_loop_display,
)
from testlib.env_info import get_router_fw_version, get_booster_fw_version
from testlib.web_gui import trigger_web_action
from testlib.relay import control_relay, restore_eth_backhaul
from testlib.serial_console import receive_monitor
from testlib.onboarding import run_polling_or_recover


def get_init_wait_time_by_action(action_label):
    label = str(action_label).lower()
    if "reset" in label:
        return cfg.RESET_INIT_WAIT_TIME
    if "reboot" in label:
        return cfg.REBOOT_INIT_WAIT_TIME
    return cfg.INIT_WAIT_TIME


def execute_one_backhaul_test(
    loop,
    interface_name,
    relay_state,
    action_xpath,
    action_label,
    max_total_limit,
    threshold,
    active_driver=None,
    init_wait_time=None,
):
    log_separator(f"LOOP {loop} - {interface_name} 測試開始")
    log_step(f"Loop {loop} {interface_name}: GUI action start ({action_label})")
    log_progress(f"STEP: 準備執行 {interface_name} 測試 (GUI 觸發 {action_label})")

    gui_ok, duration_start_time = trigger_web_action(action_xpath, action_label, active_driver)
    if not gui_ok:
        write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", "GUI Error")
        log_result(f"Loop {loop} {interface_name}: FAIL, GUI Error ({action_label})")
        log_progress(f"!! {interface_name} GUI 操作失敗，只寫 Summary，不執行 diag / recovery !!")
        log_step(f"Loop {loop} {interface_name}: restore ETH BH after GUI Error")
        restore_eth_backhaul(f"{interface_name} GUI Error")
        return False

    log_result(f"Loop {loop} {interface_name}: GUI action command sent ({action_label})")
    log_step(f"Loop {loop} {interface_name}: wait action sync, wait={cfg.REBOOT_SYNC_WAIT}s")
    log_progress(f"等待 {cfg.REBOOT_SYNC_WAIT} 秒讓 Booster 確實收到指令...")
    receive_monitor(cfg.REBOOT_SYNC_WAIT)

    backhaul_name = "ETH BH" if str(relay_state).lower() == "on" else "WiFi BH"
    log_step(f"Loop {loop} {interface_name}: switch backhaul to {backhaul_name}, relay {cfg.RELAY_ETH_PORT} {relay_state}")
    log_progress(f"STEP: Relay 切換 ({relay_state.upper()}) 配置 {interface_name}")
    control_relay(relay_state)
    receive_monitor(cfg.RELAY_SETTLE_TIME)

    if init_wait_time is None:
        init_wait_time = get_init_wait_time_by_action(action_label)
        log_progress(f"Polling init wait time 依 action_label='{action_label}' 設定為 {init_wait_time} 秒")
    else:
        log_progress(f"Polling init wait time for {interface_name}: {init_wait_time} 秒")

    log_step(
        f"Loop {loop} {interface_name}: onboarding check start "
        f"(init_wait={init_wait_time}s, threshold={threshold}, max_total_limit={max_total_limit}s)"
    )
    result = run_polling_or_recover(
        loop,
        interface_name,
        init_wait_time,
        threshold,
        f"{interface_name.replace(' ', '_')}_Fail",
        duration_start_time=duration_start_time,
        max_total_limit=max_total_limit,
    )

    if result:
        log_result(f"Loop {loop} {interface_name}: PASS")
    else:
        log_result(f"Loop {loop} {interface_name}: FAIL")
    return result


def run_gui_action_case(action_xpath, action_label, max_total_limit, threshold=None, eth_init_wait=None, wifi_init_wait=None):
    active_driver = None
    threshold = cfg.ONBOARDING_THRESHOLD if threshold is None else threshold
    log_step(f"{cfg.TEST_CASE_NAME}: GUI action case start ({action_label}), loops={cfg.TOTAL_LOOPS}")
    try:
        router_fw, active_driver = get_router_fw_version()
        log_step(f"{cfg.TEST_CASE_NAME}: wait before GUI action navigation, wait={cfg.GW_FW_TO_GUI_ACTION_SLEEP}s")
        log_progress(f"GW FW 取得完成，保留 Chrome，等待 {cfg.GW_FW_TO_GUI_ACTION_SLEEP} 秒後繼續 GUI login/navigation...")
        receive_monitor(cfg.GW_FW_TO_GUI_ACTION_SLEEP)
        booster_fw = get_booster_fw_version()

        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Fail policy: GUI Error only writes Summary; any FAIL stops current script; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            log_step(f"{cfg.TEST_CASE_NAME}: Loop {loop} start")
            eth_pass = execute_one_backhaul_test(
                loop, "ETH BH", "on", action_xpath, action_label,
                max_total_limit, threshold, active_driver, eth_init_wait
            )
            active_driver = None
            if not eth_pass:
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} FAIL at ETH BH")
                log_progress(f"LOOP {loop} ETH BH FAIL / GUI Error，停止測試，不繼續 WiFi BH。")
                return False

            wifi_pass = execute_one_backhaul_test(
                loop, "WiFi BH", "off", action_xpath, action_label,
                max_total_limit, threshold, None, wifi_init_wait
            )
            if not wifi_pass:
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} FAIL at WiFi BH")
                log_progress(f"LOOP {loop} WiFi BH FAIL / GUI Error，停止測試。")
                return False

            log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} PASS")
            log_progress(f"LOOP {loop} PASS。")

        log_step(f"{cfg.TEST_CASE_NAME}: all loops PASS, restore ETH BH")
        restore_eth_backhaul("測試 PASS 結束")
        log_result(f"{cfg.TEST_CASE_NAME}: PASS")
        log_separator("所有測試迴圈執行完畢，結果 PASS")
        return True
    except KeyboardInterrupt:
        log_result(f"{cfg.TEST_CASE_NAME}: interrupted by user")
        log_progress("使用者中斷測試。")
        return False
    except Exception as e:
        log_result(f"{cfg.TEST_CASE_NAME}: FAIL, unexpected error {type(e).__name__}: {e}")
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")
        return False
    finally:
        if active_driver is not None:
            try:
                active_driver.quit()
            except Exception:
                pass
