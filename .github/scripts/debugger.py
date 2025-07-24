import os
import re
import json
import base64
import time
import sys
import requests
import google.generativeai as genai
import zipfile
import io
import traceback

# ==============================================================================
# I. CẤU HÌNH
# ==============================================================================
print("--- 🤖 AI Auto-Debugger v1.0 Initializing ---")
try:
    ISSUE_BODY = os.environ["ISSUE_BODY"]
    ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    REPO_OWNER = os.environ["GH_USER"]
    COMMIT_EMAIL = os.environ["COMMIT_EMAIL"]
    COMMIT_NAME = os.environ["COMMIT_NAME"]
except KeyError as e:
    print(f"❌ LỖI: Thiếu biến môi trường: {e}")
    sys.exit(1)

COMMIT_AUTHOR = {"name": COMMIT_NAME, "email": COMMIT_EMAIL}
API_BASE_URL = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
genai.configure(api_key=GEMINI_API_KEY)

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def post_issue_comment(message):
    url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
    requests.post(url, headers=HEADERS, json={"body": message})

def parse_bug_report(body):
    print("--- 🕵️  Đang phân tích báo cáo lỗi ---")
    repo_match = re.search(r"- \*\*Repo:\*\*\s*`(.+?)`", body)
    run_url_match = re.search(r"- \*\*Workflow Run URL:\*\*\s*(https\S+)", body)
    if not repo_match or not run_url_match:
        raise ValueError("Không thể trích xuất Repo và Run URL từ báo cáo lỗi.")
    repo_name = repo_match.group(1)
    run_id = run_url_match.group(1).split('/')[-1]
    return repo_name, run_id

def get_failed_job_log(repo_name, run_id):
    print(f"--- 📥 Đang tải log lỗi từ Run ID: {run_id} ---")
    logs_url = f"{API_BASE_URL}/repos/{repo_name}/actions/runs/{run_id}/logs"
    for _ in range(3):
        response = requests.get(logs_url, headers=HEADERS, stream=True, timeout=60)
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                log_file_name = next((name for name in z.namelist() if 'build' in name and name.endswith('.txt')), z.namelist()[0])
                with z.open(log_file_name) as f:
                    log_content = f.read().decode('utf-8', errors='ignore')
            return "\n".join(log_content.splitlines()[-200:])
        print(f"Log chưa sẵn sàng (status: {response.status_code}), đợi 10 giây...")
        time.sleep(10)
    raise Exception("Không thể tải log lỗi sau nhiều lần thử.")

def get_file_content(repo_name, file_path):
    print(f"--- 📄 Đang đọc nội dung file: {file_path} ---")
    try:
        url = f"{API_BASE_URL}/repos/{repo_name}/contents/{file_path}"
        response = requests.get(url, headers=HEADERS, timeout=30).json()
        return base64.b64decode(response['content']).decode('utf-8'), response['sha']
    except Exception: return None, None

def call_gemini_for_fix(error_log, files_content):
    print("--- 🧠 Đang gửi thông tin cho Gemini Pro để phân tích và sửa lỗi ---")
    context_files = "".join([f"\n\n--- Content of `{path}` ---\n```\n{content}\n```" for path, content in files_content.items() if content])
    debug_prompt = f"Một build Flutter đã thất bại. Phân tích log và code để sửa lỗi.\n\n--- LOG LỖI ---\n```\n{error_log}\n```\n{context_files}\n\n**NHIỆM VỤ:**\n1. Phân tích nguyên nhân.\n2. Viết lại TOÀN BỘ nội dung của file cần sửa.\n3. Trả về MỘT JSON duy nhất có cấu trúc: `{{\"analysis\": \"...\", \"file_to_patch\": \"...\", \"corrected_code\": \"...\", \"commit_message\": \"...\"}}`. Nếu không sửa được, `file_to_patch` là `null`."
    model = genai.GenerativeModel("gemini-1.5-pro-latest")
    response = model.generate_content(debug_prompt, request_options={'timeout': 400})
    match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError(f"AI Debugger không trả về JSON hợp lệ.")
    return json.loads(match.group(0), strict=False)

def apply_patch(repo_name, file_path, new_content, commit_message, current_sha):
    print(f"--- 🩹 Đang áp dụng bản vá cho file: {file_path} ---")
    url = f"{API_BASE_URL}/repos/{repo_name}/contents/{file_path}"
    data = {"message": commit_message, "content": base64.b64encode(new_content.encode('utf-8')).decode('utf-8'), "sha": current_sha, "author": COMMIT_AUTHOR}
    requests.put(url, headers=HEADERS, json=data).raise_for_status()
    print("   - ✅ Bản vá đã được commit!")

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    try:
        repo_to_fix, failed_run_id = parse_bug_report(ISSUE_BODY)
        post_issue_comment(f"✅ **AI Debugger đã bắt đầu làm việc** trên repo `{repo_to_fix}`.")
        
        log = get_failed_job_log(repo_to_fix, failed_run_id)
        
        files_to_read = ["pubspec.yaml", "lib/main.dart"]
        files_content_map = {path: get_file_content(repo_to_fix, path) for path in files_to_read}
            
        fix_suggestion = call_gemini_for_fix(log, {p: c[0] for p, c in files_content_map.items()})
        
        file_to_patch = fix_suggestion.get("file_to_patch")
        if file_to_patch and file_to_patch in files_content_map:
            current_sha = files_content_map[file_to_patch][1]
            if not current_sha: raise ValueError(f"Không tìm thấy SHA của file cần vá: {file_to_patch}")
            
            commit_message = f"fix(ai): {fix_suggestion['commit_message']}"
            apply_patch(repo_to_fix, file_to_patch, fix_suggestion["corrected_code"], commit_message, current_sha)
            
            post_issue_comment(f"🎉 **Đã áp dụng bản vá tự động!**\n\n- **Phân tích:** {fix_suggestion['analysis']}\n- **Commit:** `{commit_message}`\n\nMột build mới sẽ được tự động kích hoạt trong repo `{repo_to_fix}`.")
        else:
            post_issue_comment(f"**Phân tích của AI:** {fix_suggestion.get('analysis', 'Không có.')}\n\nAI cho rằng không thể sửa lỗi tự động. Cần sự can thiệp của con người.")

    except Exception as e:
        error_trace = traceback.format_exc()
        error_message = f"❌ **[Debugger] Đã xảy ra lỗi:**\n\n**Lỗi:**\n```{e}```\n\n**Traceback:**\n```{error_trace}```"
        post_issue_comment(error_message)
        sys.exit(1)
