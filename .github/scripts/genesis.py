import os, re, json, base64, time, sys, requests, google.generativeai as genai, traceback

# ==============================================================================
# I. CẤU HÌNH VÀ LẤY BIẾN MÔI TRƯỜNG
# ==============================================================================
print("--- [Genesis] Bước 1: Đang tải cấu hình ---")
try:
    ISSUE_BODY = os.environ["ISSUE_BODY"]
    ISSUE_NUMBER = os.environ.get("ISSUE_NUMBER", "cli-run") # Dùng .get() để an toàn
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    REPO_OWNER = os.environ["GH_USER"]
    COMMIT_EMAIL = os.environ["COMMIT_EMAIL"]
    COMMIT_NAME = os.environ["COMMIT_NAME"]
except KeyError as e:
    print(f"❌ [Genesis] LỖI: Thiếu biến môi trường: {e}")
    sys.exit(1)

# ... (Toàn bộ các hằng số khác: COMMIT_AUTHOR, API_BASE_URL, HEADERS, FLUTTER_WORKFLOW_CONTENT... giữ nguyên như phiên bản "Siêu Ổn Định")

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def post_issue_comment(message):
    # SỬA LỖI: Chỉ comment nếu đây là một lần chạy từ Issue thật
    if ISSUE_NUMBER and ISSUE_NUMBER.isdigit():
        print(f"--- [Genesis] 💬 Phản hồi lên Issue #{ISSUE_NUMBER} ---")
        url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
        try:
            requests.post(url, headers=HEADERS, json={"body": message}, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"⚠️ [Genesis] Cảnh báo: Không thể comment. Lỗi: {e}")
    else:
        # Nếu chạy từ Gradio, chỉ in ra log
        print(f"--- [Genesis] Log: {message} ---")


def parse_issue_body(body):
    print("--- [Genesis] Bước 2: Đang phân tích yêu cầu ---")
    # ... (Hàm này giữ nguyên như phiên bản "Siêu Ổn Định")
    pass

def call_gemini_for_code(user_prompt, language, model_name):
    print(f"--- [Genesis] Bước 3: Đang gọi AI ({model_name}) ---")
    # ... (Hàm này giữ nguyên như phiên bản "Kiên cường")
    pass

# ... (Tất cả các hàm tiện ích khác: create_repo, flatten_file_tree, commit_files_via_api... giữ nguyên)

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    try:
        # Toàn bộ logic trong `main` giữ nguyên như phiên bản "Siêu Ổn Định",
        # không cần thay đổi gì vì nó đã được thiết kế để đọc `ISSUE_BODY`.
        
        params = parse_issue_body(ISSUE_BODY)
        repo_name, language, ai_model, user_prompt = params.values()
        
        post_issue_comment(f"✅ Đã nhận yêu cầu. Bắt đầu gọi AI ({ai_model})...")
        
        file_tree = call_gemini_for_code(user_prompt, language, ai_model)
        
        # ... (logic thêm workflow, tạo repo, commit file)
        
        success_message = f"🎉 **Dự án `{repo_name}` đã được tạo thành công!**\n- **Link:** https://github.com/{REPO_OWNER}/{repo_name}"
        post_issue_comment(success_message)
        
    except Exception as e:
        error_message = f"❌ **Đã xảy ra lỗi:**\n\n**Lỗi:**\n```{e}```\n\n**Traceback:**\n```{traceback.format_exc()}```"
        post_issue_comment(error_message)
        # In lỗi ra stderr để tiến trình cha (app.py) có thể bắt được
        print(error_message, file=sys.stderr)
        sys.exit(1)
