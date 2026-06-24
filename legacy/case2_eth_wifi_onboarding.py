"""
case2_eth_wifi_onboarding.py

ETH BH / WiFi BH onboarding check.
"""
import common

common.TEST_CASE_NAME = "case2_Standard Onboarding"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test_eth_wifi_onboarding()
