import os
import re
import json
import base64
import time
import sys
import requests
import google.generativeai as genai
from zipfile import ZipFile
from io import BytesIO

# ==============================================================================
# I. CẤU HÌNH VÀ LẤY BIẾN MÔI TRƯỜNG
# ==============================================================================
print("--- 🤖 AI Auto-Debugger Initialized ---")
try:
    ISSUE_BODY = os.environ["ISSUE_BODY"]
    ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    REPO_OWNER = os.environ["GH_USER"]
    COMMIT_AUTHOR = {"name": os.environ["COMMIT_NAME"], "email": os.environ["COMMIT_EMAIL"]}
except KeyError as e:
    print(f"❌ LỖI NGHIÊM TRỌNG: Thiếu biến môi trường: {e}")
    sys.exit(1)

API_BASE_URL = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
genai.configure(api_key=GEMINI_API_KEY)

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def post_issue_comment(message):
    url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
    try:
        requests.post(url, headers=HEADERS, json={"body": message}, timeout=30)
    except Exception as e:
        print(f"⚠️ Cảnh báo: Không thể comment lên issue: {e}")

def parse_report_issue(body):
    print("--- 🔎 Đang phân tích báo cáo lỗi ---")
    repo_match = re.search(r"- \*\*Repo:\*\* `(.*?)`", body)
    run_url_match = re.search(r"- \*\*Workflow Run URL:\*\* (.*)", body)
    
    if not repo_match or not run_url_match:
        raise ValueError("Issue báo lỗi không chứa đủ thông tin (Repo, Workflow Run URL).")
    
    repo_full_name = repo_match.group(1)
    run_url = run_url_match.group(1)
    run_id = run_url.split('/')[-1]
    
    print(f"   - Repo bị lỗi: {repo_full_name}")
    print(f"   - Run ID: {run_id}")
    return repo_full_name, run_id

def get_failed_job_log(repo_full_name, run_id):
    print("--- 📥 Đang tải log lỗi từ workflow ---")
    jobs_url = f"{API_BASE_URL}/repos/{repo_full_name}/actions/runs/{run_id}/jobs"
    jobs = requests.get(jobs_url, headers=HEADERS).json()['jobs']
    
    for job in jobs:
        if job['conclusion'] == 'failure':
            log_url = job['logs_url']
            print(f"   - Tìm thấy job thất bại: {job['name']}. Đang tải log...")
            # GitHub chuyển hướng đến một URL khác, cần cho phép chuyển hướng
            log_content = requests.get(log_url, headers=HEADERS, allow_redirects=True).text
            # Chỉ lấy 150 dòng cuối để không làm prompt quá dài
            short_log = "\n".join(log_content.splitlines()[-150:])
            return short_log
    
    raise ValueError("Không tìm thấy job nào thất bại trong workflow run.")

def get_file_content(repo_full_name, file_path):
    print(f"--- 📥 Đang tải nội dung file: {file_path} ---")
    content_url = f"{API_BASE_URL}/repos/{repo_full_name}/contents/{file_path}"
    response = requests.get(content_url, headers=HEADERS).json()
    return base64.b64decode(response['content']).decode('utf-8'), response['sha']

def call_gemini_for_fix(error_log, code_files):
    print("--- 🧠 Đang yêu cầu Gemini phân tích và sửa lỗi ---")
    model = genai.GenerativeModel("gemini-1.5-pro-latest") # Dùng model mạnh nhất để gỡ lỗi
    
    files_str = "\n".join([f"--- FILE: {path} ---\n```dart\n{content}\n```" for path, (content, _) in code_files.items()])

    prompt = f"""
    Bạn là một Kỹ sư Flutter Senior chuyên gỡ lỗi. Một quy trình build đã thất bại.
    
    **LOG LỖI (150 DÒNG CUỐI):**
    ```
    {error_log}
    ```

    **CÁC FILE CODE LIÊN QUAN:**
    {files_str}

    **NHIỆM VỤ:**
    1.  Phân tích log lỗi và code để tìm ra nguyên nhân gốc rễ.
    2.  Viết lại **TOÀN BỘ NỘI DUNG** của file cần sửa để khắc phục lỗi.
    3.  Chỉ trả về kết quả dưới dạng một đối tượng JSON duy nhất theo định dạng sau. **Không giải thích gì thêm.**

    **ĐỊNH DẠNG JSON:**
    ```json
    {{
      "analysis": "Nguyên nhân lỗi là do thư viện `non_existent_package` không tồn tại trong `pubspec.yaml`.",
      "file_to_fix": "pubspec.yaml",
      "corrected_code": "name: my_app\ndescription: A new Flutter project.\n...\ndependencies:\n  flutter:\n    sdk: flutter\n  # Đã xóa bỏ thư viện không tồn tại\n"
    }}
    ```
    """
    response = model.generate_content(prompt, request_options={'timeout': 400})
    match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError(f"AI không trả về JSON sửa lỗi hợp lệ.")
    
    print("   - ✅ Gemini đã đề xuất bản vá.")
    return json.loads(match.group(0), strict=False)

def commit_fix(repo_full_name, file_path, new_content, old_sha, commit_message):
    print(f"--- ⬆️  Đang commit bản vá cho file {file_path} ---")
    url = f"{API_BASE_URL}/repos/{repo_full_name}/contents/{file_path}"
    data = {
        "message": commit_message,
        "content": base64.b64encode(new_content.encode('utf-8')).decode('utf-8'),
        "sha": old_sha,
        "author": COMMIT_AUTHOR
    }
    requests.put(url, headers=HEADERS, json=data).raise_for_status()
    print("   - ✅ Đã commit bản vá thành công!")

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    try:
        repo_to_fix, run_id = parse_report_issue(ISSUE_BODY)
        
        # Đếm số lần thử (cơ chế an toàn)
        issue_comments_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
        comments = requests.get(issue_comments_url, headers=HEADERS).json()
        attempt_count = sum(1 for c in comments if "AI Auto-Debugger Attempt" in c.get('body', ''))
        
        if attempt_count >= 2:
            post_issue_comment("❌ **Đã thử sửa lỗi 2 lần và thất bại.** Dừng lại để con người can thiệp.")
            sys.exit(0)

        post_issue_comment(f"✅ **AI Auto-Debugger Attempt #{attempt_count + 1}**\n\nBắt đầu quy trình phân tích và sửa lỗi tự động...")

        error_log = get_failed_job_log(repo_to_fix, run_id)
        
        # Giả định lỗi thường ở pubspec.yaml hoặc lib/main.dart
        files_to_analyze = {}
        try:
            pubspec_content, pubspec_sha = get_file_content(repo_to_fix, "pubspec.yaml")
            files_to_analyze["pubspec.yaml"] = (pubspec_content, pubspec_sha)
        except Exception: pass # Bỏ qua nếu file không tồn tại

        try:
            main_dart_content, main_dart_sha = get_file_content(repo_to_fix, "lib/main.dart")
            files_to_analyze["lib/main.dart"] = (main_dart_content, main_dart_sha)
        except Exception: pass

        if not files_to_analyze:
            raise ValueError("Không thể tải về bất kỳ file nào để phân tích.")

        fix_suggestion = call_gemini_for_fix(error_log, files_to_analyze)
        
        file_to_fix = fix_suggestion.get("file_to_fix")
        corrected_code = fix_suggestion.get("corrected_code")
        analysis = fix_suggestion.get("analysis")

        if not file_to_fix or not corrected_code:
            raise ValueError("AI không trả về đầy đủ thông tin để sửa lỗi (file_to_fix, corrected_code).")

        post_issue_comment(f"🧠 **Phân tích của AI:** {analysis}\n\nĐang áp dụng bản vá cho file `{file_to_fix}`...")
        
        _, old_sha = files_to_analyze[file_to_fix]
        commit_message = f"fix(ai): Attempt to fix build error: {analysis}"
        commit_fix(repo_to_fix, file_to_fix, corrected_code, old_sha, commit_message)
        
        post_issue_comment("✅ **Đã áp dụng bản vá!**\n\nCommit mới đã được đẩy lên. Một workflow build mới sẽ được tự động kích hoạt trong repo con. Hãy theo dõi kết quả.")

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_message = f"❌ **Debugger đã gặp lỗi nghiêm trọng:**\n\n**Lỗi:**\n```\n{e}\n```\n\n**Traceback:**\n```\n{error_trace}\n```"
        post_issue_comment(error_message)
        sys.exit(1)
