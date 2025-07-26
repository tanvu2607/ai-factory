import os, re, json, base64, time, sys, requests, google.generativeai as genai, traceback, argparse

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

def upload_secrets(repo_name, keystore_b64, keystore_pass, key_alias, key_pass):
    print(f"--- [Genesis] 🔑 Đang tự động thêm secrets vào repo {repo_name} ---")
    try:
        from nacl import encoding, public
    except ImportError:
        print("   - Cảnh báo: Thư viện 'pynacl' chưa được cài đặt trong môi trường. Sẽ bỏ qua bước thêm secrets.", file=sys.stderr)
        return

    secrets_to_upload = {
        "RELEASE_KEYSTORE_BASE64": keystore_b64,
        "RELEASE_KEYSTORE_PASSWORD": keystore_pass,
        "RELEASE_KEY_ALIAS": key_alias,
        "RELEASE_KEY_PASSWORD": key_pass
    }
    
    key_url = f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/actions/secrets/public-key"
    key_data = requests.get(key_url, headers=HEADERS).json()
    public_key = public.PublicKey(key_data['key'], encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)

    for name, value in secrets_to_upload.items():
        encrypted = base64.b64encode(sealed_box.encrypt(value.encode("utf-8"))).decode("utf-8")
        requests.put(f"{API_BASE_URL}/repos/{REPO_OWNER}/{repo_name}/actions/secrets/{name}", headers=HEADERS, json={"encrypted_value": encrypted, "key_id": key_data['key_id']}).raise_for_status()
    
    print(f"   - ✅ Đã thêm thành công {len(secrets_to_upload)} secrets.")


# ==============================================================================
# III. HÀM THỰC THI CHÍNH
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Genesis Script")
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--keystore-b64", required=False, default="")
    parser.add_argument("--keystore-pass", required=False, default="")
    parser.add_argument("--key-alias", required=False, default="")
    parser.add_argument("--key-pass", required=False, default="")
    args = parser.parse_args()

    try:
        print(f"✅ Đã nhận yêu cầu cho repo `{args.repo_name}`.")
        file_tree = call_gemini_for_code(args.prompt, args.language, args.model)
        
        if args.language.lower() == 'flutter':
            file_tree[".github/workflows/build.yml"] = FLUTTER_WORKFLOW_CONTENT
        
        flat_file_tree = flatten_file_tree(file_tree)
        create_and_commit_project(args.repo_name, flat_file_tree)

        if args.keystore_b64 and args.keystore_pass and args.key_alias and args.key_pass:
            upload_secrets(
                args.repo_name,
                args.keystore_b64,
                args.keystore_pass,
                args.key_alias,
                args.key_pass
            )
        else:
            print("--- [Genesis] ℹ️  Bỏ qua bước thêm secrets do không được cung cấp. ---")
        
        print(f"🎉 Dự án `{args.repo_name}` đã được tạo thành công!")
        
    except Exception as e:
        print(f"❌ Đã xảy ra lỗi trong genesis.py: {e}\n{traceback.format_exc()}", file=sys.stderr)
        sys.exit(1)
