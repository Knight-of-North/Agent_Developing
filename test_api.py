"""DeepSeek API 连通性测试
用法：cd campus-agent && python test_api.py
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
    max_tokens=int(os.getenv("DEEPSEEK_MAX_TOKENS", "16384")),
    reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "high"),
)

print(f"模型: {os.getenv('DEEPSEEK_MODEL')}")
print(f"端点: {os.getenv('DEEPSEEK_BASE_URL')}")
print("正在调用...\n")
resp = llm.invoke("用一句话回答：你是谁？")
print("模型回复:", resp.content)
