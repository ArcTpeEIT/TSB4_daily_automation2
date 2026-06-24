"""
case4_re_cold_reboot.py

RE cold reboot -> ETH BH / WiFi BH onboarding check.
"""
import common

common.TEST_CASE_NAME = "case4_Cold Reboot Onboarding"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test_re_cold_reboot()
