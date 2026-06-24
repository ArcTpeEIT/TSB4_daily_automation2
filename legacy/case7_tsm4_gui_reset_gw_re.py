"""
case7_tsm4_gui_reset_gw_re.py
透過 TSM4 GUI 點擊 Reset Router+Boosters（XPATH_RESET_ALL），
然後確認 ETH BH / WiFi BH onboarding 狀態。
"""
import common

common.TEST_CASE_NAME = "case7_Reset (Router + Boosters) via TSM4 Web GUI"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test(
        action_xpath=common.XPATH_RESET_ALL,
        action_label="Reset Router+Boosters",
    )
