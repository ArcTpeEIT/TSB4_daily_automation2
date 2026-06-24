"""
case9_tsm4_gui_reset_re.py
透過 TSM4 GUI 點擊 Reset RE（XPATH_RESET_RE），
然後確認 ETH BH / WiFi BH onboarding 狀態。
"""
import common

common.TEST_CASE_NAME = "case9_Reset Boosters via TSM4 Web GUI"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test(
        action_xpath=common.XPATH_RESET_RE,
        action_label="Reset RE",
    )
