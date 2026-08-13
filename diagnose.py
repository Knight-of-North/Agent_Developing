"""
诊断脚本：搞清楚 deepseek-v4-flash 到底返回了什么
运行：python diagnose.py
"""
from nodes import llm, generate_script_node
import json

print("=== 测试1：模型对「输出JSON」的原始返回 ===")
r = llm.invoke('只输出一个JSON对象，不要任何其他文字：{"name":"张三","age":20}')
print("content 类型:", type(r.content).__name__)
print("content 值:", repr(r.content))
print("reasoning_content:", repr(getattr(r, "reasoning_content", "【无此字段】")))
print()

print("=== 测试2：generate_script_node 的完整返回 ===")
result = generate_script_node({"theme": "校园密室"})
script = result.get("script", {})
print("script 的所有键:", list(script.keys()))
print("script 内容:")
print(json.dumps(script, ensure_ascii=False, indent=2)[:1000])
