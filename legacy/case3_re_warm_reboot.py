"""
case3_re_warm_reboot.py

RE warm reboot -> ETH BH / WiFi BH onboarding check.
"""
import common

common.TEST_CASE_NAME = "case3_Warm Reboot Onboarding"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test_re_warm_reboot()
