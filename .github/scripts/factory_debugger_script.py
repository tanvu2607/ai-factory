import os
import re
import json
import base64
import sys
import requests
import google.generativeai as genai
import zipfile
import io
import traceback
from pathlib import Path

# ==============================================================================
# I. CẤU HÌNH VÀ LẤY BIẾN MÔI TRƯỜNG
# ==============================================================================
print("--- 🤖 Factory Self-Debugger v1.0 Initializing ---")
try:
    FAILED_RUN_ID = os.environ["FAILED_RUN_ID"]
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"] # Đây là GH_PAT
    REPO_FULL_NAME = os.environ["REPO_TO_FIX"] # ví dụ "tanvu2607/ai-factory"
    FILE_TO_FIX_PATH = os.environ["FILE_TO_FIX"] # ví dụ ".github/scripts/genesis.py"
    REPO_OWNER, REPO_NAME = REPO_FULL_NAME.split('/')
except KeyError as e:
    print(f"❌ LỖI: Thiếu biến môi trường: {e}")
    sys.exit(1)

API_BASE_URL = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
genai.configure(api_key=GEMINI_API_KEY)

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def download_and_extract_logs():
    print(f"--- 📥 Đang tải log của lần chạy thất bại: {FAILED_RUN_ID} ---")
    logs_url = f"{API_BASE_URL}/repos/{REPO_FULL_NAME}/actions/runs/{FAILED_RUN_ID}/logs"
    for i in range(3):
        response = requests.get(logs_url, headers=HEADERS, stream=True, timeout=60)
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                # Tìm file log của job có khả năng bị lỗi nhất
                log_file_name = next((name for name in z.namelist() if 'generate-app' in name and name.endswith('.txt')), z.namelist()[0])
                with z.open(log_file_name) as f:
                    log_content = f.read().decode('utf-8', errors='ignore')
            # Lấy 300 dòng cuối để có đủ ngữ cảnh
            return "\n".join(log_content.splitlines()[-300:])
        print(f"Log chưa sẵn sàng (status: {response.status_code}), đợi 10 giây... (lần {i+1})")
        time.sleep(10)
    raise Exception("Không thể tải log lỗi sau nhiều lần thử.")

def get_file_to_fix_content():
    print(f"--- 📄 Đang đọc nội dung của file bị lỗi: {FILE_TO_FIX_PATH} ---")
    url = f"{API_BASE_URL}/repos/{REPO_FULL_NAME}/contents/{FILE_TO_FIX_PATH}"
    response = requests.get(url, headers=HEADERS, timeout=30).json()
    return base64.b64decode(response['content']).decode('utf-8')

def call_gemini_for_fix(error_log, original_code):
    print("--- 🧠 Đang gửi thông tin cho Gemini 1.5 Pro để phân tích và sửa lỗi ---")
    
    debug_prompt = f"""
    Bạn là một kỹ sư phần mềm Python Senior chuyên gỡ lỗi các hệ thống tự động hóa trên GitHub Actions.
    Một workflow đã thất bại. Nhiệm vụ của bạn là phân tích log lỗi, tìm ra nguyên nhân trong mã nguồn Python và viết lại toàn bộ file để sửa lỗi đó.

    --- LOG LỖI (300 dòng cuối) ---
    ```
    {error_log}
    ```

    --- MÃ NGUỒN GỐC CỦA FILE `{FILE_TO_FIX_PATH}` ---
    ```python
    {original_code}
    ```

    **YÊU CẦU:**
    1.  **Phân tích nguyên nhân gốc rễ** của lỗi.
    2.  **Viết lại TOÀN BỘ nội dung** của file `{FILE_TO_FIX_PATH}` với bản vá lỗi. Đảm bảo code mới phải hoàn chỉnh và đúng cú pháp.
    3.  Chỉ trả về kết quả dưới dạng một **đối tượng JSON duy nhất** có cấu trúc sau:
        `{{
          "analysis": "Phân tích ngắn gọn, chính xác về nguyên nhân lỗi.",
          "corrected_code": "Toàn bộ nội dung mới của file đã được sửa lỗi.",
          "commit_message": "Một commit message mô tả bản vá lỗi (ví dụ: fix(genesis): Improve JSON parsing to handle control characters)"
        }}`
    """
    
    # Dùng model Pro để có khả năng suy luận tốt nhất
    model = genai.GenerativeModel("gemini-1.5-pro-latest")
    response = model.generate_content(debug_prompt, request_options={'timeout': 600})
    
    match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError(f"AI Debugger không trả về JSON hợp lệ. Phản hồi thô:\n{response.text}")
    
    print("   - ✅ AI đã đề xuất một bản vá.")
    return json.loads(match.group(0), strict=False)

def set_action_output(name, value):
    """Ghi giá trị vào GITHUB_OUTPUT để các step sau có thể sử dụng."""
    with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
        # Xử lý chuỗi nhiều dòng cho output
        value = value.replace('%', '%25').replace('\n', '%0A').replace('\r', '%0D')
        f.write(f"{name}={value}\n")

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    try:
        error_log = download_and_extract_logs()
        original_code = get_file_to_fix_content()
        fix_suggestion = call_gemini_for_fix(error_log, original_code)

        # Ghi các kết quả ra GITHUB_OUTPUT
        set_action_output("analysis", fix_suggestion.get("analysis", "No analysis provided."))
        set_action_output("commit_message", fix_suggestion.get("commit_message", "fix(ai): Automated fix attempt"))
        
        # Lưu code đã sửa vào một file tạm
        corrected_code = fix_suggestion.get("corrected_code")
        if corrected_code:
            Path(FILE_TO_FIX_PATH).parent.mkdir(parents=True, exist_ok=True)
            Path(FILE_TO_FIX_PATH).write_text(corrected_code, encoding="utf-8")
            print(f"   - ✅ Đã ghi code đã sửa vào file cục bộ: {FILE_TO_FIX_PATH}")
        else:
            raise ValueError("AI không cung cấp code đã sửa.")

    except Exception as e:
        print("--- ❌ Đã xảy ra lỗi trong quá trình gỡ lỗi ---")
        traceback.print_exc()
        # Ghi lỗi ra output để có thể hiển thị trong PR
        set_action_output("analysis", f"An error occurred in the debugger:\n```\n{e}\n```")
        set_action_output("commit_message", "chore: Debugger failed to generate a fix")
        sys.exit(1)
