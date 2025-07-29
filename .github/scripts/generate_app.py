import os
import sys
import time
import base64
import google.generativeai as genai
from github import Github

# --- Lấy thông tin từ biến môi trường do GitHub Actions cung cấp ---
try:
    google_api_key = os.environ["GOOGLE_API_KEY"]
    github_token = os.environ["GITHUB_TOKEN"]
    github_username = os.environ["GITHUB_USERNAME"]
    repo_name = os.environ["ISSUE_TITLE"].strip().replace(" ", "-") # Lấy tên repo từ tiêu đề issue
    user_prompt = os.environ["ISSUE_BODY"]
    issue_number = int(os.environ["ISSUE_NUMBER"])
except KeyError as e:
    print(f"Lỗi: Biến môi trường {e} chưa được thiết lập!")
    sys.exit(1)

# --- Cấu hình API ---
genai.configure(api_key=google_api_key)
g = Github(github_token)
user = g.get_user(github_username)
repo_controller = g.get_repo(f"{github_username}/ai-app-factory") # Repo điều khiển
issue = repo_controller.get_issue(issue_number)

# --- Các hàm gọi Gemini API ---
def generate_from_gemini(prompt_text, model_name="gemini-1.5-flash"):
    """Hàm chung để gọi Gemini và xử lý lỗi cơ bản."""
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt_text)
        # Loại bỏ các ký tự markdown thừa mà AI có thể trả về
        return response.text.strip().replace("```kotlin", "").replace("```xml", "").replace("```groovy", "").replace("```", "")
    except Exception as e:
        print(f"Lỗi khi gọi Gemini API: {e}")
        return None

def generate_detailed_prompt(prompt):
    return generate_from_gemini(f"""
    You are an expert Android app architect. A user wants to create an app based on this idea: '{prompt}'.
    Your task is to expand this idea into a detailed technical specification for a simple, single-activity Android application using Kotlin and XML layouts.
    The specification must include a list of necessary files and a clear description of the UI and logic. Keep it simple.
    """)

def generate_file_content(spec, file_path):
    return generate_from_gemini(f"""
    Based on this detailed specification:
    ---
    {spec}
    ---
    Generate the complete, syntactically correct code for the file: `{file_path}`.
    Only output the raw code for the file. Do not include any explanation or markdown formatting.
    """)

# --- Các hàm Workflow ---
def get_build_workflow():
    return f"""
name: Android CI Build

on:
  push:
    branches: [ "main" ]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
    - name: Checkout code
      uses: actions/checkout@v4

    - name: Set up JDK 17
      uses: actions/setup-java@v4
      with:
        java-version: '17'
        distribution: 'temurin'

    - name: Setup Gradle
      uses: gradle/actions/setup-gradle@v3

    - name: Build with Gradle
      id: build_step
      run: ./gradlew assembleDebug

    - name: Upload APK
      uses: actions/upload-artifact@v4
      with:
        name: app-debug.apk
        path: app/build/outputs/apk/debug/app-debug.apk

    - name: Trigger Auto-Fix on Failure
      if: failure()
      run: |
        gh workflow run fix.yml -R {github_username}/{repo_name}
      env:
        GITHUB_TOKEN: ${{{{ secrets.GH_PAT }}}}
"""

def get_fix_workflow():
    return """
name: Auto Fix Build Errors

on:
  workflow_dispatch:

jobs:
  fix-it:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.GH_PAT }}

      - name: Get latest failed build log
        id: get_log
        uses: dawidd6/action-get-previous-run-log@v1.2.0
        with:
          workflow: build.yml
          token: ${{ secrets.GH_PAT }}

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'
      
      - name: Install dependencies for fix script
        run: pip install google-generativeai requests

      - name: Run Auto-Fix Script
        id: run_fix
        env:
          GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY_FOR_FIX }}
          FAILED_LOG: ${{ steps.get_log.outputs.log }}
        # Script này sẽ phải được tạo trong repo sản phẩm
        run: |
          # Logic sửa lỗi sẽ được thực hiện bởi một script riêng
          # (Đây là phần thử nghiệm và phức tạp nhất)
          echo "FIX_SCRIPT_PLACEHOLDER"

      - name: Commit and push the fix
        if: steps.run_fix.outputs.fix_applied == 'true'
        run: |
          git config --global user.name 'AI Factory Bot'
          git config --global user.email 'bot@example.com'
          git add .
          git commit -m "chore: Attempting auto-fix for build error"
          git push
"""

# --- Cấu trúc file Android cơ bản ---
# (Đây là một ví dụ đơn giản, thực tế có thể phức tạp hơn)
ANDROID_PROJECT_STRUCTURE = {
    # Các file sẽ được tạo bởi Gemini
    "app/src/main/java/com/example/aifactoryapp/MainActivity.kt": "",
    "app/src/main/res/layout/activity_main.xml": "",
    # Các file cấu hình cố định
    "app/build.gradle.kts": """plugins{id("com.android.application");id("org.jetbrains.kotlin.android")}
android{namespace="com.example.aifactoryapp";compileSdk=34;defaultConfig{applicationId="com.example.aifactoryapp";minSdk=24;targetSdk=34;versionCode=1;versionName="1.0"};buildTypes{release{isMinifyEnabled=false;proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),"proguard-rules.pro")}};compileOptions{sourceCompatibility=JavaVersion.VERSION_1_8;targetCompatibility=JavaVersion.VERSION_1_8};kotlinOptions{jvmTarget="1.8"}}
dependencies{implementation("androidx.core:core-ktx:1.12.0");implementation("androidx.appcompat:appcompat:1.6.1");implementation("com.google.android.material:material:1.11.0");implementation("androidx.constraintlayout:constraintlayout:2.1.4")}""",
    "build.gradle.kts": """plugins { id("com.android.application") version "8.2.0" apply false; id("org.jetbrains.kotlin.android") version "1.9.20" apply false }""",
    "settings.gradle.kts": """pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS); repositories { google(); mavenCentral() } }
rootProject.name = "AI Factory App"
include(":app")""",
    "app/src/main/AndroidManifest.xml": """<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.aifactoryapp"><application android:allowBackup="true" android:icon="@mipmap/ic_launcher" android:label="@string/app_name" android:roundIcon="@mipmap/ic_launcher_round" android:supportsRtl="true" android:theme="@style/Theme.AppCompat.Light"><activity android:name=".MainActivity" android:exported="true"><intent-filter><action android:name="android.intent.action.MAIN" /><category android:name="android.intent.category.LAUNCHER" /></intent-filter></activity></application></manifest>""",
    "app/src/main/res/values/strings.xml": """<resources><string name="app_name">AI Factory App</string></resources>""",
    # Workflows
    ".github/workflows/build.yml": get_build_workflow(),
    ".github/workflows/fix.yml": get_fix_workflow(),
}

# --- Main Logic ---
def main():
    issue.create_comment(f"🚀 Bắt đầu quá trình tạo ứng dụng cho repo `{repo_name}`...")
    
    # 1. Tạo repo mới trên GitHub
    try:
        new_repo = user.create_repo(repo_name, description=f"App generated by AI Factory from prompt: {user_prompt[:50]}...", private=False)
        print(f"Repo '{repo_name}' đã được tạo.")
        issue.create_comment(f"✅ Đã tạo thành công repo: [{repo_name}](https://github.com/{github_username}/{repo_name})")
    except Exception as e:
        print(f"Lỗi khi tạo repo: {e}")
        issue.create_comment(f"❌ Lỗi! Không thể tạo repo `{repo_name}`. Có thể nó đã tồn tại.")
        sys.exit(1)

    time.sleep(2) # Đợi một chút để repo sẵn sàng
    
    # 2. Dùng Gemini tạo spec chi tiết
    print("Đang tạo spec chi tiết...")
    detailed_spec = generate_detailed_prompt(user_prompt)
    if not detailed_spec:
        issue.create_comment("❌ Lỗi! Không thể tạo spec chi tiết từ Gemini.")
        sys.exit(1)

    # 3. Tạo các file mã nguồn và đẩy lên repo mới
    files_to_generate = [
        "app/src/main/java/com/example/aifactoryapp/MainActivity.kt",
        "app/src/main/res/layout/activity_main.xml"
    ]

    for file_path in files_to_generate:
        print(f"Đang tạo nội dung cho {file_path}...")
        content = generate_file_content(detailed_spec, file_path)
        if content:
            ANDROID_PROJECT_STRUCTURE[file_path] = content
        else:
            print(f"Không thể tạo nội dung cho {file_path}, sẽ sử dụng file trống.")
        time.sleep(5) # Thêm độ trễ để tránh lỗi rate limit của Gemini

    # 4. Commit tất cả các file vào repo mới
    print("Đang commit các file vào repo mới...")
    for file_path, content in ANDROID_PROJECT_STRUCTURE.items():
        if content:
            try:
                new_repo.create_file(file_path, f"feat: Create {os.path.basename(file_path)}", content)
                print(f" -> Đã tạo file: {file_path}")
                time.sleep(1) # Tránh rate limit của GitHub API
            except Exception as e:
                print(f"Lỗi khi tạo file {file_path}: {e}")

    issue.create_comment("✅ Hoàn tất! Mã nguồn đã được đẩy lên repo mới. Quá trình build sẽ tự động bắt đầu. Hãy kiểm tra tab 'Actions' của repo đó.")
    issue.edit(state="closed") # Đóng issue lại khi đã hoàn thành

if __name__ == "__main__":
    main()
