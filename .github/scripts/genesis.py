import os
import re
import json
import base64
import time
import sys
import requests
import google.generativeai as genai

# ==============================================================================
# I. CẤU HÌNH VÀ LẤY BIẾN MÔI TRƯỜNG
# ==============================================================================
print("--- Bước 1: Đang tải cấu hình và biến môi trường ---")
try:
    ISSUE_BODY = os.environ["ISSUE_BODY"]
    ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    REPO_OWNER = os.environ["GH_USER"]
    COMMIT_EMAIL = os.environ["COMMIT_EMAIL"]
    COMMIT_NAME = os.environ["COMMIT_NAME"]
except KeyError as e:
    print(f"❌ LỖI NGHIÊM TRỌNG: Thiếu biến môi trường bắt buộc: {e}")
    sys.exit(1)

COMMIT_AUTHOR = {"name": COMMIT_NAME, "email": COMMIT_EMAIL}
API_BASE_URL = "https://api.github.com"

FLUTTER_WORKFLOW_CONTENT = r"""
name: Build and Release Flutter APK
on: [push, workflow_dispatch]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with: { java-version: '17', distribution: 'temurin' }
      - uses: subosito/flutter-action@v2
        with: { channel: 'stable' }
      - run: flutter pub get
      - run: mkdir -p android/app
      - name: Decode Keystore
        run: echo "${{ secrets.RELEASE_KEYSTORE_BASE64 }}" | base64 --decode > android/app/upload-keystore.jks
      - name: Create key.properties
        run: |
          echo "storePassword=${{ secrets.RELEASE_KEYSTORE_PASSWORD }}" > android/key.properties
          echo "keyPassword=${{ secrets.RELEASE_KEY_PASSWORD }}" >> android/key.properties
          echo "keyAlias=${{ secrets.RELEASE_KEY_ALIAS }}" >> android/key.properties
          echo "storeFile=../app/upload-keystore.jks" >> android/key.properties
      - name: Build APK
        run: |
          flutter clean
          flutter build apk --release
      - uses: actions/upload-artifact@v4
        with:
          name: release-apk
          path: build/app/outputs/flutter-apk/app-release.apk
"""

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def github_api_request(method, url, json_data=None):
    """Hàm chung để gửi yêu cầu đến GitHub API, đã được sửa lỗi."""
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        # Chuyển đổi json_data thành chuỗi JSON nếu nó là một dictionary
        data_payload = json.dumps(json_data) if json_data else None
        response = requests.request(method, url, headers=headers, data=data_payload, timeout=60)
        response.raise_for_status()
        # Trả về None nếu không có nội dung (ví dụ: status 204)
        return response.json() if response.status_code != 204 and response.content else None
    except requests.exceptions.HTTPError as e:
        print(f"Lỗi API GitHub ({e.response.status_code}) khi gọi {method} {url}: {e.response.text}")
        raise

def parse_issue_body(body):
    """Phân tích nội dung của issue và dọn dẹp prompt."""
    print("--- Bước 2: Đang phân tích nội dung yêu cầu từ Issue ---")
    params = {}
    pattern = re.compile(r"### (.*?)\s*\n\s*(.*?)\s*(?=\n###|$)", re.DOTALL)
    
    for match in pattern.finditer(body):
        key = match.group(1).strip().lower().replace(' ', '_')
        value = match.group(2).strip()
        params[key] = value

    final_params = {
        "repo_name": params.get("new_repository_name"),
        "language": params.get("language_or_framework"),
        "ai_model": params.get("gemini_model"),
        "prompt": params.get("detailed_prompt_(the_blueprint)"),
    }
    
    if not all(final_params.values()):
        missing = [k for k, v in final_params.items() if not v]
        raise ValueError(f"Không thể phân tích đủ thông tin từ Issue. Các trường bị thiếu: {missing}")

    # Dọn dẹp prompt, loại bỏ các khối markdown
    final_params['prompt'] = final_params['prompt'].replace("```text", "").replace("```", "").strip()
    
    print(f"   - Repo mới: {final_params['repo_name']}")
    return final_params

def call_gemini(user_prompt, language, model_name):
    # ... (Hàm này giữ nguyên như cũ, không cần sửa) ...
    print(f"--- Bước 3: Đang gọi AI ({model_name}) để tạo code ---")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(model_name)
    final_prompt = f"""
    Bạn là một kỹ sư phần mềm chuyên về {language}.
    Dựa trên yêu cầu sau: "{user_prompt}"
    Hãy tạo ra cấu trúc file và thư mục hoàn chỉnh, sẵn sàng để build.
    Trả về kết quả dưới dạng một đối tượng JSON lồng nhau duy nhất, bao bọc trong khối ```json ... ```.
    """
    response = model.generate_content(final_prompt, request_options={'timeout': 300})
    match = re.search(r'```json\s*(\{.*?\})\s*```', response.text, re.DOTALL)
    if not match: match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError(f"AI không trả về JSON hợp lệ. Phản hồi thô:\n{response.text}")
    return json.loads(match.group(0), strict=False)

def create_repo(repo_name):
    # ... (Hàm này giữ nguyên) ...
    print(f"--- Bước 4: Đang tạo repository mới: {repo_name} ---")
    url = f"{API_BASE_URL}/user/repos"
    data = {"name": repo_name, "private": False, "auto_init": True}
    github_api_request("POST", url, data)
    time.sleep(5)

def commit_files_via_api(repo_name, file_tree):
    # ... (Hàm này giữ nguyên) ...
    print(f"--- Bước 5: Đang chuẩn bị và commit {len(file_tree)} file lên repo ---")
    main_ref = github_api_request("GET", f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/ref/heads/main")
    latest_commit_sha = main_ref['object']['sha']
    base_tree_sha = github_api_request("GET", f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/commits/{latest_commit_sha}")['tree']['sha']
    tree_elements = []
    for path, content in file_tree.items():
        if not isinstance(content, str): continue
        blob = github_api_request("POST", f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/blobs", {
            "content": content, "encoding": "utf-8"
        })
        tree_elements.append({"path": path, "mode": "100644", "type": "blob", "sha": blob['sha']})
    new_tree = github_api_request("POST", f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/trees", {
        "base_tree": base_tree_sha, "tree": tree_elements
    })
    new_commit = github_api_request("POST", f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/commits", {
        "message": "feat: Initial project structure generated by AI Factory",
        "author": COMMIT_AUTHOR, "parents": [latest_commit_sha], "tree": new_tree['sha']
    })
    github_api_request("PATCH", f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/refs/heads/main", {"sha": new_commit['sha']})
    print("   - ✅ Đã commit tất cả file thành công!")


def comment_on_issue(message):
    """Viết comment phản hồi vào issue gốc."""
    print(f"--- Phản hồi cho người dùng trên Issue #{ISSUE_NUMBER} ---")
    url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
    # Dùng `json=` thay vì `data=` để requests tự xử lý header và encoding
    github_api_request("POST", url, json_data={"body": message})

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    try:
        params = parse_issue_body(ISSUE_BODY)
        repo_name = params['repo_name']
        language = params['language']
        ai_model = params['ai_model']
        user_prompt = params['prompt']
        
        comment_on_issue(f"✅ Đã nhận yêu cầu cho repo `{repo_name}`. Bắt đầu gọi AI ({ai_model})...")
        
        file_tree = call_gemini(user_prompt, language, ai_model)
        
        if language.lower() == 'flutter':
            print("   - Dự án Flutter, đang thêm workflow build APK...")
            file_tree[".github/workflows/build_and_release.yml"] = FLUTTER_WORKFLOW_CONTENT
            comment_on_issue("⚙️ Đã thêm workflow tự động build APK vào dự án.")
        
        create_repo(repo_name)
        commit_files_via_api(repo_name, file_tree)
        
        success_message = f"""
        🎉 **Dự án `{repo_name}` đã được tạo thành công!**

        - **Link Repository:** https://github.com/{REPO_OWNER}/{repo_name}
        - **Hành động tiếp theo:**
          1. **Thêm Secrets:** Để workflow build APK hoạt động, bạn cần vào repo mới, đi tới `Settings > Secrets and variables > Actions` và thêm các secret `RELEASE_KEYSTORE_BASE64`, `RELEASE_KEYSTORE_PASSWORD`, `RELEASE_KEY_ALIAS`, `RELEASE_KEY_PASSWORD`.
          2. **Kích hoạt Workflow:** Workflow sẽ tự chạy sau khi được commit. Bạn cũng có thể vào tab 'Actions' để chạy thủ công.
        """
        comment_on_issue(success_message)
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_message = f"❌ **Đã xảy ra lỗi nghiêm trọng:**\n\n**Lỗi:**\n```\n{e}\n```\n\n**Traceback:**\n```\n{error_trace}\n```"
        comment_on_issue(error_message)
        sys.exit(1)
