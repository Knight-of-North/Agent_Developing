"""DeepSeek API 连通性测试
用法：cd Agent_Developing && python test_api.py
读取 .env 中的 DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_BASE_URL
"""
import os
from dotenv import load_dotenv

load_dotenv()

from langchain_deepseek import ChatDeepSeek

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
