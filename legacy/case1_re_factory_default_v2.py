"""
case1_re_factory_default.py

每個 loop 都執行：
RE factory default -> ETH BH onboarding check -> WiFi BH onboarding check.
"""
import common

common.TEST_CASE_NAME = "case1_Factory Default Onboarding"
common.init_log_filenames()

if __name__ == "__main__":
    common.run_test_re_factory_default()
