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
# I. CẤU HÌNH
# ==============================================================================
print("--- [Genesis] Bước 1: Đang tải cấu hình ---")
try:
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    REPO_OWNER = os.environ["GH_USER"]
    COMMIT_AUTHOR = {"name": os.environ["COMMIT_NAME"], "email": os.environ["COMMIT_EMAIL"]}
except KeyError as e:
    print(f"❌ [Genesis] LỖI: Thiếu biến môi trường: {e}", file=sys.stderr)
    sys.exit(1)

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

def extract_json_from_ai(text: str) -> dict:
    print("   - Đang trích xuất JSON...")
    if not text or not text.strip():
        raise ValueError("Phản hồi từ AI là chuỗi rỗng.")
    match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if not match: match = re.search(r'(\{.*?\})', text, re.DOTALL)
    if not match: raise ValueError(f"Không tìm thấy JSON hợp lệ trong phản hồi. Phản hồi thô:\n{text}")
    try:
        return json.loads(match.group(1), strict=False)
    except json.JSONDecodeError as ex:
        raise ValueError(f"Lỗi khi phân tích JSON: {ex}. JSON thô: {match.group(1)}")

def call_gemini_for_code(user_prompt, language, model_name):
    final_prompt = f'Bạn là một kỹ sư phần mềm chuyên về {language}. Dựa trên yêu cầu: "{user_prompt}", hãy tạo cấu trúc file và thư mục hoàn chỉnh. Trả về dưới dạng một đối tượng JSON lồng nhau duy nhất, bao bọc trong khối ```json ... ```.'
    
    model = genai.GenerativeModel(model_name)
    safety_settings = [{"category": c, "threshold": "BLOCK_NONE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
    
    for attempt in range(1, 4):
        print(f"--- [Genesis] Bước 2: Đang gọi AI ({model_name}) - Lần thử {attempt}/3 ---")
        try:
            response = model.generate_content(final_prompt, request_options={'timeout': 300}, safety_settings=safety_settings)
            
            if hasattr(response, 'text') and response.text:
                print("   - ✅ AI đã phản hồi. Đang xử lý...")
                return extract_json_from_ai(response.text)
            elif not response.parts:
                raise ValueError(f"Phản hồi từ AI bị trống hoặc bị chặn. Lý do: {getattr(response.prompt_feedback, 'block_reason', 'Không rõ')}")
            else: # Fallback
                full_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
                return extract_json_from_ai(full_text)
                
        except Exception as e:
            print(f"   - ⚠️  Lỗi ở lần thử {attempt}: {e}")
            if attempt < 3:
                print("   - Đang đợi 5 giây trước khi thử lại...")
                time.sleep(5)
            else:
                print("   - ❌ Đã thử 3 lần và vẫn thất bại.")
                raise e # Ném lại lỗi cuối cùng
    raise RuntimeError("Không thể tạo code từ AI sau nhiều lần thử.")

def flatten_file_tree(file_tree, path=''):
    items = {}
    for key, value in file_tree.items():
        new_path = os.path.join(path, key) if path else key
        if isinstance(value, dict): items.update(flatten_file_tree(value, new_path))
        else: items[new_path] = value
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
    parser = argparse.ArgumentParser(description="AI Genesis Script")
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args()
    
    repo_name, language, ai_model, user_prompt = args.repo_name, args.language, args.model, args.prompt

    try:
        print(f"✅ Đã nhận yêu cầu cho repo `{repo_name}`.")
        file_tree = call_gemini_for_code(user_prompt, language, ai_model)
        
        if language.lower() == 'flutter':
            file_tree[".github/workflows/build.yml"] = FLUTTER_WORKFLOW_CONTENT
        
        flat_file_tree = flatten_file_tree(file_tree)
        create_and_commit_project(repo_name, flat_file_tree)
        
        print(f"🎉 Dự án `{repo_name}` đã được tạo thành công!")
    except Exception as e:
        print(f"❌ Đã xảy ra lỗi trong genesis.py: {e}\n{traceback.format_exc()}", file=sys.stderr)
        sys.exit(1)
