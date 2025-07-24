import os
import re
import json
import base64
import time
import sys
import requests
import google.generativeai as genai
import traceback

# ==============================================================================
# I. CẤU HÌNH VÀ LẤY BIẾN MÔI TRƯỜNG
# ==============================================================================
print("--- [Genesis] Bước 1: Đang tải cấu hình ---")
try:
    ISSUE_BODY = os.environ["ISSUE_BODY"]
    ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"] # Đây là GH_PAT
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

FLUTTER_WORKFLOW_CONTENT = r"""
name: Build and Self-Heal Flutter App
on:
  push:
    branches: [ main ]
  workflow_dispatch:
    inputs:
      failed_run_id:
        description: 'ID of the failed workflow run to debug'
        required: false
      debug_attempt:
        description: 'Number of debug attempts'
        required: false
        default: '1'

jobs:
  build:
    if: github.event_name == 'push'
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
        id: build_step
        run: flutter build apk --release
      - uses: actions/upload-artifact@v4
        with:
          name: release-apk
          path: build/app/outputs/flutter-apk/app-release.apk
      - name: Trigger Self-Healing on Failure
        if: failure()
        uses: actions/github-script@v7
        with:
          github-token: ${{ secrets.GH_PAT_FOR_FACTORY }}
          script: |
            await github.rest.actions.createWorkflowDispatch({
              owner: '${{ github.repository_owner }}',
              repo: 'ai-factory',
              workflow_id: 'auto_debugger.yml',
              ref: 'main',
              inputs: {
                failed_run_id: `${{ github.run_id }}`,
                repo_to_fix: '${{ github.repository }}',
                debug_attempt: '1'
              }
            });
"""

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================
def post_issue_comment(message):
    url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
    requests.post(url, headers=HEADERS, json={"body": message}, timeout=30)

def parse_issue_body(body):
    print("--- [Genesis] Bước 2: Đang phân tích yêu cầu ---")
    params = {}
    pattern = re.compile(r"### (.*?)\s*\n\s*(.*?)\s*(?=\n###|$)", re.DOTALL)
    for match in pattern.finditer(body):
        key = match.group(1).strip().lower().replace(' ', '_').replace('(', '').replace(')', '')
        value = match.group(2).strip()
        params[key] = value
    final_params = {"repo_name": params.get("new_repository_name"), "language": params.get("language_or_framework"), "ai_model": params.get("gemini_model"), "prompt": params.get("detailed_prompt_the_blueprint")}
    if not all(final_params.values()): raise ValueError(f"Không thể phân tích đủ thông tin từ Issue. Thiếu: {[k for k, v in final_params.items() if not v]}")
    final_params['prompt'] = final_params['prompt'].replace("```text", "").replace("```", "").strip()
    return final_params

def call_gemini_for_code(user_prompt, language, model_name):
    print(f"--- [Genesis] Bước 3: Đang gọi AI ({model_name}) để tạo code ---")
    model = genai.GenerativeModel(model_name)
    final_prompt = f'Bạn là một kỹ sư phần mềm chuyên về {language}. Dựa trên yêu cầu: "{user_prompt}", hãy tạo cấu trúc file và thư mục hoàn chỉnh, sẵn sàng để build. Trả về dưới dạng một đối tượng JSON lồng nhau duy nhất, bao bọc trong khối ```json ... ```.'
    response = model.generate_content(final_prompt, request_options={'timeout': 300})
    match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError(f"AI không trả về JSON hợp lệ. Phản hồi thô:\n{response.text}")
    return json.loads(match.group(0), strict=False)

def create_and_commit_project(repo_name, file_tree):
    print(f"--- [Genesis] Bước 4: Đang tạo repo và commit file ---")
    requests.post(f"{API_BASE_URL}/user/repos", headers=HEADERS, json={"name": repo_name, "auto_init": True}).raise_for_status()
    time.sleep(5)
    
    ref_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/refs/heads/main"
    main_ref = requests.get(ref_url, headers=HEADERS).json()
    latest_commit_sha = main_ref['object']['sha']
    base_tree_sha = requests.get(main_ref['object']['url'], headers=HEADERS).json()['tree']['sha']
    
    tree_elements = []
    for path, content in file_tree.items():
        if not isinstance(content, str): continue
        blob_sha = requests.post(f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/blobs", headers=HEADERS, json={"content": content, "encoding": "utf-8"}).json()['sha']
        tree_elements.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})
        
    new_tree_sha = requests.post(f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/trees", headers=HEADERS, json={"base_tree": base_tree_sha, "tree": tree_elements}).json()['sha']
    
    commit_data = {"message": "feat: Initial project structure by AI Factory", "author": COMMIT_AUTHOR, "parents": [latest_commit_sha], "tree": new_tree_sha}
    new_commit_sha = requests.post(f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/git/commits", headers=HEADERS, json=commit_data).json()['sha']
    
    requests.patch(ref_url, headers=HEADERS, json={"sha": new_commit_sha}).raise_for_status()
    print("   - ✅ Đã commit tất cả file thành công!")

# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    try:
        params = parse_issue_body(ISSUE_BODY)
        repo_name, language, ai_model, user_prompt = params.values()
        post_issue_comment(f"✅ Đã nhận yêu cầu cho repo `{repo_name}`. Bắt đầu gọi AI ({ai_model})...")
        file_tree = call_gemini_for_code(user_prompt, language, ai_model)
        
        if language.lower() == 'flutter':
            file_tree[".github/workflows/build.yml"] = FLUTTER_WORKFLOW_CONTENT
            post_issue_comment("⚙️ Đã thêm workflow tự động build và tự sửa lỗi.")
        
        create_and_commit_project(repo_name, file_tree)
        
        success_message = f"🎉 **Dự án `{repo_name}` đã được tạo thành công!**\n\n- **Link:** https://github.com/{REPO_OWNER}/{repo_name}\n- **Lưu ý:** Hãy vào repo mới, mục `Settings > Secrets` để thêm các secret cần thiết cho việc build APK, đặc biệt là `GH_PAT_FOR_FACTORY` (dán chính PAT của `ai-factory`)."
        post_issue_comment(success_message)
        
    except Exception as e:
        error_trace = traceback.format_exc()
        error_message = f"❌ **[Genesis] Đã xảy ra lỗi:**\n\n**Lỗi:**\n```{e}```\n\n**Traceback:**\n```{error_trace}```"
        post_issue_comment(error_message)
        sys.exit(1)
