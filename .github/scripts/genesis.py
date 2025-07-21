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
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

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

def post_issue_comment(message):
    """Gửi một comment lên issue gốc."""
    print(f"--- 💬 Phản hồi lên Issue #{ISSUE_NUMBER} ---")
    url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
    try:
        response = requests.post(url, headers=HEADERS, json={"body": message}, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Cảnh báo: Không thể comment lên issue. Lỗi: {e}")

def parse_issue_body(body):
    """Phân tích nội dung của issue."""
    print("--- Bước 2: Đang phân tích yêu cầu từ Issue ---")
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

    final_params['prompt'] = final_params['prompt'].replace("```text", "").replace("```", "").strip()
    print(f"   - ✅ Phân tích thành công. Repo mới: {final_params['repo_name']}")
    return final_params

def call_gemini(user_prompt, language, model_name):
    """Gọi Gemini để tạo cấu trúc dự án."""
    print(f"--- Bước 3: Đang gọi AI ({model_name}) để tạo code ---")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(model_name)
    final_prompt = f'Bạn là một kỹ sư phần mềm chuyên về {language}. Dựa trên yêu cầu: "{user_prompt}", hãy tạo cấu trúc file và thư mục hoàn chỉnh, sẵn sàng để build. Trả về kết quả dưới dạng một đối tượng JSON lồng nhau duy nhất, bao bọc trong khối ```json ... ```.'
    response = model.generate_content(final_prompt, request_options={'timeout': 300})
    
    match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError(f"AI không trả về JSON hợp lệ. Phản hồi thô:\n{response.text}")
    
    print("   - ✅ AI đã tạo code thành công.")
    return json.loads(match.group(0), strict=False)

def create_and_commit_project(repo_name, file_tree):
    """Tạo repo mới và commit tất cả các file."""
    # 1. Tạo repo
    print(f"--- Bước 4: Đang tạo repository mới: {repo_name} ---")
    repo_url = f"{API_BASE_URL}/user/repos"
    repo_data = {"name": repo_name, "private": False, "auto_init": True}
    requests.post(repo_url, headers=HEADERS, json=repo_data).raise_for_status()
    print("   - ✅ Repo đã được tạo. Đợi 5 giây...")
    time.sleep(5)

    # 2. Lấy commit SHA mới nhất
    print("--- Bước 5: Đang commit file lên repo mới ---")
    ref_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/ref/heads/main"
    main_ref = requests.get(ref_url, headers=HEADERS).json()
    latest_commit_sha = main_ref['object']['sha']
    base_tree_sha = requests.get(main_ref['object']['url'], headers=HEADERS).json()['tree']['sha']
    
    # 3. Tạo blob cho từng file
    tree_elements = []
    for path, content in file_tree.items():
        if not isinstance(content, str): continue
        blob_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/blobs"
        blob_data = {"content": content, "encoding": "utf-8"}
        blob_sha = requests.post(blob_url, headers=HEADERS, json=blob_data).json()['sha']
        tree_elements.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})
        
    # 4. Tạo tree mới
    tree_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/trees"
    tree_data = {"base_tree": base_tree_sha, "tree": tree_elements}
    new_tree_sha = requests.post(tree_url, headers=HEADERS, json=tree_data).json()['sha']
    
    # 5. Tạo commit mới
    commit_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/commits"
    commit_data = {
        "message": "feat: Initial project structure by AI Factory",
        "author": COMMIT_AUTHOR, "parents": [latest_commit_sha], "tree": new_tree_sha
    }
    new_commit_sha = requests.post(commit_url, headers=HEADERS, json=commit_data).json()['sha']
    
    # 6. Cập nhật nhánh main
    requests.patch(ref_url, headers=HEADERS, json={"sha": new_commit_sha}).raise_for_status()
    print("   - ✅ Đã commit tất cả file thành công!")

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    try:
        params = parse_issue_body(ISSUE_BODY)
        repo_name, language, ai_model, user_prompt = params.values()
        
        post_issue_comment(f"✅ Đã nhận yêu cầu. Bắt đầu gọi AI ({ai_model})...")
        
        file_tree = call_gemini(user_prompt, language, ai_model)
        
        if language.lower() == 'flutter':
            file_tree[".github/workflows/build_and_release.yml"] = FLUTTER_WORKFLOW_CONTENT
            post_issue_comment("⚙️ Đã thêm workflow build APK.")
        
        create_and_commit_project(repo_name, file_tree)
        
        success_message = f"🎉 **Dự án `{repo_name}` đã được tạo thành công!**\n\n- **Link:** https://github.com/{REPO_OWNER}/{repo_name}\n- **Lưu ý:** Hãy vào repo mới, mục `Settings > Secrets and variables > Actions` để thêm các secret cần thiết cho việc build APK."
        post_issue_comment(success_message)
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_message = f"❌ **Đã xảy ra lỗi:**\n\n**Lỗi:**\n```{e}```\n\n**Traceback:**\n```{error_trace}```"
        post_issue_comment(error_message)
        sys.exit(1)
