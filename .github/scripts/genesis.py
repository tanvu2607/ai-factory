import gradio as gr
import subprocess
import os
import sys
import logging
from threading import Thread
from queue import Queue

# --- Cấu hình Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Lấy các secrets từ môi trường của Space ---
# Gradio sẽ tự động load secrets vào os.environ
try:
    GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
    GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
    GH_USER = os.environ["GH_USER"]
    COMMIT_EMAIL = os.getenv("COMMIT_EMAIL", "bot@example.com")
    COMMIT_NAME = os.getenv("COMMIT_NAME", "Genesis AI Studio")
except KeyError as e:
    missing_secret = str(e)
    logger.error(f"FATAL ERROR: Missing required secret: {missing_secret}. Please set it in your Space settings.")
    # Hiển thị lỗi trên giao diện nếu có thể
    # (Trong thực tế, app sẽ crash và log sẽ hiển thị lỗi này)
    raise EnvironmentError(f"Missing required secret: {missing_secret}")

AI_FACTORY_DIR = "ai-factory"
AI_FACTORY_REPO_URL = f"https://github.com/{GH_USER}/ai-factory.git"

def setup_factory():
    """Clone hoặc cập nhật repo ai-factory."""
    if not os.path.exists(AI_FACTORY_DIR):
        logger.info(f"Cloning {AI_FACTORY_REPO_URL} repository...")
        subprocess.run(["git", "clone", AI_FACTORY_REPO_URL], check=True)
    else:
        logger.info(f"Updating {AI_FACTORY_DIR} repository...")
        subprocess.run(["git", "-C", AI_FACTORY_DIR, "pull"], check=True)

# Chạy setup ngay khi ứng dụng khởi động
try:
    setup_factory()
except Exception as e:
    logger.error(f"Failed to setup ai-factory: {e}")
    # Nếu không clone được thì không thể tiếp tục
    # Gradio sẽ hiển thị lỗi này trong log
    raise

def run_genesis_script(repo_name, language, ai_model, prompt):
    """
    Chạy script genesis.py trong một tiến trình con và stream output.
    """
    # Tạo nội dung "issue body" giả lập
    issue_body = f"""
    ### New Repository Name
    {repo_name}
    ### Language or Framework
    {language}
    ### Gemini Model
    {ai_model}
    ### Detailed Prompt (The Blueprint)
    {prompt}
    """

    # Tạo môi trường riêng cho tiến trình con, truyền tất cả secrets
    env = os.environ.copy()
    env["ISSUE_BODY"] = issue_body
    env["ISSUE_NUMBER"] = "gradio-run" # Đánh dấu là chạy từ Gradio

    # Đường dẫn đến script genesis.py bên trong repo đã clone
    script_path = os.path.join(AI_FACTORY_DIR, ".github", "scripts", "genesis.py")

    # Sử dụng Popen để đọc output theo thời gian thực
    process = subprocess.Popen(
        [sys.executable, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, # Gộp cả stdout và stderr
        text=True,
        env=env,
        bufsize=1,
        universal_newlines=True
    )
    
    # Đọc từng dòng output và trả về
    for line in process.stdout:
        print(line, end="") # In ra log của Space để gỡ lỗi
        yield line.strip()

    process.wait() # Đợi tiến trình kết thúc
    
    if process.returncode != 0:
        yield f"\n❌ LỖI! Quá trình thất bại. Vui lòng kiểm tra log của Space để biết chi tiết."
    else:
        repo_url = f"https://github.com/{GH_USER}/{repo_name}"
        yield f"\n🎉 HOÀN TẤT! Link Repo: {repo_url}"

# Xây dựng giao diện Gradio
with gr.Blocks(theme=gr.themes.Soft(primary_hue="blue"), title="Genesis AI Studio") as demo:
    gr.Markdown("# 🚀 Genesis AI Studio")
    gr.Markdown("Turn your ideas into complete, build-ready GitHub projects with a single prompt.")
    
    with gr.Row():
        with gr.Column(scale=2):
            repo_name_input = gr.Textbox(label="New Repository Name", placeholder="e.g., my-awesome-flutter-app")
            language_input = gr.Dropdown(label="Language / Framework", choices=["Flutter", "Python"], value="Flutter")
            model_input = gr.Dropdown(label="Gemini Model", choices=["gemini-1.5-flash-latest", "gemini-1.5-pro-latest"], value="gemini-1.5-flash-latest")
            prompt_input = gr.Textbox(label="Detailed Prompt (The Blueprint)", lines=10, placeholder="Describe the application you want to build...")
            submit_button = gr.Button("✨ Generate Project", variant="primary")
        
        with gr.Column(scale=3):
            gr.Markdown("### 📝 **Live Log**")
            log_output = gr.Textbox(label="Log", lines=20, interactive=False, value="*Awaiting your command...*")

    def stream_response(repo, lang, model, p):
        full_log = ""
        # Dùng generator để stream log
        for line in run_genesis_script(repo, lang, model, p):
            full_log += line + "\n"
            yield full_log

    submit_button.click(
        fn=stream_response, 
        inputs=[repo_name_input, language_input, model_input, prompt_input], 
        outputs=[log_output]
    )

if __name__ == "__main__":
    demo.launch()
