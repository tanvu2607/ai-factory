import os
import requests
import json
import sys

def post_issue_comment(message, repo_owner, factory_repo, issue_number, headers):
    """Gửi một comment lên issue."""
    print(f"--- 💬 Phản hồi lên Issue #{issue_number} ---")
    url = f"https://api.github.com/repos/{repo_owner}/{factory_repo}/issues/{issue_number}/comments"
    try:
        requests.post(url, headers=headers, json={"body": message}, timeout=30)
        print("Đã comment xác nhận vào issue.")
    except Exception as e:
        print(f"Không thể comment vào issue: {e}")

def main():
    print("🤖 AI Auto-Debugger workflow has been triggered!")

    try:
        # Lấy thông tin từ môi trường
        issue_number = os.environ["ISSUE_NUMBER"]
        repo_owner = os.environ["GH_USER"]
        factory_repo = "ai-factory"
        github_token = os.environ["GITHUB_TOKEN"]
        headers = {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github.v3+json"}
        
        # Comment lại vào issue để xác nhận
        message = "✅ **AI Auto-Debugger đã nhận được báo cáo lỗi.**\n\n(Chức năng phân tích log và sửa lỗi sẽ được triển khai ở bước tiếp theo.)"
        post_issue_comment(message, repo_owner, factory_repo, issue_number, headers)
    
    except KeyError as e:
        print(f"❌ Lỗi: Thiếu biến môi trường: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Đã xảy ra lỗi không xác định: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
