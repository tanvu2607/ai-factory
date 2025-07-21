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

# Lấy các biến từ môi trường của GitHub Actions, thoát nếu thiếu
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

# Thiết lập các hằng số
COMMIT_AUTHOR = {"name": COMMIT_NAME, "email": COMMIT_EMAIL}
API_BASE_URL = "https://api.github.com"

# Nội dung workflow "chuẩn" cho Flutter
FLUTTER_WORKFLOW_CONTENT = r"""
name: Build and Release Flutter APK

on:
  push:
    branches: [ main ]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Java
        uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'

      - name: Set up Flutter
        uses: subosito/flutter-action@v2
        with:
          channel: 'stable'

      - name: Get dependencies
        run: flutter pub get

      - name: Create required Android directories
        run: mkdir -p android/app

      # LƯU Ý: Người dùng cần tự thêm các secret này vào repo mới được tạo
      - name: Decode Keystore
        run: |
          echo "Decoding keystore..."
          echo "${{ secrets.RELEASE_KEYSTORE_BASE64 }}" | base64 --decode > android/app/upload-keystore.jks
        
      - name: Create key.properties
        run: |
          echo "Creating key.properties..."
          echo "storePassword=${{ secrets.RELEASE_KEYSTORE_PASSWORD }}" > android/key.properties
          echo "keyPassword=${{ secrets.RELEASE_KEY_PASSWORD }}" >> android/key.properties
          echo "keyAlias=${{ secrets.RELEASE_KEY_ALIAS }}" >> android/key.properties
          echo "storeFile=../app/upload-keystore.jks" >> android/key.properties

      - name: Build APK
        run: |
          flutter clean
          flutter build apk --release

      - name: Upload Artifact
        uses: actions/upload-artifact@v4
        with:
          name: release-apk
          path: build/app/outputs/flutter-apk/app-release.apk
"""

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def parse_issue_body(body):
    """Phân tích nội dung của issue để lấy ra các tham số."""
    print("--- Bước 2: Đang phân tích nội dung yêu cầu từ Issue ---")
    params = {}
    fields = ["repo_name", "language", "ai_model", "prompt"]
    for field in fields:
        match = re.search(rf"### {field}\s*\n\s*(.*?)\s*(?=\n###|$)", body, re.DOTALL)
        if match:
            params[field] = match.group(1).strip()
    if not all(params.get(f) for f in fields):
        raise ValueError("Không thể phân tích đủ thông tin từ Issue. Hãy chắc chắn form được điền đầy đủ.")
    print(f"   - Repo mới: {params['repo_name']}")
    print(f"   - Ngôn ngữ: {params['language']}")
    print(f"   - Model AI: {params['ai_model']}")
    return params

def call_gemini(user_prompt, language, model_name):
    """Gọi Gemini để tạo cấu trúc dự án."""
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

def github_api_request(method, url, json_data=None):
    """Hàm chung để gửi yêu cầu đến GitHub API."""
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        response = requests.request(method, url, headers=headers, json=json_data)
        response.raise_for_status()
        return response.json() if response.status_code != 204 and response.content else None
    except requests.exceptions.HTTPError as e:
        print(f"Lỗi API GitHub ({e.response.status_code}) khi gọi {method} {url}: {e.response.text}")
        raise

def create_repo(repo_name):
    """Tạo một repository mới trên GitHub."""
    print(f"--- Bước 4: Đang tạo repository mới: {repo_name} ---")
    url = f"{API_BASE_URL}/user/repos"
    data = {"name": repo_name, "private": False, "auto_init": True}
    github_api_request("POST", url, data)
    print("   - Repository đã được tạo. Đợi 5 giây để GitHub hoàn tất thiết lập...")
    time.sleep(5)

def commit_files_via_api(repo_name, file_tree):
    """Sử dụng Git Trees API để commit nhiều file cùng lúc."""
    print(f"--- Bước 5: Đang chuẩn bị và commit {len(file_tree)} file lên repo ---")
    
    # Lấy commit SHA mới nhất của nhánh main
    main_ref_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/ref/heads/main"
    main_ref = github_api_request("GET", main_ref_url)
    latest_commit_sha = main_ref['object']['sha']
    
    # Lấy tree SHA của commit đó
    commit_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/commits/{latest_commit_sha}"
    base_tree_sha = github_api_request("GET", commit_url)['tree']['sha']

    # Tạo các "blob" cho từng file
    tree_elements = []
    for path, content in file_tree.items():
        blob_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/blobs"
        blob = github_api_request("POST", blob_url, {"content": content, "encoding": "utf-8"})
        tree_elements.append({"path": path, "mode": "100644", "type": "blob", "sha": blob['sha']})
    
    # Tạo một "tree" mới từ các blob
    tree_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/trees"
    new_tree = github_api_request("POST", tree_url, {"base_tree": base_tree_sha, "tree": tree_elements})
    
    # Tạo "commit" mới
    new_commit_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/commits"
    new_commit = github_api_request("POST", new_commit_url, {
        "message": "feat: Initial project structure generated by AI Factory",
        "author": COMMIT_AUTHOR,
        "parents": [latest_commit_sha],
        "tree": new_tree['sha']
    })
    
    # Cập nhật nhánh main để trỏ vào commit mới
    github_api_request("PATCH", main_ref_url, {"sha": new_commit['sha']})
    print("   - ✅ Đã commit tất cả file thành công!")

def comment_on_issue(message):
    """Viết comment phản hồi vào issue gốc."""
    print(f"--- Phản hồi cho người dùng trên Issue #{ISSUE_NUMBER} ---")
    url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
    github_api_request("POST", url, {"body": message})

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
        
        # Thêm workflow build APK nếu là dự án Flutter
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
          2. **Kích hoạt Workflow:** Workflow sẽ tự chạy sau khi bạn push commit đầu tiên. Bạn cũng có thể vào tab 'Actions' để chạy thủ công.
        """
        comment_on_issue(success_message)
        
    except Exception as e:
        # Báo cáo lỗi chi tiết về lại issue
        error_message = f"❌ **Đã xảy ra lỗi nghiêm trọng trong quá trình tự động hóa:**\n\n**Lỗi:**\n```\n{e}\n```\n\nVui lòng kiểm tra lại prompt hoặc cấu hình."
        comment_on_issue(error_message)
        # Báo lỗi cho GitHub Actions để biết lần chạy thất bại
        sys.exit(1)
