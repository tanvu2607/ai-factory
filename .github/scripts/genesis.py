import os
import re
import json
import base64
import time
import requests
import google.generativeai as genai

# --- LẤY THÔNG TIN TỪ MÔI TRƯỜNG ACTION ---
issue_body = os.environ.get("ISSUE_BODY")
issue_number = os.environ.get("ISSUE_NUMBER")
gemini_key = os.environ.get("GEMINI_API_KEY")
github_token = os.environ.get("GITHUB_TOKEN")
github_user = os.environ.get("GH_USER")
commit_email = os.environ.get("COMMIT_EMAIL")
commit_name = os.environ.get("COMMIT_NAME")

REPO_OWNER = github_user
COMMIT_AUTHOR = {"name": commit_name, "email": commit_email}

# --- CÁC HÀM TIỆN ÍCH ---

def parse_issue(body):
    """Phân tích nội dung của issue để lấy ra các tham số."""
    params = {}
    fields = ["repo_name", "language", "ai_model", "prompt"]
    for field in fields:
        # Dùng regex để tìm giá trị của từng field trong issue body
        match = re.search(rf"### {field}\s*\n\s*(.*?)\s*(?=\n###|$)", body, re.DOTALL)
        if match:
            params[field] = match.group(1).strip()
    if not all(params.get(f) for f in fields):
        raise ValueError("Không thể phân tích đủ thông tin từ Issue. Hãy chắc chắn form được điền đầy đủ.")
    return params

def call_gemini(user_prompt, language, model_name):
    """Gọi Gemini để tạo cấu trúc dự án."""
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel(model_name)
    
    # (Nội dung prompt này giống hệt như trong buildai)
    final_prompt = f"""
    Bạn là một kỹ sư phần mềm chuyên về {language}.
    Dựa trên yêu cầu sau: "{user_prompt}"
    Hãy trả về kết quả dưới dạng một đối tượng JSON lồng nhau duy nhất, bao bọc trong khối ```json ... ```.
    """
    response = model.generate_content(final_prompt)
    
    match = re.search(r'```json\s*(\{.*?\})\s*```', response.text, re.DOTALL)
    if not match: match = re.search(r'\{.*\}', response.text, re.DOTALL)
    if not match: raise ValueError("AI không trả về JSON hợp lệ.")
    
    return json.loads(match.group(0), strict=False)

def github_api_request(method, url, data=None):
    """Hàm chung để gọi GitHub API."""
    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    try:
        response = requests.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() if response.content else None
    except requests.exceptions.HTTPError as e:
        print(f"Lỗi API GitHub ({e.response.status_code}): {e.response.text}")
        raise

def create_repo(repo_name):
    """Tạo một repository mới."""
    print(f"Tạo repo mới: {repo_name}...")
    url = "https://api.github.com/user/repos"
    data = {"name": repo_name, "private": False, "auto_init": True} # auto_init để có nhánh main
    github_api_request("POST", url, data)
    time.sleep(5) # Đợi GitHub tạo repo xong

def commit_files(repo_name, file_tree):
    """Sử dụng Git Trees API để commit nhiều file cùng lúc."""
    print("Bắt đầu commit file...")
    # Lấy commit và tree SHA mới nhất của nhánh main
    main_ref = github_api_request("GET", f"https://api.github.com/repos/{REPO_OWNER}/{repo_name}/git/ref/heads/main")
    latest_commit_sha = main_ref['object']['sha']
    base_tree_sha = github_api_request("GET", f"https://api.github.com/repos/{REPO_OWNER}/{repo_name}/git/commits/{latest_commit_sha}")['tree']['sha']

    # Tạo các blob cho từng file
    tree_elements = []
    for path, content in file_tree.items():
        blob = github_api_request("POST", f"https://api.github.com/repos/{REPO_OWNER}/{repo_name}/git/blobs", {
            "content": content,
            "encoding": "utf-8"
        })
        tree_elements.append({
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": blob['sha']
        })
    
    # Tạo một tree mới
    new_tree = github_api_request("POST", f"https://api.github.com/repos/{REPO_OWNER}/{repo_name}/git/trees", {
        "base_tree": base_tree_sha,
        "tree": tree_elements
    })
    
    # Tạo commit mới
    new_commit = github_api_request("POST", f"https://api.github.com/repos/{REPO_OWNER}/{repo_name}/git/commits", {
        "message": "feat: Initial commit by AI Factory",
        "author": COMMIT_AUTHOR,
        "parents": [latest_commit_sha],
        "tree": new_tree['sha']
    })
    
    # Cập nhật đầu của nhánh main để trỏ vào commit mới
    github_api_request("POST", f"https://api.github.com/repos/{REPO_OWNER}/{repo_name}/git/refs/heads/main", {
        "sha": new_commit['sha']
    })
    print("Commit file thành công!")

def comment_on_issue(message):
    """Viết comment phản hồi vào issue."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/ai-factory/issues/{issue_number}/comments"
    github_api_request("POST", url, {"body": message})

# --- HÀM THỰC THI CHÍNH ---
try:
    print("Bắt đầu xử lý Issue...")
    params = parse_issue(issue_body)
    repo_name, language, model, prompt = params['repo_name'], params['language'], params['ai_model'], params['prompt']
    
    comment_on_issue(f"✅ Đã nhận yêu cầu tạo repo `{repo_name}`. Bắt đầu gọi AI...")
    
    file_tree = call_gemini(prompt, language, model)
    
    # (Có thể thêm file workflow vào đây nếu muốn)
    
    comment_on_issue("✅ AI đã tạo code thành công. Bắt đầu tạo repo và commit file...")
    
    create_repo(repo_name)
    commit_files(repo_name, file_tree)
    
    success_message = f"""
    🎉 **Dự án `{repo_name}` đã được tạo thành công!**

    - **Link Repository:** https://github.com/{REPO_OWNER}/{repo_name}
    - Một workflow build sẽ tự động được kích hoạt. Hãy vào tab 'Actions' của repo mới để theo dõi.
    """
    comment_on_issue(success_message)
    
except Exception as e:
    error_message = f"❌ **Đã xảy ra lỗi trong quá trình tự động hóa:**\n\n```\n{e}\n```"
    comment_on_issue(error_message)
    sys.exit(1)
