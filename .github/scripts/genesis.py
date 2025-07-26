import os
import re
import json
import base64
import time
import sys
import requests
import google.generativeai as genai
import traceback
import argparse

# ==============================================================================
# I. CẤU HÌNH VÀ LẤY BIẾN MÔI TRƯỜNG
# ==============================================================================
print("--- [Genesis] Bước 1: Đang tải cấu hình ---")
try:
    # ISSUE_NUMBER giờ đây là tùy chọn, được truyền từ CLI
    ISSUE_NUMBER = os.environ.get("ISSUE_NUMBER", "cli-run")
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    REPO_OWNER = os.environ["GH_USER"]
    COMMIT_EMAIL = os.environ["COMMIT_EMAIL"]
    COMMIT_NAME = os.environ["COMMIT_NAME"]
except KeyError as e:
    print(f"❌ [Genesis] LỖI: Thiếu biến môi trường: {e}")
    sys.exit(1)

COMMIT_AUTHOR = {"name": COMMIT_NAME, "email": COMMIT_EMAIL}
API_BASE_URL = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
genai.configure(api_key=GEMINI_API_KEY)

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
      - name: Decode Keystore and Create Properties
        run: |
          mkdir -p android/app
          echo "${{ secrets.RELEASE_KEYSTORE_BASE64 }}" | base64 --decode > android/app/upload-keystore.jks
          echo "storePassword=${{ secrets.RELEASE_KEYSTORE_PASSWORD }}" > android/key.properties
          echo "keyPassword=${{ secrets.RELEASE_KEY_PASSWORD }}" >> android/key.properties
          echo "keyAlias=${{ secrets.RELEASE_KEY_ALIAS }}" >> android/key.properties
          echo "storeFile=../app/upload-keystore.jks" >> android/key.properties
      - name: Build APK
        run: flutter build apk --release
      - uses: actions/upload-artifact@v4
        with: { name: release-apk, path: build/app/outputs/flutter-apk/app-release.apk }
"""

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def post_issue_comment(message):
    if ISSUE_NUMBER and ISSUE_NUMBER.isdigit():
        print(f"--- [Genesis] 💬 Phản hồi lên Issue #{ISSUE_NUMBER} ---")
        url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
        try:
            requests.post(url, headers=HEADERS, json={"body": message}, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"⚠️ [Genesis] Cảnh báo: Không thể comment. Lỗi: {e}")
    else:
        print(f"--- [Genesis] Log: {message} ---")

def call_gemini_for_code(user_prompt, language, model_name):
    print(f"--- [Genesis] Bước 2: Đang gọi AI ({model_name}) ---")
    model = genai.GenerativeModel(model_name)
    final_prompt = f'Bạn là một kỹ sư phần mềm chuyên về {language}. Dựa trên yêu cầu: "{user_prompt}", hãy tạo cấu trúc file và thư mục hoàn chỉnh. Trả về dưới dạng một đối tượng JSON lồng nhau duy nhất, bao bọc trong khối ```json ... ```.'
    response = model.generate_content(final_prompt, request_options={'timeout': 300})
    match = re.search(r'```json\s*(\{.*?\})\s*```', response.text, re.DOTALL)
    if not match: match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError(f"AI không trả về JSON hợp lệ. Phản hồi thô:\n{response.text}")
    print("   - ✅ AI đã tạo code thành công.")
    return json.loads(match.group(0), strict=False)

def flatten_file_tree(file_tree, path=''):
    items = {}
    for key, value in file_tree.items():
        new_path = os.path.join(path, key) if path else key
        if isinstance(value, dict):
            items.update(flatten_file_tree(value, new_path))
        else:
            items[new_path] = value
    return items

def create_and_commit_project(repo_name, file_tree):
    print(f"--- [Genesis] Bước 3: Đang tạo repo và commit {len(file_tree)} file ---")
    requests.post(f"{API_BASE_URL}/user/repos", headers=HEADERS, json={"name": repo_name, "private": False, "auto_init": True}).raise_for_status()
    print("   - Repo đã được tạo. Đợi 5 giây...")
    time.sleep(5)
    
    ref_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/refs/heads/main"
    main_ref = requests.get(ref_url, headers=HEADERS).json()
    latest_commit_sha = main_ref['object']['sha']
    base_tree_sha = requests.get(main_ref['object']['url'], headers=HEADERS).json()['tree']['sha']
    
    tree_elements = []
    for path, content in file_tree.items():
        if not isinstance(content, str): continue
        blob = requests.post(f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/blobs", headers=HEADERS, json={"content": content, "encoding": "utf-8"}).json()
        tree_elements.append({"path": path, "mode": "100644", "type": "blob", "sha": blob['sha']})
        
    new_tree = requests.post(f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/trees", headers=HEADERS, json={"base_tree": base_tree_sha, "tree": tree_elements}).json()
    new_commit = requests.post(f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/commits", headers=HEADERS, json={"message": "feat: Initial project structure by AI Factory", "author": COMMIT_AUTHOR, "parents": [latest_commit_sha], "tree": new_tree['sha']}).json()
    requests.patch(ref_url, headers=HEADERS, json={"sha": new_commit['sha']}).raise_for_status()
    print("   - ✅ Đã commit tất cả file thành công!")

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    # === THAY ĐỔI CỐT LÕI: SỬ DỤNG ARGPARSE ĐỂ NHẬN INPUT ===
    parser = argparse.ArgumentParser(description="AI Genesis Script")
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    # issue-number là tùy chọn, sẽ có giá trị "gradio-run" nếu chạy từ Space
    parser.add_argument("--issue-number", default="cli-run")
    args = parser.parse_args()

    # Gán các biến từ arguments
    repo_name = args.repo_name
    language = args.language
    ai_model = args.model
    user_prompt = args.prompt
    ISSUE_NUMBER = args.issue_number # Cập nhật biến toàn cục

    try:
        post_issue_comment(f"✅ Đã nhận yêu cầu. Bắt đầu gọi AI ({ai_model})...")
        
        file_tree = call_gemini_for_code(user_prompt, language, ai_model)
        
        if language.lower() == 'flutter':
            file_tree[".github/workflows/build.yml"] = FLUTTER_WORKFLOW_CONTENT
            post_issue_comment("⚙️ Đã thêm workflow build APK.")
        
        flat_file_tree = flatten_file_tree(file_tree)
        create_and_commit_project(repo_name, flat_file_tree)
        
        success_message = f"🎉 **Dự án `{repo_name}` đã được tạo thành công!**\n- **Link:** https://github.com/{REPO_OWNER}/{repo_name}"
        post_issue_comment(success_message)
        
    except Exception as e:
        error_message = f"❌ **Đã xảy ra lỗi:**\n\n**Lỗi:**\n```{e}```\n\n**Traceback:**\n```{traceback.format_exc()}```"
        post_issue_comment(error_message)
        print(error_message, file=sys.stderr)
        sys.exit(1)
