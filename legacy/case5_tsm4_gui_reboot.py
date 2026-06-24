"""
case5_tsm4_gui_reboot.py

透過 TSM4 GUI Settings -> Maintenance 點擊 Restart，
然後確認 ETH BH / WiFi BH onboarding 狀態。
"""
import common

common.TEST_CASE_NAME = "case5_TSM4 GUI Reboot"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test_tsm4_reboot()
