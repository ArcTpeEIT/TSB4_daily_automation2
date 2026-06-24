"""
case6_tsm4_gui_reboot_gw_re.py
透過 TSM4 GUI 點擊 Reboot GW+RE（XPATH_REBOOT_ALL），
然後確認 ETH BH / WiFi BH onboarding 狀態。
"""
import common

common.TEST_CASE_NAME = "case6_Reboot (Router + Boosters) via TSM4 Web GUI"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test(
        action_xpath=common.XPATH_REBOOT_ALL,
        action_label="Reboot GW+RE",
    )
