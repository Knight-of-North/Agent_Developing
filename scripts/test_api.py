"""DeepSeek API 连通性测试（手动诊断脚本，非 pytest 用例）

用法：cd Agent_Developing && python scripts/test_api.py
读取 .env 中的 DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_BASE_URL

H2：此脚本原来在项目根目录且顶层直接执行 llm.invoke —— pytest 按默认规则
收集 test_*.py 时会 import 该模块，触发真实 API 调用（烧钱且 CI 报错）。
修复：迁到 scripts/（pytest.ini 的 testpaths=tests 不覆盖这里）+ 加 __main__ 守卫，
只有显式 `python scripts/test_api.py` 才发请求。
"""
import os
from dotenv import load_dotenv

load_dotenv()

from langchain_deepseek import ChatDeepSeek


def main():
    llm = ChatDeepSeek(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        # 8-20 审查修复：参数必须与 nodes.py 的 get_llm() 保持一致——
        # 之前 reasoning_effort="high" + max_tokens=16384 测的是"思考模式"行为，
        # 和游戏实际跑的 reasoning_effort="none"(关 thinking)不一致，排查会误导。
        max_tokens=4096,
        reasoning_effort="none",
    )

    print(f"模型: {os.getenv('DEEPSEEK_MODEL')}")
    print(f"端点: {os.getenv('DEEPSEEK_BASE_URL')}")
    print("正在调用...\n")
    resp = llm.invoke("用一句话回答：你是谁？")
    print("模型回复:", resp.content)


if __name__ == "__main__":
    main()
