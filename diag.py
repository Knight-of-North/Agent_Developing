"""
网络诊断脚本：直接测 DeepSeek 连接，暴露真实错误
运行：python diag.py
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("DEEPSEEK_API_KEY", "")
base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

print(f"base_url: {base_url}")
print(f"model: {model}")
# 脱敏：不打印密钥任何片段，只报告"是否已配置 + 长度"，避免密钥进入终端历史/日志
print(f"api_key: {'已配置' if api_key else '未配置'}（长度 {len(api_key)}）")


def test(name, trust_env):
    print(f"\n=== {name} ===")
    try:
        client = httpx.Client(trust_env=trust_env, timeout=30)
        r = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "你好"}]},
        )
        print(f"状态码: {r.status_code}")
        print(f"响应: {r.text[:500]}")
    except Exception as e:
        print(f"异常: {type(e).__name__}: {e}")
        cause = e.__cause__
        while cause:
            print(f"  -> 底层原因: {type(cause).__name__}: {cause}")
            cause = cause.__cause__


test("测试1：trust_env=False（不走代理，直连）", trust_env=False)
test("测试2：trust_env=True（默认，会读代理）", trust_env=True)
