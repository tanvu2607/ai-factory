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
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    REPO_OWNER = os.environ["GH_USER"]
    COMMIT_EMAIL = os.environ["COMMIT_EMAIL"]
    COMMIT_NAME = os.environ["COMMIT_NAME"]
    # Lấy các secret của keystore từ môi trường
    RELEASE_KEYSTORE_BASE64 = os.environ["RELEASE_KEYSTORE_BASE64"]
    RELEASE_KEYSTORE_PASSWORD = os.environ["RELEASE_KEYSTORE_PASSWORD"]
    RELEASE_KEY_ALIAS = os.environ["RELEASE_KEY_ALIAS"]
    RELEASE_KEY_PASSWORD = os.environ["RELEASE_KEY_PASSWORD"]
except KeyError as e:
    print(f"❌ LỖI: Thiếu biến môi trường hoặc secret: {e}")
    sys.exit(1)

COMMIT_AUTHOR = {"name": COMMIT_NAME, "email": COMMIT_EMAIL}
API_BASE_URL = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
genai.configure(api_key=GEMINI_API_KEY)

FLUTTER_WORKFLOW_CONTENT = r"""
name: Build and Self-Heal Flutter App
on: [push, workflow_dispatch]
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
        with: { name: release-apk, path: build/app/outputs/flutter-apk/app-release.apk }
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
                repo_to_fix: '${{ github.repository }}'
              }
            });
"""

# ==============================================================================
# II. CÁC HÀM TIỆN ÍCH
# ==============================================================================

def post_issue_comment(message):
    url = f"{API_BASE_URL}/repos/{REPO_OWNER}/ai-factory/issues/{ISSUE_NUMBER}/comments"
    requests.post(url, headers=HEADERS, json={"body": message})

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

def _call_gemini_raw(prompt, model_name):
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt, request_options={'timeout': 300})
    return response.text

def extract_and_clean_json(text):
    print("--- 🧠 Đang trích xuất và dọn dẹp JSON ---")
    match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if not match: match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match: raise ValueError("Không tìm thấy đối tượng JSON hợp lệ trong phản hồi.")
    json_str = match.group(0).replace("```json", "").replace("```", "").strip()
    json_str = re.sub(r',\s*([\}\]])', r'\1', json_str)
    return json_str

def call_gemini_for_code(user_prompt, language, model_name):
    print(f"--- [Genesis] Bước 3: Đang gọi AI ({model_name}) ---")
    final_prompt = f'Bạn là một kỹ sư phần mềm chuyên về {language}. Dựa trên yêu cầu: "{user_prompt}", hãy tạo cấu trúc file và thư mục hoàn chỉnh. Trả về JSON lồng nhau duy nhất, bao bọc trong khối ```json ... ```.'
    raw_response = ""
    json_str = ""
    try:
        raw_response = _call_gemini_raw(final_prompt, model_name)
        json_str = extract_and_clean_json(raw_response)
        parsed_json = json.loads(json_str)
        print("   - ✅ AI đã tạo JSON hợp lệ lần đầu.")
        return parsed_json
    except (json.JSONDecodeError, ValueError) as e:
        post_issue_comment(f"⚠️ **Cảnh báo:** AI đã trả về JSON không hợp lệ. Bắt đầu tự sửa lỗi...")
        repair_prompt = f"Phản hồi trước của bạn gây lỗi parse JSON. LỖI: {e}\nJSON BỊ LỖI:\n---\n{json_str or raw_response}\n---\nNHIỆM VỤ: Sửa lại CHUỖI JSON trên. Chỉ trả về DUY NHẤT khối JSON đã sửa."
        print(f"--- [Genesis] Đang gọi AI - Lần 2 (Sửa lỗi) ---")
        repaired_response = ""
        try:
            repaired_response = _call_gemini_raw(repair_prompt, model_name)
            repaired_json_str = extract_and_clean_json(repaired_response)
            parsed_json = json.loads(repaired_json_str)
            print("   - ✅ AI đã tự sửa lỗi JSON thành công.")
            post_issue_comment("✅ **Thông tin:** Vòng lặp tự sửa lỗi JSON đã thành công.")
            return parsed_json
        except Exception as final_e:
            raise Exception(f"AI không thể tự sửa lỗi JSON. Lỗi cuối: {final_e}")
    except Exception as e:
        raise e

def upload_secrets_to_repo(repo_name):
    print(f"--- 🔑 Đang tự động thêm secrets vào repo {repo_name} ---")
    from nacl import encoding, public
    
    secrets_to_upload = {
        "RELEASE_KEYSTORE_BASE64": RELEASE_KEYSTORE_BASE64,
        "RELEASE_KEYSTORE_PASSWORD": RELEASE_KEYSTORE_PASSWORD,
        "RELEASE_KEY_ALIAS": RELEASE_KEY_ALIAS,
        "RELEASE_KEY_PASSWORD": RELEASE_KEY_PASSWORD,
        "GH_PAT_FOR_FACTORY": GITHUB_TOKEN
    }
    
    key_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/actions/secrets/public-key"
    key_data = requests.get(key_url, headers=HEADERS).json()
    public_key = public.PublicKey(key_data['key'], encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    
    for name, value in secrets_to_upload.items():
        print(f"   - 🔐 Đang tải lên secret: {name}")
        encrypted = sealed_box.encrypt(value.encode("utf-8"))
        b64_encrypted = base64.b64encode(encrypted).decode("utf-8")
        secret_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/actions/secrets/{name}"
        requests.put(secret_url, headers=HEADERS, json={"encrypted_value": b64_encrypted, "key_id": key_data['key_id']}).raise_for_status()
    
    print(f"   - ✅ Đã thêm thành công {len(secrets_to_upload)} secrets.")

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
            file_tree[".github/workflows/build_and_release.yml"] = FLUTTER_WORKFLOW_CONTENT
            post_issue_comment("⚙️ Đã thêm workflow build & self-heal.")
        
        create_and_commit_project(repo_name, file_tree)
        upload_secrets_to_repo(repo_name) # <-- GỌI HÀM MỚI
        
        success_message = f"""
        🎉 **Dự án `{repo_name}` đã được tạo và cấu hình hoàn chỉnh!**

        - **Link Repository:** https://github.com/{REPO_OWNER}/{repo_name}
        - **Trạng thái:** Tất cả secrets cần thiết đã được tự động thêm.
        - **Hành động:** Workflow build APK đã được kích hoạt. Bạn có thể vào tab 'Actions' của repo mới để theo dõi và tải về sản phẩm.
        """
        post_issue_comment(success_message)
        
    except Exception as e:
        error_trace = traceback.format_exc()
        error_message = f"❌ **[Genesis] Đã xảy ra lỗi:**\n\n**Lỗi:**\n```{e}```\n\n**Traceback:**\n```{error_trace}```"
        post_issue_comment(error_message)
        sys.exit(1)
