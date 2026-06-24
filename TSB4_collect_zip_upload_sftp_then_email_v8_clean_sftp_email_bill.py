import os
import glob
import re
import zipfile
import smtplib
import shutil
import paramiko
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')



def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_step(message):
    print(f"[{_ts()}] [PROGRESS-STEP] >>> {message}", flush=True)


def log_result(message):
    print(f"[{_ts()}] [PROGRESS-RESULT] >>> {message}", flush=True)


def log_progress(message):
    print(f"[{_ts()}] [PROGRESS] >>> {message}", flush=True)
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

# ==================== SFTP Upload Setting ====================
# 對齊 Download_fw_then_upgrade.py 的 SFTP 設定。
SFTP_HOST = "arc-sftp.arcadyan.com.tw"
SFTP_PORT = 22
SFTP_USER = "arctaxbooster4"
SFTP_PASS = "%C82B5B3"
SFTP_UPLOAD_ROOT = "/TA_booster4/DailyBuild_Automation"

# FW upgrade log is generated in automation root by Download_fw_then_upgrade_TSB4_fixed_log_root.py.
# Keep the pattern narrow to avoid collecting unrelated logs from the root folder.
FW_UPGRADE_LOG_PATTERNS = [
    "fw_upgrade*.log",
]
# ============================================================


def send_email(subject, body, attachments=None):
    log_step("Final collect: send email report")
    sender = 'arctest3903@gmail.com'
    # receiver = 'bill_chen@arcadyan.com'
    receivers = [
    'bill_chen@arcadyan.com',
    #'jh_yen@arcadyan.com',
    #'zach_chu@arcadyan.com',
    #'chocho_chen@arcadyan.com',
    #'dennis_chiang@arcadyan.com',
    #'quantum_wu@arcadyan.com',    
    ]
    app_password = 'apthsnwksezkwtbo'

    if attachments is None:
        attachments = []

    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = sender
   # msg['To'] = receiver
    msg['To'] = ', '.join(receivers)
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    for file_path in attachments:
        if os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(file_path)}"')
            msg.attach(part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, app_password)
            server.send_message(msg)
        log_result("Final collect: email send PASS")
        return True, "None"
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        log_result(f"Final collect: email send FAIL, reason={reason}")
        return False, reason


def sftp_mkdir_p(sftp, remote_dir):
    """遞迴建立 SFTP 遠端資料夾。"""
    parts = [p for p in remote_dir.replace("\\", "/").split("/") if p]
    current = "/" if remote_dir.startswith("/") else ""

    for part in parts:
        if current in ("", "/"):
            current = current + part if current == "/" else part
        else:
            current = current + "/" + part

        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def upload_files_to_sftp(local_files, remote_dir):
    """將指定檔案上傳到 SFTP remote_dir。"""
    uploaded = []
    transport = None

    try:
        log_step(f"Final collect: connect SFTP {SFTP_HOST}:{SFTP_PORT}")
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_USER, password=SFTP_PASS)
        sftp = paramiko.SFTPClient.from_transport(transport)

        log_result("Final collect: SFTP login PASS")
        log_step(f"Final collect: ensure SFTP folder {remote_dir}")
        sftp_mkdir_p(sftp, remote_dir)

        for local_file in local_files:
            if not os.path.exists(local_file):
                log_progress(f"SFTP Skip，不存在: {local_file}")
                continue

            remote_path = remote_dir.rstrip("/") + "/" + os.path.basename(local_file)
            log_step(f"Final collect: upload {os.path.basename(local_file)} to SFTP")
            log_progress(f"SFTP Upload: {local_file} -> {remote_path}")
            sftp.put(local_file, remote_path)
            uploaded.append(remote_path)

        sftp.close()
        transport.close()
        log_result(f"Final collect: SFTP upload PASS, uploaded={len(uploaded)}")
        return True, uploaded, "None"

    except Exception as e:
        try:
            if transport:
                transport.close()
        except Exception:
            pass

        log_result(f"Final collect: SFTP upload FAIL, reason={type(e).__name__}: {e}")
        return False, uploaded, f"{type(e).__name__}: {e}"


CASE_DESCRIPTIONS = {
    "case1_Factory Default Onboarding": "Restore factory default, then verify GW/RE can complete onboarding again.",
    "case2_Standard Onboarding": "Run normal onboarding and verify the RE can join the Mesh network.",
    "case3_RE Warm Reboot Onboarding": "Warm reboot the RE, then verify onboarding or Mesh recovery works normally.",
    "case4_RE Cold Reboot Onboarding": "Cold reboot or power-cycle the RE, then verify it can reconnect and complete onboarding.",
    "case5_TSM4 Restart Onboarding": "Restart TSM4 service/process, then verify onboarding and device management remain normal.",
    "case6_Reboot GW+RE Onboarding": "Reboot both GW and RE, then verify the Mesh topology can be restored.",
    "case7_Reset Router+Boosters Onboarding": "Reset Router/GW and Boosters/RE, then verify onboarding can rebuild the Mesh network.",
    "case8_Reboot RE Onboarding": "Reboot only the RE, then verify it can reconnect to the GW automatically.",
    "case9_Reset RE Onboarding": "Reset only the RE, then verify it can onboard again.",
    "case10_Main_WiFi_Random_SSID_Key_Sync_SpecialChar": "Set random Main Wi-Fi SSID/key with special characters and verify sync from GW to RE.",
    "case11_Guest_WiFi_Random_SSID_Key_Sync_SpecialChar": "Set random Guest Wi-Fi SSID/key with special characters and verify sync from GW to RE.",
    "case13_BH_Random_SSID_Lost_Connect_Check": "Randomly change Backhaul SSID and verify RE disconnection/reconnection behavior.",
}


def normalize_case_name(raw_case_name):
    """Normalize the case name read from the first line of Summary.log."""
    case_name = raw_case_name.strip()
    case_name = case_name.lstrip("- ").strip()
    return case_name


def format_case_description(case_name):
    """Format case name and short purpose for the email body."""
    description = CASE_DESCRIPTIONS.get(
        case_name,
        "Run this test case and verify the expected onboarding or configuration result."
    )
    return f"{case_name}:\n  {description}"


def extract_last_diagnostic_summary(lines):
    """Extract the last DIAGNOSTIC SUMMARY table from a Summary.log file."""
    summary_indexes = [idx for idx, line in enumerate(lines) if "DIAGNOSTIC SUMMARY" in line]
    if not summary_indexes:
        return ""

    header_idx = summary_indexes[-1]
    start_idx = header_idx

    # Include the separator line right before DIAGNOSTIC SUMMARY, if it exists.
    for idx in range(header_idx - 1, -1, -1):
        stripped = lines[idx].strip()
        if stripped and set(stripped) <= {"="}:
            start_idx = idx
            break
        if stripped and set(stripped) <= {"-"}:
            continue
        if header_idx - idx > 5:
            break

    end_idx = len(lines)
    for idx in range(header_idx + 1, len(lines)):
        if ">>> [RESULT]" in lines[idx]:
            end_idx = idx + 1
            break

    return "".join(lines[start_idx:end_idx]).rstrip()



def sanitize_report_name(name):
    """Return a Windows-safe, SFTP-safe report folder/file token."""
    value = str(name or "Unknown_FW").strip()
    value = value.replace("/", "_").replace("\\", "_")
    value = re.sub(r"[\s:*?\"<>|]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._")
    return value or "Unknown_FW"


def extract_fw_version_from_summary_files(summary_files):
    """Extract FW version from Summary.log files when case logs exist."""
    for summary_path in summary_files:
        try:
            with open(summary_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "Booster Firmware Version" in line:
                        return line.split(":")[-1].strip().replace("/", "_")
                    if "Firmware version:" in line and "root@" not in line:
                        return line.split(":")[-1].strip().replace("/", "_")
        except Exception as e:
            log_progress(f"FW version parse skip Summary.log {summary_path}, reason={type(e).__name__}: {e}")
    return ""


def extract_fw_version_from_fw_filename(fw_name):
    """Prefer a short TSB4 token from an ArcSigned firmware filename."""
    base = os.path.basename(str(fw_name or "").strip().strip("'\""))
    base = re.sub(r"\.bin$", "", base, flags=re.IGNORECASE)
    m = re.search(r"(TSB4[._A-Za-z0-9-]+?)(?:_ArcSigned|$)", base)
    if m:
        return m.group(1)
    return base


def extract_fw_version_from_fw_upgrade_logs(fw_upgrade_files):
    """Extract FW version from fw_upgrade*.log when no Summary.log exists."""
    latest_first = sorted(
        fw_upgrade_files,
        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        reverse=True,
    )
    for fw_log in latest_first:
        try:
            with open(fw_log, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            log_progress(f"FW version parse skip FW log {fw_log}, reason={type(e).__name__}: {e}")
            continue

        m = re.search(r"target firmware found\s*=\s*([^\r\n]+)", text, flags=re.IGNORECASE)
        if m:
            return extract_fw_version_from_fw_filename(m.group(1))

        m = re.search(r"(\S*TSB4\S*ArcSigned\S*\.bin)", text, flags=re.IGNORECASE)
        if m:
            return extract_fw_version_from_fw_filename(m.group(1))
    return ""


def unique_existing_files(file_list):
    """Preserve order while removing duplicate paths and non-files."""
    result = []
    seen = set()
    for path in file_list:
        norm = os.path.normpath(path)
        if norm in seen:
            continue
        if os.path.isfile(path):
            seen.add(norm)
            result.append(path)
    return result


def email_file_list(files):
    if not files:
        return "None"
    return "\n".join(f"  - {os.path.basename(f)}" for f in files)

def main():
    log_step("Final collect start: scan Summary/Console/diagnosticcomlog/FW-upgrade files")
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    remote_dir = SFTP_UPLOAD_ROOT.rstrip("/")

    summary_files = sorted(glob.glob("*Summary.log"))
    console_files = sorted(glob.glob("*Console.log"))

    # Diagnostic tgz files are produced by per-case fail recovery.
    # This final collect script must NOT SSH/login/download diagnosticcomlog.tgz.
    # It only packages existing local *_diagnosticcomlog.tgz files into the final ZIP.
    diagnostic_files = sorted(set(
        glob.glob("*diagnosticcomlog.tgz") +
        glob.glob(os.path.join("RE_fail_logs", "*diagnosticcomlog.tgz")) +
        glob.glob(os.path.join("RE_fail_logs", "**", "*diagnosticcomlog.tgz"), recursive=True)
    ))

    fw_upgrade_files = sorted(set(
        fw_log
        for pattern in FW_UPGRADE_LOG_PATTERNS
        for fw_log in glob.glob(pattern)
        if os.path.isfile(fw_log)
    ))

    collected_logs = unique_existing_files(summary_files + console_files + diagnostic_files + fw_upgrade_files)

    log_result(
        f"Final collect: found Summary={len(summary_files)}, "
        f"Console={len(console_files)}, diagnostic={len(diagnostic_files)}, "
        f"fw_upgrade_log={len(fw_upgrade_files)}"
    )

    if not collected_logs:
        log_result("Final collect FAIL: no log files found")
        return 1

    fw_version = extract_fw_version_from_summary_files(summary_files)
    if not fw_version:
        fw_version = extract_fw_version_from_fw_upgrade_logs(fw_upgrade_files)
    if not fw_version:
        fw_version = "Unknown_FW"
    fw_version = sanitize_report_name(fw_version)

    log_result(f"Final collect: Booster FW version = {fw_version}")

    has_summary = bool(summary_files)
    collection_mode = "FULL_SUMMARY" if has_summary else "PARTIAL_NO_SUMMARY"

    target_folder = f"{fw_version}"
    if not os.path.exists(target_folder):
        log_step(f"Final collect: create local report folder {target_folder}")
        os.makedirs(target_folder)
        log_result(f"Final collect: local report folder ready = {target_folder}")

    all_summary_name = os.path.join(target_folder, f"{fw_version}_all_case_summary.log")
    zip_name = os.path.join(target_folder, f"{fw_version}_TestReport_{now_str}.zip")

    critical_issues = []
    failed_diagnostic_summaries = []
    fail_count = 0
    pass_count = 0
    case_list = []

    for f_path in summary_files:
        has_real_fail = False
        fname = os.path.basename(f_path)
        try:
            with open(f_path, "r", encoding="utf-8", errors="ignore") as infile:
                lines = infile.readlines()
        except Exception as e:
            critical_issues.append(f"[ISSUE in {fname}]: cannot read Summary.log, reason={type(e).__name__}: {e}")
            fail_count += 1
            continue

        diagnostic_summary = extract_last_diagnostic_summary(lines)

        for line in lines:
            parts = [p.strip().upper() for p in line.split("|")]
            if len(parts) >= 5 and ("FAIL" in parts or "TIMEOUT" in parts):
                critical_issues.append(f"[ISSUE in {fname}]: {line.strip()}")
                has_real_fail = True

        if lines:
            case_name = normalize_case_name(lines[0])
            case_list.append(format_case_description(case_name))

        if has_real_fail:
            fail_count += 1
            if diagnostic_summary:
                failed_diagnostic_summaries.append(f">> FILE: {fname}\n{diagnostic_summary}")
        else:
            pass_count += 1

    if has_summary:
        test_status = "PASS" if fail_count == 0 else "FAIL"
    else:
        test_status = "INCOMPLETE"
        critical_issues.append("[NO SUMMARY]: no Summary.log files found; this package contains available logs only.")

    log_step(f"Final collect: build all-case summary {all_summary_name}")
    with open(all_summary_name, "w", encoding="utf-8") as outfile:
        outfile.write("=" * 95 + "\n")
        title = "AUTOMATION TEST REPORT" if has_summary else "AUTOMATION PARTIAL LOG PACKAGE"
        outfile.write(f" {title} - {fw_version}\n")
        outfile.write(f" Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        outfile.write("=" * 95 + "\n\n")
        outfile.write(
            f"[ Statistics ]\n"
            f" Collection Mode: {collection_mode}\n"
            f" Test Status: {test_status}\n"
            f" Summary Logs: {len(summary_files)}\n"
            f" Console Logs: {len(console_files)}\n"
            f" Diagnostic Files: {len(diagnostic_files)}\n"
            f" FW Upgrade Logs: {len(fw_upgrade_files)}\n"
        )
        if has_summary:
            outfile.write(
                f" Total Cases: {len(summary_files)}\n"
                f" Pass: {pass_count}\n"
                f" Fail: {fail_count}\n\n"
            )
        else:
            outfile.write(
                " Total Cases: N/A\n"
                " Pass: N/A\n"
                " Fail: N/A\n"
                " Note: No Summary.log was found, so the test result cannot be determined.\n\n"
            )

        outfile.write("[ SFTP Upload Target ]\n")
        outfile.write(f"  - {remote_dir}\n\n")

        if summary_files:
            outfile.write("[ Summary Logs Included in ZIP ]\n")
            for summary_f in summary_files:
                outfile.write(f"  - {os.path.basename(summary_f)}\n")
            outfile.write("\n")

        if console_files:
            outfile.write("[ Console Logs Included in ZIP ]\n")
            for console_f in console_files:
                outfile.write(f"  - {os.path.basename(console_f)}\n")
            outfile.write("\n")

        if diagnostic_files:
            outfile.write("[ Diagnostic Files Included in ZIP ]\n")
            for diag_f in diagnostic_files:
                outfile.write(f"  - {os.path.basename(diag_f)}\n")
            outfile.write("\n")

        if fw_upgrade_files:
            outfile.write("[ FW Upgrade Logs Included in ZIP ]\n")
            for fw_log in fw_upgrade_files:
                outfile.write(f"  - {os.path.basename(fw_log)}\n")
            outfile.write("\n")

        if critical_issues:
            outfile.write("[ CRITICAL ISSUES / COLLECTION NOTES ]\n")
            for issue in critical_issues:
                outfile.write(f"!! {issue}\n")
            outfile.write("-" * 50 + "\n\n")

        if has_summary:
            outfile.write("[ Detailed Summary Logs ]\n")
            for f_path in summary_files:
                outfile.write(f"\n>> FILE: {os.path.basename(f_path)}\n")
                with open(f_path, "r", encoding="utf-8", errors="ignore") as infile:
                    outfile.write(infile.read())
                outfile.write("\n" + "=" * 95 + "\n")
        else:
            outfile.write("[ Detailed Summary Logs ]\n")
            outfile.write("No Summary.log files were found. See Console/FW-upgrade logs in this ZIP.\n")

    log_result(
        f"Final collect: all-case summary generated, mode={collection_mode}, "
        f"status={test_status}, pass={pass_count}, fail={fail_count}"
    )

    log_step(f"Final collect: create ZIP report {zip_name}")
    files_to_zip = unique_existing_files([all_summary_name] + collected_logs)

    files_added = 0
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for log_f in files_to_zip:
            if os.path.exists(log_f):
                zf.write(log_f, os.path.basename(log_f))
                files_added += 1

    log_result(f"Final collect: ZIP created, files_included={files_added}")

    # all_case_summary.log is included in zip. Upload only zip to SFTP.
    upload_files = [zip_name]
    sftp_ok, uploaded_paths, sftp_error = upload_files_to_sftp(upload_files, remote_dir)

    case_str = "\n\n".join(case_list) if case_list else "No Summary.log files were found, so no case list can be generated."

    if critical_issues:
        issue_highlight = "[ CRITICAL ISSUES / COLLECTION NOTES ]\n"
        for issue in critical_issues:
            issue_highlight += f"!! {issue}\n"
    else:
        issue_highlight = "None"

    if failed_diagnostic_summaries:
        diagnostic_summary_text = "\n\n".join(failed_diagnostic_summaries)
        diagnostic_summary_block = f"""
[ Diagnostic Summary for Failed Case ]
{diagnostic_summary_text}
"""
    else:
        diagnostic_summary_block = ""

    summary_highlight = email_file_list(summary_files)
    console_highlight = email_file_list(console_files)
    diag_highlight = email_file_list(diagnostic_files)
    fw_upgrade_highlight = email_file_list(fw_upgrade_files)
    sftp_uploaded_text = "\n".join(f"  - {p}" for p in uploaded_paths) if uploaded_paths else "None"

    if has_summary:
        subject = f"[{test_status}] TSB4 Automation Test Report - {fw_version} - {now_str}"
    else:
        subject = f"[INCOMPLETE] TSB4 Automation Partial Log Package - {fw_version} - {now_str}"

    body = f"""Firmware: {fw_version}
Test Status: {test_status}
Collection Mode: {collection_mode}

[ Critical Issue Highlight ]
{issue_highlight}
{diagnostic_summary_block}
[ Case List ]
{case_str}

Please download the ZIP report from the following SFTP path:
{sftp_uploaded_text}

[ Summary Logs Included in ZIP ]
{summary_highlight}

[ Console Logs Included in ZIP ]
{console_highlight}

[ Diagnostic Files Included in ZIP ]
{diag_highlight}

[ FW Upgrade Logs Included in ZIP ]
{fw_upgrade_highlight}

[ SFTP Upload ]
Status: {'PASS' if sftp_ok else 'FAIL'}

All available Summary, Console, FW upgrade, and diagnostic files are included in the ZIP file.
"""

    email_attachments = [] if sftp_ok else [all_summary_name]
    email_ok, email_error = send_email(subject, body, email_attachments)

    log_step("Final collect: cleanup original Summary/Console/diagnostic/FW-upgrade files")
    # Clean only source logs in the working directory/tree. Keep target_folder output files.
    for f in collected_logs:
        try:
            os.remove(f)
        except Exception:
            pass

    log_result(f"Final collect: cleanup completed, local_folder={target_folder}")
    log_progress(f"SFTP target folder: {remote_dir}")

    if sftp_ok and email_ok:
        log_result(f"Final collect PASS: status={test_status}, mode={collection_mode}, zip={zip_name}")
        return 0

    fail_reasons = []
    if not sftp_ok:
        fail_reasons.append(f"SFTP={sftp_error}")
    if not email_ok:
        fail_reasons.append(f"EMAIL={email_error}")
    log_result(f"Final collect FAIL: {'; '.join(fail_reasons)}")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
